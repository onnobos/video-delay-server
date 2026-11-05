import platform
import cv2
from typing import List, Dict, Optional, Tuple
from src.logger import get_logger

logger = get_logger(__name__)

PREFERRED_CAPTURE_KEYWORDS = [
    "hd60 s+",
    "hd60",
    "game capture",
    "elgato",
    "cam link",
    "capture",
    "hdmi"
]

def get_backend() -> int:
    """Return platform-appropriate OpenCV backend."""
    if platform.system() == "Darwin":
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_ANY

def list_available_cameras() -> List[Dict]:
    """
    Enumerate all connected video devices with hardware names.
    Uses cv2_enumerate_cameras on macOS/AVFoundation, with fallback to probing.
    """
    cameras: List[Dict] = []
    backend = get_backend()

    # 1. Try cv2_enumerate_cameras
    try:
        from cv2_enumerate_cameras import enumerate_cameras
        found = list(enumerate_cameras(backend))
        for cam in found:
            name_lower = cam.name.lower()
            is_capture = any(kw in name_lower for kw in PREFERRED_CAPTURE_KEYWORDS)
            cameras.append({
                "index": int(cam.index),
                "name": str(cam.name),
                "path": str(cam.path) if hasattr(cam, "path") else "",
                "is_capture_card": is_capture,
                "is_default": False
            })
    except Exception as e:
        logger.warning(f"cv2_enumerate_cameras discovery failed ({e}), falling back to device probing...")

    # 2. Fallback probing if enumerate_cameras returned nothing
    if not cameras:
        for idx in range(6):
            try:
                cap = cv2.VideoCapture(idx, backend)
                if cap.isOpened():
                    ret, _ = cap.read()
                    if ret:
                        cameras.append({
                            "index": idx,
                            "name": f"Camera {idx}",
                            "path": f"device_{idx}",
                            "is_capture_card": False,
                            "is_default": False
                        })
                    cap.release()
            except Exception:
                pass

    # 3. Determine default camera (prioritizing Game Capture HD60 S+)
    if cameras:
        # First priority: matches "HD60 S+" or "HD60"
        default_cam = None
        for cam in cameras:
            n = cam["name"].lower()
            if "hd60 s+" in n or "hd60" in n:
                default_cam = cam
                break
        
        # Second priority: any other capture card / Elgato
        if not default_cam:
            for cam in cameras:
                if cam["is_capture_card"]:
                    default_cam = cam
                    break

        # Fallback priority: index 0
        if not default_cam:
            default_cam = cameras[0]

        default_cam["is_default"] = True
        logger.info(f"Discovered {len(cameras)} cameras. Default: [{default_cam['index']}] {default_cam['name']}")

    return cameras

def find_default_camera() -> Tuple[int, str]:
    """
    Returns (index, name) of the best available camera,
    defaulting to Elgato HD60 S+ if present.
    """
    cams = list_available_cameras()
    for cam in cams:
        if cam.get("is_default"):
            return cam["index"], cam["name"]
    if cams:
        return cams[0]["index"], cams[0]["name"]
    return 0, "Default Camera"

def get_camera_info(index: int) -> Optional[Dict]:
    """Get metadata for a specific camera index."""
    for cam in list_available_cameras():
        if cam["index"] == index:
            return cam
    return None
