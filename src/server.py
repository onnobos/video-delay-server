import os
import glob
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.config import BASE_DIR, RECORDINGS_DIR, THUMBNAILS_DIR
from src.manager import StreamManager
from src.settings import StreamSettings
from src.logger import get_logger
from src import webrtc, cameras, diagnostics

logger = get_logger(__name__)


class StatusEndpointFilter(logging.Filter):
    """Suppress noisy 200 OK polling logs for GET /status."""
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not ("GET /status " in msg and " 200" in msg)


manager = StreamManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, StatusEndpointFilter) for f in access_logger.filters):
        access_logger.addFilter(StatusEndpointFilter())
    yield
    await webrtc.close_all()
    manager.stop()


app = FastAPI(title="Video Delay Server API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Project directory paths
STATIC_DIR = os.path.join(BASE_DIR, "static")

os.makedirs(RECORDINGS_DIR, exist_ok=True)
os.makedirs(THUMBNAILS_DIR, exist_ok=True)

# Mount static asset routes
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/recordings", StaticFiles(directory=RECORDINGS_DIR), name="recordings")
app.mount("/thumbnails", StaticFiles(directory=THUMBNAILS_DIR), name="thumbnails")


class WebRTCOffer(BaseModel):
    sdp: str
    type: str


class CameraSelectRequest(BaseModel):
    index: int


class SettingsUpdateRequest(BaseModel):
    delay_seconds: Optional[int] = Field(default=None, ge=1, le=120)
    fps: Optional[int] = Field(default=None, ge=5, le=60)
    show_timestamp: Optional[bool] = None
    show_fps: Optional[bool] = None
    show_camera_name: Optional[bool] = None


class TestPatternRequest(BaseModel):
    enabled: bool


@app.get("/")
def root():
    """Redirect to the control panel."""
    return RedirectResponse(url="/static/index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Return 204 No Content to eliminate browser favicon 404 log noise."""
    return Response(status_code=204)


# --- WEBRTC ---
@app.post("/offer")
async def webrtc_offer(offer: WebRTCOffer):
    """Accept WebRTC offer from browser, return answer."""
    if not manager.is_running or not manager.stream:
        # Auto-initialize stream off the event loop so camera init doesn't stall WebRTC
        await asyncio.to_thread(manager.start)
    answer = await webrtc.handle_offer(offer.sdp, offer.type, lambda: manager.stream)
    return answer


# --- CAMERAS ---
@app.get("/cameras")
def get_cameras():
    """List all detected camera hardware with friendly names and default marker."""
    cams = cameras.list_available_cameras()
    if manager.stream is not None and manager.is_running:
        active_idx = manager.stream.camera_index
    else:
        active_idx = manager.settings.active_camera_index
        if active_idx is None:
            for c in cams:
                if c.get("is_default"):
                    active_idx = c["index"]
                    break

    return {
        "cameras": cams,
        "active_index": active_idx
    }


@app.post("/cameras/select")
def select_camera(req: CameraSelectRequest):
    """Switch active capture device dynamically."""
    try:
        manager.switch_camera(req.index)
        return {
            "message": f"Switched to camera {req.index}",
            "status": manager.get_status()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- STREAM & SERVER CONTROLS ---
@app.get("/status")
def get_status():
    return manager.get_status()


@app.post("/server/start")
def start_server():
    if manager.is_running:
        return {"message": "Server process is already running", "status": manager.get_status()}
    manager.start()
    return {"message": "Server process started", "status": manager.get_status()}


@app.post("/server/stop")
def stop_server():
    manager.stop()
    return {"message": "Server process stopped", "status": manager.get_status()}


@app.post("/server/restart")
def restart_server(settings: StreamSettings):
    try:
        manager.restart(settings)
        return {"message": "Server restarted with new settings", "status": manager.get_status()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/stream/start")
def start_stream():
    """Start stream playback and recording."""
    if not manager.is_running:
        manager.start()
    manager.start_streaming()
    return {"message": "Streaming started", "status": manager.get_status()}


@app.post("/stream/stop")
def stop_stream():
    if not manager.is_running:
        raise HTTPException(status_code=400, detail="Server is not running.")
    manager.stop_streaming()
    return {"message": "Streaming stopped", "status": manager.get_status()}


@app.post("/stream/pause")
def pause_stream():
    if not manager.is_running or not manager.stream:
        raise HTTPException(status_code=400, detail="Stream is not active.")
    manager.stream.paused = True
    return {"message": "Stream paused", "status": manager.get_status()}


@app.post("/stream/resume")
def resume_stream():
    if not manager.is_running or not manager.stream:
        raise HTTPException(status_code=400, detail="Stream is not active.")
    manager.stream.paused = False
    return {"message": "Stream resumed", "status": manager.get_status()}


# --- SETTINGS & SNAPSHOT ---
@app.get("/settings")
def get_settings():
    return manager.settings


@app.patch("/settings")
def update_settings(req: SettingsUpdateRequest):
    """Dynamically update settings without restarting the camera hardware."""
    if req.delay_seconds is not None:
        manager.set_delay(req.delay_seconds)
    if req.fps is not None:
        manager.set_fps(req.fps)
    if req.show_timestamp is not None:
        manager.settings.show_timestamp = req.show_timestamp
    if req.show_fps is not None:
        manager.settings.show_fps = req.show_fps
    if req.show_camera_name is not None:
        manager.settings.show_camera_name = req.show_camera_name

    return {"message": "Settings updated", "status": manager.get_status()}


@app.get("/snapshot")
def get_snapshot():
    """Capture and return the current frame as a high-quality JPEG."""
    if not manager.is_running or not manager.stream:
        raise HTTPException(status_code=400, detail="Stream is not active.")

    snapshot_bytes = manager.stream.get_snapshot()
    if snapshot_bytes is None:
        raise HTTPException(status_code=503, detail="No frame available. Camera may not be capturing.")

    return Response(content=snapshot_bytes, media_type="image/jpeg")


# --- HEADLESS DIAGNOSTICS & SELF-HEALING ---
@app.get("/diagnostics")
def get_diagnostics():
    """Run 6-point headless diagnostics (TCC permission, USB speed, frame health, storage)."""
    return diagnostics.run_full_diagnostics(manager)


@app.get("/logs")
def get_server_logs(lines: int = 80):
    """Return recent lines from application and error logs for mobile troubleshooting."""
    return diagnostics.get_recent_logs(lines=lines)


@app.post("/diagnostics/recover")
def recover_hardware():
    """Reset and re-discover camera hardware from remote browser."""
    try:
        status = manager.recover_hardware()
        return {"message": "Camera hardware reset and re-initialized", "status": status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/diagnostics/test-pattern")
def toggle_test_pattern(req: TestPatternRequest):
    """Enable or disable synthetic moving test pattern to isolate WebRTC vs camera issues."""
    status = manager.set_test_pattern(req.enabled)
    return {
        "message": f"Test pattern {'enabled' if req.enabled else 'disabled'}",
        "status": status
    }


@app.post("/system/restart-mac")
async def restart_mac():
    """Gracefully flush recordings, close WebRTC peers, and restart macOS."""
    await webrtc.close_all()
    result = diagnostics.trigger_host_power_action("restart", manager)
    return {
        "message": "Gracefully stopping stream and restarting Mac...",
        "action": "restart",
        "result": result,
    }


@app.post("/system/shutdown-mac")
async def shutdown_mac():
    """Gracefully flush recordings, close WebRTC peers, and shut down macOS."""
    await webrtc.close_all()
    result = diagnostics.trigger_host_power_action("shutdown", manager)
    return {
        "message": "Gracefully stopping stream and shutting down Mac...",
        "action": "shutdown",
        "result": result,
    }


# --- RECORDINGS GALLERY ---
@app.get("/videos")
def list_videos():
    """List all recorded MP4 videos with duration, size, and thumbnails."""
    if not os.path.exists(RECORDINGS_DIR):
        return {"videos": []}

    videos = []
    for video_file in sorted(glob.glob(os.path.join(RECORDINGS_DIR, "*.mp4")), reverse=True):
        filename = os.path.basename(video_file)
        name_without_ext = os.path.splitext(filename)[0]
        thumbnail_file = os.path.join(THUMBNAILS_DIR, f"{name_without_ext}.jpg")

        try:
            stat = os.stat(video_file)
            videos.append({
                "filename": filename,
                "name": name_without_ext,
                "size": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                "url": f"/recordings/{filename}",
                "thumbnail": f"/thumbnails/{name_without_ext}.jpg" if os.path.exists(thumbnail_file) else None
            })
        except OSError:
            pass

    return {"videos": videos}


@app.delete("/videos/{filename}")
def delete_video(filename: str):
    """Delete a recording and its corresponding thumbnail."""
    # Sanitize filename
    safe_name = os.path.basename(filename)
    video_path = os.path.join(RECORDINGS_DIR, safe_name)
    name_without_ext = os.path.splitext(safe_name)[0]
    thumb_path = os.path.join(THUMBNAILS_DIR, f"{name_without_ext}.jpg")

    if not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail="Video not found")

    try:
        os.remove(video_path)
        if os.path.exists(thumb_path):
            os.remove(thumb_path)
        return {"message": f"Deleted {safe_name}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
