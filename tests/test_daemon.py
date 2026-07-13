"""Tests for the service-side join-in-progress seek (daemon.py)."""

import time

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
