import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import numpy as np
from src import cameras
from src.settings import StreamSettings
from src.stream import VideoDelayStream


class TestVideoDelayServer(unittest.TestCase):
    def test_camera_discovery(self):
        cams = cameras.list_available_cameras()
        self.assertIsInstance(cams, list)
        self.assertGreater(len(cams), 0, "Should detect at least one camera device")

        def_idx, def_name = cameras.find_default_camera()
        print(f"\n[TEST] Default Camera Discovered: [{def_idx}] {def_name}")

        # Verify that if HD60 S+ is connected, it is chosen as default
        has_hd60 = any("hd60" in c["name"].lower() for c in cams)
        if has_hd60:
            self.assertIn("hd60", def_name.lower(), "Default camera should be the Elgato HD60 S+")

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


if __name__ == "__main__":
    unittest.main()
