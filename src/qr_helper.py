import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.network import get_local_ip, get_player_url, get_control_url

def display_banner(port: int = 8000, show_qr: bool = True):
    local_ip = get_local_ip()
    player_url = get_player_url(port)
    control_url = get_control_url(port)

    print("\n" + "=" * 62)
    print("      🚀 VIDEO DELAY SERVER 2.0 (APPLE SILICON READY)")
    print("=" * 62)
    print(f"  • Local Machine:   http://localhost:{port}")
    print(f"  • Control Panel:   {control_url}")
    print(f"  • Fullscreen View: {player_url}")
    print("=" * 62)

    if show_qr and local_ip != "127.0.0.1":
        try:
            import qrcode
            print("\n  📱 SCAN WITH YOUR PHONE TO OPEN FULLSCREEN LIVE DELAY:")
            print()
            qr = qrcode.QRCode(border=1)
            qr.add_data(player_url)
            qr.print_ascii(invert=True)
            print()
        except ImportError:
            pass

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    display_banner(port=port)
