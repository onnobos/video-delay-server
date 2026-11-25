import threading
from typing import Optional, Dict, Any
from src.stream import VideoDelayStream
from src.settings import StreamSettings
from src.logger import get_logger
from src import webrtc, cameras

logger = get_logger(__name__)


class StreamManager:
    """
    Manages the lifecycle of VideoDelayStream, handling camera switching,
    dynamic settings updates, and telemetry status reporting.
    """
    def __init__(self):
        self.stream: Optional[VideoDelayStream] = None
        self.settings: StreamSettings = StreamSettings()
        self.is_running = False
        self._lock = threading.RLock()

    def start(self, settings: Optional[StreamSettings] = None):
        with self._lock:
            if self.is_running and self.stream:
                return

            if settings:
                self.settings = settings

            logger.info("Manager: Starting video delay stream...")
            self.stream = VideoDelayStream(settings=self.settings, auto_start=False, headless=True)
            self.stream.start(blocking=False)
            self.is_running = True
            logger.info("Manager: Stream started successfully.")

    def stop(self):
        with self._lock:
            if not self.is_running or not self.stream:
                return

            logger.info("Manager: Stopping video delay stream...")
            self.stream.stop()
            self.stream = None
            self.is_running = False
            logger.info("Manager: Stream stopped.")

    def restart(self, new_settings: StreamSettings):
        with self._lock:
            logger.info("Manager: Restarting with new settings...")
            was_streaming = self.stream.streaming if self.stream else False
            was_recording = self.stream.is_recording if self.stream else False

            self.stop()
            self.settings = new_settings
            self.start(self.settings)

            if was_streaming and self.stream:
                self.start_streaming()
            if was_recording and self.stream:
                self.stream.start_recording()

    def switch_camera(self, camera_index: int):
        """Switch camera device dynamically."""
        with self._lock:
            logger.info(f"Manager: Switching to camera index {camera_index}...")
            self.settings.cam_index = camera_index
            self.settings.cam_index_primary = camera_index

            info = cameras.get_camera_info(camera_index)
            if info:
                self.settings.cam_name = info["name"]

            if self.is_running:
                self.restart(self.settings)

    def set_delay(self, delay_seconds: int):
        """Update delay seconds dynamically without stream restart."""
        with self._lock:
            if self.stream:
                self.stream.set_delay(delay_seconds)
            else:
                self.settings.delay_seconds = max(1, min(120, int(delay_seconds)))

    def set_fps(self, fps: int):
        """Update target FPS dynamically without stream restart."""
        with self._lock:
            if self.stream:
                self.stream.set_fps(fps)
            else:
                self.settings.fps = max(5, min(60, int(fps)))

    def start_streaming(self):
        with self._lock:
            if not self.is_running or not self.stream:
                self.start()

            if self.stream and not self.stream.streaming:
                self.stream.streaming = True
                if not self.stream.is_recording:
                    self.stream.start_recording()
                logger.info("Manager: Streaming activated.")

    def stop_streaming(self):
        with self._lock:
            if self.stream and self.stream.streaming:
                self.stream.streaming = False
                if self.stream.is_recording:
                    self.stream.stop_recording()
                logger.info("Manager: Streaming stopped.")

    def toggle_pause(self) -> bool:
        with self._lock:
            if self.stream:
                self.stream.paused = not self.stream.paused
                logger.info(f"Manager: Stream paused = {self.stream.paused}")
                return self.stream.paused
            return False

    def recover_hardware(self) -> Dict[str, Any]:
        """Force full camera re-discovery and restart capture hardware."""
        with self._lock:
            logger.info("Manager: Performing hardware recovery and camera re-discovery...")
            was_streaming = self.stream.streaming if self.stream else False
            self.stop()
            def_idx, def_name = cameras.find_default_camera()
            self.settings.cam_index = def_idx
            self.settings.cam_index_primary = def_idx
            self.settings.cam_name = def_name
            self.start(self.settings)
            if was_streaming and self.stream:
                self.start_streaming()
            return self.get_status()

    def set_test_pattern(self, enabled: bool) -> Dict[str, Any]:
        """Toggle synthetic moving test pattern mode to isolate WebRTC vs camera issues."""
        with self._lock:
            if not self.is_running or not self.stream:
                self.start()
            if self.stream:
                self.stream.test_pattern_mode = bool(enabled)
                if enabled:
                    self.stream.frame_health = "test_pattern"
                    self.stream.diagnostic_hint = "Synthetic test pattern active"
                else:
                    self.stream.frame_health = "no_signal" if self.stream.is_signal_lost else "healthy"
                    self.stream.diagnostic_hint = ""
                logger.info(f"Manager: Test pattern mode = {self.stream.test_pattern_mode}")
            return self.get_status()

    def get_status(self) -> Dict[str, Any]:
        """Aggregate comprehensive system, stream, and memory telemetry."""
        status: Dict[str, Any] = {
            "process_running": self.is_running,
            "streaming": False,
            "paused": False,
            "recording": False,
            "active_viewers": webrtc.get_active_viewers_count(),
            "settings": self.settings.model_dump(),
            "telemetry": {
                "camera_name": self.settings.cam_name or "Auto (Elgato HD60 S+)",
                "camera_index": self.settings.active_camera_index,
                "buffer_length": 0,
                "buffer_capacity": self.settings.buffer_size,
                "buffer_percent": 0.0,
                "estimated_ram_mb": 0.0,
                "signal_lost": False,
                "frame_health": "stopped",
                "diagnostic_hint": "",
                "test_pattern": False,
                "reconnect_attempts": 0
            }
        }

        if self.stream:
            status["streaming"] = self.stream.streaming
            status["paused"] = self.stream.paused
            status["recording"] = self.stream.is_recording
            status["telemetry"] = self.stream.get_metrics()

        return status
