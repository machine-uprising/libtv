"""Pure tests for channel-lineup configuration (channels.py)."""

import json

from libtv import channels


def _custom(**overrides):
    defn = {"id": "libtv.custom.1", "name": "80s Action", "type": "movies",
            "genres": [], "studios": [], "year_from": None, "year_to": None,
            "order": "random"}
    defn.update(overrides)
    return defn


def test_load_missing_file_returns_default_lineup(tmp_path):
    defs = channels.load(str(tmp_path / "channels.json"))
    assert [d["id"] for d in defs] == ["libtv.movies", "libtv.tv"]
    assert defs[0]["type"] == "movies"
    assert defs[1]["type"] == "episodes"


def test_load_corrupt_file_returns_default_lineup(tmp_path):
    path = tmp_path / "channels.json"
    path.write_text("{not json", encoding="utf-8")
    assert channels.load(str(path)) == channels.default_lineup()


def test_save_load_round_trip(tmp_path):
    path = str(tmp_path / "channels.json")
    defs = [_custom(genres=["Action"], year_from=1980, year_to=1989)]
    channels.save(path, defs)
    assert channels.load(path) == defs


def test_load_respects_deliberately_empty_lineup(tmp_path):
    path = tmp_path / "channels.json"
    path.write_text(json.dumps({"version": 1, "channels": []}), encoding="utf-8")
    assert channels.load(str(path)) == []


def test_load_defaults_missing_order_to_random_for_backward_compatibility(tmp_path):
    path = tmp_path / "channels.json"
    path.write_text(json.dumps({"version": 1, "channels": [
        {"id": "libtv.custom.1", "name": "Old channel", "type": "movies"},
    ]}), encoding="utf-8")
    assert channels.load(str(path))[0]["order"] == "random"


def test_load_drops_invalid_order(tmp_path):
    path = tmp_path / "channels.json"
    path.write_text(json.dumps({"version": 1, "channels": [
        {"id": "libtv.custom.1", "name": "X", "type": "movies", "order": "shuffle-everything"},
    ]}), encoding="utf-8")
    assert channels.load(str(path))[0]["order"] == "random"


def test_load_drops_malformed_entries(tmp_path):
    path = tmp_path / "channels.json"
    path.write_text(json.dumps({"version": 1, "channels": [
        {"id": "x", "name": "No type", "type": "records"},
        {"name": "No id", "type": "movies"},
        {"id": "ok", "name": "OK", "type": "movies", "year_from": "1999"},
        "garbage",
    ]}), encoding="utf-8")
    defs = channels.load(str(path))
    assert [d["id"] for d in defs] == ["ok"]
    assert defs[0]["year_from"] == 1999  # coerced to int
    assert defs[0]["genres"] == []  # missing fields filled in


def test_next_id_skips_existing_custom_ids():
    defs = channels.default_lineup() + [_custom(id="libtv.custom.7")]
    assert channels.next_id(defs) == "libtv.custom.8"
    assert channels.next_id([]) == "libtv.custom.1"


def test_move_swaps_and_respects_bounds():
    defs = channels.default_lineup()
    assert channels.move(defs, "libtv.tv", -1) is True
    assert [d["id"] for d in defs] == ["libtv.tv", "libtv.movies"]
    assert channels.move(defs, "libtv.tv", -1) is False, "already first"
    assert channels.move(defs, "libtv.nope", 1) is False


def test_build_filter_empty_is_none():
    assert channels.build_filter(_custom()) is None


def test_build_filter_single_genre_is_bare_rule():
    assert channels.build_filter(_custom(genres=["Action"])) == {
        "field": "genre", "operator": "is", "value": "Action"
    }


def test_build_filter_multiple_genres_or_together():
    assert channels.build_filter(_custom(genres=["Action", "Sci-Fi"])) == {
        "or": [
            {"field": "genre", "operator": "is", "value": "Action"},
            {"field": "genre", "operator": "is", "value": "Sci-Fi"},
        ]
    }


def test_build_filter_year_bounds_are_exclusive_string_rules():
    # Kodi filter values must be strings; 1980–1989 inclusive becomes >1979 and <1990.
    assert channels.build_filter(_custom(year_from=1980, year_to=1989)) == {
        "and": [
            {"field": "year", "operator": "greaterthan", "value": "1979"},
            {"field": "year", "operator": "lessthan", "value": "1990"},
        ]
    }


def test_build_filter_combines_all_dimensions():
    filt = channels.build_filter(
        _custom(genres=["Action"], studios=["A24", "Neon"], year_from=2010)
    )
    assert filt == {
        "and": [
            {"field": "genre", "operator": "is", "value": "Action"},
            {"or": [
                {"field": "studio", "operator": "is", "value": "A24"},
                {"field": "studio", "operator": "is", "value": "Neon"},
            ]},
            {"field": "year", "operator": "greaterthan", "value": "2009"},
        ]
    }


def test_describe_summarizes_filters():
    text = channels.describe(_custom(genres=["Action"], year_from=1980, year_to=1989))
    assert "Movies" in text
    assert "Action" in text
    assert "1980–1989" in text
    assert channels.describe(_custom(type="episodes")) == "TV shows"
    assert channels.describe(_custom(type="mixed")) == "Movies & TV shows"


def test_mixed_type_is_valid_and_groups_separately():
    assert "mixed" in channels.TYPES
    assert channels.group(_custom(type="mixed")) == "Mixed"


def test_describe_shows_non_default_order():
    assert "A–Z" in channels.describe(_custom(order="az"))
    assert "Recently added" in channels.describe(_custom(order="newest"))
    assert "Random" not in channels.describe(_custom(order="random")), \
        "the default order is left off the summary"


def test_build_sort_random_is_none():
    assert channels.build_sort(_custom(order="random")) is None


def test_build_sort_az_and_newest():
    assert channels.build_sort(_custom(order="az")) == {
        "method": "title", "order": "ascending", "ignorearticle": True
    }
    assert channels.build_sort(_custom(order="newest")) == {
        "method": "dateadded", "order": "descending"
    }
