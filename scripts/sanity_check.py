#!/usr/bin/env python3
"""Sanity-check a running Kodi's video library over HTTP JSON-RPC.

Queries the SAME data the add-on relies on (resources/lib/libtv/library.py)
and flags the problems that actually break LibTV before you go chasing add-on
bugs in a live install:

  * empty library                -> nothing to schedule, both channels blank
  * items missing ``file``       -> unplayable; the resolver builds
                                    ListItem(path=item["file"])
  * ``runtime`` that looks like  -> JSON-RPC returns runtime in SECONDS; a
    minutes                         value like 90 for a feature film means
                                    something upstream is feeding minutes
                                    (the classic Kodi trap in CLAUDE.md)

Enable Kodi's JSON-RPC web server first: Settings -> Services -> Control ->
"Allow remote control via HTTP" (default port 8080).

Stdlib only -- no venv required:

    python3 scripts/sanity_check.py --host 127.0.0.1 --port 8080 \
        --user kodi --password secret
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request

# Mirror resources/lib/libtv/library.py so we check exactly what the add-on
# reads. Keep these in sync if the add-on's property lists change.
MOVIE_PROPS = ["title", "file", "runtime", "plot", "genre"]
EPISODE_PROPS = [
    "title", "file", "runtime", "plot", "showtitle", "season", "episode", "genre",
]

# A real feature/episode runtime in seconds is comfortably above this. A value
# below it (e.g. 90 for a 90-minute film, or 22 for a sitcom) is almost
# certainly minutes leaking in where seconds are expected.
MIN_PLAUSIBLE_SECONDS = 300


def jsonrpc(args, method, params=None):
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    ).encode()
    url = f"http://{args.host}:{args.port}/jsonrpc"
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    if args.user:
        token = base64.b64encode("{}:{}".format(args.user, args.password or "").encode()).decode()
        req.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            sys.exit("error: 401 Unauthorized -- pass --user/--password (HTTP auth is on).")
        sys.exit(f"error: HTTP {exc.code} from {url}")
    except urllib.error.URLError as exc:
        sys.exit(
            f"error: cannot reach {url} ({exc.reason}). Is Kodi running with "
            "'Allow remote control via HTTP' enabled?"
        )
    if "error" in body:
        sys.exit("error: JSON-RPC {} failed: {}".format(method, body["error"]))
    return body.get("result", {})


def check_kind(args, label, method, key, props):
    result = jsonrpc(args, method, {"properties": props})
    items = result.get(key, [])
    total = result.get("limits", {}).get("total", len(items))

    problems = []
    if not items:
        problems.append(f"EMPTY -- no {key} in the library; this channel will be blank")
        print_section(label, total, problems)
        return len(problems)

    missing_file = [it for it in items if not it.get("file")]
    if missing_file:
        problems.append(
            "{} item(s) missing 'file' -> unplayable (e.g. id {})".format(
                len(missing_file), missing_file[0].get(_id_field(key), "?")
            )
        )

    suspect = [
        it for it in items
        if isinstance(it.get("runtime"), int) and 0 < it["runtime"] < MIN_PLAUSIBLE_SECONDS
    ]
    if suspect:
        sample = suspect[0]
        problems.append(
            "{} item(s) have runtime < {}s -- looks like MINUTES not seconds "
            "(e.g. '{}' runtime={})".format(
                len(suspect), MIN_PLAUSIBLE_SECONDS,
                sample.get("title", "?"), sample.get("runtime"),
            )
        )

    zero_runtime = [it for it in items if not it.get("runtime")]
    if zero_runtime:
        problems.append(
            f"{len(zero_runtime)} item(s) have no runtime -> scheduled at the 90-min default"
        )

    print_section(label, total, problems)
    # zero-runtime is a warning, not a failure
    return len([p for p in problems if "default" not in p])


def _id_field(key):
    return {"movies": "movieid", "episodes": "episodeid"}.get(key, "id")


def print_section(label, total, problems):
    status = "OK" if not problems else "ISSUES"
    print(f"\n[{status}] {label} -- {total} item(s) reported")
    for p in problems:
        print(f"  - {p}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default="8080")
    parser.add_argument("--user", default=None, help="HTTP JSON-RPC username (if auth is enabled)")
    parser.add_argument("--password", default=None, help="HTTP JSON-RPC password")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    ping = jsonrpc(args, "JSONRPC.Ping")
    if ping != "pong":
        sys.exit(f"error: unexpected ping response: {ping!r}")
    print(f"Connected to Kodi at {args.host}:{args.port}")

    failures = 0
    failures += check_kind(args, "Movies channel", "VideoLibrary.GetMovies", "movies", MOVIE_PROPS)
    failures += check_kind(
        args, "TV Shows channel", "VideoLibrary.GetEpisodes", "episodes", EPISODE_PROPS
    )

    if failures:
        print(f"\nResult: {failures} issue group(s) found -- fix these before testing playback.")
        sys.exit(1)
    print("\nResult: library looks good for LibTV.")


if __name__ == "__main__":
    main()
