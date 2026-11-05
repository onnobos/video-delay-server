#!/usr/bin/env bash
# ==============================================================================
#  Video Delay Server 2.0 - Unified Management & Deployment CLI (macOS)
# ==============================================================================
set -e

APP_NAME="video_delay_server"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/myenv"
PORT="${PORT:-8000}"
HOST="0.0.0.0"

PLIST_LABEL="com.$USER.$APP_NAME"
PLIST_FILE="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

# Colors
C_RESET='\033[0m'
C_BOLD='\033[1m'
C_GREEN='\033[32m'
C_CYAN='\033[36m'
C_YELLOW='\033[33m'
C_RED='\033[31m'

info()  { echo -e "${C_CYAN}ℹ️  $*${C_RESET}"; }
ok()    { echo -e "${C_GREEN}✅ $*${C_RESET}"; }
warn()  { echo -e "${C_YELLOW}⚠️  $*${C_RESET}"; }
err()   { echo -e "${C_RED}❌ $*${C_RESET}"; }

# ------------------------------------------------------------------------------
# 1. Python Discovery & Virtual Environment Bootstrap
# ------------------------------------------------------------------------------
detect_python() {
    local candidates=("python3.11" "python3.12" "python3.10" "python3.13" "python3")
    for py in "${candidates[@]}"; do
        if command -v "$py" &>/dev/null; then
            local ver
            ver=$("$py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)
            local major=${ver%.*}
            local minor=${ver#*.}
            if [ "$major" -eq 3 ] && [ "$minor" -ge 9 ]; then
                echo "$(command -v "$py")"
                return 0
            fi
        fi
    done
    return 1
}

ensure_environment() {
    # Ensure logs, recordings, thumbnails exist
    mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/recordings" "$PROJECT_DIR/thumbnails"

    # Create venv if missing
    if [ ! -f "$VENV_DIR/bin/python" ]; then
        info "Virtual environment not found. Locating Python 3.9+..."
        local py_bin
        py_bin=$(detect_python) || {
            err "Python 3.9+ is required but was not found. Please install via: brew install python@3.11"
            exit 1
        }
        info "Creating virtual environment using $py_bin..."
        "$py_bin" -m venv "$VENV_DIR"
        ok "Virtual environment created at $VENV_DIR"
        
        info "Installing dependencies from requirements.txt..."
        "$VENV_DIR/bin/pip" install --upgrade pip --quiet
        "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
        ok "All dependencies installed."
    else
        # Quick verification of requirements
        if [ ! -f "$VENV_DIR/.installed" ] || [ "$PROJECT_DIR/requirements.txt" -nt "$VENV_DIR/.installed" ]; then
            info "Syncing dependencies from requirements.txt..."
            "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt" --quiet
            touch "$VENV_DIR/.installed"
        fi
    fi
}

check_port() {
    local pid
    pid=$(lsof -ti :"$PORT" 2>/dev/null || true)
    if [ -n "$pid" ]; then
        warn "Port $PORT is currently in use by PID $pid."
        # If it belongs to video delay server, offer to stop it
        if ps -p "$pid" -o command= | grep -q "main.py"; then
            info "Stopping existing Video Delay Server process (PID: $pid)..."
            kill -9 "$pid" 2>/dev/null || true
            sleep 1
        else
            warn "Another program is using port $PORT. Set PORT=<number> ./server.sh start to use an alternate port."
        fi
    fi
}

# ------------------------------------------------------------------------------
# 2. Service Management Functions
# ------------------------------------------------------------------------------
cmd_start() {
    echo -e "${C_BOLD}==============================================================${C_RESET}"
    echo -e "${C_BOLD}   🎬 Starting Video Delay Server (Background Service)        ${C_RESET}"
    echo -e "${C_BOLD}==============================================================${C_RESET}"
    
    ensure_environment
    check_port

    # Unload existing agent if running
    if launchctl list | grep -q "$PLIST_LABEL"; then
        info "Unloading existing LaunchAgent..."
        launchctl unload "$PLIST_FILE" 2>/dev/null || true
    fi

    # Hardware detection
    echo ""
    info "Scanning video capture hardware..."
    "$VENV_DIR/bin/python" main.py --list-cameras || true

    # Create LaunchAgent plist
    info "Configuring macOS LaunchAgent service at $PLIST_FILE..."
    mkdir -p "$HOME/Library/LaunchAgents"

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
        <string>$VENV_DIR/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
EOF

    info "Loading LaunchAgent service..."
    launchctl load "$PLIST_FILE"

    # Verification polling (wait up to 5 seconds)
    local waited=0
    local running=false
    while [ $waited -lt 5 ]; do
        sleep 1
        if curl -s "http://127.0.0.1:$PORT/status" &>/dev/null; then
            running=true
            break
        fi
        waited=$((waited + 1))
    done

    if [ "$running" = true ]; then
        local pid
        pid=$(pgrep -f "main.py --api" | head -n 1)
        ok "Video Delay Server is live! (PID: ${pid:-active})"
        
        # Display Banner & QR Code
        "$VENV_DIR/bin/python" src/qr_helper.py "$PORT"
    else
        err "Service started but port $PORT is not responding yet."
        echo "Check error logs: cat logs/videoserver_error.log"
    fi
}

cmd_run() {
    echo -e "${C_BOLD}==============================================================${C_RESET}"
    echo -e "${C_BOLD}   🎬 Running Video Delay Server (Interactive Terminal Mode)   ${C_RESET}"
    echo -e "${C_BOLD}==============================================================${C_RESET}"
    
    ensure_environment
    check_port

    exec "$VENV_DIR/bin/python" main.py --api --host "$HOST" --port "$PORT"
}

cmd_stop() {
    info "Stopping Video Delay Server..."
    local stopped=false

    if launchctl list 2>/dev/null | grep -q "$PLIST_LABEL"; then
        launchctl unload "$PLIST_FILE" 2>/dev/null || true
        stopped=true
    fi

    # Kill any lingering python processes for main.py
    local pids
    pids=$(pgrep -f "main.py" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        kill -9 $pids 2>/dev/null || true
        stopped=true
    fi

    if [ "$stopped" = true ]; then
        ok "Server stopped and port $PORT released."
    else
        warn "Server was not running."
    fi
}

cmd_status() {
    echo -e "${C_BOLD}--- Video Delay Server Status ---${C_RESET}"
    
    local loaded=false
    if launchctl list 2>/dev/null | grep -q "$PLIST_LABEL"; then
        loaded=true
    fi

    if curl -s --connect-timeout 1 "http://127.0.0.1:$PORT/status" &>/dev/null; then
        local pid
        pid=$(pgrep -f "main.py" | head -n 1)
        ok "Status: RUNNING (PID: $pid, Port: $PORT)"
        info "LaunchAgent: $([ "$loaded" = true ] && echo "Active" || echo "Inactive / Standalone")"
        
        # Query status json
        "$VENV_DIR/bin/python" -c "
import urllib.request, json
try:
    with urllib.request.urlopen('http://127.0.0.1:$PORT/status') as r:
        d = json.loads(r.read())
        t = d.get('telemetry', {})
        print(f'  • Active Camera: {t.get(\"camera_name\", \"Unknown\")}')
        print(f'  • Resolution:    {t.get(\"resolution\", \"Unknown\")} @ {t.get(\"fps\", 30)} FPS')
        print(f'  • Delay:         {d.get(\"settings\", {}).get(\"delay_seconds\", 5)}s')
        print(f'  • Buffer RAM:    {t.get(\"estimated_ram_mb\", 0)} MB ({t.get(\"buffer_percent\", 0)}% full)')
        print(f'  • Viewers:       {d.get(\"active_viewers\", 0)}')
        print(f'  • Streaming:     {\"YES\" if d.get(\"streaming\") else \"NO\"}')
except Exception as e:
    pass
" 2>/dev/null || true
        echo ""
        "$VENV_DIR/bin/python" src/qr_helper.py "$PORT"
    else
        warn "Status: STOPPED (Port $PORT is closed)"
        if [ "$loaded" = true ]; then
            warn "LaunchAgent is registered but process is not responding. Check: cat logs/videoserver_error.log"
        fi
    fi
}

cmd_logs() {
    local log_file="$PROJECT_DIR/logs/videoserver.log"
    if [ ! -f "$log_file" ]; then
        warn "Log file $log_file does not exist yet."
        exit 0
    fi
    info "Streaming logs from $log_file (Ctrl+C to exit)..."
    tail -f -n 40 "$log_file"
}

cmd_uninstall() {
    echo -e "${C_BOLD}==============================================================${C_RESET}"
    echo -e "${C_BOLD}   🗑️  Uninstalling Video Delay Server Service                ${C_RESET}"
    echo -e "${C_BOLD}==============================================================${C_RESET}"
    
    cmd_stop

    if [ -f "$PLIST_FILE" ]; then
        rm -f "$PLIST_FILE"
        ok "Removed LaunchAgent plist at $PLIST_FILE"
    fi

    echo ""
    read -p "Do you also want to remove recorded videos, thumbnails, and logs? (y/N): " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -rf "$PROJECT_DIR/recordings" "$PROJECT_DIR/thumbnails" "$PROJECT_DIR/logs"
        ok "Cleaned recordings, thumbnails, and logs."
    fi

    ok "Uninstallation complete."
}

# ------------------------------------------------------------------------------
# Entrypoint & Command Dispatcher
# ------------------------------------------------------------------------------
case "${1:-}" in
    start)
        cmd_start
        ;;
    run)
        cmd_run
        ;;
    stop)
        cmd_stop
        ;;
    restart)
        cmd_stop
        sleep 1
        cmd_start
        ;;
    status)
        cmd_status
        ;;
    logs)
        cmd_logs
        ;;
    uninstall|remove)
        cmd_uninstall
        ;;
    help|--help|-h|"")
        echo -e "${C_BOLD}Video Delay Server 2.0 CLI${C_RESET}"
        echo "Usage: ./server.sh <command>"
        echo ""
        echo "Commands:"
        echo "  start       Setup environment, start background LaunchAgent service, and show QR code"
        echo "  run         Run server in foreground with live terminal output and QR code"
        echo "  stop        Stop running service and free port $PORT"
        echo "  restart     Restart the background service"
        echo "  status      Check health, active camera, buffer RAM, and local URLs"
        echo "  logs        Stream live server logs (tail -f)"
        echo "  uninstall   Remove LaunchAgent service with optional data cleanup"
        echo ""
        ;;
    *)
        err "Unknown command: $1"
        echo "Run ./server.sh help for usage."
        exit 1
        ;;
esac
