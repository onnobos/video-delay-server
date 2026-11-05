import socket
import subprocess
from typing import Optional

def get_local_ip() -> str:
    """
    Determine the primary LAN IP address of this machine.
    Tries UDP routing first, then falls back to macOS network interfaces.
    """
    # 1. Check outbound route (fast, no actual packet sent)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith('127.'):
            return ip
    except Exception:
        pass

    # 2. Iterate common macOS network interfaces
    for iface in ['en0', 'en1', 'en2', 'en3', 'en4', 'bridge0', 'p2p0']:
        try:
            out = subprocess.check_output(
                ['ipconfig', 'getifaddr', iface],
                stderr=subprocess.DEVNULL
            ).decode().strip()
            if out and not out.startswith('127.'):
                return out
        except Exception:
            pass

    return '127.0.0.1'

def get_player_url(port: int = 8000) -> str:
    ip = get_local_ip()
    return f"http://{ip}:{port}/static/player.html"

def get_control_url(port: int = 8000) -> str:
    ip = get_local_ip()
    return f"http://{ip}:{port}"
