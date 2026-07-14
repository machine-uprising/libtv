---
name: document-changes
description: Sync all project documentation after a code or behavior change in LibTV. Use at the end of EVERY change that touches runtime code, settings, packaging, or workflow — before telling the user the work is done. Also invocable as /document-changes to audit doc freshness.
---

# Document every change

Every change to this repo lands **fully documented, in the same change**.
Documentation debt is a bug. Work through the checklist below; for each doc,
either update it or positively confirm it needs nothing.

## Doc map — who owns what

| Document | Owns | Update when… |
| --- | --- | --- |
| `docs/architecture.md` | **Canonical design**: components, data flow, schedule model, file formats, resolution/seek sequence, settings table, invariants, packaging, roadmap | Any behavior, interface, file-format, module, or settings change. If code and this doc disagree, fixing that is part of the change. |
| `CLAUDE.md` | Dev constraints, commands, gotchas, live-verified findings, known gaps | New constraint/trap discovered, command changed, gap opened or closed, layout changed |
| `docs/live-testing.md` | Live verification checklist | Anything that changes what must be verified in a real Kodi, or how |
| `README.md` | User-facing overview, install, dev quickstart, project status | User-visible features/setup changed; keep **Project status** current |
| `addon.xml` | Version + `<news>` | **Every** shipped behavior change: bump version (zip filename must change — Kodi caches zips by path) and rewrite `<news>` for the release |

## Checklist (run in this order)

1. **List what changed** this session: behavior, interfaces, file formats,
   settings, packaging, workflow, discoveries (things learned the hard way).
2. **`docs/architecture.md`** — walk the affected sections; rewrite them to
   describe the *current* design (not the change history). Check the
   settings table, artifact formats, and the tune sequence in §5 whenever
   the resolver, daemon, or generator moved.
3. **`CLAUDE.md`** — record any new live-verified finding or gotcha with
   *why* (these prevent regressions by future sessions); update commands,
   layout, and known gaps.
4. **`docs/live-testing.md`** — add/adjust verification steps for the new
   behavior; name the exact log line to look for where applicable.
5. **`README.md`** — refresh status and anything user-visible.
6. **`addon.xml`** — if runtime behavior changed: bump the version, rewrite
   `<news>`.
7. **Settings changed?** Confirm the triple: `resources/settings.xml` +
   `strings.po` msgid + `tests/conftest.py` `SETTINGS` default — and the
   settings table in `docs/architecture.md`.
8. **Verify**: `poetry run ruff check . && poetry run pytest -q` still green;
   grep the docs for now-stale references to what you changed (old file
   names, old commands, old version numbers).

## Rules

- Documentation states **what is**, not what used to be. Change history
  belongs in git commits and `<news>`, not in prose (exception: recorded
  findings in `CLAUDE.md` that explain why a design must not regress).
- Don't duplicate: each fact has one owning doc (see map); others link to it.
- Hard-won knowledge (live-Kodi behavior, packaging traps, API quirks) is
  the most valuable documentation — never let it live only in a chat
  transcript or commit message.
- A change is not "done" until this checklist is.
