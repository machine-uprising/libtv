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
  `context.py` backs the in-playback EPG overlay, reachable two ways: the
  `xbmc.python.script` extension (`RunScript(plugin.video.libtv)`, bindable
  to any keymap key — the trigger to actually rely on) and the
  `kodi.context.item` extension (confirmed **not** to work live — see "Live-
  verified findings" below). Neither passes the `plugin://` argv triple
  `default.py` gets — never route `context.py` through `plugin.run`
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
  - `keymap.py` — pure key validation (`valid_key`) and keymap XML
    rendering (`render_keymap_xml`), plus `apply_from_settings()` which
    writes/removes `special://profile/keymaps/libtv.xml` — backs the
    "Hotkey" + "Save hotkey now" settings so binding the overlay's
    `RunScript` trigger doesn't require hand-editing a keymap file
- `resources/settings.xml` — new-format (`version="1"`) settings; labels are
  msgid numbers resolved via `resources/language/resource.language.en_gb/strings.po`
- `resources/media/overlay_bg.png` — the only bundled runtime image asset:
  a tiny solid semi-transparent PNG stretched behind the EPG overlay's
  list so its text is legible over playing video. Explicitly un-ignored in
  `.gitignore` (see packaging gotchas) since this directory's PNGs/JPGs are
  otherwise excluded.
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
errors or warnings. **It calls out to Kodi's official add-on repository
over the network** (`check_addon.get_all_repo_addons`, to check for id
collisions) with no way to skip it (`--skip-dependency-checks` doesn't
cover this call) — if that network call is slow/unreachable, the whole run
hangs with zero output for minutes rather than failing fast. If it hangs,
that's an environment/network issue, not a sign the add-on is broken —
retry later rather than trying to debug the add-on.

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
- `resources/media/*.png`/`*.jpg` are gitignored (intended for
  future/dev-generated assets, not committed ones) — a genuine bundled
  runtime asset placed there (e.g. `overlay.py`'s background image) is
  invisible to `git add` and therefore silently **absent from the zip**
  unless explicitly un-ignored (`!resources/media/<file>.png` in
  `.gitignore`). Any new committed media file needs the same treatment, or
  it'll ship with an empty add-on and fail confusingly at runtime instead
  of at build time.

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
- **`xbmcgui.ControlList`'s real keyword arguments are underscore-prefixed**
  (`_itemHeight`, `_space`, `_imageWidth`, ... — everything past
  `selectedColor`) even though the API docs' prose names them without the
  underscore (`itemHeight`, etc.) — confirmed live on Omega/Windows:
  `ControlList(x, y, w, h, itemHeight=60)` raises
  `TypeError: 'itemHeight' is an invalid keyword argument for this
  function`; `kodistubs`' actual `__init__` signature (not its docstring)
  has the correct underscore-prefixed names and would have caught this —
  check the stub's *signature*, not its prose, when a Kodi API call's
  keyword arguments are ambiguous.
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

**Live-verified findings (in-playback EPG overlay):**
- **The `kodi.context.item` extension does not surface the "LibTV guide
  (Now/Next)" entry** on the tested setup — neither a mouse right-click nor
  the `c` key showed it during PVR playback (`c` instead opened Kodi's own
  built-in channel/guide overlay). The XML is schema-valid (confirmed
  against `kodi-addon-checker`'s bundled XSD — see the schema note above),
  so this looks like a skin- or remote-binding issue rather than a
  packaging mistake, but the root cause is unconfirmed. Do not treat
  `kodi.context.item` as the reliable trigger for this feature.
- In response, `context.py` is now also exposed via an `xbmc.python.script`
  extension, making `RunScript(plugin.video.libtv)` callable from any
  user-defined keymap — this sidesteps the context-menu mechanism entirely
  and is the trigger to document/rely on. A "Hotkey" text setting +
  "Save hotkey now" button (`keymap.apply_from_settings`) writes/removes
  `special://profile/keymaps/libtv.xml` so the user never hand-edits a
  keymap file. The settings-driven write itself works (confirmed: the file
  appears with the chosen key).
- **A `FullscreenVideo`-only keymap binding did nothing while a PVR channel
  was genuinely playing full-screen** — not even a `kodi.log` trace of
  `RunScript` firing (ruling out a script-side error; the keypress itself
  wasn't reaching the binding). `keymap.render_keymap_xml` now writes the
  same binding under **both** `FullscreenVideo` and `FullscreenLiveTV`,
  since some Kodi versions/skins still route live TV playback through the
  legacy `FullscreenLiveTV` window context.
- **Confirmed: the dual-context keymap fix works** — after re-saving the
  hotkey and restarting, pressing the bound key during PVR playback
  successfully invoked `RunScript(plugin.video.libtv)` → `context.py` →
  `overlay.show()` (proven by a Python traceback from *inside*
  `overlay.py` appearing in `kodi.log`, which only happens once the script
  actually runs). So both the `xbmc.python.script`/`RunScript` wiring and
  the `FullscreenLiveTV` addition are now live-verified as far as
  triggering the overlay goes.
- That run then hit a real bug: `xbmcgui.ControlList(..., itemHeight=60)`
  raised `TypeError` (see the `ControlList` keyword-argument finding
  above) — fixed by using `_itemHeight` instead.
- **Next live pass hit another real bug**: `Control N in window M has been
  asked to focus, but it can't` in `kodi.log`, no traceback. Cause:
  `self.setFocus(self._list)` was called from `_EpgOverlay.__init__` —
  i.e. before `doModal()` had ever shown the window. Kodi can't grant
  focus to a control on a window that isn't part of the active/visible
  window stack yet, so the call silently fails (window not shown = no
  crash, no focus, no error thrown into Python — only a GUI-log line).
  **Fix**: move `setFocus()` into an `onInit()` override, which Kodi calls
  once the window has actually been initialized/shown — the documented
  place to set initial focus on a hand-built `Window`/`WindowDialog`.
- **Third live pass: the window opened and blocked in `doModal()` (log
  showed `overlay showing N channel row(s)` and then nothing further) but
  was completely invisible** — pressing Esc once did nothing, pressing it
  again brought up Kodi's own OSD with the video still playing underneath,
  confirming the overlay *was* open and closed on that first Esc, it just
  never rendered anything visible. Root cause: a code-only `WindowDialog`
  draws no background of its own, and the `ControlList` had no explicit
  `textColor`/`selectedColor`, so it likely wasn't drawing legible content
  either. **Fix**: added `resources/media/overlay_bg.png` (a small solid
  semi-transparent PNG, generated with pure Python — no Pillow needed —
  since the repo has no image assets yet), drawn via `xbmcgui.ControlImage`
  behind the list, plus explicit `textColor`/`selectedColor` on the
  `ControlList` so rows and the focused row are legible regardless of
  skin/video content behind them. Not yet live-verified whether this
  actually makes the overlay visible — see `docs/live-testing.md` §5a.

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
- The in-playback EPG overlay (`overlay.py`, `keymap.py`,
  `docs/architecture.md` §6a) is unit-tested for its pure
  `schedule.find_now_and_next` lookup and `keymap.py`'s key
  validation/XML-rendering/write-remove logic — the `WindowDialog`/
  `ControlList` rendering cannot be faked meaningfully. The
  `kodi.context.item` trigger is **confirmed not to work** (see "Live-
  verified findings" above); the `RunScript(plugin.video.libtv)`/keymap
  trigger (with the `FullscreenLiveTV` fix) is now **confirmed to fire**.
  Still open: whether the overlay actually **renders and behaves
  correctly** once construction no longer crashes (list displays, focus/
  navigation works, selecting a row tunes the channel, closing without
  selecting leaves playback alone) drawn over an actively playing **PVR**
  stream specifically. See `docs/live-testing.md` §5a.
