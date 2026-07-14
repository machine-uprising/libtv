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
        # Missing or stale schedule — rebuild once and retry.
        xbmc.log(f"LibTV: schedule miss for {channel_id}, regenerating", xbmc.LOGINFO)
        data = generator.regenerate()
        found = schedule.find_current(data, channel_id, now)
    if found is None:
        xbmc.log(f"LibTV: nothing scheduled on {channel_id}", xbmc.LOGWARNING)
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    prog, offset = found
    # The join-in-progress seek is performed by the service (daemon.py) from
    # Player.onAVStarted — this resolver script is not reliable for it (Kodi
    # terminates it during channel changes, and ignores StartOffset on
    # resolved PVR streams). Written BEFORE resolving so it survives even if
    # this script dies right after setResolvedUrl.
    if xbmcaddon.Addon().getSettingBool("join_in_progress") and offset > 5:
        generator.write_pending_seek(prog["file"], offset)
    xbmcplugin.setResolvedUrl(handle, True, xbmcgui.ListItem(path=prog["file"]))


def run(argv):
    base_url = argv[0]
    handle = int(argv[1])
    params = {k: v[0] for k, v in parse_qs(argv[2][1:]).items()}
    action = params.get("action")

    if action == "play":
        play(handle, params.get("channel", ""))
    elif action == "build":
        build()
    elif action in ("channels", "channel_add", "channel_options"):
        # Imported lazily: manage imports plugin (for build), so a top-level
        # import here would be circular.
        from libtv import manage

        if action == "channels":
            manage.show_list(base_url, handle)
        elif action == "channel_add":
            manage.add_channel(handle)
        else:
            manage.channel_options(handle, params.get("channel", ""))
    elif action == "settings":
        xbmcaddon.Addon().openSettings()
    else:
        main_menu(base_url, handle)
