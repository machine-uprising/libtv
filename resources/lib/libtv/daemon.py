"""Background service loop: regenerate at login and on a timer."""
from __future__ import annotations

import xbmc

from libtv import generator


def _regenerate_safely():
    try:
        generator.regenerate()
    except Exception as exc:  # never let one bad run kill the service
        xbmc.log(f"LibTV: generation failed: {exc}", xbmc.LOGERROR)


def run():
    monitor = xbmc.Monitor()
    _regenerate_safely()
    while not monitor.abortRequested():
        if monitor.waitForAbort(generator.regen_interval_seconds()):
            break
        _regenerate_safely()
