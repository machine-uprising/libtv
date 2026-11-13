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

from libtv import generator, keymap, schedule

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

# Joining a programme this close to the end of its real file is pointless —
# playback would end seconds later and bounce straight back out of Live TV.
# Matches daemon.py's seek clamp, which parks exactly here.
_DEAD_TAIL_SECONDS = 10


def main_menu(base_url, handle):
    for label, action, is_folder in [
        ("Setup guide", "setup_guide", False),
        ("Auto-configure IPTV Simple Client", "auto_configure_iptv", False),
        ("Manage channels", "channels", True),
        ("Rebuild channels now", "build", False),
        ("IPTV Simple Client setup paths", "show_iptv_paths", False),
        ("Open settings", "settings", False),
    ]:
        li = xbmcgui.ListItem(label)
        xbmcplugin.addDirectoryItem(handle, f"{base_url}?action={action}", li, is_folder)
    xbmcplugin.endOfDirectory(handle)


def open_channel_manager():
    """Open the channel-management directory from the settings dialog.

    The settings button can't just be `ActivateWindow(Videos,…)` directly in
    its `<data>`: the Add-on Settings dialog is itself a modal dialog, and
    Kodi does not reliably swap the active window out from under an open
    dialog — the settings button used to bind straight to that ActivateWindow
    call and silently did nothing when clicked. Routing it through a
    RunPlugin action instead means real Python code runs synchronously, so
    the dialog can be explicitly closed and *then* the Videos window
    activated, sequenced rather than left to chance.
    """
    xbmc.executebuiltin("Dialog.Close(all,true)")
    xbmc.executebuiltin(
        "ActivateWindow(Videos,plugin://plugin.video.libtv/?action=channels,return)"
    )


def build(regenerate_fn=None):
    """Rebuild everything and refresh the PVR client.

    regenerate_fn lets callers substitute a cheaper rebuild than a full
    `generator.regenerate()` (e.g. `manage.py`'s diff-driven
    `generator.relabel_schedule` for edits that don't change what any
    channel fetches) while still sharing the refresh/notification logic.

    A rebuild can take a few seconds (a JSON-RPC round trip per channel),
    so an upfront notification is shown before doing any work — the same
    "don't leave the button looking dead while something synchronous runs"
    fix already applied to auto_configure_iptv_simple().
    """
    xbmcgui.Dialog().notification(
        "LibTV", "Rebuilding channels & guide…", xbmcgui.NOTIFICATION_INFO, 4000
    )
    (regenerate_fn or generator.regenerate)()
    refreshed = generator.refresh_pvr()
    message = (
        "Channels & guide updated"
        if refreshed
        else "Channels rebuilt — guide refresh skipped"
    )
    xbmcgui.Dialog().notification("LibTV", message, xbmcgui.NOTIFICATION_INFO, 5000)


def show_setup_guide():
    """Walk a fresh install through the steps to get Live TV working.

    Static prose except for the generated paths and instance name, which
    are read live so the guide never goes stale relative to what
    `show_iptv_setup_info` (a more focused, dedicated dialog for just the
    IPTV Simple paths) shows.
    """
    m3u, xmltv = generator.m3u_path(), generator.xmltv_path()
    m3u_special, xmltv_special = generator.m3u_special_path(), generator.xmltv_special_path()
    name = generator.pvr_instance_name()
    message = (
        "1. Scan your videos into Kodi's library (Movies and/or TV shows) —\n"
        "   LibTV builds channels from whatever is already in your library.\n\n"
        "2. (Optional) Use \"Manage channels\" to customize the lineup. Two\n"
        "   default channels (all Movies, all TV Shows) exist out of the box.\n\n"
        "3. Press \"Rebuild channels now\" (or just wait — the background\n"
        "   service rebuilds automatically) to generate the guide and\n"
        "   channel list on disk:\n"
        f"   {m3u}\n"
        f"   {xmltv}\n\n"
        "4. Install and enable \"PVR IPTV Simple Client\" from Kodi's\n"
        "   add-on repository (Add-ons -> Install from repository -> \n"
        "   PVR clients).\n\n"
        f"5. Press \"Auto-configure IPTV Simple Client\" to have LibTV create\n"
        f"   or update an IPTV Simple instance named \"{name}\" (change this\n"
        "   under \"Instance name\" above) pointed at the paths below. This\n"
        "   uses an unofficial technique (Kodi has no supported API for\n"
        "   this) — if it doesn't work, use \"IPTV Simple Client setup\n"
        "   paths\" to paste them in by hand instead:\n"
        "   General -> M3U Play List (Local Path):\n"
        f"   {m3u_special}\n"
        "   EPG Settings -> XMLTV URL (Local Path):\n"
        f"   {xmltv_special}\n\n"
        "6. Open Kodi's TV section — your channels should appear with a\n"
        "   full guide.\n\n"
        "7. (Optional) Set a hotkey under \"In-playback guide hotkey\" to\n"
        "   open a Now/Next overlay while a channel is playing."
    )
    xbmcgui.Dialog().textviewer("LibTV - Setup guide", message)


def show_iptv_setup_info():
    """Show the M3U/XMLTV paths the user must paste into IPTV Simple's own
    settings, as a manual fallback for auto_configure_iptv_simple(). Uses
    special:// paths (confirmed IPTV Simple reads local files through
    Kodi's own VFS, which resolves them) rather than a real OS path, so
    they keep working if the profile dir ever moves."""
    m3u, xmltv = generator.m3u_special_path(), generator.xmltv_special_path()
    message = (
        "Enter these paths into PVR IPTV Simple Client's settings:\n\n"
        f"General -> M3U Play List (Local Path):\n{m3u}\n\n"
        f"EPG Settings -> XMLTV URL (Local Path):\n{xmltv}"
    )
    xbmcgui.Dialog().textviewer("LibTV - IPTV Simple Client setup", message)


_IPTV_CONFIGURE_MESSAGES = {
    "not_installed": (
        "PVR IPTV Simple Client isn't installed and enabled. Install/enable "
        "it (Add-ons -> My add-ons -> PVR clients) and try again."
    ),
    "playing": "Can't configure while something is playing — try again once playback stops.",
    "unchanged": "IPTV Simple Client is already configured for LibTV",
    "configured": "IPTV Simple Client configured — restart Kodi if the guide doesn't appear",
    "declined": "IPTV Simple Client left unchanged",
}

# Outcomes the user must actually act on — shown as a blocking dialog they
# have to dismiss, not a notification toast that can go unnoticed (e.g. if
# they've already navigated away from the settings screen that triggered
# it). "unchanged"/"configured"/"declined" are just confirmations and stay
# as toasts.
_IPTV_CONFIGURE_BLOCKING_OUTCOMES = ("not_installed", "playing")


def auto_configure_iptv_simple():
    """Write/refresh a named IPTV Simple instance for LibTV and reload it —
    see `generator.configure_iptv_simple` for the mechanism and why it's an
    unofficial technique rather than a supported Kodi API call.

    A same-named instance that already exists with *different* settings is
    never silently overwritten: `configure_iptv_simple()` returns
    "exists_different" for that case, and this asks the user to confirm
    before retrying with force=True.
    """
    dialog = xbmcgui.Dialog()
    dialog.notification("LibTV", "Configuring IPTV Simple Client…", xbmcgui.NOTIFICATION_INFO, 3000)
    xbmc.log("LibTV: auto-configure IPTV Simple Client requested", xbmc.LOGINFO)

    outcome = generator.configure_iptv_simple()
    if outcome == "exists_different":
        name = generator.pvr_instance_name()
        confirmed = dialog.yesno(
            "LibTV",
            f'An IPTV Simple Client instance named "{name}" already exists '
            "with different settings. Update it to point at LibTV's guide?",
        )
        outcome = generator.configure_iptv_simple(force=True) if confirmed else "declined"

    message = _IPTV_CONFIGURE_MESSAGES[outcome]
    if outcome in _IPTV_CONFIGURE_BLOCKING_OUTCOMES:
        dialog.ok("LibTV", message)
    else:
        dialog.notification("LibTV", message, xbmcgui.NOTIFICATION_INFO, 5000)


def _channel_name(data, channel_id):
    """The channel's display name — what the PVR client shows for it, and
    therefore how daemon.py can find its Kodi channel id by label."""
    for ch in (data or {}).get("channels", []):
        if ch["id"] == channel_id:
            return ch.get("name", "")
    return ""


def _finished_programme(channel_id):
    """The programme the service reports as just played to its end on this
    channel, or None.

    Written by daemon.onPlayBackEnded before it re-tunes. The schedule alone
    can't answer "what comes next" at that moment: when a slot outlived its
    real file — the usual reason a programme ends early — the schedule still
    shows the finished programme on air, and resolving that again would seek
    straight back into the seconds before the end it just reached.
    """
    advance = generator.read_pending_advance()
    if not advance or advance.get("channel_id") != channel_id:
        return None
    generator.clear_pending_advance()
    return advance


def _in_dead_tail(prog, offset):
    """True if joining at `offset` would land in the last seconds of the
    programme's real file — i.e. the schedule's slot is longer than the
    content it was built for (a scraped runtime that overstates the file,
    or a fallback slot length for an item with no duration metadata). The
    real duration comes from the observed-playback cache, so this only
    fires for files that have actually played at least once."""
    real = generator.load_runtime_cache().get(prog.get("file")) or 0
    return bool(real) and offset > max(real - _DEAD_TAIL_SECONDS, 0)


def _skip_outlived_slot(data, channel_id, prog, offset):
    """(programme, offset) to actually resolve for this channel.

    Normally just what the schedule says is on now. Two cases move past it,
    both meaning the slot has outlived the file it was built for: the
    service reported the programme as finished (continue after *that* one,
    which may be ahead of what the schedule thinks is on air), or joining
    at `offset` would land in the dead tail of the current one.

    Playback only ever moves forward — an advance that would resolve
    something scheduled *earlier* than what is on air now is ignored, so a
    stale request can't drag a channel backwards. The schedule itself is
    left alone here; the service's own stale-schedule rebuild is what brings
    the guide back in line.
    """
    finished = _finished_programme(channel_id)
    if finished is not None:
        reason, after = "continuing after the programme that just ended", finished
    elif _in_dead_tail(prog, offset):
        reason, after = "slot outlived its file", prog
    else:
        return prog, offset

    nxt = schedule.next_after(data, channel_id, after)
    if nxt is None:
        xbmc.log(
            f"LibTV: {channel_id}: {reason}, but nothing is scheduled after it",
            xbmc.LOGWARNING,
        )
        return prog, offset
    if (nxt["start"], nxt.get("file")) == (prog["start"], prog.get("file")):
        # The schedule already moved on to it by itself; join it where the
        # guide says it is rather than restarting it.
        return prog, offset
    if nxt["start"] < prog["start"]:
        return prog, offset
    xbmc.log(
        f"LibTV: {channel_id}: {reason} — playing {nxt.get('title', '')!r} instead",
        xbmc.LOGINFO,
    )
    return nxt, 0


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

    prog, offset = _skip_outlived_slot(data, channel_id, *found)
    li = xbmcgui.ListItem(path=prog["file"])
    # Lets overlay.show() find which channel is currently playing (so the
    # EPG overlay can open with its cursor already on it) via the same
    # Player().getPlayingItem() property mechanism as libtv_seek_offset —
    # Kodi's Player core retains the resolved ListItem's own properties for
    # as long as it's playing, independent of this script having exited.
    li.setProperty("libtv_channel_id", channel_id)
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
    seek = int(offset) if xbmcaddon.Addon().getSettingBool("join_in_progress") and offset > 5 else 0
    if seek:
        li.setProperty("libtv_seek_offset", str(seek))
        generator.write_pending_seek(prog["file"], seek)
    # Hand the whole tune over to the service: which library file this
    # channel really resolved to (the key its observed-runtime cache must
    # use), the slot it was scheduled into (so a file whose real duration
    # contradicts the guide can be spotted) and the channel to re-tune when
    # the programme ends. Written before resolving, so it is already on disk
    # by the time playback starts.
    generator.write_current_tune(channel_id, _channel_name(data, channel_id), prog, seek)
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
    elif action == "apply_keymap":
        keymap.apply_from_settings()
    elif action == "show_iptv_paths":
        show_iptv_setup_info()
    elif action == "setup_guide":
        show_setup_guide()
    elif action == "auto_configure_iptv":
        auto_configure_iptv_simple()
    elif action == "open_channels":
        open_channel_manager()
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
