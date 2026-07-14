"""Orchestrates schedule generation and file output in the profile dir."""
from __future__ import annotations

import json
import os
import time

import xbmc
import xbmcaddon
import xbmcvfs

from libtv import channels, library, schedule, writers

M3U_NAME = "channels.m3u"
XMLTV_NAME = "guide.xmltv"
SCHEDULE_NAME = "schedule.json"
CHANNELS_NAME = "channels.json"
PENDING_SEEK_NAME = "pending_seek.json"

# A pending seek older than this is abandoned (playback never started).
PENDING_SEEK_MAX_AGE = 120

# The PVR client that consumes our M3U/XMLTV output.
PVR_CLIENT = "pvr.iptvsimple"


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

    lineup = library.fetch_channels(load_channel_defs(), max_items)

    now = time.time()
    anchor = schedule.day_anchor(now)
    if shuffle:
        for ch in lineup:
            ch["items"] = schedule.shuffled(ch["id"], ch["items"], anchor)

    data = schedule.build_schedule(lineup, anchor, now + epg_hours * 3600)

    prof = profile_dir()
    with open(os.path.join(prof, M3U_NAME), "w", encoding="utf-8") as f:
        f.write(writers.render_m3u(data, addon_id))
    with open(os.path.join(prof, XMLTV_NAME), "w", encoding="utf-8") as f:
        f.write(writers.render_xmltv(data))
    with open(schedule_path(), "w", encoding="utf-8") as f:
        json.dump(data, f)

    total = sum(len(ch["programmes"]) for ch in data["channels"])
    xbmc.log(
        f"LibTV: generated {len(data['channels'])} channels / {total} programmes in {prof}",
        xbmc.LOGINFO,
    )
    return data


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
    details = library.json_rpc(
        "Addons.GetAddonDetails", {"addonid": PVR_CLIENT, "properties": ["enabled"]}
    )
    if not details.get("addon", {}).get("enabled"):
        xbmc.log(f"LibTV: {PVR_CLIENT} not installed/enabled, skipping PVR refresh", xbmc.LOGINFO)
        return False
    library.json_rpc("Addons.SetAddonEnabled", {"addonid": PVR_CLIENT, "enabled": False})
    xbmc.sleep(500)
    library.json_rpc("Addons.SetAddonEnabled", {"addonid": PVR_CLIENT, "enabled": True})
    xbmc.log("LibTV: toggled IPTV Simple to reload channels and guide", xbmc.LOGINFO)
    return True


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
