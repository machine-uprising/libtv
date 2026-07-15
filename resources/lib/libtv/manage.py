"""Dialog-driven channel management UI.

The "Manage channels" directory lists the lineup; every item is a command:
clicking it re-invokes the plugin, which walks the user through dialogs,
saves channels.json, rebuilds what actually needs it (see `_apply`'s
content_changed — edits that can't affect what a channel fetches skip the
library refetch entirely), and refreshes the container so the list reflects
the change.
"""
from __future__ import annotations

from urllib.parse import quote

import xbmc
import xbmcgui
import xbmcplugin

from libtv import channels, generator, library, plugin


def show_list(base_url, handle):
    li = xbmcgui.ListItem("[B]+ Add channel[/B]")
    xbmcplugin.addDirectoryItem(handle, f"{base_url}?action=channel_add", li, False)
    li = xbmcgui.ListItem("[B]+ Auto-generate channels by genre[/B]")
    xbmcplugin.addDirectoryItem(handle, f"{base_url}?action=autotune", li, False)
    li = xbmcgui.ListItem("[B]+ Auto-generate channels by studio[/B]")
    xbmcplugin.addDirectoryItem(handle, f"{base_url}?action=autotune_studio", li, False)
    for defn in generator.load_channel_defs():
        li = xbmcgui.ListItem(f"{defn['name']}  —  {channels.describe(defn)}")
        url = f"{base_url}?action=channel_options&channel={quote(defn['id'])}"
        xbmcplugin.addDirectoryItem(handle, url, li, False)
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


def _done(handle):
    """Close a command invocation without returning directory content."""
    xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)


def _apply(definitions, content_changed=True):
    """Persist the lineup, rebuild what actually needs it, and redraw the
    list.

    content_changed=False is for edits that can't have changed what any
    channel fetches — rename, reorder, delete — so the (expensive, one
    JSON-RPC round trip per channel) library refetch is skipped entirely via
    generator.relabel_schedule, which instead patches the existing
    schedule's channel names/order/membership directly. content_changed=True
    (the default: add a channel, edit filters & order, autotune) always
    does the full fetch, since the fetch criteria themselves may have
    changed. Each call site already knows which kind of edit just happened,
    so no generic before/after diff of channels.json is needed to decide.
    """
    generator.save_channel_defs(definitions)
    if content_changed:
        plugin.build()
    else:
        plugin.build(regenerate_fn=lambda: generator.relabel_schedule(definitions))
    xbmc.executebuiltin("Container.Refresh")


def _ask_year(dialog, heading, current):
    raw = dialog.input(heading, str(current or ""), type=xbmcgui.INPUT_NUMERIC)
    try:
        return int(raw) or None
    except ValueError:
        return None


_ORDER_LABELS = {"random": "Random", "az": "A–Z", "newest": "Recently added"}


def _ask_order(dialog, current):
    values = list(channels.ORDERS)
    labels = [_ORDER_LABELS[v] for v in values]
    preselect = values.index(current) if current in values else 0
    choice = dialog.select("Content order", labels, preselect=preselect)
    return values[choice] if choice >= 0 else current


def _edit_filters(dialog, defn):
    """Walk the user through the content-order and filter dialogs, mutating
    defn in place.

    Empty selections mean "no filter". Pickers whose library query returns
    nothing (e.g. no studios scraped) are skipped; cancelling a multiselect
    keeps the current selection. Cancelling the order picker also keeps the
    current value (it defaults to "random" for new channels).
    """
    defn["order"] = _ask_order(dialog, defn.get("order", "random"))
    genres = library.fetch_genres(defn["type"])
    if genres:
        preselect = [i for i, g in enumerate(genres) if g in defn["genres"]]
        picked = dialog.multiselect("Genres (none selected = all)", genres, preselect=preselect)
        if picked is not None:
            defn["genres"] = [genres[i] for i in picked]
    studios = library.fetch_studios(defn["type"])
    if studios:
        preselect = [i for i, s in enumerate(studios) if s in defn["studios"]]
        picked = dialog.multiselect("Studios (none selected = all)", studios, preselect=preselect)
        if picked is not None:
            defn["studios"] = [studios[i] for i in picked]
    defn["year_from"] = _ask_year(dialog, "First year (blank = no limit)", defn.get("year_from"))
    defn["year_to"] = _ask_year(dialog, "Last year (blank = no limit)", defn.get("year_to"))


_KINDS = ["Movies", "TV shows", "Mixed (Movies & TV shows)"]
_KIND_TYPES = ["movies", "episodes", "mixed"]


def _preview_match_count(defn):
    """Non-blocking preview of how many library items the current filter
    combination matches, shown right after editing filters and before the
    channel is actually saved — catches "this filter matches nothing" (or
    "matches way more than I meant") before the user commits to it.
    """
    count = library.count_matches(defn)
    noun = "item" if count == 1 else "items"
    verb = "matches" if count == 1 else "match"
    xbmcgui.Dialog().notification(
        "LibTV", f"{count} {noun} {verb} this channel", xbmcgui.NOTIFICATION_INFO, 3000
    )


def add_channel(handle):
    dialog = xbmcgui.Dialog()
    kind = dialog.select("Channel type", _KINDS)
    if kind < 0:
        return _done(handle)
    name = dialog.input("Channel name")
    if not name:
        return _done(handle)
    definitions = generator.load_channel_defs()
    defn = {
        "id": channels.next_id(definitions),
        "name": name,
        "type": _KIND_TYPES[kind],
        "genres": [],
        "studios": [],
        "year_from": None,
        "year_to": None,
        "order": "random",
    }
    _edit_filters(dialog, defn)
    _preview_match_count(defn)
    definitions.append(defn)
    _apply(definitions)
    _done(handle)


def channel_options(handle, channel_id):
    dialog = xbmcgui.Dialog()
    definitions = generator.load_channel_defs()
    defn = channels.find(definitions, channel_id)
    if defn is None:
        xbmc.executebuiltin("Container.Refresh")
        return _done(handle)

    choice = dialog.select(
        defn["name"], ["Rename", "Edit filters & order", "Move up", "Move down", "Delete"]
    )
    changed = False
    # Only "Edit filters & order" can change what the channel fetches; the
    # rest (rename/reorder/delete) let _apply take the cheap relabel-only
    # path instead of a full library refetch (generator.relabel_schedule).
    content_changed = False
    if choice == 0:
        name = dialog.input("Channel name", defn["name"])
        if name and name != defn["name"]:
            defn["name"] = name
            changed = True
    elif choice == 1:
        _edit_filters(dialog, defn)
        _preview_match_count(defn)
        changed = True
        content_changed = True
    elif choice == 2:
        changed = channels.move(definitions, channel_id, -1)
    elif choice == 3:
        changed = channels.move(definitions, channel_id, +1)
    elif choice == 4:
        if dialog.yesno("Delete channel", f"Delete '{defn['name']}'?"):
            definitions.remove(defn)
            changed = True

    if changed:
        _apply(definitions, content_changed=content_changed)
    _done(handle)


_AUTO_SUFFIX = {"movies": "Movies", "episodes": "TV", "mixed": "Movies & TV"}


def _rebuild_autotune(existing, channel_type, genres, selected):
    """Replace this type's autotune channels with one per selected genre.

    Manually created channels, and autotune channels of other types, are
    left untouched. Ids are deterministic (channels.auto_id), so a genre
    that was selected before and still is keeps the same channel — reselecting
    is a no-op for it — while a deselected genre's channel is dropped.
    """
    kept = [d for d in existing if not channels.is_auto(d, channel_type)]
    auto_defs = [
        {
            "id": channels.auto_id(channel_type, genre),
            "name": f"{genre} {_AUTO_SUFFIX[channel_type]}",
            "type": channel_type,
            "genres": [genre],
            "studios": [],
            "year_from": None,
            "year_to": None,
            "order": "random",
        }
        for genre in genres
        if genre in selected
    ]
    return kept + auto_defs


def autotune_genres(handle):
    """Auto-generate one channel per selected library genre.

    Presents the type picker (Movies / TV shows / Mixed) already used by
    add_channel, then a multiselect of every genre in the library for that
    type, preselected with whatever autotune channels of that type already
    exist. Confirming rebuilds exactly the set of auto channels for that
    type to match the new selection — unlike a manually added channel, no
    per-channel filter editing dialog is needed since the genre filter is
    the whole point.
    """
    dialog = xbmcgui.Dialog()
    kind = dialog.select("Auto-generate channels: content type", _KINDS)
    if kind < 0:
        return _done(handle)
    channel_type = _KIND_TYPES[kind]

    genres = library.fetch_genres(channel_type)
    if not genres:
        dialog.notification(
            "LibTV", "No genres found in the library", xbmcgui.NOTIFICATION_INFO, 5000
        )
        return _done(handle)

    definitions = generator.load_channel_defs()
    existing = {d["id"] for d in definitions if channels.is_auto(d, channel_type)}
    preselect = [i for i, g in enumerate(genres) if channels.auto_id(channel_type, g) in existing]
    picked = dialog.multiselect(
        "Genres to auto-create as channels (unchecking removes the channel)",
        genres,
        preselect=preselect,
    )
    if picked is None:
        return _done(handle)

    selected = {genres[i] for i in picked}
    definitions = _rebuild_autotune(definitions, channel_type, genres, selected)
    _apply(definitions)
    _done(handle)


def _rebuild_studio_autotune(existing, channel_type, studios, selected):
    """Studio-facet counterpart of _rebuild_autotune — same replace-in-place
    behavior, but scoped to studio-autotune ids (channels.is_studio_auto) so
    it never touches manually created channels or genre-autotune channels.
    """
    kept = [d for d in existing if not channels.is_studio_auto(d, channel_type)]
    auto_defs = [
        {
            "id": channels.auto_studio_id(channel_type, studio),
            "name": f"{studio} {_AUTO_SUFFIX[channel_type]}",
            "type": channel_type,
            "genres": [],
            "studios": [studio],
            "year_from": None,
            "year_to": None,
            "order": "random",
        }
        for studio in studios
        if studio in selected
    ]
    return kept + auto_defs


def autotune_studios(handle):
    """Auto-generate one channel per selected library studio.

    Studio-facet counterpart of autotune_genres — same flow (type picker,
    then a multiselect of every studio for that type, preselected with
    existing studio-autotune channels), kept as a separate action rather than
    folded into autotune_genres so a facet picker doesn't get added to what
    was a one-step flow, and so genre and studio autotune channels stay in
    clearly distinct id sub-namespaces (channels.auto_studio_id).
    """
    dialog = xbmcgui.Dialog()
    kind = dialog.select("Auto-generate channels: content type", _KINDS)
    if kind < 0:
        return _done(handle)
    channel_type = _KIND_TYPES[kind]

    studios = library.fetch_studios(channel_type)
    if not studios:
        dialog.notification(
            "LibTV", "No studios found in the library", xbmcgui.NOTIFICATION_INFO, 5000
        )
        return _done(handle)

    definitions = generator.load_channel_defs()
    existing = {d["id"] for d in definitions if channels.is_studio_auto(d, channel_type)}
    preselect = [
        i for i, s in enumerate(studios) if channels.auto_studio_id(channel_type, s) in existing
    ]
    picked = dialog.multiselect(
        "Studios to auto-create as channels (unchecking removes the channel)",
        studios,
        preselect=preselect,
    )
    if picked is None:
        return _done(handle)

    selected = {studios[i] for i in picked}
    definitions = _rebuild_studio_autotune(definitions, channel_type, studios, selected)
    _apply(definitions)
    _done(handle)
