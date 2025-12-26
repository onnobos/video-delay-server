import cv2
import time
import threading
import os
import datetime
from collections import deque
from fractions import Fraction
from cyndilib.sender import Sender
from cyndilib.video_frame import VideoSendFrame
from cyndilib.wrapper.ndi_structs import FourCC

from src.settings import StreamSettings
from src.logger import get_logger

logger = get_logger(__name__)

RECORDINGS_DIR = "recordings"
THUMBNAILS_DIR = "thumbnails"

class VideoDelayStream:
    def __init__(self, settings: StreamSettings, auto_start=False, headless=False):
        self.settings = settings
        self.running = True
        self.streaming = auto_start  # If auto_start is True, start streaming immediately
        self.paused = False     # 'p' to pause
        self.headless = headless
        self.buffer = deque(maxlen=self.settings.buffer_size)
        self.writer = None
        self.is_recording = False
        self.current_recording_name = None
        self.thumbnail_saved = False
        self.latest_frame = None
        self.frame_lock = threading.Lock()
        
        # Ensure recordings and thumbnails directories exist
        if not os.path.exists(RECORDINGS_DIR):
            os.makedirs(RECORDINGS_DIR)
        if not os.path.exists(THUMBNAILS_DIR):
            os.makedirs(THUMBNAILS_DIR)
        
        # 1. Setup Camera
        self.cap = cv2.VideoCapture(self.settings.cam_index_primary)
        if not self.cap.isOpened():
            logger.info(f"Camera {self.settings.cam_index_primary} failed, trying index {self.settings.cam_index_secondary}...")
            self.cap = cv2.VideoCapture(self.settings.cam_index_secondary)
            
        if not self.cap.isOpened():
            raise ValueError("Could not open video device. Check Permissions in System Settings > Privacy > Camera.")

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.settings.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.settings.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.settings.fps)

        # --- Verify Camera Resolution ---
        ret, test_frame = self.cap.read()
        if not ret:
            raise ValueError("Could not read a frame from camera immediately after opening. Check device.")
        
        h, w, c = test_frame.shape
        logger.info(f"Camera initialized. Request: {self.settings.width}x{self.settings.height} @ {self.settings.fps}fps. Actual: {w}x{h} @ {self.cap.get(cv2.CAP_PROP_FPS)}fps")
        
        # Use ACTUAL dimensions for NDI to avoid buffer mismatch crashes
        self.width = int(w)
        self.height = int(h)
        
        # 2. Setup NDI Sender
        try:
            self.sender = Sender(ndi_name=self.settings.source_name, clock_video=True)
            
            # Configure VideoSendFrame via setters (constructor args are ignored)
            self.vf = VideoSendFrame()
            self.vf.set_resolution(self.width, self.height)
            self.vf.set_fourcc(FourCC.BGRA)
            self.vf.set_frame_rate(Fraction(30000, 1001))
            
            # Attach the frame to the sender BEFORE opening
            self.sender.set_video_frame(self.vf)
            
            self.sender.open()
            logger.info(f"NDI Stream started: '{self.settings.source_name}' with {self.settings.delay_seconds}s delay.")
            
        except Exception as e:
            logger.error(f"Error during NDI setup: {e}")
            raise e

    def capture_loop(self):
        """Reads frames continuously to keep camera buffer empty."""
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                # Add timestamp overlay at capture time (before buffering)
                timestamp = time.strftime("%H:%M:%S")
                # Black outline
                cv2.putText(frame, timestamp, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 6)
                # White text
                cv2.putText(frame, timestamp, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
                
                # Store latest frame for snapshot feature (thread-safe)
                with self.frame_lock:
                    self.latest_frame = frame.copy()
                
                self.buffer.append(frame)
            else:
                time.sleep(0.001)

    def start_recording(self):
        if self.is_recording:
            return

        # Filename: yyyy-mm-dd_HH-MM-SS.mp4
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.current_recording_name = timestamp
        filename = os.path.join(RECORDINGS_DIR, f"{timestamp}.mp4")
        
        # Define codec and create VideoWriter
        fourcc = cv2.VideoWriter_fourcc(*'mp4v') 
        self.writer = cv2.VideoWriter(filename, fourcc, self.settings.fps, (self.width, self.height))
        
        self.is_recording = True
        self.thumbnail_saved = False
        logger.info(f"Recording started: {filename}")

    def stop_recording(self):
        if self.writer:
            self.writer.release()
            self.writer = None
        self.is_recording = False
        logger.info("Recording stopped.")

    def transmit_loop(self):
        """Sends the oldest frame via NDI and handles user input."""
        if not self.headless:
            logger.info("Press 's' to Start Stream, 'p' to Pause, 'q' to Quit.")
        else:
            logger.info("Running in headless mode. Press Ctrl+C to stop.")
        
        while self.running:
            if len(self.buffer) >= self.settings.buffer_size:
                frame_bgr = self.buffer.popleft() # Get oldest frame
                
                if frame_bgr is None or frame_bgr.size == 0:
                    continue

                # Safety check for dimensions
                h, w = frame_bgr.shape[:2]
                if w != self.width or h != self.height:
                    frame_bgr = cv2.resize(frame_bgr, (self.width, self.height))
                
                # --- Recording Logic ---
                # Record only if streaming and not paused
                if self.streaming and not self.paused:
                    if not self.is_recording:
                        self.start_recording()
                    if self.writer:
                        self.writer.write(frame_bgr)
                        
                        # Generate thumbnail from first frame
                        if not self.thumbnail_saved and self.current_recording_name:
                            thumbnail_path = os.path.join(THUMBNAILS_DIR, f"{self.current_recording_name}.jpg")
                            # Resize for thumbnail (320x180 for 16:9)
                            thumb_height = 180
                            thumb_width = int(thumb_height * self.width / self.height)
                            thumbnail = cv2.resize(frame_bgr, (thumb_width, thumb_height))
                            cv2.imwrite(thumbnail_path, thumbnail, [cv2.IMWRITE_JPEG_QUALITY, 85])
                            self.thumbnail_saved = True
                            logger.info(f"Thumbnail saved: {thumbnail_path}")
                else:
                    # If we stop streaming or pause, stop/pause recording?
                    # For now, let's stop recording if we stop streaming.
                    # If just paused, we technically just stop writing frames, which effectively pauses the video file too.
                    if not self.streaming and self.is_recording:
                        self.stop_recording()

                # --- Status Overlay (Only needed if showing preview) ---
                if not self.headless:
                    if self.paused:
                        cv2.putText(frame_bgr, "PAUSED", (30, 120), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                    elif not self.streaming:
                        cv2.putText(frame_bgr, "STANDBY - Press 's' to Stream", (30, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
                    elif self.is_recording:
                         cv2.putText(frame_bgr, "REC", (self.width - 100, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

                    # Show Preview
                    cv2.imshow("Preview (Delayed)", frame_bgr)

                    # Handle Input
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord('q'):
                        self.running = False
                        break
                    elif key == ord('s'):
                        if not self.streaming:
                            self.streaming = True
                            logger.info("Streaming STARTED.")
                    elif key == ord('p'):
                        self.paused = not self.paused
                        state = "PAUSED" if self.paused else "RESUMED"
                        logger.info(f"Stream {state}.")

                # --- NDI Transmission ---
                if self.streaming and not self.paused:
                    # Convert BGR (OpenCV) to BGRA (NDI expects Alpha channel)
                    frame_bgra = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2BGRA)
                    
                    # Write data to the pre-allocated VideoFrame object
                    # NDI expects a flat 1D buffer, so we flatten the numpy array
                    self.vf.write_data(frame_bgra.reshape(-1))
                    
                    # Tell sender to broadcast the current frame
                    self.sender.send_video()
            else:
                # Fill buffer: tiny sleep to prevent CPU spinning
                time.sleep(0.005) 

    def start(self, blocking=True):
        t_cap = threading.Thread(target=self.capture_loop)
        t_cap.daemon = True
        t_cap.start()

        if blocking:
            try:
                self.transmit_loop()
            except KeyboardInterrupt:
                self.stop()
        else:
            t_trans = threading.Thread(target=self.transmit_loop)
            t_trans.daemon = True
            t_trans.start()

    def get_snapshot(self):
        """
        Capture and return the latest frame as JPEG bytes.
        Returns None if no frame is available.
        """
        with self.frame_lock:
            if self.latest_frame is not None:
                # Encode frame as JPEG
                ret, buffer = cv2.imencode('.jpg', self.latest_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ret:
                    return buffer.tobytes()
        return None

    def stop(self):
        self.running = False
        if self.is_recording:
            self.stop_recording()
        if self.cap.isOpened():
            self.cap.release()
        if hasattr(self, 'sender'):
            self.sender.close()
        cv2.destroyAllWindows()
        logger.info("Stream stopped.")
