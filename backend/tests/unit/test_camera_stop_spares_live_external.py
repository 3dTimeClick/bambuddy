"""/camera/stop ends the leaving viewer's external streams, not the others'.

The stop guard counted only fan-out subscribers, which external cameras never
use: one viewer leaving (a Cam Wall tile turning to snapshot, the Camera page
closed, the camera window's Refresh) killed every external RTSP/USB ffmpeg of
the printer, and the other viewers froze. A viewer now names itself (a random
id per mount, sent with its stream and its stop): its own streams end at once,
also one whose cleanup was skipped (#2675), and so does any stream that sends no
frames; other viewers' live streams keep running. A stop without an id (a
cached older frontend) still ends them all.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from backend.app.api.routes import camera

PRINTER = 7


class _Proc:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = None
        self.stdout = None
        self.stderr = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


@pytest.fixture
def no_fanout(monkeypatch):
    monkeypatch.setattr(camera, "_FFMPEG_KILL_TIMEOUT", 0.05)
    monkeypatch.setattr(camera, "get_subscriber_count", lambda _key: 0)

    async def no_broadcaster(_key):
        return False

    monkeypatch.setattr(camera, "shutdown_broadcaster", no_broadcaster)


@pytest.fixture
def register():
    added: list[tuple[str, _Proc]] = []

    def _register(suffix: str, pid: int, viewer: str | None, last_frame_age: float | None = 0.5):
        sid = f"{PRINTER}-ext-{suffix}"
        proc = _Proc(pid)
        event = asyncio.Event()
        camera._active_streams[sid] = proc
        camera._disconnect_events[sid] = event
        camera._spawned_ffmpeg_pids[pid] = time.time()
        if viewer is not None:
            camera._stream_viewers[sid] = viewer
        if last_frame_age is not None:
            camera._stream_last_frame_times[sid] = time.time() - last_frame_age
        added.append((sid, proc))
        return proc, event

    yield _register
    for sid, proc in added:
        camera._active_streams.pop(sid, None)
        camera._disconnect_events.pop(sid, None)
        camera._stream_last_frame_times.pop(sid, None)
        camera._stream_viewers.pop(sid, None)
        camera._spawned_ffmpeg_pids.pop(proc.pid, None)


@pytest.mark.asyncio
async def test_a_viewer_leaving_ends_its_own_stream_at_once(no_fanout, register):
    """#2675: its cleanup may have been skipped, and the stream still looks live."""
    own, own_event = register("own00001", 54001, viewer="tile-a", last_frame_age=0.1)

    result = await camera.stop_camera_stream(PRINTER, _=None, viewer="tile-a")

    assert result["stopped"] == 1
    assert own.returncode is not None and own_event.is_set()
    assert f"{PRINTER}-ext-own00001" not in camera._stream_viewers


@pytest.mark.asyncio
async def test_another_viewers_live_stream_keeps_running(no_fanout, register):
    own, _ = register("own00002", 54002, viewer="tile-a")
    other, other_event = register("other002", 54003, viewer="page-b")

    result = await camera.stop_camera_stream(PRINTER, _=None, viewer="tile-a")

    assert result["stopped"] == 1
    assert own.returncode is not None
    assert other.returncode is None and not other_event.is_set()
    assert f"{PRINTER}-ext-other002" in camera._active_streams


@pytest.mark.asyncio
async def test_a_stream_sending_no_frames_ends_whoever_opened_it(no_fanout, register):
    stuck, _ = register("stuck003", 54004, viewer="page-b", last_frame_age=60.0)
    never, _ = register("never003", 54005, viewer=None, last_frame_age=None)
    live, _ = register("live0003", 54006, viewer=None)

    result = await camera.stop_camera_stream(PRINTER, _=None, viewer="tile-a")

    assert result["stopped"] == 2
    assert stuck.returncode is not None and never.returncode is not None
    assert live.returncode is None


@pytest.mark.asyncio
async def test_a_stop_without_a_viewer_still_ends_them_all(no_fanout, register):
    """A cached older frontend sends no id."""
    a, _ = register("all00004", 54007, viewer="tile-a")
    b, _ = register("all00005", 54008, viewer="page-b")

    result = await camera.stop_camera_stream(PRINTER, _=None)

    assert result["stopped"] == 2
    assert a.returncode is not None and b.returncode is not None


@pytest.mark.asyncio
async def test_a_deleted_printer_loses_every_stream(no_fanout, register):
    live, live_event = register("live0006", 54009, viewer="page-b")

    await camera.forget_printer(PRINTER)

    assert live.returncode is not None and live_event.is_set()
    assert f"{PRINTER}-ext-live0006" not in camera._stream_viewers


@pytest.mark.asyncio
async def test_the_janitor_drops_the_viewer_of_a_stream_it_kills(no_fanout, register, monkeypatch):
    """Orphaned by a skipped cleanup and its viewer's stop never came (#2675)."""
    monkeypatch.setattr(camera, "_scan_bambu_ffmpeg_pids", lambda: [])
    # A real pid, so the janitor's existence check keeps it.
    stale, stale_event = register("stale007", os.getpid(), viewer="tile-a", last_frame_age=60.0)
    camera._spawned_ffmpeg_pids[stale.pid] = time.time() - 120

    await camera.cleanup_orphaned_streams()

    assert stale.returncode is not None and stale_event.is_set()
    assert f"{PRINTER}-ext-stale007" not in camera._stream_viewers


@pytest.mark.asyncio
async def test_the_janitor_keeps_the_viewer_of_a_stream_between_reconnects(no_fanout, register, monkeypatch):
    """Its ffmpeg died and the stream is starting the next one under the same id:
    its viewer's stop must still reach it."""
    monkeypatch.setattr(camera, "_scan_bambu_ffmpeg_pids", lambda: [])
    dead, _ = register("dead0008", 54011, viewer="tile-a")
    dead.returncode = 1

    await camera.cleanup_orphaned_streams()

    assert camera._stream_viewers.get(f"{PRINTER}-ext-dead0008") == "tile-a"
    assert f"{PRINTER}-ext-dead0008" in camera._disconnect_events
