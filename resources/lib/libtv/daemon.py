"""Background service: regenerates on a timer and performs the
join-in-progress seek when a LibTV-resolved stream starts playing."""
from __future__ import annotations

import json
import time

import xbmc

from libtv import generator, schedule

# How often to retry just the PVR guide refresh (not a full regen) while one
# is pending because playback was active when it was last attempted.
PVR_RETRY_SECONDS = 30

# How long to wait before re-reading getTotalTime()/getPlayingFile() in
# onAVStarted — right after the callback fires, getTotalTime() can still
# report the *previous* item's duration (seen more on Linux decode paths),
# while getPlayingFile() already names the new file.
TOTAL_TIME_SETTLE_MS = 300

# Minimum spacing between EOF-triggered re-tunes, so a channel that ends
# immediately on every tune can never turn into a tight re-tune loop.
RETUNE_MIN_INTERVAL = 30

# How long to wait after onPlayBackEnded before re-tuning, so Kodi's own
# PVR stream-reopen (which sometimes continues the channel by itself) gets
# a chance to win instead of being double-tuned over.
RETUNE_GRACE_MS = 1000


class JoinInProgressPlayer(xbmc.Player):
    """Seeks a freshly started stream to the scheduled position, and
    re-tunes the channel when a programme's file plays to its natural end.

    The resolver (plugin.py) leaves a pending-seek file; onAVStarted fires
    exactly when playback begins, so the seek happens here in the long-lived
    service — the resolver script itself gets terminated during channel
    changes and cannot be trusted to stay alive long enough to seek.

    The resolver also resolves each tune to a plain finite video file, so
    when that file reaches EOF Kodi has no reason to consider the channel
    still live — without onPlayBackEnded re-tuning it, playback falls out
    of Live TV back to the guide/home window.
    """

    def __init__(self):
        super().__init__()
        # (file, kodi_channelid, libtv_channel_id) of the LibTV-resolved PVR
        # stream currently playing, or None when the active playback isn't a
        # LibTV channel (ordinary library playback must never re-tune).
        self._tracked = None
        self._last_retune = 0.0

    def onAVStarted(self):
        try:
            playing = self.getPlayingFile()
            total = self.getTotalTime()
            # Don't trust a single immediate read: getTotalTime() can lag a
            # channel change and report the previous item's duration under
            # the new item's filename, permanently poisoning the observed-
            # runtime cache (which now outranks scraped runtimes). Re-read
            # after a settle delay; if the file changed under us, skip
            # recording entirely — another onAVStarted is coming for it.
            xbmc.sleep(TOTAL_TIME_SETTLE_MS)
            if self.getPlayingFile() != playing:
                return
            total = self.getTotalTime()
        except RuntimeError:
            return
        # Record the real duration for whatever just started, regardless of
        # whether it's a join-in-progress seek — this is what lets
        # library.fetch_channels fall back to an observed duration instead
        # of a scraper-provided runtime of 0.
        generator.record_observed_runtime(playing, total)

        self._tracked = self._tracked_tune(playing)

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

    def onPlayBackEnded(self):
        """Natural EOF only (user stops/channel changes fire
        onPlayBackStopped instead): re-tune the channel so the next
        scheduled programme starts, instead of Kodi dropping to the guide."""
        tracked, self._tracked = self._tracked, None
        if not tracked:
            return
        ended_file, channelid, libtv_id = tracked

        now = time.time()
        if now - self._last_retune < RETUNE_MIN_INTERVAL:
            xbmc.log("LibTV: skipping EOF re-tune (rate limit)", xbmc.LOGWARNING)
            return

        # Loop guard: if the schedule still says the file that just ended is
        # on air (its slot was longer than the real file), re-tuning would
        # resolve the same file again, seek near its end, and end again — an
        # endless loop of 10-second tails. Accept the drop-to-guide instead;
        # correct runtime data makes this case rare.
        data = generator.load_schedule()
        found = schedule.find_current(data, libtv_id, now) if data else None
        if found is not None and found[0].get("file") == ended_file:
            xbmc.log(
                f"LibTV: not re-tuning {libtv_id} — schedule still shows the "
                "programme that just finished (slot longer than file)",
                xbmc.LOGWARNING,
            )
            return

        # Give Kodi's own PVR stream-reopen a moment; if it already
        # continued the channel, a second Player.Open would interrupt it.
        xbmc.sleep(RETUNE_GRACE_MS)
        if self.isPlaying():
            return

        xbmc.log(f"LibTV: programme ended, re-tuning channel {libtv_id}", xbmc.LOGINFO)
        result = json.loads(xbmc.executeJSONRPC(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "Player.Open",
            "params": {"item": {"channelid": channelid}},
        })))
        if "error" in result:
            xbmc.log(f"LibTV: EOF re-tune failed: {result['error']}", xbmc.LOGERROR)
            return
        self._last_retune = now

    def onPlayBackStopped(self):
        # User-initiated stop or channel change — never re-tune over it.
        self._tracked = None

    def onPlayBackError(self):
        self._tracked = None

    def _tracked_tune(self, playing_file):
        """(file, kodi_channelid, libtv_channel_id) when the active playback
        is a LibTV-resolved PVR channel, else None.

        The LibTV channel id comes from the "libtv_channel_id" property the
        resolver set on the resolved ListItem (retained by Kodi's Player
        core, same mechanism as libtv_seek_offset); the Kodi-side channelid
        (what Player.Open needs to re-tune) comes from Player.GetItem.
        """
        try:
            item = self.getPlayingItem()
        except RuntimeError:
            return None
        libtv_id = item.getProperty("libtv_channel_id") if item else ""
        if not libtv_id:
            return None

        players = json.loads(xbmc.executeJSONRPC(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "Player.GetActivePlayers",
            "params": {},
        }))).get("result") or []
        for player in players:
            if player.get("type") != "video":
                continue
            result = json.loads(xbmc.executeJSONRPC(json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "Player.GetItem",
                "params": {"playerid": player["playerid"]},
            }))).get("result") or {}
            got = result.get("item") or {}
            if got.get("type") == "channel" and got.get("id") is not None:
                return (playing_file, got["id"], libtv_id)
        return None

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
