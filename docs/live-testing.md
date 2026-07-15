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
  (`daemon.JoinInProgressPlayer.onAVStarted`) from `pending_seek.json`
  written by the resolver; the log line to look for is
  `LibTV: joining programme in progress at <n>s`. Requires the service to be
  running (it starts with Kodi / on add-on install).
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
  - Filter counts sanity: spot-check a genre/studio/year channel's programmes
    against the library (filters run in Kodi's DB via `List.Filter`; the
    unit tests only verify the filter JSON we send).

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
