"""Plugin routing: add-on menu and the channel stream resolver.

IPTV Simple Client plays plugin://plugin.video.libtv/?action=play&channel=<id>
for every channel; `play` resolves that to whatever the schedule says is on
air right now.
"""
from __future__ import annotations

import time
from urllib.parse import parse_qs

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin

from libtv import generator, schedule

# Cross-process loop guard: Window(10000) properties are visible to every
# short-lived resolver process talking to this Kodi instance (the same
# mechanism used for the seek-offset ListItem property), so a timestamp
# written by one play() invocation is readable by the very next one even
# though each is a separate interpreter. Guards against a tight
# resolve-fail-resolve loop (e.g. Kodi re-invoking the resolver rapidly for
# a channel with a persistently stale/broken schedule) hammering the library
# with a full JSON-RPC regeneration on every single resolve.
_SCHEDULE_MISS_REGEN_PROPERTY = "libtv_last_schedule_miss_regen"
_SCHEDULE_MISS_REGEN_GUARD_SECONDS = 5


def main_menu(base_url, handle):
    for label, action, is_folder in [
        ("Manage channels", "channels", True),
        ("Rebuild channels now", "build", False),
        ("Open settings", "settings", False),
    ]:
        li = xbmcgui.ListItem(label)
        xbmcplugin.addDirectoryItem(handle, f"{base_url}?action={action}", li, is_folder)
    xbmcplugin.endOfDirectory(handle)


def build():
    generator.regenerate()
    refreshed = generator.refresh_pvr()
    message = (
        "Channels & guide updated"
        if refreshed
        else "Channels rebuilt — guide refresh skipped"
    )
    xbmcgui.Dialog().notification("LibTV", message, xbmcgui.NOTIFICATION_INFO, 5000)


def play(handle, channel_id):
    now = time.time()
    data = generator.load_schedule()
    found = schedule.find_current(data, channel_id, now) if data else None
    if found is None:
        window = xbmcgui.Window(10000)
        last_regen = window.getProperty(_SCHEDULE_MISS_REGEN_PROPERTY)
        if last_regen and now - float(last_regen) < _SCHEDULE_MISS_REGEN_GUARD_SECONDS:
            # Another resolve just forced a regen and the schedule is still
            # missing this channel — regenerating again this fast can't fix
            # it and would just pile up JSON-RPC library queries; let it
            # fail this resolve and rely on the next natural regen.
            xbmc.log(
                f"LibTV: repeated schedule miss for {channel_id}, skipping regen (loop guard)",
                xbmc.LOGWARNING,
            )
        else:
            # Missing or stale schedule — rebuild once and retry.
            window.setProperty(_SCHEDULE_MISS_REGEN_PROPERTY, str(now))
            xbmc.log(f"LibTV: schedule miss for {channel_id}, regenerating", xbmc.LOGINFO)
            data = generator.regenerate()
            found = schedule.find_current(data, channel_id, now)
    if found is None:
        xbmc.log(f"LibTV: nothing scheduled on {channel_id}", xbmc.LOGWARNING)
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    prog, offset = found
    li = xbmcgui.ListItem(path=prog["file"])
    # The join-in-progress seek is performed by the service (daemon.py) from
    # Player.onAVStarted — this resolver script is not reliable for it (Kodi
    # terminates it during channel changes, and ignores StartOffset on
    # resolved PVR streams). Kodi's Player core retains the resolved
    # ListItem's own properties even after this script exits, so daemon.py
    # reads the offset from Player().getPlayingItem() rather than a file —
    # no disk I/O, no staleness window, no rapid-zap race to reason about.
    # The pending-seek file is still written as a fallback until this is
    # live-verified across PVR channel changes (see docs/live-testing.md);
    # drop it once confirmed.
    if xbmcaddon.Addon().getSettingBool("join_in_progress") and offset > 5:
        li.setProperty("libtv_seek_offset", str(int(offset)))
        generator.write_pending_seek(prog["file"], offset)
    xbmcplugin.setResolvedUrl(handle, True, li)


def run(argv):
    base_url = argv[0]
    handle = int(argv[1])
    params = {k: v[0] for k, v in parse_qs(argv[2][1:]).items()}
    action = params.get("action")

    if action == "play":
        play(handle, params.get("channel", ""))
    elif action == "build":
        build()
    elif action in ("channels", "channel_add", "channel_options", "autotune", "autotune_studio"):
        # Imported lazily: manage imports plugin (for build), so a top-level
        # import here would be circular.
        from libtv import manage

        if action == "channels":
            manage.show_list(base_url, handle)
        elif action == "channel_add":
            manage.add_channel(handle)
        elif action == "autotune":
            manage.autotune_genres(handle)
        elif action == "autotune_studio":
            manage.autotune_studios(handle)
        else:
            manage.channel_options(handle, params.get("channel", ""))
    elif action == "settings":
        xbmcaddon.Addon().openSettings()
    else:
        main_menu(base_url, handle)
