# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

LibTV is a Kodi add-on (id `plugin.video.libtv`) that turns the user's Kodi
library into linear TV channels. It scans the library over Kodi's JSON-RPC
API, builds a persisted schedule, and writes an M3U playlist plus an XMLTV
guide into the add-on profile directory. Kodi's **PVR IPTV Simple Client**
consumes those two files and provides the actual Live TV UI, guide, and
playback — this add-on never renders TV itself.

Data flow: `VideoLibrary.*` JSON-RPC → schedule (contiguous programmes with
epoch times, persisted as `schedule.json`) → `channels.m3u` + `guide.xmltv`
in the profile dir → IPTV Simple Client. Each M3U entry is a
`plugin://plugin.video.libtv/?action=play&channel=<id>` URL; on playback the
resolver looks up what the schedule says is on air *now* and resolves to that
file, handing the join-in-progress offset to the background service (via a
`ListItem` property, primary, and `pending_seek.json`, fallback — see
"Live-verified findings" below) so zapping joins programmes already in
progress.

**`docs/architecture.md` is the canonical design document** — component map,
schedule model, file formats, the tune/seek sequence, settings, invariants.
Read it before changing behavior; update it as part of any change that alters
the design.

## Documentation discipline

Every change lands fully documented in the same change. At the end of any
change touching runtime code, settings, packaging, or workflow, run the
`document-changes` skill (`.claude/skills/document-changes/SKILL.md`) and
work through its checklist — it maps which document owns what.

## Layout

- `addon.xml` — manifest; extension points define how Kodi invokes the code
- `default.py` / `service.py` / `context.py` — thin entry shims only (see
  constraint below); they add `resources/lib` to `sys.path` and delegate.
  `context.py` backs the `kodi.context.item` extension (in-playback EPG
  overlay) and is invoked with Kodi's context-menu calling convention, not
  the `plugin://` argv triple `default.py` gets — never route it through
  `plugin.run`
- `resources/lib/libtv/` — the actual code:
  - `schedule.py`, `writers.py`, `channels.py` — **pure logic, no Kodi
    imports** (keep it that way); `channels.py` owns the channel-lineup
    config (`channels.json`) and JSON-RPC filter building
  - `library.py` — JSON-RPC library queries (filtered per channel definition)
  - `generator.py` — orchestration: build schedule, write
    M3U/XMLTV/schedule.json (`regenerate`, full library fetch) or patch just
    the schedule's channel metadata in place without a fetch
    (`relabel_schedule` — the management UI's diff-driven invalidation, see
    `docs/architecture.md` §3), PVR refresh (`refresh_pvr`), pending-seek and
    observed-runtime cache persistence
  - `plugin.py` — menu + stream resolver (`play` = the linear-TV core),
    including a schedule-miss loop guard (a `Window(10000)` property rate-
    limiting forced regenerations)
  - `manage.py` — dialog-driven channel management UI (with a match-count
    preview before saving a channel's filters) + genre- and studio-based
    autotune
  - `daemon.py` — background regeneration loop (`xbmc.Monitor`-based),
    self-healing PVR-refresh retry, join-in-progress seek + observed-runtime
    recording (`JoinInProgressPlayer`)
  - `overlay.py` — in-playback EPG overlay: a code-only
    `xbmcgui.WindowDialog` (no skin XML) listing every channel's Now/Next,
    read-only against `schedule.json` (never regenerates or refreshes PVR)
- `resources/settings.xml` — new-format (`version="1"`) settings; labels are
  msgid numbers resolved via `resources/language/resource.language.en_gb/strings.po`
- `tests/conftest.py` — hand-rolled fake `xbmc*` modules that make the add-on
  importable outside Kodi
- `docs/` — `architecture.md` (canonical design), `live-testing.md`
  (real-Kodi verification checklist)
- `scripts/` — dev-only helpers: `build_zip.py` (packaging), `sanity_check.py`
  (library sanity over HTTP JSON-RPC)
- `.claude/` — Claude Code project config (committed, excluded from the zip):
  `skills/document-changes` (doc-sync checklist), `skills/deploy-to-kodi`
  (release gate for the live Kodi), `hooks/protect-kodi-addons.py` +
  `settings.json` (PreToolUse hook denying writes to the installed add-on)

## Commands

Poetry manages the **dev toolchain only** (see constraints below).

```bash
poetry install                                  # set up dev env (.venv in-project)
poetry run pytest                               # run tests
poetry run pytest tests/test_schedule.py::test_find_current_misses   # single test
poetry run ruff check .                         # lint
poetry run kodi-addon-checker --branch omega .  # validate add-on structure
```

`kodi-addon-checker` on the repo dir reports a folder-name error and `.venv`
noise; for a clean signal, rsync the add-on files into a folder named
`plugin.video.libtv` (excluding dev files) and check that. Must pass with no
errors or warnings.

Build the installable zip (packages **committed** state only — commit first):

```bash
make zip        # runs scripts/build_zip.py
```

Dev-only files are excluded from the zip via `export-ignore` in `.gitattributes`;
keep that list in sync when adding dev-only files or directories. A `Makefile`
wraps the common tasks (`make check`, `make zip`); the build refuses dirty
trees and verifies the built zip contains the key add-on files.

**Packaging gotchas (both shipped broken zips before):**
- `.gitignore` patterns must be root-anchored where they could match add-on
  source — an unanchored `lib/` once silently excluded `resources/lib/` from
  git and therefore from the built zip (`ModuleNotFoundError: No module named
  'libtv'` at runtime).
- Never build the zip with `git archive --format=zip` — git appends the
  commit SHA as a zip archive comment, which Kodi's zip parser rejects with
  "invalid structure". `scripts/build_zip.py` repacks git archive's tar
  output into a plain zip instead.
- Kodi caches a zip's central directory by file path — replacing a zip
  in place and reinstalling can read stale entry offsets ("Failed to open
  file" on addon.xml) until Kodi restarts. The build names zips
  `<id>-<version>.zip` so releases land on fresh paths; when iterating on
  the same version, restart Kodi between install attempts.

## The live Kodi is production — never modify it in place

The Kodi instance on the Windows host runs the deployed add-on
(`%APPDATA%\Kodi\addons\plugin.video.libtv\`, from WSL
`/mnt/c/Users/Dave/AppData/Roaming/Kodi/addons/plugin.video.libtv/`).
**Never edit, copy, or symlink files into that directory** — no "fast
iteration" hot-patching, even for a one-file fix. Hot-patched code also
diverges from what the long-lived service holds in memory, so the running
system ends up in a state that matches neither the repo nor the release.

The only deployment path is: commit → `make zip` → install the versioned
zip in Kodi → restart Kodi. Run the `deploy-to-kodi` skill
(`.claude/skills/deploy-to-kodi/SKILL.md`) whenever a change needs to reach
the live instance; it walks the full gate.

Interacting with the live Kodi read-only (JSON-RPC queries, reading logs,
inspecting profile-dir artifacts under `userdata/addon_data/`) is fine and
encouraged for diagnosis.

## Hard constraints — Kodi runtime

- **`xbmc`, `xbmcaddon`, `xbmcgui`, `xbmcplugin`, `xbmcvfs` exist only inside
  Kodi's embedded interpreter.** They cannot be pip-installed. `kodistubs`
  (dev dependency) provides editor/type stubs only.
- **Never add runtime dependencies to `pyproject.toml`.** Runtime deps must be
  Kodi module add-ons declared in `addon.xml` (e.g.
  `<import addon="script.module.requests"/>`), or vendored.
- **Runtime code must stay Python 3.8 compatible** (Kodi 19 Matrix ships 3.8;
  `addon.xml` targets `xbmc.python` 3.0.1 = Kodi 19+). No `match`, no `X | Y`
  unions, no 3.9+ stdlib additions in add-on code; f-strings are fine. Ruff is
  pinned to `target-version = "py38"`. Dev tooling/tests may use 3.11+.
- **Use `xbmcvfs.translatePath`, not `xbmc.translatePath`** — the latter was
  removed in Kodi 19. The test fakes deliberately omit it so regressions fail.
- **JSON-RPC `runtime` values are in SECONDS**, not minutes (a classic Kodi
  trap — the original prototype got this wrong).
- **`VideoLibrary.GetEpisodes` returns `runtime: 0` unless `streamdetails`
  is also in the requested properties** (verified live on Omega/Windows,
  against a library where the Kodi UI showed correct durations). Kodi only
  fills episode `runtime` from the file's stream details when they're
  requested, and episode scrapers often set no runtime at all — dropping
  `streamdetails` from `library.py`'s property lists silently reverts every
  episode to the 90-minute default slot and breaks the join-in-progress
  seek. `scripts/sanity_check.py` mirrors the property lists; keep in sync.
  As a second line of defense, `library._resolve_runtime` also falls back to
  an observed-playback-duration cache (`generator.record_observed_runtime`,
  written from `daemon.JoinInProgressPlayer.onAVStarted`) before giving up
  and using the 90-minute default — this does not remove the need to keep
  `streamdetails` requested, since the cache only has data for files that
  have actually played at least once.
- `default.py`, `service.py`, and `context.py` must stay ≤15 counted lines
  (kodi-addon-checker "complex entry point" rule) — logic goes in
  `resources/lib/libtv/`.
- **`kodi.context.item` extension schema**: `<extension point="kodi.context.item">`
  requires a `<menu id="kodi.core.main">` (or `kodi.core.manage`) wrapper
  around each `<item>`, and `library` is an attribute of `<item>`, not of
  `<extension>` — `kodi-addon-checker` rejects both the unwrapped and
  extension-level-`library` forms (verified against its bundled XSD,
  `matrix_contextitem.xsd`/`jarvis_contextitem.xsd`).
- The add-on profile directory does not exist until created — `generator.profile_dir()`
  handles `xbmcvfs.mkdirs`; write files only through it.
- `addon.xml` changes must pass `kodi-addon-checker`. Valid plugin extension
  point is `xbmc.python.pluginsource` (not `xbmc.addon.video`); the
  `xbmc.service` extension no longer accepts a `start` attribute.
- New settings: add to `resources/settings.xml` (new format needs `<level>`,
  `<default>`, `<control>`) **and** a numbered msgid in `strings.po`
  (32100-range), and mirror a default in `tests/conftest.py` `SETTINGS`.
- For install-from-zip to work, the top-level folder inside the zip must equal
  the add-on id (`plugin.video.libtv`) — hence the `--prefix` in the build
  command; the repo directory name (`libtv`) doesn't matter.

## Design invariants

- **Schedules must be stable within a day.** Channels anchor at midnight UTC
  and shuffle with a seed of `channel_id:anchor` (`schedule.shuffled`), so a
  regeneration at 15:00 must not change what was on air at 14:59. Don't
  introduce nondeterminism (unseeded shuffle, now-anchored schedules) into
  schedule building. This is also why a channel's `order: random` selection
  (`library.fetch_channels`) must never use Kodi's own JSON-RPC `List.Sort`
  method `"random"` — it re-randomizes on every call, so capping its output
  at `max_items` would pick a different item set on every regeneration.
  Instead fetch the full filtered set unsorted and pick the day-stable
  sample with `schedule.shuffled(channel_id, items, anchor)`.
- **`schedule.json` is the contract between generator and resolver.** The
  XMLTV guide, the M3U, and playback resolution all derive from the same
  persisted schedule; never compute "what's on" from anything else.
- Keep `schedule.py`, `writers.py`, and `channels.py` free of Kodi imports so
  the core stays unit-testable.
- **Channel ids are permanent.** Renames and reorders in the management UI
  must never change a channel's id — the deterministic shuffle seed and the
  PVR channel identity both key off it.
- **PVR refresh (IPTV Simple toggle) may only run from the manual build
  action and the service loop.** The resolver also regenerates (on schedule
  miss), but toggling `pvr.iptvsimple` mid-tune aborts the tune, and toggling
  during any playback kills the stream — `generator.refresh_pvr()` guards on
  `Player().isPlaying()` and must keep doing so.

## Testing approach

Unit tests run outside Kodi by injecting fake `xbmc*` modules into
`sys.modules` from `tests/conftest.py` **before** add-on code is imported
(`pythonpath = [".", "resources/lib"]` in pyproject makes `libtv` importable).
When add-on code starts using a new piece of the Kodi API, extend the fakes.
Set `tests.conftest.JSONRPC_RESPONSES` to control library queries; fakes
record side effects in `tests.conftest.CALLS` for assertions. Entry-point
behavior is tested by running `default.py` via `runpy` with a patched
`sys.argv`.

Anything touching real playback, PVR behavior, or the EPG UI can only be
verified in a running Kodi instance with IPTV Simple Client configured —
see `docs/live-testing.md` for the checklist.

**Live-verified findings (join-in-progress):**
- Kodi ignores the ListItem `StartOffset` property on streams resolved for
  PVR IPTV Simple (confirmed on Omega/Windows). Do not reintroduce it.
- The resolver script CANNOT perform the seek itself: polling after
  `setResolvedUrl` works on first tune but fails on channel changes (the
  resolver script gets terminated when the previous channel's stream stops).
- Hence the current design: the resolver sets a `"libtv_seek_offset"`
  property directly on the `ListItem` it resolves (primary — read back via
  `Player().getPlayingItem().getProperty(...)`, which Kodi's Player core
  retains independent of the resolver script) and *also* writes
  `pending_seek.json` (fallback — before resolving, so it survives script
  death). The long-lived service seeks from
  `daemon.JoinInProgressPlayer.onAVStarted`, preferring the property and
  falling back to the file, clamping to the real file duration either way.
  On a file mismatch via the file-fallback path, the pending seek is left in
  place (rapid-zap races); stale entries are dropped after
  `PENDING_SEEK_MAX_AGE`.
- **The `"libtv_seek_offset"` ListItem-property path is NOT YET live-verified**
  as the primary mechanism — it's a custom property, not `StartOffset`, so
  the ignored-`StartOffset` finding doesn't tell us whether it survives to
  PVR playback the same way. `pending_seek.json` stays as a safety net until
  this is confirmed; see `docs/live-testing.md` §5. Any change to this flow
  must be re-verified live across *channel changes*, not just first tune.

## Known gaps (as of 2026-07)

- No icon/fanart assets yet (checker suggests adding them).
- The channel management UI (custom channels, filters, reorder, the
  diff-driven invalidation that skips a library refetch for rename/move/
  delete), genre- and studio-based autotune (`manage.autotune_genres`,
  `manage.autotune_studios`), the PVR-toggle refresh (including its
  self-healing retry, `daemon.PVR_RETRY_SECONDS`), the `"libtv_seek_offset"`
  ListItem-property seek handoff, and the resolver's schedule-miss loop
  guard are all unit-tested but not yet live-verified in a real Kodi — see
  the checklist in `docs/live-testing.md`.
- XMLTV `star-rating`/`new`/`xmltv_ns` fields depend on the library actually
  reporting `rating`/`playcount` for an item — not yet spot-checked against a
  real scraper's field coverage.
- The in-playback EPG overlay (`overlay.py`, `docs/architecture.md` §6a) is
  unit-tested only for its pure `schedule.find_now_and_next` lookup — the
  `kodi.context.item` trigger and the `WindowDialog`/`ControlList` rendering
  cannot be faked meaningfully and are **not yet live-verified**: whether the
  context-menu entry actually appears/behaves correctly in a real Kodi, and
  whether a code-only overlay window drawn over an actively playing **PVR**
  stream (as opposed to a regular video) behaves the same way — every other
  PVR-specific surprise in this project came from PVR streams differing from
  regular playback. See `docs/live-testing.md`.
