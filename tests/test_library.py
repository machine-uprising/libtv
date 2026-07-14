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
    defs = [{"id": "libtv.tv", "name": "TV Shows", "type": "episodes",
             "genres": [], "studios": [], "year_from": None, "year_to": None}]

    items = library.fetch_channels(defs, 10)[0]["items"]

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
    )

    for method in ("VideoLibrary.GetMovies", "VideoLibrary.GetEpisodes"):
        call = next(c for c in conftest.CALLS if c[1] == method)
        assert "streamdetails" in call[2]["properties"]
