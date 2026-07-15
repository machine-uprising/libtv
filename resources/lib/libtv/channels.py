"""Pure channel-lineup configuration: defaults, persistence, JSON-RPC filters.

No Kodi imports. A channel definition looks like:

    {"id": "libtv.custom.1", "name": "80s Action", "type": "movies",
     "genres": ["Action"], "studios": [], "year_from": 1980, "year_to": 1989,
     "order": "random"}

`type` is "movies", "episodes", or "mixed" (both movies and episodes in one
channel). Empty filter fields mean "no filter". `order` controls which items
are selected out of a larger library and their base sequence — see ORDERS
below; "random" is the default and is what gives a channel variety instead of
always the same handful of alphabetically-first shows once the library
exceeds `max_items`. The list order in channels.json is the channel (and
therefore guide) order. Channel ids are assigned once and never change — the
deterministic shuffle seed and the PVR channel identity both key off them, so
renames and reorders must not touch ids. Manually created channels get a
counter-assigned "libtv.custom.<n>" id (see `next_id`); autotune-generated
channels get a deterministic id instead so re-running autotune updates the
same channel rather than duplicating it — genre-facet autotune uses
"libtv.auto.<type>.<slug>" (see `auto_id`) and studio-facet autotune uses
"libtv.auto.studio.<type>.<slug>" (see `auto_studio_id`), kept in a separate
sub-namespace so the two facets' channels can never collide or shadow each
other.
"""
from __future__ import annotations

import json
import re

TYPES = ("movies", "episodes", "mixed")

# "random" draws a day-stable random sample from the whole filtered library
# (library.fetch_channels + schedule.shuffled) — the only order that isn't
# dominated by whatever a handful of early-alphabet items happen to be.
# "az"/"newest" ask Kodi to sort+limit server-side, so they are inherently
# capped at literally the first `max_items` matches, oldest-alphabet or
# newest-added first.
ORDERS = ("random", "az", "newest")

_CUSTOM_ID = re.compile(r"libtv\.custom\.(\d+)$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Auto-generated (autotune) channel ids are deterministic — libtv.auto.<type>.<slug>
# — rather than counter-assigned, so re-running autotune for the same
# type+label always maps to the same channel, keeping the "ids are
# permanent" invariant without needing to remember what was picked before.
AUTO_ID_PREFIX = "libtv.auto."


def default_lineup():
    """The original hardcoded lineup, used when no channels.json exists."""
    return [
        {"id": "libtv.movies", "name": "Movies", "type": "movies",
         "genres": [], "studios": [], "year_from": None, "year_to": None,
         "order": "random"},
        {"id": "libtv.tv", "name": "TV Shows", "type": "episodes",
         "genres": [], "studios": [], "year_from": None, "year_to": None,
         "order": "random"},
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
        "order": entry.get("order") if entry.get("order") in ORDERS else "random",
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


def auto_id(channel_type, label):
    """Deterministic id for a genre-autotune-generated channel.

    Same channel_type+label always yields the same id, so re-running
    autotune updates the existing channel in place instead of creating a
    duplicate with a fresh counter-assigned id.
    """
    slug = _SLUG_RE.sub("-", label.lower()).strip("-")
    return f"{AUTO_ID_PREFIX}{channel_type}.{slug}"


def is_auto(defn, channel_type=None):
    """True for a genre-autotune id. Deliberately does NOT match studio-autotune
    ids (auto_studio_id) — they live under a distinct "studio." sub-namespace
    precisely so the two facets' rebuilds never collide or shadow each other.
    """
    prefix = f"{AUTO_ID_PREFIX}{channel_type}." if channel_type else AUTO_ID_PREFIX
    return defn.get("id", "").startswith(prefix) and not is_studio_auto(defn)


def auto_studio_id(channel_type, label):
    """Deterministic id for a studio-autotune-generated channel.

    Namespaced under "libtv.auto.studio." (rather than reusing auto_id's
    "libtv.auto.<type>." shape) so studio-facet and genre-facet autotune
    channels can never collide on id even if a studio and a genre happen to
    share a label.
    """
    slug = _SLUG_RE.sub("-", label.lower()).strip("-")
    return f"{AUTO_ID_PREFIX}studio.{channel_type}.{slug}"


def is_studio_auto(defn, channel_type=None):
    prefix = (
        f"{AUTO_ID_PREFIX}studio.{channel_type}." if channel_type else f"{AUTO_ID_PREFIX}studio."
    )
    return defn.get("id", "").startswith(prefix)


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


_GROUP_LABELS = {"movies": "Movies", "episodes": "TV", "mixed": "Mixed"}
_KIND_LABELS = {"movies": "Movies", "episodes": "TV shows", "mixed": "Movies & TV shows"}
# "random" is the default and left off the summary; only call out the others.
_ORDER_LABELS = {"az": "A–Z", "newest": "Recently added"}


def group(defn):
    return _GROUP_LABELS[defn["type"]]


def describe(defn):
    """One-line human summary of a channel's source, filters, and order."""
    parts = [_KIND_LABELS[defn["type"]]]
    if defn.get("genres"):
        parts.append(", ".join(defn["genres"]))
    if defn.get("studios"):
        parts.append(", ".join(defn["studios"]))
    year_from, year_to = defn.get("year_from"), defn.get("year_to")
    if year_from or year_to:
        parts.append(f"{year_from or '…'}–{year_to or '…'}")
    order_label = _ORDER_LABELS.get(defn.get("order"))
    if order_label:
        parts.append(order_label)
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


_SORT = {
    "az": {"method": "title", "order": "ascending", "ignorearticle": True},
    "newest": {"method": "dateadded", "order": "descending"},
}


def build_sort(defn):
    """Kodi JSON-RPC List.Sort for this channel's order, or None for "random".

    "random" has no Kodi-side sort: library.fetch_channels instead pulls the
    whole filtered set and picks a day-stable random sample itself, since
    Kodi's own "random" sort method re-randomizes on every query and would
    violate schedule stability within a day.
    """
    return _SORT.get(defn.get("order"))
