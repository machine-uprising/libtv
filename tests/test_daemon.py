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


def _start_tracked_playback(monkeypatch, playing_file="/media/a.mkv", channelid=7):
    """Run onAVStarted with the full PVR-channel context the resolver and
    Kodi would provide: a resolved ListItem carrying libtv_channel_id, and
    JSON-RPC reporting a video player whose item is a PVR channel."""
    li = _resolved_listitem(playing_file)
    li.setProperty("libtv_channel_id", "ch1")
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "Player.GetActivePlayers",
                        [{"playerid": 1, "type": "video"}])
    monkeypatch.setitem(conftest.JSONRPC_RESPONSES, "Player.GetItem",
                        {"item": {"type": "channel", "id": channelid}})
    conftest.PLAYER.update(playing=True, file=playing_file, time=0.0, total=480.0)
    player = daemon.JoinInProgressPlayer()
    player.onAVStarted()
    return player


def test_onplaybackended_retunes_to_next_programme(monkeypatch):
    player = _start_tracked_playback(monkeypatch)
    _write_schedule("/media/b.mkv")  # schedule has moved on to the next file
    conftest.PLAYER.update(playing=False)

    player.onPlayBackEnded()

    assert _retune_calls() == [{"item": {"channelid": 7}}]


def test_onplaybackended_ignores_untracked_playback(monkeypatch):
    """Ordinary library playback (no PVR channel context) ending must never
    re-tune anything."""
    conftest.PLAYER.update(playing=True, file="/media/a.mkv", time=0.0, total=480.0)
    player = daemon.JoinInProgressPlayer()
    player.onAVStarted()
    conftest.PLAYER.update(playing=False)

    player.onPlayBackEnded()

    assert _retune_calls() == []


def test_onplaybackended_skips_when_same_file_still_on_air(monkeypatch):
    """Loop guard: a slot longer than its real file means the ended file is
    still 'on air' — re-tuning would resolve the same file, seek near its
    end, and end again, forever. Accept the drop-to-guide instead."""
    player = _start_tracked_playback(monkeypatch)
    _write_schedule("/media/a.mkv")  # the file that just ended
    conftest.PLAYER.update(playing=False)

    player.onPlayBackEnded()

    assert _retune_calls() == []


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


def test_regenerate_safely_survives_generation_errors(monkeypatch):
    from libtv import generator

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(generator, "regenerate", _boom)

    assert daemon._regenerate_safely() is False
