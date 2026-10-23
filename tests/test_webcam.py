#!/usr/bin/env python3
"""WebcamCamera unit tests - cv2.VideoCapture is mocked, no hardware needed."""
import sys
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C
from hardware import WebcamCamera


def _cap(frame=(480, 640, 3), ok=True):
    """Fake VideoCapture: returns `frame` (or read-failure) forever."""
    c = mock.MagicMock()
    c.isOpened.return_value = True
    f = np.full(frame, 120, np.uint8) if frame is not None else None
    c.read.return_value = (ok, f)
    return c


class TestWebcamCamera(unittest.TestCase):

    @mock.patch("cv2.VideoCapture")
    def test_resize_to_frame_size(self, VC):
        VC.return_value = _cap()                      # 640x480 != FRAME_SIZE
        cam = WebcamCamera()
        out = cam.read()
        self.assertEqual((out.shape[1], out.shape[0]), C.FRAME_SIZE)
        cam.close()

    @mock.patch("cv2.VideoCapture")
    def test_read_failure_raises(self, VC):
        VC.return_value = _cap(ok=False)
        cam = WebcamCamera()
        with self.assertRaisesRegex(RuntimeError, "Webcam read failed"):
            cam.read()

    @mock.patch("sys.platform", "win32")
    @mock.patch("cv2.VideoCapture")
    def test_dshow_on_windows(self, VC):
        VC.return_value = _cap()
        WebcamCamera()
        VC.assert_called_once_with(0, cv2.CAP_DSHOW)

    @mock.patch("sys.platform", "linux")
    @mock.patch("cv2.VideoCapture")
    def test_no_dshow_on_linux(self, VC):
        VC.return_value = _cap()
        WebcamCamera()
        VC.assert_called_once_with(0)               # plain open, no DSHOW

    @mock.patch("cv2.VideoCapture")
    def test_requests_size_and_discards_5(self, VC):
        c = _cap()
        VC.return_value = c
        WebcamCamera()
        # 1280x720@30 requested
        c.set.assert_any_call(cv2.CAP_PROP_FRAME_WIDTH, C.FRAME_SIZE[0])
        c.set.assert_any_call(cv2.CAP_PROP_FRAME_HEIGHT, C.FRAME_SIZE[1])
        c.set.assert_any_call(cv2.CAP_PROP_FPS, 30)
        # 5 warm-up reads + 1 speed-check read in __init__
        self.assertEqual(c.read.call_count, 6)

    @mock.patch("cv2.VideoCapture")
    def test_closed_camera_raises(self, VC):
        c = _cap()
        c.isOpened.return_value = False
        VC.return_value = c
        with self.assertRaisesRegex(RuntimeError, "did not open"):
            WebcamCamera()


if __name__ == "__main__":
    unittest.main(verbosity=2)
