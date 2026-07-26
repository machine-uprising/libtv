# Diagnosing "channels play but the EPG is empty"

Symptom: channels appear in Kodi's **TV** section and play correctly, but
the guide shows no programme content (blank rows, or channels with no
Now/Next info) for any channel. Channel playback working rules out a lot —
it confirms IPTV Simple Client is installed/enabled, LibTV's M3U is being
read, and the PVR client is generally functional. The problem is narrower
than "IPTV Simple isn't working" — it's specifically about the guide/XMLTV
path.

This document is a runbook for narrowing that down, in order of how likely
each step is to be the actual cause. Run them in order — each one splits
the remaining possibilities roughly in half.

## Before you start: what the code guarantees (and doesn't)

A code review of `writers.py`, `schedule.py`, and `generator.py` rules out
several tempting explanations up front, so don't spend time on them:

- **Channel-id mismatches between the M3U and XMLTV are not possible.** The
  `tvg-id` written into the M3U (`writers.py`'s `render_m3u`) and the
  `<channel id>`/`<programme channel>` written into the XMLTV
  (`render_xmltv`) all come from the exact same `ch["id"]` field in the
  schedule data — there's no separate id-generation step that could drift.
- **Programme times are not timezone-dependent.** `writers._xmltv_time()`
  formats every time with `time.gmtime(epoch)` and a hardcoded `+0000`
  literal — it never touches the host OS's local timezone or tzdata, so a
  Linux-vs-Windows (or any OS) timezone difference cannot be the cause.
- **If you used LibTV's "Auto-configure IPTV Simple Client" button**, the
  `m3uPathType`/`epgPathType` values it writes are symmetric (`"0"` = local
  path, for both), and both `m3uPath`/`epgPath` use the same `special://`
  scheme — so a manual-dropdown mistake (e.g. EPG field left on "Remote
  URL") isn't possible on that path. (It *is* possible if you set IPTV
  Simple up by hand instead — see step 2 below.)
- **The one real asymmetry auto-configure writes**: `m3uCache: "false"` vs
  `epgCache: "true"` (`generator._desired_pvr_instance_settings()`). LibTV
  disables IPTV Simple's cache for the channel list but leaves it enabled
  for the guide. This is currently undocumented/unexplained in
  `docs/architecture.md` §7, and is the strongest code-level lead: if IPTV
  Simple's EPG cache doesn't get correctly invalidated after LibTV writes a
  fresh `guide.xmltv` and toggles the addon, it would keep serving
  stale/empty cached guide data indefinitely while the M3U (never cached)
  keeps loading fine — which looks exactly like this symptom.

## Step 1 — does `guide.xmltv` actually have programme data?

This is the single most important check: it splits the problem into "LibTV
never generated a real guide" vs. "IPTV Simple isn't consuming a guide
that's actually fine."

Open the file in the add-on's profile directory:

- Linux: `~/.kodi/userdata/addon_data/plugin.video.libtv/guide.xmltv`
- Windows: `%APPDATA%\Kodi\userdata\addon_data\plugin.video.libtv\guide.xmltv`

Confirm it has real `<programme>` elements with populated `start`/`stop`/
`title` values — not just `<channel>` tags with no programmes underneath.

- **If programmes are present and look correct** → go to step 2.
- **If the file is missing, empty, or has channels but no programmes** →
  skip to step 5 (this is a generation-side problem, not an IPTV Simple
  problem).

## Step 2 — check what IPTV Simple itself has stored

Find the instance-settings file for the IPTV Simple instance LibTV
configured:

- Linux: `~/.kodi/userdata/addon_data/pvr.iptvsimple/instance-settings-<id>.xml`
- Windows: `%APPDATA%\Kodi\userdata\addon_data\pvr.iptvsimple\instance-settings-<id>.xml`

Confirm:

- `epgPath` is the `special://` form:
  `special://profile/addon_data/plugin.video.libtv/guide.xmltv` — **not** a
  resolved OS path like `/home/you/.kodi/.../guide.xmltv` or
  `C:\Users\...\guide.xmltv`.
- `epgPathType` is `0` (local path).
- Note the `epgCache` value (expected: `"true"` from auto-configure — this
  is the lead from the "before you start" section above).

If you configured IPTV Simple **manually** instead of via LibTV's
auto-configure button, also open IPTV Simple's own **Configure** dialog
(Settings → Player → my add-ons → PVR clients → PVR IPTV Simple Client →
Configure) and check the EPG Settings tab's path-type dropdown — confirm
it's actually set to "Local Path", not "Remote Path (URL)". A `special://`
value pasted into a field set to "Remote Path" would explain the M3U
loading fine (if that field was set correctly) while the EPG silently
fails.

## Step 3 — check `kodi.log`

- Linux: `~/.kodi/temp/kodi.log`
- Windows: `%APPDATA%\Kodi\kodi.log`

Search for `iptvsimple`, `EPG`, or `XMLTV` around startup or PVR-client-
enable time. A file-read or parse failure often logs here even when the
Kodi GUI shows no visible error at all. Also check for LibTV's own log
lines (`LibTV: wrote pvr.iptvsimple instance settings to <path>`,
`LibTV: toggled IPTV Simple to reload channels and guide`) to confirm the
configure/refresh actually ran.

## Step 4 — force a clean EPG reload (tests the cache lead directly)

In **Kodi's own** PVR & Live TV settings (not LibTV's), look for a "Clear
cached data for PVR add-ons" action, or manually delete IPTV Simple's own
EPG cache directory/database if one exists under its profile data. Then
run LibTV's **Regenerate channels now** again and check whether the guide
populates.

If this fixes it, the `epgCache: "true"` setting is the confirmed root
cause — see "If this turns out to be the cache setting" below.

## Step 5 — guide.xmltv itself is empty: check the generation side

If step 1 showed no real programme data, the bug is in LibTV's schedule
generation on this machine, not in IPTV Simple's consumption of it. Likely
causes:

- The library JSON-RPC fetch (`library.py`) returned no items on this
  Kodi instance — a different library/profile than whatever was used to
  verify this previously, so check whether Kodi's own library view (not
  LibTV) actually shows movies/episodes for the channels you configured.
  `scripts/sanity_check.py` (see `docs/live-testing.md` §6) queries the
  same JSON-RPC properties the add-on uses and flags an empty library
  directly.
- Channel filters in `channels.json` matching nothing on this library
  (e.g. a genre/studio/year filter tuned to a different library's
  content).
- Regeneration silently not having run yet — check `schedule.json`'s
  `anchor`/mtime in the profile directory to confirm a build actually
  happened recently.

## If this turns out to be the cache setting

If step 4 confirms `epgCache: "true"` is the cause, the fix is to change
`generator._desired_pvr_instance_settings()` to write `epgCache: "false"`
to match `m3uCache`, update the two tests that assert the current values
(`tests/test_generator_and_resolver.py`, `tests/test_writers.py`), and add
a code comment plus a `docs/architecture.md` §7 note explaining why both
are disabled. Run `poetry run pytest` and `poetry run ruff check .` after.
Whatever the actual root cause turns out to be, add a new "Live-verified
findings" entry to `CLAUDE.md` documenting it — this is new
platform-specific information (this bug surfaced on Linux/Ubuntu; the
add-on had previously only been live-verified on Windows) that the project
doesn't have recorded yet, per the existing pattern of entries there.
