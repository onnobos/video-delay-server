from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, FileResponse, Response
from src.manager import StreamManager
from src.settings import StreamSettings
from src.logger import get_logger
import os
import glob
from datetime import datetime

logger = get_logger(__name__)

app = FastAPI(title="Video Delay Server API")
manager = StreamManager()

# Mount static files directory
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Mount recordings directory for video playback
recordings_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "recordings")
if os.path.exists(recordings_dir):
    app.mount("/recordings", StaticFiles(directory=recordings_dir), name="recordings")

# Mount thumbnails directory
thumbnails_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "thumbnails")
if not os.path.exists(thumbnails_dir):
    os.makedirs(thumbnails_dir)
app.mount("/thumbnails", StaticFiles(directory=thumbnails_dir), name="thumbnails")

@app.get("/")
def root():
    """Redirect to the control panel."""
    return RedirectResponse(url="/static/index.html")

@app.on_event("startup")
def startup_event():
    # Auto-start manager in standby mode if desired, or just wait for command
    # For now, we start the manager process so it's ready, but streaming is off by default
    manager.start()

@app.on_event("shutdown")
def shutdown_event():
    manager.stop()

@app.get("/status")
def get_status():
    return manager.get_status()

@app.post("/server/start")
def start_server():
    if manager.is_running:
        return {"message": "Server process is already running"}
    manager.start()
    return {"message": "Server process started", "status": manager.get_status()}

@app.post("/server/stop")
def stop_server():
    manager.stop()
    return {"message": "Server process stopped", "status": manager.get_status()}

@app.post("/server/restart")
def restart_server(settings: StreamSettings):
    """Restart the server with new settings."""
    try:
        manager.restart(settings)
        return {"message": "Server restarted with new settings", "status": manager.get_status()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/stream/start")
def start_stream():
    """Start NDI streaming and recording."""
    if not manager.is_running:
        raise HTTPException(status_code=400, detail="Server process is not running. Call /server/start first.")
    manager.start_streaming()
    return {"message": "Streaming started", "status": manager.get_status()}

@app.post("/stream/stop")
def stop_stream():
    """Stop NDI streaming and recording."""
    if not manager.is_running:
         raise HTTPException(status_code=400, detail="Server process is not running.")
    manager.stop_streaming()
    return {"message": "Streaming stopped", "status": manager.get_status()}

@app.post("/stream/pause")
def pause_stream():
    if not manager.is_running:
         raise HTTPException(status_code=400, detail="Server process is not running.")
    if not manager.stream:
        raise HTTPException(status_code=400, detail="Stream is not active.")
        
    manager.stream.paused = True
    return {"message": "Stream paused", "status": manager.get_status()}

@app.post("/stream/resume")
def resume_stream():
    if not manager.is_running:
         raise HTTPException(status_code=400, detail="Server process is not running.")
    if not manager.stream:
        raise HTTPException(status_code=400, detail="Stream is not active.")

    manager.stream.paused = False
    return {"message": "Stream resumed", "status": manager.get_status()}

@app.get("/settings")
def get_settings():
    return manager.settings

@app.get("/snapshot")
def get_snapshot():
    """Capture and return the current frame as a JPEG image."""
    if not manager.is_running:
        raise HTTPException(status_code=400, detail="Server process is not running.")
    if not manager.stream:
        raise HTTPException(status_code=400, detail="Stream is not active.")
    
    snapshot_bytes = manager.stream.get_snapshot()
    if snapshot_bytes is None:
        raise HTTPException(status_code=503, detail="No frame available. Camera may not be capturing.")
    
    return Response(content=snapshot_bytes, media_type="image/jpeg")

@app.get("/videos")
def list_videos():
    """List all recorded videos with metadata."""
    recordings_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "recordings")
    thumbnails_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "thumbnails")
    
    if not os.path.exists(recordings_path):
        return {"videos": []}
    
    videos = []
    for video_file in sorted(glob.glob(os.path.join(recordings_path, "*.mp4")), reverse=True):
        filename = os.path.basename(video_file)
        name_without_ext = os.path.splitext(filename)[0]
        thumbnail_file = os.path.join(thumbnails_path, f"{name_without_ext}.jpg")
        
        stat = os.stat(video_file)
        videos.append({
            "filename": filename,
            "name": name_without_ext,
            "size": stat.st_size,
            "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
            "url": f"/recordings/{filename}",
            "thumbnail": f"/thumbnails/{name_without_ext}.jpg" if os.path.exists(thumbnail_file) else None
        })
    
    return {"videos": videos}
