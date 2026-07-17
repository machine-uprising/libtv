"""Skin-independent in-playback EPG overlay: a Now/Next list for every
channel, rendered with a code-only xbmcgui.WindowDialog (no skin XML) so it
draws on top of active video/PVR playback without needing Kodi's own
skin-dependent PVR Guide window.

Read-only against schedule.json: never calls generator.regenerate() or
generator.refresh_pvr() from this path — either would risk aborting or
disrupting the very playback the overlay is opened over (see
generator.refresh_pvr's docstring and the PVR-refresh invariant in
CLAUDE.md). Triggered by the kodi.context.item extension (context.py),
which is a different Kodi calling convention than plugin.py's plugin://
routing — show() takes no arguments and is never reached through
plugin.run.
"""
from __future__ import annotations

import time

import xbmc
import xbmcgui

from libtv import generator, schedule

_PLAY_URL = "plugin://plugin.video.libtv/?action=play&channel={0}"

# Confirmed against the real xbmcgui module (Kodistubs 21/Omega): these
# match xbmcgui.ACTION_SELECT_ITEM/ACTION_MOUSE_LEFT_CLICK/
# ACTION_PREVIOUS_MENU/ACTION_NAV_BACK exactly. tests.conftest's fake
# xbmcgui doesn't define the named constants, so use the numeric values
# directly rather than depending on the fake growing them.
_ACTION_SELECT_ITEM = 7
_ACTION_MOUSE_LEFT_CLICK = 100
_ACTION_PREVIOUS_MENU = 10
_ACTION_NAV_BACK = 92


def _row_label(name, current, upcoming):
    """Pure formatting for one channel's list row: (label, label2)."""
    if current is None:
        now_txt = "Off air"
    else:
        remaining_min = max(0, int((current["stop"] - time.time()) // 60))
        now_txt = f"{current['title']} ({remaining_min}m left)"
    next_txt = "" if upcoming is None else f"Next: {upcoming['title']}"
    return name, f"{now_txt}   {next_txt}".rstrip()


class _EpgOverlay(xbmcgui.WindowDialog):
    def __init__(self, rows):
        super().__init__()
        # rows: [(channel_id, name, current_prog_or_None, next_prog_or_None), ...]
        self._channel_ids = [row[0] for row in rows]
        self.selected_channel = None
        # Kodi's real ControlList.__init__ takes underscore-prefixed keyword
        # names (_itemHeight, _space, ...) for everything past selectedColor
        # — the bare "itemHeight" from the API docs' prose raises TypeError
        # at runtime (confirmed live on Omega/Windows; kodistubs matches the
        # real signature here, the docstring text does not).
        self._list = xbmcgui.ControlList(60, 60, 1200, 600, _itemHeight=60)
        items = []
        for _, name, current, upcoming in rows:
            label, label2 = _row_label(name, current, upcoming)
            items.append(xbmcgui.ListItem(label=label, label2=label2))
        self._list.addItems(items)
        self.addControl(self._list)

    def onInit(self):
        # Kodi can't grant focus to a control before the window itself is
        # actually shown — calling setFocus() from __init__ (i.e. before
        # doModal() has shown this window) logs "Control N in window M has
        # been asked to focus, but it can't" and silently leaves nothing
        # focused (confirmed live on Omega/Windows). onInit() fires once
        # Kodi has finished initializing/showing the window, which is the
        # documented place to set initial focus.
        self.setFocus(self._list)

    def _select_focused(self):
        pos = self._list.getSelectedPosition()
        if 0 <= pos < len(self._channel_ids):
            self.selected_channel = self._channel_ids[pos]

    def onAction(self, action):
        action_id = action.getId()
        if action_id in (_ACTION_PREVIOUS_MENU, _ACTION_NAV_BACK):
            self.close()
        elif action_id in (_ACTION_SELECT_ITEM, _ACTION_MOUSE_LEFT_CLICK):
            self._select_focused()
            self.close()

    def onControl(self, control):
        if control == self._list:
            self._select_focused()
            self.close()


def show():
    """Entry point called by context.py.

    Logs a breadcrumb at each step (invocation, row count, window close,
    selection) — this path previously failed silently twice in a row (a
    TypeError with no log line reaching the caller, then a focus failure
    logged only as a GUI warning with no traceback), so the only way to
    tell "RunScript never fired" apart from "it ran fine but the window
    wasn't visible/interactive" is to have explicit markers to check
    kodi.log against.
    """
    xbmc.log("LibTV: overlay.show() invoked", xbmc.LOGINFO)
    data = generator.load_schedule()
    if not data or not data.get("channels"):
        xbmc.log("LibTV: overlay.show() found no schedule/channels", xbmc.LOGWARNING)
        xbmcgui.Dialog().notification(
            "LibTV", "Guide not built yet", xbmcgui.NOTIFICATION_INFO, 3000
        )
        return

    now = time.time()
    rows = []
    for ch in data["channels"]:
        current, upcoming = schedule.find_now_and_next(data, ch["id"], now)
        rows.append((ch["id"], ch["name"], current, upcoming))

    xbmc.log(f"LibTV: overlay showing {len(rows)} channel row(s)", xbmc.LOGINFO)
    overlay = _EpgOverlay(rows)
    overlay.doModal()
    xbmc.log("LibTV: overlay closed", xbmc.LOGINFO)
    selected = overlay.selected_channel
    if selected:
        xbmc.log(f"LibTV: overlay selected channel {selected}", xbmc.LOGINFO)
        xbmc.executebuiltin(f"PlayMedia({_PLAY_URL.format(selected)})")
