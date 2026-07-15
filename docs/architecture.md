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
| `addon.xml` | Manifest. Two extension points: `xbmc.python.pluginsource` → `default.py`, `xbmc.service` → `service.py`. |
| `default.py`, `service.py` | Thin entry shims (≤15 lines each, enforced by kodi-addon-checker). Add `resources/lib` to `sys.path` and delegate. |
| `resources/lib/libtv/schedule.py` | **Pure.** Schedule building and lookup. No Kodi imports. |
| `resources/lib/libtv/writers.py` | **Pure.** Renders M3U and XMLTV strings from a schedule dict. |
| `resources/lib/libtv/channels.py` | **Pure.** Channel-lineup configuration: `channels.json` load/save, default lineup, id allocation, reorder, JSON-RPC filter building (`build_filter`), and per-channel selection-order building (`build_sort`). |
| `resources/lib/libtv/library.py` | Kodi JSON-RPC library queries (`VideoLibrary.GetMovies` / `GetEpisodes`, filtered and sorted per channel definition) plus genre/studio pickers for the management UI. Resolves each item's runtime, falling back to stream-details duration then an observed-playback-duration cache (§4); for `order: random` also does the day-stable selection (§4). |
| `resources/lib/libtv/generator.py` | Orchestration: fetch → schedule → write all artifacts. Owns the profile directory, the pending-seek handoff file, the version-stamped observed-runtime cache, and the PVR refresh (`refresh_pvr`). |
| `resources/lib/libtv/plugin.py` | Plugin routing: menu, build action, and the stream resolver (`play`), including its schedule-miss loop guard (§6). |
| `resources/lib/libtv/manage.py` | Dialog-driven channel management UI (add/rename/filter/order/reorder/delete) plus genre- and studio-based channel autotune (§3). |
| `resources/lib/libtv/daemon.py` | Service loop (periodic regeneration, self-healing PVR-refresh retry) + `JoinInProgressPlayer` (the seek half of join-in-progress; also records observed durations). |
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
  settings button) edits the file via dialogs; every mutation saves,
  rebuilds all artifacts, and refreshes the PVR client immediately. Genre
  and studio pickers are populated from the library (`library.fetch_genres`,
  `library.fetch_studios` — JSON-RPC has no `GetStudios`, so studios are
  aggregated from movie/show items); for a `mixed` channel these union the
  movie and tvshow results.
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
- Possible future channel sources: per-show channels, smart-playlist-backed
  channels, tag filters, decade-based autotune.
- `star-rating`/`new`/`xmltv_ns` XMLTV fields (§5) depend on the library
  reporting `rating`/`playcount` — not unit-testable against a real scraper's
  actual field coverage; spot-check against a live library.
