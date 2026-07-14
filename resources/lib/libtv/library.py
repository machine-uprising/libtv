"""Kodi video library access over JSON-RPC."""
from __future__ import annotations

import json

import xbmc

from libtv import channels

# "streamdetails" must stay in these lists even though the values are only
# used as a fallback: Kodi fills `runtime` from stream details ONLY when
# streamdetails is also requested. Episode scrapers often provide no runtime
# at all, so without it GetEpisodes returns runtime=0 for whole shows and
# every episode gets scheduled at the 90-minute default (live-verified).
MOVIE_PROPS = ["title", "file", "runtime", "plot", "genre", "streamdetails"]
EPISODE_PROPS = [
    "title", "file", "runtime", "plot", "showtitle", "season", "episode", "genre",
    "streamdetails",
]

# channel type -> (method, result key, properties)
_MEDIA = {
    "movies": ("VideoLibrary.GetMovies", "movies", MOVIE_PROPS),
    "episodes": ("VideoLibrary.GetEpisodes", "episodes", EPISODE_PROPS),
}


def json_rpc(method, params=None):
    request = {"jsonrpc": "2.0", "method": method, "id": 1, "params": params or {}}
    response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
    if "error" in response:
        xbmc.log(f"LibTV: JSON-RPC {method} failed: {response['error']}", xbmc.LOGERROR)
        return {}
    return response.get("result", {})


def _resolve_runtime(item):
    """Fill a missing/zero runtime from stream details, then drop them.

    Requesting streamdetails normally makes Kodi return the extracted file
    duration as `runtime` already; this explicit fallback covers versions
    that don't, and keeps the bulky streamdetails blob out of the schedule.
    """
    details = item.pop("streamdetails", None) or {}
    if not item.get("runtime"):
        video = details.get("video") or [{}]
        item["runtime"] = video[0].get("duration") or 0
    return item


def fetch_channels(definitions, max_items):
    """Query the library per channel definition and return raw channel
    definitions (unscheduled). Filters run server-side in Kodi's database."""
    out = []
    for defn in definitions:
        method, key, props = _MEDIA[defn["type"]]
        params = {"properties": props}
        filt = channels.build_filter(defn)
        if filt:
            params["filter"] = filt
        items = json_rpc(method, params).get(key, [])[:max_items]
        items = [_resolve_runtime(item) for item in items]
        out.append({
            "id": defn["id"],
            "name": defn["name"],
            "group": channels.group(defn),
            "logo": "",
            "items": items,
        })
    return out


def fetch_genres(channel_type):
    """All library genre labels for a channel type, for the filter picker."""
    kodi_type = "movie" if channel_type == "movies" else "tvshow"
    result = json_rpc(
        "VideoLibrary.GetGenres", {"type": kodi_type, "sort": {"method": "label"}}
    )
    return [g["label"] for g in result.get("genres", []) if g.get("label")]


def fetch_studios(channel_type):
    """All studio labels in the library for a channel type.

    JSON-RPC has no VideoLibrary.GetStudios, so aggregate from the items
    (shows for episode channels — episodes inherit their show's studio).
    """
    if channel_type == "movies":
        method, key = "VideoLibrary.GetMovies", "movies"
    else:
        method, key = "VideoLibrary.GetTVShows", "tvshows"
    items = json_rpc(method, {"properties": ["studio"]}).get(key, [])
    studios = set()
    for item in items:
        studios.update(s for s in item.get("studio") or [] if s)
    return sorted(studios)
