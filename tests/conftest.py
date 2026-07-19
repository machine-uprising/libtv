"""Install fake Kodi API modules so add-on code can be imported outside Kodi.

The real xbmc/xbmcaddon/xbmcgui/xbmcplugin/xbmcvfs modules only exist inside
Kodi's embedded interpreter. These fakes cover just what the add-on touches;
extend them as the add-on grows. Kodistubs (a dev dependency) provides type
hints for editors but is not importable at runtime, hence the hand-rolled
fakes here.

Deliberately absent: xbmc.translatePath — removed in Kodi 19. Code that
still calls it must fail tests.
"""

import json
import os
import sys
import tempfile
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PROFILE_DIR = tempfile.mkdtemp(prefix="libtv-profile-")

# Tests can replace entries to control what JSON-RPC calls return.
JSONRPC_RESPONSES: dict[str, dict] = {}

SETTINGS = {
    "max_items": "150",
    "shuffle": "true",
    "epg_hours": "24",
    "regen_interval_hours": "6",
    "join_in_progress": "true",
    "refresh_pvr": "true",
    "overlay_hotkey_key": "g",
    "instance_name": "LibTV",
}

# Calls recorded by fakes, for assertions: list of (module.func, args) tuples.
CALLS: list[tuple] = []

# Queued dialog answers, popped FIFO per method; empty queue = cancel-ish
# defaults (select -1, multiselect None, input "", yesno False).
DIALOG_RESPONSES: dict[str, list] = {"select": [], "multiselect": [], "input": [], "yesno": []}


def _execute_jsonrpc(request: str) -> str:
    parsed = json.loads(request)
    method = parsed["method"]
    CALLS.append(("xbmc.executeJSONRPC", method, parsed.get("params", {})))
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": JSONRPC_RESPONSES.get(method, {})})


# Tests mutate this to simulate the player; seekTime lands in CALLS.
PLAYER = {"playing": False, "file": "", "time": 0.0, "total": 0.0}

# The ListItem last passed to xbmcplugin.setResolvedUrl(succeeded=True) —
# mirrors Kodi's Player core retaining the resolved item's properties, so
# Player().getPlayingItem() can read back what the resolver set even though
# the resolver script itself has already exited.
_CURRENT_LISTITEM = None

# Backing store for the fake xbmcgui.Window(10000) — mirrors Kodi's Home
# window properties being visible across every short-lived process talking
# to the same Kodi instance (plugin.py's resolve-loop guard relies on this).
_WINDOW_PROPERTIES: dict[str, str] = {}


class _Monitor:
    """Non-aborting monitor: waitForAbort returns immediately without
    sleeping. Don't call daemon.run() against this fake — it would spin."""

    def abortRequested(self) -> bool:
        return False

    def waitForAbort(self, timeout: float = 0) -> bool:
        return False


class _Player:
    def isPlaying(self) -> bool:
        return PLAYER["playing"]

    def _require_playing(self):
        if not PLAYER["playing"]:
            raise RuntimeError("XBMC is not playing any file")

    def getPlayingFile(self) -> str:
        self._require_playing()
        return PLAYER["file"]

    def getPlayingItem(self):
        self._require_playing()
        return _CURRENT_LISTITEM

    def getTime(self) -> float:
        self._require_playing()
        return PLAYER["time"]

    def getTotalTime(self) -> float:
        self._require_playing()
        return PLAYER["total"]

    def seekTime(self, seconds: float) -> None:
        self._require_playing()
        PLAYER["time"] = seconds
        CALLS.append(("xbmc.Player.seekTime", seconds))


def _make_xbmc() -> types.ModuleType:
    mod = types.ModuleType("xbmc")
    mod.LOGDEBUG, mod.LOGINFO, mod.LOGWARNING, mod.LOGERROR = 0, 1, 2, 3
    mod.log = lambda msg, level=1: None
    mod.sleep = lambda ms: None
    mod.executeJSONRPC = _execute_jsonrpc
    mod.executebuiltin = lambda command: CALLS.append(("xbmc.executebuiltin", command))
    mod.Monitor = _Monitor
    mod.Player = _Player
    return mod


def _make_xbmcvfs() -> types.ModuleType:
    mod = types.ModuleType("xbmcvfs")

    def translate_path(path: str) -> str:
        if path.startswith("special://profile"):
            return _PROFILE_DIR
        if path.startswith("special://"):
            return REPO_ROOT
        return path

    def listdir(path: str):
        if not os.path.exists(path):
            return [], []
        entries = os.listdir(path)
        dirs = [e for e in entries if os.path.isdir(os.path.join(path, e))]
        files = [e for e in entries if not os.path.isdir(os.path.join(path, e))]
        return dirs, files

    mod.translatePath = translate_path
    mod.exists = os.path.exists
    mod.mkdirs = lambda path: os.makedirs(path, exist_ok=True) or True
    mod.listdir = listdir
    return mod


class _Addon:
    def __init__(self, addon_id: str = "plugin.video.libtv") -> None:
        self._id = addon_id

    def getAddonInfo(self, key: str) -> str:
        return {
            "id": self._id,
            "profile": f"special://profile/addon_data/{self._id}/",
            "path": REPO_ROOT,
            "version": "0.2.0",
        }.get(key, "")

    def getSetting(self, key: str) -> str:
        return SETTINGS.get(key, "")

    def getSettingBool(self, key: str) -> bool:
        return SETTINGS.get(key, "false") == "true"

    def setSetting(self, key: str, value: str) -> None:
        SETTINGS[key] = value

    def openSettings(self) -> None:
        CALLS.append(("xbmcaddon.openSettings",))


def _make_xbmcaddon() -> types.ModuleType:
    mod = types.ModuleType("xbmcaddon")
    mod.Addon = _Addon
    return mod


def _dialog_answer(method: str, default):
    queue = DIALOG_RESPONSES[method]
    return queue.pop(0) if queue else default


class _Dialog:
    def notification(self, heading, message, icon=None, time=5000, sound=True) -> None:
        CALLS.append(("xbmcgui.notification", heading, message))

    def select(self, heading, options, autoclose=0, preselect=-1, useDetails=False) -> int:
        CALLS.append(("xbmcgui.select", heading, list(options)))
        return _dialog_answer("select", -1)

    def multiselect(self, heading, options, autoclose=0, preselect=None, useDetails=False):
        CALLS.append(("xbmcgui.multiselect", heading, list(options)))
        return _dialog_answer("multiselect", None)

    def input(self, heading, defaultt="", type=0, option=0, autoclose=0) -> str:
        CALLS.append(("xbmcgui.input", heading, defaultt))
        return _dialog_answer("input", "")

    def yesno(self, heading, message, nolabel="", yeslabel="", autoclose=0) -> bool:
        CALLS.append(("xbmcgui.yesno", heading, message))
        return _dialog_answer("yesno", False)

    def textviewer(self, heading, text, usemono=False) -> None:
        CALLS.append(("xbmcgui.textviewer", heading, text))


class _ListItem:
    def __init__(self, label: str = "", label2: str = "", path: str = "", offscreen: bool = False):
        self.label = label
        self.path = path
        self.properties: dict[str, str] = {}

    def setProperty(self, key: str, value: str) -> None:
        self.properties[key] = value

    def getProperty(self, key: str) -> str:
        return self.properties.get(key, "")

    def setArt(self, art: dict) -> None:
        pass

    def setInfo(self, type: str, infoLabels: dict) -> None:
        pass


class _Window:
    def __init__(self, window_id: int = 0) -> None:
        self._id = window_id

    def getProperty(self, key: str) -> str:
        return _WINDOW_PROPERTIES.get(key, "")

    def setProperty(self, key: str, value: str) -> None:
        _WINDOW_PROPERTIES[key] = value

    def clearProperty(self, key: str) -> None:
        _WINDOW_PROPERTIES.pop(key, None)


def _make_xbmcgui() -> types.ModuleType:
    mod = types.ModuleType("xbmcgui")
    mod.Dialog = _Dialog
    mod.ListItem = _ListItem
    mod.Window = _Window
    mod.NOTIFICATION_INFO = "info"
    mod.NOTIFICATION_WARNING = "warning"
    mod.INPUT_ALPHANUM = 0
    mod.INPUT_NUMERIC = 1
    return mod


def _make_xbmcplugin() -> types.ModuleType:
    mod = types.ModuleType("xbmcplugin")

    def set_resolved_url(handle, succeeded, listitem) -> None:
        global _CURRENT_LISTITEM
        if succeeded:
            _CURRENT_LISTITEM = listitem
        CALLS.append(("xbmcplugin.setResolvedUrl", handle, succeeded, listitem))

    mod.addDirectoryItem = lambda handle, url, listitem, isFolder=False: CALLS.append(
        ("xbmcplugin.addDirectoryItem", url)
    ) or True
    mod.endOfDirectory = lambda handle, succeeded=True, updateListing=False, cacheToDisc=True: None
    mod.setResolvedUrl = set_resolved_url
    return mod


for name, factory in {
    "xbmc": _make_xbmc,
    "xbmcvfs": _make_xbmcvfs,
    "xbmcaddon": _make_xbmcaddon,
    "xbmcgui": _make_xbmcgui,
    "xbmcplugin": _make_xbmcplugin,
}.items():
    sys.modules.setdefault(name, factory())


import pytest  # noqa: E402  (fakes must be installed before anything imports xbmc*)


@pytest.fixture(autouse=True)
def _reset_kodi_fakes():
    yield
    from libtv import generator, keymap

    generator.clear_pending_seek()
    for path in (
        generator.channels_path(),
        generator._runtime_cache_path(),
        generator.schedule_path(),
        os.path.join(generator.profile_dir(), generator.M3U_NAME),
        os.path.join(generator.profile_dir(), generator.XMLTV_NAME),
        keymap.path(),
    ):
        try:
            os.remove(path)
        except OSError:
            pass
    # A test may have set a custom instance_name (reverted by monkeypatch
    # before this teardown runs), so the file it created can't be found by
    # recomputing pvr_instance_settings_path() here — sweep the directory
    # for any instance-settings-*.xml instead of guessing one path.
    pvr_dir = generator._pvr_client_profile_dir()
    if os.path.exists(pvr_dir):
        for fname in os.listdir(pvr_dir):
            if fname.startswith("instance-settings-") and fname.endswith(".xml"):
                try:
                    os.remove(os.path.join(pvr_dir, fname))
                except OSError:
                    pass
    PLAYER.update(playing=False, file="", time=0.0, total=0.0)
    global _CURRENT_LISTITEM
    _CURRENT_LISTITEM = None
    _WINDOW_PROPERTIES.clear()
    CALLS.clear()
    for queue in DIALOG_RESPONSES.values():
        queue.clear()
