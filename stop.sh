#!/bin/bash
USER=$(whoami)
PLIST="com.$USER.video_delay_server"
PATH_TO_PLIST="$HOME/Library/LaunchAgents/$PLIST.plist"

if launchctl list | grep -q "$PLIST"; then
    echo "🛑 Stopping Video Delay Server..."
    launchctl unload "$PATH_TO_PLIST"
    echo "✅ Server stopped."
else
    echo "⚠️  Server is not running."
fi