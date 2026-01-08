import cv2
import time
import threading
import os
import datetime
from collections import deque
from fractions import Fraction
from typing import Optional

# NDI Imports
from cyndilib.sender import Sender
from cyndilib.video_frame import VideoSendFrame
from cyndilib.wrapper.ndi_structs import FourCC

# App Imports
from src.settings import StreamSettings
from src.logger import get_logger

logger = get_logger(__name__)

RECORDINGS_DIR = "recordings"
THUMBNAILS_DIR = "thumbnails"

class VideoRecorder(threading.Thread):
    """
    Dedicated thread for writing to disk. 
    Prevents disk lag from freezing the NDI stream.
    """
    def __init__(self, filename, width, height, fps, queue):
        super().__init__()
        self.filename = filename
        self.width = width
        self.height = height
        self.fps = fps
        self.queue = queue
        self.running = True
        self.writer = None

    def run(self):
        # Initialize Writer
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.filename), exist_ok=True)
        
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        self.writer = cv2.VideoWriter(self.filename, fourcc, self.fps, (self.width, self.height))
        
        while self.running or len(self.queue) > 0:
            if len(self.queue) > 0:
                frame = self.queue.popleft()
                if self.writer:
                    self.writer.write(frame)
            else:
                time.sleep(0.01) # Sleep if no frames to write
        
        if self.writer:
            self.writer.release()
            logger.info(f"Video saved: {self.filename}")

    def stop(self):
        self.running = False

class VideoDelayStream:
    def __init__(self, settings: StreamSettings, auto_start=False, headless=False):
        self.settings = settings
        self.running = True
        self.streaming = auto_start
        self.paused = False
        self.headless = headless
        
        # Main Delay Buffer
        self.buffer = deque(maxlen=self.settings.buffer_size)
        
        # Recording State
        self.is_recording = False
        self.rec_queue = deque(maxlen=300) # Limit queue to prevent memory overflow (~10s at 30fps)
        self.recorder_thread: Optional[VideoRecorder] = None
        self.current_recording_name = None
        self.thumbnail_saved = False
        
        self.latest_frame = None
        self.frame_lock = threading.Lock()
        
        # Ensure directories
        os.makedirs(RECORDINGS_DIR, exist_ok=True)
        os.makedirs(THUMBNAILS_DIR, exist_ok=True)
        
        # 1. Setup Camera
        self.cap = cv2.VideoCapture(self.settings.cam_index_primary)
        if not self.cap.isOpened():
            logger.warning(f"Camera {self.settings.cam_index_primary} failed, trying {self.settings.cam_index_secondary}...")
            self.cap = cv2.VideoCapture(self.settings.cam_index_secondary)
            
        if not self.cap.isOpened():
             raise ValueError("Could not open video device.")
        
        # Request Resolution
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.settings.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.settings.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.settings.fps)

        # Get Actual Config
        ret, test_frame = self.cap.read()
        if not ret: raise ValueError("Camera failed to read first frame.")
        h, w, c = test_frame.shape
        self.width, self.height = int(w), int(h)
        self.actual_fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
        
        logger.info(f"Camera initialized: {self.width}x{self.height} @ {self.actual_fps}FPS")
        
        # 2. Setup NDI
        # clock_video=False because we control the timing manually in transmit_loop
        self.sender = Sender(ndi_name=self.settings.source_name, clock_video=False) 
        self.vf = VideoSendFrame()
        self.vf.set_resolution(self.width, self.height)
        self.vf.set_fourcc(FourCC.BGRA)
        self.vf.set_frame_rate(Fraction(30000, 1001)) # ~29.97 fps
        self.sender.set_video_frame(self.vf)
        self.sender.open()
        
        logger.info(f"NDI Stream started: '{self.settings.source_name}' with {self.settings.delay_seconds}s delay.")

    def capture_loop(self):
        """Reads frames continuously. This keeps the camera buffer empty."""
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                # OPTIMIZATION: Simple timestamp overlay
                timestamp = time.strftime("%H:%M:%S")
                # Black Outline
                cv2.putText(frame, timestamp, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 6)
                # White Text
                cv2.putText(frame, timestamp, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)

                # Store reference for snapshot (Thread Safe)
                with self.frame_lock:
                    self.latest_frame = frame.copy()  # Copy to prevent memory leak 

                # Add to delay buffer
                self.buffer.append(frame)
            else:
                time.sleep(0.005)

    def transmit_loop(self):
        """
        Sends frames to NDI and manages Recording.
        Controls the PACING of the stream.
        """
        if not self.headless:
            logger.info("Press 's' to Start Stream, 'p' to Pause, 'q' to Quit.")
        else:
            logger.info("Running in headless mode. Press Ctrl+C to stop.")
            
        target_frame_time = 1.0 / self.actual_fps
        
        while self.running:
            start_time = time.time()
            
            # Only process if we have reached the delay buffer size
            if len(self.buffer) >= self.settings.buffer_size:
                frame_bgr = self.buffer.popleft()
                
                # 1. Handle Recording (Non-Blocking)
                if self.is_recording and self.recorder_thread:
                    self.rec_queue.append(frame_bgr) # Fast append
                    
                    # Save Thumbnail Once
                    if not self.thumbnail_saved and self.current_recording_name:
                        self._save_thumbnail(frame_bgr)
                else:
                    # Auto-stop recording if streaming stopped
                    if not self.streaming and self.is_recording:
                        self.stop_recording()

                # 2. Preview (Only if NOT headless)
                if not self.headless:
                    # Add status overlay
                    display_frame = frame_bgr.copy()
                    if self.paused:
                        cv2.putText(display_frame, "PAUSED", (self.width - 200, 60), 
                                  cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 165, 255), 3)
                    if self.is_recording:
                        cv2.putText(display_frame, "REC", (self.width - 100, self.height - 30), 
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                    
                    cv2.imshow("Preview (Delayed)", display_frame)
                    del display_frame  # Explicitly release frame memory
                    
                    # Handle keyboard input
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

                # 3. NDI Send (The Critical Path)
                if self.streaming and not self.paused:
                    # Convert to BGRA (NDI Requirement)
                    frame_bgra = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2BGRA)
                    self.vf.write_data(frame_bgra.reshape(-1))
                    self.sender.send_video()
            else:
                # Buffer filling up... wait a tiny bit
                time.sleep(0.002)
                continue

            # --- PACER LOGIC ---
            # Sleep remainder of frame time to maintain steady FPS
            processing_time = time.time() - start_time
            sleep_needed = target_frame_time - processing_time
            if sleep_needed > 0:
                time.sleep(sleep_needed)

    def _save_thumbnail(self, frame):
        try:
            filename = os.path.join(THUMBNAILS_DIR, f"{self.current_recording_name}.jpg")
            # Resize to small jpg
            thumb_height = 180
            thumb_width = int(thumb_height * self.width / self.height)
            thumb = cv2.resize(frame, (thumb_width, thumb_height))
            cv2.imwrite(filename, thumb, [cv2.IMWRITE_JPEG_QUALITY, 85])
            self.thumbnail_saved = True
            logger.info(f"Thumbnail saved: {filename}")
        except Exception as e:
            logger.error(f"Thumbnail save failed: {e}")

    def start_recording(self):
        if self.is_recording: return
        
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.current_recording_name = timestamp
        filename = os.path.join(RECORDINGS_DIR, f"{timestamp}.mp4")
        
        # Start the Separate Thread
        self.rec_queue.clear()
        self.recorder_thread = VideoRecorder(filename, self.width, self.height, self.settings.fps, self.rec_queue)
        self.recorder_thread.start()
        
        self.is_recording = True
        self.thumbnail_saved = False
        logger.info(f"Recording started: {filename}")

    def stop_recording(self):
        if self.recorder_thread:
            self.recorder_thread.stop()
            self.recorder_thread.join(timeout=5.0)  # Wait for thread to finish
            self.recorder_thread = None
        # Clear recording queue to free memory
        self.rec_queue.clear()
        self.is_recording = False
        logger.info("Recording stopped.")

    def get_snapshot(self):
        """Thread-safe snapshot for API."""
        with self.frame_lock:
            if self.latest_frame is not None:
                ret, buffer = cv2.imencode('.jpg', self.latest_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ret:
                    return buffer.tobytes()
        return None

    def start(self, blocking=False):
        """Starts threads. 'blocking' arg is kept for compatibility with manager.py"""
        if not hasattr(self, 't_cap') or not self.t_cap.is_alive():
            self.t_cap = threading.Thread(target=self.capture_loop)
            self.t_cap.daemon = True
            self.t_cap.start()

        if not hasattr(self, 't_trans') or not self.t_trans.is_alive():
            self.t_trans = threading.Thread(target=self.transmit_loop)
            self.t_trans.daemon = True
            self.t_trans.start()
            
        logger.info("Stream threads started.")

    def stop(self):
        logger.info("Stopping stream...")
        self.running = False
        if self.is_recording:
            self.stop_recording()
        if self.cap.isOpened():
            self.cap.release()
        if hasattr(self, 'sender') and self.sender:
            self.sender.close()
        if not self.headless:
            cv2.destroyAllWindows()
        logger.info("Stream stopped.")
