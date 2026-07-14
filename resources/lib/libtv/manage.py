"""Dialog-driven channel management UI.

The "Manage channels" directory lists the lineup; every item is a command:
clicking it re-invokes the plugin, which walks the user through dialogs,
saves channels.json, rebuilds everything (schedule, M3U, XMLTV, PVR
refresh), and refreshes the container so the list reflects the change.
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
    for defn in generator.load_channel_defs():
        li = xbmcgui.ListItem(f"{defn['name']}  —  {channels.describe(defn)}")
        url = f"{base_url}?action=channel_options&channel={quote(defn['id'])}"
        xbmcplugin.addDirectoryItem(handle, url, li, False)
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


def _done(handle):
    """Close a command invocation without returning directory content."""
    xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)


def _apply(definitions):
    """Persist the lineup, rebuild all artifacts, and redraw the list."""
    generator.save_channel_defs(definitions)
    plugin.build()
    xbmc.executebuiltin("Container.Refresh")


def _ask_year(dialog, heading, current):
    raw = dialog.input(heading, str(current or ""), type=xbmcgui.INPUT_NUMERIC)
    try:
        return int(raw) or None
    except ValueError:
        return None


def _edit_filters(dialog, defn):
    """Walk the user through the filter dialogs, mutating defn in place.

    Empty selections mean "no filter". Pickers whose library query returns
    nothing (e.g. no studios scraped) are skipped; cancelling a multiselect
    keeps the current selection.
    """
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


def add_channel(handle):
    dialog = xbmcgui.Dialog()
    kind = dialog.select("Channel type", ["Movies", "TV shows"])
    if kind < 0:
        return _done(handle)
    name = dialog.input("Channel name")
    if not name:
        return _done(handle)
    definitions = generator.load_channel_defs()
    defn = {
        "id": channels.next_id(definitions),
        "name": name,
        "type": "movies" if kind == 0 else "episodes",
        "genres": [],
        "studios": [],
        "year_from": None,
        "year_to": None,
    }
    _edit_filters(dialog, defn)
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
        defn["name"], ["Rename", "Edit filters", "Move up", "Move down", "Delete"]
    )
    changed = False
    if choice == 0:
        name = dialog.input("Channel name", defn["name"])
        if name and name != defn["name"]:
            defn["name"] = name
            changed = True
    elif choice == 1:
        _edit_filters(dialog, defn)
        changed = True
    elif choice == 2:
        changed = channels.move(definitions, channel_id, -1)
    elif choice == 3:
        changed = channels.move(definitions, channel_id, +1)
    elif choice == 4:
        if dialog.yesno("Delete channel", f"Delete '{defn['name']}'?"):
            definitions.remove(defn)
            changed = True

    if changed:
        _apply(definitions)
    _done(handle)
