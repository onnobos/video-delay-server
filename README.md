# Video Delay Server

A Python application that captures video from a webcam, buffers it for a configurable delay (default 5 seconds), and streams it live to any browser via **WebRTC** — with sub-second overhead on top of the configured delay. Clients on any device (iPhone, iPad, laptop) can watch the delayed stream directly in their browser with no app install. Features include automatic recording, thumbnail generation, web-based remote control, snapshot capture, and comprehensive logging.

## Features

### Core Functionality
- **Video Capture**: Supports generic USB webcams and macOS Continuity Camera
- **Configurable Delay Buffer**: Creates a "live replay" effect (default 5 seconds)
- **WebRTC Streaming**: Ultra-low overhead delivery via `aiortc` — no HLS segmenting delays
- **Full Page Player**: Dedicated fullscreen player page for iPhone/iPad/desktop
- **Timestamp & FPS Overlay**: Shows capture time and FPS on each frame to demonstrate delay
- **Automatic Recording**: Records streams to MP4 files with timestamp-based filenames
- **Thumbnail Generation**: Creates thumbnails from the first frame of each recording

### Control & Management
- **Web Control Panel**: Mobile-friendly interface for remote control
- **Snapshot Capture**: Full resolution (1280×720) JPEG snapshots of the delayed stream
- **Video Gallery**: Browse and download recorded videos with thumbnails
- **REST API**: Full programmatic control via HTTP endpoints
- **Headless Mode**: Runs as a background service without GUI
- **Preview Mode**: Optional window with keyboard controls for interactive use

### System Features
- **Structured Logging**: Comprehensive logging system with file and console output
- **macOS Service**: Deploy as a background service with auto-start on boot
- **Connection Monitoring**: Web interface shows real-time connection status
- **Settings Management**: Dynamic configuration updates without restart

## Prerequisites

- **Python 3.9+**
- **Webcam**: USB webcam or built-in camera
- **macOS**: For deployment scripts (optional)

## Quick Start

### Manual Installation

1. **Clone the repository:**
   ```bash
   git clone <your-repo-url>
   cd videoserver
   ```

2. **Create and activate virtual environment:**
   ```bash
   python3 -m venv myenv
   source myenv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the server:**
   ```bash
   python3 main.py --api
   ```

5. **Access the web interface:**
   - Open `http://localhost:8000` in your browser

### macOS Deployment (Recommended)

Deploy as a background service that starts automatically on boot:

```bash
chmod +x deploy_mac.sh
./deploy_mac.sh
```

The deployment script will:
- Set up the virtual environment
- Install all dependencies
- Create a macOS LaunchAgent service
- Start the server automatically
- Configure it to restart on boot

**Service Management:**
```bash
# Stop service
launchctl unload ~/Library/LaunchAgents/com.$USER.video_delay_server.plist

# Start service
launchctl load ~/Library/LaunchAgents/com.$USER.video_delay_server.plist

# Check status
launchctl list | grep video_delay_server
```

**Remove Service:**
```bash
chmod +x remove_mac.sh
./remove_mac.sh
```

## Usage

### 1. API Server Mode (Recommended)

Start the web-based control server:
```bash
python3 main.py --api
```

**Access Points:**
- **Control Panel**: `http://localhost:8000`
- **Full Page Player**: `http://localhost:8000/static/player.html`
- **Video Gallery**: `http://localhost:8000/static/gallery.html`
- **API Documentation**: `http://localhost:8000/docs`

**From Network Devices (iPhone, iPad, etc.):**
- Replace `localhost` with your Mac's IP address
- Example: `http://192.168.1.100:8000/static/player.html`

### 2. Headless Streaming

Start streaming immediately without web interface:
```bash
python3 main.py --start
```

### 3. Interactive Mode

Run with preview window and keyboard controls:
```bash
python3 main.py --window
```

**Keyboard Controls:**
- `s`: Start Streaming
- `p`: Pause/Resume
- `q`: Quit

## Web Control Panel

The mobile-friendly control panel provides:

### Status Dashboard
- **Server Status**: Running/Stopped
- **Streaming Status**: Active/Inactive/Paused
- **Recording Status**: Recording/Not Recording
- **Current Delay**: Shows configured delay time

### Stream Controls
- **Start/Stop Streaming**: Begin or end WebRTC transmission
- **Pause/Resume**: Temporarily pause without stopping
- **Live Player**: Embedded WebRTC video player — watch the delayed stream right in the control panel
- **Open Full Page Player**: Opens `player.html` in a new tab — full viewport video, black background
- **Snapshot Capture**: Take and view full resolution (1280×720) snapshots of the delayed stream
- **Connection Status**: Real-time server connectivity indicator

### Settings Management
- **Delay Duration**: Adjust delay from 1-60 seconds
- **Frame Rate**: Modify FPS (15-60)
- **Apply & Restart**: Update settings with automatic restart

### Video Gallery

Separate page showing all recordings:
- **Thumbnail Previews**: Visual preview of each recording
- **File Information**: Size and creation date
- **Click to Download**: Direct download or playback
- **Statistics**: Total videos and storage used
- **Auto-Refresh**: Updates every 5 seconds

## API Reference

### Status & Information
- `GET /status` - Get current server status and settings
- `GET /videos` - List all recorded videos with metadata
- `GET /settings` - Get current configuration

### Server Control
- `POST /server/start` - Start the server process
- `POST /server/stop` - Stop the server process
- `POST /server/restart` - Restart with new settings (JSON body)

### Stream Control
- `POST /stream/start` - Start WebRTC streaming and recording
- `POST /stream/stop` - Stop streaming and recording
- `POST /stream/pause` - Pause transmission
- `POST /stream/resume` - Resume transmission
- `POST /offer` - WebRTC SDP offer/answer exchange (used by browser player)
- `GET /snapshot` - Capture and return delayed frame as full resolution JPEG

### Example API Usage

```bash
# Check status
curl http://localhost:8000/status

# Start streaming
curl -X POST http://localhost:8000/stream/start

# Update settings and restart
curl -X POST http://localhost:8000/server/restart \
  -H "Content-Type: application/json" \
  -d '{
    "delay_seconds": 15,
    "fps": 30
  }'

# List recordings
curl http://localhost:8000/videos

# Capture snapshot
curl http://localhost:8000/snapshot --output snapshot.jpg
```

## Configuration

### Default Settings (src/config.py)
```python
DELAY_SECONDS = 5
FPS = 30
WIDTH = 1280
HEIGHT = 720
CAM_INDEX_PRIMARY = 0
CAM_INDEX_SECONDARY = 1
```

### Dynamic Configuration
Settings can be changed via:
1. **Web Interface**: Use the Settings panel and click "Apply & Restart"
2. **API**: Send JSON to `/server/restart` endpoint
3. **Code**: Edit `src/config.py` (requires manual restart)

## Logging

All logs are stored in the `logs/` directory:

- **logs/videoserver.log**: Application logs (INFO level)
- **logs/videoserver_error.log**: Error logs (when deployed as service)

**Log Format:**
```
2025-12-25 19:30:15 - src.stream - INFO - Camera initialized. Request: 1280x720 @ 30fps
2025-12-25 19:30:16 - src.manager - INFO - Manager: Stream process started.
```

**View Logs:**
```bash
# Real-time monitoring
tail -f logs/videoserver.log

# View errors only
grep ERROR logs/videoserver.log
```

## Project Structure

```
videoserver/
├── main.py                  # Application entry point
├── requirements.txt         # Python dependencies
├── deploy_mac.sh           # macOS deployment script
├── remove_mac.sh           # macOS removal script
├── README.md               # This file
├── .gitignore              # Git ignore rules
│
├── recordings/             # Recorded video files (auto-created)
├── thumbnails/             # Video thumbnails (auto-created)
├── logs/                   # Application logs (auto-created)
│
├── static/                 # Web interface files
│   ├── index.html         # Control panel
│   ├── player.html        # Full page WebRTC video player
│   └── gallery.html       # Video gallery
│
└── src/                    # Source code
    ├── __init__.py
    ├── config.py          # Default configuration
    ├── settings.py        # Pydantic settings model
    ├── logger.py          # Logging configuration
    ├── stream.py          # Video capture, delay buffer, recording
    ├── webrtc.py          # WebRTC track and signaling
    ├── manager.py         # Stream lifecycle management
    └── server.py          # FastAPI server & endpoints
```

## Troubleshooting

### Camera Access Issues
**macOS**: Grant camera permissions in System Settings > Privacy & Security > Camera

### iPhone/iPad Can't Connect
The macOS firewall may be blocking port 8000. Allow it:
```bash
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add $(which python3)
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp $(which python3)
```
Or run `deploy_mac.sh` — it handles this automatically.

### Port Already in Use
Change the port:
```bash
python3 main.py --api --port 8001
```

### Service Won't Start
Check logs:
```bash
tail -f logs/videoserver.log
```

View service status:
```bash
launchctl list | grep video_delay_server
```

### Connection Issues
- Verify firewall settings allow port 8000
- Check that server is running: `launchctl list | grep video_delay_server`
- Ensure you're using the correct IP address for network access

## Development

### Running Tests
```bash
# Test camera access
python3 main.py --window

# Test API server
python3 main.py --api
curl http://localhost:8000/status
```

### Adding Features
The modular architecture makes it easy to extend:
- **Stream processing**: Edit `src/stream.py`
- **API endpoints**: Add to `src/server.py`
- **Configuration**: Update `src/settings.py`
- **Web UI**: Modify `static/index.html` or `static/gallery.html`

## License

[Your License Here]

## Credits

Built with:
- [OpenCV](https://opencv.org/) - Video capture and processing
- [aiortc](https://github.com/aiortc/aiortc) - WebRTC server implementation
- [FastAPI](https://fastapi.tiangolo.com/) - Web framework
- [Uvicorn](https://www.uvicorn.org/) - ASGI server
