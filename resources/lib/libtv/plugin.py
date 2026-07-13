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
    for label, action in [
        ("Rebuild channels now", "build"),
        ("Open settings", "settings"),
    ]:
        li = xbmcgui.ListItem(label)
        xbmcplugin.addDirectoryItem(handle, f"{base_url}?action={action}", li, False)
    xbmcplugin.endOfDirectory(handle)


def build():
    generator.regenerate()
    xbmcgui.Dialog().notification(
        "LibTV", "Channels & guide updated", xbmcgui.NOTIFICATION_INFO, 5000
    )


# How long to wait for playback to actually start before giving up on the
# join-in-progress seek, and how often to poll while waiting.
SEEK_TIMEOUT = 30.0
SEEK_POLL = 0.25


def _seek_into_programme(offset, file_path):
    """Seek once playback has started, to join the programme in progress.

    Kodi ignores the ListItem StartOffset property on streams resolved for
    PVR IPTV Simple (verified on Omega), so the resolver stays alive after
    setResolvedUrl, waits for the player to open our file, and seeks.
    """
    monitor = xbmc.Monitor()
    player = xbmc.Player()
    waited = 0.0
    while waited < SEEK_TIMEOUT and not monitor.abortRequested():
        if player.isPlaying():
            try:
                playing = player.getPlayingFile()
                total = player.getTotalTime()
                current = player.getTime()
            except RuntimeError:
                playing, total, current = None, 0, 0  # stream not fully open yet
            # A different file playing is NOT "user zapped away": on a channel
            # change the previous channel's stream is still playing when this
            # resolver runs. Keep waiting until OUR file is open; if the user
            # really left, the timeout ends the wait.
            if playing == file_path and total > 0:
                # Clamp: bad runtime metadata can schedule slots longer than
                # the actual file; never seek past (or into the last 10s of)
                # the real duration.
                target = min(offset, max(total - 10, 0))
                if current < target - 5:
                    player.seekTime(target)
                return
        if monitor.waitForAbort(SEEK_POLL):
            return
        waited += SEEK_POLL
    xbmc.log("LibTV: playback of scheduled file never started, no seek", xbmc.LOGWARNING)


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
    xbmcplugin.setResolvedUrl(handle, True, xbmcgui.ListItem(path=prog["file"]))
    if xbmcaddon.Addon().getSettingBool("join_in_progress") and offset > 5:
        _seek_into_programme(int(offset), prog["file"])


def run(argv):
    base_url = argv[0]
    handle = int(argv[1])
    params = {k: v[0] for k, v in parse_qs(argv[2][1:]).items()}
    action = params.get("action")

    if action == "play":
        play(handle, params.get("channel", ""))
    elif action == "build":
        build()
    elif action == "settings":
        xbmcaddon.Addon().openSettings()
    else:
        main_menu(base_url, handle)
