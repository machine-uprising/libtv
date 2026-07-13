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
}

# Calls recorded by fakes, for assertions: list of (module.func, args) tuples.
CALLS: list[tuple] = []


def _execute_jsonrpc(request: str) -> str:
    method = json.loads(request)["method"]
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": JSONRPC_RESPONSES.get(method, {})})


# Tests mutate this to simulate the player; seekTime lands in CALLS.
PLAYER = {"playing": False, "file": "", "time": 0.0, "total": 0.0}


class _Monitor:
    """Non-aborting monitor: waitForAbort returns immediately without
    sleeping, so poll loops (e.g. the join-in-progress seek) run fast in
    tests. Don't call daemon.run() against this fake — it would spin."""

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

    mod.translatePath = translate_path
    mod.exists = os.path.exists
    mod.mkdirs = lambda path: os.makedirs(path, exist_ok=True) or True
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


class _Dialog:
    def notification(self, heading, message, icon=None, time=5000, sound=True) -> None:
        CALLS.append(("xbmcgui.notification", heading, message))


class _ListItem:
    def __init__(self, label: str = "", label2: str = "", path: str = "", offscreen: bool = False):
        self.label = label
        self.path = path
        self.properties: dict[str, str] = {}

    def setProperty(self, key: str, value: str) -> None:
        self.properties[key] = value

    def setArt(self, art: dict) -> None:
        pass

    def setInfo(self, type: str, infoLabels: dict) -> None:
        pass


def _make_xbmcgui() -> types.ModuleType:
    mod = types.ModuleType("xbmcgui")
    mod.Dialog = _Dialog
    mod.ListItem = _ListItem
    mod.NOTIFICATION_INFO = "info"
    return mod


def _make_xbmcplugin() -> types.ModuleType:
    mod = types.ModuleType("xbmcplugin")

    def set_resolved_url(handle, succeeded, listitem) -> None:
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
    PLAYER.update(playing=False, file="", time=0.0, total=0.0)
    CALLS.clear()
