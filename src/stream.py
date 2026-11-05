import cv2
import time
import threading
import os
import datetime
from collections import deque
from typing import Optional, Dict, Any
import numpy as np

# App Imports
from src.settings import StreamSettings
from src.logger import get_logger
from src import cameras

logger = get_logger(__name__)

RECORDINGS_DIR = "recordings"
THUMBNAILS_DIR = "thumbnails"


class VideoRecorder(threading.Thread):
    """
    Dedicated thread for writing recordings to disk using H.264 (avc1).
    Decoupled from capture and streaming loops to avoid frame drops.
    """
    def __init__(self, filename: str, width: int, height: int, fps: float, queue: deque):
        super().__init__(daemon=True)
        self.filename = filename
        self.width = width
        self.height = height
        self.fps = max(1.0, float(fps))
        self.queue = queue
        self.running = True
        self.writer: Optional[cv2.VideoWriter] = None

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
        next_write = time.monotonic()

        while self.running or len(self.queue) > 0:
            now = time.monotonic()
            if now < next_write:
                time.sleep(max(0.001, next_write - now))
            next_write += frame_interval

            if len(self.queue) > 0:
                frame = self.queue.popleft()
                if self.writer and self.writer.isOpened():
                    self.writer.write(frame)

        if self.writer:
            self.writer.release()
            logger.info(f"Video saved successfully: {self.filename}")

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
        self.cap: Optional[cv2.VideoCapture] = None
        self.width = self.settings.width
        self.height = self.settings.height
        self.actual_fps = float(self.settings.fps)
        self._open_camera()

        # Circular delay buffer
        self.buffer_lock = threading.Lock()
        self.buffer = deque(maxlen=self.settings.buffer_size)

        # Frame broadcasting for WebRTC
        self.frame_lock = threading.Lock()
        self.latest_frame: Optional[np.ndarray] = None
        self.delayed_frame: Optional[np.ndarray] = None
        self.frame_seq = 0
        self.frame_event = threading.Event()

        # Signal loss state
        self.is_signal_lost = False
        self.consecutive_drops = 0
        self.last_reconnect_attempt = 0.0

        # Recording state
        self.is_recording = False
        self.rec_queue = deque(maxlen=300)
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

        # If no camera index specified, use the smart default (Elgato HD60 S+ if connected)
        def_idx, def_name = cameras.find_default_camera()
        return def_idx, def_name

    def _open_camera(self):
        """Open and configure the OpenCV VideoCapture device."""
        backend = cameras.get_backend()
        logger.info(f"Opening camera {self.camera_index} ({self.camera_name}) with backend {backend}...")
        self.cap = cv2.VideoCapture(self.camera_index, backend)

        if not self.cap or not self.cap.isOpened():
            # Try secondary camera if primary fails
            sec_idx = self.settings.cam_index_secondary if self.settings.cam_index_secondary is not None else 0
            if sec_idx != self.camera_index:
                logger.warning(f"Camera {self.camera_index} failed, falling back to secondary {sec_idx}...")
                self.cap = cv2.VideoCapture(sec_idx, backend)
                if self.cap and self.cap.isOpened():
                    self.camera_index = sec_idx
                    info = cameras.get_camera_info(sec_idx)
                    self.camera_name = info["name"] if info else f"Camera {sec_idx}"

        if not self.cap or not self.cap.isOpened():
            logger.error(f"Failed to open camera device {self.camera_index}.")
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

        # Timestamp
        cv2.putText(frame, f"Server Time: {time_str}", (30, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (120, 120, 120), 1)

        return frame

    def _apply_overlays(self, frame: np.ndarray):
        """Draw configured timestamp, FPS, and camera overlays."""
        # 1. Timestamp (Top-Left)
        if self.settings.show_timestamp:
            timestamp = time.strftime("%H:%M:%S")
            cv2.putText(frame, timestamp, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 5)
            cv2.putText(frame, timestamp, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2)

        # 2. FPS Overlay (Top-Right)
        if self.settings.show_fps:
            fps_text = f"{int(self.actual_fps)} FPS"
            (tw, _), _ = cv2.getTextSize(fps_text, cv2.FONT_HERSHEY_SIMPLEX, 1.1, 2)
            fps_x = self.width - tw - 30
            cv2.putText(frame, fps_text, (fps_x, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 5)
            cv2.putText(frame, fps_text, (fps_x, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2)

        # 3. Camera Name (Bottom-Left)
        if self.settings.show_camera_name:
            cam_text = self.camera_name
            cv2.putText(frame, cam_text, (30, self.height - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
            cv2.putText(frame, cam_text, (30, self.height - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)

    def capture_loop(self):
        """Continuous frame ingestion loop with auto-reconnection and signal fallback."""
        target_interval = 1.0 / self.actual_fps

        while self.running:
            start_time = time.time()

            frame = None
            if self.cap and self.cap.isOpened():
                ret, raw_frame = self.cap.read()
                if ret and raw_frame is not None:
                    frame = raw_frame
                    self.consecutive_drops = 0
                    if self.is_signal_lost:
                        logger.info("Video signal restored!")
                        self.is_signal_lost = False
                else:
                    self.consecutive_drops += 1
            else:
                self.consecutive_drops += 1

            # Handle signal loss
            if frame is None:
                if not self.is_signal_lost and self.consecutive_drops >= 5:
                    logger.warning("HDMI signal lost or camera disconnected. Showing standby screen...")
                    self.is_signal_lost = True

                # Background reconnection attempt every 2 seconds
                now = time.time()
                if self.is_signal_lost and (now - self.last_reconnect_attempt > 2.0):
                    self.last_reconnect_attempt = now
                    try:
                        if self.cap:
                            self.cap.release()
                        backend = cameras.get_backend()
                        self.cap = cv2.VideoCapture(self.camera_index, backend)
                    except Exception as e:
                        logger.debug(f"Reconnect attempt error: {e}")

                frame = self._generate_standby_frame()

            # Apply overlays
            self._apply_overlays(frame)

            # Store latest raw frame for snapshot
            with self.frame_lock:
                self.latest_frame = frame.copy()

            # Append to circular delay buffer
            with self.buffer_lock:
                self.buffer.append(frame)

            # Pace capture loop
            elapsed = time.time() - start_time
            sleep_needed = target_interval - elapsed
            if sleep_needed > 0:
                time.sleep(sleep_needed)

    def transmit_loop(self):
        """Controls pacing and pops delayed frames for WebRTC and Recording."""
        target_interval = 1.0 / self.actual_fps

        while self.running:
            start_time = time.time()

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
                    self.frame_event.set()
                    self.frame_event.clear()

                # Handle video recording
                if self.is_recording and self.recorder_thread:
                    self.rec_queue.append(delayed_bgr)
                    if not self.thumbnail_saved and self.current_recording_name:
                        self._save_thumbnail(delayed_bgr)
                elif not self.streaming and self.is_recording:
                    self.stop_recording()

                # Interactive window preview (if not headless)
                if not self.headless:
                    display_frame = delayed_bgr.copy()
                    if self.paused:
                        cv2.putText(display_frame, "PAUSED", (self.width - 200, 60), 
                                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 165, 255), 3)
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
            elapsed = time.time() - start_time
            sleep_needed = target_interval - elapsed
            if sleep_needed > 0:
                time.sleep(sleep_needed)

    def set_delay(self, new_delay_seconds: int):
        """Dynamically adjust the delay buffer size without stopping the stream."""
        new_delay_seconds = max(1, min(120, int(new_delay_seconds)))
        new_buffer_size = int(new_delay_seconds * self.actual_fps)
        logger.info(f"Dynamically updating delay: {self.settings.delay_seconds}s -> {new_delay_seconds}s (buffer: {new_buffer_size})")

        with self.buffer_lock:
            self.settings.delay_seconds = new_delay_seconds
            # Recreate deque with new maxlen preserving recent frames
            current_frames = list(self.buffer)
            self.buffer = deque(current_frames, maxlen=new_buffer_size)

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
        if self.is_recording:
            return

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.current_recording_name = timestamp
        filename = os.path.join(RECORDINGS_DIR, f"{timestamp}.mp4")

        self.rec_queue.clear()
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
        if self.recorder_thread:
            self.recorder_thread.stop()
            self.recorder_thread.join(timeout=4.0)
            self.recorder_thread = None
        self.rec_queue.clear()
        self.is_recording = False
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
            "buffer_length": buf_len,
            "buffer_capacity": buf_max,
            "buffer_percent": percent_full,
            "buffer_delay_seconds": self.settings.delay_seconds,
            "estimated_ram_mb": estimated_ram_mb,
            "signal_lost": self.is_signal_lost,
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

        if self.cap and self.cap.isOpened():
            self.cap.release()

        if not self.headless:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass

        logger.info("Stream stopped cleanly.")
