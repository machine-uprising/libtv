"""library.py: property lists and the stream-details runtime fallback.

Kodi only fills episode `runtime` from stream details when `streamdetails`
is also requested; episode scrapers often supply no runtime at all, so
without it whole shows come back runtime=0 and get 90-minute default slots.
"""

from tests import conftest

EPISODES = {
    "episodes": [
        # Normal case: Kodi already resolved runtime (from scraper or
        # stream details) — fallback must not override it.
        {"title": "Scraped", "file": "/a.mkv", "runtime": 1800,
         "streamdetails": {"video": [{"duration": 1750}]}},
        # The bug case: no scraped runtime, duration only in stream details.
        {"title": "Unscraped", "file": "/b.mkv", "runtime": 0,
         "streamdetails": {"video": [{"duration": 2632}]}},
        # No duration anywhere: stays 0 so schedule.py applies its default.
        {"title": "Bare", "file": "/c.mkv", "runtime": 0, "streamdetails": {}},
    ]
}


def test_fetch_channels_runtime_falls_back_to_streamdetails(monkeypatch):
    from libtv import library

    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "VideoLibrary.GetEpisodes", EPISODES)
    # "az" order keeps the fetch order deterministic (Kodi is the one doing
    # the actual sort; the fake JSON-RPC just echoes the fixture) so this
    # test can assert on exact item order.
    defs = [{"id": "libtv.tv", "name": "TV Shows", "type": "episodes",
             "genres": [], "studios": [], "year_from": None, "year_to": None,
             "order": "az"}]

    items = library.fetch_channels(defs, 10, 0)[0]["items"]

    assert [it["runtime"] for it in items] == [1800, 2632, 0]
    # The bulky streamdetails blob must not leak into the schedule.
    assert all("streamdetails" not in it for it in items)


def test_fetch_channels_requests_streamdetails(monkeypatch):
    from libtv import library

    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "VideoLibrary.GetMovies", {"movies": []})
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "VideoLibrary.GetEpisodes", {"episodes": []})
    library.fetch_channels(
        [{"id": "libtv.movies", "name": "Movies", "type": "movies",
          "genres": [], "studios": [], "year_from": None, "year_to": None},
         {"id": "libtv.tv", "name": "TV Shows", "type": "episodes",
          "genres": [], "studios": [], "year_from": None, "year_to": None}],
        10,
        0,
    )

    for method in ("VideoLibrary.GetMovies", "VideoLibrary.GetEpisodes"):
        call = next(c for c in conftest.CALLS if c[1] == method)
        assert "streamdetails" in call[2]["properties"]


MOVIES = {
    "movies": [
        {"title": "Movie A", "file": "/a.mkv", "runtime": 6000, "genre": ["Action"]},
    ]
}


def test_fetch_channels_mixed_type_combines_movies_and_episodes(monkeypatch):
    from libtv import library

    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "VideoLibrary.GetMovies", MOVIES)
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "VideoLibrary.GetEpisodes", EPISODES)
    defs = [{"id": "libtv.custom.1", "name": "Mixed", "type": "mixed",
             "genres": [], "studios": [], "year_from": None, "year_to": None,
             "order": "az"}]

    result = library.fetch_channels(defs, 10, 0)[0]

    assert result["group"] == "Mixed"
    titles = [it["title"] for it in result["items"]]
    assert titles == ["Movie A", "Scraped", "Unscraped", "Bare"]


def test_fetch_channels_mixed_type_caps_combined_total(monkeypatch):
    from libtv import library

    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "VideoLibrary.GetMovies", MOVIES)
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "VideoLibrary.GetEpisodes", EPISODES)
    defs = [{"id": "libtv.custom.1", "name": "Mixed", "type": "mixed",
             "genres": [], "studios": [], "year_from": None, "year_to": None,
             "order": "az"}]

    items = library.fetch_channels(defs, 2, 0)[0]["items"]

    assert len(items) == 2


def test_fetch_channels_random_order_selects_from_full_pool_and_is_day_stable(monkeypatch):
    """The bug this fixes: with a plain per-query cap, a library sorted with
    early-alphabet shows dominating means only 1-2 shows ever get scheduled
    once the library exceeds max_items. "random" order must draw its sample
    from the whole filtered pool, and stay the same for a given anchor."""
    from libtv import library

    many_episodes = {"episodes": [
        {"title": f"Ep {i}", "file": f"/{i}.mkv", "runtime": 1800, "genre": []}
        for i in range(20)
    ]}
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "VideoLibrary.GetEpisodes", many_episodes)
    defs = [{"id": "libtv.tv", "name": "TV Shows", "type": "episodes",
             "genres": [], "studios": [], "year_from": None, "year_to": None,
             "order": "random"}]

    first = library.fetch_channels(defs, 5, 1000)[0]["items"]
    second = library.fetch_channels(defs, 5, 1000)[0]["items"]

    assert len(first) == 5
    assert first == second, "same anchor must produce the same sample"
    # The query itself must not be server-limited to max_items -- Kodi's
    # GetEpisodes response here already has all 20 in the fixture, but the
    # request params must not carry sort/limits for "random".
    rpc_call = [c for c in conftest.CALLS if c[1] == "VideoLibrary.GetEpisodes"][-1]
    assert "sort" not in rpc_call[2]
    assert "limits" not in rpc_call[2]

    third_diff_anchor = library.fetch_channels(defs, 5, 2000)[0]["items"]
    assert third_diff_anchor != first, "a different anchor should pick a different sample"


def test_fetch_genres_mixed_unions_movie_and_tvshow_genres(monkeypatch):
    from libtv import library

    def fake_json_rpc(method, params=None):
        conftest.CALLS.append(("json_rpc", method, params))
        if params.get("type") == "movie":
            return {"genres": [{"label": "Action"}]}
        return {"genres": [{"label": "Comedy"}]}

    monkeypatch.setattr(library, "json_rpc", fake_json_rpc)

    assert library.fetch_genres("mixed") == ["Action", "Comedy"]


def test_fetch_studios_mixed_unions_movie_and_tvshow_studios(monkeypatch):
    from libtv import library

    def fake_json_rpc(method, params=None):
        if method == "VideoLibrary.GetMovies":
            return {"movies": [{"studio": ["A24"]}]}
        return {"tvshows": [{"studio": ["HBO"]}]}

    monkeypatch.setattr(library, "json_rpc", fake_json_rpc)

    assert library.fetch_studios("mixed") == ["A24", "HBO"]
