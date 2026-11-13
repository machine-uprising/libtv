"""Tests for the service-side join-in-progress seek (daemon.py)."""

import time

import xbmcgui
from libtv import daemon, generator

from tests import conftest


def _seek_calls():
    return [c for c in conftest.CALLS if c[0] == "xbmc.Player.seekTime"]


def test_onavstarted_seeks_and_consumes_pending():
    generator.write_pending_seek("/media/a.mkv", 1800)
    conftest.PLAYER.update(playing=True, file="/media/a.mkv", time=0.0, total=6000.0)

    daemon.JoinInProgressPlayer().onAVStarted()

    assert _seek_calls() == [("xbmc.Player.seekTime", 1800)]
    assert generator.read_pending_seek() is None, "pending seek must be consumed"


def test_onavstarted_clamps_to_file_length():
    # Scheduled slot says 5000s in, but the file is only 3000s long.
    generator.write_pending_seek("/media/a.mkv", 5000)
    conftest.PLAYER.update(playing=True, file="/media/a.mkv", time=0.0, total=3000.0)

    daemon.JoinInProgressPlayer().onAVStarted()

    assert _seek_calls() == [("xbmc.Player.seekTime", 3000.0 - 10)]


def test_onavstarted_leaves_pending_for_other_stream():
    """A different file starting (e.g. mid rapid zap) must not consume or act
    on a pending seek that belongs to another stream."""
    generator.write_pending_seek("/media/a.mkv", 1800)
    conftest.PLAYER.update(playing=True, file="/media/other.mkv", time=0.0, total=6000.0)

    daemon.JoinInProgressPlayer().onAVStarted()

    assert _seek_calls() == []
    assert generator.read_pending_seek() is not None, "pending seek must survive"


def test_onavstarted_discards_stale_pending(monkeypatch):
    generator.write_pending_seek("/media/a.mkv", 1800)
    conftest.PLAYER.update(playing=True, file="/media/a.mkv", time=0.0, total=6000.0)
    real_now = time.time()
    monkeypatch.setattr(time, "time", lambda: real_now + generator.PENDING_SEEK_MAX_AGE + 1)

    daemon.JoinInProgressPlayer().onAVStarted()

    assert _seek_calls() == []
    monkeypatch.undo()
    assert generator.read_pending_seek() is None, "stale pending must be deleted"


def test_onavstarted_without_pending_is_a_noop():
    conftest.PLAYER.update(playing=True, file="/media/a.mkv", time=0.0, total=6000.0)

    daemon.JoinInProgressPlayer().onAVStarted()

    assert _seek_calls() == []


def test_onavstarted_records_observed_runtime_regardless_of_pending_seek():
    conftest.PLAYER.update(playing=True, file="/media/a.mkv", time=0.0, total=4321.0)

    daemon.JoinInProgressPlayer().onAVStarted()

    assert generator.load_runtime_cache() == {"/media/a.mkv": 4321}


def _resolved_listitem(path, offset=None):
    """Simulate what plugin.py's play() hands to setResolvedUrl."""
    li = xbmcgui.ListItem(path=path)
    if offset is not None:
        li.setProperty("libtv_seek_offset", str(offset))
    conftest._CURRENT_LISTITEM = li
    return li


def test_onavstarted_seeks_from_listitem_property_alone():
    """The property path must work with no pending-seek file at all — this
    is what makes it safe to eventually drop the file fallback."""
    _resolved_listitem("/media/a.mkv", offset=1800)
    conftest.PLAYER.update(playing=True, file="/media/a.mkv", time=0.0, total=6000.0)

    daemon.JoinInProgressPlayer().onAVStarted()

    assert _seek_calls() == [("xbmc.Player.seekTime", 1800)]


def test_onavstarted_prefers_listitem_property_over_pending_file():
    """If both are present (the normal, hedged case), the property wins and
    the pending file is cleared as part of using it."""
    generator.write_pending_seek("/media/a.mkv", 999)  # would seek wrong if used
    _resolved_listitem("/media/a.mkv", offset=1800)
    conftest.PLAYER.update(playing=True, file="/media/a.mkv", time=0.0, total=6000.0)

    daemon.JoinInProgressPlayer().onAVStarted()

    assert _seek_calls() == [("xbmc.Player.seekTime", 1800)]
    assert generator.read_pending_seek() is None


def test_onavstarted_falls_back_to_pending_file_without_property():
    """A resolved item with no libtv_seek_offset property (e.g. a pathway
    where it doesn't survive to PVR playback) must still fall back to the
    pending-seek file rather than silently not seeking."""
    generator.write_pending_seek("/media/a.mkv", 1800)
    _resolved_listitem("/media/a.mkv")  # no offset property set
    conftest.PLAYER.update(playing=True, file="/media/a.mkv", time=0.0, total=6000.0)

    daemon.JoinInProgressPlayer().onAVStarted()

    assert _seek_calls() == [("xbmc.Player.seekTime", 1800)]


def test_onavstarted_skips_recording_when_file_changes_mid_start():
    """getTotalTime() can lag a channel change: right after onAVStarted the
    old item's duration can be reported under the new item's filename. If
    the playing file changes between the two reads, nothing may be recorded
    — a poisoned cache entry would outrank scraped runtimes forever."""
    conftest.PLAYER.update(
        playing=True, time=0.0, total=6000.0,
        file_queue=["/media/a.mkv", "/media/b.mkv"],
    )

    daemon.JoinInProgressPlayer().onAVStarted()

    assert generator.load_runtime_cache() == {}
    assert _seek_calls() == []


def test_onavstarted_records_second_total_time_read():
    """When the file is stable across the settle delay, the second
    getTotalTime() read wins — it's the one taken after the player had time
    to probe the real duration."""
    conftest.PLAYER.update(
        playing=True, file="/media/a.mkv", time=0.0, total=0.0,
        total_queue=[6000.0, 480.0],
    )

    daemon.JoinInProgressPlayer().onAVStarted()

    assert generator.load_runtime_cache() == {"/media/a.mkv": 480}


def _playing_a_pvr_channel(monkeypatch, channelid=7):
    """Kodi reporting that a PVR channel is what's playing — what makes a
    stream the player names something other than the library file (a
    `pvr://` path, say) still attributable to the resolver's tune record."""
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "Player.GetActivePlayers",
                        [{"playerid": 1, "type": "video"}])
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "Player.GetItem",
                        {"item": {"type": "channel", "id": channelid}})


def test_onavstarted_records_duration_under_the_library_path(monkeypatch):
    """The cache is read back keyed by the library's `file`, so it must be
    written under the path the resolver reported — Kodi is free to name a
    PVR stream something else entirely, and an entry under that name could
    never be looked up again."""
    generator.write_current_tune(
        "ch1", "Channel One",
        {"file": "/media/a.mkv", "start": 1000.0, "stop": 1480.0}, 0,
    )
    _playing_a_pvr_channel(monkeypatch)
    conftest.PLAYER.update(playing=True, file="pvr://channels/tv/1.pvr",
                           time=0.0, total=480.0)

    daemon.JoinInProgressPlayer().onAVStarted()

    assert generator.load_runtime_cache() == {"/media/a.mkv": 480}


def test_onavstarted_ignores_a_tune_record_for_other_playback(monkeypatch):
    """Starting an ordinary library video shortly after watching a channel
    must not inherit that channel's duration or seek offset."""
    generator.write_current_tune(
        "ch1", "Channel One",
        {"file": "/media/a.mkv", "start": 1000.0, "stop": 1000.0 + 5400}, 1800,
    )
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "Player.GetActivePlayers",
                        [{"playerid": 1, "type": "video"}])
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "Player.GetItem",
                        {"item": {"type": "movie", "id": 4}})
    conftest._CURRENT_LISTITEM = None
    conftest.PLAYER.update(playing=True, file="/media/unrelated.mkv", time=0.0, total=600.0)

    player = daemon.JoinInProgressPlayer()
    player.onAVStarted()

    assert generator.load_runtime_cache() == {"/media/unrelated.mkv": 600}
    assert _seek_calls() == []
    assert player.schedule_stale is False


def test_onavstarted_flags_the_schedule_when_the_file_contradicts_its_slot():
    """Learning that a 90-minute slot holds an 8-minute file is the moment
    the guide is known to be wrong — the service rebuilds on this flag
    rather than leaving it up until the next scheduled rebuild."""
    generator.write_current_tune(
        "ch1", "Channel One",
        {"file": "/media/a.mkv", "start": 1000.0, "stop": 1000.0 + 5400}, 0,
    )
    conftest.PLAYER.update(playing=True, file="/media/a.mkv", time=0.0, total=480.0)

    player = daemon.JoinInProgressPlayer()
    player.onAVStarted()

    assert player.schedule_stale is True


def test_onavstarted_does_not_flag_a_schedule_that_matches():
    generator.write_current_tune(
        "ch1", "Channel One",
        {"file": "/media/a.mkv", "start": 1000.0, "stop": 1000.0 + 480}, 0,
    )
    conftest.PLAYER.update(playing=True, file="/media/a.mkv", time=0.0, total=470.0)

    player = daemon.JoinInProgressPlayer()
    player.onAVStarted()

    assert player.schedule_stale is False


def test_onavstarted_seeks_from_the_tune_record_without_a_filename_match(monkeypatch):
    """Neither the ListItem property nor the pending-seek file is usable if
    the player names the stream differently from the library file — the tune
    record needs no filename match, so the seek still happens."""
    generator.write_current_tune(
        "ch1", "Channel One",
        {"file": "/media/a.mkv", "start": 1000.0, "stop": 1000.0 + 6000}, 1800,
    )
    _playing_a_pvr_channel(monkeypatch)
    conftest._CURRENT_LISTITEM = None
    conftest.PLAYER.update(playing=True, file="pvr://channels/tv/1.pvr",
                           time=0.0, total=6000.0)

    daemon.JoinInProgressPlayer().onAVStarted()

    assert _seek_calls() == [("xbmc.Player.seekTime", 1800)]


def test_onavstarted_seeks_only_once_per_tune():
    """The tune record isn't consumed when read (EOF handling still needs
    it), so a repeated callback for the same playback must not re-seek."""
    generator.write_current_tune(
        "ch1", "Channel One",
        {"file": "/media/a.mkv", "start": 1000.0, "stop": 1000.0 + 6000}, 1800,
    )
    conftest._CURRENT_LISTITEM = None
    conftest.PLAYER.update(playing=True, file="/media/a.mkv", time=0.0, total=6000.0)

    player = daemon.JoinInProgressPlayer()
    player.onAVStarted()
    player.onAVStarted()

    assert _seek_calls() == [("xbmc.Player.seekTime", 1800)]


def test_kodi_channel_id_falls_back_to_matching_the_channel_name(monkeypatch):
    """Player.GetItem may describe the resolved file rather than the PVR
    channel; the channel's display name is the one label LibTV and Kodi
    both know, since LibTV writes it into the M3U."""
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "Player.GetActivePlayers",
                        [{"playerid": 1, "type": "video"}])
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "Player.GetItem",
                        {"item": {"type": "movie", "id": 4}})
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "PVR.GetChannels",
                        {"channels": [{"channelid": 12, "label": "Channel Two"},
                                      {"channelid": 13, "label": "Channel One"}]})
    conftest.PLAYER.update(playing=True, file="/media/a.mkv", time=0.0, total=480.0)

    player = daemon.JoinInProgressPlayer()

    assert player._kodi_channel_id("Channel One") == 13
    assert player._kodi_channel_id("No Such Channel") is None


def _play_through(monkeypatch, seconds, playing_file="/media/a.mkv", total=5400.0,
                  slot_stop=None, pause=False, seek=False, stop=False):
    """Play a file from its start to its natural end, `seconds` of wall
    clock apart, and return the player."""
    start = 1000.0
    generator.write_current_tune(
        "ch1", "Channel One",
        {"file": playing_file, "start": start, "stop": slot_stop or start + 5400}, 0,
    )
    _playing_a_pvr_channel(monkeypatch)
    conftest._CURRENT_LISTITEM = None
    conftest.PLAYER.update(playing=True, file=playing_file, time=0.0, total=total)

    now = [time.time()]
    monkeypatch.setattr(time, "time", lambda: now[0])
    player = daemon.JoinInProgressPlayer()
    player.onAVStarted()
    if pause:
        player.onPlayBackPaused()
    if seek:
        player.onPlayBackSeek(10000, 10000)
    now[0] += seconds
    conftest.PLAYER.update(playing=False)
    if stop:
        player.onPlayBackStopped()
    player.onPlayBackEnded()
    return player


def test_playing_a_file_through_measures_its_real_duration(monkeypatch):
    """getTotalTime() is the player's *claim* about the stream — for a PVR
    stream it can describe the channel's EPG entry rather than the file.
    Playing start to finish measures the duration with nothing to
    misreport, so that measurement wins."""
    player = _play_through(monkeypatch, 480)

    assert generator.load_runtime_cache() == {"/media/a.mkv": 480}
    assert player.schedule_stale is True, "an 8-minute file in a 90-minute slot"


def test_a_paused_or_seeked_playthrough_measures_nothing(monkeypatch):
    """Wall-clock elapsed time stops matching playback time."""
    _play_through(monkeypatch, 480, pause=True)
    assert generator.load_runtime_cache() == {"/media/a.mkv": 5400}

    conftest.PLAYER.update(playing=True)
    _play_through(monkeypatch, 480, seek=True)
    assert generator.load_runtime_cache() == {"/media/a.mkv": 5400}


def test_a_stopped_playthrough_measures_nothing(monkeypatch):
    """Playback cut short says nothing about how long the file is."""
    _play_through(monkeypatch, 480, stop=True)

    assert generator.load_runtime_cache() == {"/media/a.mkv": 5400}


def test_a_joined_in_progress_playthrough_measures_nothing(monkeypatch):
    """Joining partway in means the start offset came from a duration that
    may itself be wrong, so elapsed time can't be trusted to complete it."""
    generator.write_current_tune(
        "ch1", "Channel One",
        {"file": "/media/a.mkv", "start": 1000.0, "stop": 1000.0 + 5400}, 1800,
    )
    _playing_a_pvr_channel(monkeypatch)
    conftest._CURRENT_LISTITEM = None
    conftest.PLAYER.update(playing=True, file="/media/a.mkv", time=0.0, total=5400.0)
    now = [time.time()]
    monkeypatch.setattr(time, "time", lambda: now[0])

    player = daemon.JoinInProgressPlayer()
    player.onAVStarted()
    now[0] += 480
    conftest.PLAYER.update(playing=False)
    player.onPlayBackEnded()

    assert generator.load_runtime_cache() == {"/media/a.mkv": 5400}


def _retune_calls():
    return [c[2] for c in conftest.CALLS
            if c[0] == "xbmc.executeJSONRPC" and c[1] == "Player.Open"]


def _write_schedule(current_file, channel_id="ch1"):
    """Persist a minimal schedule whose channel is airing current_file now."""
    import json

    now = time.time()
    data = {"anchor": 0, "channels": [{
        "id": channel_id, "name": "Ch", "group": "", "logo": "",
        "programmes": [{"start": now - 60, "stop": now + 600,
                        "title": "P", "file": current_file}],
    }]}
    with open(generator.schedule_path(), "w", encoding="utf-8") as f:
        json.dump(data, f)


def _tune(file="/media/a.mkv", start=None, stop=None, offset=0, channel_id="ch1"):
    """Write the tune record plugin.play() leaves for the service."""
    start = time.time() if start is None else start
    stop = start + 480 if stop is None else stop
    generator.write_current_tune(
        channel_id, "Channel One",
        {"file": file, "start": start, "stop": stop}, offset,
    )


def _start_tracked_playback(monkeypatch, playing_file="/media/a.mkv", channelid=7,
                            tune_file=None):
    """Run onAVStarted with the full PVR-channel context the resolver and
    Kodi would provide: the resolver's tune record on disk, and JSON-RPC
    reporting a video player whose item is a PVR channel."""
    _resolved_listitem(playing_file)
    _tune(file=tune_file or playing_file)
    _playing_a_pvr_channel(monkeypatch, channelid)
    conftest.PLAYER.update(playing=True, file=playing_file, time=0.0, total=480.0)
    player = daemon.JoinInProgressPlayer()
    player.onAVStarted()
    return player


def test_onplaybackended_retunes_and_reports_what_finished(monkeypatch):
    """The resolver is told which programme ended, not just to re-tune: the
    schedule on its own still shows a finished programme on air whenever its
    slot outlived the real file."""
    player = _start_tracked_playback(monkeypatch)
    _write_schedule("/media/b.mkv")
    conftest.PLAYER.update(playing=False)

    player.onPlayBackEnded()

    assert _retune_calls() == [{"item": {"channelid": 7}}]
    advance = generator.read_pending_advance()
    assert (advance["channel_id"], advance["file"]) == ("ch1", "/media/a.mkv")


def test_onplaybackended_tracks_channel_without_listitem_properties(monkeypatch):
    """The tune record is a file, so EOF continuation must work even if the
    resolved ListItem's properties never reach PVR playback (the mechanism
    CLAUDE.md flags as unverified) — and even if the player reports the
    stream under something other than the library path."""
    conftest._CURRENT_LISTITEM = None
    player = _start_tracked_playback(
        monkeypatch, playing_file="pvr://channels/tv/1.pvr", tune_file="/media/a.mkv"
    )
    _write_schedule("/media/b.mkv")
    conftest.PLAYER.update(playing=False)

    player.onPlayBackEnded()

    assert _retune_calls() == [{"item": {"channelid": 7}}]


def test_onplaybackended_ignores_library_playback_after_a_recent_tune(monkeypatch):
    """A tune record stays readable for a couple of minutes. Starting an
    ordinary library video in that window must not inherit the channel and
    get re-tuned over when it ends."""
    _tune(file="/media/a.mkv")
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "Player.GetActivePlayers",
                        [{"playerid": 1, "type": "video"}])
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "Player.GetItem",
                        {"item": {"type": "movie", "id": 4}})
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "PVR.GetChannels",
                        {"channels": [{"channelid": 13, "label": "Channel One"}]})
    conftest.PLAYER.update(playing=True, file="/media/unrelated.mkv", time=0.0, total=600.0)
    player = daemon.JoinInProgressPlayer()
    player.onAVStarted()
    _write_schedule("/media/b.mkv")
    conftest.PLAYER.update(playing=False)

    player.onPlayBackEnded()

    assert _retune_calls() == []


def test_onplaybackended_ignores_untracked_playback(monkeypatch):
    """Ordinary library playback (no PVR channel context) ending must never
    re-tune anything."""
    conftest.PLAYER.update(playing=True, file="/media/a.mkv", time=0.0, total=480.0)
    player = daemon.JoinInProgressPlayer()
    player.onAVStarted()
    conftest.PLAYER.update(playing=False)

    player.onPlayBackEnded()

    assert _retune_calls() == []


def test_onplaybackended_drops_the_advance_request_if_the_retune_fails(monkeypatch):
    """Nothing will consume it, and a request left lying around would skip a
    programme on whatever the user tunes to next."""
    player = _start_tracked_playback(monkeypatch)
    _write_schedule("/media/a.mkv")
    conftest.PLAYER.update(playing=False)
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "Player.Open",
                        {"error": {"code": -32100, "message": "nope"}})

    player.onPlayBackEnded()

    assert generator.read_pending_advance() is None


def test_onplaybackended_skips_when_already_playing_again(monkeypatch):
    """If Kodi's own PVR stream-reopen already continued the channel, a
    second Player.Open would interrupt it."""
    player = _start_tracked_playback(monkeypatch)
    _write_schedule("/media/b.mkv")
    conftest.PLAYER.update(playing=True)  # something is playing again

    player.onPlayBackEnded()

    assert _retune_calls() == []


def test_onplaybackended_rate_limited(monkeypatch):
    player = _start_tracked_playback(monkeypatch)
    _write_schedule("/media/b.mkv")
    conftest.PLAYER.update(playing=False)
    player._last_retune = time.time()  # a re-tune just happened

    player.onPlayBackEnded()

    assert _retune_calls() == []


def test_onplaybackstopped_clears_tracking(monkeypatch):
    """A user stop / channel change must clear the tracked tune so a later
    stray onPlayBackEnded can't re-tune a channel nobody is watching."""
    player = _start_tracked_playback(monkeypatch)
    _write_schedule("/media/b.mkv")
    conftest.PLAYER.update(playing=False)

    player.onPlayBackStopped()
    player.onPlayBackEnded()

    assert _retune_calls() == []


def _toggle_calls():
    return [c[2]["enabled"] for c in conftest.CALLS
            if c[0] == "xbmc.executeJSONRPC" and c[1] == "Addons.SetAddonEnabled"]


def _with_iptv_simple(monkeypatch, enabled=True):
    monkeypatch.setitem(
        conftest.JSONRPC_RESPONSES,
        "Addons.GetAddonDetails",
        {"addon": {"addonid": "pvr.iptvsimple", "enabled": enabled}},
    )


def test_regenerate_safely_reports_no_retry_needed_when_refreshed(monkeypatch):
    _with_iptv_simple(monkeypatch)

    assert daemon._regenerate_safely() is False
    assert _toggle_calls() == [False, True]


def test_regenerate_safely_reports_retry_needed_during_playback(monkeypatch):
    _with_iptv_simple(monkeypatch)
    conftest.PLAYER.update(playing=True, file="/media/a.mkv")

    assert daemon._regenerate_safely() is True
    assert _toggle_calls() == []


def test_regenerate_safely_reports_no_retry_when_client_missing(monkeypatch):
    # No Addons.GetAddonDetails response = client not installed — nothing to
    # usefully retry.
    assert daemon._regenerate_safely() is False
    assert _toggle_calls() == []


def test_service_rebuilds_promptly_when_a_duration_contradicts_the_schedule(monkeypatch):
    """A player callback can learn mid-afternoon that the guide is wrong.
    The loop must not sleep until the next scheduled rebuild (hours away),
    and must not then rebuild again for every following programme."""
    import xbmc
    from libtv import generator

    created = []

    class _FlaggingPlayer(daemon.JoinInProgressPlayer):
        def __init__(self):
            super().__init__()
            created.append(self)

    class _Monitor:
        def __init__(self):
            self.waits = []

        def abortRequested(self):
            return False

        def waitForAbort(self, timeout=0):
            self.waits.append(timeout)
            created[0].schedule_stale = True  # keep asking for a rebuild
            return len(self.waits) > 3

    monitor = _Monitor()
    regens = []
    monkeypatch.setattr(daemon, "JoinInProgressPlayer", _FlaggingPlayer)
    monkeypatch.setattr(xbmc, "Monitor", lambda: monitor)
    monkeypatch.setattr(generator, "regenerate", lambda: regens.append(1))

    daemon.run()

    assert len(regens) == 2, "one rebuild at startup, one for the stale flag"
    assert max(monitor.waits) <= daemon.STALE_CHECK_SECONDS, \
        "the loop must wake often enough to notice the flag"


def test_regenerate_safely_survives_generation_errors(monkeypatch):
    from libtv import generator

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(generator, "regenerate", _boom)

    assert daemon._regenerate_safely() is False
