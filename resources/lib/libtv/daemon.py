"""Background service: regenerates on a timer and performs the
join-in-progress seek when a LibTV-resolved stream starts playing."""
from __future__ import annotations

import time

import xbmc

from libtv import generator

# How often to retry just the PVR guide refresh (not a full regen) while one
# is pending because playback was active when it was last attempted.
PVR_RETRY_SECONDS = 30


class JoinInProgressPlayer(xbmc.Player):
    """Seeks a freshly started stream to the scheduled position.

    The resolver (plugin.py) leaves a pending-seek file; onAVStarted fires
    exactly when playback begins, so the seek happens here in the long-lived
    service — the resolver script itself gets terminated during channel
    changes and cannot be trusted to stay alive long enough to seek.
    """

    def onAVStarted(self):
        try:
            playing = self.getPlayingFile()
            total = self.getTotalTime()
        except RuntimeError:
            return
        # Record the real duration for whatever just started, regardless of
        # whether it's a join-in-progress seek — this is what lets
        # library.fetch_channels fall back to an observed duration instead
        # of a scraper-provided runtime of 0.
        generator.record_observed_runtime(playing, total)

        offset = self._seek_offset(playing)
        if offset is None:
            return
        # Clamp: bad runtime metadata can schedule slots longer than the
        # actual file; never seek past (or into the last 10s of) the real
        # duration.
        target = min(offset, max(total - 10, 0))
        if target > 5:
            xbmc.log(f"LibTV: joining programme in progress at {target}s", xbmc.LOGINFO)
            self.seekTime(target)

    def _seek_offset(self, playing_file):
        """The resolver's chosen join-in-progress offset for playing_file,
        or None if there is nothing to seek to.

        Prefers the "libtv_seek_offset" property the resolver set directly
        on the resolved ListItem (plugin.py's play()) — Kodi's Player core
        retains a resolved item's own properties for as long as it's
        playing, independent of the resolver script (which has already
        exited by the time this fires) and with no rapid-zap ambiguity
        (this is *the* item currently playing, not a guess from a filename
        match). Falls back to the pending-seek file — kept as a safety net
        until the property path is live-verified across PVR channel changes
        (see docs/live-testing.md); drop the file path once confirmed.
        """
        try:
            item = self.getPlayingItem()
        except RuntimeError:
            item = None
        raw = item.getProperty("libtv_seek_offset") if item else ""
        if raw:
            generator.clear_pending_seek()
            try:
                return int(raw)
            except ValueError:
                pass

        pending = generator.read_pending_seek()
        if not pending or pending["file"] != playing_file:
            # Unknown, or belongs to another stream (rapid zap outran us).
            # Leave it for the stream it belongs to; staleness cleanup
            # discards it if that stream never starts.
            return None
        generator.clear_pending_seek()
        return pending["offset"]


def _regenerate_safely():
    """Run one regen+refresh cycle. Returns True if the PVR refresh still
    needs a retry (skipped because something was playing) rather than
    waiting for the next full regen cycle — see PVR_RETRY_SECONDS."""
    try:
        generator.regenerate()
        # IPTV Simple serves cached data until reloaded; refresh_pvr skips
        # itself during playback so a running stream is never interrupted.
        if generator.refresh_pvr():
            return False
        return xbmc.Player().isPlaying()
    except Exception as exc:  # never let one bad run kill the service
        xbmc.log(f"LibTV: generation failed: {exc}", xbmc.LOGERROR)
        return False


def run():
    monitor = xbmc.Monitor()
    # Must stay referenced for the lifetime of the service or Kodi stops
    # delivering player callbacks.
    player = JoinInProgressPlayer()
    pending_pvr_refresh = _regenerate_safely()
    next_regen = time.time() + generator.regen_interval_seconds()
    while not monitor.abortRequested():
        wait = PVR_RETRY_SECONDS if pending_pvr_refresh else max(1, next_regen - time.time())
        if monitor.waitForAbort(wait):
            break
        if time.time() >= next_regen:
            pending_pvr_refresh = _regenerate_safely()
            next_regen = time.time() + generator.regen_interval_seconds()
        elif pending_pvr_refresh:
            # Guide/M3U are already fresh; only the IPTV Simple reload was
            # skipped last time, so just retry the toggle, not a full regen.
            pending_pvr_refresh = not generator.refresh_pvr()
    del player
