import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import queue
import tempfile
import threading
import time
from collections import deque
from unittest.mock import patch, MagicMock, AsyncMock
import av
import numpy as np
from src import cameras, diagnostics
from src.logger import get_logger
from src.manager import StreamManager
from src.settings import StreamSettings
from src.stream import VideoDelayStream, VideoRecorder
from src import server, webrtc


class TestVideoDelayServer(unittest.TestCase):
    def test_camera_discovery(self):
        cams = cameras.list_available_cameras()
        self.assertIsInstance(cams, list)
        if not cams:
            self.skipTest("No cameras accessible in current sandbox/TCC environment")

        def_idx, def_name = cameras.find_default_camera()
        print(f"\n[TEST] Default Camera Discovered: [{def_idx}] {def_name}")

        # Verify that if HD60 S+ is connected, it is chosen as default
        has_hd60 = any("hd60" in c["name"].lower() for c in cams)
        if has_hd60:
            self.assertIn("hd60", def_name.lower(), "Default camera should be the Elgato HD60 S+")

    def test_camera_auto_default_and_fallback_sync(self):
        settings = StreamSettings()
        self.assertIsNone(settings.active_camera_index)

        mock_cams = [
            {"index": 0, "name": "Game Capture HD60 S+", "path": "p0", "is_capture_card": True, "is_default": True},
            {"index": 1, "name": "BRIO 4K Stream Edition", "path": "p1", "is_capture_card": False, "is_default": False},
        ]
        stream = VideoDelayStream.__new__(VideoDelayStream)
        stream.settings = settings
        with patch("src.cameras.list_available_cameras", return_value=mock_cams) as mock_list:
            idx, name = stream._resolve_camera()
            self.assertEqual((idx, name), (0, "Game Capture HD60 S+"))
            mock_list.assert_called_once()

        # Test fallback updates settings and GET /cameras reports running camera_index
        fake_bad_cap = MagicMock()
        fake_bad_cap.isOpened.return_value = False
        fake_good_cap = MagicMock()
        fake_good_cap.isOpened.return_value = True
        fake_good_cap.read.return_value = (True, np.zeros((720, 1280, 3), dtype=np.uint8))

        stream.settings = StreamSettings(cam_index=1, cam_index_secondary=0)
        stream.camera_index = 1
        stream.camera_name = "Camera 1"
        stream.width = 1280
        stream.height = 720
        with patch("src.stream.cv2.VideoCapture", side_effect=[fake_bad_cap, fake_good_cap]), \
             patch("src.cameras.get_camera_info", return_value=mock_cams[0]):
            stream._open_camera()

        self.assertEqual(stream.camera_index, 0)
        self.assertEqual(stream.camera_name, "Game Capture HD60 S+")
        self.assertEqual(stream.settings.cam_name, "Game Capture HD60 S+")

        with patch.object(server.manager, "stream", stream), \
             patch.object(server.manager, "is_running", True), \
             patch("src.cameras.list_available_cameras", return_value=mock_cams):
            resp = server.get_cameras()
            self.assertEqual(resp["active_index"], 0)

    def test_settings_model(self):
        settings = StreamSettings(delay_seconds=8, fps=30)
        self.assertEqual(settings.buffer_size, 240)
        self.assertEqual(settings.delay_seconds, 8)

    def test_standby_frame_generator(self):
        settings = StreamSettings(delay_seconds=2, fps=30, width=640, height=360)
        # Create stream in headless mode without opening physical camera
        stream = VideoDelayStream.__new__(VideoDelayStream)
        stream.width = 640
        stream.height = 360
        stream.camera_name = "Game Capture HD60 S+"

        frame = stream._generate_standby_frame()
        self.assertIsInstance(frame, np.ndarray)
        self.assertEqual(frame.shape, (360, 640, 3))
        # Ensure it has visual content (not blank)
        self.assertGreater(np.mean(frame), 10)

    def test_dynamic_delay_resizing(self):
        settings = StreamSettings(delay_seconds=3, fps=30)
        stream = VideoDelayStream.__new__(VideoDelayStream)
        stream.settings = settings
        stream.actual_fps = 30.0
        from collections import deque
        import threading
        stream.buffer_lock = threading.Lock()
        stream.buffer = deque(maxlen=90)

        # Populate buffer with dummy frames
        for _ in range(90):
            stream.buffer.append(np.zeros((10, 10, 3), dtype=np.uint8))
        self.assertEqual(len(stream.buffer), 90)

        # Dynamically resize to 5 seconds (150 frames)
        stream.set_delay(5)
        self.assertEqual(stream.settings.delay_seconds, 5)
        self.assertEqual(stream.buffer.maxlen, 150)
        self.assertEqual(len(stream.buffer), 90)

    def test_non_blocking_reconnect_standby_rate(self):
        settings = StreamSettings(delay_seconds=1, fps=30, width=320, height=240)
        stream = VideoDelayStream.__new__(VideoDelayStream)
        stream.settings = settings
        stream.running = True
        stream.headless = True
        stream.camera_index = 0
        stream.camera_name = "Test Cam"
        stream.width = 320
        stream.height = 240
        stream.actual_fps = 30.0
        stream.measured_fps = 30.0
        stream._fps_timestamps = deque(maxlen=60)
        stream._cap_lock = threading.Lock()
        stream.cap = None
        stream.buffer_lock = threading.Lock()
        stream.buffer = deque(maxlen=30)
        stream.frame_lock = threading.Lock()
        stream.latest_frame = None
        stream.delayed_frame = None
        stream.frame_seq = 0
        stream.is_signal_lost = True
        stream.consecutive_drops = 5
        stream.last_reconnect_attempt = 0.0
        stream._reconnecting = False

        def slow_capture(*args, **kwargs):
            time.sleep(0.6)
            m = MagicMock()
            m.isOpened.return_value = False
            return m

        with patch("src.stream.cv2.VideoCapture", side_effect=slow_capture):
            t = threading.Thread(target=stream.capture_loop, daemon=True)
            t.start()
            time.sleep(0.55)
            stream.running = False
            t.join(timeout=2.0)

        # Even though reconnect blocks for 0.6s, capture_loop stays at ~30 FPS (>10 frames in 0.55s)
        self.assertGreater(len(stream.buffer), 10)

    def test_video_recorder_cfr_resize_and_empty_cleanup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. Empty 0-frame recording is discarded cleanly
            empty_path = os.path.join(tmpdir, "empty.mp4")
            q_empty = queue.Queue(maxsize=50)
            rec_empty = VideoRecorder(empty_path, 320, 180, 15.0, queue=q_empty)
            rec_empty.start()
            time.sleep(0.1)
            rec_empty.stop()
            rec_empty.join(timeout=3.0)
            self.assertFalse(os.path.exists(empty_path), "0-frame MP4 should be removed")

            # 2. Mismatched resolution + slow arrival still produces valid CFR MP4
            valid_path = os.path.join(tmpdir, "valid.mp4")
            q_valid = queue.Queue(maxsize=50)
            rec_valid = VideoRecorder(valid_path, 320, 180, 15.0, queue=q_valid)
            rec_valid.start()
            # Push 3 frames of different size (640x360) spaced out over ~0.4s
            for c in (50, 120, 200):
                q_valid.put(np.full((360, 640, 3), c, dtype=np.uint8))
                time.sleep(0.14)
            rec_valid.stop()
            rec_valid.join(timeout=3.0)

            self.assertTrue(os.path.exists(valid_path))
            self.assertGreaterEqual(rec_valid.frames_written, 4)
            with av.open(valid_path) as container:
                v = container.streams.video[0]
                self.assertEqual((v.width, v.height), (320, 180))
                decoded = sum(1 for _ in container.decode(video=0))
                self.assertGreaterEqual(decoded, 4)

    def test_webrtc_rebind_pause_and_peer_cleanup(self):
        # Build two lightweight stream objects to simulate camera switch / restart
        def make_stream(color_val):
            s = VideoDelayStream.__new__(VideoDelayStream)
            s.settings = StreamSettings(delay_seconds=1, fps=30, width=320, height=240)
            s.running = True
            s.paused = False
            s.streaming = True
            s.headless = True
            s.actual_fps = 30.0
            s.width = 320
            s.height = 240
            s.buffer_lock = threading.Lock()
            s.buffer = deque([np.full((240, 320, 3), color_val, dtype=np.uint8)], maxlen=30)
            s.frame_lock = threading.Lock()
            s.delayed_frame = np.full((240, 320, 3), color_val, dtype=np.uint8)
            s.frame_seq = 1
            s.rec_lock = threading.Lock()
            s.is_recording = False
            s.recorder_thread = None
            s.rec_queue = queue.Queue(maxsize=10)
            s.current_recording_name = None
            s.thumbnail_saved = False
            return s

        s1 = make_stream(30)
        s2 = make_stream(200)
        holder = {"stream": s1}

        async def run_track_test():
            track = webrtc.VideoDelayTrack(lambda: holder["stream"])
            f1 = await track.recv()
            arr1 = f1.to_ndarray(format="rgb24")
            self.assertEqual(int(arr1[0, 0, 0]), 30)

            # Pause holds frozen frame without needing new frame_seq
            s1.paused = True
            f_paused = await track.recv()
            arr_paused = f_paused.to_ndarray(format="rgb24")
            self.assertEqual(int(arr_paused[0, 0, 0]), 30)
            self.assertGreater(f_paused.pts, f1.pts)

            # Replace stream (camera switch) -> track rebinds to s2 cleanly
            s1.running = False
            holder["stream"] = s2
            f2 = await track.recv()
            arr2 = f2.to_ndarray(format="rgb24")
            self.assertEqual(int(arr2[0, 0, 0]), 200)
            track.stop()

        asyncio.run(run_track_test())

        # transmit_loop respects pause (does not pop buffer or advance frame_seq)
        s_pause = make_stream(99)
        s_pause.settings.delay_seconds = 1
        s_pause.settings.fps = 10
        s_pause.buffer = deque([np.zeros((240, 320, 3), dtype=np.uint8)] * 10, maxlen=10)
        s_pause.paused = True
        s_pause.frame_seq = 5
        t = threading.Thread(target=s_pause.transmit_loop, daemon=True)
        t.start()
        time.sleep(0.15)
        s_pause.running = False
        t.join(timeout=2.0)
        self.assertEqual(s_pause.frame_seq, 5)
        self.assertEqual(len(s_pause.buffer), 10)

        # Viewer count excludes "connecting" peers and watchdog reaps stuck peers
        pc_connecting = MagicMock()
        pc_connecting.connectionState = "connecting"
        pc_connecting.close = AsyncMock()
        pc_connected = MagicMock()
        pc_connected.connectionState = "connected"
        pc_connected.close = AsyncMock()

        webrtc.pcs.clear()
        webrtc.pcs.add(pc_connecting)
        webrtc.pcs.add(pc_connected)
        self.assertEqual(webrtc.get_active_viewers_count(), 1)
        webrtc.pcs.clear()

        async def run_watchdog_test():
            webrtc.pcs.clear()
            fake_pc = MagicMock()
            fake_pc.connectionState = "connecting"
            fake_pc.localDescription = MagicMock(sdp="v=0", type="answer")
            fake_pc.setRemoteDescription = AsyncMock()
            fake_pc.createAnswer = AsyncMock(return_value=MagicMock())
            fake_pc.setLocalDescription = AsyncMock()
            fake_pc.close = AsyncMock()
            handlers = {}
            fake_pc.on = lambda ev: (lambda fn: handlers.setdefault(ev, fn))

            with patch("src.webrtc.RTCPeerConnection", return_value=fake_pc):
                await webrtc.handle_offer("v=0", "offer", lambda: s2, connect_timeout=0.05)
                self.assertIn(fake_pc, webrtc.pcs)
                await asyncio.sleep(0.12)
                self.assertNotIn(fake_pc, webrtc.pcs)
                fake_pc.close.assert_awaited()

        asyncio.run(run_watchdog_test())

    def test_manager_concurrency_dynamic_delay_fps_and_patch_settings(self):
        mgr = StreamManager()
        s = VideoDelayStream.__new__(VideoDelayStream)
        s.settings = mgr.settings
        s.actual_fps = float(mgr.settings.fps)
        s.buffer_lock = threading.Lock()
        s.buffer = deque(maxlen=mgr.settings.buffer_size)
        mgr.stream = s
        mgr.is_running = True

        # set_delay logs old -> new (5s -> 10s), and repeat is a no-op
        with self.assertLogs("src.stream", level="INFO") as cm:
            mgr.set_delay(10)
            mgr.set_delay(10)
        self.assertEqual(len(cm.output), 1)
        self.assertIn("5s -> 10s", cm.output[0])
        self.assertEqual(s.buffer.maxlen, 300)

        # set_fps updates target FPS and resizes buffer
        mgr.set_fps(15)
        self.assertEqual(mgr.settings.fps, 15)
        self.assertEqual(s.actual_fps, 15.0)
        self.assertEqual(s.buffer.maxlen, 150)

        # PATCH /settings handler updates delay, fps, and overlay toggles
        with patch.object(server, "manager", mgr):
            s.camera_index = 0
            s.camera_name = "Test"
            s.width = 1280
            s.height = 720
            s.measured_fps = 15.0
            s.is_signal_lost = False
            s.is_recording = False
            s.streaming = False
            s.paused = False

            req = server.SettingsUpdateRequest(
                delay_seconds=6,
                fps=20,
                show_timestamp=False,
                show_fps=False,
                show_camera_name=True,
            )
            res = server.update_settings(req)
            self.assertEqual(res["status"]["settings"]["delay_seconds"], 6)
            self.assertEqual(res["status"]["settings"]["fps"], 20)
            self.assertFalse(res["status"]["settings"]["show_timestamp"])
            self.assertTrue(res["status"]["settings"]["show_camera_name"])

        # Concurrent start/stop calls serialize under RLock without creating duplicate streams
        created_streams = []

        def fake_stream_ctor(*args, **kwargs):
            time.sleep(0.05)
            st = MagicMock()
            st.streaming = False
            st.is_recording = False
            created_streams.append(st)
            return st

        mgr2 = StreamManager()
        with patch("src.manager.VideoDelayStream", side_effect=fake_stream_ctor):
            threads = [threading.Thread(target=mgr2.start) for _ in range(5)]
            for th in threads:
                th.start()
            for th in threads:
                th.join(timeout=2.0)
        self.assertEqual(len(created_streams), 1)

    def test_frontend_reliability_and_xss_safety(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "static", "index.html"), "r") as f:
            index_html = f.read()
        with open(os.path.join(base, "static", "gallery.html"), "r") as f:
            gallery_html = f.read()

        # index.html: guarded single WebRTC startup, ICE gathering wait, fps sent, overlays synced
        self.assertIn("webrtcConnecting", index_html)
        self.assertIn("overlaysInitialized", index_html)
        self.assertIn("iceGatheringState", index_html)
        self.assertIn("fps: parseInt(document.getElementById('inputFps').value", index_html)
        self.assertNotIn("setTimeout(startWebRTC, 1000)", index_html)
        self.assertNotIn("stun.l.google.com", index_html)

        # gallery.html: no inline onclick HTML string interpolation
        self.assertNotIn('onclick="playModal', gallery_html)
        self.assertNotIn('onclick="deleteVideo', gallery_html)
        self.assertIn("addEventListener('click'", gallery_html)

    def test_logging_rotation_filter_and_favicon(self):
        l1 = get_logger("test.logger.one")
        l2 = get_logger("test.logger.two")
        self.assertEqual(len(l1.handlers), 1)
        self.assertIsInstance(l1.handlers[0], RotatingFileHandler)
        self.assertIs(l1.handlers[0], l2.handlers[0])

        flt = server.StatusEndpointFilter()
        rec_status = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '127.0.0.1 - "GET /status HTTP/1.1" 200 OK', (), None)
        rec_offer = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '127.0.0.1 - "POST /offer HTTP/1.1" 200 OK', (), None)
        rec_err = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '127.0.0.1 - "GET /status HTTP/1.1" 500 Internal Server Error', (), None)
        self.assertFalse(flt.filter(rec_status))
        self.assertTrue(flt.filter(rec_offer))
        self.assertTrue(flt.filter(rec_err))

        fav = server.favicon()
        self.assertEqual(fav.status_code, 204)

    def test_frame_health_black_frozen_and_test_pattern(self):
        s = VideoDelayStream.__new__(VideoDelayStream)
        s.settings = StreamSettings(delay_seconds=1, fps=30, width=320, height=240)
        s.width = 320
        s.height = 240
        s.frame_health = "healthy"
        s.diagnostic_hint = ""
        s._black_frame_streak = 0
        s._frozen_frame_streak = 0
        s._last_sample_small = None
        s._test_pattern_tick = 0
        s.test_pattern_mode = False
        s.is_signal_lost = False

        # 30 black frames -> black_frame warning
        black = np.zeros((240, 320, 3), dtype=np.uint8)
        for _ in range(30):
            self.assertTrue(s._analyze_frame_health(black))
        self.assertEqual(s.frame_health, "black_frame")

        # Varying bright frames recover to healthy
        f1 = np.full((240, 320, 3), 80, dtype=np.uint8)
        f2 = np.full((240, 320, 3), 120, dtype=np.uint8)
        self.assertTrue(s._analyze_frame_health(f1))
        self.assertTrue(s._analyze_frame_health(f2))
        self.assertEqual(s.frame_health, "healthy")

        # Identical frames -> frozen_frame at 60, auto-heal trigger (False) at 150
        static = np.full((240, 320, 3), 100, dtype=np.uint8)
        for i in range(155):
            res = s._analyze_frame_health(static)
            if i == 65:
                self.assertEqual(s.frame_health, "frozen_frame")
            if not res:
                break
        self.assertFalse(res)

        # Synthetic test pattern animates across ticks
        tp1 = s._generate_test_pattern_frame()
        tp2 = s._generate_test_pattern_frame()
        self.assertEqual(tp1.shape, (240, 320, 3))
        self.assertFalse(np.array_equal(tp1, tp2))

        mgr = StreamManager()
        mgr.stream = s
        mgr.is_running = True
        s.camera_index = 0
        s.camera_name = "Test"
        s.actual_fps = 30.0
        s.measured_fps = 30.0
        s.buffer_lock = threading.Lock()
        s.buffer = deque(maxlen=30)
        s.is_recording = False
        s.streaming = False
        s.paused = False
        s.reconnect_attempts = 0
        st = mgr.set_test_pattern(True)
        self.assertTrue(st["telemetry"]["test_pattern"])
        self.assertEqual(st["telemetry"]["frame_health"], "test_pattern")

    def test_name_pinned_reconnect_handles_index_shift(self):
        s = VideoDelayStream.__new__(VideoDelayStream)
        s.settings = StreamSettings(width=320, height=240, fps=30)
        s.camera_index = 0
        s.camera_name = "Game Capture HD60 S+"
        s.preferred_camera_name = "Game Capture HD60 S+"
        s._cap_lock = threading.Lock()
        s.cap = None
        s.width = 320
        s.height = 240
        s.consecutive_drops = 5
        s.is_signal_lost = True
        s._reconnecting = True
        s.reconnect_attempts = 0
        s._black_frame_streak = 0
        s._frozen_frame_streak = 0
        s.frame_health = "no_signal"
        s.diagnostic_hint = ""

        # Simulate iPhone Continuity Camera taking index 0 and HD60 S+ moving to index 1
        shifted_cams = [
            {"index": 0, "name": "iPhone OB (2) Camera", "path": "p0", "is_capture_card": False, "is_default": False},
            {"index": 1, "name": "Game Capture HD60 S+", "path": "p1", "is_capture_card": True, "is_default": True},
        ]
        good_cap = MagicMock()
        good_cap.isOpened.return_value = True
        good_cap.read.return_value = (True, np.full((240, 320, 3), 100, dtype=np.uint8))

        with patch("src.cameras.list_available_cameras", return_value=shifted_cams), \
             patch("src.stream.cv2.VideoCapture", return_value=good_cap) as mock_vc:
            s._attempt_reconnect()

        mock_vc.assert_called_with(1, cameras.get_backend())
        self.assertEqual(s.camera_index, 1)
        self.assertFalse(s.is_signal_lost)
        self.assertEqual(s.frame_health, "healthy")
        self.assertFalse(s._reconnecting)

    def test_diagnostics_engine_and_endpoints(self):
        # Test USB 2.0 speed detection in check_usb_and_cameras
        fake_usb = '{"SPUSBDataType": [{"_name": "Game Capture HD60 S+", "device_speed": "high_speed (480 Mb/sec)"}]}'
        fake_cams = [{"index": 0, "name": "Game Capture HD60 S+", "is_capture_card": True, "is_default": True}]
        with patch("src.cameras.list_available_cameras", return_value=fake_cams), \
             patch("src.diagnostics.subprocess.check_output", return_value=fake_usb.encode("utf-8")):
            usb_res = diagnostics.check_usb_and_cameras()
            self.assertEqual(usb_res["status"], "error")
            self.assertIn("USB 2.0", usb_res["detail"])

        # Test full diagnostics + server endpoints
        mgr = StreamManager()
        with patch.object(server, "manager", mgr), \
             patch("src.diagnostics.check_tcc_permission", return_value={"id": "tcc_permission", "title": "TCC", "status": "ok", "detail": "ok", "remediation": ""}), \
             patch("src.diagnostics.check_usb_and_cameras", return_value={"id": "usb_hardware", "title": "USB", "status": "ok", "detail": "ok", "remediation": ""}):
            diag = server.get_diagnostics()
            self.assertIn("overall_status", diag)
            self.assertEqual(len(diag["checks"]), 5)

            logs = server.get_server_logs(lines=20)
            self.assertIn("app_log", logs)
            self.assertIn("error_log", logs)

        with patch.object(server.manager, "recover_hardware", return_value={"process_running": True}) as mock_rec, \
             patch.object(server.manager, "set_test_pattern", return_value={"telemetry": {"test_pattern": True}}) as mock_tp:
            rec_res = server.recover_hardware()
            self.assertIn("reset", rec_res["message"])
            mock_rec.assert_called_once()

            tp_res = server.toggle_test_pattern(server.TestPatternRequest(enabled=True))
            self.assertIn("enabled", tp_res["message"])
            mock_tp.assert_called_once_with(True)

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "static", "index.html"), "r") as f:
            index_html = f.read()
        self.assertIn("/diagnostics/recover", index_html)
        self.assertIn("/diagnostics/test-pattern", index_html)
        self.assertIn("/logs?lines=80", index_html)

    def test_host_power_actions_and_endpoints(self):
        # 1. execute_os_power_command uses sudo -n /sbin/shutdown first
        ok_res = MagicMock(returncode=0, stderr="")
        with patch("src.diagnostics.subprocess.run", return_value=ok_res) as mock_run:
            res = diagnostics.execute_os_power_command("restart")
            self.assertEqual(res, {"method": "shutdown", "success": True})
            mock_run.assert_called_once()
            self.assertEqual(mock_run.call_args[0][0], ["sudo", "-n", "/sbin/shutdown", "-r", "now"])

        # 2. Falls back to osascript when sudo -n fails
        fail_res = MagicMock(returncode=1, stderr="password required")
        osa_ok = MagicMock(returncode=0, stderr="")
        with patch("src.diagnostics.subprocess.run", side_effect=[fail_res, osa_ok]) as mock_run:
            res = diagnostics.execute_os_power_command("shutdown")
            self.assertEqual(res, {"method": "osascript", "success": True})
            self.assertEqual(mock_run.call_count, 2)
            self.assertEqual(mock_run.call_args_list[1][0][0][0], "/usr/bin/osascript")
            self.assertIn("shut down", mock_run.call_args_list[1][0][0][2])

        # 3. trigger_host_power_action stops manager cleanly before running command
        fake_mgr = MagicMock()
        with patch("src.diagnostics.execute_os_power_command") as mock_exec:
            sched = diagnostics.trigger_host_power_action("restart", fake_mgr, delay_sec=0.02)
            fake_mgr.stop.assert_called_once()
            self.assertEqual(sched["status"], "scheduled")
            self.assertEqual(sched["action"], "restart")
            time.sleep(0.06)
            mock_exec.assert_called_once_with("restart")

        # 4. FastAPI power endpoints close WebRTC and schedule power action
        async def run_endpoints():
            with patch("src.webrtc.close_all", new=AsyncMock()) as mock_close, \
                 patch("src.diagnostics.trigger_host_power_action", return_value={"status": "scheduled", "action": "restart"}) as mock_trig:
                r1 = await server.restart_mac()
                self.assertEqual(r1["action"], "restart")
                mock_close.assert_awaited_once()
                mock_trig.assert_called_once_with("restart", server.manager)

            with patch("src.webrtc.close_all", new=AsyncMock()) as mock_close, \
                 patch("src.diagnostics.trigger_host_power_action", return_value={"status": "scheduled", "action": "shutdown"}) as mock_trig:
                r2 = await server.shutdown_mac()
                self.assertEqual(r2["action"], "shutdown")
                mock_close.assert_awaited_once()
                mock_trig.assert_called_once_with("shutdown", server.manager)

        asyncio.run(run_endpoints())

        # 5. UI contains confirmation modal and power endpoints
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "static", "index.html"), "r") as f:
            index_html = f.read()
        self.assertIn("powerModal", index_html)
        self.assertIn("/system/restart-mac", index_html)
        self.assertIn("/system/shutdown-mac", index_html)


if __name__ == "__main__":
    unittest.main()
