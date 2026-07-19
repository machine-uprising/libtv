"""Pure M3U / XMLTV rendering from a schedule dict. No Kodi imports."""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from urllib.parse import quote


def stream_url(addon_id, channel_id):
    return f"plugin://{addon_id}/?action=play&channel={quote(channel_id)}"


def render_m3u(schedule_data, addon_id):
    """M3U where every channel resolves through this add-on, so playback
    always lands on whatever is currently on air."""
    lines = ["#EXTM3U"]
    for ch in schedule_data["channels"]:
        lines.append(
            f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-name="{ch["name"]}"'
            f' group-title="{ch.get("group", "")}" tvg-logo="{ch.get("logo", "")}",{ch["name"]}'
        )
        lines.append(stream_url(addon_id, ch["id"]))
    return "\n".join(lines) + "\n"


def _xmltv_time(epoch):
    return time.strftime("%Y%m%d%H%M%S +0000", time.gmtime(epoch))


def render_xmltv(schedule_data):
    """XMLTV guide as a UTF-8 string."""
    tv = ET.Element("tv", {"generator-info-name": "LibTV"})
    for ch in schedule_data["channels"]:
        chan = ET.SubElement(tv, "channel", id=ch["id"])
        ET.SubElement(chan, "display-name").text = ch["name"]
        if ch.get("logo"):
            ET.SubElement(chan, "icon", src=ch["logo"])

    for ch in schedule_data["channels"]:
        for prog in ch["programmes"]:
            el = ET.SubElement(tv, "programme", {
                "start": _xmltv_time(prog["start"]),
                "stop": _xmltv_time(prog["stop"]),
                "channel": ch["id"],
            })
            # Element order follows the xmltv.dtd sequence (title, sub-title,
            # desc, credits, date, category, icon, episode-num, new, rating,
            # star-rating, ...) — some readers are strict about it even though
            # most aren't.
            showtitle = prog.get("showtitle")
            ET.SubElement(el, "title").text = showtitle or prog["title"]
            if showtitle:
                ET.SubElement(el, "sub-title").text = prog["title"]
            if prog.get("plot"):
                ET.SubElement(el, "desc").text = prog["plot"]
            director = prog.get("director") or []
            cast = prog.get("cast") or []
            if director or cast:
                credits = ET.SubElement(el, "credits")
                for name in director[:5]:
                    ET.SubElement(credits, "director").text = name
                for actor in cast[:5]:
                    name = actor.get("name") if isinstance(actor, dict) else actor
                    if not name:
                        continue
                    role = actor.get("role") if isinstance(actor, dict) else None
                    actor_el = ET.SubElement(credits, "actor", {"role": role} if role else {})
                    actor_el.text = name
            if prog.get("year"):
                ET.SubElement(el, "date").text = str(prog["year"])
            for genre in prog.get("genre", []):
                ET.SubElement(el, "category").text = genre
            if prog.get("icon"):
                ET.SubElement(el, "icon", src=prog["icon"])
            if prog.get("season") is not None and prog.get("episode") is not None:
                season, episode = int(prog["season"]), int(prog["episode"])
                # xmltv_ns is zero-based ("season.episode.part"); onscreen is
                # the human "SxxExx" form. Both are optional per the DTD —
                # some EPG skins only render one or the other.
                ET.SubElement(el, "episode-num", system="xmltv_ns").text = (
                    f"{season - 1}.{episode - 1}."
                )
                ET.SubElement(el, "episode-num", system="onscreen").text = (
                    f"S{season:02d}E{episode:02d}"
                )
            if prog.get("playcount") == 0:
                ET.SubElement(el, "new")
            if prog.get("mpaa"):
                ET.SubElement(el, "rating", system="MPAA").text = prog["mpaa"]
            if prog.get("rating"):
                star = ET.SubElement(el, "star-rating")
                ET.SubElement(star, "value").text = f"{float(prog['rating']):.1f}/10"

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tv, encoding="unicode")


def render_iptv_instance_settings(settings, version=2):
    """Kodi's canonical multi-instance-addon settings file format:
    <settings version="N"><setting id="x" default="true">value</setting>...
    </settings> — confirmed against Kodi core's own serializer
    (CSettingsValueXmlSerializer) and a real Kodi-migrated pvr.iptvsimple
    instance file, and matching the format PseudoTV Live's current code
    writes for the same purpose. Kodi's reader only looks at each
    <setting>'s id and text, so the always-true `default` attribute (also
    what PseudoTV writes) is harmless even when a value isn't actually the
    schema default.
    """
    root = ET.Element("settings", version=str(version))
    for setting_id, value in settings.items():
        el = ET.SubElement(root, "setting", id=setting_id, default="true")
        if value not in (None, ""):
            el.text = str(value)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def parse_iptv_instance_settings(xml_text):
    """Inverse of render_iptv_instance_settings, for idempotency checks
    against whatever instance file (ours from a previous run, or none) is
    currently on disk. Missing/unparseable input returns {} rather than
    raising — "no instance file yet" is the expected common case."""
    if not xml_text or not xml_text.strip():
        return {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    return {el.get("id"): (el.text or "") for el in root.findall("setting")}
