"""Pure schedule logic: no Kodi imports, fully unit-testable.

A schedule maps each channel to a contiguous list of programmes with epoch
start/stop times. Channels are anchored at midnight UTC and cycle through
their item list until the guide horizon is covered, like a real linear
station. Runtimes from Kodi's JSON-RPC are in SECONDS (not minutes).
"""
from __future__ import annotations

import random

DAY_SECONDS = 24 * 60 * 60
# Items with no runtime metadata are slotted as 90 minutes.
DEFAULT_RUNTIME = 90 * 60
# Guard against zero/garbage runtimes producing infinite-loop slots.
MIN_RUNTIME = 5 * 60


def day_anchor(now_epoch):
    """Midnight UTC of the day containing now_epoch."""
    return int(now_epoch) - int(now_epoch) % DAY_SECONDS


def shuffled(channel_id, items, anchor_epoch):
    """Deterministic per-channel, per-day shuffle.

    Regenerating the schedule later the same day must not change what is
    currently on air, so the seed only varies with the channel and the day.
    """
    rng = random.Random(f"{channel_id}:{anchor_epoch}")
    items = list(items)
    rng.shuffle(items)
    return items


def _programme(item, start):
    runtime = item.get("runtime") or 0
    duration = max(MIN_RUNTIME, int(runtime) or DEFAULT_RUNTIME)
    prog = {
        "start": start,
        "stop": start + duration,
        "title": item.get("title", ""),
        "file": item.get("file", ""),
        "plot": item.get("plot", ""),
        "genre": list(item.get("genre") or []),
    }
    # Episodes carry show context used by the XMLTV writer.
    for key in ("showtitle", "season", "episode"):
        if item.get(key) not in (None, ""):
            prog[key] = item[key]
    # Movies report `year`; episodes report `firstaired` (an air date) —
    # normalize both to a single "year" field for the XMLTV <date> element.
    year = item.get("year") or (str(item["firstaired"])[:4] if item.get("firstaired") else None)
    if year:
        prog["year"] = year
    if item.get("mpaa"):
        prog["mpaa"] = item["mpaa"]
    if item.get("director"):
        prog["director"] = list(item["director"])
    if item.get("cast"):
        prog["cast"] = item["cast"]
    if item.get("thumbnail"):
        prog["icon"] = item["thumbnail"]
    if item.get("rating"):
        prog["rating"] = item["rating"]
    # playcount is only meaningful if the library actually reported it —
    # absence (not requested / older Kodi) must not be mistaken for "unwatched".
    if item.get("playcount") is not None:
        prog["playcount"] = item["playcount"]
    return prog


def build_schedule(channels, anchor_epoch, until_epoch):
    """Build a schedule dict from channel definitions.

    channels: [{"id", "name", "group", "logo", "items": [library items]}]
    Programmes run back-to-back from anchor_epoch until at least until_epoch,
    cycling through the item list as many times as needed.
    """
    out = {"anchor": anchor_epoch, "channels": []}
    for ch in channels:
        programmes = []
        items = ch.get("items") or []
        cursor = anchor_epoch
        index = 0
        while items and cursor < until_epoch:
            prog = _programme(items[index % len(items)], cursor)
            programmes.append(prog)
            cursor = prog["stop"]
            index += 1
        out["channels"].append({
            "id": ch["id"],
            "name": ch["name"],
            "group": ch.get("group", ""),
            "logo": ch.get("logo", ""),
            "programmes": programmes,
        })
    return out


def find_current(schedule_data, channel_id, now_epoch):
    """Return (programme, offset_seconds) airing on channel_id at now_epoch.

    Returns None if the channel is unknown or the schedule does not cover
    now_epoch (stale schedule) — callers should regenerate and retry.
    """
    for ch in schedule_data.get("channels", []):
        if ch["id"] != channel_id:
            continue
        for prog in ch["programmes"]:
            if prog["start"] <= now_epoch < prog["stop"]:
                return prog, now_epoch - prog["start"]
        return None
    return None
