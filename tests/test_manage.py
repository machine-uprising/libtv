"""Channel-management UI flows, driven end-to-end through default.py with
queued fake dialog answers (tests/conftest.py DIALOG_RESPONSES)."""

import os
import runpy
import sys

from libtv import channels as channels_module
from libtv import generator

from tests import conftest

MOVIES = {
    "movies": [
        {"title": "Movie A", "file": "/media/a.mkv", "runtime": 6000, "plot": "A",
         "genre": ["Action"], "studio": ["A24"]},
    ]
}
EPISODES = {
    "episodes": [
        {"title": "Pilot", "file": "/media/s01e01.mkv", "runtime": 1800, "plot": "B",
         "showtitle": "Some Show", "season": 1, "episode": 1, "genre": ["Comedy"]},
    ]
}
GENRES = {"genres": [{"label": "Action"}, {"label": "Comedy"}]}
TVSHOWS = {"tvshows": [{"studio": ["Network X"]}]}


def _with_library(monkeypatch):
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "VideoLibrary.GetMovies", MOVIES)
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "VideoLibrary.GetEpisodes", EPISODES)
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "VideoLibrary.GetGenres", GENRES)
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "VideoLibrary.GetTVShows", TVSHOWS)


def _run_plugin(monkeypatch, query):
    monkeypatch.setattr(sys, "argv", ["plugin://plugin.video.libtv/", "7", query])
    conftest.CALLS.clear()
    runpy.run_path("default.py", run_name="__main__")


def _refreshed():
    return ("xbmc.executebuiltin", "Container.Refresh") in conftest.CALLS


def test_manage_list_shows_add_and_channel_items(monkeypatch):
    _run_plugin(monkeypatch, "?action=channels")
    urls = [c[1] for c in conftest.CALLS if c[0] == "xbmcplugin.addDirectoryItem"]
    assert any("action=channel_add" in u for u in urls)
    assert any("action=channel_options&channel=libtv.movies" in u for u in urls)
    assert any("action=channel_options&channel=libtv.tv" in u for u in urls)


def test_add_channel_flow_saves_filters_and_rebuilds(monkeypatch):
    _with_library(monkeypatch)
    conftest.DIALOG_RESPONSES["select"].append(0)  # type: Movies
    conftest.DIALOG_RESPONSES["input"].extend(["80s Action", "1980", "1989"])
    conftest.DIALOG_RESPONSES["multiselect"].append([0])  # genres: Action
    # No answer queued for the studio picker that follows -> treated as
    # cancelled, which keeps the current (empty) studio selection.

    _run_plugin(monkeypatch, "?action=channel_add")

    defs = generator.load_channel_defs()
    added = [d for d in defs if d["id"] == "libtv.custom.1"]
    assert len(added) == 1
    assert added[0] == {
        "id": "libtv.custom.1", "name": "80s Action", "type": "movies",
        "genres": ["Action"], "studios": [], "year_from": 1980, "year_to": 1989,
        "order": "random",
    }
    # Rebuilt immediately: the new channel is live in the schedule.
    schedule = generator.load_schedule()
    assert "libtv.custom.1" in [ch["id"] for ch in schedule["channels"]]
    assert _refreshed()


def test_add_mixed_channel_pulls_movies_and_episodes(monkeypatch):
    _with_library(monkeypatch)
    conftest.DIALOG_RESPONSES["select"].append(2)  # type: Mixed
    conftest.DIALOG_RESPONSES["input"].extend(["Everything", "", ""])
    conftest.DIALOG_RESPONSES["multiselect"].append([])  # genres: none

    _run_plugin(monkeypatch, "?action=channel_add")

    defs = generator.load_channel_defs()
    added = [d for d in defs if d["id"] == "libtv.custom.1"]
    assert added == [{
        "id": "libtv.custom.1", "name": "Everything", "type": "mixed",
        "genres": [], "studios": [], "year_from": None, "year_to": None,
        "order": "random",
    }]
    schedule = generator.load_schedule()
    ch = next(c for c in schedule["channels"] if c["id"] == "libtv.custom.1")
    titles = {p["title"] for p in ch["programmes"]}
    assert "Movie A" in titles
    assert "Pilot" in titles
    assert _refreshed()


def test_add_channel_cancelled_changes_nothing(monkeypatch):
    _with_library(monkeypatch)  # no dialog answers queued = user cancels

    _run_plugin(monkeypatch, "?action=channel_add")

    assert not os.path.exists(generator.channels_path())
    assert not _refreshed()


def test_rename_channel(monkeypatch):
    _with_library(monkeypatch)
    conftest.DIALOG_RESPONSES["select"].append(0)  # Rename
    conftest.DIALOG_RESPONSES["input"].append("Retro TV")

    _run_plugin(monkeypatch, "?action=channel_options&channel=libtv.tv")

    defs = generator.load_channel_defs()
    renamed = [d for d in defs if d["id"] == "libtv.tv"]
    assert renamed[0]["name"] == "Retro TV", "rename must keep the channel id"
    assert _refreshed()


def test_edit_content_order(monkeypatch):
    _with_library(monkeypatch)
    conftest.DIALOG_RESPONSES["select"].extend([1, 1])  # Edit filters & order, then order: A-Z
    conftest.DIALOG_RESPONSES["multiselect"].append([])  # genres: none
    conftest.DIALOG_RESPONSES["input"].extend(["", ""])  # year bounds: blank

    _run_plugin(monkeypatch, "?action=channel_options&channel=libtv.tv")

    defs = generator.load_channel_defs()
    edited = [d for d in defs if d["id"] == "libtv.tv"][0]
    assert edited["order"] == "az"
    # The rebuild must have asked Kodi to sort+limit server-side for "az".
    call = next(c for c in conftest.CALLS if c[1] == "VideoLibrary.GetEpisodes")
    assert call[2]["sort"] == {"method": "title", "order": "ascending", "ignorearticle": True}
    assert call[2]["limits"] == {"start": 0, "end": 150}
    assert _refreshed()


def test_move_channel_up_reorders_lineup(monkeypatch):
    _with_library(monkeypatch)
    conftest.DIALOG_RESPONSES["select"].append(2)  # Move up

    _run_plugin(monkeypatch, "?action=channel_options&channel=libtv.tv")

    assert [d["id"] for d in generator.load_channel_defs()] == ["libtv.tv", "libtv.movies"]
    assert _refreshed()


def test_autotune_creates_channel_per_selected_genre(monkeypatch):
    _with_library(monkeypatch)
    conftest.DIALOG_RESPONSES["select"].append(0)  # type: Movies
    conftest.DIALOG_RESPONSES["multiselect"].append([0, 1])  # Action, Comedy

    _run_plugin(monkeypatch, "?action=autotune")

    defs = generator.load_channel_defs()
    auto = [d for d in defs if channels_module.is_auto(d)]
    assert {d["id"] for d in auto} == {
        channels_module.auto_id("movies", "Action"),
        channels_module.auto_id("movies", "Comedy"),
    }
    action = next(d for d in auto if d["genres"] == ["Action"])
    assert action["name"] == "Action Movies"
    assert action["type"] == "movies"
    assert action["order"] == "random"
    assert _refreshed()


def test_autotune_rerun_with_same_selection_is_idempotent(monkeypatch):
    _with_library(monkeypatch)
    conftest.DIALOG_RESPONSES["select"].extend([0, 0])
    conftest.DIALOG_RESPONSES["multiselect"].extend([[0], [0]])

    _run_plugin(monkeypatch, "?action=autotune")
    first = generator.load_channel_defs()
    _run_plugin(monkeypatch, "?action=autotune")
    second = generator.load_channel_defs()

    assert first == second


def test_autotune_deselecting_a_genre_removes_its_channel(monkeypatch):
    _with_library(monkeypatch)
    conftest.DIALOG_RESPONSES["select"].extend([0, 0])
    conftest.DIALOG_RESPONSES["multiselect"].extend([[0, 1], [0]])  # then drop Comedy

    _run_plugin(monkeypatch, "?action=autotune")
    _run_plugin(monkeypatch, "?action=autotune")

    ids = {d["id"] for d in generator.load_channel_defs()}
    assert channels_module.auto_id("movies", "Action") in ids
    assert channels_module.auto_id("movies", "Comedy") not in ids


def test_autotune_leaves_manual_channels_and_other_types_untouched(monkeypatch):
    _with_library(monkeypatch)
    conftest.DIALOG_RESPONSES["select"].extend([0, 1])  # Movies, then TV shows
    conftest.DIALOG_RESPONSES["multiselect"].extend([[0], [1]])  # Action movies; Comedy TV

    _run_plugin(monkeypatch, "?action=autotune")
    _run_plugin(monkeypatch, "?action=autotune")

    defs = generator.load_channel_defs()
    ids = {d["id"] for d in defs}
    assert "libtv.movies" in ids and "libtv.tv" in ids, "default lineup must survive"
    assert channels_module.auto_id("movies", "Action") in ids
    assert channels_module.auto_id("episodes", "Comedy") in ids


def test_autotune_no_genres_notifies_and_changes_nothing(monkeypatch):
    _with_library(monkeypatch)
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "VideoLibrary.GetGenres", {"genres": []})
    conftest.DIALOG_RESPONSES["select"].append(0)  # type: Movies

    _run_plugin(monkeypatch, "?action=autotune")

    assert not os.path.exists(generator.channels_path())
    notes = [c for c in conftest.CALLS if c[0] == "xbmcgui.notification"]
    assert notes == [("xbmcgui.notification", "LibTV", "No genres found in the library")]


def test_autotune_cancelled_at_type_picker_changes_nothing(monkeypatch):
    _with_library(monkeypatch)  # no select answer queued = user cancels

    _run_plugin(monkeypatch, "?action=autotune")

    assert not os.path.exists(generator.channels_path())
    assert not _refreshed()


def test_autotune_cancelled_at_genre_picker_changes_nothing(monkeypatch):
    _with_library(monkeypatch)
    conftest.DIALOG_RESPONSES["select"].append(0)  # type: Movies
    # no multiselect answer queued = user cancels the genre picker

    _run_plugin(monkeypatch, "?action=autotune")

    assert not os.path.exists(generator.channels_path())
    assert not _refreshed()


def test_autotune_studio_creates_channel_per_selected_studio(monkeypatch):
    _with_library(monkeypatch)
    conftest.DIALOG_RESPONSES["select"].append(0)  # type: Movies
    conftest.DIALOG_RESPONSES["multiselect"].append([0])  # A24

    _run_plugin(monkeypatch, "?action=autotune_studio")

    defs = generator.load_channel_defs()
    auto = [d for d in defs if channels_module.is_studio_auto(d)]
    assert {d["id"] for d in auto} == {channels_module.auto_studio_id("movies", "A24")}
    a24 = auto[0]
    assert a24["name"] == "A24 Movies"
    assert a24["studios"] == ["A24"]
    assert a24["genres"] == []
    assert a24["type"] == "movies"
    assert _refreshed()


def test_autotune_studio_rerun_with_same_selection_is_idempotent(monkeypatch):
    _with_library(monkeypatch)
    conftest.DIALOG_RESPONSES["select"].extend([0, 0])
    conftest.DIALOG_RESPONSES["multiselect"].extend([[0], [0]])

    _run_plugin(monkeypatch, "?action=autotune_studio")
    first = generator.load_channel_defs()
    _run_plugin(monkeypatch, "?action=autotune_studio")
    second = generator.load_channel_defs()

    assert first == second


def test_autotune_studio_deselecting_removes_its_channel(monkeypatch):
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "VideoLibrary.GetMovies", {
        "movies": [
            {"title": "A", "file": "/a.mkv", "runtime": 6000, "studio": ["A24"]},
            {"title": "B", "file": "/b.mkv", "runtime": 6000, "studio": ["Neon"]},
        ]
    })
    conftest.DIALOG_RESPONSES["select"].extend([0, 0])
    conftest.DIALOG_RESPONSES["multiselect"].extend([[0, 1], [0]])  # both, then drop Neon

    _run_plugin(monkeypatch, "?action=autotune_studio")
    _run_plugin(monkeypatch, "?action=autotune_studio")

    ids = {d["id"] for d in generator.load_channel_defs()}
    assert channels_module.auto_studio_id("movies", "A24") in ids
    assert channels_module.auto_studio_id("movies", "Neon") not in ids


def test_autotune_studio_and_genre_autotune_coexist_without_collision(monkeypatch):
    _with_library(monkeypatch)
    conftest.DIALOG_RESPONSES["select"].extend([0, 0])  # Movies, then Movies
    conftest.DIALOG_RESPONSES["multiselect"].extend([[0], [0]])  # genre Action; studio A24

    _run_plugin(monkeypatch, "?action=autotune")
    _run_plugin(monkeypatch, "?action=autotune_studio")

    defs = generator.load_channel_defs()
    ids = {d["id"] for d in defs}
    assert channels_module.auto_id("movies", "Action") in ids
    assert channels_module.auto_studio_id("movies", "A24") in ids
    # Neither facet's rebuild touched the other's channel.
    assert len([d for d in defs if channels_module.is_auto(d)]) == 1
    assert len([d for d in defs if channels_module.is_studio_auto(d)]) == 1


def test_autotune_studio_no_studios_notifies_and_changes_nothing(monkeypatch):
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "VideoLibrary.GetMovies", {
        "movies": [{"title": "Movie A", "file": "/media/a.mkv", "runtime": 6000}]
    })
    conftest.DIALOG_RESPONSES["select"].append(0)  # type: Movies

    _run_plugin(monkeypatch, "?action=autotune_studio")

    assert not os.path.exists(generator.channels_path())
    notes = [c for c in conftest.CALLS if c[0] == "xbmcgui.notification"]
    assert notes == [("xbmcgui.notification", "LibTV", "No studios found in the library")]


def test_autotune_studio_cancelled_at_type_picker_changes_nothing(monkeypatch):
    _with_library(monkeypatch)  # no select answer queued = user cancels

    _run_plugin(monkeypatch, "?action=autotune_studio")

    assert not os.path.exists(generator.channels_path())
    assert not _refreshed()


def test_delete_channel_requires_confirmation(monkeypatch):
    _with_library(monkeypatch)
    conftest.DIALOG_RESPONSES["select"].append(4)  # Delete
    conftest.DIALOG_RESPONSES["yesno"].append(False)  # ... but say no

    _run_plugin(monkeypatch, "?action=channel_options&channel=libtv.tv")
    assert not os.path.exists(generator.channels_path())

    conftest.DIALOG_RESPONSES["select"].append(4)
    conftest.DIALOG_RESPONSES["yesno"].append(True)
    _run_plugin(monkeypatch, "?action=channel_options&channel=libtv.tv")

    assert [d["id"] for d in generator.load_channel_defs()] == ["libtv.movies"]
    assert _refreshed()
