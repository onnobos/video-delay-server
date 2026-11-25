import argparse
import os
import sys
import time
import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import cameras, diagnostics
from src.settings import StreamSettings
from src.stream import VideoDelayStream


def benchmark_raw_capture(camera_index: int, width: int, height: int, fps: int, duration: float = 2.5):
    backend = cameras.get_backend()
    cap = cv2.VideoCapture(camera_index, backend)
    if not cap.isOpened():
        print(f"  [{width}x{height}@{fps}] Failed to open camera {camera_index}")
        return None

    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)

    # Warmup
    for _ in range(5):
        cap.read()

    frame_count = 0
    read_ms_total = 0.0
    actual_shape = None
    start = time.monotonic()

    while time.monotonic() - start < duration:
        t0 = time.monotonic()
        ret, frame = cap.read()
        dt_ms = (time.monotonic() - t0) * 1000.0
        if ret and frame is not None:
            frame_count += 1
            read_ms_total += dt_ms
            actual_shape = (frame.shape[1], frame.shape[0])

    elapsed = time.monotonic() - start
    reported_fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()

    actual_fps = frame_count / max(0.001, elapsed)
    avg_read_ms = read_ms_total / max(1, frame_count)
    shape_str = f"{actual_shape[0]}x{actual_shape[1]}" if actual_shape else "none"
    print(
        f"  Target {width:4d}x{height:<4d}@{fps:2d} -> {actual_fps:5.1f} FPS | "
        f"actual={shape_str} | cap.read={avg_read_ms:5.1f}ms | reported={reported_fps:.1f}"
    )
    return actual_fps, avg_read_ms


def benchmark_pipeline(camera_index: int, width: int, height: int, fps: int, duration: float = 4.0):
    print(f"\n--- Full VideoDelayStream Pipeline Benchmark ({width}x{height}@{fps}, 1s delay) ---")
    settings = StreamSettings(delay_seconds=1, fps=fps, width=width, height=height, cam_index=camera_index)
    stream = VideoDelayStream(settings=settings, auto_start=True, headless=True)
    stream.start()
    try:
        time.sleep(duration)
        m = stream.get_metrics()
        print(
            f"  capture_fps={m['measured_fps']} | transmit_fps={m['transmit_fps']} | "
            f"cap.read={m['capture_read_ms']}ms | proc={m['capture_proc_ms']}ms | "
            f"res={m['resolution']} | health={m['frame_health']}"
        )
        if m["measured_fps"] < 20.0:
            if m["capture_read_ms"] > 45.0:
                print("  [DIAGNOSIS] Bottleneck is hardware/USB cap.read() latency. Check USB 3.0 (5 Gbps) cable & HDMI source.")
            elif m["capture_proc_ms"] > 15.0:
                print("  [DIAGNOSIS] Bottleneck is CPU processing. Lower resolution to 960x540 or disable recording.")
    finally:
        stream.stop()


def main():
    parser = argparse.ArgumentParser(description="Camera & Pipeline FPS Benchmark")
    parser.add_argument("--camera", type=int, default=None, help="Camera index (default: auto-detect Elgato HD60 S+)")
    parser.add_argument("--width", type=int, default=1280, help="Target width (default: 1280)")
    parser.add_argument("--height", type=int, default=720, help="Target height (default: 720)")
    parser.add_argument("--fps", type=int, default=30, help="Target FPS (default: 30)")
    parser.add_argument("--duration", type=float, default=2.5, help="Seconds per raw mode test (default: 2.5)")
    parser.add_argument("--bench", action="store_true", help="Run full multi-resolution + pipeline benchmark")
    args = parser.parse_args()

    arch_chk = diagnostics.check_python_architecture()
    usb_chk = diagnostics.check_usb_and_cameras()
    print(f"Python Arch : [{arch_chk['status'].upper()}] {arch_chk['detail']}")
    print(f"USB/Cameras : [{usb_chk['status'].upper()}] {usb_chk['detail']}")
    if usb_chk.get("remediation"):
        print(f"  -> Fix: {usb_chk['remediation']}")

    if args.camera is not None:
        cam_idx = args.camera
        info = cameras.get_camera_info(cam_idx)
        cam_name = info["name"] if info else f"Camera {cam_idx}"
    else:
        cam_idx, cam_name = cameras.find_default_camera()

    print(f"\nTesting Camera [{cam_idx}] {cam_name}:")
    if args.bench:
        for w, h, f in [(1280, 720, 30), (1920, 1080, 30), (960, 540, 30)]:
            benchmark_raw_capture(cam_idx, w, h, f, duration=args.duration)
        benchmark_pipeline(cam_idx, args.width, args.height, args.fps)
    else:
        benchmark_raw_capture(cam_idx, args.width, args.height, args.fps, duration=args.duration)


if __name__ == "__main__":
    main()
