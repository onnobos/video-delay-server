# Video Delay Server 2.0

A high-performance video delay application engineered for macOS (Apple Silicon M1/M2/M3/M4 & Intel). It ingests video from HDMI capture cards (like the **Elgato Game Capture HD60 S+**), USB webcams, or Continuity Cameras, holds it in a configurable delay buffer (1–120 seconds), and streams it live to any browser via **synchronized WebRTC** with sub-second overhead.

Phones, tablets, and laptops on the local Wi-Fi can watch the delayed live replay directly in their browser without installing any app. Includes automatic H.264 recording, in-browser replay gallery, mobile QR code terminal connection, and dynamic camera switching.

---

## Features

### Core Capabilities
* **Hardware Camera Discovery & Auto-Default**: Detects connected video devices with friendly names via AVFoundation and automatically defaults to the **Elgato Game Capture HD60 S+**.
* **Dynamic Camera Switching**: Change active video inputs on the fly via the Web UI dropdown or API without stopping the server.
* **Zero-Sleep Live Capture & Auto-Downscaling**: Self-paced `cap.read()` ingestion (`BUFFERSIZE=1`) with monotonic 60→30 FPS gating and automatic downscaling of oversized 1080p/4K feeds to your target preset (`960×540`, `1280×720`, or `1920×1080`).
* **HDMI Signal-Loss Protection & Health Watchdog**: Displays an automatic standby screen (*"WAITING FOR VIDEO SIGNAL"*) if an HDMI cable or camera disconnects, detects silent black or frozen frames, and auto-recovers in the background.
* **Event-Driven H.264 WebRTC Streaming**: Multi-peer `aiortc` delivery using `asyncio.Event` wakeups, a shared `BGR → YUV420P` conversion cache, and **H.264 codec preference** (VP8 fallback) for smooth 30 FPS playback across phones and tablets.
* **Dynamic Delay, FPS & Recording Controls**: Adjust delay (1–120s), target FPS, resolution presets, and toggle **Record while streaming** (`avc1` H.264 MP4) without restarting the server.
* **Headless 6-Point Diagnostics & Test Pattern**: Built-in health checks for TCC camera permission, native `arm64` vs Rosetta 2 Python, USB 3.0 link speed, per-stage FPS timings (`cap.read`, `proc`, `transmit`, `webrtc`), server log viewer, and synthetic SMPTE test pattern mode.
* **Remote Mac Power Control**: Safely flush active recordings and restart or shut down a headless Mac directly from the Web UI (with optional passwordless `sudo /sbin/shutdown` setup).
* **In-Browser Video Gallery & Mobile QR Code**: Review H.264 recordings in a modal player, customize overlays (timestamp, FPS, camera name), and scan the terminal ASCII QR code from your phone.

---

## Hardware Compatibility (Elgato HD60 S+)

* **Apple Silicon Native**: The **Elgato HD60 S+** is a driverless **UVC (USB Video Class)** device. It is recognized natively by macOS and AVFoundation out of the box without proprietary drivers.
* **USB 3.0 Required**: The HD60 S+ requires a genuine **USB 3.0 / 3.1 SuperSpeed (5 Gbps)** cable. Standard white Apple USB-C charging cables are USB 2.0 only and will result in black screens.
* **Clean HDMI Output**: Ensure your camera's HDMI output is clean (no on-screen camera battery/focus UI) and has HDCP disabled.

---

## Quick Start

### ⚡ 1-Command Setup & Launch (macOS)

Clone the repository and run:

```bash
chmod +x server.sh
./server.sh start
```

The script will automatically:
1. Detect native `arm64` Python 3.9+ (`python3.11`, `python3.12`, etc.) on Apple Silicon
2. Bootstrap the virtual environment (`myenv/`) and install dependencies
3. Scan connected video devices (defaulting to the **Elgato HD60 S+**)
4. Launch the background daemon (`LaunchAgent`) with `ProcessType = Interactive` QoS and auto-restart on boot
5. Print local URLs and an **interactive ASCII QR Code** in your terminal

---

### 🎛️ Unified Management CLI (`./server.sh`)

| Command | Description |
| :--- | :--- |
| **`./server.sh start`** | Bootstrap environment, install `Interactive` LaunchAgent, start daemon, and display QR code |
| **`./server.sh run`** | Run in foreground with live terminal logs and mobile QR code |
| **`./server.sh status`** | Show health, active PID, camera name, buffer RAM %, viewers, and local URLs |
| **`./server.sh stop`** | Stop the background service and free port 8000 |
| **`./server.sh restart`** | Gracefully restart the service |
| **`./server.sh doctor`** | Run headless diagnostics (TCC permission, Python arch, USB 3.0 speed, cameras, logs) |
| **`./server.sh bench`** | Benchmark raw camera FPS across resolutions, USB link speed, and full pipeline FPS |
| **`./server.sh setup-power`** | Configure passwordless `sudo /sbin/shutdown` for remote Mac Restart/Shutdown from Web UI |
| **`./server.sh logs`** | Stream live server logs (`tail -f logs/videoserver.log`) |
| **`./server.sh uninstall`** | Unload service, remove plist, with optional cleanup of recordings/logs |

*(Legacy scripts `./deploy_mac.sh`, `./stop.sh`, and `./remove_mac.sh` remain supported as seamless shortcuts).*

---

## 📱 Running on Mobile (iPhone, iPad, Android)

1. Ensure your phone is connected to the **same Wi-Fi network** as your Mac.
2. Open the camera app on your phone and **scan the terminal QR code**, or type:
   * **Fullscreen Live Replay Player**: `http://<your-mac-ip>:8000/static/player.html`
   * **Control Panel**: `http://<your-mac-ip>:8000`
   * **Video Gallery**: `http://<your-mac-ip>:8000/static/gallery.html`

> [!TIP]
> **iPhone / iPad Fullscreen "App" Mode:**  
> In Safari, tap the **Share** button (box with up-arrow) ➔ select **"Add to Home Screen"**.  
> When launched from your Home Screen, it opens as a standalone fullscreen app with no browser address bar or buttons!

---

## 🖥️ Headless M1 / M2 / M3 Mac Setup Guide

Running the Video Delay Server on a **headless Apple Silicon Mac (e.g. Mac mini or MacBook)** is ideal for a dedicated 24/7 sports replay station. Follow these steps to configure unattended operation:

### Step 1: Enable Automatic Login (Critical for Camera Access)
macOS security (TCC) restricts camera access to active user sessions. To allow headless operation after a reboot:
1. Go to **System Settings ➔ Users & Groups ➔ Automatically log in as:** ➔ select your user account.
2. *(Note: FileVault must be disabled for automatic login without keyboard input at boot).*

### Step 2: Prevent Sleep
Run in Terminal or over SSH:
```bash
sudo pmset -a sleep 0
sudo pmset -a displaysleep 0
sudo pmset -a womp 1
```

### Step 3: Grant Camera Permission (One-Time Only)
macOS will never display a permission dialog over pure SSH. Authorize it once:
1. Connect to your Mac via **Screen Sharing / VNC** (`vnc://<mac-ip>` from Finder on another Mac, or plug in a monitor once).
2. Open Terminal inside the graphical session and run:
   ```bash
   ./server.sh run
   ```
3. When macOS prompts: *"Terminal would like to access the Camera"*, click **Allow**.
4. Press `Ctrl + C` to stop. Camera access is now permanently saved.

### Step 4: Start the Background Daemon
```bash
./server.sh start
```
The server is now registered with `launchd` and will automatically start upon boot unattended.

### Step 5: Connect via Local Hostname (`.local`)
Instead of tracking changing DHCP IP addresses, connect via your Mac's Bonjour hostname:
```text
http://<your-mac-name>.local:8000/static/player.html
```
*(Check your hostname using `echo $(hostname)`).*

---

## ⚡ Performance & Low-FPS Troubleshooting

If the projected FPS overlay or browser stream runs below 20 FPS, run the built-in benchmark and diagnostics:

```bash
./server.sh bench
./server.sh doctor
```

1. **Isolate Hardware (`cap.read`) vs. Python CPU (`proc`):**
   * If `cap.read > 45 ms`: The bottleneck is upstream of Python — verify the Elgato HD60 S+ is using a **USB 3.0 (5 Gbps)** cable directly on the Mac (USB 2.0 `480 Mb/s` throttles high-res video), check your camera's HDMI output frame rate/exposure, or switch Resolution in the Web UI to **`960×540 (High FPS)`**.
   * If `proc > 15 ms`: Uncheck **Record while streaming** in the Web UI or lower Resolution to **`960×540 (High FPS)`**.
2. **Verify Native ARM64 Python (Apple Silicon):**
   * Running an `x86_64` Python binary under Rosetta 2 cuts throughput sharply. `./server.sh doctor` flags Rosetta 2 automatically, and `./server.sh start` prefers native `/opt/homebrew/bin/python3.11`.
3. **Update Older LaunchAgent Plists:**
   * Run `./server.sh restart` so the LaunchAgent plist includes `ProcessType = Interactive` and `LowPriorityIO = false`, preventing macOS from scheduling capture threads on Efficiency Cores or coalescing timers.

---

## API Reference

### Camera Discovery & Selection
* `GET /cameras` - List detected video devices with friendly names and default flags
* `POST /cameras/select` - Switch camera device dynamically (`{"index": 1}`)

### Server & Stream Controls
* `GET /status` - Real-time telemetry (active camera, `measured_fps`, `transmit_fps`, `webrtc_fps`, `capture_read_ms`, `capture_proc_ms`, buffer %, estimated RAM, viewers count)
* `POST /stream/start` - Begin WebRTC delay transmission (and recording if `record_on_stream` is enabled)
* `POST /stream/stop` - Stop streaming and recording
* `POST /stream/pause` - Temporarily pause transmission
* `POST /stream/resume` - Resume transmission
* `POST /offer` - WebRTC SDP signaling endpoint
* `GET /snapshot` - Capture full-resolution delayed JPEG snapshot

### Dynamic Settings
* `GET /settings` - Get active configuration
* `PATCH /settings` - Dynamically update `delay_seconds` (1–120s), `fps` (5–60), `width`/`height`, `record_on_stream`, `downscale_oversized`, `show_timestamp`, `show_fps`, and `show_camera_name`

### Diagnostics & Headless Host Control
* `GET /diagnostics` - Run 6-point headless diagnostics (TCC, Python arch, USB speed, frame health, storage/network, power permissions)
* `GET /logs?lines=80` - Tail recent application and error logs
* `POST /diagnostics/recover` - Reset and re-discover camera hardware remotely
* `POST /diagnostics/test-pattern` - Toggle synthetic SMPTE test pattern (`{"enabled": true}`)
* `POST /system/restart-mac` - Flush active recordings/streams and restart macOS
* `POST /system/shutdown-mac` - Flush active recordings/streams and shut down macOS

### Video Gallery & Storage
* `GET /videos` - List all recordings with metadata, duration, and thumbnail URLs
* `DELETE /videos/{filename}` - Permanently delete an MP4 recording and its thumbnail

---

## Project Structure

```
video-delay-server/
├── server.sh                # Unified management CLI (start, stop, run, status, doctor, bench, logs)
├── deploy_mac.sh            # macOS deployment shortcut
├── stop.sh                  # Stop service shortcut
├── remove_mac.sh            # Service uninstallation shortcut
├── main.py                  # Python application entrypoint
├── test_camera_fps.py       # Multi-resolution camera & full-pipeline FPS benchmark tool
├── requirements.txt         # Dependencies
│
├── src/
│   ├── cameras.py           # Hardware camera discovery & auto-default logic
│   ├── diagnostics.py       # 6-point headless health checks, log tailing & Mac power actions
│   ├── network.py           # LAN IP resolution & URL formatting
│   ├── qr_helper.py         # Terminal ASCII QR code generator
│   ├── stream.py            # Zero-sleep capture, circular delay buffer, H.264 recorder
│   ├── webrtc.py            # Event-driven WebRTC broadcaster (YUV420P cache + H.264 preference)
│   ├── manager.py           # Stream lifecycle & status aggregation
│   ├── server.py            # FastAPI REST API & static routing
│   ├── settings.py          # Pydantic configuration models
│   ├── config.py            # Default system constants
│   └── logger.py            # Rotating file and console logger
│
├── static/
│   ├── index.html           # Web control panel with camera dropdown, FPS telemetry & diagnostics
│   ├── player.html          # Full-page WebRTC video player (iPhone/iPad optimized)
│   └── gallery.html         # In-browser video replay gallery with modal player
│
├── tests/
│   └── test_components.py   # Unit test suite
├── recordings/              # Captured H.264 MP4 videos (auto-created)
├── thumbnails/              # Replay thumbnails (auto-created)
└── logs/                    # Application and error logs (auto-created)
```

---

## License

MIT License. Built for sports coaches, video analysts, and live production teams.
