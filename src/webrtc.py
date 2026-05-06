import asyncio
import fractions
import time
import cv2

from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaRelay
from av import VideoFrame

from src.logger import get_logger

logger = get_logger(__name__)

VIDEO_CLOCK_RATE = 90000
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)

# Track all active peer connections so we can close them on shutdown
pcs: set[RTCPeerConnection] = set()


class VideoDelayTrack(VideoStreamTrack):
    """
    An aiortc VideoStreamTrack that pulls frames from the VideoDelayStream
    delay buffer and delivers them to WebRTC peers.
    """
    kind = "video"

    def __init__(self, stream):
        super().__init__()
        self._stream = stream
        self._pts = 0
        self._last_frame = None

    async def recv(self) -> VideoFrame:
        fps = max(1, round(self._stream.actual_fps))
        frame_duration = VIDEO_CLOCK_RATE // fps

        # Wait until the delay buffer has enough frames
        while True:
            frame_bgr = self._stream.get_delayed_frame()
            if frame_bgr is not None:
                break
            await asyncio.sleep(1.0 / fps)

        # Convert BGR → RGB for av
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        av_frame = VideoFrame.from_ndarray(frame_rgb, format="rgb24")
        av_frame.pts = self._pts
        av_frame.time_base = VIDEO_TIME_BASE
        self._pts += frame_duration

        # Pace delivery to match camera FPS
        await asyncio.sleep(1.0 / fps)

        return av_frame


async def handle_offer(sdp: str, sdp_type: str, stream) -> dict:
    """
    Process a WebRTC offer from the browser, attach the delay video track,
    and return the answer SDP.
    """
    offer = RTCSessionDescription(sdp=sdp, type=sdp_type)
    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info(f"WebRTC connection state: {pc.connectionState}")
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            pcs.discard(pc)

    track = VideoDelayTrack(stream)
    pc.addTrack(track)

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type,
    }


async def close_all():
    """Close all active peer connections (called on server shutdown)."""
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()
