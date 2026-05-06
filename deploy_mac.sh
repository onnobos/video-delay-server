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
# 2. INSTALL DEPENDENCIES
# ==========================================
echo "--- Installing Python dependencies ---"
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt" --quiet

# Allow Python through macOS firewall so iPhones/other devices on the network can connect
echo "--- Allowing Python through macOS firewall ---"
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add "$VENV_DIR/bin/python" 2>/dev/null || true
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp "$VENV_DIR/bin/python" 2>/dev/null || true

# ==========================================
# 3. CREATE AGENT
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
# 4. LAUNCH
# ==========================================
echo "--- Starting Service ---"
launchctl load "$PLIST_FILE"

# ==========================================
# 5. VERIFY
# ==========================================
sleep 2
PID=$(pgrep -f "main.py --api")
IP_ADDRESS=$(ipconfig getifaddr en0 || ipconfig getifaddr en1)

if [ -n "$PID" ]; then
    echo ""
    echo "✅ SUCCESS! Service is running (PID: $PID)"
    echo "---------------------------------------------------"
    echo "Control panel: http://$IP_ADDRESS:$PORT"
    echo "Full screen player: http://$IP_ADDRESS:$PORT/static/player.html"
    echo "---------------------------------------------------"
    echo "⚠️  IMPORTANT: If camera fails, grant Terminal camera access in System Settings → Privacy."
else
    echo "❌ Failed to start. Check logs: cat logs/videoserver_error.log"
fi