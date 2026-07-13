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
            showtitle = prog.get("showtitle")
            ET.SubElement(el, "title").text = showtitle or prog["title"]
            if showtitle:
                ET.SubElement(el, "sub-title").text = prog["title"]
            if prog.get("plot"):
                ET.SubElement(el, "desc").text = prog["plot"]
            for genre in prog.get("genre", []):
                ET.SubElement(el, "category").text = genre
            if prog.get("season") is not None and prog.get("episode") is not None:
                ET.SubElement(el, "episode-num", system="onscreen").text = (
                    f"S{int(prog['season']):02d}E{int(prog['episode']):02d}"
                )

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tv, encoding="unicode")
