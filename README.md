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
  full control over channel names and lineup order. Editing a channel's
  filters shows a "N items match this channel" preview before you save, so
  an over-narrow (or accidentally empty) filter is obvious immediately.
  Renaming, reordering, or deleting a channel updates the guide instantly
  without re-scanning your whole library — only adding a channel or
  changing its filters does a full rescan.
- **Auto-generate channels by genre or studio**: instead of adding channels
  one at a time, pick a content type and multiselect genres (or studios)
  from your library — LibTV creates one channel per selection. Re-run either
  one any time to add/remove its channels; manually created channels and the
  other facet's autotune channels are never touched.
- **Content order** per channel: Random (default — a day-stable random
  sample of the whole filtered library, so a channel with more content than
  the item cap doesn't just end up replaying the same couple of
  alphabetically-first shows/movies forever), A–Z, or Recently added.
- **Rich guide entries**: release year, MPAA rating, star rating,
  director/cast credits, poster art, and an "unwatched" flag are pulled from
  your library metadata into the XMLTV guide when available, alongside the
  plot, genre, and episode numbering (both `SxxEyy` and the zero-based
  `xmltv_ns` form, for skins that read one or the other).
- **In-playback EPG overlay**: while a channel is playing, opens a scrollable
  list of every channel's current and next programme and lets you jump
  straight to one — without leaving playback, and regardless of whether your
  skin supports Kodi's own PVR Guide window with video playing behind it.
  Bind it to a key of your choice from **Settings → Guide & playback**: set
  **Hotkey** to any Kodi key name (default `g`), then press
  **Save hotkey now** — LibTV writes the keymap file for you
  (`special://profile/keymaps/libtv.xml`); clear the field and save again to
  remove the binding. Restart Kodi afterwards (keymaps load at startup).
  There's also a video context-menu entry ("LibTV guide (Now/Next)"), but it
  did not appear in testing — the hotkey is the trigger to actually use; see
  `docs/live-testing.md` §5a.
- After every rebuild LibTV automatically reloads IPTV Simple (unless
  something is playing); if a refresh is skipped because something was
  playing, it retries on its own shortly after playback stops, instead of
  waiting for the next scheduled rebuild.
- Episode/movie durations that come back missing from your library's
  metadata (a common scraper gap) self-correct after the item is played
  once — LibTV remembers the real duration and uses it for future guide
  slots.
- `service.py` runs in the background: regenerates the schedule and files at
  login and on a configurable interval, and performs the join-in-progress
  seek when a channel starts playing.
- `default.py` provides the add-on menu (manage channels, auto-generate,
  rebuild, settings) and the stream resolver.
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
PVR streams); as of v0.5.0 the primary handoff for that seek is a property
set on the resolved `ListItem` rather than a file, though this specific
mechanism is **not yet live-verified** (a file-based fallback remains in
place — see `docs/live-testing.md`). Episode durations come from the
library's stream details (v0.3.1), with a v0.5.0 addition: durations still
missing after that self-correct from observed playback the first time an
item is actually played, and the cache backing it self-invalidates on
upgrade (v0.6.0). Custom channels (genre/studio/year filters, rename,
reorder, a match-count preview before saving (v0.6.1), and diff-driven
invalidation so rename/reorder/delete skip the library rescan entirely
(v0.6.2)), genre- and studio-based channel autotune, richer XMLTV guide
fields (year/rating/star-rating/credits/artwork/unwatched flag/dual
episode-num systems), the automatic post-rebuild guide refresh (including
its self-healing retry), and a resolver loop guard against rapid repeated
schedule misses are implemented and unit-tested but still awaiting live
verification in a real Kodi (see `docs/live-testing.md`). As of v0.7.0, an
in-playback EPG overlay is implemented and passes `kodi-addon-checker`. Its
video context-menu trigger was tested live and **confirmed not to work**
(the entry never appeared); v0.7.1 adds a keymap-bindable
`RunScript(plugin.video.libtv)` trigger in response, and v0.7.2 adds a
"Hotkey" setting + "Save hotkey now" button so that keymap is written for
you instead of hand-edited. Both the `RunScript` trigger and the
settings-driven write, along with whether the overlay window behaves
correctly drawn over an actively playing PVR stream, are not yet themselves
live-verified. See `CLAUDE.md` for development constraints and known gaps.
