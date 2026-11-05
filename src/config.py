# --- CONFIGURATION ---
DELAY_SECONDS = 5
FPS = 30
WIDTH = 1280
HEIGHT = 720

# Camera defaults (auto-discovered dynamically if not specified)
CAM_INDEX_PRIMARY = 1
CAM_INDEX_SECONDARY = 0

# Video Overlay Defaults
SHOW_TIMESTAMP = True
SHOW_FPS = True
SHOW_CAMERA_NAME = False

# Buffer memory safety
MAX_DELAY_SECONDS = 120
DEFAULT_BUFFER_SIZE = DELAY_SECONDS * FPS
