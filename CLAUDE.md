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
    `docs/architecture.md` §3), PVR refresh (`refresh_pvr`), IPTV Simple
    instance auto-configuration (`configure_iptv_simple` — an unofficial
    technique, not yet live-verified, see the hard-constraints note below),
    pending-seek and observed-runtime cache persistence
  - `plugin.py` — menu + stream resolver (`play` = the linear-TV core),
    including a schedule-miss loop guard (a `Window(10000)` property rate-
    limiting forced regenerations); also three info/action dialogs: a
    first-run `setup_guide` walkthrough, `auto_configure_iptv_simple` (real
    auto-configuration of IPTV Simple, via `generator.configure_iptv_simple`),
    and `show_iptv_setup_info` (the manual fallback — just the M3U/XMLTV
    paths to paste into IPTV Simple's settings by hand)
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
- **IPTV Simple Client CAN be auto-configured from LibTV, but only via an
  unofficial technique — there is no supported Kodi API for it.** An
  earlier version of this note claimed auto-configuration was "researched
  and confirmed infeasible" based on a secondhand forum thread about an old
  PseudoTV Live hack breaking under Kodi 20's multi-instance model. **That
  claim was wrong** — reading PseudoTV Live's actual current source (both
  its release and `nightly` branches) showed it still auto-configures
  `pvr.iptvsimple` today, with zero manual GUI step, by writing an
  instance-settings file directly. The JSON-RPC `Addons.*` namespace really
  does have only four methods (`GetAddons`, `GetAddonDetails`,
  `SetAddonEnabled`, `ExecuteAddon`, no settings/instance call), and Kodi
  core issue `xbmc/xbmc#22779` really does confirm the Python `xbmcaddon`
  API can't manage instance settings — but neither of those closes off
  **direct file writes**: since Kodi 20 (Nexus), a multi-instance add-on's
  per-instance config is just
  `special://profile/addon_data/<addon-id>/instance-settings-<id>.xml`, in
  Kodi core's own generic settings-serialization format
  (`<settings version="N"><setting id="x">value</setting>...</settings>`,
  confirmed against Kodi core's `CSettingsValueXmlSerializer` and a real
  Kodi-migrated instance file) — a hand-written file in this format is
  indistinguishable, to Kodi, from one its own GUI wrote, and toggling the
  add-on off/on over `Addons.SetAddonEnabled` (the same call
  `refresh_pvr()` already uses) makes Kodi pick it up. `generator.
  configure_iptv_simple()` implements this (`docs/architecture.md` §7);
  **lesson for future research**: verify claims like "X is infeasible"
  against the actual current source of whatever's cited as precedent, not
  a forum thread describing it — code changes, forum posts don't get
  updated.
- **The `m3uPath`/`epgPath` values written into `pvr.iptvsimple`'s config
  can be `special://` URLs, not resolved OS paths** — confirmed by reading
  `pvr.iptvsimple`'s own C++ source: its local-file reading
  (`FileUtils::GetFileContents`/`GetCachedFileContents`) always goes
  through `kodi::vfs::CFile`/`FileExists`/`StatFile` (Kodi's VFS layer),
  never a raw OS file open, for both the local-path and remote-URL cases —
  so `special://profile/addon_data/plugin.video.libtv/channels.m3u`
  resolves the same as any other Kodi-recognized path would.
  `generator.m3u_special_path()`/`xmltv_special_path()` build this from
  `addon.getAddonInfo("profile")` directly (not `xbmcvfs.translatePath`,
  which is for OS paths); `m3u_path()`/`xmltv_path()` (the real OS paths)
  are unchanged and still what `regenerate()` itself writes to, since plain
  `open()` doesn't understand `special://`.
- **A pvr.iptvsimple instance is looked up by its `kodi_addon_instance_name`
  setting, not by the id in its filename** — an instance a user created by
  hand through Kodi's own PVR settings GUI gets whatever id Kodi's GUI
  assigned, unrelated to `configure_iptv_simple()`'s own
  `zlib.crc32(name) % 2**31` scheme for a *freshly created* instance. So
  finding "does an instance with this name already exist" requires parsing
  every `instance-settings-*.xml` in `pvr.iptvsimple`'s profile dir and
  comparing each one's `kodi_addon_instance_name`
  (`generator._find_pvr_instance`), not just checking the one deterministic
  path. A same-named instance with different settings is never silently
  overwritten — `configure_iptv_simple()` returns `"exists_different"` and
  `plugin.auto_configure_iptv_simple()` confirms with the user
  (`Dialog().yesno()`) before retrying with `force=True`.

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
  skin/video content behind them.
- **Fourth live pass: the background now rendered, but the list still
  showed no text/rows at all** — confirming the background fix worked but
  exposing a second, independent problem. Two suspects, both fixed at
  once rather than tested one at a time (this loop has cost a live
  round-trip per bug so far): (1) `ControlList` had no explicit `font` —
  a code-only list with none specified may render its background/focus
  behavior but no text at all, since there's no skin to inherit a default
  from; now set to `'font12'`, present in effectively every skin's
  `Font.xml`. (2) `ListItem(label=..., label2=...)` was used, but a bare
  `ControlList` has no skin XML defining *where* `label2` would even
  draw — there is no default second-label layout to fall back to. Both
  Now/Next fields are combined into a single `label` string
  (`overlay._row_label`) instead.
- **Fifth live pass: font + single-label change made no difference —
  still solid black, absolutely nothing visible, not even a focus
  rectangle or row divider.** That total absence of *any* rendering
  output from `ControlList` (confirmed via a direct question: "does even
  a highlight bar show?" — no) — across four fix attempts targeting
  different specific parameters — pointed at the no-skin `ControlList`
  item-rendering path itself being the problem, not any one keyword
  argument. **Fix**: rebuilt rendering on plain `xbmcgui.ControlLabel`s
  (the most primitive text-drawing control Kodi has) — one per channel
  row, fixed height computed to fit all rows in the background — with
  navigation and the current-row highlight entirely hand-rolled in
  `_EpgOverlay.onAction` (tracking a `_cursor` index, recoloring labels'
  `textColor` directly via `setLabel(...)`) rather than relying on any
  native list/button focus behavior. No `setFocus()`/`onInit()` at all
  anymore — `ControlLabel`s aren't focusable, and the goal was to depend
  on as little native rendering as possible after `ControlList`'s
  complete failure.
- **Sixth live pass: text finally rendered readable rows** — the
  `ControlLabel` rewrite worked for rendering. Two things were still
  broken: (1) **up/down did nothing to the overlay — Kodi's own native
  channel-preview banner responded instead** (channel info changed,
  nothing tuned, the overlay's highlight never moved). Root cause: the
  overlay listened for the generic `ACTION_MOVE_UP`(3)/`ACTION_MOVE_DOWN`(4),
  but a remote/keyboard press during actual PVR playback apparently
  generates `ACTION_CHANNEL_UP`(184)/`ACTION_CHANNEL_DOWN`(185) instead —
  a Live-TV-specific action pair, confirmed to exist in `xbmcgui`, that
  this project hadn't encountered before. **Fix**: `onAction` now handles
  both pairs (handling one that never fires on a given setup is
  harmless). (2) The panel spanned nearly the full screen height
  (**user-reported**, not a bug found via log) instead of a small
  bottom-margin strip. **Fix**: rebuilt the panel as a fixed
  `_PANEL_X/_PANEL_Y/_PANEL_W/_PANEL_H` strip near the bottom, with only
  `_VISIBLE_ROWS` (4) `ControlLabel`s ever created and reused via a
  `_scroll` window into the full row list — not one label per channel.
- **Seventh live pass: the panel moved to the bottom margin correctly, but
  two things were still wrong.** (1) **No text was visible until the
  first up/down press** — the previous round's fix (`_render()` called
  from `onInit()`) populated labels via `setLabel()` right as the window
  was shown, but this apparently doesn't paint until the *next* redraw;
  the very first successful "readable text rows" test (the round before)
  had set real text as a **constructor argument**, not via a later
  `setLabel()` call — that's the actual distinction, not `onInit()` vs.
  `__init__()` timing as first assumed. **Fix**: labels are now always
  constructed with their real text up front (`ControlLabel(...,
  label=self._row_text(i))`); `onInit()` was removed entirely — no longer
  needed. (2) **The gold `textColor` highlight never appeared, on any
  row, even after up/down worked** — the identical "post-initial-render
  `setLabel()` color change doesn't repaint" problem `ControlList`'s
  `selectedColor` already had. **Fix**: replaced color-based highlighting
  with a text prefix (`"> "` on the current row, `"  "` on others) — text
  *content* changes are the one thing confirmed twice now to reliably
  repaint; don't reach for dynamic control colors in this add-on's
  code-only windows again without expecting this.
- **Eighth live pass: the marker showed correctly, but two things were
  reported wrong.** (1) **The overlay always opened with its cursor on
  the first channel in the list, not the one actually playing.** **Fix**:
  `plugin.play()` now also sets a `"libtv_channel_id"` property on the
  resolved `ListItem` — the identical handoff mechanism as
  `"libtv_seek_offset"` (Kodi's Player core retains a resolved item's own
  properties for as long as it's playing) — and
  `overlay._current_channel_id()` reads it back to compute the overlay's
  initial cursor/scroll position. (2) **Up/Down genuinely changed the
  live channel being watched, not just a cosmetic preview banner as
  first assumed** — confirmed by the user's own wording ("channel shift
  on the actual player"). This is *not* fixable from a Python-level
  `onAction`: the physical key is evidently bound to Kodi's native
  channel-surf at a layer the modal overlay window doesn't govern,
  regardless of whether `onAction` also receives and handles the same
  action id. **Decision (user's choice, given the fix options and their
  tradeoffs)**: rather than attempt a riskier, unverified window-scoped
  keymap override, navigation was moved to `ACTION_MOVE_LEFT`/
  `ACTION_MOVE_RIGHT` (1/2) instead of Up/Down — a deliberate UX
  trade-off, since Left/Right is less conventional for browsing a
  vertical list, but isn't natively bound to channel-surfing. **Caveat
  flagged, not yet checked**: Left/Right are commonly bound to seek
  back/forward during regular video playback — if that collides the same
  way, it'll need a different key pair again. **Not yet live-verified**:
  does the cursor now open on the correct channel, does Left/Right avoid
  the collision, does selecting a row tune the channel. See
  `docs/live-testing.md` §5a.

**Live-verified findings (IPTV Simple auto-configuration):**

- **First live pass against a Kodi instance with IPTV Simple disabled
  produced no visible error or warning of any kind** — the underlying
  `_pvr_client_enabled()` check itself worked correctly (it returns
  `"not_installed"`, confirmed by unit test and by the JSON-RPC error
  handling already in `library.json_rpc`), but the *only* feedback was a
  single `xbmcgui.Dialog().notification()` toast: easy to miss entirely if
  the user had already navigated away from the screen that triggered the
  action, and there was no earlier "this is running" feedback either,
  since the whole call is synchronous and includes the 500ms
  `xbmc.sleep()` inside the reload toggle. **Fix**:
  `plugin.auto_configure_iptv_simple()` now shows an immediate "Configuring
  IPTV Simple Client…" notification before doing any work, and the two
  actionable failure outcomes (`"not_installed"`, `"playing"`) now use a
  blocking `Dialog().ok()` instead of a notification, so they can't be
  missed the same way. `generator.configure_iptv_simple()` also gained a
  `LOGWARNING` log line for the not-installed case (previously logged
  nothing at all for that branch). **Not yet re-verified** that the fix
  actually resolves the visibility problem live — see
  `docs/live-testing.md` §4a.

## Known gaps (as of 2026-07)

- No icon/fanart assets yet (checker suggests adding them).
- The channel management UI (custom channels, filters, reorder, the
  diff-driven invalidation that skips a library refetch for rename/move/
  delete), genre- and studio-based autotune (`manage.autotune_genres`,
  `manage.autotune_studios`), the PVR-toggle refresh (including its
  self-healing retry, `daemon.PVR_RETRY_SECONDS`), the `"libtv_seek_offset"`
  ListItem-property seek handoff, the resolver's schedule-miss loop guard,
  and the `setup_guide`/`show_iptv_paths` info dialogs
  (`plugin.show_setup_guide` / `plugin.show_iptv_setup_info`) are all
  unit-tested but not yet live-verified in a real Kodi — see the checklist
  in `docs/live-testing.md`.
- **`generator.configure_iptv_simple()` (the `auto_configure_iptv` action) —
  real IPTV Simple auto-configuration via an unofficial file-write
  technique — is unit-tested but carries genuine risk until live-verified**,
  since it writes into `pvr.iptvsimple`'s own profile directory, not just
  LibTV's own. Deliberately not wired into the automatic regeneration loop
  or the manual build action pending that verification — see
  `docs/architecture.md` §7 and `docs/live-testing.md`.
- XMLTV `star-rating`/`new`/`xmltv_ns` fields depend on the library actually
  reporting `rating`/`playcount` for an item — not yet spot-checked against a
  real scraper's field coverage.
- The in-playback EPG overlay (`overlay.py`, `keymap.py`,
  `docs/architecture.md` §6a) is unit-tested for its pure
  `schedule.find_now_and_next` lookup and `keymap.py`'s key
  validation/XML-rendering/write-remove logic — the `WindowDialog`/
  `ControlLabel` rendering cannot be faked meaningfully. The
  `kodi.context.item` trigger is **confirmed not to work** (see "Live-
  verified findings" above). **Confirmed working live**: the
  `RunScript(plugin.video.libtv)`/keymap trigger, the `ControlLabel`-based
  rendering (which replaced an `xbmcgui.ControlList` that rendered nothing
  at all across four fix attempts), readable text and the `"> "` cursor
  marker, and the bottom-margin scrolling panel's position. **Confirmed
  needing a real fix, not cosmetic**: Up/Down genuinely changed the live
  channel via Kodi's native channel-surf, not just a preview — navigation
  moved to Left/Right instead (user's choice among the fix options; see
  "Live-verified findings" for the tradeoffs). **Not yet re-verified**:
  does the overlay now open with its cursor already on the
  currently-playing channel (`"libtv_channel_id"` property, new this
  round); does Left/Right avoid the same collision Up/Down had (Left/
  Right are common video-seek keys, so this needs checking, not assuming);
  does selecting a row tune the channel; does closing without selecting
  leave playback alone — drawn over an actively playing **PVR** stream
  specifically. See `docs/live-testing.md` §5a.
