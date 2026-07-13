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

    # Episodes are titled by show, with the episode title as sub-title.
    assert episode.find("title").text == "Some Show"
    assert episode.find("sub-title").text == "Pilot"
    assert episode.find("episode-num").text == "S01E01"
