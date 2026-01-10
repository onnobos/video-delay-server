#!/bin/bash

# ==========================================
# CONFIGURATION
# ==========================================
APP_NAME="video_delay_server"
PROJECT_DIR=$(pwd)
VENV_DIR="$PROJECT_DIR/myenv"
PORT=8000
HOST="0.0.0.0"

PLIST_LABEL="com.$USER.$APP_NAME"
PLIST_FILE="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

# ==========================================
# 1. PREP & CLEANUP
# ==========================================
echo "--- Configuring Camera-Safe Service (LaunchAgent) ---"

# Create logs directory
if [ ! -d "$PROJECT_DIR/logs" ]; then
    mkdir -p "$PROJECT_DIR/logs"
fi

# Unload existing agent if running
if launchctl list | grep -q "$PLIST_LABEL"; then
    launchctl unload "$PLIST_FILE"
fi

# ==========================================
# 2. CREATE AGENT
# ==========================================
echo "--- Creating Plist at $PLIST_FILE ---"

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
        <string>$VENV_DIR/bin/python</string>
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
        <key>PYTHONPATH</key>
        <string>$PROJECT_DIR</string>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
        <key>PATH</key>
        <string>$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
EOF

# ==========================================
# 3. LAUNCH
# ==========================================
echo "--- Starting Service ---"
launchctl load "$PLIST_FILE"

# ==========================================
# 4. VERIFY
# ==========================================
sleep 2
PID=$(pgrep -f "main.py --api")
IP_ADDRESS=$(ipconfig getifaddr en0 || ipconfig getifaddr en1)

if [ -n "$PID" ]; then
    echo ""
    echo "✅ SUCCESS! Service is running (PID: $PID)"
    echo "---------------------------------------------------"
    echo "Access at: http://$IP_ADDRESS:$PORT"
    echo "---------------------------------------------------"
    echo "⚠️  IMPORTANT: If camera fails, see Step 4 below!"
else
    echo "❌ Failed to start. Check logs: cat logs/videoserver_error.log"
fi