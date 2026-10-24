#!/usr/bin/env python3
"""STEP 5 - time source proof.

engine.Engine.loop() picks the frame timestamp as

    now   = time.perf_counter()              # wall clock, always
    t_sim = getattr(self.cam, "sim_t", None) # only a schedule-mode sim camera
    sorter.process(..., t=now if t_sim is None else t_sim)

This test runs the REAL loop (no copied logic) with three cameras:

  a) NonSimCam      - no sim_t attribute at all (like WebcamCamera/Pi Camera)
  b) SimulatedCamera non-schedule - sim_t exists but stays None
  c) SimulatedCamera schedule mode - sim_t advances by sim_dt each read

For (a) and (b) every t handed to the sorter must be a perf_counter value:
inside the measured wall-clock window, and never a small sim-second number.
For (c) every step between consecutive t's must equal sim_dt exactly, proving
sim time flows ONLY because the camera exposes sim_t - it is opt-in, never
silently mixed in.
"""
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["BEAN_DB"] = str(ROOT / "var" / "test_timesrc.db")  # temp DB

import numpy as np                                          # noqa: E402

import config as C                                          # noqa: E402
import hardware                                             # noqa: E402
from engine import Engine                                   # noqa: E402

checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


class NonSimCam:
    """Stands in for WebcamCamera / Pi Camera: read() -> frame, close(),
    settings - and NO sim_t attribute (never defined, never set)."""
    settings = {}

    def read(self):
        return np.zeros((C.FRAME_SIZE[1], C.FRAME_SIZE[0], 3), np.uint8)

    def close(self):
        pass


class SpySorter:
    def __init__(self):
        self.ts = []

    def process(self, frame, armed=True, tim=None, t=None,
                detections=None, annotate=False):
        self.ts.append(t)
        return frame


def run_engine(cam_maker, seconds=0.4):
    """Run the real Engine.loop() briefly, return (ts, t0, t1)."""
    e = Engine(camera_source="sim", valve_source="sim")
    e.cam.close()
    e.cam = cam_maker()
    e.mode = "run"
    e.sorter = spy = SpySorter()
    t0 = time.perf_counter()
    th = threading.Thread(target=e.loop, daemon=True)
    th.start()
    time.sleep(seconds)
    e.stop()
    th.join(timeout=5)
    t1 = time.perf_counter()
    e.close()
    return spy.ts, t0, t1


# ---------------------------------------------------------------- a) non-sim
ts, t0, t1 = run_engine(NonSimCam)
check("non-sim camera: sorter saw frames", len(ts) >= 5, f"n={len(ts)}")
check("non-sim camera: every t is perf_counter",
      all(t0 - 0.05 <= t <= t1 + 0.05 for t in ts),
      f"t range {min(ts):.4f}..{max(ts):.4f} within "
      f"[{t0:.4f},{t1:.4f}]" if ts else "")
check("non-sim camera: sim time never used",
      all(t > 1000.0 for t in ts),
      "all t are wall-clock perf_counter (>> 1e3s since boot), none look "
      "like sim seconds starting near 0")

# --------------------------------------------- b) sim camera, non-schedule
os.environ.pop("BEAN_SIM_SCHEDULE", None)
ts, t0, t1 = run_engine(hardware.SimulatedCamera)
cam2 = hardware.SimulatedCamera()
check("non-schedule SimulatedCamera: sim_t stays None",
      cam2.sim_t is None and getattr(cam2, "_sched_on") is False,
      f"sim_t={cam2.sim_t}")
cam2.close()
check("non-schedule sim camera: every t is perf_counter",
      len(ts) >= 5 and all(t0 - 0.05 <= t <= t1 + 0.05 for t in ts),
      f"n={len(ts)} range {min(ts):.4f}..{max(ts):.4f}")

# ------------------------------------------------- c) sim camera, schedule
# _step() (which advances sim_t) only runs in the "moving" scene, exactly as
# Engine.set_mode("run") does via cam.set_scene("moving").
os.environ["BEAN_SIM_SCHEDULE"] = "1"
os.environ["BEAN_SIM_DT"] = "0.05"
os.environ["BEAN_SIM_SECS"] = "30"


def _sched_cam():
    c = hardware.SimulatedCamera()
    c.set_scene("moving")
    return c


ts, t0, t1 = run_engine(_sched_cam)
os.environ.pop("BEAN_SIM_SCHEDULE", None)
steps = [b - a for a, b in zip(ts, ts[1:])]
check("schedule sim camera: t follows sim_t (step == sim_dt)",
      len(ts) >= 5 and all(abs(d - 0.05) < 1e-9 for d in steps)
      and ts[0] < 1.0,
      f"n={len(ts)} first={ts[0]:.3f} max step dev "
      f"{max((abs(d - 0.05) for d in steps), default=0):.2e}")
check("schedule sim camera: t is NOT wall clock",
      all(t < 10.0 for t in ts),
      f"max t={max(ts):.3f} << perf_counter ~{t0:.0f}")

# ----------------------------------- structural: real camera classes never
# define or assign sim_t (grep their source; instance attrs included)
src_webcam = hardware.WebcamCamera.__dict__ and True
import inspect                                             # noqa: E402
check("WebcamCamera source never mentions sim_t",
      "sim_t" not in inspect.getsource(hardware.WebcamCamera))
check("Pi Camera source never mentions sim_t",
      "sim_t" not in inspect.getsource(hardware.Camera))

print("=" * 50)
print(f"{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
