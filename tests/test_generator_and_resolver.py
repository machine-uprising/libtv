"""End-to-end tests through the fake Kodi layer: generate, then resolve."""

import json
import os
import runpy
import sys

from tests import conftest

MOVIES = {
    "movies": [
        {"title": "Movie A", "file": "/media/a.mkv", "runtime": 6000, "plot": "A",
         "genre": ["Action"]},
    ]
}
EPISODES = {
    "episodes": [
        {"title": "Pilot", "file": "/media/s01e01.mkv", "runtime": 1800, "plot": "B",
         "showtitle": "Some Show", "season": 1, "episode": 1, "genre": ["Comedy"]},
    ]
}


def _with_library(monkeypatch):
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "VideoLibrary.GetMovies", MOVIES)
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "VideoLibrary.GetEpisodes", EPISODES)


def test_regenerate_writes_all_outputs(monkeypatch):
    from libtv import generator

    _with_library(monkeypatch)
    data = generator.regenerate()

    prof = generator.profile_dir()
    assert os.path.exists(os.path.join(prof, "channels.m3u"))
    assert os.path.exists(os.path.join(prof, "guide.xmltv"))
    assert os.path.exists(os.path.join(prof, "schedule.json"))

    with open(os.path.join(prof, "schedule.json"), encoding="utf-8") as f:
        persisted = json.load(f)
    assert persisted == data
    assert [ch["id"] for ch in data["channels"]] == ["libtv.movies", "libtv.tv"]
    assert data["channels"][0]["programmes"], "movies channel must have programmes"

    with open(os.path.join(prof, "channels.m3u"), encoding="utf-8") as f:
        assert "plugin://plugin.video.libtv/?action=play&channel=libtv.movies" in f.read()


def _run_plugin(monkeypatch, query):
    monkeypatch.setattr(
        sys, "argv", ["plugin://plugin.video.libtv/", "7", query]
    )
    conftest.CALLS.clear()
    runpy.run_path("default.py", run_name="__main__")


def test_resolver_plays_current_programme(monkeypatch):
    _with_library(monkeypatch)
    _run_plugin(monkeypatch, "?action=play&channel=libtv.movies")

    resolved = [c for c in conftest.CALLS if c[0] == "xbmcplugin.setResolvedUrl"]
    assert len(resolved) == 1
    _, handle, succeeded, listitem = resolved[0]
    assert handle == 7
    assert succeeded is True
    assert listitem.path == "/media/a.mkv"
    # Mid-programme zap must set a start offset (join in progress).
    offset = int(listitem.properties.get("StartOffset", "0"))
    assert 0 <= offset < 6000


def test_resolver_fails_cleanly_for_unknown_channel(monkeypatch):
    _with_library(monkeypatch)
    _run_plugin(monkeypatch, "?action=play&channel=libtv.nope")

    resolved = [c for c in conftest.CALLS if c[0] == "xbmcplugin.setResolvedUrl"]
    assert len(resolved) == 1
    assert resolved[0][2] is False


def test_menu_lists_actions(monkeypatch):
    _run_plugin(monkeypatch, "")
    urls = [c[1] for c in conftest.CALLS if c[0] == "xbmcplugin.addDirectoryItem"]
    assert any("action=build" in u for u in urls)
    assert any("action=settings" in u for u in urls)
