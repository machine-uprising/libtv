---
name: deploy-to-kodi
description: Ship the current LibTV changes to the live Kodi instance the proper way — quality gate, commit, versioned zip, install, restart. Use whenever a change needs to reach the running Kodi (including "just verify this live"). NEVER copy, edit, or symlink files into the installed add-on directory instead of this.
---

# Deploy to the live Kodi

The Kodi instance on the Windows host is **production**. Its installed
add-on directory (`/mnt/c/Users/Dave/AppData/Roaming/Kodi/addons/plugin.video.libtv/`
from WSL) must only ever contain code that Kodi itself installed from a
built, versioned zip. Hot-patching it — even one file, even "just to
verify" — is forbidden: it bypasses the quality gate, desyncs the running
system from both the repo and the release, and leaves the long-lived
service running different code than what's on disk.

If you are about to write anything under `/mnt/c/.../Kodi/addons/`, stop
and run this checklist instead.

## Deployment checklist (in order — every step gates the next)

1. **Quality gate** — all of these green, run now, not remembered-green:
   ```bash
   poetry run ruff check . && poetry run pytest -q
   ```
   plus `kodi-addon-checker` if `addon.xml` changed (see CLAUDE.md for the
   clean rsync method).
2. **Docs synced** — the `document-changes` skill checklist has been run
   for this change.
3. **Version bumped** — `addon.xml` version is newer than what's installed
   (check the installed `addon.xml`), with an updated `<news>` line. The
   zip filename must change or Kodi's zip cache serves the stale build.
4. **Commit** — `make zip` packages committed state only and refuses a
   dirty tree. Commit everything that should ship (ask the user first if
   committing wasn't part of their request).
5. **Build** — `make zip` → `dist/plugin.video.libtv-<version>.zip`, which
   self-checks the zip contents.
6. **Install** — in Kodi: Add-ons → Install from zip file → select the new
   versioned zip. This needs the GUI; if you can't drive it, hand the user
   the exact zip path (Windows form:
   `\\wsl$\<distro>\home\dave\projects\libtv\dist\...`) and wait.
7. **Restart Kodi** — the long-lived service keeps old modules in memory;
   an install alone does not reload it.
8. **Verify live** — run the relevant checks from `docs/live-testing.md`
   against the running instance (JSON-RPC and reading logs/artifacts are
   always allowed — the prohibition is only on *writing* to the installed
   add-on directory).

## What is allowed without a deploy

- Reading anything on the live machine: `kodi.log`, generated artifacts in
  `userdata/addon_data/plugin.video.libtv/`, JSON-RPC queries.
- Triggering the add-on's own actions over JSON-RPC (e.g. a rebuild) to
  observe the *deployed* version's behavior — but conclusions only apply
  to the installed version, not uncommitted repo code.
