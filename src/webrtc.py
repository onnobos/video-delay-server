import asyncio
import fractions
import time
import cv2
from typing import Any, Callable, Optional, Union

from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.mediastreams import MediaStreamError
from av import VideoFrame

from src.logger import get_logger

logger = get_logger(__name__)

VIDEO_CLOCK_RATE = 90000
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)

# Track all active peer connections
pcs: set[RTCPeerConnection] = set()


class VideoDelayTrack(VideoStreamTrack):
    """
    An optimized aiortc VideoStreamTrack delivering frames from VideoDelayStream
    with precise PTS timing, pause frame-hold, and dynamic stream rebinding.
    """
    kind = "video"

    def __init__(self, stream_or_provider: Union[Any, Callable[[], Optional[Any]]]):
        super().__init__()
        self._get_stream: Callable[[], Optional[Any]] = (
            stream_or_provider if callable(stream_or_provider) else (lambda: stream_or_provider)
        )
        self._pts = 0
        self._last_seq = -1
        self._last_stream_id: Optional[int] = None
        self._last_frame_bgr: Optional[Any] = None
        self._start_time: Optional[float] = None

    async def recv(self) -> VideoFrame:
        if self.readyState != "live":
            raise MediaStreamError

        if self._start_time is None:
            self._start_time = time.monotonic()

        frame_bgr = None
        fps = 30.0
        frame_pts_step = int(VIDEO_CLOCK_RATE / fps)

        # Wait until a fresh delayed frame (or paused frozen frame) is available
        while True:
            if self.readyState != "live":
                raise MediaStreamError

            stream = self._get_stream()
            if stream is None or not getattr(stream, "running", False):
                await asyncio.sleep(0.02)
                continue

            fps = max(1.0, float(getattr(stream, "actual_fps", 30.0)))
            frame_interval = 1.0 / fps
            frame_pts_step = int(VIDEO_CLOCK_RATE / fps)

            # Rebind seamlessly if StreamManager restarted/replaced the stream
            stream_id = id(stream)
            if stream_id != self._last_stream_id:
                self._last_stream_id = stream_id
                self._last_seq = -1

            # Emit frozen frame at low keepalive rate when paused
            if getattr(stream, "paused", False) and self._last_frame_bgr is not None:
                await asyncio.sleep(max(frame_interval, 0.1))
                frame_bgr = self._last_frame_bgr
                break

            current_seq = stream.frame_seq
            if current_seq != self._last_seq and stream.delayed_frame is not None:
                self._last_seq = current_seq
                candidate = stream.get_delayed_frame()
                if candidate is not None:
                    self._last_frame_bgr = candidate
                    frame_bgr = candidate
                    break

            await asyncio.sleep(min(0.005, frame_interval / 4))

        # Convert BGR -> RGB for av VideoFrame
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        av_frame = VideoFrame.from_ndarray(frame_rgb, format="rgb24")
        av_frame.pts = self._pts
        av_frame.time_base = VIDEO_TIME_BASE
        self._pts += frame_pts_step

        return av_frame


async def handle_offer(
    sdp: str,
    sdp_type: str,
    stream_or_provider: Union[Any, Callable[[], Optional[Any]]],
    connect_timeout: float = 15.0,
    disconnect_grace: float = 5.0,
) -> dict:
    """
    Process WebRTC offer from browser, attach the delay video track,
    and return answer SDP.
    """
    offer = RTCSessionDescription(sdp=sdp, type=sdp_type)
    pc = RTCPeerConnection()
    pcs.add(pc)

    logger.info(f"WebRTC: New offer received. Active peers: {len(pcs)}")

    bg_tasks: set[asyncio.Task] = set()

    async def _cleanup_pc(reason: str):
        if pc in pcs:
            pcs.discard(pc)
            try:
                await pc.close()
            except Exception:
                pass
            logger.info(f"WebRTC peer closed ({reason}). Remaining peers: {len(pcs)}")
        for task in list(bg_tasks):
            if not task.done() and task is not asyncio.current_task():
                task.cancel()

    async def _disconnect_watchdog():
        try:
            await asyncio.sleep(disconnect_grace)
            if pc in pcs and pc.connectionState in ("disconnected", "failed", "closed"):
                await _cleanup_pc("disconnected timeout")
        except asyncio.CancelledError:
            pass

    async def _connect_watchdog():
        try:
            await asyncio.sleep(connect_timeout)
            if pc in pcs and pc.connectionState in ("new", "connecting"):
                await _cleanup_pc("connecting timeout")
        except asyncio.CancelledError:
            pass

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        state = pc.connectionState
        logger.info(f"WebRTC peer state changed: {state}")
        if state in ("failed", "closed"):
            await _cleanup_pc(state)
        elif state == "disconnected":
            t = asyncio.create_task(_disconnect_watchdog())
            bg_tasks.add(t)
            t.add_done_callback(bg_tasks.discard)
        elif state == "connected":
            for task in list(bg_tasks):
                if not task.done():
                    task.cancel()

    watchdog = asyncio.create_task(_connect_watchdog())
    bg_tasks.add(watchdog)
    watchdog.add_done_callback(bg_tasks.discard)

    video_track = VideoDelayTrack(stream_or_provider)
    pc.addTrack(video_track)

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


def get_active_viewers_count() -> int:
    """Return count of actively connected WebRTC peer sessions."""
    active = 0
    for pc in list(pcs):
        if pc.connectionState == "connected":
            active += 1
    return active


async def close_all():
    """Close all open peer connections cleanly."""
    logger.info(f"Closing {len(pcs)} WebRTC peer connections...")
    coros = [pc.close() for pc in list(pcs)]
    await asyncio.gather(*coros, return_exceptions=True)
    pcs.clear()
