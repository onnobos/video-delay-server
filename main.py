import sys
import os
import argparse
import uvicorn
import logging

# Add src to path to ensure imports work correctly if run from root
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.stream import VideoDelayStream
from src.settings import StreamSettings
from src.logger import setup_logger

logger = setup_logger(__name__, log_file='videoserver.log', level=logging.INFO)

def main():
    parser = argparse.ArgumentParser(description="Video Delay Server")
    parser.add_argument("--start", action="store_true", help="Start streaming immediately on launch")
    parser.add_argument("--window", action="store_true", help="Show preview window (interactive controls enabled)")
    parser.add_argument("--api", action="store_true", help="Run as API server (remote control mode)")
    parser.add_argument("--camera", type=int, default=None, help="Camera index (default: auto-detect Elgato HD60 S+)")
    parser.add_argument("--list-cameras", action="store_true", help="List detected cameras and exit")
    parser.add_argument("--delay", type=int, default=5, help="Delay in seconds (default: 5)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="API host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="API port (default: 8000)")
    
    args = parser.parse_args()
    
    if args.list_cameras:
        from src import cameras
        cams = cameras.list_available_cameras()
        print("\nDetected Cameras:")
        for c in cams:
            marker = " [DEFAULT]" if c.get("is_default") else ""
            print(f"  [{c['index']}] {c['name']}{marker}")
        print()
        return

    if args.api:
        from src.qr_helper import display_banner
        display_banner(port=args.port)
        logger.info(f"Starting API Server on {args.host}:{args.port}...")
        uvicorn.run("src.server:app", host=args.host, port=args.port, reload=False, log_level="info")
        return

    # Default is headless (no window) unless --window is passed
    headless = not args.window
    
    # Load settings with optional CLI overrides
    settings = StreamSettings(
        delay_seconds=args.delay,
        cam_index=args.camera
    )
    
    try:
        stream = VideoDelayStream(settings=settings, auto_start=args.start, headless=headless)
        stream.start()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(0)

if __name__ == "__main__":
    main()
