# Video Delay Server 2.0

A high-performance video delay application engineered for macOS (Apple Silicon M1/M2/M3/M4 & Intel). It ingests video from HDMI capture cards (like the **Elgato Game Capture HD60 S+**), USB webcams, or Continuity Cameras, holds it in a configurable delay buffer (1–120 seconds), and streams it live to any browser via **synchronized WebRTC** with sub-second overhead.

Phones, tablets, and laptops on the local Wi-Fi can watch the delayed live replay directly in their browser without installing any app. Includes automatic H.264 recording, in-browser replay gallery, mobile QR code terminal connection, and dynamic camera switching.

---

## Features

### Core Capabilities
* **Hardware Camera Discovery & Auto-Default**: Detects connected video devices with friendly names via AVFoundation and automatically defaults to the **Elgato Game Capture HD60 S+**.
* **Dynamic Camera Switching**: Change active video inputs on the fly via the Web UI dropdown or API without stopping the server.
* **HDMI Signal-Loss Protection**: Displays an automatic standby screen (*"WAITING FOR VIDEO SIGNAL - Game Capture HD60 S+"*) if an HDMI cable or camera disconnects, and auto-resumes once signal returns.
* **Synchronized WebRTC Streaming**: Multi-peer synchronized delivery via `aiortc` with monotonic PTS timing — sub-second overhead on top of your chosen delay.
* **Dynamic Delay Resizing**: Adjust delay buffer duration (1–120s) on-the-fly from the web panel without tearing or rebooting.
* **Apple Silicon Native H.264 Recording**: Encodes recordings in hardware-accelerated H.264 (`avc1`) MP4 format for native instant playback in iOS Safari, iPadOS, and macOS.
* **In-Browser Video Gallery**: Review recorded clips with instant in-page modal playback, formatted file sizes, download, and delete controls.
* **Customizable Visual Overlays**: Toggle live timestamp, FPS counter, and camera name overlays on or off.
* **Mobile Terminal QR Code**: Prints an ASCII QR code on startup so you can simply scan your terminal screen with an iPhone or Android camera to open the live stream.

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
1. Detect Python 3.9+ (`python3.11`, `python3.12`, etc.)
2. Bootstrap the virtual environment (`myenv/`) and install dependencies
3. Scan connected video devices (defaulting to the **Elgato HD60 S+**)
4. Launch the background daemon (`LaunchAgent`) with auto-restart on boot
5. Print local URLs and an **interactive ASCII QR Code** in your terminal

---

### 🎛️ Unified Management CLI (`./server.sh`)

| Command | Description |
| :--- | :--- |
| **`./server.sh start`** | Bootstrap environment, install LaunchAgent, start background daemon, and display QR code |
| **`./server.sh run`** | Run in foreground with live terminal logs and mobile QR code |
| **`./server.sh status`** | Show health, active PID, camera name, buffer RAM %, viewers, and local URLs |
| **`./server.sh stop`** | Stop the background service and free port 8000 |
| **`./server.sh restart`** | Gracefully restart the service |
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

## API Reference

### Camera Discovery & Selection
* `GET /cameras` - List detected video devices with friendly names and default flags
* `POST /cameras/select` - Switch camera device dynamically (`{"index": 1}`)

### Server & Stream Controls
* `GET /status` - Real-time telemetry (active camera, buffer %, estimated RAM, viewers count)
* `POST /stream/start` - Begin WebRTC delay transmission and recording
* `POST /stream/stop` - Stop streaming and recording
* `POST /stream/pause` - Temporarily pause transmission
* `POST /stream/resume` - Resume transmission
* `POST /offer` - WebRTC SDP signaling endpoint
* `GET /snapshot` - Capture full-resolution delayed JPEG snapshot

### Dynamic Settings
* `GET /settings` - Get active configuration
* `PATCH /settings` - Update delay seconds (1–120s) or overlays on the fly without server restart

### Video Gallery & Storage
* `GET /videos` - List all recordings with metadata, duration, and thumbnail URLs
* `DELETE /videos/{filename}` - Permanently delete an MP4 recording and its thumbnail

---

## Project Structure

```
video-delay-server/
├── server.sh                # Unified management CLI (start, stop, run, status, logs)
├── deploy_mac.sh            # macOS deployment shortcut
├── stop.sh                  # Stop service shortcut
├── remove_mac.sh            # Service uninstallation shortcut
├── main.py                  # Python application entrypoint
├── requirements.txt         # Dependencies
│
├── src/
│   ├── cameras.py           # Hardware camera discovery & auto-default logic
│   ├── network.py           # LAN IP resolution & URL formatting
│   ├── qr_helper.py         # Terminal ASCII QR code generator
│   ├── stream.py            # Video capture, delay buffer, H.264 recorder
│   ├── webrtc.py            # Event-driven WebRTC broadcaster
│   ├── manager.py           # Stream lifecycle & status aggregation
│   ├── server.py            # FastAPI REST API & static routing
│   ├── settings.py          # Pydantic configuration models
│   ├── config.py            # Default system constants
│   └── logger.py            # Rotating file and console logger
│
├── static/
│   ├── index.html           # Web control panel with camera dropdown & telemetry
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
