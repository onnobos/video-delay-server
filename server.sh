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
SUDOERS_FILE="/etc/sudoers.d/video_delay_server"
NO_AUTOSTART_FILE="$PROJECT_DIR/.no_autostart"

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
    local host_arch
    host_arch=$(uname -m)
    local candidates=(
        "/opt/homebrew/bin/python3.11"
        "/opt/homebrew/bin/python3.12"
        "/opt/homebrew/bin/python3.10"
        "/opt/homebrew/bin/python3"
        "python3.11"
        "python3.12"
        "python3.10"
        "python3.13"
        "python3"
    )
    for py in "${candidates[@]}"; do
        if command -v "$py" &>/dev/null; then
            local py_path
            py_path="$(command -v "$py")"
            local ver
            ver=$("$py_path" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)
            local major=${ver%.*}
            local minor=${ver#*.}
            if [ "$major" = "3" ] && [ "$minor" -ge 9 ]; then
                # On Apple Silicon, prefer native arm64 over Rosetta x86_64
                if [ "$host_arch" = "arm64" ]; then
                    local py_arch
                    py_arch=$("$py_path" -c 'import platform; print(platform.machine())' 2>/dev/null || true)
                    if [ "$py_arch" != "arm64" ]; then
                        continue
                    fi
                fi
                echo "$py_path"
                return 0
            fi
        fi
    done

    # Fallback if only x86_64 python exists
    for py in "python3.11" "python3.12" "python3.10" "python3"; do
        if command -v "$py" &>/dev/null; then
            local py_path
            py_path="$(command -v "$py")"
            if [ "$host_arch" = "arm64" ]; then
                warn "Only non-native Python found ($py_path). Performance may be throttled under Rosetta 2."
            fi
            echo "$py_path"
            return 0
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

graceful_kill_pid() {
    local pid="$1"
    if [ -z "$pid" ]; then
        return 0
    fi
    kill -TERM "$pid" 2>/dev/null || true
    local ticks=0
    while [ $ticks -lt 10 ]; do
        if ! kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        sleep 0.5
        ticks=$((ticks + 1))
    done
    kill -9 "$pid" 2>/dev/null || true
}

check_port() {
    local pid
    pid=$(lsof -ti :"$PORT" 2>/dev/null || true)
    if [ -n "$pid" ]; then
        warn "Port $PORT is currently in use by PID $pid."
        # If it belongs to video delay server, offer to stop it
        if ps -p "$pid" -o command= | grep -q "main.py"; then
            info "Stopping existing Video Delay Server process (PID: $pid)..."
            graceful_kill_pid "$pid"
        else
            warn "Another program is using port $PORT. Set PORT=<number> ./server.sh start to use an alternate port."
        fi
    fi
}

# ------------------------------------------------------------------------------
# 2. Service Management Functions
# ------------------------------------------------------------------------------
write_launchagent_plist() {
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

    <key>ProcessType</key>
    <string>Interactive</string>

    <key>LowPriorityIO</key>
    <false/>
    
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
        <key>OPENCV_AVFOUNDATION_SKIP_AUTH</key>
        <string>1</string>
        <key>PATH</key>
        <string>$VENV_DIR/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
EOF
}

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

    if [ -f "$NO_AUTOSTART_FILE" ]; then
        warn "Auto-start on boot is disabled (.no_autostart active). Starting as standalone background process..."
        PYTHONPATH="$PROJECT_DIR" PYTHONUNBUFFERED=1 OPENCV_AVFOUNDATION_SKIP_AUTH=1 \
            nohup "$VENV_DIR/bin/python" main.py --api --host "$HOST" --port "$PORT" \
            >> "$PROJECT_DIR/logs/videoserver.log" \
            2>> "$PROJECT_DIR/logs/videoserver_error.log" < /dev/null &
    else
        write_launchagent_plist
        info "Loading LaunchAgent service..."
        launchctl load "$PLIST_FILE"
    fi

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
        if [ ! -f "$SUDOERS_FILE" ]; then
            echo ""
            info "Tip: Run './server.sh setup-power' once to enable 1-click Mac Restart/Shutdown from the web UI."
        fi
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

    # Kill any lingering python processes for main.py gracefully first
    local pids
    pids=$(pgrep -f "main.py" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        for p in $pids; do
            graceful_kill_pid "$p"
        done
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

cmd_doctor() {
    ensure_environment
    "$VENV_DIR/bin/python" -m src.diagnostics "$PORT"
}

cmd_bench() {
    ensure_environment
    "$VENV_DIR/bin/python" "$PROJECT_DIR/test_camera_fps.py" --bench
}

cmd_setup_power() {
    echo -e "${C_BOLD}==============================================================${C_RESET}"
    echo -e "${C_BOLD}   🔌 Configuring Passwordless Mac Shutdown & Restart         ${C_RESET}"
    echo -e "${C_BOLD}==============================================================${C_RESET}"
    local tmp_sudo
    tmp_sudo=$(mktemp)
    cat > "$tmp_sudo" <<EOF
# Allow Video Delay Server user ($USER) to cleanly shutdown or restart macOS headlessly
$USER ALL=(root) NOPASSWD: /sbin/shutdown -h now, /sbin/shutdown -r now
EOF
    if sudo visudo -cf "$tmp_sudo"; then
        sudo cp "$tmp_sudo" "$SUDOERS_FILE"
        sudo chown root:wheel "$SUDOERS_FILE"
        sudo chmod 0440 "$SUDOERS_FILE"
        rm -f "$tmp_sudo"
        ok "Passwordless Mac Restart & Shutdown enabled at $SUDOERS_FILE"
    else
        rm -f "$tmp_sudo"
        err "Failed visudo validation; sudoers file was not installed."
        exit 1
    fi
}

cmd_autostart() {
    local sub="${1:-status}"
    case "$sub" in
        on|enable)
            rm -f "$NO_AUTOSTART_FILE"
            write_launchagent_plist
            ok "Auto-start on Mac boot ENABLED ($PLIST_FILE)."
            ;;
        off|disable|remove)
            rm -f "$PLIST_FILE"
            touch "$NO_AUTOSTART_FILE"
            ok "Auto-start on Mac boot REMOVED ($PLIST_FILE deleted; server will not start on reboot)."
            ;;
        status)
            if [ -f "$PLIST_FILE" ] && [ ! -f "$NO_AUTOSTART_FILE" ]; then
                ok "Auto-start on Mac boot: ENABLED ($PLIST_FILE)"
            else
                warn "Auto-start on Mac boot: DISABLED (LaunchAgent removed)"
            fi
            ;;
        *)
            err "Usage: ./server.sh autostart [on|off|status]"
            exit 1
            ;;
    esac
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

    if [ -f "$SUDOERS_FILE" ]; then
        sudo rm -f "$SUDOERS_FILE" 2>/dev/null || true
        ok "Removed power sudoers rule at $SUDOERS_FILE"
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
    doctor)
        cmd_doctor
        ;;
    bench)
        cmd_bench
        ;;
    setup-power)
        cmd_setup_power
        ;;
    autostart)
        cmd_autostart "${2:-status}"
        ;;
    uninstall|remove)
        cmd_uninstall
        ;;
    help|--help|-h|"")
        echo -e "${C_BOLD}Video Delay Server 2.0 CLI${C_RESET}"
        echo "Usage: ./server.sh <command>"
        echo ""
        echo "Commands:"
        echo "  start       Setup environment, start background server, and show QR code"
        echo "  run         Run server in foreground with live terminal output and QR code"
        echo "  stop        Stop running service and free port $PORT"
        echo "  restart     Restart the background service"
        echo "  status      Check health, active camera, buffer RAM, and local URLs"
        echo "  doctor      Run headless hardware, permission, USB speed, and log diagnostics"
        echo "  bench       Benchmark raw camera FPS, USB speed, and full pipeline FPS"
        echo "  setup-power Enable passwordless Mac Restart/Shutdown from the web control panel"
        echo "  autostart   Manage Mac boot auto-start: ./server.sh autostart [on|off|status]"
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
