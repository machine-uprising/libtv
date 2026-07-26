"""Kodi video library access over JSON-RPC."""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET

import xbmc
import xbmcvfs

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

# Files.GetDirectory (used to evaluate a smart playlist, see fetch_playlist_items)
# returns whichever fields actually apply to each returned item's own type, so
# requesting the union of both property lists is harmless.
PLAYLIST_PROPS = list(dict.fromkeys(MOVIE_PROPS + EPISODE_PROPS))

# Where Kodi stores saved video smart playlists. Movies/TV-episode playlists
# may be saved either directly here or one level into movies/ or tvshows/
# subfolders — both layouts are listed.
_PLAYLISTS_ROOT = "special://profile/playlists/video/"

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


def _select(defn, items, sort, max_items, anchor_epoch):
    """Apply a channel's `order` to a fetched item pool and cap at max_items.

    "az"/"newest" were already sorted+limited server-side, so this just
    re-applies the cap (belt-and-braces for the mixed-type case, where each
    media kind was capped individually before combining). "random" (no
    server-side sort) picks a day-stable random sample via
    `schedule.shuffled`, seeded on `anchor_epoch` — a plain server-side
    random sort would re-randomize on every regeneration and violate the
    "schedule is stable within a day" invariant. Shared by both the
    filter-query and smart-playlist fetch paths in fetch_channels.
    """
    if sort:
        return items[:max_items]
    return schedule.shuffled(defn["id"], items, anchor_epoch)[:max_items]


def _fetch_filter_items(defn, max_items, runtime_cache, sort):
    filt = channels.build_filter(defn)
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
    return items


def fetch_channels(definitions, max_items, anchor_epoch, runtime_cache=None):
    """Query each channel definition's source and return raw channel
    definitions (unscheduled).

    Two sources (channels.SOURCES): "filter" queries VideoLibrary.GetMovies/
    GetEpisodes server-side per genres/studios/year_from/year_to (mixed
    channels query both kinds and combine the results; `max_items` caps the
    combined per-channel total, not each query). "smartplaylist" instead
    evaluates an existing Kodi Smart Playlist via fetch_playlist_items.
    Either way, `_select` applies the channel's `order` and the max_items cap
    the same way.
    """
    out = []
    for defn in definitions:
        sort = channels.build_sort(defn)
        if defn.get("source") == "smartplaylist":
            limits = {"start": 0, "end": max_items} if sort else None
            fetched = fetch_playlist_items(defn["playlist_path"], sort, limits)
            items = [_resolve_runtime(item, runtime_cache) for item in fetched]
        else:
            items = _fetch_filter_items(defn, max_items, runtime_cache, sort)
        items = _select(defn, items, sort, max_items, anchor_epoch)
        out.append({
            "id": defn["id"],
            "name": defn["name"],
            "group": channels.group(defn),
            "logo": "",
            "items": items,
        })
    return out


def count_matches(defn):
    """Total items this (possibly not-yet-saved) channel definition would
    currently pull, without fetching the items themselves.

    A cheap preview for the management UI — confirms a filter/playlist
    actually matches something in the library before the user commits to
    saving the channel, at the cost of one extra JSON-RPC round trip
    (properties=[] and a zero-width limits window keep the response small;
    Kodi still reports the true match count in `limits.total`).
    """
    if defn.get("source") == "smartplaylist":
        params = {
            "directory": defn["playlist_path"], "media": "video",
            "properties": [], "limits": {"start": 0, "end": 0},
        }
        result = json_rpc("Files.GetDirectory", params)
        return result.get("limits", {}).get("total", len(result.get("files", [])))
    filt = channels.build_filter(defn)
    total = 0
    for kind in _MEDIA[defn["type"]]:
        method, key, _ = _QUERIES[kind]
        params = {"properties": [], "limits": {"start": 0, "end": 0}}
        if filt:
            params["filter"] = filt
        result = json_rpc(method, params)
        total += result.get("limits", {}).get("total", len(result.get(key, [])))
    return total


def _playlists_root_os():
    return xbmcvfs.translatePath(_PLAYLISTS_ROOT)


def _xsp_filenames(os_dir):
    if not xbmcvfs.exists(os_dir):
        return []
    _dirs, files = xbmcvfs.listdir(os_dir)
    return [f for f in files if f.lower().endswith(".xsp")]


def _parse_xsp(os_path):
    """(kodi_type, name) parsed from a smart playlist file, or (None, None)
    if it can't be read/parsed."""
    try:
        with open(os_path, encoding="utf-8") as f:
            root = ET.parse(f).getroot()
    except (OSError, ET.ParseError):
        return None, None
    return root.get("type"), (root.findtext("name") or "").strip()


def fetch_playlists():
    """Available Movies/TV-episode smart playlists, for the channel-source
    picker (manage._pick_playlist).

    Only .xsp files whose declared root type is "movies" or "episodes" are
    returned — these map directly onto LibTV's own channel `type`s and query
    shape. Show-level ("tvshows") and non-video playlist types are skipped
    (see docs/architecture.md's roadmap gap).
    """
    root_os = _playlists_root_os()
    subdirs = [("", root_os)]
    if xbmcvfs.exists(root_os):
        dirs, _files = xbmcvfs.listdir(root_os)
        subdirs += [(d + "/", os.path.join(root_os, d)) for d in dirs]

    out = []
    for prefix, os_dir in subdirs:
        for filename in _xsp_filenames(os_dir):
            kodi_type, name = _parse_xsp(os.path.join(os_dir, filename))
            if kodi_type not in ("movies", "episodes"):
                continue
            out.append({
                "path": _PLAYLISTS_ROOT + prefix + filename,
                "name": name or filename[:-len(".xsp")],
                "type": kodi_type,
            })
    return out


def fetch_playlist_items(path, sort=None, limits=None):
    """Items a smart playlist currently evaluates to.

    Delegates evaluation to Kodi itself via Files.GetDirectory on the
    playlist's own special:// path, rather than reimplementing Kodi's smart-
    playlist rule engine (field/operator/group vocabulary) in Python — this
    automatically supports every rule Kodi's own Smart Playlist editor
    offers. This JSON-RPC technique is new to this codebase and not yet
    live-verified — see docs/live-testing.md.
    """
    params = {"directory": path, "media": "video", "properties": PLAYLIST_PROPS}
    if sort:
        params["sort"] = sort
    if limits:
        params["limits"] = limits
    return json_rpc("Files.GetDirectory", params).get("files", [])


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
