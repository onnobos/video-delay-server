from typing import Optional
from src.stream import VideoDelayStream
from src.settings import StreamSettings
from src.logger import get_logger

logger = get_logger(__name__)

class StreamManager:
    def __init__(self):
        self.stream: Optional[VideoDelayStream] = None
        self.settings: StreamSettings = StreamSettings()
        self.is_running = False

    def start(self, settings: Optional[StreamSettings] = None):
        if self.is_running:
            return
        
        if settings:
            self.settings = settings
            
        # Initialize stream with current settings
        # We force headless=True for API-managed streams to avoid thread blocking/GUI issues on server
        self.stream = VideoDelayStream(settings=self.settings, auto_start=False, headless=True)
        self.stream.start(blocking=False)
        self.is_running = True
        logger.info("Manager: Stream process started.")

    def stop(self):
        if not self.is_running or not self.stream:
            return
            
        self.stream.stop()
        self.stream = None
        self.is_running = False
        logger.info("Manager: Stream process stopped.")

    def restart(self, new_settings: StreamSettings):
        logger.info("Manager: Restarting with new settings...")
        self.stop()
        self.start(new_settings)

    def start_streaming(self):
        if self.stream and not self.stream.streaming:
            self.stream.streaming = True
            # Automatically start recording when streaming starts
            if not self.stream.is_recording:
                self.stream.start_recording()
            logger.info("Manager: Streaming activated.")

    def stop_streaming(self):
        if self.stream and self.stream.streaming:
            self.stream.streaming = False
            # Automatically stop recording when streaming stops
            if self.stream.is_recording:
                self.stream.stop_recording()
            logger.info("Manager: Streaming deactivated.")

    def toggle_pause(self):
        if self.stream:
            self.stream.paused = not self.stream.paused
            return self.stream.paused
        return False

    def get_status(self):
        status = {
            "process_running": self.is_running,
            "streaming": False,
            "paused": False,
            "recording": False,
            "settings": self.settings.dict()
        }
        
        if self.stream:
            status["streaming"] = self.stream.streaming
            status["paused"] = self.stream.paused
            status["recording"] = self.stream.is_recording
            
        return status
