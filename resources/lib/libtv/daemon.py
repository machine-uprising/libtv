"""Background service: regenerates on a timer and performs the
join-in-progress seek when a LibTV-resolved stream starts playing."""
from __future__ import annotations

import json
import time

import xbmc

from libtv import generator

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

# How far a file's real duration may differ from the slot it was scheduled
# into before the schedule is treated as wrong and rebuilt.
SLOT_MISMATCH_TOLERANCE = 60

# How often the service loop wakes to notice a stale-schedule flag raised by
# a player callback (the regen timer itself is hours away).
STALE_CHECK_SECONDS = 30

# Minimum spacing between stale-schedule rebuilds, so a channel of items
# with wrong durations rebuilds once rather than on every programme.
STALE_REGEN_MIN_INTERVAL = 300

# Shortest end-to-end playback worth trusting as a measured duration; below
# this a "file" is more likely a failed/aborted start than real content.
MIN_ELAPSED_RUNTIME = 60


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
        # The tune record of the LibTV-resolved PVR stream currently
        # playing, plus the Kodi channel id to re-tune it with; None when
        # the active playback isn't a LibTV channel (ordinary library
        # playback must never re-tune).
        self._tracked = None
        self._last_retune = 0.0
        # (file, start) of the tune already seeked for, so a second
        # onAVStarted for the same playback can't seek twice.
        self._seeked = None
        # Raised when a file's real duration contradicts the slot it was
        # scheduled into; run()'s loop rebuilds promptly instead of leaving
        # a known-wrong guide up for hours.
        self.schedule_stale = False
        # Timing of the current playback, used to measure a file's duration
        # end-to-end rather than asking the player for it (see
        # _record_elapsed_runtime).
        self._timing = None

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

        tune = generator.read_current_tune()
        self._tracked = self._tracked_tune(tune, playing)
        # A tune record stays readable for a couple of minutes, so it only
        # describes *this* playback if the player is playing that very file
        # or Kodi confirms a PVR channel is on (the case where the player
        # names the stream something other than the file behind it).
        # Otherwise an ordinary library video started just after watching a
        # channel would inherit the channel's duration and seek offset.
        if tune and playing != tune.get("file") and self._tracked is None:
            tune = None
        # Record the real duration for whatever just started, regardless of
        # whether it's a join-in-progress seek — this is what lets
        # library.fetch_channels fall back to an observed duration instead
        # of a scraper-provided runtime of 0. Key it by the library path the
        # resolver reported rather than getPlayingFile(), so the entry is
        # under the name library.py will look it up by.
        file_key = (tune or {}).get("file") or playing
        generator.record_observed_runtime(file_key, total)
        self._log_tune(tune, playing, total)
        if tune:
            self._note_slot_mismatch(tune, total)
        self._timing = {
            "file": file_key, "started": time.time(), "offset": 0, "clean": True,
            "slot": int((tune or {}).get("stop") or 0) - int((tune or {}).get("start") or 0),
        }

        offset = self._seek_offset(playing, tune)
        if offset is None:
            return
        # The tune record is not consumed when it is read (onPlayBackEnded
        # still needs it), so remember what has already been seeked rather
        # than seeking again if a second onAVStarted fires for this stream.
        seeked = ((tune or {}).get("file") or playing, (tune or {}).get("start"))
        if self._seeked == seeked:
            return
        self._seeked = seeked
        # Clamp: bad runtime metadata can schedule slots longer than the
        # actual file; never seek past (or into the last 10s of) the real
        # duration.
        target = min(offset, max(total - 10, 0))
        if target > 5:
            xbmc.log(f"LibTV: joining programme in progress at {target}s", xbmc.LOGINFO)
            self._timing["offset"] = target
            self.seekTime(target)

    def onPlayBackEnded(self):
        """Natural EOF only (user stops/channel changes fire
        onPlayBackStopped instead): re-tune the channel so the next
        scheduled programme starts, instead of Kodi dropping to the guide."""
        self._record_elapsed_runtime()
        tracked, self._tracked = self._tracked, None
        if not tracked:
            xbmc.log("LibTV: playback ended outside a LibTV channel, not re-tuning", xbmc.LOGDEBUG)
            return
        libtv_id = tracked["libtv_id"]

        now = time.time()
        if now - self._last_retune < RETUNE_MIN_INTERVAL:
            xbmc.log("LibTV: skipping EOF re-tune (rate limit)", xbmc.LOGWARNING)
            return

        # Give Kodi's own PVR stream-reopen a moment; if it already
        # continued the channel, a second Player.Open would interrupt it.
        xbmc.sleep(RETUNE_GRACE_MS)
        if self.isPlaying():
            return

        # Tell the resolver which programme just finished, so it continues
        # *after* it. Without that it only knows what the schedule claims is
        # on air now — and when a slot outlived its real file (the usual
        # reason a programme ends early) that is still the finished
        # programme itself, which would be resolved again and seeked
        # straight back into the seconds before the end it just reached.
        generator.write_pending_advance(libtv_id, tracked["file"], tracked["start"])

        xbmc.log(f"LibTV: programme ended, re-tuning channel {libtv_id}", xbmc.LOGINFO)
        result = json.loads(xbmc.executeJSONRPC(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "Player.Open",
            "params": {"item": {"channelid": tracked["channelid"]}},
        })))
        if "error" in result:
            xbmc.log(f"LibTV: EOF re-tune failed: {result['error']}", xbmc.LOGERROR)
            # Nothing is going to consume the advance request now; leaving it
            # would skip a programme on whatever the user tunes to next.
            generator.clear_pending_advance()
            return
        self._last_retune = now

    def onPlayBackStopped(self):
        # User-initiated stop or channel change — never re-tune over it, and
        # the playback was cut short so its timing measures nothing.
        self._tracked = None
        self._timing = None

    def onPlayBackError(self):
        self._tracked = None
        self._timing = None

    def onPlayBackPaused(self):
        self._spoil_timing()

    def onPlayBackSeek(self, time_ms, seek_offset):
        self._spoil_timing()

    def onPlayBackSeekChapter(self, chapter):
        self._spoil_timing()

    def _spoil_timing(self):
        """Wall-clock elapsed time stops measuring the file once playback is
        paused or jumped around in."""
        if self._timing:
            self._timing["clean"] = False

    def _record_elapsed_runtime(self):
        """Record how long a file actually took to play end to end.

        `getTotalTime()` is what normally fills the observed-runtime cache,
        but it is the player's *claim* about the stream — for a PVR stream
        it can describe the channel's current EPG entry rather than the file
        behind it, which would quietly confirm the very slot length that is
        wrong. Playing a file from its start to its natural end measures the
        duration directly, with nothing to misreport, so that measurement
        wins when it is available.

        Only counted when playback started at the beginning (an offset would
        have come from a duration that may itself be wrong) and ran
        uninterrupted (no pause or seek — wall-clock time stops matching
        playback time otherwise).
        """
        timing, self._timing = self._timing, None
        if not timing or not timing["clean"] or timing["offset"]:
            return
        elapsed = int(time.time() - timing["started"])
        if elapsed < MIN_ELAPSED_RUNTIME:
            return
        generator.record_observed_runtime(timing["file"], elapsed)
        slot = timing.get("slot") or 0
        if slot and abs(slot - elapsed) > SLOT_MISMATCH_TOLERANCE:
            self.schedule_stale = True
            xbmc.log(
                f"LibTV: {timing['file']} played for {elapsed}s in a {slot}s slot — "
                "flagging the schedule for a rebuild",
                xbmc.LOGWARNING,
            )

    def _property(self, name):
        """A property of the ListItem currently playing, or "" — this is how
        the resolver's own values reach the service (Kodi's Player core
        retains a resolved item's properties for as long as it plays)."""
        try:
            item = self.getPlayingItem()
        except RuntimeError:
            return ""
        return item.getProperty(name) if item else ""

    def _note_slot_mismatch(self, tune, total):
        """Flag the schedule for a rebuild when the file that just started
        doesn't fit the slot it was scheduled into.

        This is the moment LibTV learns a real duration it previously had to
        guess at (no runtime metadata, or a scraped value that overstates
        the file). Waiting for the next scheduled rebuild would leave a
        known-wrong guide up for hours and make every one of this item's
        slots end early, so run() rebuilds promptly instead.
        """
        slot = int((tune.get("stop") or 0) - (tune.get("start") or 0))
        if slot <= 0 or total <= 0 or abs(slot - int(total)) <= SLOT_MISMATCH_TOLERANCE:
            return
        self.schedule_stale = True
        xbmc.log(
            f"LibTV: {tune.get('file')} runs {int(total)}s but was scheduled a {slot}s "
            "slot — flagging the schedule for a rebuild",
            xbmc.LOGWARNING,
        )

    def _log_tune(self, tune, playing_file, total):
        """One line per tune describing everything the hand-off depends on.

        Live diagnosis of this add-on costs a full deploy round trip, so the
        values that decide whether the seek, the duration cache and the EOF
        re-tune work are logged together: the resolver's record, what the
        player reports it is playing (not guaranteed to be the same string
        for a PVR stream) and whether the resolved ListItem's properties
        survived into playback.
        """
        if not tune:
            xbmc.log(
                f"LibTV: playback started with no LibTV tune record (file={playing_file})",
                xbmc.LOGDEBUG,
            )
            return
        slot = int((tune.get("stop") or 0) - (tune.get("start") or 0))
        xbmc.log(
            "LibTV: tune channel={} file={} playing_file={} slot={}s measured={}s "
            "offset={}s properties={}".format(
                tune.get("channel_id"), tune.get("file"), playing_file, slot, int(total),
                tune.get("offset"),
                "yes" if self._property("libtv_channel_id") else "no",
            ),
            xbmc.LOGINFO,
        )

    def _tracked_tune(self, tune, playing_file):
        """What onPlayBackEnded needs to re-tune this channel, or None when
        the active playback isn't a LibTV channel.

        The LibTV channel comes from the resolver's tune record (a file, so
        it doesn't depend on ListItem properties surviving into PVR
        playback), falling back to the "libtv_channel_id" property if there
        is no fresh record. The Kodi-side channel id that Player.Open needs
        comes from _kodi_channel_id.
        """
        libtv_id = (tune or {}).get("channel_id") or self._property("libtv_channel_id")
        if not libtv_id:
            return None
        # The name fallback is only safe when the player is playing the very
        # file this tune record describes. Otherwise a record left over from
        # a channel watched moments ago could attach a channel to ordinary
        # library playback — and re-tune Live TV over it when that ends.
        same_file = bool(tune) and playing_file == tune.get("file")
        channelid = self._kodi_channel_id((tune or {}).get("channel_name") if same_file else "")
        if channelid is None:
            xbmc.log(
                f"LibTV: playing {libtv_id} but Kodi reports no PVR channel for it — "
                "end-of-programme continuation is unavailable for this tune",
                xbmc.LOGWARNING,
            )
            return None
        return {
            "libtv_id": libtv_id,
            "channelid": channelid,
            "file": (tune or {}).get("file") or playing_file,
            "start": (tune or {}).get("start"),
        }

    def _kodi_channel_id(self, channel_name):
        """The Kodi PVR channel id of the channel being played, or None.

        Normally Player.GetItem reports it directly. When it doesn't (the
        player may describe the resolved file rather than the channel), fall
        back to matching the channel's display name against the PVR
        channel list — LibTV writes those names into the M3U itself, so they
        are the one label both sides agree on.
        """
        players = self._json_rpc("Player.GetActivePlayers", {}) or []
        for player in players:
            if player.get("type") != "video":
                continue
            result = self._json_rpc("Player.GetItem", {"playerid": player["playerid"]}) or {}
            got = result.get("item") or {}
            if got.get("type") == "channel" and got.get("id") is not None:
                return got["id"]
        if not channel_name:
            return None
        channels = (self._json_rpc(
            "PVR.GetChannels", {"channelgroupid": "alltv"}
        ) or {}).get("channels") or []
        for channel in channels:
            if channel.get("label") == channel_name and channel.get("channelid") is not None:
                return channel["channelid"]
        return None

    @staticmethod
    def _json_rpc(method, params):
        """Bare JSON-RPC call returning `result`, or None on error.

        library.json_rpc() is not reused here: it returns {} for both "no
        result" and "call failed", and these callers need to tell those
        apart to pick a fallback.
        """
        response = json.loads(xbmc.executeJSONRPC(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
        })))
        if "error" in response:
            xbmc.log(f"LibTV: JSON-RPC {method} failed: {response['error']}", xbmc.LOGWARNING)
            return None
        return response.get("result")

    def _seek_offset(self, playing_file, tune=None):
        """The resolver's chosen join-in-progress offset for playing_file,
        or None if there is nothing to seek to.

        Prefers the "libtv_seek_offset" property the resolver set directly
        on the resolved ListItem (plugin.py's play()) — Kodi's Player core
        retains a resolved item's own properties for as long as it's
        playing, independent of the resolver script (which has already
        exited by the time this fires) and with no rapid-zap ambiguity
        (this is *the* item currently playing, not a guess from a filename
        match). Then the tune record, which carries the same offset and
        needs no filename match — the one path that still works if the
        player reports a PVR stream under something other than the library
        path. The pending-seek file (which does match on filename) remains
        as a last fallback until the property path is live-verified across
        PVR channel changes (see docs/live-testing.md).
        """
        raw = self._property("libtv_seek_offset")
        if raw:
            generator.clear_pending_seek()
            try:
                return int(raw)
            except ValueError:
                pass

        if tune and tune.get("offset"):
            generator.clear_pending_seek()
            return int(tune["offset"])

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
    last_stale_regen = 0.0
    while not monitor.abortRequested():
        wait = PVR_RETRY_SECONDS if pending_pvr_refresh else max(1, next_regen - time.time())
        # Never sleep through a stale-schedule flag: it is raised from a
        # player callback (a file whose real duration contradicts the guide)
        # and the regen timer is hours away.
        if monitor.waitForAbort(min(wait, STALE_CHECK_SECONDS)):
            break
        if time.time() >= next_regen:
            pending_pvr_refresh = _regenerate_safely()
            next_regen = time.time() + generator.regen_interval_seconds()
            player.schedule_stale = False
            last_stale_regen = time.time()
        elif (player.schedule_stale
                and time.time() - last_stale_regen >= STALE_REGEN_MIN_INTERVAL):
            xbmc.log(
                "LibTV: rebuilding — a measured duration contradicted the schedule",
                xbmc.LOGINFO,
            )
            player.schedule_stale = False
            last_stale_regen = time.time()
            pending_pvr_refresh = _regenerate_safely()
        elif pending_pvr_refresh:
            # Guide/M3U are already fresh; only the IPTV Simple reload was
            # skipped last time, so just retry the toggle, not a full regen.
            pending_pvr_refresh = not generator.refresh_pvr()
    del player
