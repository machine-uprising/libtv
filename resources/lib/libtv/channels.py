"""Pure channel-lineup configuration: defaults, persistence, JSON-RPC filters.

No Kodi imports. A channel definition looks like:

    {"id": "libtv.custom.1", "name": "80s Action", "type": "movies",
     "genres": ["Action"], "studios": [], "year_from": 1980, "year_to": 1989}

`type` is "movies" or "episodes". Empty filter fields mean "no filter".
The list order in channels.json is the channel (and therefore guide) order.
Channel ids are assigned once and never change — the deterministic shuffle
seed and the PVR channel identity both key off them, so renames and reorders
must not touch ids.
"""
from __future__ import annotations

import json
import re

TYPES = ("movies", "episodes")

_CUSTOM_ID = re.compile(r"libtv\.custom\.(\d+)$")


def default_lineup():
    """The original hardcoded lineup, used when no channels.json exists."""
    return [
        {"id": "libtv.movies", "name": "Movies", "type": "movies",
         "genres": [], "studios": [], "year_from": None, "year_to": None},
        {"id": "libtv.tv", "name": "TV Shows", "type": "episodes",
         "genres": [], "studios": [], "year_from": None, "year_to": None},
    ]


def _year(value):
    try:
        return int(value) or None
    except (TypeError, ValueError):
        return None


def _normalize(entry):
    """Return a well-formed definition, or None if the entry is unusable."""
    if not isinstance(entry, dict):
        return None
    if not entry.get("id") or not entry.get("name") or entry.get("type") not in TYPES:
        return None
    return {
        "id": str(entry["id"]),
        "name": str(entry["name"]),
        "type": entry["type"],
        "genres": [str(g) for g in entry.get("genres") or []],
        "studios": [str(s) for s in entry.get("studios") or []],
        "year_from": _year(entry.get("year_from")),
        "year_to": _year(entry.get("year_to")),
    }


def load(path):
    """Load channel definitions; missing or corrupt file ⇒ default lineup.

    An existing file with an empty channel list is respected (the user
    deleted every channel), unlike a missing one.
    """
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return default_lineup()
    if not isinstance(raw, dict) or not isinstance(raw.get("channels"), list):
        return default_lineup()
    return [d for d in (_normalize(e) for e in raw["channels"]) if d]


def save(path, definitions):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "channels": definitions}, f, indent=2)


def next_id(definitions):
    """Next unused libtv.custom.<n> id (stable — never reuses a live id)."""
    highest = 0
    for defn in definitions:
        match = _CUSTOM_ID.match(defn.get("id", ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"libtv.custom.{highest + 1}"


def find(definitions, channel_id):
    for defn in definitions:
        if defn["id"] == channel_id:
            return defn
    return None


def move(definitions, channel_id, delta):
    """Swap the channel `delta` places (±1) in the lineup. True if moved."""
    for i, defn in enumerate(definitions):
        if defn["id"] == channel_id:
            j = i + delta
            if 0 <= j < len(definitions):
                definitions[i], definitions[j] = definitions[j], definitions[i]
                return True
            return False
    return False


def group(defn):
    return "Movies" if defn["type"] == "movies" else "TV"


def describe(defn):
    """One-line human summary of a channel's source and filters."""
    parts = ["Movies" if defn["type"] == "movies" else "TV shows"]
    if defn.get("genres"):
        parts.append(", ".join(defn["genres"]))
    if defn.get("studios"):
        parts.append(", ".join(defn["studios"]))
    year_from, year_to = defn.get("year_from"), defn.get("year_to")
    if year_from or year_to:
        parts.append(f"{year_from or '…'}–{year_to or '…'}")
    return " • ".join(parts)


def _any_of(field, values):
    rules = [{"field": field, "operator": "is", "value": v} for v in values]
    return rules[0] if len(rules) == 1 else {"or": rules}


def build_filter(defn):
    """Kodi JSON-RPC List.Filter for this channel, or None for no filter.

    Filter values must be strings (Kodi rejects numbers). "is" on multi-value
    fields (genre, studio) matches membership. Episode year/genre/studio
    filter on the episode's own metadata (year = year aired).
    """
    rules = []
    if defn.get("genres"):
        rules.append(_any_of("genre", defn["genres"]))
    if defn.get("studios"):
        rules.append(_any_of("studio", defn["studios"]))
    if defn.get("year_from"):
        rules.append({"field": "year", "operator": "greaterthan",
                      "value": str(defn["year_from"] - 1)})
    if defn.get("year_to"):
        rules.append({"field": "year", "operator": "lessthan",
                      "value": str(defn["year_to"] + 1)})
    if not rules:
        return None
    return rules[0] if len(rules) == 1 else {"and": rules}
