"""Tests for M3U / XMLTV rendering."""

import xml.etree.ElementTree as ET

from libtv import writers

SCHEDULE = {
    "anchor": 1_752_364_800,
    "channels": [
        {
            "id": "libtv.movies",
            "name": "Movies",
            "group": "Movies",
            "logo": "http://logo/movies.png",
            "programmes": [
                {
                    "start": 1_752_364_800,
                    "stop": 1_752_370_800,
                    "title": "Movie A",
                    "file": "/media/a.mkv",
                    "plot": "Plot A",
                    "genre": ["Action", "Thriller"],
                    "year": 1985,
                    "mpaa": "PG-13",
                    "director": ["Dir A"],
                    "cast": [{"name": "Actor A", "role": "Hero"}, "Actor B"],
                    "icon": "http://logo/a.jpg",
                    "rating": 8.7,
                    "playcount": 0,
                },
            ],
        },
        {
            "id": "libtv.tv",
            "name": "TV Shows",
            "group": "TV",
            "logo": "",
            "programmes": [
                {
                    "start": 1_752_364_800,
                    "stop": 1_752_366_600,
                    "title": "Pilot",
                    "file": "/media/s01e01.mkv",
                    "plot": "It begins.",
                    "genre": [],
                    "showtitle": "Some Show",
                    "season": 1,
                    "episode": 1,
                    "playcount": 1,
                },
            ],
        },
    ],
}


def test_render_m3u_uses_plugin_resolver_urls():
    m3u = writers.render_m3u(SCHEDULE, "plugin.video.libtv")
    lines = m3u.strip().split("\n")

    assert lines[0] == "#EXTM3U"
    assert 'tvg-id="libtv.movies"' in lines[1]
    assert 'group-title="Movies"' in lines[1]
    assert lines[2] == "plugin://plugin.video.libtv/?action=play&channel=libtv.movies"
    assert lines[4] == "plugin://plugin.video.libtv/?action=play&channel=libtv.tv"


def test_render_xmltv_structure():
    tv = ET.fromstring(writers.render_xmltv(SCHEDULE))

    assert [c.get("id") for c in tv.findall("channel")] == ["libtv.movies", "libtv.tv"]
    assert tv.find("channel/icon").get("src") == "http://logo/movies.png"

    movie, episode = tv.findall("programme")
    assert movie.get("start") == "20250713000000 +0000"
    assert movie.get("channel") == "libtv.movies"
    assert movie.find("title").text == "Movie A"
    assert [c.text for c in movie.findall("category")] == ["Action", "Thriller"]
    assert movie.find("date").text == "1985"
    assert movie.find("rating").get("system") == "MPAA"
    assert movie.find("rating").text == "PG-13"
    assert movie.find("icon").get("src") == "http://logo/a.jpg"
    assert movie.find("new") is not None, "playcount=0 must produce an empty <new/> tag"
    assert movie.find("star-rating/value").text == "8.7/10"
    credits = movie.find("credits")
    assert credits.find("director").text == "Dir A"
    actors = credits.findall("actor")
    assert actors[0].text == "Actor A" and actors[0].get("role") == "Hero"
    assert actors[1].text == "Actor B" and actors[1].get("role") is None
    # DTD element order: title, sub-title, desc, credits, date, category,
    # icon, episode-num, new, rating, star-rating...
    assert [child.tag for child in movie] == [
        "title", "desc", "credits", "date", "category", "category", "icon",
        "new", "rating", "star-rating",
    ]

    # Episodes are titled by show, with the episode title as sub-title.
    assert episode.find("title").text == "Some Show"
    assert episode.find("sub-title").text == "Pilot"
    nums = episode.findall("episode-num")
    assert [n.get("system") for n in nums] == ["xmltv_ns", "onscreen"]
    assert nums[0].text == "0.0."
    assert nums[1].text == "S01E01"
    assert episode.find("new") is None, "playcount=1 (watched) must not get a <new/> tag"


def test_iptv_instance_settings_round_trips():
    settings = {
        "kodi_addon_instance_name": "LibTV",
        "kodi_addon_instance_enabled": "true",
        "m3uPathType": "0",
        "m3uPath": "/profile/channels.m3u",
        "m3uCache": "false",
        "epgPathType": "0",
        "epgPath": "/profile/guide.xmltv",
        "epgCache": "true",
    }

    xml_text = writers.render_iptv_instance_settings(settings)

    root = ET.fromstring(xml_text)
    assert root.tag == "settings"
    assert root.get("version") == "2"
    assert all(el.get("default") == "true" for el in root.findall("setting"))
    assert writers.parse_iptv_instance_settings(xml_text) == settings


def test_iptv_instance_settings_empty_value_round_trips_as_self_closing():
    xml_text = writers.render_iptv_instance_settings({"m3uPath": ""})
    assert "<setting" in xml_text and "/>" in xml_text
    assert writers.parse_iptv_instance_settings(xml_text) == {"m3uPath": ""}


def test_parse_iptv_instance_settings_handles_missing_or_corrupt_input():
    assert writers.parse_iptv_instance_settings("") == {}
    assert writers.parse_iptv_instance_settings("   ") == {}
    assert writers.parse_iptv_instance_settings("not xml") == {}
