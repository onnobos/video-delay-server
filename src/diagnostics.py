import datetime
import json
import os
import platform
import subprocess
import threading
from collections import deque
from typing import Any, Dict, List, Optional

from src import cameras, network, webrtc
from src.config import LOGS_DIR, RECORDINGS_DIR
from src.logger import get_logger

logger = get_logger(__name__)

CAPTURE_USB_KEYWORDS = ("elgato", "hd60", "cam link", "capture", "uvc")
SUDOERS_POWER_FILE = "/etc/sudoers.d/video_delay_server"


def check_tcc_permission() -> Dict[str, Any]:
    """Check macOS AVFoundation camera authorization status."""
    status_map = {
        0: "not_determined",
        1: "restricted",
        2: "denied",
        3: "authorized",
    }
    try:
        import AVFoundation  # type: ignore
        code = int(AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(AVFoundation.AVMediaTypeVideo))
        state = status_map.get(code, f"unknown_{code}")
        if code == 3:
            return {
                "id": "tcc_permission",
                "title": "macOS Camera Permission (TCC)",
                "status": "ok",
                "state": state,
                "detail": "Camera access is authorized for this session.",
                "remediation": "",
            }
        if code == 0:
            cams = cameras.list_available_cameras()
            if len(cams) > 0:
                return {
                    "id": "tcc_permission",
                    "title": "macOS Camera Permission (TCC)",
                    "status": "ok",
                    "state": "authorized_uvc",
                    "detail": f"Camera capture is active ({len(cams)} device(s) accessible).",
                    "remediation": "",
                }
            return {
                "id": "tcc_permission",
                "title": "macOS Camera Permission (TCC)",
                "status": "warning",
                "state": state,
                "detail": "Camera permission has not been prompted yet.",
                "remediation": "Run './server.sh run' once inside a graphical macOS session (Screen Sharing/VNC) and click Allow.",
            }
        return {
            "id": "tcc_permission",
            "title": "macOS Camera Permission (TCC)",
            "status": "error",
            "state": state,
            "detail": f"macOS Camera access is {state}. Headless capture is blocked by TCC.",
            "remediation": "Enable Automatic Login and grant Camera permission via Screen Sharing/VNC ('./server.sh run'), or check System Settings -> Privacy & Security -> Camera.",
        }
    except Exception as e:
        return {
            "id": "tcc_permission",
            "title": "macOS Camera Permission (TCC)",
            "status": "warning",
            "state": "unknown",
            "detail": f"Could not query AVFoundation TCC status ({e}).",
            "remediation": "",
        }


def _find_usb_capture_devices(node: Any, results: List[Dict[str, str]]) -> None:
    """Recursively scan system_profiler SPUSBDataType JSON tree for capture hardware."""
    if isinstance(node, list):
        for item in node:
            _find_usb_capture_devices(item, results)
        return
    if not isinstance(node, dict):
        return

    name = str(node.get("_name", ""))
    speed = str(node.get("device_speed", ""))
    if name and any(kw in name.lower() for kw in CAPTURE_USB_KEYWORDS):
        results.append({"name": name, "speed": speed})

    for v in node.values():
        if isinstance(v, (list, dict)):
            _find_usb_capture_devices(v, results)


def check_usb_and_cameras() -> Dict[str, Any]:
    """Inspect AVFoundation cameras and USB link speeds via system_profiler."""
    cams = cameras.list_available_cameras()
    usb_devices: List[Dict[str, str]] = []

    try:
        out = subprocess.check_output(
            ["system_profiler", "SPUSBDataType", "-json"],
            stderr=subprocess.DEVNULL,
            timeout=3.0,
        ).decode("utf-8", errors="ignore")
        parsed = json.loads(out)
        _find_usb_capture_devices(parsed.get("SPUSBDataType", []), usb_devices)
    except Exception:
        pass

    usb2_warnings = []
    for dev in usb_devices:
        spd = dev["speed"].lower()
        if "480" in spd or "high_speed" in spd or "12 mb" in spd or "full_speed" in spd:
            usb2_warnings.append(f"{dev['name']} ({dev['speed']})")

    has_iphone = any("iphone" in c["name"].lower() or "continuity" in c["name"].lower() for c in cams)

    if usb2_warnings:
        return {
            "id": "usb_hardware",
            "title": "USB Capture Hardware & Speed",
            "status": "error",
            "cameras": cams,
            "usb_devices": usb_devices,
            "detail": f"Capture card negotiated USB 2.0 speed: {', '.join(usb2_warnings)}.",
            "remediation": "Unplug and reconnect with a genuine USB 3.0 / 3.1 SuperSpeed (5 Gbps) cable directly to a Mac USB-C/Thunderbolt port.",
        }

    if not cams:
        return {
            "id": "usb_hardware",
            "title": "USB Capture Hardware & Speed",
            "status": "error",
            "cameras": cams,
            "usb_devices": usb_devices,
            "detail": "No video capture devices detected by AVFoundation.",
            "remediation": "Verify the capture card USB cable is seated, power-cycle the capture card, and check Camera permissions.",
        }

    cam_summary = ", ".join(f"[{c['index']}] {c['name']}" for c in cams)
    note = " (Continuity Camera detected; name-pinning active)" if has_iphone else ""
    return {
        "id": "usb_hardware",
        "title": "USB Capture Hardware & Speed",
        "status": "ok",
        "cameras": cams,
        "usb_devices": usb_devices,
        "detail": f"Detected {len(cams)} device(s): {cam_summary}{note}",
        "remediation": "",
    }


def check_python_architecture() -> Dict[str, Any]:
    """Check whether Python is running natively (arm64) or under Rosetta 2 translation (x86_64)."""
    arch = platform.machine().lower()
    is_rosetta = False
    if platform.system() == "Darwin":
        try:
            out = subprocess.check_output(
                ["sysctl", "-n", "sysctl.proc_translated"],
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            ).decode().strip()
            is_rosetta = (out == "1")
        except Exception:
            pass

    if is_rosetta:
        return {
            "id": "python_arch",
            "title": "Python Architecture (Apple Silicon)",
            "status": "error",
            "detail": f"Python is running under Rosetta 2 emulation ({arch}), which severely throttles video FPS.",
            "remediation": "Install native ARM64 Homebrew Python ('arch -arm64 brew install python@3.11'), remove myenv/, and run './server.sh start'.",
        }

    return {
        "id": "python_arch",
        "title": "Python Architecture",
        "status": "ok",
        "detail": f"Native {arch} execution (no Rosetta overhead).",
        "remediation": "",
    }


def check_stream_and_frame_health(manager: Any) -> Dict[str, Any]:
    """Inspect active stream ingestion, FPS, per-stage timings, and frame health."""
    if not manager.is_running or not manager.stream:
        return {
            "id": "frame_health",
            "title": "Video Signal & Frame Health",
            "status": "warning",
            "detail": "Stream engine is stopped (standby). Open the player or press Start Stream to activate.",
            "remediation": "Click 'Start Stream' or 'Reset Camera Hardware' to initialize capture.",
        }

    metrics = manager.stream.get_metrics()
    health = metrics.get("frame_health", "unknown")
    hint = metrics.get("diagnostic_hint", "")
    measured = metrics.get("measured_fps", 0.0)
    tx_fps = metrics.get("transmit_fps", 0.0)
    rtc_fps = webrtc.get_webrtc_fps()
    read_ms = metrics.get("capture_read_ms", 0.0)
    proc_ms = metrics.get("capture_proc_ms", 0.0)
    target = metrics.get("fps", 30.0)
    res = metrics.get("resolution", "unknown")
    stage_info = f"cap={measured} | tx={tx_fps} | rtc={rtc_fps} FPS (read={read_ms}ms, proc={proc_ms}ms)"

    if health in ("no_signal", "black_frame", "frozen_frame"):
        return {
            "id": "frame_health",
            "title": "Video Signal & Frame Health",
            "status": "error",
            "detail": f"State: {health} ({res}, {stage_info}). {hint}".strip(),
            "remediation": hint or "Click 'Reset Camera Hardware' or check the HDMI cable and camera output.",
        }

    if measured < target * 0.7 and health != "test_pattern":
        if read_ms > 45.0:
            remedy = (
                f"Hardware/USB read bottleneck ({read_ms} ms/frame). Verify USB 3.0 (5 Gbps) cable, "
                "set camera HDMI output to 720p/1080p 30/60, or lower resolution to 960x540."
            )
        elif proc_ms > 15.0:
            remedy = (
                f"Python CPU processing bottleneck ({proc_ms} ms/frame). "
                "Try disabling 'Record while streaming' or lowering resolution to 960x540."
            )
        else:
            remedy = "Check USB 3.0 connection speed, camera output frame rate, or lower Target FPS in settings."
        return {
            "id": "frame_health",
            "title": "Video Signal & Frame Health",
            "status": "warning",
            "detail": f"Low capture frame rate at {res}: {stage_info} (target {target} FPS).",
            "remediation": remedy,
        }

    return {
        "id": "frame_health",
        "title": "Video Signal & Frame Health",
        "status": "ok",
        "detail": f"State: {health} — {res} @ {stage_info}.",
        "remediation": "",
    }


def check_storage_and_network() -> Dict[str, Any]:
    """Check free disk space in recordings directory, LAN IP, and active WebRTC peers."""
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    st = os.statvfs(RECORDINGS_DIR)
    free_gb = round((st.f_bavail * st.f_frsize) / (1024 ** 3), 2)
    local_ip = network.get_local_ip()
    viewers = webrtc.get_active_viewers_count()

    if free_gb < 1.0:
        return {
            "id": "storage_network",
            "title": "Storage & Network",
            "status": "warning",
            "detail": f"Low disk space: {free_gb} GB free | LAN IP: {local_ip} | Active viewers: {viewers}",
            "remediation": "Delete older recordings in the Gallery to free disk space.",
        }

    return {
        "id": "storage_network",
        "title": "Storage & Network",
        "status": "ok",
        "detail": f"Disk free: {free_gb} GB | LAN IP: {local_ip} | Active viewers: {viewers}",
        "remediation": "",
    }


def _tail_file(path: str, lines: int) -> List[str]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return [ln.rstrip("\n") for ln in deque(f, maxlen=lines)]
    except OSError:
        return []


def get_recent_logs(lines: int = 80) -> Dict[str, List[str]]:
    """Return recent lines from main application and error logs."""
    limit = max(10, min(500, int(lines)))
    return {
        "app_log": _tail_file(os.path.join(LOGS_DIR, "videoserver.log"), limit),
        "error_log": _tail_file(os.path.join(LOGS_DIR, "videoserver_error.log"), limit),
    }


def check_power_permissions() -> Dict[str, Any]:
    """Check whether passwordless /sbin/shutdown is configured for remote Mac power control."""
    configured = os.path.exists(SUDOERS_POWER_FILE)
    if not configured:
        try:
            res = subprocess.run(
                ["sudo", "-n", "-l", "/sbin/shutdown"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
            )
            configured = res.returncode == 0
        except Exception:
            configured = False

    if configured:
        return {
            "id": "power_control",
            "title": "Remote Mac Power Control",
            "status": "ok",
            "detail": "Passwordless sudo shutdown/restart is enabled.",
            "remediation": "",
        }
    return {
        "id": "power_control",
        "title": "Remote Mac Power Control",
        "status": "warning",
        "detail": "Using AppleScript fallback (passwordless sudo not configured yet).",
        "remediation": "Run './server.sh setup-power' once on the Mac for guaranteed headless shutdown/restart.",
    }


def execute_os_power_command(action: str) -> Dict[str, Any]:
    """Execute macOS restart or shutdown via passwordless sudo /sbin/shutdown, falling back to osascript."""
    if action not in ("restart", "shutdown"):
        raise ValueError(f"Invalid power action: {action}")

    flag = "-r" if action == "restart" else "-h"
    osa_verb = "restart" if action == "restart" else "shut down"

    sudo_err = ""
    try:
        res = subprocess.run(
            ["sudo", "-n", "/sbin/shutdown", flag, "now"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if res.returncode == 0:
            logger.info(f"Host {action} initiated via /sbin/shutdown.")
            return {"method": "shutdown", "success": True}
        sudo_err = (res.stderr or "").strip()
    except Exception as e:
        sudo_err = str(e)

    osa_err = ""
    try:
        res2 = subprocess.run(
            ["/usr/bin/osascript", "-e", f'tell application "System Events" to {osa_verb}'],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if res2.returncode == 0:
            logger.info(f"Host {action} initiated via System Events AppleScript.")
            return {"method": "osascript", "success": True}
        osa_err = (res2.stderr or "").strip()
    except Exception as e:
        osa_err = str(e)

    logger.error(f"Host {action} command failed. sudo: {sudo_err} | osascript: {osa_err}")
    return {"method": "failed", "success": False, "error": osa_err or sudo_err}


def trigger_host_power_action(action: str, manager: Any, delay_sec: float = 1.5) -> Dict[str, Any]:
    """Flush active recordings and stream cleanly, then schedule OS power action."""
    if action not in ("restart", "shutdown"):
        raise ValueError(f"Invalid power action: {action}")

    logger.info(f"System power action requested: {action}. Stopping active streams and recordings...")
    manager.stop()

    timer = threading.Timer(delay_sec, execute_os_power_command, args=(action,))
    timer.daemon = True
    timer.start()
    return {"status": "scheduled", "action": action, "delay_seconds": delay_sec}


def run_full_diagnostics(manager: Any) -> Dict[str, Any]:
    """Execute all headless diagnostic checks and summarize remediation steps."""
    checks = [
        check_tcc_permission(),
        check_python_architecture(),
        check_usb_and_cameras(),
        check_stream_and_frame_health(manager),
        check_storage_and_network(),
        check_power_permissions(),
    ]

    statuses = [c["status"] for c in checks]
    if "error" in statuses:
        overall = "error"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "ok"

    recommendations = [c["remediation"] for c in checks if c.get("remediation")]
    status_snapshot = manager.get_status()

    return {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "overall_status": overall,
        "checks": checks,
        "recommendations": recommendations,
        "telemetry": status_snapshot.get("telemetry", {}),
    }


def _print_cli_doctor(port: int = 8000) -> int:
    """Print terminal-friendly diagnostic summary for './server.sh doctor'."""
    import urllib.request

    print("\n==============================================================")
    print("  Video Delay Server — Headless Doctor Diagnostics")
    print("==============================================================")

    checks = [
        check_tcc_permission(),
        check_python_architecture(),
        check_usb_and_cameras(),
        check_storage_and_network(),
        check_power_permissions(),
    ]
    for chk in checks:
        icon = {"ok": "[OK]", "warning": "[WARN]", "error": "[FAIL]"}.get(chk["status"], "[INFO]")
        print(f"{icon:6} {chk['title']}: {chk['detail']}")
        if chk.get("remediation"):
            print(f"       -> Fix: {chk['remediation']}")

    # Query running API diagnostics if server is live
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            t = data.get("telemetry", {})
            print(f"[OK]   API Server Live on port {port} | Camera: {t.get('camera_name')} | Health: {t.get('frame_health', 'unknown')}")
    except Exception:
        print(f"[WARN] API Server is not responding on port {port}.")

    logs = get_recent_logs(10)
    if logs["error_log"]:
        print("\n--- Recent Error Log (last 10 lines) ---")
        for line in logs["error_log"]:
            print(" ", line)
    print()
    return 1 if any(c["status"] == "error" for c in checks) else 0


if __name__ == "__main__":
    import sys
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    raise SystemExit(_print_cli_doctor(port=p))
