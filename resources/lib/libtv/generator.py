"""Orchestrates schedule generation and file output in the profile dir."""
from __future__ import annotations

import json
import os
import time
import zlib

import xbmc
import xbmcaddon
import xbmcvfs

from libtv import channels, library, schedule, writers

M3U_NAME = "channels.m3u"
XMLTV_NAME = "guide.xmltv"
SCHEDULE_NAME = "schedule.json"
CHANNELS_NAME = "channels.json"
PENDING_SEEK_NAME = "pending_seek.json"
RUNTIME_CACHE_NAME = "runtime_cache.json"

# A pending seek older than this is abandoned (playback never started).
PENDING_SEEK_MAX_AGE = 120

# The PVR client that consumes our M3U/XMLTV output.
PVR_CLIENT = "pvr.iptvsimple"

# Name of the pvr.iptvsimple instance configure_iptv_simple() owns; also the
# seed for that instance's id, so it's always found at the same path again.
PVR_INSTANCE_NAME = "LibTV"


def profile_dir():
    addon = xbmcaddon.Addon()
    path = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    if not xbmcvfs.exists(path):
        xbmcvfs.mkdirs(path)
    return path


def schedule_path():
    return os.path.join(profile_dir(), SCHEDULE_NAME)


def channels_path():
    return os.path.join(profile_dir(), CHANNELS_NAME)


def m3u_path():
    return os.path.join(profile_dir(), M3U_NAME)


def xmltv_path():
    return os.path.join(profile_dir(), XMLTV_NAME)


def load_channel_defs():
    """Channel definitions from channels.json (default lineup if absent)."""
    return channels.load(channels_path())


def save_channel_defs(definitions):
    channels.save(channels_path(), definitions)


def _int_setting(addon, setting_id, default):
    try:
        return int(addon.getSetting(setting_id))
    except ValueError:
        return default


def regen_interval_seconds():
    hours = _int_setting(xbmcaddon.Addon(), "regen_interval_hours", 6)
    return max(1, hours) * 3600


def regenerate():
    """Rebuild the schedule and write M3U, XMLTV, and schedule.json.

    Returns the schedule dict so callers (e.g. the stream resolver) can use
    it immediately.
    """
    addon = xbmcaddon.Addon()
    addon_id = addon.getAddonInfo("id")
    max_items = _int_setting(addon, "max_items", 150)
    epg_hours = _int_setting(addon, "epg_hours", 24)
    shuffle = addon.getSettingBool("shuffle")

    now = time.time()
    anchor = schedule.day_anchor(now)
    runtime_cache = load_runtime_cache()
    lineup = library.fetch_channels(load_channel_defs(), max_items, anchor, runtime_cache)

    if shuffle:
        for ch in lineup:
            ch["items"] = schedule.shuffled(ch["id"], ch["items"], anchor)

    data = schedule.build_schedule(lineup, anchor, now + epg_hours * 3600)

    with open(m3u_path(), "w", encoding="utf-8") as f:
        f.write(writers.render_m3u(data, addon_id))
    with open(xmltv_path(), "w", encoding="utf-8") as f:
        f.write(writers.render_xmltv(data))
    with open(schedule_path(), "w", encoding="utf-8") as f:
        json.dump(data, f)

    total = sum(len(ch["programmes"]) for ch in data["channels"])
    n_channels = len(data["channels"])
    xbmc.log(
        f"LibTV: generated {n_channels} channels / {total} programmes in {profile_dir()}",
        xbmc.LOGINFO,
    )
    return data


def relabel_schedule(definitions):
    """Patch the persisted schedule's channel metadata (name, group,
    membership, order) from the current channel lineup, without re-fetching
    the library or recomputing programme timing.

    For management-UI edits that don't change what any channel fetches —
    rename, reorder, delete — the existing schedule's programmes are still
    entirely correct; only the M3U/XMLTV's channel-level labels and ordering
    need to change. This is `manage.py`'s diff-driven invalidation: the call
    site already knows whether an edit touched a channel's filter/type/order
    (see `manage._apply`'s `content_changed` flag), so unlike a generic
    old-vs-new diff, no schedule inspection is needed to decide whether a
    full `regenerate()` (JSON-RPC fetch across every channel) can be
    skipped — only to perform the skip once the caller has already decided.

    Falls back to a full `regenerate()` if there's no schedule to patch yet,
    or if a channel in `definitions` has no matching schedule entry (a
    caller passed a stale/incorrect content_changed=False for what turned
    out to be a genuinely new channel).
    """
    data = load_schedule()
    if data is None:
        return regenerate()

    by_id = {ch["id"]: ch for ch in data["channels"]}
    patched = []
    for defn in definitions:
        ch = by_id.get(defn["id"])
        if ch is None:
            return regenerate()
        ch["name"] = defn["name"]
        ch["group"] = channels.group(defn)
        patched.append(ch)
    data["channels"] = patched

    addon_id = xbmcaddon.Addon().getAddonInfo("id")
    with open(m3u_path(), "w", encoding="utf-8") as f:
        f.write(writers.render_m3u(data, addon_id))
    with open(xmltv_path(), "w", encoding="utf-8") as f:
        f.write(writers.render_xmltv(data))
    with open(schedule_path(), "w", encoding="utf-8") as f:
        json.dump(data, f)

    xbmc.log("LibTV: relabeled schedule without a library refetch", xbmc.LOGINFO)
    return data


def _pvr_client_enabled():
    details = library.json_rpc(
        "Addons.GetAddonDetails", {"addonid": PVR_CLIENT, "properties": ["enabled"]}
    )
    return bool(details.get("addon", {}).get("enabled"))


def _toggle_pvr_client():
    library.json_rpc("Addons.SetAddonEnabled", {"addonid": PVR_CLIENT, "enabled": False})
    xbmc.sleep(500)
    library.json_rpc("Addons.SetAddonEnabled", {"addonid": PVR_CLIENT, "enabled": True})
    xbmc.log("LibTV: toggled IPTV Simple to reload channels and guide", xbmc.LOGINFO)


def refresh_pvr():
    """Make IPTV Simple reload the regenerated M3U/EPG. Returns True if done.

    The client has no reload API, so toggle it off and on over JSON-RPC —
    the PVR manager then restarts it and it re-reads both files. Never do
    this while something is playing (it would kill the stream), and never
    call it from the stream resolver (a toggle mid-tune aborts the tune) —
    only from the manual build action and the service loop.
    """
    if not xbmcaddon.Addon().getSettingBool("refresh_pvr"):
        return False
    if xbmc.Player().isPlaying():
        xbmc.log("LibTV: playback active, skipping PVR refresh", xbmc.LOGINFO)
        return False
    if not _pvr_client_enabled():
        xbmc.log(f"LibTV: {PVR_CLIENT} not installed/enabled, skipping PVR refresh", xbmc.LOGINFO)
        return False
    _toggle_pvr_client()
    return True


def _pvr_client_profile_dir():
    addon = xbmcaddon.Addon(PVR_CLIENT)
    path = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    if not xbmcvfs.exists(path):
        xbmcvfs.mkdirs(path)
    return path


def _pvr_instance_id():
    # IPTV Simple's instance ids are 32-bit; a name-derived crc32 gives a
    # stable id across runs without persisting one anywhere ourselves —
    # the same technique PseudoTV Live's current code uses.
    return zlib.crc32(PVR_INSTANCE_NAME.encode("utf-8")) % 2147483648


def pvr_instance_settings_path():
    return os.path.join(
        _pvr_client_profile_dir(), f"instance-settings-{_pvr_instance_id()}.xml"
    )


def _desired_pvr_instance_settings():
    return {
        "kodi_addon_instance_name": PVR_INSTANCE_NAME,
        "kodi_addon_instance_enabled": "true",
        "m3uPathType": "0",
        "m3uPath": m3u_path(),
        "m3uCache": "false",
        "epgPathType": "0",
        "epgPath": xmltv_path(),
        "epgCache": "true",
    }


def configure_iptv_simple():
    """Write/refresh a dedicated "LibTV" pvr.iptvsimple instance pointed at
    our own M3U/XMLTV, and force Kodi to load it.

    Kodi has no supported API for one add-on to manage another's PVR-client
    instances (see docs/architecture.md §7) — this hand-writes the
    instance-settings XML Kodi's own multi-instance settings system reads
    (`writers.render_iptv_instance_settings`), the same technique PseudoTV
    Live's current code uses, then reuses the same enable/disable JSON-RPC
    toggle `refresh_pvr()` uses to make the client pick it up.

    Returns "not_installed", "playing", "unchanged", or "configured". The
    idempotency check (parse what's already on disk and compare) runs
    before the playback check, so a no-op call never blocks on "something
    is playing" — only an actual write does.
    """
    if not _pvr_client_enabled():
        return "not_installed"

    desired = _desired_pvr_instance_settings()
    path = pvr_instance_settings_path()
    current_text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            current_text = f.read()
    if writers.parse_iptv_instance_settings(current_text) == desired:
        return "unchanged"

    if xbmc.Player().isPlaying():
        xbmc.log("LibTV: playback active, skipping IPTV Simple auto-configure", xbmc.LOGINFO)
        return "playing"

    with open(path, "w", encoding="utf-8") as f:
        f.write(writers.render_iptv_instance_settings(desired))
    xbmc.log(f"LibTV: wrote {PVR_CLIENT} instance settings to {path}", xbmc.LOGINFO)
    _toggle_pvr_client()
    return "configured"


def load_schedule():
    """Load the persisted schedule, or None if missing/corrupt."""
    path = schedule_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        xbmc.log(f"LibTV: could not read schedule: {exc}", xbmc.LOGWARNING)
        return None


def _pending_seek_path():
    return os.path.join(profile_dir(), PENDING_SEEK_NAME)


def write_pending_seek(file_path, offset):
    """Hand a join-in-progress seek over to the service.

    The resolver cannot seek reliably itself: its script gets terminated
    when the previous channel's stream stops during a channel change, so the
    long-lived service performs the seek from its Player.onAVStarted.
    """
    payload = {"file": file_path, "offset": int(offset), "set_at": time.time()}
    with open(_pending_seek_path(), "w", encoding="utf-8") as f:
        json.dump(payload, f)


def read_pending_seek():
    """Return the pending seek, or None. Stale/corrupt entries are removed;
    fresh ones are left in place — the consumer clears after acting."""
    path = _pending_seek_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = None
    if not data or time.time() - data.get("set_at", 0) > PENDING_SEEK_MAX_AGE:
        clear_pending_seek()
        return None
    return data


def clear_pending_seek():
    try:
        os.remove(_pending_seek_path())
    except OSError:
        pass


def _runtime_cache_path():
    return os.path.join(profile_dir(), RUNTIME_CACHE_NAME)


def _addon_version():
    return xbmcaddon.Addon().getAddonInfo("version")


def load_runtime_cache():
    """Map of file path -> observed playback duration in seconds.

    Populated by daemon.JoinInProgressPlayer.onAVStarted from Kodi's own
    Player.getTotalTime() once a file is actually playing. library.py
    consults this as a fallback when the library's own runtime metadata is
    missing or zero (e.g. an episode scraper that never set one), which is
    strictly more reliable than the library's stated value since it reflects
    the real file — it's not a substitute for requesting streamdetails,
    which is still needed for Kodi to fill in runtime in the first place.

    The file is stamped with the add-on version that wrote it; a version
    mismatch (add-on upgraded since) discards the cache instead of trusting
    a possibly-incompatible on-disk shape — cheap insurance against needing
    migration code for a cache format change across releases.
    """
    path = _runtime_cache_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != _addon_version():
        return {}
    entries = data.get("entries")
    return entries if isinstance(entries, dict) else {}


def record_observed_runtime(file_path, seconds):
    """Persist an observed duration for file_path, merging into the cache."""
    if not file_path or not seconds or seconds <= 0:
        return
    cache = load_runtime_cache()
    cache[file_path] = int(seconds)
    with open(_runtime_cache_path(), "w", encoding="utf-8") as f:
        json.dump({"version": _addon_version(), "entries": cache}, f)
