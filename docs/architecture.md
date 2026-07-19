# LibTV — Application Design

Canonical design document. If the code and this document disagree, one of
them is wrong — fix whichever it is in the same change. Companion docs:
`docs/live-testing.md` (verification checklist), `CLAUDE.md` (development
constraints and gotchas), `README.md` (user-facing overview).

## 1. What LibTV is

LibTV (`plugin.video.libtv`) turns a Kodi video library into linear TV
channels with a full EPG. It does **not** render TV itself — it generates the
inputs for Kodi's native PVR stack and resolves streams on demand:

```
┌─────────────────────┐  JSON-RPC   ┌───────────────────────────┐
│ Kodi video library  │ ──────────► │ LibTV generator           │
│ (movies, episodes)  │             │ (service, every N hours)  │
└─────────────────────┘             └────────────┬──────────────┘
                                                 │ writes
                          ┌──────────────────────▼──────────────────────┐
                          │ profile dir (userdata/addon_data/…/libtv/)  │
                          │  schedule.json   ← single source of truth   │
                          │  channels.m3u    ← derived                  │
                          │  guide.xmltv     ← derived                  │
                          └──────────────────────┬──────────────────────┘
                                                 │ consumed by
                                    ┌────────────▼──────────────┐
                                    │ PVR IPTV Simple Client    │
                                    │ Kodi Live TV UI + guide   │
                                    └────────────┬──────────────┘
                                                 │ tune: plays plugin:// URL
                                    ┌────────────▼──────────────┐
                                    │ LibTV resolver (plugin.py)│
                                    │ "what is on air NOW?"     │
                                    └───────────────────────────┘
```

## 2. Components

| File | Role |
| --- | --- |
| `addon.xml` | Manifest. Four extension points: `xbmc.python.pluginsource` → `default.py`, `xbmc.service` → `service.py`, `xbmc.python.script` → `context.py` (makes `RunScript(plugin.video.libtv)` work from a keymap), `kodi.context.item` → `context.py` (unreliable in testing, §6a). |
| `default.py`, `service.py`, `context.py` | Thin entry shims (≤15 lines each, enforced by kodi-addon-checker). Add `resources/lib` to `sys.path` and delegate. `context.py` is invoked either via `RunScript` or Kodi's context-menu calling convention — neither passes a `plugin://` argv triple, so it never goes through `plugin.run`. |
| `resources/lib/libtv/schedule.py` | **Pure.** Schedule building and lookup. No Kodi imports. |
| `resources/lib/libtv/writers.py` | **Pure.** Renders M3U and XMLTV strings from a schedule dict, and renders/parses `pvr.iptvsimple` instance-settings XML (`render_iptv_instance_settings`/`parse_iptv_instance_settings`, §7). |
| `resources/lib/libtv/channels.py` | **Pure.** Channel-lineup configuration: `channels.json` load/save, default lineup, id allocation, reorder, JSON-RPC filter building (`build_filter`), and per-channel selection-order building (`build_sort`). |
| `resources/lib/libtv/library.py` | Kodi JSON-RPC library queries (`VideoLibrary.GetMovies` / `GetEpisodes`, filtered and sorted per channel definition) plus genre/studio pickers for the management UI. Resolves each item's runtime, falling back to stream-details duration then an observed-playback-duration cache (§4); for `order: random` also does the day-stable selection (§4). |
| `resources/lib/libtv/generator.py` | Orchestration: fetch → schedule → write all artifacts (`regenerate`), or patch just the persisted schedule's channel metadata without a library refetch (`relabel_schedule`, §3). Owns the profile directory, the pending-seek handoff file, the version-stamped observed-runtime cache, the PVR refresh (`refresh_pvr`), and the IPTV Simple instance auto-configuration (`configure_iptv_simple`, §7). |
| `resources/lib/libtv/plugin.py` | Plugin routing: menu, build action, the setup guide, IPTV Simple setup-paths, and auto-configure info dialogs (`show_setup_guide`, `show_iptv_setup_info`, `auto_configure_iptv_simple`, §7), and the stream resolver (`play`), including its schedule-miss loop guard (§6). |
| `resources/lib/libtv/manage.py` | Dialog-driven channel management UI (add/rename/filter/order/reorder/delete) plus genre- and studio-based channel autotune (§3). |
| `resources/lib/libtv/daemon.py` | Service loop (periodic regeneration, self-healing PVR-refresh retry) + `JoinInProgressPlayer` (the seek half of join-in-progress; also records observed durations). |
| `resources/lib/libtv/overlay.py` | In-playback EPG overlay (§6a): a code-only `xbmcgui.WindowDialog` listing every channel's Now/Next, read-only against `schedule.json`. |
| `resources/lib/libtv/keymap.py` | In-playback EPG overlay (§6a): pure key validation (`valid_key`) and keymap XML rendering (`render_keymap_xml`), plus `apply_from_settings()` which writes/removes `special://profile/keymaps/libtv.xml` from the "Hotkey"/"Save hotkey now" settings. |
| `resources/settings.xml` + `resources/language/…/strings.po` | New-format settings; labels are msgids in the 32100 range. |

## 3. The channel lineup

Channel definitions live in `channels.json` in the profile dir, owned by the
pure `channels.py` module. When the file is missing (fresh install) the
default lineup applies: `libtv.movies` (all movies) and `libtv.tv` (all
episodes). An *existing* file with an empty list is respected — the user
deleted every channel.

```json
{
  "version": 1,
  "channels": [
    {
      "id": "libtv.custom.1", "name": "80s Action", "type": "movies",
      "genres": ["Action"], "studios": [], "year_from": 1980, "year_to": 1989,
      "order": "random"
    }
  ]
}
```

- `type` is `movies`, `episodes`, or `mixed` (movies and episodes combined in
  one channel, queried and merged by `library.fetch_channels`). Empty filter
  fields mean "no filter".
- **List order is channel order** — it drives the M3U order and therefore the
  guide order. Reordering is just reordering the list.
- **Ids are allocated once and never change or get reused**: the
  deterministic shuffle seed and the PVR channel identity both key off the
  id, so renames and reorders must not touch it. Manually created channels
  get a counter-assigned `libtv.custom.<n>` id (`channels.next_id`).
  Autotune-generated channels (below) instead get a *deterministic*
  `libtv.auto.<type>.<slug>` id (`channels.auto_id`) derived from the
  channel type and genre label, so re-running autotune for the same
  type+genre always maps onto the same channel rather than creating a
  duplicate.
- Filters translate to Kodi JSON-RPC `List.Filter` clauses
  (`channels.build_filter`) and run server-side in Kodi's database: genres and
  studios OR within a field (`is` matches membership on multi-value fields),
  dimensions AND together, and year bounds become exclusive
  `greaterthan`/`lessthan` rules with **string** values (Kodi rejects
  numbers). For episode channels, genre/studio/year filter on the episode's
  own metadata (year = year aired; studio inherited from the show). A `mixed`
  channel applies the same filter to both the `GetMovies` and `GetEpisodes`
  queries.
- `order` (`channels.ORDERS`: `random` / `az` / `newest`, default `random`)
  controls which items are pulled out of a filtered set larger than
  `max_items`, and their base sequence — see §4 for how each is implemented.
  Missing/invalid `order` values (older `channels.json` files predate this
  field) normalize to `random`.
- The management UI (`manage.py`, reachable from the add-on menu and a
  settings button) edits the file via dialogs; every mutation saves and
  refreshes the PVR client immediately, but only rebuilds what actually
  needs it (**diff-driven invalidation**, below). Genre and studio pickers
  are populated from the library (`library.fetch_genres`,
  `library.fetch_studios` — JSON-RPC has no `GetStudios`, so studios are
  aggregated from movie/show items); for a `mixed` channel these union the
  movie and tvshow results.
- **Diff-driven invalidation**: `manage._apply(definitions, content_changed)`
  is the single choke point every mutation goes through.
  `content_changed=True` (add a channel, edit filters & order, autotune —
  anything that can change *what* a channel fetches) does the normal full
  `generator.regenerate()`: one JSON-RPC round trip per channel per media
  kind. `content_changed=False` (rename, move up/down, delete — nothing
  that can affect fetch criteria) instead calls
  `generator.relabel_schedule(definitions)`, which patches the *existing*
  `schedule.json`'s channel `name`/`group`/membership/order in place —
  re-rendering the M3U/XMLTV from the patched schedule but skipping the
  library fetch and programme-timing recomputation entirely. Each call site
  already knows which kind of edit just happened, so there's no need for a
  generic before/after diff of `channels.json` — the classification is just
  "did this dialog flow touch `genres`/`studios`/`year_from`/`year_to`/
  `type`/`order`, or not". `relabel_schedule` falls back to a full
  `regenerate()` if there's no schedule yet to patch (fresh install) or a
  channel in `definitions` has no matching schedule entry (a
  content_changed=False call for what turns out to be a channel `regenerate()`
  has never fetched) — safety nets, not the common path. This is scoped
  narrower than a general "staged edits" session (each dialog flow is still
  one immediate, complete action): it only removes *redundant* library
  fetches from single edits that can't need one, which matters more now
  that autotune (§3) can produce dozens of channels from one settings
  screen — a rename or reorder among them no longer re-fetches all of them.
- **Channel preview**: right after editing filters/order (add or edit flow,
  before the channel is actually saved), `manage._preview_match_count` shows
  a non-blocking notification with how many library items the current
  filter combination matches (`library.count_matches` — a `List.Filter`
  query per media kind with `properties: []` and a zero-width `limits`
  window, reading Kodi's own `limits.total` rather than fetching items).
  Catches an over-narrow (or accidentally unfiltered) channel before it's
  committed, without the cost of a full `fetch_channels`-style dry run.
- **Autotune** — two independent facets, same shape, reachable from "Manage
  channels" and their own settings buttons:
  - **Genre** (`manage.autotune_genres`) auto-generates one channel per
    selected library genre instead of configuring channels one at a time:
    pick a content type (Movies/TV shows/Mixed), then multiselect from every
    genre in the library for that type (preselected with whichever
    genre-autotune channels of that type already exist). Confirming calls
    `manage._rebuild_autotune`, which replaces *all* of that type's
    genre-autotune channels with exactly the new selection — reselecting an
    already-present genre is a no-op (same deterministic id), deselecting one
    removes its channel, and manually created channels plus autotune
    channels of the other facet or other types are left untouched. Each
    generated channel is named `"<Genre> Movies"` / `"<Genre> TV"` /
    `"<Genre> Movies & TV"`, filtered to that single genre, with
    `order: random`. Ids are `libtv.auto.<type>.<slug>` (`channels.auto_id`).
  - **Studio** (`manage.autotune_studios`) is the same flow over
    `library.fetch_studios` instead of genres — same type picker, same
    multiselect-with-preselect UX, same replace-in-place rebuild
    (`manage._rebuild_studio_autotune`), same per-channel naming pattern
    (`"<Studio> Movies"` etc.), filtered by `studios` instead of `genres`.
    Ids live in a **separate** sub-namespace,
    `libtv.auto.studio.<type>.<slug>` (`channels.auto_studio_id`), so a
    genre and a studio that happen to share a label can never collide or
    shadow each other's rebuild — `channels.is_auto` explicitly excludes
    studio-autotune ids and `channels.is_studio_auto` is the studio-facet
    equivalent of `channels.is_auto`.

## 4. The schedule model

Built in `schedule.build_schedule`, persisted as `schedule.json`.

- **Channels** come from the lineup above (`library.fetch_channels`), each
  capped at `max_items` items. For a `mixed` channel, movies and episodes are
  fetched separately (each honoring the channel's filter) and concatenated
  *before* the cap is applied, so `max_items` bounds the combined total, not
  each media kind.
- **Selection honors the channel's `order`** (`channels.build_sort`):
  - `az`/`newest` ask Kodi to sort (`title` asc / `dateadded` desc) and
    apply `List.Limits {start: 0, end: max_items}` **server-side**, so the
    cap always lands on literally the first `max_items` matches in that
    order.
  - `random` (the default) does **not** use Kodi's own `random` sort method
    — that re-randomizes on every JSON-RPC call, which would pick a
    different subset every regeneration and break the "stable within a day"
    invariant below. Instead `library.fetch_channels` pulls the *entire*
    filtered set (no `sort`/`limits` params) and calls
    `schedule.shuffled(channel_id, items, anchor)` itself, then takes the
    first `max_items` — a day-stable random sample of the whole library, not
    just whatever a handful of alphabetically-first shows/movies happen to
    be. (This is the fix for a channel that only ever seems to contain 1-2
    shows once the library exceeds the cap.)
- **Programmes are contiguous**: each channel's programmes run back-to-back
  from the anchor with no gaps, **cycling** through the item list until the
  guide horizon (`now + epg_hours`) is covered — like a real linear station.
- **Anchor**: midnight UTC of the current day (`schedule.day_anchor`),
  computed once in `generator.regenerate()` and threaded into both
  `library.fetch_channels` (for `random`-order selection above) and the
  optional global reshuffle below. The schedule is built from the anchor,
  not from "now", so what is on air at any instant is a pure function of
  (channel, items, day).
- **Deterministic global reshuffle**: independent of per-channel `order`,
  when the `shuffle` *setting* is on, every channel's already-selected items
  are additionally reordered by `random.Random(f"{channel_id}:{anchor}")`
  (`schedule.shuffled`) before scheduling — this only changes playback
  sequence, not which items were selected. Same channel + same day ⇒ same
  order. **Invariant: regenerating during the day must never change what is
  currently on air** — this is why both the `random`-order selection and the
  global reshuffle key off the same day anchor rather than wall-clock time.
- **Runtimes are SECONDS** (Kodi JSON-RPC convention). `library.py` must
  request the `streamdetails` property alongside `runtime`: Kodi only fills
  an episode's `runtime` from the file's stream details when stream details
  are requested, and episode scrapers often provide no runtime at all —
  without it, whole shows come back `runtime: 0` (verified live; the Kodi UI
  still shows the correct duration, hiding the problem). `library._resolve_runtime`
  falls back, in order: (1) `streamdetails.video[0].duration` (stripping the
  bulky blob before scheduling either way), (2) an **observed-playback-duration
  cache** (`generator.load_runtime_cache()` / `record_observed_runtime()`,
  persisted as `runtime_cache.json`) — `daemon.JoinInProgressPlayer.onAVStarted`
  records `Player().getTotalTime()` for *every* file that starts playing
  (regardless of whether a join-in-progress seek is involved), so a file
  whose library metadata never carried a usable duration self-corrects the
  first time it's actually played. Runtime still missing ⇒ 90 min default;
  runtimes under 5 min are clamped up to 5 min (`DEFAULT_RUNTIME`,
  `MIN_RUNTIME`).
- **Lookup**: `schedule.find_current(data, channel_id, now)` returns
  `(programme, offset_seconds)` or `None` (unknown channel / schedule doesn't
  cover `now`).

### schedule.json format

```json
{
  "anchor": 1752364800,
  "channels": [
    {
      "id": "libtv.movies", "name": "Movies", "group": "Movies", "logo": "",
      "programmes": [
        {
          "start": 1752364800, "stop": 1752370800,
          "title": "Pilot", "file": "/path/to/file.mkv",
          "plot": "…", "genre": ["Comedy"],
          "showtitle": "Some Show", "season": 1, "episode": 1
        }
      ]
    }
  ]
}
```

`start`/`stop` are UTC epoch seconds. `showtitle`/`season`/`episode` are only
present for episodes. A programme also carries, when the library had them:
`year` (movies' `year`, or episodes' `firstaired` normalized to just the
year), `mpaa` (movies only), `director` (list of names), `cast` (list of
`{"name", "role"}`), `icon` (from the item's `thumbnail`), `rating` (Kodi's
0–10 float, from the library's `rating` property), and `playcount` (int;
`0` means unwatched — only carried through when the library actually
reported it, so an item the library never returned a playcount for is never
mistaken for "unwatched") — all optional, all rendered into the XMLTV guide
(§5). **`schedule.json` is the contract**: the M3U, the XMLTV guide, and
playback resolution all derive from it and must never disagree.

## 5. Generated artifacts

All written by `generator.regenerate()` to the add-on profile directory
(`special://profile/addon_data/plugin.video.libtv/`), which it creates on
first use.

- **`channels.m3u`** — one `#EXTINF` per channel (`tvg-id`, `tvg-name`,
  `group-title`, `tvg-logo`) whose stream URL is
  `plugin://plugin.video.libtv/?action=play&channel=<id>`. Channels never
  point at media files directly.
- **`guide.xmltv`** — XMLTV rendered from the schedule, element order
  following `xmltv.dtd` (`title`, `sub-title`, `desc`, `credits`, `date`,
  `category`, `icon`, `episode-num`, `new`, `rating`, `star-rating`).
  Episodes are titled by show with the episode title as `<sub-title>` and
  **two** `<episode-num>` elements — `system="xmltv_ns"` (zero-based
  `season.episode.` form) and `system="onscreen"` (`SxxEyy`) — since skins
  vary in which they render. When present in the schedule, a programme also
  gets `<credits>` (`<director>`/`<actor role="…">`, capped at 5 each),
  `<date>` (year), a per-programme `<icon>`, an empty `<new/>` tag when
  `playcount == 0` (unwatched), `<rating system="MPAA">`, and
  `<star-rating><value>X.X/10</value></star-rating>` from the library's
  `rating`.
- **`schedule.json`** — see above.
- **`channels.json`** — channel lineup configuration (§3). The only artifact
  written by the management UI rather than `regenerate()`; never touched by
  regeneration.
- **`runtime_cache.json`** — `{"version": <addon version>, "entries": {file_path: observed_seconds}}`,
  written by `daemon.JoinInProgressPlayer.onAVStarted` for every file that
  starts playing; consulted by `library._resolve_runtime` (§4).
  `generator.load_runtime_cache()` discards the whole cache if its stamped
  version doesn't match the running add-on's version, so an upgrade
  self-invalidates instead of needing migration code for a cache-format
  change. Otherwise grows and is never pruned — bounded in practice by the
  size of the library actually watched.
- **`pending_seek.json`** — transient join-in-progress handoff (see §6);
  exists only between a tune and the seek. **Fallback only** — the primary
  handoff is a property on the resolved `ListItem` itself (§6); this file
  path is kept until that's live-verified across PVR channel changes, and
  can be removed once confirmed.

## 6. Stream resolution and join-in-progress

The full tune sequence (the subtle part of the design — see the history in
`CLAUDE.md` "Live-verified findings" before changing it):

1. User tunes to a channel; IPTV Simple hands the `plugin://…?action=play&channel=<id>`
   URL to the player, which invokes the resolver (`plugin.play`).
2. The resolver loads `schedule.json` and calls `find_current(now)`. On a
   miss (missing/stale schedule) it regenerates once and retries; if still
   nothing, it resolves failure. A **loop guard** protects against Kodi
   re-invoking the resolver rapidly for a channel whose schedule stays
   broken: a `Window(10000)` property (`libtv_last_schedule_miss_regen`,
   the same cross-process-visible mechanism as the seek-offset ListItem
   property, §step 3 below) records when a miss last forced a regen: a
   second miss within `plugin._SCHEDULE_MISS_REGEN_GUARD_SECONDS` (5s) skips
   the regen and just fails the resolve, instead of hammering the library
   with a full JSON-RPC rebuild on every rapid retry.
3. If `join_in_progress` is on and the offset exceeds 5 s, the resolver sets
   a `"libtv_seek_offset"` property directly on the `ListItem` it is about
   to resolve, **and** (as a fallback, see below) writes `pending_seek.json`
   — `{"file", "offset", "set_at"}` — before calling `setResolvedUrl`,
   because the resolver script can be terminated at any point after
   resolving.
4. `setResolvedUrl(handle, True, ListItem(path=programme file, properties…))`.
   The resolver exits; nothing after this point is relied upon.
5. Kodi starts playback. The **service's** `daemon.JoinInProgressPlayer`
   (an `xbmc.Player` subclass alive for the whole Kodi session) receives
   `onAVStarted`, which fires exactly when playback truly begins, and
   records the file's real duration into `runtime_cache.json` (§4)
   regardless of whether a seek is involved.
6. `JoinInProgressPlayer._seek_offset` gets the offset to seek to:
   - **Primary**: `Player().getPlayingItem().getProperty("libtv_seek_offset")`
     — Kodi's Player core retains the resolved item's own properties for as
     long as it's playing, independent of the resolver script (already
     exited) and with no rapid-zap ambiguity, since this is *the* item
     actually playing right now, not a filename match against a shared
     file. If present, `pending_seek.json` is cleared as a tidy-up (it would
     otherwise be superseded on the next tune anyway) and its value is
     unused.
   - **Fallback**: if the property is absent, read `pending_seek.json`. If
     the now-playing file matches, clear the file and use its offset. If a
     *different* file is playing (rapid zap race), leave the pending seek
     for the stream it belongs to; entries older than `PENDING_SEEK_MAX_AGE`
     (120 s) are discarded as abandoned.
7. If an offset was found, seek to `min(offset, total_duration − 10 s)`.

Why this shape (verified live on Kodi Omega/Windows, do not regress):

- Kodi **ignores the `StartOffset`** ListItem property on streams resolved
  for PVR IPTV Simple.
- The resolver **cannot seek itself** after `setResolvedUrl`: a post-resolve
  poll loop works on first tune but dies on channel changes, because Kodi
  terminates the resolver script when the previous channel's stream (same
  plugin) stops.
- `onAVStarted` in the long-lived service is immune to both problems.

**The `"libtv_seek_offset"` ListItem property is not yet live-verified** as
the *primary* handoff mechanism (it is a custom property, not `StartOffset`,
so the ignored-`StartOffset` finding above doesn't directly say whether it
survives to PVR playback the same way) — `pending_seek.json` is kept
specifically as a safety net until it is. See `docs/live-testing.md` §5 for
the verification steps; once confirmed across both first tune and channel
changes, the file-based fallback can be deleted from `plugin.py` and
`daemon.py`.

## 6a. In-playback EPG overlay

While a channel is playing, `context.py` → `overlay.show()` opens a
scrollable list of every channel with what's on now and what's next. It
exists because Kodi's own PVR Guide window keeps the video playing behind it
only on skins that support that PIP behavior (confirmed on Estuary; not
guaranteed elsewhere) — this overlay is skin-independent by construction.

Two ways to trigger it, both reaching the same `context.py`/`overlay.show()`:

- **`kodi.context.item`** — a **"LibTV guide (Now/Next)"** entry in the video
  context menu, visible only when `Player.HasVideo`. **Live-verified
  finding: this did not appear in testing** (neither via a mouse right-click
  nor the `c` key, which instead opened Kodi's own built-in channel/guide
  overlay) — root cause unknown (schema is valid per `kodi-addon-checker`;
  possibly a skin- or remote-specific context-menu binding issue). Left in
  place since it's schema-valid and may work on other skins/setups, but is
  **not** the reliable trigger.
- **`xbmc.python.script`** (added in response to the above) — makes the
  add-on directly invokable as `RunScript(plugin.video.libtv)` from any
  keymap, independent of the context-menu mechanism entirely. This is the
  trigger to actually rely on: bind it to a key of your choice via a
  `special://userdata/keymaps/*.xml` keymap (see `docs/live-testing.md`
  §5a for the exact snippet). `RunScript` resolves the add-on id through
  this extension's `library="context.py"`, so no hardcoded filesystem path
  is needed and it works the same across platforms.

- **Rendering**: `overlay._EpgOverlay` is a code-only `xbmcgui.WindowDialog`
  (no skin XML) — the first custom-rendered window in the add-on. A
  background (`xbmcgui.ControlImage` over `resources/media/overlay_bg.png`,
  a small solid semi-transparent PNG and the add-on's only bundled image
  asset) is drawn first so it sits behind everything else, since a
  code-only `WindowDialog` has no background of its own. The panel is a
  **fixed strip along the bottom margin** (`_PANEL_X/_PANEL_Y/_PANEL_W/
  _PANEL_H`), not a (near-)full-height panel — a code-only `WindowDialog`'s
  default coordinate space is 1280x720 regardless of the skin's actual
  resolution, confirmed live when an earlier `y=40..680` panel visibly
  covered nearly the whole screen.
  - **Rows are plain `xbmcgui.ControlLabel`s, reused via a scroll window,
    not an `xbmcgui.ControlList`.** `ControlList` was tried first and went
    through four live fix attempts (item-height keyword, focus timing,
    background/colors, font/single-label) that each addressed a real bug
    but still ended with **zero visual output of any kind — not even a
    focus rectangle** once the background itself was confirmed visible —
    pointing at the no-skin `ControlList` item-rendering path itself
    rather than any one parameter. Only `_VISIBLE_ROWS` `ControlLabel`s are
    ever created (not one per channel); `_EpgOverlay._render()` maps them
    to a `_scroll` window into the full row list.
  - **Every label is always given its real text at construction time**,
    including the first paint — `xbmcgui.ControlLabel(..., label=text)` —
    rather than created empty and populated via a later `setLabel()` call.
    **Live-verified finding**: labels built empty and filled from
    `onInit()` didn't paint until the *next* redraw (i.e. the first
    keypress) — nothing was visible until then. `_row_text()` computes
    each visible row's text (including the cursor marker, below) so it can
    be passed directly into the constructor on first build, and via
    `setLabel()` (confirmed reliable for *content* changes after the first
    frame) on subsequent scroll/cursor moves.
  - **The current row is marked with a text prefix (`"> "`), not a color
    change.** `ControlLabel.setLabel(textColor=...)` calls made after the
    initial render were tried first and **did not visibly change
    anything** (the earlier `ControlList` had the identical problem with
    its `selectedColor`) — text *content* changes are the one thing
    confirmed to reliably repaint, so the highlight piggybacks on that
    instead of depending on dynamic color updates a second time.
  - **Navigation is hand-rolled, not native.** `_EpgOverlay.onAction` moves
    an internal `_cursor`/`_scroll` pair — no `xbmcgui` control focus or
    `setFocus()` is used at all, since `ControlLabel`s aren't focusable in
    the first place and the goal was to depend on as little
    native-rendering behavior as possible after `ControlList`'s failure.
    **Live-verified finding**: the generic `ACTION_MOVE_UP`/
    `ACTION_MOVE_DOWN` this overlay originally listened for did nothing —
    a remote/keyboard during actual PVR playback generates
    `ACTION_CHANNEL_UP`/`ACTION_CHANNEL_DOWN` instead, which also drives
    Kodi's own native channel-preview banner *simultaneously* — `onAction`
    now handles both action pairs, which fixed the overlay's own cursor
    movement, but **the native channel-preview banner still fires
    alongside it** (confirmed live) and is a cosmetic side effect this
    add-on cannot suppress from Python — the actual tuned channel does not
    change from it, only from an explicit selection (below).
    `ACTION_SELECT_ITEM`/`ACTION_MOUSE_LEFT_CLICK` resolve the current
    cursor position to a channel id and close the window;
    `ACTION_PREVIOUS_MENU`/`ACTION_NAV_BACK` close without selecting.
- **Data**: strictly **read-only** against the persisted `schedule.json` —
  `generator.load_schedule()` plus a new pure lookup,
  `schedule.find_now_and_next(data, channel_id, now_epoch)`, returning
  `(current_or_None, next_or_None)` for one channel. Unlike `find_current`
  (§6), a miss here is a normal outcome, not a "regenerate and retry"
  signal: this code path must never call `generator.regenerate()` or
  `generator.refresh_pvr()` — either would risk aborting or disrupting the
  very playback the overlay is opened over (§7's PVR-refresh invariant
  applies here too).
- **Tuning from the overlay**: selecting a row reuses the existing resolver
  — `xbmc.executebuiltin("PlayMedia(plugin://plugin.video.libtv/?action=play&channel=<id>)")`
  — no stream-resolution logic is duplicated.
- **Setting up the keymap trigger without hand-editing files**:
  `resources/lib/libtv/keymap.py` (**pure** key validation/XML rendering —
  `valid_key`, `render_keymap_xml` — plus a thin Kodi-facing
  `apply_from_settings()`) backs a "Hotkey" text setting +
  "Save hotkey now" action button (§8). Pressing the button reads the
  configured key name, validates it (must be a safe XML element name — a
  bare Kodi key tag like `g` or `f9`), and writes
  `special://profile/keymaps/libtv.xml` binding that key to
  `RunScript(plugin.video.libtv)` in **both** the `FullscreenVideo` and
  `FullscreenLiveTV` windows — the file always contains exactly this one
  binding (duplicated across both sections), so it's safe to regenerate on
  every save. An empty key removes the file instead of writing an unusable
  one. Kodi only loads keymaps at startup, so every notification this
  emits says a restart is required.
- **Live-verified finding**: a `FullscreenVideo`-only binding produced
  **zero** effect while a PVR channel was genuinely playing full-screen —
  not even a `kodi.log` trace of `RunScript` firing, ruling out a script
  error and pointing at the window-context scoping itself. Binding
  `FullscreenLiveTV` too (now the default in `render_keymap_xml`) is the
  fix: some Kodi versions/skins still route live TV playback through that
  legacy window context rather than `FullscreenVideo`.
- **Confirmed live**: the dual-context keymap fix works — after re-saving
  the hotkey and restarting, the bound key fired `RunScript(plugin.video.libtv)`
  → `context.py` → `overlay.show()` during actual PVR playback (proven by a
  traceback originating inside `overlay.py` itself in `kodi.log`), so
  `special://profile/keymaps/` is confirmed as the right write location and
  the `xbmc.python.script` wiring is confirmed to work.
- That run then hit a real bug — `xbmcgui.ControlList(..., itemHeight=60)`
  raised `TypeError` (Kodi's actual keyword name is `_itemHeight`; see
  CLAUDE.md's hard-constraints note) — now fixed.
- The next live pass hit a second bug: `Control N in window M has been
  asked to focus, but it can't` in `kodi.log` (no traceback). Cause:
  `setFocus()` was called from `_EpgOverlay.__init__`, before `doModal()`
  had shown the window — Kodi can't focus a control on a window that isn't
  part of the active window stack yet, so the call fails silently
  (nothing thrown, just a GUI-log line and no focused control). Fixed by
  moving `setFocus()` into an `onInit()` override, the documented hook
  Kodi calls once a hand-built `Window`/`WindowDialog` has actually been
  shown. **Not yet live-verified** (§11): whether the overlay actually
  renders/behaves correctly (list display, focus/navigation, tune-on-select)
  now that both construction and focus-setting are fixed, drawn over an
  actively playing **PVR** stream specifically.

## 7. Background service and PVR refresh

`daemon.run()`: regenerates immediately at startup, then re-regenerates every
`regen_interval_hours`. Generation failures are logged and never kill the
service loop. The service also hosts `JoinInProgressPlayer` (§6) — the
instance must stay referenced for callbacks to be delivered.

IPTV Simple caches its M3U/EPG and has no reload API, so after every
regeneration `generator.refresh_pvr()` toggles the `pvr.iptvsimple` add-on
off and on over JSON-RPC (`Addons.SetAddonEnabled`); the PVR manager restarts
the client and it re-reads both files, so lineup and guide changes appear
without a Kodi restart. Guards, all of which make it return `False`:

- the `refresh_pvr` setting is off;
- **anything is playing** — toggling the client kills a live stream, so the
  refresh is skipped;
- IPTV Simple is not installed/enabled.

`refresh_pvr()` is called only from the manual build action (`plugin.build`)
and the service loop — **never from the stream resolver**: `plugin.play` also
regenerates on a schedule miss, and a toggle mid-tune would abort the tune.

**IPTV Simple can be auto-configured, but only via an unofficial technique
— there is no supported Kodi API for it.** The JSON-RPC `Addons.*`
namespace has exactly four methods (`GetAddons`/`GetAddonDetails`/
`SetAddonEnabled`/`ExecuteAddon`), none of which read or write another
add-on's settings, and Kodi core issue `xbmc/xbmc#22779` confirms even the
Python `xbmcaddon` API can't manage a multi-instance add-on's per-instance
settings. An **earlier version of this document claimed auto-configuration
was infeasible** based on a secondhand forum thread about an old PseudoTV
Live hack breaking under Kodi 20's multi-instance model — that claim was
wrong. Reading PseudoTV Live's actual current source (both its release and
`nightly` branches) showed it still auto-configures `pvr.iptvsimple`
today, by writing an instance-settings file directly rather than going
through any addon-facing API:

- Since Kodi 20 (Nexus), `pvr.iptvsimple` (and any multi-instance-capable
  add-on) is configured per-instance via
  `special://profile/addon_data/<addon-id>/instance-settings-<id>.xml`.
  This file's format — `<settings version="N"><setting id="x"
  default="true">value</setting>...</settings>` — is not add-on-specific:
  it's Kodi core's own generic settings serialization
  (`CSettingsValueXmlSerializer`), the same shape used for every add-on's
  main `settings.xml`. `kodi_addon_instance_name`/
  `kodi_addon_instance_enabled` are two more ordinary `<setting>` entries
  in that same file (auto-injected into any multi-instance add-on's schema
  by Kodi core's `CAddonSettings::AddInstanceSettings`), not a separate
  registration step or database write — so a hand-written file with the
  right `<setting>` entries is indistinguishable, to Kodi, from one its own
  GUI wrote.
- `generator.configure_iptv_simple()` builds this file via
  `writers.render_iptv_instance_settings()` (a pure function — construct
  `{setting_id: value}`, get back the XML string; `writers.
  parse_iptv_instance_settings()` is the inverse, used for the idempotency
  check below) with the fields `pvr.iptvsimple`'s own
  `resources/instance-settings.xml` schema defines for a local-path M3U/EPG
  source: `kodi_addon_instance_name`, `kodi_addon_instance_enabled`,
  `m3uPathType`/`epgPathType` (`"0"` = local path, `"1"` = remote URL — we
  always use `"0"`, pointing straight at `generator.m3u_path()`/
  `xmltv_path()`, since LibTV writes local files and runs no HTTP server),
  `m3uPath`/`epgPath`, and `m3uCache`/`epgCache`.
- The instance id is `zlib.crc32("LibTV") % 2**31` (`generator.
  _pvr_instance_id()`) — a fixed, name-derived id (the same technique
  PseudoTV Live uses) so the same file is always found and overwritten
  again on the next call, rather than accumulating duplicates.
- After writing, Kodi still needs to notice the new/changed instance file —
  `configure_iptv_simple()` reuses the exact same `Addons.SetAddonEnabled`
  off/on toggle `refresh_pvr()` already uses (factored into a shared
  `_toggle_pvr_client()`), which forces the PVR manager to reload the
  add-on and, with it, discover the instance file.
- **Idempotency comes before the playback guard, deliberately**:
  `configure_iptv_simple()` first parses whatever's already on disk
  (`parse_iptv_instance_settings`) and compares it to the desired settings
  dict; a no-op call (nothing actually needs to change) returns
  `"unchanged"` immediately without ever checking `Player().isPlaying()` —
  only an actual write is guarded against playback (same "toggling
  mid-playback kills the stream" invariant `refresh_pvr()` already
  enforces). Returns one of `"not_installed"`, `"playing"`, `"unchanged"`,
  `"configured"`.
- This is **not yet live-verified** and is deliberately **not** wired into
  the automatic background regeneration loop (`daemon.run()`) or the manual
  build action (`plugin.build`) — unlike `refresh_pvr()`, which both call
  automatically, `configure_iptv_simple()` only runs when the user presses
  the dedicated `auto_configure_iptv` action (main menu, and the first
  settings group). It's a real, working technique per another maintained
  add-on's current production code, but LibTV writing into a *different*
  add-on's own profile directory is a materially higher-risk operation than
  anything else this add-on does, and this project's own history (the EPG
  overlay, §6a) is full of things that looked correct on paper and needed
  several live round-trips to actually work — auto-configuration earns a
  spot in the automatic path only after it's been proven live, not before.
- `plugin.show_iptv_setup_info()` (the `show_iptv_paths` action) remains as
  the manual fallback — showing the two paths in a dialog to copy/paste by
  hand — for when auto-configuration doesn't work on a given setup, or
  before it's been live-verified at all.

`plugin.show_setup_guide()` (the `setup_guide` action, reachable from both
the main menu — first item — and a settings button that is also the first
group in the settings screen, §8) is a broader, numbered walkthrough of the
whole first-run flow (scan the library, optionally customize channels,
rebuild, install/enable IPTV Simple, run auto-configure or paste in the two
paths by hand, open the TV section, optionally bind the EPG overlay hotkey)
— a single, prominent starting point for a brand-new install, with
`show_iptv_setup_info` remaining as the narrower dialog for re-finding just
the paths later.

**Self-healing refresh retry**: when a regen cycle's `refresh_pvr()` is
skipped specifically because something was playing, `daemon.run()` doesn't
just wait for the next full `regen_interval_hours` cycle — it retries *only*
the toggle every `daemon.PVR_RETRY_SECONDS` (30s) via
`xbmc.Monitor.waitForAbort`, until either it succeeds or the next scheduled
full regen supersedes it. The full-regen cadence itself is unaffected: the
retry loop tracks its own next-regen deadline (`next_regen`) so a long
playback session delays only the guide *refresh*, never the underlying
schedule/M3U/XMLTV regeneration. (Skips for other reasons — the setting is
off, or the client isn't installed — are not retried, since those aren't
transient.)

## 8. Settings

| id | type | default | effect |
| --- | --- | --- | --- |
| `setup_guide` | action | — | `RunPlugin(…?action=setup_guide)` — first-run walkthrough dialog (§7); first group in the settings screen. |
| `auto_configure_iptv` | action | — | `RunPlugin(…?action=auto_configure_iptv)` — writes/refreshes a dedicated "LibTV" `pvr.iptvsimple` instance via `generator.configure_iptv_simple()` (§7); an unofficial technique, not yet live-verified. |
| `max_items` | integer 10–1000 | 150 | Cap on library items pulled per channel (§3/§4 `order` controls *which* items land within the cap). |
| `shuffle` | boolean | true | Deterministic per-day reshuffle of each channel's already-selected items, on top of the per-channel `order`. |
| `epg_hours` | integer 6–72 | 24 | Guide horizon: schedule covers `now + epg_hours`. |
| `regen_interval_hours` | integer 1–24 | 6 | Service regeneration period. |
| `join_in_progress` | boolean | true | Seek into the current programme on tune (§6). |
| `refresh_pvr` | boolean | true | Toggle IPTV Simple after rebuilds so the guide reloads (§7). |
| `manage_channels` | action | — | `ActivateWindow(Videos,…?action=channels,return)` — opens the management UI (§3). |
| `autotune_channels` | action | — | `RunPlugin(…?action=autotune)` — genre-based channel autotune (§3). |
| `autotune_studio_channels` | action | — | `RunPlugin(…?action=autotune_studio)` — studio-based channel autotune (§3). |
| `regenerate_now` | action | — | `RunPlugin(…?action=build)`. |
| `show_iptv_paths` | action | — | `RunPlugin(…?action=show_iptv_paths)` — shows the M3U/XMLTV paths to paste into IPTV Simple's own settings (§7); does not configure IPTV Simple itself. |
| `overlay_hotkey_key` | string | `g` | Kodi keymap key name for the in-playback EPG overlay (§6a); blank removes the binding. |
| `overlay_hotkey_apply` | action | — | `RunPlugin(…?action=apply_keymap)` — `keymap.apply_from_settings()` writes/removes `special://profile/keymaps/libtv.xml` (§6a). |

Adding a setting requires: `resources/settings.xml` entry (new format:
`<level>`, `<default>`, `<control>`), a msgid in `strings.po`, and a mirrored
default in `tests/conftest.py` `SETTINGS`.

## 9. Testing strategy

- **Unit tests** run outside Kodi: `tests/conftest.py` injects fake `xbmc*`
  modules into `sys.modules` before any add-on import. Pure modules
  (`schedule`, `writers`) are tested directly; Kodi-facing flows are tested
  through the fakes (`JSONRPC_RESPONSES`, `PLAYER`, `CALLS`), including
  running `default.py` end-to-end via `runpy`. `PLAYER`/`_CURRENT_LISTITEM`
  together fake `Player().getPlayingItem()` returning whatever `ListItem`
  the fake `setResolvedUrl` last received, so the ListItem-property seek
  handoff (§6) is testable without a real Kodi Player core.
- **Live verification** (`docs/live-testing.md`) covers what fakes cannot:
  IPTV Simple ingestion, real playback, and the join-in-progress seek —
  which must always be re-tested across *channel changes*, not just first
  tune.

## 10. Packaging and release

- Version lives in `addon.xml` (with a `<news>` line per release); bump it
  for any behavior change that ships to Kodi — the zip filename
  (`plugin.video.libtv-<version>.zip`) must change so Kodi's per-path zip
  cache never serves a stale build.
- `make zip` → `scripts/build_zip.py`: repacks `git archive --format=tar HEAD`
  (committed state only, `.gitattributes` `export-ignore` respected) into a
  plain zip with the required `plugin.video.libtv/` top folder, then
  self-checks the contents. Kodi rejects `git archive --format=zip` output.
- Dev toolchain (Poetry, pytest, ruff, kodi-addon-checker) never ships;
  runtime code is stdlib-only and Python 3.8 compatible (Kodi 19 floor).
- **The live Kodi's installed add-on directory is production** — code reaches
  it only through this release path (commit → zip → install → restart), never
  by copying files in directly. The rule, its rationale, and the enforcement
  hook live in `CLAUDE.md`; the `deploy-to-kodi` skill walks the gate.

## 11. Known gaps / roadmap

- No icon/fanart assets.
- Rapid same-channel re-tune edge: if the same file is already playing, the
  seek may be skipped (accepted, benign).
- The `"libtv_seek_offset"` ListItem-property seek handoff (§6) is
  implemented and unit-tested but **not yet live-verified** as the primary
  mechanism across PVR channel changes — `pending_seek.json` remains as a
  fallback until confirmed (see `docs/live-testing.md` §5).
- Genre- and studio-based channel autotune (§3, `manage.autotune_genres` /
  `manage.autotune_studios`) are implemented and unit-tested but not yet
  live-verified in a real Kodi.
- The in-playback EPG overlay (§6a): the `kodi.context.item` trigger is
  **confirmed not to work** live (schema-valid, but the menu entry did not
  appear via either right-click or the `c` key on the tested setup; root
  cause unknown). The `xbmc.python.script`/`RunScript(plugin.video.libtv)`
  keymap trigger (with the `FullscreenLiveTV` fix) and the settings-driven
  write of `special://profile/keymaps/libtv.xml`
  (`keymap.apply_from_settings`) are **confirmed working** live — the
  overlay reliably opens. Getting it to render anything cost several live
  round-trips: a construction crash (`ControlList`'s `itemHeight` vs.
  `_itemHeight` keyword), a completely invisible window (no background of
  its own), then — even with a confirmed-visible background —
  `ControlList` still produced **zero** visible output, not even its own
  focus rectangle, across further attempts (font, colors, single vs. dual
  label). That total silence pointed at the no-skin `ControlList`
  rendering path itself, so rendering was rebuilt on plain
  `ControlLabel`s with hand-rolled navigation/highlighting (§6a) — this
  **confirmed working**: readable text now renders, and the layout is now
  a small bottom-margin strip (fixed panel size + scrolling) instead of
  the near-full-height panel from the first `ControlLabel` attempt.
  Getting navigation and highlighting right cost two more rounds: (1)
  up/down drove Kodi's own native channel-preview banner instead of the
  overlay — traced to a remote/keyboard sending `ACTION_CHANNEL_UP`/
  `ACTION_CHANNEL_DOWN` during PVR playback rather than the generic move
  actions originally listened for; both are now handled, **confirmed
  fixed for the overlay's own cursor**, but the native banner still fires
  alongside it as an unfixable-from-Python cosmetic side effect. (2) The
  current-row highlight, implemented as a `ControlLabel.textColor` change,
  never visibly appeared — the same "post-initial-render `setLabel()`
  color change doesn't repaint" problem `ControlList`'s `selectedColor`
  had — replaced with a text-prefix marker (`"> "`) instead, since content
  changes are the one thing confirmed to reliably repaint. **Not yet
  live-verified**: does the marker-based highlight now show, does
  selecting a row tune the channel, does the panel/scrolling look and
  behave as intended. Every other PVR-specific surprise in this project —
  `StartOffset` ignored, resolver script termination on channel change —
  came from PVR streams differing from regular playback, so whether the
  overlay behaves correctly over an actively playing PVR stream
  specifically is still worth a dedicated check even once these are
  confirmed. See `docs/live-testing.md` for the checklist.
- The `setup_guide` and `show_iptv_paths` info dialogs (§7,
  `plugin.show_setup_guide` / `plugin.show_iptv_setup_info`) are unit-tested
  for their dispatch and message content but not yet live-verified — both
  use a standard `xbmcgui.Dialog().textviewer()`, not a hand-built window
  like the EPG overlay, so they carry little of that feature's risk, but
  should still get one live smoke test each (see `docs/live-testing.md`).
- **`generator.configure_iptv_simple()` (§7) — IPTV Simple auto-configuration
  — is unit-tested (XML round-trip, idempotency, the playback guard, the
  installed/enabled check) but carries real, un-mitigated risk until it's
  live-verified**: it writes into `pvr.iptvsimple`'s own profile directory
  using an unofficial technique (no supported Kodi API covers this), so a
  wrong assumption about the instance-settings file format or Kodi's
  reload-on-toggle behavior could leave that add-on in a broken or
  half-configured state, not just LibTV's own. This is exactly why it's
  gated behind an explicit `auto_configure_iptv` action rather than wired
  into the automatic regeneration loop or the manual build action — see
  `docs/live-testing.md` for the checklist to clear before considering that
  promotion.
- Possible future channel sources: per-show channels, smart-playlist-backed
  channels, tag filters, decade-based autotune.
- `star-rating`/`new`/`xmltv_ns` XMLTV fields (§5) depend on the library
  reporting `rating`/`playcount` — not unit-testable against a real scraper's
  actual field coverage; spot-check against a live library.
