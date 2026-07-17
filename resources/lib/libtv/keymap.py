"""Writes/removes special://profile/keymaps/libtv.xml, binding a
user-chosen key to RunScript(plugin.video.libtv) — the reliable way to open
the in-playback EPG overlay (overlay.py), since the kodi.context.item
trigger is confirmed not to work live (see CLAUDE.md "Live-verified
findings"). Driven by the "Hotkey" + "Save hotkey now" settings.
"""
from __future__ import annotations

import os
import re

import xbmcaddon
import xbmcgui
import xbmcvfs

FILENAME = "libtv.xml"

# Kodi keymap tags are XML element names (single keys like "g", or names
# like "f9"/"numpadenter") — validated before being interpolated as one.
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def valid_key(key):
    return bool(_KEY_RE.match(key or ""))


def render_keymap_xml(key):
    """Pure: the keymap fragment binding `key` to the overlay.

    `key` must already be validated (valid_key) — it is interpolated
    directly as an XML element name.
    """
    return (
        "<keymap>\n"
        "    <FullscreenVideo>\n"
        "        <keyboard>\n"
        f"            <{key}>RunScript(plugin.video.libtv)</{key}>\n"
        "        </keyboard>\n"
        "    </FullscreenVideo>\n"
        "</keymap>\n"
    )


def _keymaps_dir():
    path = xbmcvfs.translatePath("special://profile/keymaps/")
    if not xbmcvfs.exists(path):
        xbmcvfs.mkdirs(path)
    return path


def path():
    return os.path.join(_keymaps_dir(), FILENAME)


def apply_from_settings():
    """Read the "overlay_hotkey_key" setting and (re)write keymaps/libtv.xml.

    Kodi only loads keymaps at startup, so this cannot take effect without
    a restart — every notification below says so. An empty key removes any
    existing binding instead of writing an unusable one.
    """
    key = xbmcaddon.Addon().getSetting("overlay_hotkey_key").strip()
    target = path()
    if not key:
        try:
            os.remove(target)
        except OSError:
            pass
        xbmcgui.Dialog().notification(
            "LibTV", "Hotkey removed — restart Kodi to apply", xbmcgui.NOTIFICATION_INFO, 4000
        )
        return
    if not valid_key(key):
        xbmcgui.Dialog().notification(
            "LibTV", f"Invalid key name: {key}", xbmcgui.NOTIFICATION_WARNING, 4000
        )
        return
    with open(target, "w", encoding="utf-8") as f:
        f.write(render_keymap_xml(key))
    xbmcgui.Dialog().notification(
        "LibTV", "Hotkey saved — restart Kodi to apply", xbmcgui.NOTIFICATION_INFO, 4000
    )
