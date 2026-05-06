import cv2
import time
import threading
import os
import datetime
import subprocess
import glob
from collections import deque
from typing import Optional

# App Imports
from src.settings import StreamSettings
from src.logger import get_logger

logger = get_logger(__name__)

RECORDINGS_DIR = "recordings"
THUMBNAILS_DIR = "thumbnails"
HLS_DIR = os.path.join("static", "hls")

class HLSWriter(threading.Thread):
    """
    Dedicated thread that drains a frame queue into an FFmpeg HLS subprocess.
    The transmit loop enqueues frames (non-blocking); this thread writes them
    to FFmpeg stdin at a paced interval, keeping speed=1.0x.
    """
    def __init__(self, width, height, fps_int, hls_dir, segment_duration, list_size):
        super().__init__(daemon=True)
        self.width = width
        self.height = height
        self.fps_int = fps_int
        self.hls_dir = hls_dir
        self.segment_duration = segment_duration
        self.list_size = list_size
        self._queue = deque(maxlen=fps_int)  # 1s safety buffer; drops oldest if full
        self._running = True
        self.proc = None

    ENCODE_WIDTH = 854
    ENCODE_HEIGHT = 480

    def run(self):
        hls_dir_abs = os.path.abspath(self.hls_dir)
        playlist_path = os.path.join(hls_dir_abs, "stream.m3u8")
        segment_pattern = os.path.join(hls_dir_abs, "seg%05d.ts")
        gop_size = self.fps_int * self.segment_duration

        for f in glob.glob(os.path.join(hls_dir_abs, "*.ts")) + glob.glob(os.path.join(hls_dir_abs, "*.m3u8")):
            try:
                os.remove(f)
            except OSError:
                pass

        cmd = [
            "ffmpeg", "-y",
            "-f", "mjpeg",
            "-r", str(self.fps_int),
            "-i", "pipe:0",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-profile:v", "baseline",
            "-level:v", "3.1",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-g", str(gop_size),
            "-sc_threshold", "0",
            "-hls_time", str(self.segment_duration),
            "-hls_list_size", "20",
            "-hls_flags", "delete_segments",
            "-hls_allow_cache", "0",
            "-hls_segment_filename", segment_pattern,
            playlist_path,
        ]

        ffmpeg_log = open(os.path.join("logs", "ffmpeg.log"), "w")
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=ffmpeg_log,
            stderr=ffmpeg_log,
        )
        logger.info(f"HLSWriter started (PID {self.proc.pid}), {self.fps_int}fps, GOP={gop_size}.")

        while self._running:
            if self._queue:
                frame = self._queue.popleft()
                small = cv2.resize(frame, (HLSWriter.ENCODE_WIDTH, HLSWriter.ENCODE_HEIGHT))
                ret, jpg = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if not ret:
                    continue
                try:
                    self.proc.stdin.write(jpg.tobytes())
                except (BrokenPipeError, OSError) as e:
                    logger.warning(f"HLSWriter pipe error: {e}")
                    self._running = False
            else:
                time.sleep(0.001)

        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5.0)
        except Exception as e:
            logger.warning(f"HLSWriter stop error: {e}")
            self.proc.kill()
        logger.info("HLSWriter stopped.")

    def write(self, frame_bgr):
        """Enqueue a frame (non-blocking, called from transmit loop)."""
        self._queue.append(frame_bgr)

    def stop(self):
        self._running = False


class VideoRecorder(threading.Thread):
    """
    Dedicated thread for writing to disk. 
    Prevents disk lag from freezing the HLS stream.
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
        os.makedirs(os.path.dirname(self.filename), exist_ok=True)

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        self.writer = cv2.VideoWriter(self.filename, fourcc, self.fps, (self.width, self.height))

        frame_interval = 1.0 / self.fps
        next_write = time.monotonic()

        while self.running or len(self.queue) > 0:
            now = time.monotonic()
            if now < next_write:
                time.sleep(next_write - now)
            next_write += frame_interval

            if len(self.queue) > 0:
                frame = self.queue.popleft()
                if self.writer:
                    self.writer.write(frame)

        if self.writer:
            self.writer.release()
            logger.info(f"Video saved: {self.filename}")

    def stop(self):
        self.running = False

class VideoDelayStream:
    def __init__(self, settings: StreamSettings, auto_start=False, headless=False):
        logger.info("VideoDelayStream.__init__ started")
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
        self.delayed_frame = None
        self.frame_lock = threading.Lock()

        # HLS writer thread (started lazily when streaming begins)
        self.hls_writer: Optional[HLSWriter] = None

        # Ensure directories
        os.makedirs(RECORDINGS_DIR, exist_ok=True)
        os.makedirs(THUMBNAILS_DIR, exist_ok=True)
        os.makedirs(HLS_DIR, exist_ok=True)
        
        # 1. Setup Camera
        logger.info("Opening camera...")
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
        del test_frame  # Release test frame memory
        
        # Measure actual FPS by capturing frames for 2 seconds
        logger.info("Measuring camera FPS...")
        frame_count = 0
        start_time = time.time()
        measurement_duration = 2.0
        
        while time.time() - start_time < measurement_duration:
            ret, frame = self.cap.read()
            if ret:
                frame_count += 1
                del frame  # Explicitly release frame memory
        
        elapsed = time.time() - start_time
        measured_fps = frame_count / elapsed
        requested_fps = self.settings.fps
        
        # Use config FPS if camera can deliver it, otherwise use camera's max capability
        logger.info(f"Camera initialized: {self.width}x{self.height}")
        logger.info(f"Requested FPS: {requested_fps}")
        logger.info(f"Measured camera FPS: {measured_fps:.2f}")
        if measured_fps >= requested_fps:
            self.actual_fps = requested_fps
            logger.info(f"Using FPS: {self.actual_fps} (config value - camera can deliver)")
        elif measured_fps > 1.0:
            self.actual_fps = round(measured_fps, 1)
            logger.info(f"Using FPS: {self.actual_fps} (camera maximum - cannot reach {requested_fps})")
        else:
            self.actual_fps = requested_fps
            logger.warning(f"FPS measurement unreliable ({measured_fps:.2f}); falling back to requested FPS: {requested_fps}")
        
        logger.info(f"HLS stream ready: {self.width}x{self.height} @ {self.actual_fps} FPS, {self.settings.delay_seconds}s delay.")

    def capture_loop(self):
        """Reads frames continuously. This keeps the camera buffer empty."""
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                # Timestamp overlay (top-left)
                timestamp = time.strftime("%H:%M:%S")
                cv2.putText(frame, timestamp, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 6)
                cv2.putText(frame, timestamp, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)

                # FPS overlay (top-right)
                fps_text = f"{int(self.actual_fps)} FPS"
                (tw, th), _ = cv2.getTextSize(fps_text, cv2.FONT_HERSHEY_SIMPLEX, 1.2, 2)
                fps_x = self.width - tw - 30
                cv2.putText(frame, fps_text, (fps_x, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 6)
                cv2.putText(frame, fps_text, (fps_x, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)

                # Store reference for snapshot (Thread Safe)
                with self.frame_lock:
                    self.latest_frame = frame.copy()  # Copy to prevent memory leak 

                # Add to delay buffer
                self.buffer.append(frame)
            else:
                time.sleep(0.005)

    def transmit_loop(self):
        """
        Sends frames to HLS and manages Recording.
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

                # Store latest delayed frame for WebRTC
                with self.frame_lock:
                    self.delayed_frame = frame_bgr

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

                # HLS disabled — WebRTC handles streaming
                pass
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

    def _stop_hls_writer(self):
        """Stop the HLS writer and FFmpeg process."""
        if self.hls_writer:
            self.hls_writer.stop()
            self.hls_writer.join(timeout=8.0)
            self.hls_writer = None
            logger.info("HLS writer stopped.")

    def get_hls_playlist_path(self) -> str:
        """Return the relative URL path to the HLS playlist."""
        return "/hls/stream.m3u8"

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
        self.recorder_thread = VideoRecorder(filename, self.width, self.height, self.actual_fps, self.rec_queue)
        self.recorder_thread.start()
        
        self.is_recording = True
        self.thumbnail_saved = False
        logger.info(f"Recording started: {filename} @ {self.actual_fps} FPS")

    def stop_recording(self):
        if self.recorder_thread:
            self.recorder_thread.stop()
            self.recorder_thread.join(timeout=5.0)  # Wait for thread to finish
            self.recorder_thread = None
        # Clear recording queue to free memory
        self.rec_queue.clear()
        self.is_recording = False
        logger.info("Recording stopped.")

    def get_delayed_frame(self):
        """Return the current delayed frame for WebRTC, or None if not ready."""
        with self.frame_lock:
            return self.delayed_frame

    def get_snapshot(self):
        """Thread-safe snapshot for API. Uses delayed frame at full 1280x720, quality 95."""
        with self.frame_lock:
            frame = self.delayed_frame if self.delayed_frame is not None else self.latest_frame
            if frame is not None:
                ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
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
        
        # Wait for threads to finish before releasing resources
        if hasattr(self, 't_cap') and self.t_cap.is_alive():
            self.t_cap.join(timeout=2.0)
        if hasattr(self, 't_trans') and self.t_trans.is_alive():
            self.t_trans.join(timeout=2.0)
        
        if self.is_recording:
            self.stop_recording()
        self._stop_hls_writer()
        if self.cap.isOpened():
            self.cap.release()
        if not self.headless:
            cv2.destroyAllWindows()
        logger.info("Stream stopped.")
