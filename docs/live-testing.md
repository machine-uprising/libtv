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

LibTV's add-on menu (first item) and settings (first group, top of the
screen) both have a **Setup guide** button (`plugin.show_setup_guide`) that
walks this whole section as a single numbered dialog — open it first and
confirm it reads correctly before working through the steps below by hand.

1. Enable **PVR IPTV Simple Client** (Add-ons → My add-ons → PVR clients).
2. Generate the files first so they exist: open LibTV's settings and press
   **Regenerate now** (this runs `RunPlugin(plugin://plugin.video.libtv/?action=build)`),
   or let the background service run at login.
3. The files land in the add-on profile directory:
   `userdata/addon_data/plugin.video.libtv/` — on Windows Kodi that is
   `%APPDATA%\Kodi\userdata\addon_data\plugin.video.libtv\`. You should see
   `channels.m3u`, `guide.xmltv`, and `schedule.json`.
4. Configure IPTV Simple with one of two paths:
   - **Auto-configure (§4a below, not yet live-verified — try the manual
     path first until it is)**: LibTV's add-on menu and settings both have
     an **Auto-configure IPTV Simple Client** button that writes IPTV
     Simple's configuration for you.
   - **Manual (the proven path)**: LibTV's add-on menu and settings ("Guide
     & playback" → **IPTV Simple Client setup paths**) show `special://`
     paths for `channels.m3u`/`guide.xmltv` to paste in yourself:
     - **General → M3U Play List** → `special://profile/addon_data/plugin.video.libtv/channels.m3u`.
     - **EPG Settings → XMLTV** → `special://profile/addon_data/plugin.video.libtv/guide.xmltv`.
     (Confirmed against `pvr.iptvsimple`'s own source that it reads local
     files through Kodi's VFS, so `special://` resolves the same as a real
     OS path would — if that turns out wrong on your setup, the resolved
     OS path is what step 3 above already showed as a fallback.)
5. Open Kodi's **TV** section. The two default channels (Movies, TV Shows) —
   plus any custom channels from `channels.json` — should appear with a
   populated guide.

## 4a. IPTV Simple auto-configuration (`configure_iptv_simple`)

**Read this before testing.** This feature writes a file directly into
`pvr.iptvsimple`'s own `addon_data` directory using an unofficial technique
(no supported Kodi API covers it — see `docs/architecture.md` §7 and
`CLAUDE.md`'s hard-constraints note). It looks up an existing instance *by
name* before deciding whether to create a new one or ask before updating
one — this is exactly the kind of logic that needs a real Kodi to confirm,
not just unit tests against faked file I/O. **If you have an existing IPTV
Simple setup you care about, back up
`userdata/addon_data/pvr.iptvsimple/` before testing this**, and prefer a
throwaway/test Kodi profile if you have one available.

1. Confirm PVR IPTV Simple Client is installed and enabled, and that
   LibTV has generated files at least once (§4 steps 1–2).
2. From LibTV's add-on menu or settings (first group → **Auto-configure
   IPTV Simple Client**), run the action, and **confirm a "Configuring IPTV
   Simple Client…" notification appears immediately** — this is the fix
   for a live-verified problem (see `CLAUDE.md`'s "Live-verified findings
   (IPTV Simple auto-configuration)") where the action previously gave no
   feedback at all until it finished, which for a disabled-IPTV-Simple run
   meant no visible feedback whatsoever.
3. Check `kodi.log` for `LibTV: wrote pvr.iptvsimple instance settings to
   <path>` followed by `LibTV: toggled IPTV Simple to reload channels and
   guide`, and confirm a second notification reads "IPTV Simple Client
   configured — restart Kodi if the guide doesn't appear".
4. **Confirm the file actually exists** at
   `userdata/addon_data/pvr.iptvsimple/instance-settings-<id>.xml`, and
   that `m3uPath`/`epgPath` inside it are the `special://` form
   (`special://profile/addon_data/plugin.video.libtv/channels.m3u` /
   `.../guide.xmltv`), **not** a resolved OS path like
   `C:\Users\...\channels.m3u` — this is the first thing to confirm, since
   everything downstream depends on IPTV Simple actually reading a
   `special://` path correctly (confirmed against its source, but not
   against a real running instance yet — see `docs/architecture.md` §7).
5. **The critical check — does Kodi actually treat this as a real
   instance?** Open Settings → Player → my add-ons → PVR clients →
   PVR IPTV Simple Client → **Configure**, and confirm an instance named
   "LibTV" (or whatever "Instance name" is set to) now appears there (this
   is the whole premise of the technique — a hand-written file being
   indistinguishable from one Kodi's own GUI wrote; if it does *not*
   appear, the technique doesn't work on this Kodi version/build and
   `docs/architecture.md` §7 needs a correction, not another live
   round-trip guess).
6. Open Kodi's **TV** section and confirm channels and guide appear —
   same end state as the manual §4 steps, but reached without typing
   anything into IPTV Simple's own settings. This is also where the
   `special://` path check from step 4 gets its real answer: if the guide
   is empty or IPTV Simple logs a file error, the `special://` value
   didn't resolve and the manual "IPTV Simple Client setup paths" dialog
   is the fallback while that gets investigated.
7. **Idempotency**: run **Auto-configure IPTV Simple Client** again with
   nothing changed. Confirm the notification reads "IPTV Simple Client is
   already configured for LibTV", no new/duplicate instance appears in
   IPTV Simple's Configure list, and `kodi.log` shows no second
   `Addons.SetAddonEnabled` toggle.
8. **Custom instance name**: change "Instance name" (top of settings) to
   something else (e.g. "My Channels"), run the action again, and confirm:
   a *new* instance with that name appears in IPTV Simple's Configure list
   (the old "LibTV"-named one is untouched, not renamed), `kodi.log`/the
   written file both reference the new name, and the TV section still
   works. Change it back to "LibTV" and run once more to confirm it finds
   the *original* instance again rather than creating a third one.
9. **Collision confirmation — the main new behavior to verify**: with
   nothing yet configured under the current instance name, first create an
   instance with that exact name **by hand** through Kodi's own PVR
   settings GUI (point it anywhere, e.g. a dummy file) — this simulates a
   user who already had their own same-named setup. Then run
   **Auto-configure IPTV Simple Client** and confirm:
   - A yes/no dialog appears naming the instance and asking to update it —
     it must NOT silently overwrite.
   - Choosing **No** leaves that instance completely unmodified (check its
     settings in Configure, or the file's `m3uPath`, are still whatever you
     set by hand) and the notification reads "IPTV Simple Client left
     unchanged".
   - Choosing **Yes** updates that *same* instance in place (still one
     entry in Configure, not a new duplicate one) to point at LibTV's
     files, and the notification reads the normal "configured" message.
10. **Playback guard**: start playing anything, then run the action while
    something not-yet-configured would need a change (e.g. after manually
    deleting the LibTV instance file first). Confirm it's skipped — log
    line `LibTV: playback active, skipping IPTV Simple auto-configure`,
    and **a blocking "OK" dialog** (not a notification toast) reading
    "Can't configure while something is playing…" — and nothing was
    written.
11. **Not-installed guard — the specific case that had no feedback at all
    before this fix**: with IPTV Simple disabled or not installed, run the
    action and confirm: the "Configuring…" notification still appears
    first, `kodi.log` shows a `LOGWARNING` line
    (`LibTV: pvr.iptvsimple not installed/enabled, skipping IPTV Simple
    auto-configure`), and — the actual fix — **a blocking "OK" dialog**
    appears (not a toast) telling you to install/enable IPTV Simple, with
    no file written. Confirm you can't miss it even if you've clicked
    away from the settings screen that triggered the action.
12. **If you have another, pre-existing PVR instance** (IPTV Simple or a
    different PVR client) configured before testing this, confirm it is
    completely unaffected — still present, still working, in Configure
    and in the TV section — after every step above.

Once all of this is confirmed, `configure_iptv_simple()` can be considered
for wiring into the automatic regeneration loop or the manual build action
(currently deliberately not automatic — see `docs/architecture.md` §7).

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
- **Settings-screen "Manage channels" button (needs real-Kodi confirmation,
  not yet live-verified)** — the button previously bound directly to
  `ActivateWindow(Videos,…?action=channels,return)` in `<data>` and, per a
  user report against the live instance, did nothing when clicked (the Add-on
  Settings dialog is itself modal — see `docs/architecture.md` §3 for why
  this can silently swallow a direct `ActivateWindow`). It now routes through
  `RunPlugin(…?action=open_channels)` → `plugin.open_channel_manager()`,
  which explicitly closes the settings dialog first
  (`Dialog.Close(all,true)`) and only then activates the Videos window.
  Confirm: opening Settings -> the new **Channels** tab -> **Manage
  channels** now closes the settings dialog and opens the channel list (not
  just that it does nothing); confirm the same for the add-on menu's own
  "Manage channels" folder item, which is unaffected by this change (plain
  container navigation, not launched from a modal dialog) and should still
  work as before.
- **`regenerate_now` / channel-management rebuild status messaging** —
  press **Regenerate channels now** and confirm a "Rebuilding channels &
  guide…" notification appears immediately (not just the final "Channels &
  guide updated" toast once the rebuild finishes) — this was a user-reported
  gap: a full rebuild is a synchronous JSON-RPC round trip per channel, so
  without the upfront toast the button looked unresponsive for however long
  that took. Confirm the same upfront toast appears for management-UI
  mutations that trigger a full rebuild (e.g. **Add channel**, **Edit
  filters & order**), since they share `plugin.build()`.
- **Channel management UI** — open **Manage channels** (add-on menu or the
  settings screen's **Channels** tab). Verify each command item actually
  runs its dialog flow when clicked (the items are non-folder command items
  that end with `Container.Refresh` — a pattern that needs real-Kodi
  confirmation):
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
    channels by genre" item, or the settings screen's **Channels** tab) →
    pick a content type, multiselect several genres, confirm → each
    selected genre gets its own
    channel (named e.g. "Action Movies") with programmes matching that
    genre. Reopen the same flow: the previously selected genres should show
    pre-checked. Uncheck one and confirm → its channel disappears from the
    guide while the others and any manually created channels are untouched.
    Run it again for a *different* content type (e.g. TV shows after
    Movies) and confirm the first type's autotune channels survive.
  - **Auto-generate channels by studio** (add-on menu's "+ Auto-generate
    channels by studio" item, or the settings screen's **Channels** tab) →
    same flow as genre autotune but from the library's studio field;
    confirm it behaves
    identically (create/rerun-idempotent/deselect-removes) and that genre-
    and studio-autotune channels for the same content type coexist without
    either rebuild deleting the other's channels — e.g. run genre autotune
    for Movies, then studio autotune for Movies, and confirm both sets of
    channels are present in the guide afterward.
- **IPTV Simple Client setup paths dialog** — from either the add-on's main
  menu or the settings button ("Guide & playback" → **IPTV Simple Client
  setup paths**), confirm a text dialog opens showing the `special://`
  form of the `channels.m3u`/`guide.xmltv` paths (not a resolved OS path),
  and that opening it does not disturb any in-progress playback or trigger
  a PVR refresh (no `Addons.SetAddonEnabled` / `LibTV: toggled IPTV
  Simple...` log lines).
- **Setup guide dialog** — from either the add-on's main menu (first item)
  or settings (first group, **Setup guide**), confirm the full numbered
  walkthrough opens, is readable/scrollable in a `textviewer` dialog (long
  Windows paths shouldn't get cut off), the M3U/XMLTV paths it shows match
  what's actually on disk, and — same as the paths-only dialog above —
  opening it never disturbs playback or triggers a PVR refresh.
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
change from it, only an explicit selection does. **Confirmed live**: the
`"> "` marker shows correctly from the moment the overlay opens. Two more
things were wrong: (1) **the marker always started on the first channel
in the list, not the one actually playing** — fixed by having
`plugin.play()` also set a `"libtv_channel_id"` ListItem property (the
same handoff mechanism as `"libtv_seek_offset"`), which
`overlay._current_channel_id()` reads back to set the overlay's initial
cursor. (2) **Left/Right vs Up/Down**: up/down was confirmed to actually
change the live channel (not just show a preview banner as first
assumed) — this can't be suppressed from Python, so navigation now uses
Left/Right (`ACTION_MOVE_LEFT`/`ACTION_MOVE_RIGHT`) instead, per the
user's choice among the fix tradeoffs. Left/Right are common video-seek
keys during regular playback, so **check specifically whether Left/Right
now has the same kind of collision Up/Down had** — don't assume it's
clean just because it's not channel-surfing. **Not yet checked**: does
the overlay now open on the correct channel, does Left/Right avoid a
collision, does selecting a row tune the channel. That's the next thing
to verify.

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
