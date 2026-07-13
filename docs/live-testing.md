# Live testing in Kodi

Unit tests (`poetry run pytest`) exercise schedule building and M3U/XMLTV
rendering against faked `xbmc*` modules. They **cannot** verify anything that
only exists inside a running Kodi: JSON-RPC against a real library, IPTV Simple
Client ingesting the guide, stream resolution, and the `StartOffset`
join-in-progress behaviour. That verification requires a real Kodi instance.
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

`git archive` packages **committed** state only, so commit first. From the repo:

```bash
make zip          # commits nothing for you — commit first, then build
```

(or run the raw `git archive` command in the README). Then in Kodi:

1. Settings → System → Add-ons → enable **Unknown sources**.
2. Add-ons → **Install from zip file** → select `dist/plugin.video.libtv.zip`.

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
5. Open Kodi's **TV** section. The two channels (Movies, TV Shows) should
   appear with a populated guide.

## 5. What to verify (the things unit tests can't)

- **Guide ingestion** — programme names and times in the EPG match `guide.xmltv`.
- **Zap resolves and plays** — selecting a channel hits
  `?action=play&channel=libtv.movies` (or `libtv.tv`) and plays whatever the
  schedule says is on air *now*.
- **`StartOffset` join-in-progress** — this is the unverified one. Zap to a
  channel mid-programme and confirm playback **starts partway in**, matching how
  far into the programme the schedule places you, not from `0:00`. The resolver
  only sets `StartOffset` when `join_in_progress` is on and the offset exceeds
  5 seconds (`plugin.play`).
- **Guide/playback agreement** — what the EPG shows as "now" is exactly what
  plays. Both derive from `schedule.json`; they must never disagree.
- **Schedule stability within a day** — press **Regenerate now**, then confirm
  what was on air a minute ago did not retroactively change (channels anchor at
  midnight UTC with a `channel_id:anchor` seed).

## 6. Sanity-check the library over JSON-RPC

`scripts/sanity_check.py` queries a running Kodi's HTTP JSON-RPC endpoint with
the **same** properties the add-on uses (`resources/lib/libtv/library.py`) and
flags the mistakes that actually bite:

- empty library (nothing to schedule);
- items missing `file` (unplayable — the resolver sets `ListItem(path=...)`
  from it);
- items with a `runtime` that looks like **minutes**, not the **seconds**
  JSON-RPC actually returns (the classic Kodi trap called out in `CLAUDE.md`).

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

**Fast iteration:** the installed *code* lives in `addons/plugin.video.libtv/`
(separate from `userdata/addon_data/...`, which is generated output). During
development you can symlink or copy your repo's files there and press
**Regenerate now** / restart Kodi to pick up changes, rebuilding the zip only
for a clean from-scratch install test.
