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
- `default.py` / `service.py` — thin entry shims only (see constraint below);
  they add `resources/lib` to `sys.path` and delegate
- `resources/lib/libtv/` — the actual code:
  - `schedule.py`, `writers.py`, `channels.py` — **pure logic, no Kodi
    imports** (keep it that way); `channels.py` owns the channel-lineup
    config (`channels.json`) and JSON-RPC filter building
  - `library.py` — JSON-RPC library queries (filtered per channel definition)
  - `generator.py` — orchestration: build schedule, write
    M3U/XMLTV/schedule.json, PVR refresh (`refresh_pvr`)
  - `plugin.py` — menu + stream resolver (`play` = the linear-TV core)
  - `manage.py` — dialog-driven channel management UI
  - `daemon.py` — background regeneration loop (`xbmc.Monitor`-based)
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
- Hence the current design: the resolver writes `pending_seek.json` (before
  resolving, so it survives script death) and the long-lived service seeks
  from `daemon.JoinInProgressPlayer.onAVStarted`, clamping to the real file
  duration. On a file mismatch the pending seek is left in place (rapid-zap
  races); stale entries are dropped after `PENDING_SEEK_MAX_AGE`. Any change
  to this flow must be re-verified live across *channel changes*, not just
  first tune.

## Known gaps (as of 2026-07)

- No icon/fanart assets yet (checker suggests adding them).
- When the post-rebuild PVR refresh is skipped because something is playing,
  the guide stays stale until a later regen cycle finds Kodi idle (no
  deferred retry).
- The channel management UI (custom channels, filters, reorder) and the
  PVR-toggle refresh are unit-tested but not yet live-verified in a real
  Kodi — see the new items in `docs/live-testing.md`.
