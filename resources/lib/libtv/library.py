"""Kodi video library access over JSON-RPC."""
from __future__ import annotations

import json

import xbmc

MOVIE_PROPS = ["title", "file", "runtime", "plot", "genre"]
EPISODE_PROPS = ["title", "file", "runtime", "plot", "showtitle", "season", "episode", "genre"]


def json_rpc(method, params=None):
    request = {"jsonrpc": "2.0", "method": method, "id": 1, "params": params or {}}
    response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
    if "error" in response:
        xbmc.log(f"LibTV: JSON-RPC {method} failed: {response['error']}", xbmc.LOGERROR)
        return {}
    return response.get("result", {})


def fetch_channels(max_items):
    """Query the library and return raw channel definitions (unscheduled)."""
    movies = json_rpc(
        "VideoLibrary.GetMovies", {"properties": MOVIE_PROPS}
    ).get("movies", [])[:max_items]

    episodes = json_rpc(
        "VideoLibrary.GetEpisodes", {"properties": EPISODE_PROPS}
    ).get("episodes", [])[:max_items]

    return [
        {"id": "libtv.movies", "name": "Movies", "group": "Movies", "logo": "", "items": movies},
        {"id": "libtv.tv", "name": "TV Shows", "group": "TV", "logo": "", "items": episodes},
    ]
