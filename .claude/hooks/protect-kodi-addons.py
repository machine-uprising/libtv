#!/usr/bin/env python3
"""PreToolUse hook: the installed add-on in the live Kodi is PRODUCTION.

Denies any tool call that would write into Kodi's addons directory —
deployment goes commit -> make zip -> install zip -> restart Kodi (see
CLAUDE.md "The live Kodi is production" and the deploy-to-kodi skill).
Read-only access (cat/diff/ls/tail of the installed files) stays allowed.
"""
import json
import re
import sys

# WSL view of the installed add-ons dir (DrvFs paths are case-insensitive).
TARGET = re.compile(r"appdata/roaming/kodi/addons/", re.IGNORECASE)
# Mutation heuristic for shell commands: known write commands, in-place sed,
# or an output redirect (excluding fd redirects like 2>/dev/null).
MUTATION = re.compile(
    r"(^|[;&|\s])(cp|mv|rm|ln|rsync|mkdir|touch|tee|dd|truncate|unzip|install|chmod|chown)([\s;&|]|$)"
    r"|sed[^;|&]*-i"
    r"|(^|[^0-9>])>",
    re.IGNORECASE,
)
REASON = (
    "Blocked: the installed Kodi add-on directory is production. Deploy via "
    "commit -> make zip -> install the versioned zip in Kodi -> restart "
    "(run the deploy-to-kodi skill). Reading from it is fine; writing to it is not."
)


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    blocked = False
    if tool in ("Write", "Edit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        blocked = bool(TARGET.search(path))
    elif tool == "Bash":
        command = tool_input.get("command") or ""
        blocked = bool(TARGET.search(command)) and bool(MUTATION.search(command))

    if blocked:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": REASON,
                }
            },
            sys.stdout,
        )


if __name__ == "__main__":
    main()
