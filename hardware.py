"""Camera Module 3 (Picamera2) / PC webcam, and solenoid valve (gpiozero + lgpio on Pi 5)."""
import math
import os
import sys
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
    """cv2.VideoCapture for developing on a PC (no picamera2).

    No exposure/white-balance lock: webcams drift, so webcam colours are for
    UI/flow testing only - never reuse them on the Pi camera."""

    def __init__(self, index=0, saved_settings=None):
        self.index = index
        self._open(C.FRAME_SIZE)
        try:
            self.cam.set(cv2.CAP_PROP_AUTO_WB, 0)
            self.cam.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0)
        except Exception:
            pass                            # many drivers ignore these
        if not self.cam.isOpened():
            raise RuntimeError(f"webcam {index} did not open")
        for _ in range(5):                  # discard warm-up frames
            self.cam.read()
        # some cams manage only ~1 fps at 720p (uncompressed USB); if a frame
        # takes >300 ms, re-open at 640x480 - read() upscales to FRAME_SIZE
        t = time.perf_counter()
        self.cam.read()
        if time.perf_counter() - t > 0.3:
            self.cam.release()
            self._open((640, 480))
        self.settings = {"webcam": True}

    def _open(self, size):
        if sys.platform == "win32":
            self.cam = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        else:
            self.cam = cv2.VideoCapture(self.index)
        self.cam.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
        self.cam.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
        self.cam.set(cv2.CAP_PROP_FPS, 30)

    def read(self):
        ok, frame = self.cam.read()
        if not ok:
            raise RuntimeError("Webcam read failed")
        if (frame.shape[1], frame.shape[0]) != C.FRAME_SIZE:
            frame = cv2.resize(frame, C.FRAME_SIZE, interpolation=cv2.INTER_AREA)
        return frame

    def average(self, n=10):
        return _average(self.read, n)

    def close(self):
        self.cam.release()


class SimulatedCamera:
    """Synthetic frames for testing without any hardware.

    Blue conveyor with 3 darker horizontal seams; in "moving" the seams scroll
    with the beans. set_scene("empty") = belt only; "static" = belt + a 4x4
    grid, column 1..4 = grade 1..4 at GRID_X x GRID_Y; "moving" = random beans
    (~75 % grades 1-3, ~25 % grade 4) falling at ~25 px/frame, crossing mid.
    """

    BELT_BGR = (150, 110, 60)                     # blue conveyor
    SEAM_DARKER = (12, 24, 36)                    # per-seam darkening (BGR units)
    SEAM_T = 14                                   # seam line thickness, px
    GRADE_BGR = {1: (40, 90, 150), 2: (45, 80, 125), 3: (50, 70, 100), 4: (25, 30, 40)}
    PATCH_BGR = (30, 120, 190)                    # yellow-brown defect patch on G4
    GRID_X = (160, 480, 800, 1120)                # column per grade
    GRID_Y = (90, 270, 450, 630)
    BEAN_R = 40
    SPEED = 25
    MAX_BEANS = 8
    SIM_DT = 1.0 / 20.0                           # fixed sim-time step/frame

    def __init__(self, saved_settings=None):
        self._w, self._h = C.FRAME_SIZE
        self._rng = np.random.default_rng(7)
        self._scene = "empty"
        self._beans = []
        self._seam_y = [self._h // 4, self._h // 2, 3 * self._h // 4]
        self._base = None                         # cached plain belt frame
        self._noise = [self._rng.integers(-8, 9, (self._h, self._w, 3),
                                          dtype=np.int16) for _ in range(4)]
        self._noise_i = 0
        self.settings = None
        # ground truth for tracking verification (sim only)
        self._sort_line = None                  # object with .side((x,y))
        self.spawned_total = 0
        self.crossed_total = 0
        self.crossed_defects = 0
        self.active_count = 0
        # per-physical-bean truth: id -> {id, grade, crossed, cross_t,
        #   cross_f, frames_visible, x_at_line}; valve fires attribute to
        #   the live bean nearest the line (see SimulatedValve).
        self.bean_truth = {}
        self._next_bid = 1
        self._frame_i = 0
        # BEAN_SIM_SCHEDULE=1: spawns follow a fixed schedule on SIMULATED time
        # (frame index * SIM_DT), identical no matter how fast the loop runs,
        # so paired tracker runs see the same bean sequence. BEAN_SIM_SECS
        # stops spawning after that much sim time (drain follows);
        # BEAN_SIM_SPEED overrides belt speed in px/s (default 500 = 25px/fr);
        # BEAN_SIM_SEED changes the spawn RNG (paired runs keep the same seed).
        self._sched_on = bool(os.environ.get("BEAN_SIM_SCHEDULE"))
        self._sim_dt = float(os.environ.get("BEAN_SIM_DT") or self.SIM_DT)
        self._sim_t = 0.0
        self.sim_t = None                  # current sim seconds (sched only)
        self._next_spawn = 0.0
        self._spawn_until = float(os.environ.get("BEAN_SIM_SECS") or 0.0)
        self._speed_pxs = float(os.environ.get("BEAN_SIM_SPEED") or 0.0) \
            or self.SPEED / self._sim_dt        # default 25 px/frame
        self.step_px = self._speed_pxs * self._sim_dt \
            if self._sched_on else self.SPEED   # per-frame bean travel
        seed = os.environ.get("BEAN_SIM_SEED")
        if seed is not None:
            self._rng = np.random.default_rng(int(seed))

    def set_scene(self, scene):
        self._scene = scene
        self._beans = []

    def set_sort_line(self, line):
        """Feed the engine's sorting line (anything with .side((x,y))) so the
        sim can count beans that PHYSICALLY cross it. None disables."""
        self._sort_line = line

    def _belt(self):
        if self._base is None:
            self._base = np.tile(np.array(self.BELT_BGR, np.uint8),
                                 (self._h, self._w, 1))
        if self._scene == "moving":
            step = self._speed_pxs * self._sim_dt if self._sched_on else self.SPEED
            self._seam_y = [(y + step) % self._h for y in self._seam_y]
        frame = self._base.copy()
        for y, dark in zip(self._seam_y, self.SEAM_DARKER):
            bgr = tuple(max(0, c - dark) for c in self.BELT_BGR)
            iy = int(round(y))              # seams can be float in sched mode
            cv2.rectangle(frame, (0, iy - self.SEAM_T // 2),
                          (self._w, iy + self.SEAM_T // 2), bgr, -1)
        return frame

    def read(self):
        # pace like a camera (a real 30 fps cam blocks ~33 ms) but leave headroom
        # so sim + processing stays above ~20 fps on a laptop
        time.sleep(0.01)
        frame = self._belt()
        if self._scene == "static":
            for g, x in enumerate(self.GRID_X, start=1):
                for y in self.GRID_Y:
                    self._draw_bean(frame, x, y, g, self.BEAN_R)
        elif self._scene == "moving":
            self._step()
            for b in self._beans:
                self._draw_bean(frame, b[0], b[1], b[2], b[3])
        # fixed noise pool: sim palette never overflows +/-8 (16..228), no clip needed
        # schedule mode indexes noise by FRAME so paired runs are pixel-
        # identical; otherwise the index drifts with real-time pre-run reads.
        self._noise_i = (self._frame_i if self._sched_on
                         else self._noise_i + 1) % len(self._noise)
        return (frame.astype(np.int16) + self._noise[self._noise_i]).astype(np.uint8)

    def _draw_bean(self, frame, x, y, grade, r):
        x, y = int(round(x)), int(round(y))
        cv2.circle(frame, (x, y), r, self.GRADE_BGR[grade], -1, cv2.LINE_AA)
        if grade == 4:
            cv2.circle(frame, (x + r // 3, y - r // 3), r // 3, self.PATCH_BGR, -1, cv2.LINE_AA)

    def _step(self):
        if self._sched_on:
            self._sim_t += self._sim_dt
            self.sim_t = self._sim_t
        self._frame_i += 1
        step = self._speed_pxs * self._sim_dt if self._sched_on else self.SPEED
        for b in self._beans:
            b[1] += step
            if not b[5] and b[1] + b[3] > 0:
                b[7] += 1                    # visible frame, not yet crossed
            if self._sort_line is not None:
                s = self._sort_line.side((b[0], b[1])) >= 0
                if b[4] is not None and b[4] != s and not b[5]:
                    b[5] = True                    # physically crossed, once
                    b[8] = self._sim_t if self._sched_on else self._frame_i
                    truth = self.bean_truth[b[6]]
                    truth["crossed"] = True
                    truth["cross_t"] = b[8]
                    truth["cross_f"] = self._frame_i
                    truth["x_at_line"] = b[0]
                    truth["frames_visible"] = b[7]
                    self.crossed_total += 1
                    if b[2] == 4:
                        self.crossed_defects += 1
                b[4] = s
        self._beans = [b for b in self._beans if b[1] < self._h + 60]
        tries = 0
        while len(self._beans) < self.MAX_BEANS and tries < 50:
            if self._sched_on and (self._sim_t >= self._spawn_until
                                   or self._sim_t < self._next_spawn):
                break                            # fixed schedule: not yet/never
            tries += 1
            x = float(self._rng.integers(80, self._w - 80))
            y = float(-self.BEAN_R - self._rng.integers(0, 400))
            grade = 4 if self._rng.random() < 0.25 else int(self._rng.integers(1, 4))
            r = int(self._rng.integers(30, 46))
            if any(abs(b[0] - x) < 2 * self.BEAN_R + 20 and abs(b[1] - y) < 2 * self.BEAN_R + 20
                   for b in self._beans):
                continue
            bid = self._next_bid
            self._next_bid += 1
            self._beans.append([x, y, grade, r, None, False, bid, 0, None])
            self.bean_truth[bid] = {"id": bid, "grade": grade, "r": r,
                                    "x_spawn": x, "y_spawn": y,
                                    "spawn_f": self._frame_i,
                                    "crossed": False, "cross_t": None,
                                    "cross_f": None, "x_at_line": None,
                                    "frames_visible": 0}
            self.spawned_total += 1
            if self._sched_on:
                # one bean per scheduled slot; next slot on sim time
                self._next_spawn = self._sim_t + float(self._rng.uniform(0.3, 0.8))
                break
        self.active_count = len(self._beans)

    def nearest_to_line(self, t_c=None, x_on_line=None):
        """Which physical bean a valve fire is for. With a crossing anchor
        (t_c = the tracker's crossing time, x_on_line = where on the line):
        the bean whose PHYSICAL crossing is closest in time, x tie-broken.
        Without an anchor: the live bean nearest the line right now.
        Returns a copy of its truth record + dist/score."""
        if self._sort_line is None:
            return None
        if t_c is not None:
            crossed = [b for b in self.bean_truth.values() if b["crossed"]]
            if not crossed:
                return None
            def score(b):
                dx = abs((b["x_at_line"] or 0) - (x_on_line or 0))
                return abs(b["cross_t"] - t_c) + dx / 1000.0
            best = min(crossed, key=score)
            rec = dict(best)
            rec["dist_px"] = score(best)
            return rec
        if not self._beans:
            return None
        ln = math.sqrt(self._sort_line.len2)
        best, best_d = None, 1e18
        for b in self._beans:
            d = abs(self._sort_line.side((b[0], b[1]))) / ln
            if d < best_d:
                best, best_d = b, d
        if best is None:
            return None
        rec = dict(self.bean_truth.get(best[6], {}))
        rec["dist_px"] = best_d
        return rec

    def average(self, n=10):
        return _average(self.read, n)

    def close(self):
        pass


class Valve:
    def __init__(self):
        from gpiozero import OutputDevice
        self.dev = OutputDevice(C.VALVE_PIN, active_high=C.VALVE_ACTIVE_HIGH,
                                initial_value=False)
        self._lock = threading.Lock()

    def pulse(self, delay=None, duration=None, uncertain=False,
              t_c=None, x_on_line=None):
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
    """No hardware. When .ground is set to a SimulatedCamera, every pulse is
    recorded and attributed to the physical bean nearest the line at that
    instant -> fires = [{t, bean_id, grade, dist_px, uncertain}]."""
    def __init__(self):
        self.ground = None                  # SimulatedCamera or None
        self.fires = []

    def pulse(self, delay=None, duration=None, uncertain=False,
              t_c=None, x_on_line=None):
        delay = C.VALVE_DELAY_S if delay is None else delay
        duration = C.VALVE_PULSE_S if duration is None else duration
        bean = (self.ground.nearest_to_line(t_c, x_on_line)
                if self.ground is not None else None)
        t = getattr(self.ground, "sim_t", None)
        self.fires.append({"t": t, "t_c": t_c, "x_on_line": x_on_line,
                           "bean_id": bean and bean.get("id"),
                           "grade": bean and bean.get("grade"),
                           "dist_px": bean and bean.get("dist_px"),
                           "uncertain": bool(uncertain)})
        print(f"[valve:simulated] BLOW (delay={delay}, duration={duration})")

    def close(self):
        pass


def make_camera(source="pi", saved_settings=None):
    """source: "pi" (Picamera2, webcam fallback if unavailable), "webcam" or
    "webcam:N" (PC camera index N), "sim" (SimulatedCamera)."""
    if source == "sim":
        return SimulatedCamera()
    if source == "webcam" or source.startswith("webcam:"):
        idx = int(source.split(":", 1)[1]) if ":" in source else C.WEBCAM_INDEX
        return WebcamCamera(idx)
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
