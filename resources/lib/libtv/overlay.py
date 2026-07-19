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

import os
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from libtv import generator, schedule

_PLAY_URL = "plugin://plugin.video.libtv/?action=play&channel={0}"

# A code-only WindowDialog draws nothing behind its controls — over a
# playing video, an unstyled overlay was confirmed live to be completely
# invisible (doModal() blocked and the log showed it running, but nothing
# was ever seen on screen; Esc still closed it once pressed twice). This
# bundled background must stay tracked despite the repo's generic
# `resources/media/*.png` ignore rule (see .gitignore's exception).
_BG_IMAGE = os.path.join(
    xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo("path")),
    "resources", "media", "overlay_bg.png",
)

_TEXT_COLOR = "0xFFFFFFFF"
_FONT = "font12"  # present in effectively every skin's Font.xml
# The current row is marked with a text prefix, not a text color change.
# Live testing found ControlLabel.setLabel(textColor=...) calls made after
# the initial render didn't visibly change anything (an earlier ControlList
# had the same problem with its selectedColor) — text CONTENT changes are
# the one thing confirmed to reliably repaint, so highlighting piggybacks
# on that instead of depending on dynamic color updates again.
_CURSOR_MARK = "> "
_NO_MARK = "  "

# A strip along the bottom margin, not the (near-)full-height panel of
# earlier versions — a code-only WindowDialog's default coordinate space is
# 1280x720 regardless of the skin's actual resolution, confirmed live (a
# panel spanning y=40..680 visibly covered nearly the whole screen).
_PANEL_X, _PANEL_Y, _PANEL_W, _PANEL_H = 40, 490, 1200, 190
_ROW_HEIGHT = 36
_VISIBLE_ROWS = 4  # fits _PANEL_H with margin; more rows scroll into view

# Confirmed against the real xbmcgui module (Kodistubs 21/Omega).
# tests.conftest's fake xbmcgui doesn't define the named constants, so use
# the numeric values directly rather than depending on the fake growing them.
_ACTION_SELECT_ITEM = 7
_ACTION_PREVIOUS_MENU = 10
_ACTION_MOUSE_LEFT_CLICK = 100
_ACTION_NAV_BACK = 92
# Live testing found ACTION_MOVE_UP/DOWN (3/4) did nothing to the overlay —
# a remote/keyboard during actual PVR playback instead generates
# ACTION_CHANNEL_UP/DOWN (184/185), which *also* drives Kodi's own native
# channel-surf simultaneously (confirmed: the live channel changed, not
# just a preview). That collision can't be suppressed from a Python-level
# onAction, so navigation uses Left/Right instead — not bound to
# channel-surfing — even though Up/Down would read more conventionally for
# a vertical list. Left/Right are common video-seek keys during regular
# playback, so if this collides too, that's the next thing to check.
_ACTION_MOVE_LEFT = 1
_ACTION_MOVE_RIGHT = 2


def _current_channel_id():
    """The LibTV channel id actually playing right now, or None.

    Mirrors the already-live-verified libtv_seek_offset handoff
    (plugin.play sets a ListItem property; Kodi's Player core retains it
    for as long as that item is playing, independent of the resolver
    script having already exited) so the overlay can open with its cursor
    already on the right row instead of always defaulting to the first
    channel in the list.
    """
    player = xbmc.Player()
    if not player.isPlaying():
        return None
    try:
        return player.getPlayingItem().getProperty("libtv_channel_id") or None
    except RuntimeError:
        return None


def _row_label(name, current, upcoming):
    """Pure formatting for one channel's list row: a single combined string."""
    if current is None:
        now_txt = "Off air"
    else:
        remaining_min = max(0, int((current["stop"] - time.time()) // 60))
        now_txt = f"{current['title']} ({remaining_min}m left)"
    next_txt = "" if upcoming is None else f"  |  Next: {upcoming['title']}"
    return f"{name}: {now_txt}{next_txt}"


class _EpgOverlay(xbmcgui.WindowDialog):
    """Renders rows as plain ControlLabels with a hand-rolled cursor and
    scroll window, rather than xbmcgui.ControlList.

    Live testing found ControlList rendered *nothing* at all in a
    code-only window — not even a focus rectangle — across four successive
    attempts to fix it (item height keyword, focus timing,
    background/colors, font/single-label). ControlLabel is the most
    primitive text-drawing control Kodi has; navigation and the
    current-row highlight are tracked and drawn entirely by this class via
    onAction, rather than relying on any native list/button focus
    rendering, to remove that whole axis of uncertainty.

    Only `_VISIBLE_ROWS` ControlLabels are ever created, reused as the
    cursor scrolls through the full channel list — not one per channel —
    so the panel stays a fixed, small strip regardless of channel count.
    """

    def __init__(self, rows, initial_index=0):
        super().__init__()
        # rows: [(channel_id, name, current_prog_or_None, next_prog_or_None), ...]
        self._rows = rows
        self._channel_ids = [row[0] for row in rows]
        self.selected_channel = None
        self._cursor = initial_index
        # Keep the initial cursor in view rather than always scrolled to
        # the top — matters once there are more channels than visible rows.
        self._scroll = max(0, min(initial_index, len(rows) - _VISIBLE_ROWS))

        # Background first so labels draw on top of it, not behind.
        self._background = xbmcgui.ControlImage(_PANEL_X, _PANEL_Y, _PANEL_W, _PANEL_H, _BG_IMAGE)
        self.addControl(self._background)

        # Real content is passed to the constructor directly, not set via a
        # later setLabel() call — live testing found labels built empty and
        # populated from onInit() didn't paint until the *next* redraw (the
        # first keypress), while labels constructed with their real text
        # up front showed correctly from the very first frame.
        self._labels = []
        for i in range(min(_VISIBLE_ROWS, len(rows))):
            label = xbmcgui.ControlLabel(
                _PANEL_X + 20, _PANEL_Y + 10 + i * _ROW_HEIGHT,
                _PANEL_W - 40, _ROW_HEIGHT - 4,
                self._row_text(i), font=_FONT, textColor=_TEXT_COLOR,
            )
            self.addControl(label)
            self._labels.append(label)

    def _row_text(self, row_index):
        _, name, current, upcoming = self._rows[row_index]
        mark = _CURSOR_MARK if row_index == self._cursor else _NO_MARK
        return f"{mark}{_row_label(name, current, upcoming)}"

    def _render(self):
        for i, label in enumerate(self._labels):
            row_index = self._scroll + i
            text = self._row_text(row_index) if row_index < len(self._rows) else ""
            label.setLabel(text, font=_FONT, textColor=_TEXT_COLOR)

    def _move(self, delta):
        if not self._rows:
            return
        self._cursor = max(0, min(len(self._rows) - 1, self._cursor + delta))
        if self._cursor < self._scroll:
            self._scroll = self._cursor
        elif self._cursor >= self._scroll + len(self._labels):
            self._scroll = self._cursor - len(self._labels) + 1
        self._render()

    def onAction(self, action):
        action_id = action.getId()
        if action_id in (_ACTION_PREVIOUS_MENU, _ACTION_NAV_BACK):
            self.close()
        elif action_id == _ACTION_MOVE_LEFT:
            self._move(-1)
        elif action_id == _ACTION_MOVE_RIGHT:
            self._move(1)
        elif action_id in (_ACTION_SELECT_ITEM, _ACTION_MOUSE_LEFT_CLICK):
            if 0 <= self._cursor < len(self._channel_ids):
                self.selected_channel = self._channel_ids[self._cursor]
            self.close()


def show():
    """Entry point called by context.py.

    Logs a breadcrumb at each step (invocation, row count, window close,
    selection) — earlier rounds of this feature failed silently more than
    once (a TypeError with no log line reaching the caller, a focus
    failure logged only as a GUI warning, an invisible-but-running window),
    so the only way to tell those failure modes apart from "it's actually
    working now" is to have explicit markers to check kodi.log against.
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

    current_id = _current_channel_id()
    initial_index = next((i for i, r in enumerate(rows) if r[0] == current_id), 0)

    xbmc.log(f"LibTV: overlay showing {len(rows)} channel row(s)", xbmc.LOGINFO)
    overlay = _EpgOverlay(rows, initial_index)
    overlay.doModal()
    xbmc.log("LibTV: overlay closed", xbmc.LOGINFO)
    selected = overlay.selected_channel
    if selected:
        xbmc.log(f"LibTV: overlay selected channel {selected}", xbmc.LOGINFO)
        xbmc.executebuiltin(f"PlayMedia({_PLAY_URL.format(selected)})")
