import asyncio
from collections import deque
import fractions
import threading
import time
import av
import cv2
from typing import Any, Callable, Optional, Tuple, Union

from aiortc import RTCPeerConnection, RTCRtpSender, RTCSessionDescription, VideoStreamTrack
from aiortc.codecs import h264 as aiortc_h264
from aiortc.mediastreams import MediaStreamError
from av import VideoFrame

from src.logger import get_logger

logger = get_logger(__name__)

VIDEO_CLOCK_RATE = 90000
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)

# Track all active peer connections, their tracks, & shared YUV420P conversion cache
pcs: set[RTCPeerConnection] = set()
_pc_tracks: dict[RTCPeerConnection, "VideoDelayTrack"] = {}
_yuv_lock = threading.Lock()
_yuv_cache: Optional[Tuple[Tuple[int, int], Any]] = None
_fps_lock = threading.Lock()
_last_fps_key: Optional[Tuple[int, int]] = None
_webrtc_timestamps: deque = deque(maxlen=60)
_h264_patched = False


def _configure_fast_h264_encoder(preset: str = "ultrafast", thread_count: int = 2) -> None:
    """Configure aiortc's libx264 encoder for low CPU and bounded thread count per viewer."""
    global _h264_patched
    if _h264_patched:
        return
    _h264_patched = True

    orig_encode_frame = aiortc_h264.H264Encoder._encode_frame

    def _fast_encode_frame(self, frame: av.VideoFrame, force_keyframe: bool):
        if self.codec and (
            frame.width != self.codec.width
            or frame.height != self.codec.height
            or abs(self.target_bitrate - self.codec.bit_rate) / max(1, self.codec.bit_rate) > 0.1
        ):
            self.buffer_data = b""
            self.buffer_pts = None
            self.codec = None

        if self.codec is None:
            codec = av.CodecContext.create("libx264", "w")
            codec.width = frame.width
            codec.height = frame.height
            codec.bit_rate = self.target_bitrate
            codec.pix_fmt = "yuv420p"
            codec.framerate = fractions.Fraction(aiortc_h264.MAX_FRAME_RATE, 1)
            codec.time_base = fractions.Fraction(1, aiortc_h264.MAX_FRAME_RATE)
            codec.thread_count = thread_count
            codec.options = {
                "level": "31",
                "tune": "zerolatency",
                "preset": preset,
            }
            codec.profile = "Baseline"
            self.codec = codec

        return orig_encode_frame(self, frame, force_keyframe)

    aiortc_h264.H264Encoder._encode_frame = _fast_encode_frame


_configure_fast_h264_encoder()


def _get_or_convert_yuv420p(frame_bgr: Any, cache_key: Optional[Tuple[int, int]] = None) -> Any:
    """
    Convert BGR frame to planar YUV420P (I420) once per (stream_id, frame_seq).
    Multiple WebRTC viewers reuse the cached I420 array while building independent VideoFrames.
    """
    global _yuv_cache
    if cache_key is not None:
        with _yuv_lock:
            if _yuv_cache is not None and _yuv_cache[0] == cache_key:
                return _yuv_cache[1]

    h, w = frame_bgr.shape[:2]
    h2, w2 = h & ~1, w & ~1
    if h2 != h or w2 != w:
        frame_bgr = frame_bgr[:h2, :w2]
    yuv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2YUV_I420)

    if cache_key is not None:
        with _yuv_lock:
            _yuv_cache = (cache_key, yuv)
    return yuv


def _bgr_to_yuv420p_frame(
    frame_bgr: Any,
    pts: int,
    time_base: fractions.Fraction,
    cache_key: Optional[Tuple[int, int]] = None,
) -> VideoFrame:
    """Create an independent VideoFrame(yuv420p) backed by cached I420 conversion."""
    yuv = _get_or_convert_yuv420p(frame_bgr, cache_key=cache_key)
    av_frame = VideoFrame.from_ndarray(yuv, format="yuv420p")
    av_frame.pts = pts
    av_frame.time_base = time_base
    return av_frame


def _record_webrtc_emission(cache_key: Optional[Tuple[int, int]], is_paused: bool = False) -> None:
    """Record unique frame emission timestamps without double-counting across concurrent viewers."""
    global _last_fps_key
    with _fps_lock:
        if not is_paused and cache_key is not None:
            if _last_fps_key == cache_key:
                return
            _last_fps_key = cache_key
        _webrtc_timestamps.append(time.monotonic())


def get_webrtc_fps() -> float:
    """Return rolling WebRTC stream emission rate (FPS), or 0.0 if idle."""
    with _fps_lock:
        if len(_webrtc_timestamps) < 2:
            return 0.0
        now = time.monotonic()
        if now - _webrtc_timestamps[-1] > 2.0:
            return 0.0
        span = _webrtc_timestamps[-1] - _webrtc_timestamps[0]
        if span <= 0:
            return 0.0
        return round((len(_webrtc_timestamps) - 1) / span, 1)


def _get_h264_preferred_codecs():
    """Return video codec list ordered with H.264 first, VP8/RTX as fallback."""
    caps = RTCRtpSender.getCapabilities("video")
    if not caps or not caps.codecs:
        return []
    h264 = [c for c in caps.codecs if c.mimeType.lower() == "video/h264"]
    vp8 = [c for c in caps.codecs if c.mimeType.lower() == "video/vp8"]
    other = [c for c in caps.codecs if c.mimeType.lower() not in ("video/h264", "video/vp8")]
    return h264 + vp8 + other


class VideoDelayTrack(VideoStreamTrack):
    """
    Event-driven aiortc VideoStreamTrack delivering YUV420P frames from VideoDelayStream
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
        self._frame_ready: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._bound_stream: Optional[Any] = None
        self._listener_cb: Optional[Callable[[], None]] = None

    def _unbind_listener(self):
        if self._bound_stream is not None and self._listener_cb is not None:
            if hasattr(self._bound_stream, "remove_frame_listener"):
                try:
                    self._bound_stream.remove_frame_listener(self._listener_cb)
                except Exception:
                    pass
        self._bound_stream = None
        self._listener_cb = None

    def _bind_listener(self, stream: Any):
        self._unbind_listener()
        if not hasattr(stream, "add_frame_listener"):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._frame_ready is None:
            self._frame_ready = asyncio.Event()
        self._loop = loop
        event = self._frame_ready

        def _on_frame():
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                pass

        self._listener_cb = _on_frame
        self._bound_stream = stream
        stream.add_frame_listener(_on_frame)

    def stop(self):
        self._unbind_listener()
        self._last_frame_bgr = None
        self._frame_ready = None
        super().stop()

    async def recv(self) -> VideoFrame:
        if self.readyState != "live":
            raise MediaStreamError

        if self._start_time is None:
            self._start_time = time.monotonic()
        if self._frame_ready is None:
            self._frame_ready = asyncio.Event()

        frame_bgr = None
        fps = 30.0
        frame_pts_step = int(VIDEO_CLOCK_RATE / fps)
        cache_key: Optional[Tuple[int, int]] = None
        is_paused_emit = False

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
                self._bind_listener(stream)

            # Emit frozen frame at low keepalive rate when paused
            if getattr(stream, "paused", False) and self._last_frame_bgr is not None:
                await asyncio.sleep(max(frame_interval, 0.1))
                frame_bgr = self._last_frame_bgr
                is_paused_emit = True
                cache_key = None
                break

            self._frame_ready.clear()
            current_seq = stream.frame_seq
            if current_seq != self._last_seq and stream.delayed_frame is not None:
                self._last_seq = current_seq
                candidate = stream.get_delayed_frame()
                if candidate is not None:
                    self._last_frame_bgr = candidate
                    frame_bgr = candidate
                    cache_key = (stream_id, current_seq)
                    break

            if self._bound_stream is stream:
                try:
                    await asyncio.wait_for(self._frame_ready.wait(), timeout=0.05)
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(min(0.005, frame_interval / 4))

        pts = self._pts
        self._pts += frame_pts_step
        av_frame = await asyncio.to_thread(
            _bgr_to_yuv420p_frame, frame_bgr, pts, VIDEO_TIME_BASE, cache_key
        )
        _record_webrtc_emission(cache_key, is_paused=is_paused_emit)
        return av_frame


async def handle_offer(
    sdp: str,
    sdp_type: str,
    stream_or_provider: Union[Any, Callable[[], Optional[Any]]],
    connect_timeout: float = 15.0,
    disconnect_grace: float = 5.0,
) -> dict:
    """
    Process WebRTC offer from browser, attach a per-peer H.264-preferred delay track,
    and return answer SDP.
    """
    offer = RTCSessionDescription(sdp=sdp, type=sdp_type)
    pc = RTCPeerConnection()
    pcs.add(pc)
    video_track = VideoDelayTrack(stream_or_provider)
    _pc_tracks[pc] = video_track

    logger.info(f"WebRTC: New offer received. Active peers: {len(pcs)}")

    bg_tasks: set[asyncio.Task] = set()

    async def _cleanup_pc(reason: str):
        global _yuv_cache
        track = _pc_tracks.pop(pc, None)
        if track is not None:
            try:
                track.stop()
            except Exception:
                pass
        if pc in pcs:
            pcs.discard(pc)
            try:
                await pc.close()
            except Exception:
                pass
            if not pcs:
                with _yuv_lock:
                    _yuv_cache = None
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

    pc.addTrack(video_track)

    # Prefer H.264 over VP8 for faster encoding and Apple hardware decoding
    try:
        preferred = _get_h264_preferred_codecs()
        if preferred and hasattr(pc, "getTransceivers"):
            for transceiver in pc.getTransceivers():
                if getattr(transceiver, "kind", None) == "video" and hasattr(transceiver, "setCodecPreferences"):
                    transceiver.setCodecPreferences(preferred)
    except Exception as e:
        logger.debug(f"Could not set H.264 codec preference: {e}")

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
    """Close all open peer connections cleanly and reset YUV/FPS state."""
    global _yuv_cache, _last_fps_key
    logger.info(f"Closing {len(pcs)} WebRTC peer connections...")
    for track in list(_pc_tracks.values()):
        try:
            track.stop()
        except Exception:
            pass
    _pc_tracks.clear()
    coros = [pc.close() for pc in list(pcs)]
    await asyncio.gather(*coros, return_exceptions=True)
    pcs.clear()
    with _yuv_lock:
        _yuv_cache = None
    with _fps_lock:
        _last_fps_key = None
        _webrtc_timestamps.clear()
