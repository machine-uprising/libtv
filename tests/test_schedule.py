"""Tests for the pure schedule logic."""

from libtv import schedule

ANCHOR = 1_752_364_800  # 2025-07-13 00:00:00 UTC (multiple of 86400)

ITEMS = [
    {"title": "Movie A", "file": "/media/a.mkv", "runtime": 6000, "plot": "A", "genre": ["Action"]},
    {"title": "Movie B", "file": "/media/b.mkv", "runtime": 5400, "plot": "B", "genre": ["Drama"]},
]

CHANNELS = [{"id": "libtv.movies", "name": "Movies", "group": "Movies", "logo": "", "items": ITEMS}]


def test_day_anchor_is_midnight_utc():
    assert schedule.day_anchor(ANCHOR + 12345) == ANCHOR
    assert schedule.day_anchor(ANCHOR) == ANCHOR


def test_build_schedule_is_contiguous_and_cycles():
    horizon = ANCHOR + 12 * 3600
    data = schedule.build_schedule(CHANNELS, ANCHOR, horizon)
    progs = data["channels"][0]["programmes"]

    assert progs[0]["start"] == ANCHOR
    for prev, nxt in zip(progs, progs[1:]):
        assert prev["stop"] == nxt["start"], "schedule must have no gaps"
    assert progs[-1]["stop"] >= horizon, "schedule must cover the horizon"

    # 12h of alternating 100min/90min items needs more than one pass over
    # the 2-item list — the channel must cycle.
    assert len(progs) > len(ITEMS)
    assert progs[2]["title"] == "Movie A"


def test_build_schedule_runtime_fallbacks():
    channels = [{
        "id": "c", "name": "C", "items": [
            {"title": "No runtime", "file": "/x.mkv"},
            {"title": "Bogus runtime", "file": "/y.mkv", "runtime": 3},
        ],
    }]
    data = schedule.build_schedule(channels, ANCHOR, ANCHOR + 1)
    progs = data["channels"][0]["programmes"]
    assert progs[0]["stop"] - progs[0]["start"] == schedule.DEFAULT_RUNTIME

    data = schedule.build_schedule([channels[0]], ANCHOR, ANCHOR + schedule.DEFAULT_RUNTIME + 1)
    second = data["channels"][0]["programmes"][1]
    assert second["stop"] - second["start"] == schedule.MIN_RUNTIME


def test_build_schedule_carries_through_enrichment_fields():
    channels = [{
        "id": "c", "name": "C", "items": [
            {"title": "Movie A", "file": "/a.mkv", "runtime": 6000, "year": 1985,
             "mpaa": "PG-13", "director": ["Dir A"],
             "cast": [{"name": "Actor A", "role": "Hero"}], "thumbnail": "http://x/thumb.jpg",
             "rating": 8.7, "playcount": 0},
            {"title": "Ep 1", "file": "/b.mkv", "runtime": 1800, "firstaired": "2020-05-01",
             "playcount": 2},
        ],
    }]
    data = schedule.build_schedule(channels, ANCHOR, ANCHOR + 6000 + 1)
    movie, episode = data["channels"][0]["programmes"][:2]

    assert movie["year"] == 1985
    assert movie["mpaa"] == "PG-13"
    assert movie["director"] == ["Dir A"]
    assert movie["cast"] == [{"name": "Actor A", "role": "Hero"}]
    assert movie["icon"] == "http://x/thumb.jpg"
    assert movie["rating"] == 8.7
    assert movie["playcount"] == 0
    # Episodes have no `year`, only `firstaired` — normalized to the year.
    assert episode["year"] == "2020"
    assert episode["playcount"] == 2
    assert "mpaa" not in episode
    assert "rating" not in episode


def test_build_schedule_omits_playcount_when_not_reported():
    channels = [{
        "id": "c", "name": "C", "items": [
            {"title": "Movie A", "file": "/a.mkv", "runtime": 6000},
        ],
    }]
    data = schedule.build_schedule(channels, ANCHOR, ANCHOR + 6000 + 1)
    movie = data["channels"][0]["programmes"][0]
    assert "playcount" not in movie, "absence must not be mistaken for playcount=0 (unwatched)"


def test_build_schedule_empty_channel():
    data = schedule.build_schedule(
        [{"id": "e", "name": "Empty", "items": []}], ANCHOR, ANCHOR + 3600
    )
    assert data["channels"][0]["programmes"] == []


def test_find_current_returns_programme_and_offset():
    data = schedule.build_schedule(CHANNELS, ANCHOR, ANCHOR + 6 * 3600)
    # 30 minutes into Movie A (runtime 6000s = 100min)
    found = schedule.find_current(data, "libtv.movies", ANCHOR + 1800)
    assert found is not None
    prog, offset = found
    assert prog["title"] == "Movie A"
    assert offset == 1800

    # Just after Movie A ends, Movie B is on
    prog, offset = schedule.find_current(data, "libtv.movies", ANCHOR + 6000)
    assert prog["title"] == "Movie B"
    assert offset == 0


def test_find_current_misses():
    data = schedule.build_schedule(CHANNELS, ANCHOR, ANCHOR + 3600)
    assert schedule.find_current(data, "libtv.unknown", ANCHOR) is None
    beyond = data["channels"][0]["programmes"][-1]["stop"] + 10
    assert schedule.find_current(data, "libtv.movies", beyond) is None, "stale schedule"


def test_shuffle_is_deterministic_per_day():
    items = [{"title": f"T{i}"} for i in range(20)]
    a = schedule.shuffled("ch", items, ANCHOR)
    b = schedule.shuffled("ch", items, ANCHOR)
    assert a == b, "same channel+day must give the same order"
    assert schedule.shuffled("ch", items, ANCHOR + 86400) != a
    assert schedule.shuffled("other", items, ANCHOR) != a
    assert items == [{"title": f"T{i}"} for i in range(20)], "input must not be mutated"
