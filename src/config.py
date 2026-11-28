import os

# --- PATHS ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDINGS_DIR = os.path.join(BASE_DIR, "recordings")
THUMBNAILS_DIR = os.path.join(BASE_DIR, "thumbnails")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

# --- CONFIGURATION ---
DELAY_SECONDS = 5
FPS = 30
WIDTH = 1280
HEIGHT = 720

# Camera defaults (None = auto-discover best capture card, e.g. Elgato HD60 S+)
CAM_INDEX_PRIMARY = None
CAM_INDEX_SECONDARY = 0

# Video Overlay Defaults
SHOW_TIMESTAMP = True
SHOW_FPS = True
SHOW_CAMERA_NAME = False

# Capture & Recording Defaults
RECORD_ON_STREAM = True
DOWNSCALE_OVERSIZED = True
AUTO_START_STREAM = False
AUTO_START_STREAM_FILE = os.path.join(BASE_DIR, ".auto_start_stream")

# Buffer memory safety
MAX_DELAY_SECONDS = 120
DEFAULT_BUFFER_SIZE = DELAY_SECONDS * FPS
