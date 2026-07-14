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
| `resources/lib/libtv/channels.py` | **Pure.** Channel-lineup configuration: `channels.json` load/save, default lineup, id allocation, reorder, and JSON-RPC filter building. |
| `resources/lib/libtv/library.py` | Kodi JSON-RPC library queries (`VideoLibrary.GetMovies` / `GetEpisodes`, filtered per channel definition) plus genre/studio pickers for the management UI. Resolves each item's runtime, falling back to stream-details duration (§4). |
| `resources/lib/libtv/generator.py` | Orchestration: fetch → schedule → write all artifacts. Owns the profile directory, the pending-seek handoff files, and the PVR refresh (`refresh_pvr`). |
| `resources/lib/libtv/plugin.py` | Plugin routing: menu, build action, and the stream resolver (`play`). |
| `resources/lib/libtv/manage.py` | Dialog-driven channel management UI (add/rename/filter/reorder/delete). |
| `resources/lib/libtv/daemon.py` | Service loop (periodic regeneration) + `JoinInProgressPlayer` (the seek half of join-in-progress). |
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
      "genres": ["Action"], "studios": [], "year_from": 1980, "year_to": 1989
    }
  ]
}
```

- `type` is `movies` or `episodes`. Empty filter fields mean "no filter".
- **List order is channel order** — it drives the M3U order and therefore the
  guide order. Reordering is just reordering the list.
- **Ids are allocated once (`libtv.custom.<n>`) and never change or get
  reused**: the deterministic shuffle seed and the PVR channel identity both
  key off the id, so renames and reorders must not touch it.
- Filters translate to Kodi JSON-RPC `List.Filter` clauses
  (`channels.build_filter`) and run server-side in Kodi's database: genres and
  studios OR within a field (`is` matches membership on multi-value fields),
  dimensions AND together, and year bounds become exclusive
  `greaterthan`/`lessthan` rules with **string** values (Kodi rejects
  numbers). For episode channels, genre/studio/year filter on the episode's
  own metadata (year = year aired; studio inherited from the show).
- The management UI (`manage.py`, reachable from the add-on menu and a
  settings button) edits the file via dialogs; every mutation saves,
  rebuilds all artifacts, and refreshes the PVR client immediately. Genre
  and studio pickers are populated from the library (`library.fetch_genres`,
  `library.fetch_studios` — JSON-RPC has no `GetStudios`, so studios are
  aggregated from movie/show items).

## 4. The schedule model

Built in `schedule.build_schedule`, persisted as `schedule.json`.

- **Channels** come from the lineup above (`library.fetch_channels`), each
  capped at `max_items` items.
- **Programmes are contiguous**: each channel's programmes run back-to-back
  from the anchor with no gaps, **cycling** through the item list until the
  guide horizon (`now + epg_hours`) is covered — like a real linear station.
- **Anchor**: midnight UTC of the current day (`schedule.day_anchor`). The
  schedule is built from the anchor, not from "now", so what is on air at any
  instant is a pure function of (channel, items, day).
- **Deterministic shuffle**: when `shuffle` is on, items are ordered by
  `random.Random(f"{channel_id}:{anchor}")` (`schedule.shuffled`). Same
  channel + same day ⇒ same order. **Invariant: regenerating during the day
  must never change what is currently on air.**
- **Runtimes are SECONDS** (Kodi JSON-RPC convention). `library.py` must
  request the `streamdetails` property alongside `runtime`: Kodi only fills
  an episode's `runtime` from the file's stream details when stream details
  are requested, and episode scrapers often provide no runtime at all —
  without it, whole shows come back `runtime: 0` (verified live; the Kodi UI
  still shows the correct duration, hiding the problem). `library._resolve_runtime`
  additionally falls back to `streamdetails.video[0].duration` explicitly and
  strips the blob before scheduling. Runtime still missing ⇒ 90 min default;
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
present for episodes. **`schedule.json` is the contract**: the M3U, the XMLTV
guide, and playback resolution all derive from it and must never disagree.

## 5. Generated artifacts

All written by `generator.regenerate()` to the add-on profile directory
(`special://profile/addon_data/plugin.video.libtv/`), which it creates on
first use.

- **`channels.m3u`** — one `#EXTINF` per channel (`tvg-id`, `tvg-name`,
  `group-title`, `tvg-logo`) whose stream URL is
  `plugin://plugin.video.libtv/?action=play&channel=<id>`. Channels never
  point at media files directly.
- **`guide.xmltv`** — XMLTV rendered from the schedule. Episodes are titled
  by show with the episode title as `<sub-title>` and an
  `<episode-num system="onscreen">SxxEyy</episode-num>`.
- **`schedule.json`** — see above.
- **`channels.json`** — channel lineup configuration (§3). The only artifact
  written by the management UI rather than `regenerate()`; never touched by
  regeneration.
- **`pending_seek.json`** — transient join-in-progress handoff (see §6);
  exists only between a tune and the seek.

## 6. Stream resolution and join-in-progress

The full tune sequence (the subtle part of the design — see the history in
`CLAUDE.md` "Live-verified findings" before changing it):

1. User tunes to a channel; IPTV Simple hands the `plugin://…?action=play&channel=<id>`
   URL to the player, which invokes the resolver (`plugin.play`).
2. The resolver loads `schedule.json` and calls `find_current(now)`. On a
   miss (missing/stale schedule) it regenerates once and retries; if still
   nothing, it resolves failure.
3. If `join_in_progress` is on and the offset exceeds 5 s, it writes
   `pending_seek.json` — `{"file", "offset", "set_at"}` — **before** calling
   `setResolvedUrl`, because the resolver script can be terminated at any
   point after resolving.
4. `setResolvedUrl(handle, True, ListItem(path=programme file))`. The
   resolver exits; nothing after this point is relied upon.
5. Kodi starts playback. The **service's** `daemon.JoinInProgressPlayer`
   (an `xbmc.Player` subclass alive for the whole Kodi session) receives
   `onAVStarted`, which fires exactly when playback truly begins.
6. It reads `pending_seek.json`. If the now-playing file matches, it clears
   the file and seeks to `min(offset, total_duration − 10 s)`. If a
   *different* file is playing (rapid zap race), the pending seek is left for
   the stream it belongs to; entries older than `PENDING_SEEK_MAX_AGE`
   (120 s) are discarded as abandoned.

Why this shape (verified live on Kodi Omega/Windows, do not regress):

- Kodi **ignores the `StartOffset`** ListItem property on streams resolved
  for PVR IPTV Simple.
- The resolver **cannot seek itself** after `setResolvedUrl`: a post-resolve
  poll loop works on first tune but dies on channel changes, because Kodi
  terminates the resolver script when the previous channel's stream (same
  plugin) stops.
- `onAVStarted` in the long-lived service is immune to both problems.

## 7. Background service and PVR refresh

`daemon.run()`: regenerates immediately at startup, then re-regenerates every
`regen_interval_hours` via `xbmc.Monitor.waitForAbort` (so Kodi shutdown
interrupts the wait cleanly). Generation failures are logged and never kill
the service loop. The service also hosts `JoinInProgressPlayer` (§6) — the
instance must stay referenced for callbacks to be delivered.

IPTV Simple caches its M3U/EPG and has no reload API, so after every
regeneration `generator.refresh_pvr()` toggles the `pvr.iptvsimple` add-on
off and on over JSON-RPC (`Addons.SetAddonEnabled`); the PVR manager restarts
the client and it re-reads both files, so lineup and guide changes appear
without a Kodi restart. Guards, all of which make it return `False`:

- the `refresh_pvr` setting is off;
- **anything is playing** — toggling the client kills a live stream, so the
  refresh is skipped (the cached guide stays until the next regen cycle);
- IPTV Simple is not installed/enabled.

`refresh_pvr()` is called only from the manual build action (`plugin.build`)
and the service loop — **never from the stream resolver**: `plugin.play` also
regenerates on a schedule miss, and a toggle mid-tune would abort the tune.

## 8. Settings

| id | type | default | effect |
| --- | --- | --- | --- |
| `max_items` | integer 10–1000 | 150 | Cap on library items pulled per channel. |
| `shuffle` | boolean | true | Deterministic per-day shuffle vs library order. |
| `epg_hours` | integer 6–72 | 24 | Guide horizon: schedule covers `now + epg_hours`. |
| `regen_interval_hours` | integer 1–24 | 6 | Service regeneration period. |
| `join_in_progress` | boolean | true | Seek into the current programme on tune (§6). |
| `refresh_pvr` | boolean | true | Toggle IPTV Simple after rebuilds so the guide reloads (§7). |
| `manage_channels` | action | — | `ActivateWindow(Videos,…?action=channels,return)` — opens the management UI (§3). |
| `regenerate_now` | action | — | `RunPlugin(…?action=build)`. |

Adding a setting requires: `resources/settings.xml` entry (new format:
`<level>`, `<default>`, `<control>`), a msgid in `strings.po`, and a mirrored
default in `tests/conftest.py` `SETTINGS`.

## 9. Testing strategy

- **Unit tests** run outside Kodi: `tests/conftest.py` injects fake `xbmc*`
  modules into `sys.modules` before any add-on import. Pure modules
  (`schedule`, `writers`) are tested directly; Kodi-facing flows are tested
  through the fakes (`JSONRPC_RESPONSES`, `PLAYER`, `CALLS`), including
  running `default.py` end-to-end via `runpy`.
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
- When a PVR refresh is skipped because something is playing, the guide stays
  stale until the next service regen cycle finds Kodi idle (no deferred
  retry).
- Possible future channel sources: per-show channels, smart-playlist-backed
  channels, tag filters.
