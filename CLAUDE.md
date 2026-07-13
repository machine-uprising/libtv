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
file with a `StartOffset`, so zapping joins programmes in progress.

## Layout

- `addon.xml` — manifest; extension points define how Kodi invokes the code
- `default.py` / `service.py` — thin entry shims only (see constraint below);
  they add `resources/lib` to `sys.path` and delegate
- `resources/lib/libtv/` — the actual code:
  - `schedule.py`, `writers.py` — **pure logic, no Kodi imports** (keep it that way)
  - `library.py` — JSON-RPC library queries
  - `generator.py` — orchestration: build schedule, write M3U/XMLTV/schedule.json
  - `plugin.py` — menu + stream resolver (`play` = the linear-TV core)
  - `daemon.py` — background regeneration loop (`xbmc.Monitor`-based)
- `resources/settings.xml` — new-format (`version="1"`) settings; labels are
  msgid numbers resolved via `resources/language/resource.language.en_gb/strings.po`
- `tests/conftest.py` — hand-rolled fake `xbmc*` modules that make the add-on
  importable outside Kodi

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
- `default.py` and `service.py` must stay ≤15 counted lines
  (kodi-addon-checker "complex entry point" rule) — logic goes in
  `resources/lib/libtv/`.
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
  schedule building.
- **`schedule.json` is the contract between generator and resolver.** The
  XMLTV guide, the M3U, and playback resolution all derive from the same
  persisted schedule; never compute "what's on" from anything else.
- Keep `schedule.py` and `writers.py` free of Kodi imports so the core stays
  unit-testable.

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
- Hence the current design: the resolver writes `pending_seek.json` (before
  resolving, so it survives script death) and the long-lived service seeks
  from `daemon.JoinInProgressPlayer.onAVStarted`, clamping to the real file
  duration. On a file mismatch the pending seek is left in place (rapid-zap
  races); stale entries are dropped after `PENDING_SEEK_MAX_AGE`. Any change
  to this flow must be re-verified live across *channel changes*, not just
  first tune.

## Known gaps (as of 2026-07)

- No icon/fanart assets yet (checker suggests adding them).
- Channel lineup is hardcoded to two channels (all movies / all episodes) in
  `library.fetch_channels`; genre- or show-based configurable channels are the
  next feature.
- After regeneration, IPTV Simple keeps serving its cached M3U/EPG until a
  PVR data refresh or Kodi restart; automating that refresh is an open task.
