# LibTV

Turn your Kodi library into real linear TV channels with full EPG/guide support.

## How it works

LibTV does not play video itself. It generates the two files that Kodi's
native PVR system needs, and lets **PVR IPTV Simple Client** do the rest:

```
┌─────────────────────┐   JSON-RPC    ┌──────────────────────────┐
│ Kodi video library  │ ────────────► │ LibTV (this add-on)      │
│ (movies, episodes)  │               │ builds channel lineups   │
└─────────────────────┘               └───────────┬──────────────┘
                                                  │ writes
                                    ┌─────────────▼──────────────┐
                                    │ channels.m3u  +  guide.xmltv│
                                    │ (add-on profile directory)  │
                                    └─────────────┬──────────────┘
                                                  │ consumed by
                                    ┌─────────────▼──────────────┐
                                    │ PVR IPTV Simple Client      │
                                    │ → Kodi Live TV: channels,   │
                                    │   guide, channel surfing    │
                                    └────────────────────────────┘
```

The M3U doesn't point channels at static files — every channel is a
`plugin://plugin.video.libtv/?action=play&channel=<id>` URL. LibTV keeps a
persisted schedule (`schedule.json`) of contiguous programmes anchored at
midnight UTC, and when you zap to a channel it resolves to whatever is on air
*right now*, joining the programme in progress. The XMLTV guide is rendered
from the same schedule, so the guide and playback always agree.

- **Custom channels**: out of the box you get two channels (all Movies, all
  TV Shows), and **Manage channels** (in the add-on menu and settings) lets
  you build your own — movie, TV-show, or mixed (movies and TV shows
  together) channels filtered by genre, studio, and production years, with
  full control over channel names and lineup order.
- **Content order** per channel: Random (default — a day-stable random
  sample of the whole filtered library, so a channel with more content than
  the item cap doesn't just end up replaying the same couple of
  alphabetically-first shows/movies forever), A–Z, or Recently added.
- After every rebuild LibTV automatically reloads IPTV Simple (unless
  something is playing), so lineup and guide changes show up without
  restarting Kodi.
- `service.py` runs in the background: regenerates the schedule and files at
  login and on a configurable interval, and performs the join-in-progress
  seek when a channel starts playing.
- `default.py` provides the add-on menu (manage channels, rebuild, settings)
  and the stream resolver.
- `resources/lib/libtv/` holds the actual logic; the schedule building,
  channel configuration, and file rendering are pure Python and fully
  unit-tested.
- Settings: max items per channel, shuffle, guide length, refresh interval,
  join-in-progress, automatic guide refresh.

The full design — component map, schedule model, file formats, and the
tune/seek sequence — is documented in [`docs/architecture.md`](docs/architecture.md).

## Installation

1. Build the zip (see below) or download a release.
2. Kodi → Add-ons → Install from zip file → select `plugin.video.libtv-<version>.zip`.
3. Install and enable **PVR IPTV Simple Client**, then point it at the
   generated files in the add-on profile directory
   (`userdata/addon_data/plugin.video.libtv/`):
   - M3U playlist: `channels.m3u`
   - XMLTV guide: `guide.xmltv`
4. Open Kodi's **TV** section — your library channels appear with a full guide.

## Development

Kodi add-ons run inside Kodi's embedded Python interpreter, where the
`xbmc*` API modules live. Development happens outside Kodi with
[Poetry](https://python-poetry.org/)-managed tooling and faked Kodi modules
for unit tests; only runtime behavior (playback, PVR, EPG UI) needs a real
Kodi instance.

### Dev container

Open the repo in VS Code and choose **Reopen in Container**
(requires Docker; on WSL2, enable Docker Desktop's WSL integration).
The container installs Python 3.12, Poetry, and all dev dependencies.

Working without the container works too — just install Poetry locally.

### Common tasks

```bash
poetry install                                  # one-time setup
poetry run pytest                               # unit tests
poetry run ruff check .                         # lint
poetry run kodi-addon-checker --branch omega .  # validate add-on structure
```

### Testing philosophy

`tests/conftest.py` injects fake `xbmc*` modules so the add-on imports and
the M3U/XMLTV generation logic is unit-testable in plain Python. Integration
behavior (does IPTV Simple pick up the guide, does zapping work) must be
verified in a running Kodi — install the built zip into a local Kodi and
check the Live TV section. The running Kodi is treated as production: changes
reach it only via commit → `make zip` → install the versioned zip → restart,
never by editing the installed add-on in place (see `docs/live-testing.md`).

### Building the installable zip

Kodi requires the folder inside the zip to match the add-on id
(`plugin.video.libtv`). Build from committed state:

```bash
make zip    # → dist/plugin.video.libtv-<version>.zip
```

Dev-only files (tests, Poetry config, dev container) are excluded via
`.gitattributes` `export-ignore` rules. Note the zip is built by
`scripts/build_zip.py` rather than `git archive --format=zip`, because Kodi
rejects git's zip output (it carries an archive comment Kodi can't parse).

## Project status

Working prototype, verified installing and generating inside a real Kodi:
schedule generation, M3U/XMLTV output, the `plugin://` stream resolver, and
the background refresh service are implemented and unit-tested, and the
add-on passes `kodi-addon-checker` cleanly. Join-in-progress works by
seeking right after playback starts (Kodi ignores `StartOffset` on resolved
PVR streams). Episode durations come from the library's stream details
(v0.3.1) — without them, episode scrapers that omit runtimes caused uniform
90-minute guide slots. Custom channels (genre/studio/year filters, rename, reorder)
and the automatic post-rebuild guide refresh are implemented and unit-tested
but still awaiting live verification in a real Kodi (see
`docs/live-testing.md`). See `CLAUDE.md` for development constraints and
known gaps.
