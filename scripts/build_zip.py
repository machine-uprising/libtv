#!/usr/bin/env python3
"""Build dist/plugin.video.libtv.zip from committed HEAD.

Kodi rejects zips made by ``git archive --format=zip`` with an "invalid
structure" error: git appends the commit SHA as a zip archive comment, and
Kodi's zip parser expects the end-of-central-directory record at a fixed
offset from the end of the file. This script keeps git archive's semantics
(committed state only, .gitattributes export-ignore respected) by taking its
TAR output and repacking it as a plain zip with the top-level
``plugin.video.libtv/`` folder Kodi requires.

Stdlib only -- no venv required:

    python3 scripts/build_zip.py [--allow-dirty]
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tarfile
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ADDON_ID = "plugin.video.libtv"

# Fail the build if any of these are missing from the archive -- an
# overly-broad .gitignore once silently dropped resources/lib entirely.
REQUIRED = [
    "addon.xml",
    "default.py",
    "service.py",
    "resources/settings.xml",
    "resources/lib/libtv/plugin.py",
]


def run(repo, *cmd):
    return subprocess.run(cmd, cwd=repo, check=True, capture_output=True).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-dirty", action="store_true",
        help="build from HEAD even if the working tree has uncommitted changes",
    )
    args = parser.parse_args()

    repo = Path(__file__).resolve().parent.parent
    if not args.allow_dirty:
        dirty = run(repo, "git", "status", "--porcelain").decode().strip()
        if dirty:
            sys.exit(
                "error: uncommitted or untracked changes -- this packages HEAD only; "
                "commit first (or pass --allow-dirty if you know HEAD is what you want)."
            )

    tar_bytes = run(repo, "git", "archive", "--format=tar", "HEAD")

    # Versioned filename (Kodi repo convention). It also guarantees a fresh
    # path per release: Kodi caches zip directories by path, and replacing a
    # zip in place can serve stale entry offsets until Kodi restarts.
    version = ET.fromstring(run(repo, "git", "show", "HEAD:addon.xml")).get("version")
    dist = repo / "dist"
    dist.mkdir(exist_ok=True)
    zip_path = dist / f"{ADDON_ID}-{version}.zip"

    names = set()
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar, \
            zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        stamp = time.localtime()[:6]
        root = zipfile.ZipInfo(f"{ADDON_ID}/", date_time=stamp)
        root.external_attr = (0o755 << 16) | 0x10
        zf.writestr(root, b"")
        for member in tar:
            names.add(member.name)
            info = zipfile.ZipInfo(
                f"{ADDON_ID}/{member.name}{'/' if member.isdir() else ''}",
                date_time=time.localtime(member.mtime)[:6],
            )
            if member.isdir():
                info.external_attr = (0o755 << 16) | 0x10
                zf.writestr(info, b"")
            else:
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, tar.extractfile(member).read())

    missing = [f for f in REQUIRED if f not in names]
    if missing:
        zip_path.unlink()
        sys.exit(
            "error: built zip would be missing {} -- check .gitignore / .gitattributes".format(
                ", ".join(missing)
            )
        )

    print(f"built {zip_path.relative_to(repo)} ({len(names)} entries)")


if __name__ == "__main__":
    main()
