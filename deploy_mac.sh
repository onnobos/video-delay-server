#!/bin/bash

# ==========================================
# CONFIGURATION
# ==========================================
APP_NAME="video_delay_server" # A unique name for your service
PROJECT_DIR=$(pwd)            # Assumes you run this script from the project root
VENV_DIR="$PROJECT_DIR/myenv" # Using existing myenv virtual environment
PORT=8000                     # Port to run the app on
HOST="0.0.0.0"               # Bind to all interfaces for network access

# MacOS specific paths
PLIST_LABEL="com.$USER.$APP_NAME"
PLIST_FILE="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

# ==========================================
# 1. ENVIRONMENT SETUP
# ==========================================
echo "--- Setting up Video Delay Server on macOS ---"

# Check for Python 3
if ! command -v python3 &> /dev/null; then
    echo "Python3 could not be found. Please install it (e.g., brew install python)"
    exit 1
fi

# Check for NDI Tools
echo "⚠️  IMPORTANT: Make sure NDI Tools are installed from https://ndi.video/tools/"

# Create Virtual Env if missing
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv $VENV_DIR
fi

# Install Dependencies
echo "Installing dependencies..."
source $VENV_DIR/bin/activate
pip install --upgrade pip

# Install from requirements.txt
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    pip install -r requirements.txt
else
    echo "Error: requirements.txt not found!"
    exit 1
fi

# ==========================================
# 2. CREATE LAUNCH AGENT (The macOS "Service")
# ==========================================
echo "--- Creating Launch Agent (.plist) ---"

# Unload existing service if it exists to allow updates
if launchctl list | grep -q "$PLIST_LABEL"; then
    launchctl unload "$PLIST_FILE"
fi

# Create the .plist file
# This XML tells macOS how to run your app in the background
cat > "$PLIST_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_LABEL</string>
    
    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>
    
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/python3</string>
        <string>main.py</string>
        <string>--api</string>
        <string>--host</string>
        <string>$HOST</string>
        <string>--port</string>
        <string>$PORT</string>
    </array>
    
    <key>RunAtLoad</key>
    <true/>
    
    <key>KeepAlive</key>
    <true/>
    
    <key>StandardOutPath</key>
    <string>$PROJECT_DIR/logs/videoserver.log</string>
    
    <key>StandardErrorPath</key>
    <string>$PROJECT_DIR/logs/videoserver_error.log</string>
    
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
EOF

# ==========================================
# 3. START THE SERVICE
# ==========================================
echo "--- Loading and Starting Service ---"

# Load the new plist
launchctl load "$PLIST_FILE"

# ==========================================
# 4. NETWORK INFO & INSTRUCTIONS
# ==========================================
# Get the local LAN IP address
IP_ADDRESS=$(ipconfig getifaddr en0) 
# Note: 'en0' is usually WiFi on Macs. If using Ethernet, it might be 'en1'.

echo ""
echo "✅ Video Delay Server Deployment Successful!"
echo "============================================================"
echo "The server is now running in the background as a service."
echo ""
echo "📱 Web Control Panel:"
echo "   Local:   http://localhost:$PORT"
echo "   Network: http://$IP_ADDRESS:$PORT"
echo ""
echo "📹 Video Gallery:"
echo "   Local:   http://localhost:$PORT/static/gallery.html"
echo "   Network: http://$IP_ADDRESS:$PORT/static/gallery.html"
echo ""
echo "📊 Service Management:"
echo "   Stop:    launchctl unload $PLIST_FILE"
echo "   Start:   launchctl load $PLIST_FILE"
echo "   Restart: launchctl unload $PLIST_FILE && launchctl load $PLIST_FILE"
echo "   Status:  launchctl list | grep $PLIST_LABEL"
echo ""
echo "📝 Logs:"
echo "   Output: $PROJECT_DIR/logs/videoserver.log"
echo "   Errors: $PROJECT_DIR/logs/videoserver_error.log"
echo ""
echo "⚠️  Camera Permissions:"
echo "   Make sure to grant camera access in:"
echo "   System Settings > Privacy & Security > Camera"
echo "============================================================"