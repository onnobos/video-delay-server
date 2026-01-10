#!/bin/bash

# ==========================================
# CONFIGURATION
# ==========================================
APP_NAME="video_delay_server"
PROJECT_DIR=$(pwd)

# MacOS specific paths
PLIST_LABEL="com.$USER.$APP_NAME"
PLIST_FILE="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

echo "============================================================"
echo "Video Delay Server - Removal Script for macOS"
echo "============================================================"
echo ""

# ==========================================
# 1. STOP AND UNLOAD SERVICE
# ==========================================
echo "--- Stopping Service ---"

# Check if service is loaded
if launchctl list | grep -q "$PLIST_LABEL"; then
    echo "Stopping and unloading service..."
    launchctl unload "$PLIST_FILE" 2>/dev/null
    
    # Wait a moment for the service to fully stop
    sleep 2
    
    # Verify it's stopped
    if launchctl list | grep -q "$PLIST_LABEL"; then
        echo "⚠️  Warning: Service may still be running. Trying force unload..."
        launchctl remove "$PLIST_LABEL" 2>/dev/null
        sleep 1
    fi
    
    echo "✓ Service stopped."
else
    echo "Service is not currently running."
fi

# ==========================================
# 2. REMOVE LAUNCH AGENT PLIST
# ==========================================
echo ""
echo "--- Removing Launch Agent ---"

if [ -f "$PLIST_FILE" ]; then
    rm "$PLIST_FILE"
    echo "✓ Removed: $PLIST_FILE"
else
    echo "Launch Agent file not found (already removed or never installed)."
fi

# ==========================================
# 3. OPTIONAL CLEANUP
# ==========================================
echo ""
echo "--- Optional Cleanup ---"
echo ""
echo "The following items can be removed manually if desired:"
echo ""
echo "📁 Project Directory:"
echo "   $PROJECT_DIR"
echo ""
echo "📁 Virtual Environment:"
echo "   $PROJECT_DIR/myenv"
echo ""
echo "📁 Recordings:"
echo "   $PROJECT_DIR/recordings"
echo ""
echo "📁 Thumbnails:"
echo "   $PROJECT_DIR/thumbnails"
echo ""
echo "📁 Logs:"
echo "   $PROJECT_DIR/logs"
echo ""

# Ask user if they want to remove optional items
read -p "Do you want to remove recordings, thumbnails, and logs? (y/N): " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo "Removing generated content..."
    
    if [ -d "$PROJECT_DIR/recordings" ]; then
        rm -rf "$PROJECT_DIR/recordings"
        echo "✓ Removed recordings/"
    fi
    
    if [ -d "$PROJECT_DIR/thumbnails" ]; then
        rm -rf "$PROJECT_DIR/thumbnails"
        echo "✓ Removed thumbnails/"
    fi
    
    if [ -d "$PROJECT_DIR/logs" ]; then
        rm -rf "$PROJECT_DIR/logs"
        echo "✓ Removed logs/"
    fi
    
    echo ""
    echo "✓ Generated content removed."
else
    echo ""
    echo "Skipping removal of generated content."
fi

echo ""
read -p "Do you want to remove the virtual environment? (y/N): " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    if [ -d "$PROJECT_DIR/myenv" ]; then
        echo "Removing virtual environment..."
        rm -rf "$PROJECT_DIR/myenv"
        echo "✓ Removed myenv/"
    else
        echo "Virtual environment not found."
    fi
else
    echo "Keeping virtual environment."
fi

# ==========================================
# 4. COMPLETION
# ==========================================
echo ""
echo "============================================================"
echo "✅ Video Delay Server Service Removed!"
echo "============================================================"
echo ""
echo "The background service has been stopped and removed."
echo ""
echo "To completely remove the application:"
echo "  1. Delete the project directory: rm -rf $PROJECT_DIR"
echo ""
echo "To reinstall the service:"
echo "  1. Run: ./deploy_mac.sh"
echo ""
echo "============================================================"
