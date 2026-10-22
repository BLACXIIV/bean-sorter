"""Camera Module 3 (Picamera2) / PC webcam, and solenoid valve (gpiozero + lgpio on Pi 5)."""
import threading
import time

import cv2
import numpy as np

import config as C


def _average(read_fn, n):
    acc = None
    for _ in range(n):
        f = read_fn().astype(np.float32)
        acc = f if acc is None else acc + f
    return (acc / n).astype(np.uint8)


class Camera:
    """Fixed focus + locked exposure/white-balance so colours stay comparable
    between calibration and operation."""

    def __init__(self, saved_settings=None):
        from picamera2 import Picamera2
        from libcamera import controls

        self.cam = Picamera2()
        frame_us = int(1_000_000 / C.FPS)
        cfg = self.cam.create_video_configuration(
            main={"size": C.FRAME_SIZE, "format": "RGB888"},   # numpy order = BGR (OpenCV friendly)
            controls={"FrameDurationLimits": (frame_us, frame_us)},
            buffer_count=4)
        self.cam.configure(cfg)
        self.cam.start()
        self.cam.set_controls({"AfMode": controls.AfModeEnum.Manual,
                               "LensPosition": float(C.LENS_POSITION)})
        self.settings = None
        if saved_settings:
            self.apply(saved_settings)
        else:
            time.sleep(2.0)                 # let auto exposure / white balance settle
            self.settings = self.lock()

    def lock(self):
        md = self.cam.capture_metadata()
        s = {"ExposureTime": int(md["ExposureTime"]),
             "AnalogueGain": float(md["AnalogueGain"]),
             "ColourGains": [float(x) for x in md["ColourGains"]]}
        if C.EXPOSURE_US:
            s["ExposureTime"] = int(C.EXPOSURE_US)
        if C.ANALOGUE_GAIN:
            s["AnalogueGain"] = float(C.ANALOGUE_GAIN)
        self.apply(s)
        return s

    def apply(self, s):
        self.cam.set_controls({"AeEnable": False, "AwbEnable": False,
                               "ExposureTime": int(s["ExposureTime"]),
                               "AnalogueGain": float(s["AnalogueGain"]),
                               "ColourGains": tuple(s["ColourGains"])})
        self.settings = s
        time.sleep(0.5)

    def read(self):
        return self.cam.capture_array("main")

    def average(self, n=10):
        return _average(self.read, n)

    def close(self):
        try:
            self.cam.stop()
            self.cam.close()
        except Exception:
            pass


class WebcamCamera:
    """cv2.VideoCapture fallback for developing on a PC (no picamera2).

    No exposure/white-balance lock: settings stays None, so a Pi that later
    opens the same DB re-locks its own camera instead of applying this."""

    def __init__(self, saved_settings=None):
        self.cam = cv2.VideoCapture(C.WEBCAM_INDEX, cv2.CAP_DSHOW)
        self.cam.set(cv2.CAP_PROP_FRAME_WIDTH, C.FRAME_SIZE[0])
        self.cam.set(cv2.CAP_PROP_FRAME_HEIGHT, C.FRAME_SIZE[1])
        if not self.cam.isOpened():
            raise RuntimeError(f"webcam {C.WEBCAM_INDEX} did not open")
        self.settings = None

    def read(self):
        ok, frame = self.cam.read()
        if not ok:
            raise RuntimeError("webcam frame grab failed")
        return frame

    def average(self, n=10):
        return _average(self.read, n)

    def close(self):
        self.cam.release()


class Valve:
    def __init__(self):
        from gpiozero import OutputDevice
        self.dev = OutputDevice(C.VALVE_PIN, active_high=C.VALVE_ACTIVE_HIGH,
                                initial_value=False)
        self._lock = threading.Lock()

    def pulse(self, delay=None, duration=None):
        """Non-blocking: opens the valve after `delay`, for `duration` seconds."""
        delay = C.VALVE_DELAY_S if delay is None else delay
        duration = C.VALVE_PULSE_S if duration is None else duration
        threading.Thread(target=self._run, args=(delay, duration), daemon=True).start()

    def _run(self, delay, duration):
        if delay > 0:
            time.sleep(delay)
        with self._lock:
            try:
                self.dev.on()
                time.sleep(min(duration, C.VALVE_MAX_PULSE_S))
            finally:
                self.dev.off()

    def close(self):
        try:
            self.dev.off()
            self.dev.close()
        except Exception:
            pass


class SimulatedValve:
    def pulse(self, delay=None, duration=None):
        print(f"[valve:simulated] BLOW (delay={delay}, duration={duration})")

    def close(self):
        pass


def make_camera(saved_settings=None):
    """Pi Camera when picamera2 exists, otherwise the PC webcam."""
    try:
        return Camera(saved_settings)
    except Exception as e:
        print(f"[camera] picamera2 unavailable ({e}) - using webcam")
        return WebcamCamera()


def make_valve(dry_run=False):
    if dry_run:
        return SimulatedValve()
    try:
        return Valve()
    except Exception as e:
        print(f"[valve] GPIO unavailable ({e}) - using simulated valve")
        return SimulatedValve()
