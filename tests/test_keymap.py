"""Tests for keymap.py: pure key validation/rendering, and the
settings-driven write/remove flow via the fake xbmc* modules."""

import os

from libtv import keymap

from tests import conftest
from tests.conftest import CALLS


def test_valid_key_accepts_letters_and_function_key_names():
    assert keymap.valid_key("g")
    assert keymap.valid_key("f9")
    assert keymap.valid_key("numpadenter")


def test_valid_key_rejects_empty_and_unsafe_input():
    assert not keymap.valid_key("")
    assert not keymap.valid_key(None)
    assert not keymap.valid_key("g h")
    assert not keymap.valid_key("<g>")
    assert not keymap.valid_key("9g")


def test_render_keymap_xml_binds_runscript_to_the_key():
    xml = keymap.render_keymap_xml("g")
    assert xml.count("<g>RunScript(plugin.video.libtv)</g>") == 2
    assert "<FullscreenVideo>" in xml
    assert "<FullscreenLiveTV>" in xml


def test_apply_from_settings_writes_the_keymap_file(monkeypatch):
    monkeypatch.setitem(conftest.SETTINGS, "overlay_hotkey_key", "h")
    keymap.apply_from_settings()
    assert os.path.exists(keymap.path())
    with open(keymap.path(), encoding="utf-8") as f:
        content = f.read()
    assert "<h>RunScript(plugin.video.libtv)</h>" in content
    assert ("xbmcgui.notification", "LibTV", "Hotkey saved — restart Kodi to apply") in CALLS


def test_apply_from_settings_removes_file_when_key_blank(monkeypatch):
    monkeypatch.setitem(conftest.SETTINGS, "overlay_hotkey_key", "h")
    keymap.apply_from_settings()
    assert os.path.exists(keymap.path())

    monkeypatch.setitem(conftest.SETTINGS, "overlay_hotkey_key", "  ")
    keymap.apply_from_settings()
    assert not os.path.exists(keymap.path())
    assert ("xbmcgui.notification", "LibTV", "Hotkey removed — restart Kodi to apply") in CALLS


def test_apply_from_settings_rejects_invalid_key_without_writing(monkeypatch):
    monkeypatch.setitem(conftest.SETTINGS, "overlay_hotkey_key", "not a key")
    keymap.apply_from_settings()
    assert not os.path.exists(keymap.path())
    assert ("xbmcgui.notification", "LibTV", "Invalid key name: not a key") in CALLS
