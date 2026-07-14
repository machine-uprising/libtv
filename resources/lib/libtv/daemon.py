"""Background service: regenerates on a timer and performs the
join-in-progress seek when a LibTV-resolved stream starts playing."""
from __future__ import annotations

import xbmc

from libtv import generator


class JoinInProgressPlayer(xbmc.Player):
    """Seeks a freshly started stream to the scheduled position.

    The resolver (plugin.py) leaves a pending-seek file; onAVStarted fires
    exactly when playback begins, so the seek happens here in the long-lived
    service — the resolver script itself gets terminated during channel
    changes and cannot be trusted to stay alive long enough to seek.
    """

    def onAVStarted(self):
        pending = generator.read_pending_seek()
        if not pending:
            return
        try:
            playing = self.getPlayingFile()
            total = self.getTotalTime()
        except RuntimeError:
            return
        if playing != pending["file"]:
            # Something else started (or a rapid zap outran us). Leave the
            # pending seek for the stream it belongs to; staleness cleanup
            # discards it if that stream never starts.
            return
        generator.clear_pending_seek()
        # Clamp: bad runtime metadata can schedule slots longer than the
        # actual file; never seek past (or into the last 10s of) the real
        # duration.
        target = min(pending["offset"], max(total - 10, 0))
        if target > 5:
            xbmc.log(f"LibTV: joining programme in progress at {target}s", xbmc.LOGINFO)
            self.seekTime(target)


def _regenerate_safely():
    try:
        generator.regenerate()
        # IPTV Simple serves cached data until reloaded; refresh_pvr skips
        # itself during playback so a running stream is never interrupted.
        generator.refresh_pvr()
    except Exception as exc:  # never let one bad run kill the service
        xbmc.log(f"LibTV: generation failed: {exc}", xbmc.LOGERROR)


def run():
    monitor = xbmc.Monitor()
    # Must stay referenced for the lifetime of the service or Kodi stops
    # delivering player callbacks.
    player = JoinInProgressPlayer()
    _regenerate_safely()
    while not monitor.abortRequested():
        if monitor.waitForAbort(generator.regen_interval_seconds()):
            break
        _regenerate_safely()
    del player
