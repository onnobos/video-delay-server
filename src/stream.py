import cv2
import time
import threading
import queue
import os
import datetime
from collections import deque
from typing import Optional, Dict, Any
import numpy as np

# App Imports
from src.config import RECORDINGS_DIR, THUMBNAILS_DIR
from src.settings import StreamSettings
from src.logger import get_logger
from src import cameras

logger = get_logger(__name__)


class VideoRecorder(threading.Thread):
    """
    Dedicated thread for writing recordings to disk using H.264 (avc1)
    at a constant frame rate matching real wall-clock duration.
    Decoupled from capture and streaming loops to avoid frame drops.
    """
    def __init__(self, filename: str, width: int, height: int, fps: float, queue: queue.Queue):
        super().__init__(daemon=True)
        self.filename = filename
        self.width = int(width)
        self.height = int(height)
        self.fps = max(1.0, float(fps))
        self.queue = queue
        self.running = True
        self.writer: Optional[cv2.VideoWriter] = None
        self.frames_written = 0
        self._last_frame: Optional[np.ndarray] = None

    def _write_frame(self, frame: np.ndarray):
        if not self.writer or not self.writer.isOpened():
            return
        h, w = frame.shape[:2]
        if w != self.width or h != self.height:
            frame = cv2.resize(frame, (self.width, self.height))
        self.writer.write(frame)
        self.frames_written += 1

    def run(self):
        os.makedirs(os.path.dirname(self.filename), exist_ok=True)

        # Try H.264 (avc1) first for Safari/iOS/macOS compatibility; fallback to mp4v
        fourcc = cv2.VideoWriter_fourcc(*'avc1')
        self.writer = cv2.VideoWriter(self.filename, fourcc, self.fps, (self.width, self.height))
        if not self.writer.isOpened():
            logger.warning("H.264 (avc1) writer failed to open, falling back to mp4v...")
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            self.writer = cv2.VideoWriter(self.filename, fourcc, self.fps, (self.width, self.height))

        frame_interval = 1.0 / self.fps
        next_tick = time.monotonic()

        while self.running:
            now = time.monotonic()
            if now < next_tick:
                time.sleep(max(0.001, next_tick - now))

            try:
                frame = self.queue.get_nowait()
                self._last_frame = frame
                self._write_frame(frame)
            except queue.Empty:
                if self._last_frame is not None:
                    self._write_frame(self._last_frame)

            next_tick += frame_interval
            now_after = time.monotonic()
            if now_after - next_tick > 0.5:
                next_tick = now_after + frame_interval

        # Drain any remaining queued frames immediately without sleeping
        while not self.queue.empty():
            try:
                frame = self.queue.get_nowait()
                self._write_frame(frame)
            except queue.Empty:
                break

        if self.writer:
            self.writer.release()

        if self.frames_written == 0:
            if os.path.exists(self.filename):
                try:
                    os.remove(self.filename)
                except OSError:
                    pass
            logger.warning(f"Recording discarded (0 frames captured): {self.filename}")
        else:
            logger.info(f"Video saved successfully: {self.filename} ({self.frames_written} frames)")

    def stop(self):
        self.running = False


class VideoDelayStream:
    """
    High-performance video delay engine with:
    - Smart camera selection (defaulting to Elgato HD60 S+)
    - HDMI signal-loss protection with standby frame generator
    - Circular delay buffer with dynamic resizing
    - Event-driven thread-safe frame delivery for WebRTC
    - H.264 video recording
    """
    test_pattern_mode: bool = False
    frame_health: str = "healthy"
    diagnostic_hint: str = ""
    reconnect_attempts: int = 0
    preferred_camera_name: Optional[str] = None
    _black_frame_streak: int = 0
    _frozen_frame_streak: int = 0
    _last_sample_small: Optional[np.ndarray] = None
    _test_pattern_tick: int = 0

    def __init__(self, settings: StreamSettings, auto_start: bool = False, headless: bool = False):
        logger.info("Initializing VideoDelayStream...")
        self.settings = settings
        self.running = True
        self.streaming = auto_start
        self.paused = False
        self.headless = headless
        
        # Determine camera to use
        self.camera_index, self.camera_name = self._resolve_camera()
        logger.info(f"Selected Camera: [{self.camera_index}] {self.camera_name}")

        # Ensure directories
        os.makedirs(RECORDINGS_DIR, exist_ok=True)
        os.makedirs(THUMBNAILS_DIR, exist_ok=True)

        # Open camera hardware
        self._cap_lock = threading.Lock()
        self.cap: Optional[cv2.VideoCapture] = None
        self.width = self.settings.width
        self.height = self.settings.height
        self.actual_fps = float(self.settings.fps)
        self.measured_fps = float(self.settings.fps)
        self._fps_timestamps: deque = deque(maxlen=60)
        self._open_camera()

        # Circular delay buffer
        self.buffer_lock = threading.Lock()
        self.buffer = deque(maxlen=self.settings.buffer_size)

        # Frame broadcasting for WebRTC
        self.frame_lock = threading.Lock()
        self.latest_frame: Optional[np.ndarray] = None
        self.delayed_frame: Optional[np.ndarray] = None
        self.frame_seq = 0

        # Signal loss & frame health watchdog state
        self.preferred_camera_name: Optional[str] = self.camera_name
        self.test_pattern_mode: bool = False
        self.is_signal_lost = False
        self.consecutive_drops = 0
        self.last_reconnect_attempt = 0.0
        self._reconnecting = False
        self.reconnect_attempts: int = 0
        self.frame_health: str = "no_signal"
        self.diagnostic_hint: str = ""
        self._black_frame_streak: int = 0
        self._frozen_frame_streak: int = 0
        self._last_sample_small: Optional[np.ndarray] = None
        self._test_pattern_tick: int = 0

        # Recording state
        self.rec_lock = threading.Lock()
        self.is_recording = False
        self.rec_queue: queue.Queue = queue.Queue(maxsize=300)
        self.recorder_thread: Optional[VideoRecorder] = None
        self.current_recording_name: Optional[str] = None
        self.thumbnail_saved = False

        # Threads
        self.t_cap: Optional[threading.Thread] = None
        self.t_trans: Optional[threading.Thread] = None

    def _resolve_camera(self) -> tuple[int, str]:
        """Determine camera index and name using settings or smart defaults."""
        target_idx = self.settings.active_camera_index
        available = cameras.list_available_cameras()

        if target_idx is not None:
            for cam in available:
                if cam["index"] == target_idx:
                    return cam["index"], cam["name"]
            return target_idx, f"Camera {target_idx}"

        # If no camera index specified, pick the smart default from single discovery
        for cam in available:
            if cam.get("is_default"):
                return cam["index"], cam["name"]
        if available:
            return available[0]["index"], available[0]["name"]
        return 0, "Default Camera"

    def _open_camera(self):
        """Open and configure the OpenCV VideoCapture device."""
        backend = cameras.get_backend()
        orig_index = self.camera_index
        sec_idx = self.settings.cam_index_secondary if self.settings.cam_index_secondary is not None else 0
        logger.info(f"Opening camera {self.camera_index} ({self.camera_name}) with backend {backend}...")
        self.cap = cv2.VideoCapture(self.camera_index, backend)

        if not self.cap or not self.cap.isOpened():
            if self.cap:
                self.cap.release()
                self.cap = None
            # Try secondary camera if primary fails
            if sec_idx != orig_index:
                logger.warning(f"Camera {orig_index} failed, falling back to secondary {sec_idx}...")
                self.cap = cv2.VideoCapture(sec_idx, backend)
                if self.cap and self.cap.isOpened():
                    self.camera_index = sec_idx
                    info = cameras.get_camera_info(sec_idx)
                    self.camera_name = info["name"] if info else f"Camera {sec_idx}"
                    self.settings.cam_name = self.camera_name

        if not self.cap or not self.cap.isOpened():
            if self.cap:
                self.cap.release()
                self.cap = None
            logger.error(f"Failed to open camera device {orig_index} (and fallback {sec_idx}).")
            self.is_signal_lost = True
            return

        # Request resolution & FPS
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.settings.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.settings.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.settings.fps)

        # Inspect first frame for actual dimensions
        ret, test_frame = self.cap.read()
        if ret and test_frame is not None:
            h, w, _ = test_frame.shape
            self.width = int(w)
            self.height = int(h)
            self.is_signal_lost = False
            del test_frame
            logger.info(f"Camera successfully initialized: {self.width}x{self.height}")
        else:
            logger.warning("Camera opened but failed to read initial frame (waiting for HDMI signal).")
            self.is_signal_lost = True

    def _generate_standby_frame(self) -> np.ndarray:
        """Create a clean, animated standby frame when HDMI signal is disconnected."""
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        frame[:] = (20, 20, 24)  # Dark charcoal slate

        # Outer border
        cv2.rectangle(frame, (20, 20), (self.width - 20, self.height - 20), (45, 45, 55), 2)

        # Status text
        center_y = self.height // 2
        center_x = self.width // 2

        msg1 = "WAITING FOR VIDEO SIGNAL"
        msg2 = f"Input: {self.camera_name}"
        time_str = datetime.datetime.now().strftime("%H:%M:%S")

        # Title
        t_size1, _ = cv2.getTextSize(msg1, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        cv2.putText(frame, msg1, (center_x - t_size1[0] // 2, center_y - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 165, 255), 2)

        # Device info
        t_size2, _ = cv2.getTextSize(msg2, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 1)
        cv2.putText(frame, msg2, (center_x - t_size2[0] // 2, center_y + 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (180, 180, 180), 1)

        # Diagnostic hint & reconnect counter
        hint = getattr(self, "diagnostic_hint", "") or "Waiting for HDMI signal..."
        t_size3, _ = cv2.getTextSize(hint, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.putText(frame, hint, (center_x - t_size3[0] // 2, center_y + 65),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (100, 200, 255), 1)

        attempts = getattr(self, "reconnect_attempts", 0)
        if attempts > 0:
            att_msg = f"Auto-reconnect attempts: {attempts}"
            t_size4, _ = cv2.getTextSize(att_msg, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.putText(frame, att_msg, (center_x - t_size4[0] // 2, center_y + 95),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (140, 140, 150), 1)

        # Timestamp
        cv2.putText(frame, f"Server Time: {time_str}", (30, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (120, 120, 120), 1)

        return frame

    def _generate_test_pattern_frame(self) -> np.ndarray:
        """Create an animated SMPTE-style synthetic test pattern frame."""
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        bars = [
            (235, 235, 235),  # White
            (16, 235, 235),   # Yellow
            (235, 235, 16),   # Cyan
            (16, 235, 16),    # Green
            (235, 16, 235),   # Magenta
            (16, 16, 235),    # Red
            (235, 16, 16),    # Blue
        ]
        bar_w = max(1, self.width // len(bars))
        for i, color in enumerate(bars):
            x0 = i * bar_w
            x1 = self.width if i == len(bars) - 1 else (i + 1) * bar_w
            frame[:, x0:x1] = color

        # Animated horizontal scan bar to make delay & motion visually obvious
        tick = getattr(self, "_test_pattern_tick", 0)
        self._test_pattern_tick = (tick + 6) % max(1, self.height)
        y0 = self._test_pattern_tick
        y1 = min(self.height, y0 + 18)
        frame[y0:y1, :] = (255, 255, 255)

        # Center banner
        cy, cx = self.height // 2, self.width // 2
        cv2.rectangle(frame, (max(20, cx - 280), max(20, cy - 50)),
                      (min(self.width - 20, cx + 280), min(self.height - 20, cy + 50)), (20, 20, 24), -1)
        msg = "TEST PATTERN MODE (SYNTHETIC SIGNAL)"
        sz, _ = cv2.getTextSize(msg, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2)
        cv2.putText(frame, msg, (cx - sz[0] // 2, cy - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 180), 2)
        sub = f"Time: {datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]}"
        sz2, _ = cv2.getTextSize(sub, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        cv2.putText(frame, sub, (cx - sz2[0] // 2, cy + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 1)
        return frame

    def _analyze_frame_health(self, raw_frame: np.ndarray) -> bool:
        """
        Inspect captured frame for silent black or frozen video failures.
        Returns False if frozen long enough to require hardware reset.
        """
        small = raw_frame[::16, ::16]
        mean_val = float(np.mean(small))

        if mean_val < 3.0:
            self._black_frame_streak += 1
            self._frozen_frame_streak = 0
            self._last_sample_small = small.copy()
            if self._black_frame_streak >= 30:
                self.frame_health = "black_frame"
                self.diagnostic_hint = "Black video frame detected (check HDMI source power, HDCP, or USB 3.0 cable)"
            return True

        self._black_frame_streak = 0
        if self._last_sample_small is not None and self._last_sample_small.shape == small.shape:
            diff = float(np.mean(np.abs(small.astype(np.int16) - self._last_sample_small.astype(np.int16))))
            if diff < 0.05:
                self._frozen_frame_streak += 1
            else:
                self._frozen_frame_streak = 0
        else:
            self._frozen_frame_streak = 0

        self._last_sample_small = small.copy()

        if self._frozen_frame_streak >= 60:
            self.frame_health = "frozen_frame"
            self.diagnostic_hint = "Video signal appears frozen (HDMI handshake may be stuck)"
            if self._frozen_frame_streak >= 150:
                self._frozen_frame_streak = 0
                return False
            return True

        self.frame_health = "healthy"
        self.diagnostic_hint = ""
        return True

    def _apply_overlays(self, frame: np.ndarray):
        """Draw configured timestamp, FPS, and camera overlays."""
        # 1. Timestamp (Top-Left)
        if self.settings.show_timestamp:
            timestamp = time.strftime("%H:%M:%S")
            cv2.putText(frame, timestamp, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 5)
            cv2.putText(frame, timestamp, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2)

        # 2. FPS Overlay (Top-Right)
        if self.settings.show_fps:
            fps_text = f"{int(round(self.measured_fps))} FPS"
            (tw, _), _ = cv2.getTextSize(fps_text, cv2.FONT_HERSHEY_SIMPLEX, 1.1, 2)
            fps_x = self.width - tw - 30
            cv2.putText(frame, fps_text, (fps_x, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 5)
            cv2.putText(frame, fps_text, (fps_x, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2)

        # 3. Camera Name (Bottom-Left)
        if self.settings.show_camera_name:
            cam_text = self.camera_name
            cv2.putText(frame, cam_text, (30, self.height - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
            cv2.putText(frame, cam_text, (30, self.height - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)

    def _attempt_reconnect(self):
        """Background reconnection attempt with name-pinning so capture_loop stays paced."""
        self.reconnect_attempts += 1
        new_cap = None
        try:
            # Re-enumerate to guard against device index shifts (e.g., iPhone Continuity Camera)
            available = cameras.list_available_cameras()
            if not available:
                self.diagnostic_hint = "No video capture devices detected on USB / macOS AVFoundation"
            else:
                matched = None
                if self.preferred_camera_name:
                    pref_lower = self.preferred_camera_name.lower()
                    for cam in available:
                        if cam["name"].lower() == pref_lower:
                            matched = cam
                            break
                if matched is None and self.settings.active_camera_index is None:
                    for cam in available:
                        if cam.get("is_default"):
                            matched = cam
                            break
                if matched is not None and matched["index"] != self.camera_index:
                    logger.info(
                        f"Camera '{self.camera_name}' moved from index {self.camera_index} "
                        f"to {matched['index']} (device list changed)"
                    )
                    self.camera_index = matched["index"]
                    self.camera_name = matched["name"]
                    self.settings.cam_name = matched["name"]

            backend = cameras.get_backend()
            new_cap = cv2.VideoCapture(self.camera_index, backend)
            if new_cap and new_cap.isOpened():
                new_cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.settings.width)
                new_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.settings.height)
                new_cap.set(cv2.CAP_PROP_FPS, self.settings.fps)
                ret, test_frame = new_cap.read()
                if ret and test_frame is not None:
                    h, w = test_frame.shape[:2]
                    with self._cap_lock:
                        old_cap = self.cap
                        self.cap = new_cap
                        new_cap = None
                        if old_cap:
                            try:
                                old_cap.release()
                            except Exception:
                                pass
                        self.width = int(w)
                        self.height = int(h)
                        self.consecutive_drops = 0
                        self.is_signal_lost = False
                        self._black_frame_streak = 0
                        self._frozen_frame_streak = 0
                        self.frame_health = "healthy"
                        self.diagnostic_hint = ""
                    del test_frame
                    logger.info(f"Video signal restored! ({self.width}x{self.height})")
                    return
                else:
                    self.diagnostic_hint = "Camera opened but no video frames (check HDMI source / USB 3.0 cable)"
        except Exception as e:
            logger.debug(f"Reconnect attempt error: {e}")
        finally:
            if new_cap is not None:
                try:
                    new_cap.release()
                except Exception:
                    pass
            self._reconnecting = False

    def capture_loop(self):
        """Continuous frame ingestion loop with non-blocking auto-reconnection and health watchdog."""
        while self.running:
            start_time = time.monotonic()
            target_fps = max(1.0, float(self.settings.fps))
            target_interval = 1.0 / target_fps

            frame = None
            if self.test_pattern_mode:
                frame = self._generate_test_pattern_frame()
                self.frame_health = "test_pattern"
                self.diagnostic_hint = "Synthetic test pattern active"
            else:
                if not self.is_signal_lost:
                    with self._cap_lock:
                        cap = self.cap
                        if cap and cap.isOpened():
                            ret, raw_frame = cap.read()
                        else:
                            ret, raw_frame = False, None

                    if ret and raw_frame is not None:
                        keep_ok = self._analyze_frame_health(raw_frame)
                        if keep_ok:
                            frame = raw_frame
                            self.width = int(raw_frame.shape[1])
                            self.height = int(raw_frame.shape[0])
                            self.consecutive_drops = 0
                        else:
                            logger.warning("Frozen camera stream detected; resetting capture hardware...")
                            self.is_signal_lost = True
                            self.frame_health = "no_signal"
                            self.diagnostic_hint = "Resetting frozen camera hardware..."
                            self.last_reconnect_attempt = time.monotonic()
                            with self._cap_lock:
                                if self.cap:
                                    try:
                                        self.cap.release()
                                    except Exception:
                                        pass
                                    self.cap = None
                    else:
                        self.consecutive_drops += 1
                        if self.consecutive_drops >= 5:
                            logger.warning("HDMI signal lost or camera disconnected. Showing standby screen...")
                            self.is_signal_lost = True
                            self.frame_health = "no_signal"
                            if not self.diagnostic_hint:
                                self.diagnostic_hint = "HDMI signal lost or camera disconnected"
                            self.last_reconnect_attempt = time.monotonic()
                            with self._cap_lock:
                                if self.cap:
                                    try:
                                        self.cap.release()
                                    except Exception:
                                        pass
                                    self.cap = None

                if frame is None:
                    self.frame_health = "no_signal"
                    if not self.diagnostic_hint:
                        self.diagnostic_hint = "HDMI signal lost or camera disconnected"
                    now = time.monotonic()
                    if self.is_signal_lost and not self._reconnecting and (now - self.last_reconnect_attempt >= 3.0):
                        self.last_reconnect_attempt = now
                        self._reconnecting = True
                        threading.Thread(target=self._attempt_reconnect, daemon=True).start()

                    frame = self._generate_standby_frame()

            # Measure actual ingestion FPS
            fps_now = time.monotonic()
            self._fps_timestamps.append(fps_now)
            if len(self._fps_timestamps) >= 2:
                span = self._fps_timestamps[-1] - self._fps_timestamps[0]
                if span > 0:
                    self.measured_fps = round((len(self._fps_timestamps) - 1) / span, 1)

            # Apply overlays
            self._apply_overlays(frame)

            # Store latest raw frame for snapshot
            with self.frame_lock:
                self.latest_frame = frame.copy()

            # Append to circular delay buffer
            with self.buffer_lock:
                self.buffer.append(frame)

            # Pace capture loop
            elapsed = time.monotonic() - start_time
            sleep_needed = target_interval - elapsed
            if sleep_needed > 0:
                time.sleep(sleep_needed)

    def transmit_loop(self):
        """Controls pacing and pops delayed frames for WebRTC and Recording."""
        while self.running:
            start_time = time.monotonic()
            target_fps = max(1.0, float(self.settings.fps))
            target_interval = 1.0 / target_fps

            if self.paused:
                if not self.headless and self.delayed_frame is not None:
                    display_frame = self.delayed_frame.copy()
                    cv2.putText(display_frame, "PAUSED", (self.width - 200, 60),
                                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 165, 255), 3)
                    cv2.imshow("Video Delay Server", display_frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord('q'):
                        self.running = False
                        break
                    elif key == ord('p'):
                        self.paused = not self.paused
                time.sleep(min(0.05, target_interval))
                continue

            has_frame = False
            delayed_bgr = None

            with self.buffer_lock:
                # Buffer has reached configured delay
                if len(self.buffer) >= self.settings.buffer_size:
                    delayed_bgr = self.buffer.popleft()
                    has_frame = True

            if has_frame and delayed_bgr is not None:
                # Store delayed frame & notify WebRTC subscribers
                with self.frame_lock:
                    self.delayed_frame = delayed_bgr
                    self.frame_seq += 1

                # Handle video recording thread-safely
                should_stop_rec = False
                with self.rec_lock:
                    if self.is_recording and self.recorder_thread:
                        if self.rec_queue.full():
                            try:
                                self.rec_queue.get_nowait()
                            except queue.Empty:
                                pass
                        try:
                            self.rec_queue.put_nowait(delayed_bgr)
                        except queue.Full:
                            pass
                        if not self.thumbnail_saved and self.current_recording_name:
                            self._save_thumbnail(delayed_bgr)
                    elif not self.streaming and self.is_recording:
                        should_stop_rec = True

                if should_stop_rec:
                    self.stop_recording()

                # Interactive window preview (if not headless)
                if not self.headless:
                    display_frame = delayed_bgr.copy()
                    if self.is_recording:
                        cv2.putText(display_frame, "REC", (self.width - 100, self.height - 30),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                    cv2.imshow("Video Delay Server", display_frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord('q'):
                        self.running = False
                        break
                    elif key == ord('s'):
                        self.streaming = not self.streaming
                    elif key == ord('p'):
                        self.paused = not self.paused
            else:
                # Buffer still filling up
                time.sleep(0.002)
                continue

            # Pacing
            elapsed = time.monotonic() - start_time
            sleep_needed = target_interval - elapsed
            if sleep_needed > 0:
                time.sleep(sleep_needed)

    def set_delay(self, new_delay_seconds: int):
        """Dynamically adjust the delay buffer size without stopping the stream."""
        new_delay_seconds = max(1, min(120, int(new_delay_seconds)))
        with self.buffer_lock:
            old_delay = self.settings.delay_seconds
            new_buffer_size = int(new_delay_seconds * self.actual_fps)
            if new_delay_seconds == old_delay and self.buffer.maxlen == new_buffer_size:
                return
            logger.info(f"Dynamically updating delay: {old_delay}s -> {new_delay_seconds}s (buffer: {new_buffer_size})")
            self.settings.delay_seconds = new_delay_seconds
            self.buffer = deque(self.buffer, maxlen=new_buffer_size)

    def set_fps(self, new_fps: int):
        """Dynamically adjust target FPS and buffer size without stopping the stream."""
        new_fps = max(5, min(60, int(new_fps)))
        with self.buffer_lock:
            old_fps = int(self.settings.fps)
            self.settings.fps = new_fps
            self.actual_fps = float(new_fps)
            new_buffer_size = int(self.settings.delay_seconds * self.actual_fps)
            if new_fps == old_fps and self.buffer.maxlen == new_buffer_size:
                return
            logger.info(f"Dynamically updating target FPS: {old_fps} -> {new_fps} (buffer: {new_buffer_size})")
            self.buffer = deque(self.buffer, maxlen=new_buffer_size)

    def _save_thumbnail(self, frame: np.ndarray):
        """Save a crisp thumbnail for gallery display."""
        try:
            filename = os.path.join(THUMBNAILS_DIR, f"{self.current_recording_name}.jpg")
            thumb_height = 180
            thumb_width = int(thumb_height * self.width / self.height)
            thumb = cv2.resize(frame, (thumb_width, thumb_height))
            cv2.imwrite(filename, thumb, [cv2.IMWRITE_JPEG_QUALITY, 85])
            self.thumbnail_saved = True
            logger.info(f"Thumbnail saved: {filename}")
        except Exception as e:
            logger.error(f"Thumbnail save failed: {e}")

    def start_recording(self):
        with self.rec_lock:
            if self.is_recording:
                return

            timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self.current_recording_name = timestamp
            filename = os.path.join(RECORDINGS_DIR, f"{timestamp}.mp4")

            while not self.rec_queue.empty():
                try:
                    self.rec_queue.get_nowait()
                except queue.Empty:
                    break

            self.recorder_thread = VideoRecorder(
                filename=filename,
                width=self.width,
                height=self.height,
                fps=self.actual_fps,
                queue=self.rec_queue
            )
            self.recorder_thread.start()
            self.is_recording = True
            self.thumbnail_saved = False
            logger.info(f"Recording started: {filename} @ {self.actual_fps} FPS (H.264)")

    def stop_recording(self):
        with self.rec_lock:
            recorder = self.recorder_thread
            self.recorder_thread = None
            self.is_recording = False

        if recorder:
            recorder.stop()
            recorder.join(timeout=6.0)

        while not self.rec_queue.empty():
            try:
                self.rec_queue.get_nowait()
            except queue.Empty:
                break
        logger.info("Recording stopped.")

    def get_delayed_frame(self) -> Optional[np.ndarray]:
        """Thread-safe getter for the current delayed frame."""
        with self.frame_lock:
            return self.delayed_frame

    def get_snapshot(self) -> Optional[bytes]:
        """Thread-safe JPEG snapshot of the delayed frame."""
        with self.frame_lock:
            frame = self.delayed_frame if self.delayed_frame is not None else self.latest_frame
            if frame is not None:
                ret, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                if ret:
                    return buf.tobytes()
        return None

    def get_metrics(self) -> Dict[str, Any]:
        """Return real-time operational and memory metrics."""
        with self.buffer_lock:
            buf_len = len(self.buffer)
            buf_max = self.buffer.maxlen or self.settings.buffer_size

        percent_full = round((buf_len / max(1, buf_max)) * 100, 1)
        # Approximate frame memory: width * height * 3 bytes
        frame_bytes = self.width * self.height * 3
        estimated_ram_mb = round((buf_len * frame_bytes) / (1024 * 1024), 1)

        return {
            "camera_index": self.camera_index,
            "camera_name": self.camera_name,
            "resolution": f"{self.width}x{self.height}",
            "fps": self.actual_fps,
            "measured_fps": round(self.measured_fps, 1),
            "buffer_length": buf_len,
            "buffer_capacity": buf_max,
            "buffer_percent": percent_full,
            "buffer_delay_seconds": self.settings.delay_seconds,
            "estimated_ram_mb": estimated_ram_mb,
            "signal_lost": self.is_signal_lost,
            "frame_health": self.frame_health,
            "diagnostic_hint": self.diagnostic_hint,
            "test_pattern": self.test_pattern_mode,
            "reconnect_attempts": self.reconnect_attempts,
            "recording": self.is_recording,
            "streaming": self.streaming,
            "paused": self.paused
        }

    def start(self, blocking: bool = False):
        """Start ingestion and pacing threads."""
        if not hasattr(self, 't_cap') or not self.t_cap or not self.t_cap.is_alive():
            self.t_cap = threading.Thread(target=self.capture_loop, daemon=True)
            self.t_cap.start()

        if not hasattr(self, 't_trans') or not self.t_trans or not self.t_trans.is_alive():
            self.t_trans = threading.Thread(target=self.transmit_loop, daemon=True)
            self.t_trans.start()

        logger.info("Stream threads running.")

    def stop(self):
        """Gracefully release hardware and stop all worker threads."""
        logger.info("Stopping stream...")
        self.running = False

        if hasattr(self, 't_cap') and self.t_cap and self.t_cap.is_alive():
            self.t_cap.join(timeout=2.0)
        if hasattr(self, 't_trans') and self.t_trans and self.t_trans.is_alive():
            self.t_trans.join(timeout=2.0)

        if self.is_recording:
            self.stop_recording()

        with self._cap_lock:
            if self.cap and self.cap.isOpened():
                self.cap.release()
            self.cap = None

        if not self.headless:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass

        logger.info("Stream stopped cleanly.")
