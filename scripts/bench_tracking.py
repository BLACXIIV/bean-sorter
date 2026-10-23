#!/usr/bin/env python3
"""Benchmark TrajectoryTracker.update with 10 / 30 / 60 detections per frame.

  python scripts/bench_tracking.py [frames]
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C                      # noqa: E402
import vision                           # noqa: E402
from sorter import Line                 # noqa: E402
from tracking import TrajectoryTracker  # noqa: E402

GRADE_LAB = np.array([60.0, 0.0, 0.0], np.float32)


def pal():
    return [vision.PaletteColor(GRADE_LAB.copy(), 1.0, "#000000")]


def bench(n_dets, frames=60):
    """n_dets beans moving down at ~400 px/s in a grid; measure update() ms."""
    C.TRACK_HUNGARIAN_MAX = 10**9          # force Hungarian for this bench
    rng = np.random.default_rng(1)
    cols = max(1, int(np.ceil(np.sqrt(n_dets * 1280 / 720))))
    xs = [60 + (i % cols) * (1160 / cols) for i in range(n_dets)]
    tracker = TrajectoryTracker(Line((0, 360), (1280, 360)))
    t = 0.0
    ms = []
    # warm up: get beans on screen and confirmed
    ys = [-60 - (i // cols) * 90 for i in range(n_dets)]
    for f in range(frames):
        t += 0.05                        # 20 fps
        dets = [{"centroid": (x + float(rng.uniform(-2, 2)), y),
                 "area": 3000.0, "contour": None, "palette": pal()}
                for x, y in zip(xs, ys) if -60 < y < 780]
        t0 = time.perf_counter()
        tracker.update(dets, t)
        ms.append((time.perf_counter() - t0) * 1000)
        ys = [y + 20 for y in ys]
    return np.mean(ms), np.max(ms)


def main():
    import platform
    print(f"machine: {platform.processor() or platform.machine()} "
          f"python {platform.python_version()} ({sys.platform})")
    for n in (10, 30, 60):
        mean, mx = bench(n)
        flag = "  <-- over 5 ms" if n == 30 and mean > 5.0 else ""
        print(f"{n:>3} detections: mean {mean:6.2f} ms/call  "
              f"max {mx:6.2f} ms{flag}")


if __name__ == "__main__":
    main()
