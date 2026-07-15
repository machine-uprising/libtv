"""Kodi video library access over JSON-RPC."""
from __future__ import annotations

import json

import xbmc

from libtv import channels, schedule

# "streamdetails" must stay in these lists even though the values are only
# used as a fallback: Kodi fills `runtime` from stream details ONLY when
# streamdetails is also requested. Episode scrapers often provide no runtime
# at all, so without it GetEpisodes returns runtime=0 for whole shows and
# every episode gets scheduled at the 90-minute default (live-verified).
MOVIE_PROPS = [
    "title", "file", "runtime", "plot", "genre", "year", "mpaa", "director", "cast",
    "thumbnail", "streamdetails", "rating", "playcount",
]
EPISODE_PROPS = [
    "title", "file", "runtime", "plot", "showtitle", "season", "episode", "genre",
    "firstaired", "director", "cast", "thumbnail", "streamdetails", "rating", "playcount",
]

# media kind -> (method, result key, properties)
_QUERIES = {
    "movies": ("VideoLibrary.GetMovies", "movies", MOVIE_PROPS),
    "episodes": ("VideoLibrary.GetEpisodes", "episodes", EPISODE_PROPS),
}

# channel type -> which media kinds to query
_MEDIA = {
    "movies": ("movies",),
    "episodes": ("episodes",),
    "mixed": ("movies", "episodes"),
}


def json_rpc(method, params=None):
    request = {"jsonrpc": "2.0", "method": method, "id": 1, "params": params or {}}
    response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
    if "error" in response:
        xbmc.log(f"LibTV: JSON-RPC {method} failed: {response['error']}", xbmc.LOGERROR)
        return {}
    return response.get("result", {})


def _resolve_runtime(item, runtime_cache=None):
    """Fill a missing/zero runtime from stream details, then drop them.

    Requesting streamdetails normally makes Kodi return the extracted file
    duration as `runtime` already; this explicit fallback covers versions
    that don't, and keeps the bulky streamdetails blob out of the schedule.
    If still missing, fall back to a duration actually observed during
    playback (generator.record_observed_runtime) — more reliable than
    scraped metadata for the exact file being scheduled.
    """
    details = item.pop("streamdetails", None) or {}
    if not item.get("runtime"):
        video = details.get("video") or [{}]
        item["runtime"] = video[0].get("duration") or 0
    if not item.get("runtime") and runtime_cache:
        observed = runtime_cache.get(item.get("file"))
        if observed:
            item["runtime"] = observed
    return item


def fetch_channels(definitions, max_items, anchor_epoch, runtime_cache=None):
    """Query the library per channel definition and return raw channel
    definitions (unscheduled). Filters run server-side in Kodi's database.

    Mixed channels query both movies and episodes and combine the results;
    `max_items` caps the combined per-channel total, not each query.

    Selection depends on the channel's `order` (channels.build_sort):
    "az"/"newest" ask Kodi to sort and limit server-side, so the cap always
    lands on the same alphabetically/recency-first slice. "random" (the
    default) instead pulls the whole filtered set and picks a day-stable
    random sample via `schedule.shuffled`, seeded on `anchor_epoch` — a plain
    server-side random sort would re-randomize on every regeneration and
    violate the "schedule is stable within a day" invariant.
    """
    out = []
    for defn in definitions:
        filt = channels.build_filter(defn)
        sort = channels.build_sort(defn)
        items = []
        for kind in _MEDIA[defn["type"]]:
            method, key, props = _QUERIES[kind]
            params = {"properties": props}
            if filt:
                params["filter"] = filt
            if sort:
                params["sort"] = sort
                params["limits"] = {"start": 0, "end": max_items}
            fetched = json_rpc(method, params).get(key, [])
            items.extend(_resolve_runtime(item, runtime_cache) for item in fetched)
        if sort:
            items = items[:max_items]
        else:
            items = schedule.shuffled(defn["id"], items, anchor_epoch)[:max_items]
        out.append({
            "id": defn["id"],
            "name": defn["name"],
            "group": channels.group(defn),
            "logo": "",
            "items": items,
        })
    return out


_KODI_LIBRARY_TYPES = {"movies": ("movie",), "episodes": ("tvshow",), "mixed": ("movie", "tvshow")}


def fetch_genres(channel_type):
    """All library genre labels for a channel type, for the filter picker."""
    labels = set()
    for kodi_type in _KODI_LIBRARY_TYPES[channel_type]:
        result = json_rpc(
            "VideoLibrary.GetGenres", {"type": kodi_type, "sort": {"method": "label"}}
        )
        labels.update(g["label"] for g in result.get("genres", []) if g.get("label"))
    return sorted(labels)


def fetch_studios(channel_type):
    """All studio labels in the library for a channel type.

    JSON-RPC has no VideoLibrary.GetStudios, so aggregate from the items
    (shows for episode channels — episodes inherit their show's studio).
    """
    studios = set()
    for kodi_type in _KODI_LIBRARY_TYPES[channel_type]:
        method, key = (
            ("VideoLibrary.GetMovies", "movies") if kodi_type == "movie"
            else ("VideoLibrary.GetTVShows", "tvshows")
        )
        items = json_rpc(method, {"properties": ["studio"]}).get(key, [])
        for item in items:
            studios.update(s for s in item.get("studio") or [] if s)
    return sorted(studios)
