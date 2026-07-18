# Live testing in Kodi

Unit tests (`poetry run pytest`) exercise schedule building and M3U/XMLTV
rendering against faked `xbmc*` modules. They **cannot** verify anything that
only exists inside a running Kodi: JSON-RPC against a real library, IPTV Simple
Client ingesting the guide, stream resolution, and the join-in-progress
seek. That verification requires a real Kodi instance.
This document is the checklist for doing it.

## 1. Get a Kodi you can reach

Development happens on WSL2, which has no display for a GUI app, so run Kodi
somewhere with a real UI. Easiest first:

| Option | Notes |
| --- | --- |
| **Kodi on the Windows host** | Simplest. Userdata lives at `%APPDATA%\Kodi\`. The WSL repo is reachable from Windows at `\\wsl$\...`, and the Windows userdata is reachable from WSL at `/mnt/c/Users/<you>/AppData/Roaming/Kodi/`. |
| **Kodi in Docker** (e.g. `linuxserver/kodi`) | Disposable, reproducible, web/VNC UI. Good for wiring straight to the repo. |
| **LibreELEC VM** | Closest to a real device and matches Kodi 19 / Python 3.8 — our compatibility floor. Heaviest to set up. |

Whichever you pick, **enable the JSON-RPC web server** so the sanity-check
script (below) can reach it: Settings → Services → Control → *Allow remote
control via HTTP* (default port `8080`). Note the port, username, and password.

## 2. Give Kodi a library

The add-on reads `VideoLibrary.GetMovies` / `VideoLibrary.GetEpisodes`. **If the
library is empty, both channels come back empty and there is nothing to
schedule.** Point Kodi at a media source (even a handful of sample files with
NFO metadata) and let it scan before chasing any add-on bug.

Confirm the library is actually populated with the sanity-check script in §6 —
do this *first*, so you never debug the add-on when the real problem is an empty
library.

## 3. Build and install the add-on

The build packages **committed** state only, so commit first. From the repo:

```bash
make zip          # → dist/plugin.video.libtv-<version>.zip
```

(Never zip with `git archive --format=zip` or reuse a zip path across
rebuilds — see the packaging gotchas in `CLAUDE.md`.) Then in Kodi:

1. Settings → System → Add-ons → enable **Unknown sources**.
2. Add-ons → **Install from zip file** → select the versioned zip.
3. If reinstalling during iteration, fully restart Kodi first (zip
   directory cache).

## 4. Wire up IPTV Simple Client

1. Enable **PVR IPTV Simple Client** (Add-ons → My add-ons → PVR clients).
2. Generate the files first so they exist: open LibTV's settings and press
   **Regenerate now** (this runs `RunPlugin(plugin://plugin.video.libtv/?action=build)`),
   or let the background service run at login.
3. The files land in the add-on profile directory:
   `userdata/addon_data/plugin.video.libtv/` — on Windows Kodi that is
   `%APPDATA%\Kodi\userdata\addon_data\plugin.video.libtv\`. You should see
   `channels.m3u`, `guide.xmltv`, and `schedule.json`.
4. Configure IPTV Simple:
   - **General → M3U Play List** → path to `channels.m3u` (local file).
   - **EPG Settings → XMLTV** → path to `guide.xmltv`.
5. Open Kodi's **TV** section. The two default channels (Movies, TV Shows) —
   plus any custom channels from `channels.json` — should appear with a
   populated guide.

## 5. What to verify (the things unit tests can't)

- **Guide ingestion** — programme names and times in the EPG match `guide.xmltv`.
- **Zap resolves and plays** — selecting a channel hits
  `?action=play&channel=libtv.movies` (or `libtv.tv`) and plays whatever the
  schedule says is on air *now*.
- **Join-in-progress** — zap to a channel mid-programme and confirm playback
  **jumps partway in shortly after starting** (expect a moment of the
  beginning before the jump), matching where the schedule places you. Test
  BOTH first tune *and* switching between channels — they exercise different
  code paths. The seek is performed by the background service
  (`daemon.JoinInProgressPlayer.onAVStarted`); the log line to look for is
  `LibTV: joining programme in progress at <n>s`. Requires the service to be
  running (it starts with Kodi / on add-on install).
  - **This is the top-priority live-verification item right now**: the
    resolver sets a `"libtv_seek_offset"` property directly on the resolved
    `ListItem` as the *primary* handoff (read back via
    `Player().getPlayingItem().getProperty(...)` in `daemon.py`), with
    `pending_seek.json` kept only as a fallback — see `docs/architecture.md`
    §6 and the "NOT YET live-verified" note in `CLAUDE.md`. To confirm the
    property path is actually what's firing (not silently falling through to
    the file), temporarily add a log line in
    `JoinInProgressPlayer._seek_offset` right after a truthy `raw`, or watch
    that the seek still happens correctly even if you delete
    `pending_seek.json` from the profile dir between resolving and playback
    starting (a tight window, but on a slow zap you may catch it). Test
    across *channel changes specifically* — that's the case the file-based
    approach was built to handle and where a property-based approach could
    behave differently. Once confirmed reliable across several zaps, the
    `pending_seek.json` fallback code in `plugin.py`/`daemon.py` can be
    deleted.
- **Guide/playback agreement** — what the EPG shows as "now" is exactly what
  plays. Both derive from `schedule.json`; they must never disagree.
- **Episode durations are real, not 90-minute defaults** — after a rebuild,
  spot-check an episode channel in the EPG: programme lengths must match the
  files (e.g. ~44 min slots for a 43:52 episode), not a uniform 1h30m grid.
  A wall of 90-minute slots means `runtime` came back 0 — historically
  because `streamdetails` was missing from the `GetEpisodes` property list
  (the Kodi quirk recorded in `CLAUDE.md`). This also silently breaks
  join-in-progress: the offset is computed against the oversized slot and
  the seek clamps to 10 s before the file's end (playback joins at the
  credits). Verified against a real library on 2026-07-14.
- **Schedule stability within a day** — press **Regenerate now**, then confirm
  what was on air a minute ago did not retroactively change (channels anchor at
  midnight UTC with a `channel_id:anchor` seed).
- **Guide refreshes after a rebuild (no Kodi restart)** — with nothing
  playing, press **Regenerate now** and confirm the TV guide reflects the new
  data within a few seconds. The refresh works by toggling IPTV Simple off/on;
  the log line to look for is
  `LibTV: toggled IPTV Simple to reload channels and guide`. Expect a brief
  PVR "importing channels" flash — that is the reload happening.
- **Refresh is skipped during playback** — rebuild while a stream is playing:
  playback must NOT be interrupted, the notification reads
  "Channels rebuilt — guide refresh skipped", and the log shows
  `LibTV: playback active, skipping PVR refresh`.
- **Refresh self-heals after playback ends** — trigger a rebuild while
  something is playing (skipped, per above), then stop playback and wait up
  to `daemon.PVR_RETRY_SECONDS` (30s): the guide should refresh on its own
  without waiting for the next full `regen_interval_hours` cycle, and
  without a second manual "Regenerate now". Look for
  `LibTV: toggled IPTV Simple to reload channels and guide` appearing within
  that window of playback stopping. This also confirms the retry doesn't
  starve the normal regen cycle — if you leave something playing for longer
  than `regen_interval_hours`, a full regen should still occur on schedule
  (check `schedule.json`'s `anchor`/mtime) even though the refresh itself
  keeps retrying every 30s.
- **Episode/movie durations self-correct after being played once** — find
  (or force, via a scraper that omits duration) an item that schedules with
  the 90-minute default despite `streamdetails` being requested. Play it
  once to completion or until Kodi reports a real duration, then trigger a
  rebuild: the guide slot should now match the file's real length. Check
  `runtime_cache.json` in the profile dir for a `{file: seconds}` entry —
  the log line to watch for the underlying mechanism is
  `LibTV: joining programme in progress...` (confirms `onAVStarted` fired;
  duration recording happens on the same event, silently — there's no
  dedicated log line for it currently).
- **XMLTV enrichment fields appear in the guide** — for a movie/episode with
  full library metadata (year, MPAA rating, director, cast, thumbnail,
  rating, playcount), confirm the EPG entry (or `guide.xmltv` directly)
  shows a release year, director/cast credits, and a poster/thumbnail where
  the skin's EPG view supports it. An item missing some of this metadata in
  Kodi's library should simply omit the corresponding XMLTV element, not
  error.
  - **Star rating / new tag / dual episode-num** — check `guide.xmltv`
    directly for an item with a Kodi `rating` set: `<star-rating><value>` should
    show `X.X/10`. For an unwatched item (`playcount` 0), the `<programme>`
    should carry an empty `<new/>` element; play it to completion and
    regenerate — `<new/>` should disappear. For an episode, confirm **two**
    `<episode-num>` elements appear (`system="xmltv_ns"` zero-based, and
    `system="onscreen"` `SxxEyy"`) — if your skin's EPG only shows one, check
    which system it reads.
- **Runtime cache self-invalidates on upgrade** — after installing a new
  version over an old one, check `runtime_cache.json`'s `"version"` field
  updates to the new add-on version and the cache doesn't carry forward stale
  entries from a different version (delete the file and let it regrow if you
  need a clean before/after comparison).
- **Channel management UI** — open **Manage channels** (add-on menu or the
  settings button). Verify each command item actually runs its dialog flow
  when clicked (the items are non-folder command items that end with
  `Container.Refresh` — a pattern that needs real-Kodi confirmation):
  - **Add** a channel (e.g. Movies, one genre, a year range) → it appears in
    the list, in `channels.json`, and — after the automatic rebuild+refresh —
    in the TV guide with only matching items scheduled.
  - **Add a Mixed channel** (movies and TV shows in one channel) → the guide
    shows both movies and episodes interleaved on that channel, episode
    entries still get the show title / `SxxEyy` treatment, and genre/studio
    filter pickers offer values from both movies and TV shows.
  - **Content order** — on a library with more shows than `max_items`, set a
    TV-show channel's order to **Random**: after a rebuild the guide should
    show programmes from *more than* the first couple of alphabetically-first
    shows (this is the bug this feature fixes — confirm it actually varies,
    not just that the option exists). Regenerate again without changing the
    day and confirm what's on air right now is unchanged (day-stable
    selection). Switch the same channel to **A–Z** and confirm the guide
    settles to a fixed, alphabetically-first set of shows; switch to
    **Recently added** and confirm it's the most-recently-added items.
  - **Rename** → guide shows the new name on the *same* channel
    (`channels.json` id unchanged).
  - **Move up/down** → channel order changes in the guide after refresh.
  - **Delete** (with confirmation) → channel gone from the guide.
  - **Diff-driven invalidation (rename/move/delete take the fast path)** —
    with debug logging on, do a Rename, a Move up/down, and a Delete on a
    channel and confirm the log does **not** show a fresh
    `VideoLibrary.GetMovies`/`GetEpisodes` call for any of them (only
    `LibTV: relabeled schedule without a library refetch`), while an **Edit
    filters & order** on the same channel *does* trigger a normal fetch.
    Also confirm the guide and playback are still correct after each —
    especially after a rename immediately followed by a channel change,
    since this path skips the programme-timing recomputation entirely and
    relies on the existing schedule still being valid.
  - Filter counts sanity: spot-check a genre/studio/year channel's programmes
    against the library (filters run in Kodi's DB via `List.Filter`; the
    unit tests only verify the filter JSON we send).
  - **Channel preview count** — while adding or editing a channel's
    filters/order, confirm a "N item(s) match this channel" notification
    appears right after the order/genre/studio/year dialogs and before the
    "Channels & guide updated" rebuild notification. Pick a filter
    combination you know matches nothing (e.g. a genre with no year overlap)
    and confirm it reports 0 rather than erroring or silently saving.
  - **Auto-generate channels by genre** (add-on menu's "+ Auto-generate
    channels by genre" item, or the settings button) → pick a content type,
    multiselect several genres, confirm → each selected genre gets its own
    channel (named e.g. "Action Movies") with programmes matching that
    genre. Reopen the same flow: the previously selected genres should show
    pre-checked. Uncheck one and confirm → its channel disappears from the
    guide while the others and any manually created channels are untouched.
    Run it again for a *different* content type (e.g. TV shows after
    Movies) and confirm the first type's autotune channels survive.
  - **Auto-generate channels by studio** (add-on menu's "+ Auto-generate
    channels by studio" item, or the settings button) → same flow as genre
    autotune but from the library's studio field; confirm it behaves
    identically (create/rerun-idempotent/deselect-removes) and that genre-
    and studio-autotune channels for the same content type coexist without
    either rebuild deleting the other's channels — e.g. run genre autotune
    for Movies, then studio autotune for Movies, and confirm both sets of
    channels are present in the guide afterward.
- **Resolver loop guard** — hard to trigger deliberately without a broken
  channel, but if you ever see `LibTV: schedule miss for <id>, regenerating`
  logged repeatedly within a few seconds for the same channel, the very next
  one should instead log
  `LibTV: repeated schedule miss for <id>, skipping regen (loop guard)` and
  not trigger another full JSON-RPC library rebuild. Low priority to chase
  deliberately — the mechanism only prevents wasted work, not incorrect
  playback, so this is worth a quick log check if it comes up rather than a
  dedicated repro.

## 5a. In-playback EPG overlay

A code-only overlay listing every channel's current/next programme
(`docs/architecture.md` §6a), reachable via `context.py` → `overlay.show()`.
Neither trigger below nor the `xbmcgui.WindowDialog` rendering can be
unit-tested.

**Trigger 1 — `kodi.context.item` (confirmed NOT working):** a "LibTV guide
(Now/Next)" video context-menu entry, visible only while playing. Live
testing found this entry does not appear — neither a mouse right-click nor
the `c` key showed it (`c` instead opened Kodi's own built-in channel/guide
overlay). The extension's XML is schema-valid; root cause is unconfirmed
(possibly skin- or remote-specific). **Do not rely on this trigger.**

**Trigger 2 — `RunScript(plugin.video.libtv)` via a keymap (confirmed
working):** added because trigger 1 didn't surface. Add a keymap so any key
you like calls the overlay directly, bypassing the context menu entirely.
Two ways to set this up, both producing the same file:

- **Via settings (use this — confirmed working):** open LibTV's settings →
  "Guide & playback" → set **Hotkey** to a Kodi key name (default `g`; check
  your skin/remote's existing keymap first so you don't shadow something
  already bound), then press **Save hotkey now**
  (`keymap.apply_from_settings`). Confirm a notification says the hotkey was
  saved, and that `special://profile/keymaps/libtv.xml` (Windows:
  `%APPDATA%\Kodi\userdata\keymaps\libtv.xml`) now exists with content
  matching the manual snippet below. Clear the Hotkey field and save again
  to confirm the file is removed.
- **Manual fallback**, if you want to hand-verify the format or the settings
  path isn't working: create `special://userdata/keymaps/libtv.xml` yourself
  with:

```xml
<keymap>
    <FullscreenVideo>
        <keyboard>
            <g>RunScript(plugin.video.libtv)</g>
        </keyboard>
    </FullscreenVideo>
    <FullscreenLiveTV>
        <keyboard>
            <g>RunScript(plugin.video.libtv)</g>
        </keyboard>
    </FullscreenLiveTV>
</keymap>
```

**Live-verified finding**: a `FullscreenVideo`-only binding produced
**zero** effect while a PVR channel was genuinely playing full-screen — not
even a trace in `kodi.log` of `RunScript` firing. Binding the same key under
`FullscreenLiveTV` too (as above) is the fix — some Kodi versions/skins
still route live TV playback through that legacy window context rather
than `FullscreenVideo`. `keymap.render_keymap_xml` now writes both sections;
if you saved a hotkey before this fix, press **Save hotkey now** again to
regenerate the file with both sections, then restart Kodi and retest.

Either way, restart Kodi (keymaps are loaded at startup) and press the key
while a LibTV channel is playing.

**Confirmed live**: with the dual-context keymap, pressing the bound key
during actual PVR playback did invoke `RunScript(plugin.video.libtv)` →
`context.py` → `overlay.show()` — proven by a `kodi.log` traceback
originating inside `overlay.py` itself. That traceback was a real bug,
now fixed: `xbmcgui.ControlList(..., itemHeight=60)` raised `TypeError`
because Kodi's actual keyword name is `_itemHeight` (see `CLAUDE.md`'s
hard-constraints note). The next attempt hit a second bug: `Control N in
window M has been asked to focus, but it can't` in `kodi.log` (no
traceback) — `setFocus()` was called from `__init__`, before the window
was ever shown via `doModal()`, so the focus request silently failed.
Fixed by moving `setFocus()` into an `onInit()` override. The *next* live
pass confirmed the window opened and blocked in `doModal()` exactly as
expected (log showed `overlay showing N channel row(s)`, then nothing
further until closed) — **but rendered nothing visible at all**: pressing
Esc once did nothing, pressing it again brought up Kodi's own OSD with the
video still playing underneath, meaning the overlay had been open the
whole time and closed on that first Esc, just invisibly. Root cause: a
code-only `WindowDialog` has no background of its own, and the
`ControlList` had no explicit text colors. **Fix**: added
`resources/media/overlay_bg.png` (small solid semi-transparent PNG, the
add-on's only bundled image asset) drawn behind the list via
`xbmcgui.ControlImage`, plus explicit `textColor`/`selectedColor` on the
list. The *next* live pass confirmed the background now renders — but
**still no text or row content was visible**, isolating a second,
independent problem. Fixed two suspects at once: `ControlList` had no
`font` (now `'font12'`), and rows used `label`/`label2` (a bare, no-skin
list has no defined layout for a second label at all — combined into one
`label` string via `overlay._row_label`). The *next* live pass showed the
background rendering correctly with **still absolutely nothing else
visible — not even a focus rectangle or row divider** — across four fix
attempts targeting different `ControlList` parameters, pointing at the
no-skin `ControlList` item-rendering path itself rather than any one
keyword. **Fix**: rendering rebuilt on plain `xbmcgui.ControlLabel`s (one
per channel row) with hand-rolled up/down navigation and highlight
(`_EpgOverlay.onAction` tracks its own cursor and recolors labels
directly) instead of any native list/button focus behavior. **Confirmed
live**: readable text rows finally appeared. Two things were still wrong:
(1) up/down changed Kodi's own native channel-info banner instead of
anything in the overlay (the highlight never moved, and nothing tuned) —
traced to listening for the generic `ACTION_MOVE_UP`/`ACTION_MOVE_DOWN`
when a remote/keyboard during PVR playback apparently sends
`ACTION_CHANNEL_UP`/`ACTION_CHANNEL_DOWN` instead; `onAction` now handles
both pairs. (2) The panel spanned nearly the full screen height instead
of a small strip near the bottom — rebuilt as a fixed bottom-margin panel
with only 4 `ControlLabel`s reused via scrolling, not one per channel.
**Confirmed live**: panel now sits correctly in the bottom margin, and
the overlay's own cursor now moves on up/down (fixing that part) — but
two more things were wrong: (1) **no text was visible until the first
up/down press** — labels were populated via `setLabel()` from `onInit()`,
which apparently doesn't paint until the next redraw; fixed by always
constructing labels with their real text as a constructor argument
instead (`onInit()` removed, no longer needed). (2) **the gold highlight
never appeared, on any row** — the same "`setLabel(textColor=...)` after
the first render doesn't repaint" problem `ControlList`'s `selectedColor`
already had; replaced with a `"> "` text-prefix marker instead of a color
change. Also confirmed (and accepted as a cosmetic, Python-unfixable
side effect): Kodi's own native channel-preview banner still fires
alongside `ACTION_CHANNEL_UP`/`DOWN` — the actual tuned channel does not
change from it, only an explicit selection does. **Whether the marker
highlight now shows, and whether selecting a row tunes the channel, has
still not been checked** (Enter/OK wasn't reachable to test last round).
That's the next thing to verify.

Checklist, using whichever trigger you're testing:

- **Settings write the keymap file correctly** — confirm the Hotkey
  setting + "Save hotkey now" button actually produces
  `special://profile/keymaps/libtv.xml` with the expected binding, and that
  clearing the field + saving removes the file. This exercises real
  `xbmcvfs`/filesystem behavior that unit tests only fake.
- **Trigger opens the overlay** — confirm the chosen mechanism (keymap key,
  or the context-menu entry if you're re-testing trigger 1 on a different
  skin/remote) actually opens the Now/Next list while a LibTV channel is
  playing, and does nothing/is absent when nothing is playing.
- **Playback is undisturbed** — opening the overlay must not
  pause/stop/hiccup the underlying stream. Test against an actual **PVR
  IPTV Simple** channel specifically, not just a regular library video —
  this project has repeatedly found PVR-resolved streams behave differently
  from regular playback (`StartOffset` ignored, resolver script termination
  on channel change), so this is not assumed to just work by analogy.
- **Now/Next text is correct** — cross-check at least two channels' rows
  against `schedule.json`, including one channel near a programme boundary
  (confirm the "Next" title flips at the right moment).
- **Selecting a row tunes there** — picking a different channel's row
  closes the overlay and switches to it via
  `PlayMedia(plugin://plugin.video.libtv/?action=play&channel=<id>)`; the
  new channel plays whatever the schedule says is on air now (same as a
  normal zap).
- **Back/escape closes without switching** — confirm the currently playing
  channel is unaffected.
- **No PVR refresh happens** — while using the overlay, the log must show
  no `LibTV: toggled IPTV Simple to reload channels and guide` or
  `Addons.SetAddonEnabled` calls; the overlay is read-only against
  `schedule.json` and must never regenerate or refresh the PVR client.

## 6. Sanity-check the library over JSON-RPC

`scripts/sanity_check.py` queries a running Kodi's HTTP JSON-RPC endpoint with
the **same** properties the add-on uses (`resources/lib/libtv/library.py`) and
flags the mistakes that actually bite:

- empty library (nothing to schedule);
- items missing `file` (unplayable — the resolver sets `ListItem(path=...)`
  from it);
- items with a `runtime` that looks like **minutes**, not the **seconds**
  JSON-RPC actually returns (the classic Kodi trap called out in `CLAUDE.md`);
- items with no usable duration at all (no runtime *and* no stream-details
  duration — warned, not fatal: they schedule at the 90-minute default).

```bash
python3 scripts/sanity_check.py --host 127.0.0.1 --port 8080 \
    --user kodi --password secret
```

(Only stdlib — no venv needed. `--user`/`--password` are optional if you left
HTTP auth off.)

## 7. Debugging loop

Enable **Settings → System → Logging → Enable debug logging**, then follow the
log — all `xbmc.log(...)` calls and Python tracebacks land there:

- Windows: `%APPDATA%\Kodi\kodi.log`
- Linux / Docker: `~/.kodi/temp/kodi.log`

From WSL you can follow the Windows Kodi log directly:

```bash
tail -f "/mnt/c/Users/<you>/AppData/Roaming/Kodi/kodi.log" | grep -i libtv
```

**The live Kodi is production.** The installed *code* lives in
`addons/plugin.video.libtv/` (separate from `userdata/addon_data/...`, which
is generated output). Never edit, copy, or symlink repo files into it — every
change reaches the live instance only via commit → `make zip` → install the
versioned zip → restart Kodi (the `deploy-to-kodi` skill walks the full
gate). Reading from the live instance (logs, JSON-RPC, generated artifacts)
is always fine.
