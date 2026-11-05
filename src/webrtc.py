import asyncio
import fractions
import time
import cv2
from typing import Optional

from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
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
    with precise PTS timing and frame sequence tracking.
    """
    kind = "video"

    def __init__(self, stream):
        super().__init__()
        self._stream = stream
        self._pts = 0
        self._last_seq = -1
        self._start_time: Optional[float] = None

    async def recv(self) -> VideoFrame:
        fps = max(1.0, float(self._stream.actual_fps))
        frame_interval = 1.0 / fps
        frame_pts_step = int(VIDEO_CLOCK_RATE / fps)

        if self._start_time is None:
            self._start_time = time.monotonic()

        # Wait until a fresh delayed frame is available
        while True:
            # Check if new sequence frame arrived
            current_seq = self._stream.frame_seq
            if current_seq != self._last_seq and self._stream.delayed_frame is not None:
                self._last_seq = current_seq
                frame_bgr = self._stream.get_delayed_frame()
                if frame_bgr is not None:
                    break

            # If stream is stopped or buffer filling up, yield control briefly
            await asyncio.sleep(min(0.005, frame_interval / 4))

        # Convert BGR -> RGB for av VideoFrame
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        av_frame = VideoFrame.from_ndarray(frame_rgb, format="rgb24")
        av_frame.pts = self._pts
        av_frame.time_base = VIDEO_TIME_BASE
        self._pts += frame_pts_step

        return av_frame


async def handle_offer(sdp: str, sdp_type: str, stream) -> dict:
    """
    Process WebRTC offer from browser, attach the delay video track,
    and return answer SDP.
    """
    offer = RTCSessionDescription(sdp=sdp, type=sdp_type)
    pc = RTCPeerConnection()
    pcs.add(pc)

    logger.info(f"WebRTC: New offer received. Active peers: {len(pcs)}")

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info(f"WebRTC peer state changed: {pc.connectionState}")
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            pcs.discard(pc)
            logger.info(f"WebRTC peer closed. Remaining peers: {len(pcs)}")

    video_track = VideoDelayTrack(stream)
    pc.addTrack(video_track)

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


def get_active_viewers_count() -> int:
    """Return count of actively connected WebRTC peer sessions."""
    active = 0
    for pc in list(pcs):
        if pc.connectionState in ("connected", "connecting"):
            active += 1
    return active


async def close_all():
    """Close all open peer connections cleanly."""
    logger.info(f"Closing {len(pcs)} WebRTC peer connections...")
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros, return_exceptions=True)
    pcs.clear()
