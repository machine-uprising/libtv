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
    assert generator.m3u_path() == os.path.join(prof, "channels.m3u")
    assert generator.xmltv_path() == os.path.join(prof, "guide.xmltv")
    assert os.path.exists(generator.m3u_path())
    assert os.path.exists(generator.xmltv_path())

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


def test_configure_iptv_simple_requires_client_installed(monkeypatch):
    from libtv import generator

    _with_iptv_simple(monkeypatch, enabled=False)
    assert generator.configure_iptv_simple() == "not_installed"
    assert not os.path.exists(generator.pvr_instance_settings_path())
    assert _toggle_calls() == []


def test_configure_iptv_simple_writes_instance_and_toggles(monkeypatch):
    from libtv import generator, writers

    _with_iptv_simple(monkeypatch)
    assert generator.configure_iptv_simple() == "configured"
    assert _toggle_calls() == [False, True]

    with open(generator.pvr_instance_settings_path(), encoding="utf-8") as f:
        written = writers.parse_iptv_instance_settings(f.read())
    assert written == {
        "kodi_addon_instance_name": "LibTV",
        "kodi_addon_instance_enabled": "true",
        "m3uPathType": "0",
        "m3uPath": generator.m3u_path(),
        "m3uCache": "false",
        "epgPathType": "0",
        "epgPath": generator.xmltv_path(),
        "epgCache": "true",
    }


def test_configure_iptv_simple_is_idempotent(monkeypatch):
    from libtv import generator

    _with_iptv_simple(monkeypatch)
    assert generator.configure_iptv_simple() == "configured"
    conftest.CALLS.clear()

    assert generator.configure_iptv_simple() == "unchanged"
    assert _toggle_calls() == []


def test_configure_iptv_simple_skips_during_playback(monkeypatch):
    from libtv import generator

    _with_iptv_simple(monkeypatch)
    conftest.PLAYER.update(playing=True, file="/media/a.mkv")

    assert generator.configure_iptv_simple() == "playing"
    assert not os.path.exists(generator.pvr_instance_settings_path())
    assert _toggle_calls() == []


def test_configure_iptv_simple_unchanged_never_checks_playback(monkeypatch):
    """A no-op call must not be blocked by 'something is playing' — only an
    actual write needs that guard."""
    from libtv import generator

    _with_iptv_simple(monkeypatch)
    assert generator.configure_iptv_simple() == "configured"

    conftest.PLAYER.update(playing=True, file="/media/a.mkv")
    assert generator.configure_iptv_simple() == "unchanged"


def test_refresh_pvr_skips_when_client_missing_or_setting_off(monkeypatch):
    from libtv import generator

    # No Addons.GetAddonDetails response = client not installed.
    assert generator.refresh_pvr() is False
    assert _toggle_calls() == []

    _with_iptv_simple(monkeypatch)
    monkeypatch.setitem(conftest.SETTINGS, "refresh_pvr", "false")
    assert generator.refresh_pvr() is False
    assert _toggle_calls() == []


def test_relabel_schedule_falls_back_to_regenerate_for_an_unknown_channel(monkeypatch):
    """A caller claiming content_changed=False for what turns out to be a
    channel with no existing schedule entry (e.g. a bug, or a genuinely new
    channel passed by mistake) must not silently drop it — fall back to a
    full regenerate() instead."""
    from libtv import generator

    _with_library(monkeypatch)
    generator.regenerate()
    definitions = generator.load_channel_defs()
    definitions.append({
        "id": "libtv.custom.1", "name": "New", "type": "movies",
        "genres": [], "studios": [], "year_from": None, "year_to": None,
        "order": "random",
    })
    # Real callers (manage._apply) always save before relabeling/regenerating;
    # the fallback path reads channels.json from disk (via regenerate()), not
    # the in-memory `definitions` list, so this must be persisted first too.
    generator.save_channel_defs(definitions)

    data = generator.relabel_schedule(definitions)

    ids = {ch["id"] for ch in data["channels"]}
    assert "libtv.custom.1" in ids, "must fall back to a real fetch, not silently drop the channel"


def test_runtime_cache_round_trips(monkeypatch):
    from libtv import generator

    assert generator.load_runtime_cache() == {}

    generator.record_observed_runtime("/media/a.mkv", 5432)
    generator.record_observed_runtime("/media/b.mkv", 1800)

    assert generator.load_runtime_cache() == {"/media/a.mkv": 5432, "/media/b.mkv": 1800}


def test_runtime_cache_self_invalidates_on_version_mismatch(monkeypatch):
    from libtv import generator

    generator.record_observed_runtime("/media/a.mkv", 5432)
    assert generator.load_runtime_cache() == {"/media/a.mkv": 5432}

    path = generator._runtime_cache_path()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["version"], "cache must be stamped with the add-on version that wrote it"
    data["version"] = "some-other-version"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    assert generator.load_runtime_cache() == {}, \
        "an add-on upgrade must discard a cache written by a different version"


def test_runtime_cache_ignores_zero_or_missing_duration(monkeypatch):
    from libtv import generator

    generator.record_observed_runtime("/media/a.mkv", 0)
    generator.record_observed_runtime("", 1800)
    generator.record_observed_runtime(None, 1800)

    assert generator.load_runtime_cache() == {}


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
    # the resolver just records what to seek to — primarily as a property on
    # the resolved ListItem itself, with the pending-seek file kept as a
    # fallback (see daemon.JoinInProgressPlayer._seek_offset).
    assert listitem.getProperty("libtv_seek_offset") == str(offset)
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


def test_resolver_loop_guard_skips_repeated_regen_on_persistent_miss(monkeypatch):
    from libtv import generator, plugin

    _with_library(monkeypatch)
    calls = []
    real_regenerate = generator.regenerate

    def counting_regenerate():
        calls.append(1)
        return real_regenerate()

    monkeypatch.setattr(generator, "regenerate", counting_regenerate)

    # "libtv.nope" is never on any schedule, so every resolve is a miss —
    # simulates Kodi re-invoking the resolver rapidly for a broken channel.
    plugin.play(1, "libtv.nope")
    plugin.play(2, "libtv.nope")

    assert len(calls) == 1, "a resolve within the guard window must not force another regen"
    resolved = [c for c in conftest.CALLS if c[0] == "xbmcplugin.setResolvedUrl"]
    assert [r[2] for r in resolved] == [False, False]


def test_resolver_loop_guard_allows_regen_again_after_window_passes(monkeypatch):
    from libtv import generator, plugin

    _with_library(monkeypatch)
    calls = []
    real_regenerate = generator.regenerate

    def counting_regenerate():
        calls.append(1)
        return real_regenerate()

    monkeypatch.setattr(generator, "regenerate", counting_regenerate)

    base = time.time()
    monkeypatch.setattr(time, "time", lambda: base)
    plugin.play(1, "libtv.nope")

    monkeypatch.setattr(
        time, "time", lambda: base + plugin._SCHEDULE_MISS_REGEN_GUARD_SECONDS + 1
    )
    plugin.play(2, "libtv.nope")

    assert len(calls) == 2, "once the guard window has passed a genuine miss must regen again"


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
    assert any("action=show_iptv_paths" in u for u in urls)
    assert any("action=setup_guide" in u for u in urls)
    assert any("action=auto_configure_iptv" in u for u in urls)
    assert any("action=settings" in u for u in urls)


def test_show_iptv_paths_action_displays_m3u_and_xmltv_paths(monkeypatch):
    from libtv import generator

    _run_plugin(monkeypatch, "?action=show_iptv_paths")

    views = [c for c in conftest.CALLS if c[0] == "xbmcgui.textviewer"]
    assert len(views) == 1
    _, heading, message = views[0]
    assert heading == "LibTV - IPTV Simple Client setup"
    assert generator.m3u_path() in message
    assert generator.xmltv_path() in message


def test_setup_guide_action_displays_walkthrough(monkeypatch):
    from libtv import generator

    _run_plugin(monkeypatch, "?action=setup_guide")

    views = [c for c in conftest.CALLS if c[0] == "xbmcgui.textviewer"]
    assert len(views) == 1
    _, heading, message = views[0]
    assert heading == "LibTV - Setup guide"
    assert generator.m3u_path() in message
    assert generator.xmltv_path() in message
    assert "PVR IPTV Simple Client" in message
    assert "Manage channels" in message


def test_auto_configure_iptv_action_writes_instance_and_notifies(monkeypatch):
    _with_iptv_simple(monkeypatch)

    _run_plugin(monkeypatch, "?action=auto_configure_iptv")

    assert _toggle_calls() == [False, True]
    notes = [c for c in conftest.CALLS if c[0] == "xbmcgui.notification"]
    assert notes == [
        ("xbmcgui.notification", "LibTV",
         "IPTV Simple Client configured — restart Kodi if the guide doesn't appear")
    ]


def test_auto_configure_iptv_action_reports_not_installed(monkeypatch):
    _with_iptv_simple(monkeypatch, enabled=False)

    _run_plugin(monkeypatch, "?action=auto_configure_iptv")

    notes = [c for c in conftest.CALLS if c[0] == "xbmcgui.notification"]
    assert notes == [
        ("xbmcgui.notification", "LibTV", "Install and enable PVR IPTV Simple Client first")
    ]
