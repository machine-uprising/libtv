"""End-to-end tests through the fake Kodi layer: generate, then resolve."""

import json
import os
import runpy
import sys
import time

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


def test_regenerate_uses_custom_lineup_and_filters(monkeypatch):
    from libtv import generator

    _with_library(monkeypatch)
    generator.save_channel_defs([
        {"id": "libtv.custom.1", "name": "80s Action", "type": "movies",
         "genres": ["Action"], "studios": [], "year_from": 1980, "year_to": 1989},
    ])

    data = generator.regenerate()

    assert [ch["id"] for ch in data["channels"]] == ["libtv.custom.1"]
    assert data["channels"][0]["name"] == "80s Action"

    movie_queries = [c for c in conftest.CALLS
                     if c[0] == "xbmc.executeJSONRPC" and c[1] == "VideoLibrary.GetMovies"]
    assert len(movie_queries) == 1
    sent_filter = movie_queries[0][2]["filter"]
    assert {"field": "genre", "operator": "is", "value": "Action"} in sent_filter["and"]


def _with_iptv_simple(monkeypatch, enabled=True):
    monkeypatch.setitem(
        conftest.JSONRPC_RESPONSES,
        "Addons.GetAddonDetails",
        {"addon": {"addonid": "pvr.iptvsimple", "enabled": enabled}},
    )


def _toggle_calls():
    return [c[2]["enabled"] for c in conftest.CALLS
            if c[0] == "xbmc.executeJSONRPC" and c[1] == "Addons.SetAddonEnabled"]


def test_refresh_pvr_toggles_iptv_simple(monkeypatch):
    from libtv import generator

    _with_iptv_simple(monkeypatch)
    assert generator.refresh_pvr() is True
    assert _toggle_calls() == [False, True], "must disable then re-enable the client"


def test_refresh_pvr_skips_during_playback(monkeypatch):
    from libtv import generator

    _with_iptv_simple(monkeypatch)
    conftest.PLAYER.update(playing=True, file="/media/a.mkv")
    assert generator.refresh_pvr() is False
    assert _toggle_calls() == []


def test_refresh_pvr_skips_when_client_missing_or_setting_off(monkeypatch):
    from libtv import generator

    # No Addons.GetAddonDetails response = client not installed.
    assert generator.refresh_pvr() is False
    assert _toggle_calls() == []

    _with_iptv_simple(monkeypatch)
    monkeypatch.setitem(conftest.SETTINGS, "refresh_pvr", "false")
    assert generator.refresh_pvr() is False
    assert _toggle_calls() == []


def _run_plugin(monkeypatch, query):
    monkeypatch.setattr(
        sys, "argv", ["plugin://plugin.video.libtv/", "7", query]
    )
    conftest.CALLS.clear()
    runpy.run_path("default.py", run_name="__main__")


def _freeze_mid_programme(monkeypatch, data, seconds_in=1800):
    """Pin time.time() to `seconds_in` past the first movie programme."""
    start = data["channels"][0]["programmes"][0]["start"]
    monkeypatch.setattr(time, "time", lambda: start + seconds_in)
    return seconds_in


def test_resolver_plays_current_programme_and_hands_off_seek(monkeypatch):
    from libtv import generator

    _with_library(monkeypatch)
    data = generator.regenerate()
    offset = _freeze_mid_programme(monkeypatch, data, seconds_in=1800)

    _run_plugin(monkeypatch, "?action=play&channel=libtv.movies")

    resolved = [c for c in conftest.CALLS if c[0] == "xbmcplugin.setResolvedUrl"]
    assert len(resolved) == 1
    _, handle, succeeded, listitem = resolved[0]
    assert handle == 7
    assert succeeded is True
    assert listitem.path == "/media/a.mkv"
    # The seek itself happens in the service (daemon.JoinInProgressPlayer);
    # the resolver just records what to seek to.
    pending = generator.read_pending_seek()
    assert pending["file"] == "/media/a.mkv"
    assert pending["offset"] == offset


def test_resolver_writes_no_pending_seek_when_disabled(monkeypatch):
    from libtv import generator

    _with_library(monkeypatch)
    data = generator.regenerate()
    _freeze_mid_programme(monkeypatch, data, seconds_in=1800)
    monkeypatch.setitem(conftest.SETTINGS, "join_in_progress", "false")

    _run_plugin(monkeypatch, "?action=play&channel=libtv.movies")

    assert generator.read_pending_seek() is None


def test_resolver_fails_cleanly_for_unknown_channel(monkeypatch):
    from libtv import generator

    _with_library(monkeypatch)
    _run_plugin(monkeypatch, "?action=play&channel=libtv.nope")

    resolved = [c for c in conftest.CALLS if c[0] == "xbmcplugin.setResolvedUrl"]
    assert len(resolved) == 1
    assert resolved[0][2] is False
    assert generator.read_pending_seek() is None


def test_build_action_regenerates_and_refreshes_pvr(monkeypatch):
    from libtv import generator

    _with_library(monkeypatch)
    _with_iptv_simple(monkeypatch)

    _run_plugin(monkeypatch, "?action=build")

    assert generator.load_schedule() is not None
    assert _toggle_calls() == [False, True]
    notes = [c for c in conftest.CALLS if c[0] == "xbmcgui.notification"]
    assert notes == [("xbmcgui.notification", "LibTV", "Channels & guide updated")]


def test_build_action_reports_skipped_refresh(monkeypatch):
    _with_library(monkeypatch)
    _with_iptv_simple(monkeypatch)
    conftest.PLAYER.update(playing=True, file="/media/a.mkv")

    _run_plugin(monkeypatch, "?action=build")

    assert _toggle_calls() == []
    notes = [c for c in conftest.CALLS if c[0] == "xbmcgui.notification"]
    assert notes == [
        ("xbmcgui.notification", "LibTV", "Channels rebuilt — guide refresh skipped")
    ]


def test_resolver_regeneration_never_touches_pvr(monkeypatch):
    """A schedule miss during a tune regenerates, but toggling the PVR client
    mid-tune would abort the tune — it must never happen from the resolver."""
    _with_library(monkeypatch)
    _with_iptv_simple(monkeypatch)

    _run_plugin(monkeypatch, "?action=play&channel=libtv.movies")

    assert _toggle_calls() == []


def test_menu_lists_actions(monkeypatch):
    _run_plugin(monkeypatch, "")
    urls = [c[1] for c in conftest.CALLS if c[0] == "xbmcplugin.addDirectoryItem"]
    assert any("action=channels" in u for u in urls)
    assert any("action=build" in u for u in urls)
    assert any("action=settings" in u for u in urls)
