"""Orchestrates schedule generation and file output in the profile dir."""
from __future__ import annotations

import json
import os
import time

import xbmc
import xbmcaddon
import xbmcvfs

from libtv import library, schedule, writers

M3U_NAME = "channels.m3u"
XMLTV_NAME = "guide.xmltv"
SCHEDULE_NAME = "schedule.json"


def profile_dir():
    addon = xbmcaddon.Addon()
    path = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    if not xbmcvfs.exists(path):
        xbmcvfs.mkdirs(path)
    return path


def schedule_path():
    return os.path.join(profile_dir(), SCHEDULE_NAME)


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

    channels = library.fetch_channels(max_items)

    now = time.time()
    anchor = schedule.day_anchor(now)
    if shuffle:
        for ch in channels:
            ch["items"] = schedule.shuffled(ch["id"], ch["items"], anchor)

    data = schedule.build_schedule(channels, anchor, now + epg_hours * 3600)

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
