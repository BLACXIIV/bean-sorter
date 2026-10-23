#!/usr/bin/env python3
"""Per-stage cost profile of the run loop, trajectory vs legacy tracker.

Measures, in ms per frame, on real SimulatedCamera "moving" frames:
  cam read (sim render) / find_beans / classify votes / tracker.update /
  overlay (frame.copy + draw) / jpeg encode - exactly the stages the engine
  spends per frame, with FEATURES['trajectory_tracking'] on and off.

  python scripts/profile_stages.py [frames]
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C                      # noqa: E402
import vision                           # noqa: E402
import sorter as S_mod                  # noqa: E402
import tracking as T_mod                # noqa: E402
from hardware import SimulatedCamera    # noqa: E402
from sorter import Line, Sorter         # noqa: E402

STREAM_W, STREAM_H = 640, 360


class _Valve:
    def pulse(self, delay=None, duration=None):
        pass


class _DB:
    def log_event(self, *a, **kw):
        pass


def build_model(cam):
    """Calibrate belt + grades from the sim's static grid (real pipeline)."""
    cam.set_scene("empty")
    frame = cam.read()
    # one mean Lab per tap - like the real calibration (few stored colours;
    # bean_mask/filter_pixels cost scales with the number of colours)
    labs = np.stack([vision.bgr_to_lab(
        frame[y - 3:y + 3, x - 3:x + 3].reshape(-1, 3)).mean(axis=0)
        for x, y in ((640, 60), (640, 500), (200, 650), (1080, 650))])
    belt = vision.BeltModel(labs)
    cam.set_scene("static")
    frame = cam.read()
    model = {}
    for g, gx in enumerate(cam.GRID_X, start=1):
        cols = []
        for gy in cam.GRID_Y[:2]:
            b = vision.bean_at(frame, None, (gx, gy), belt)
            pal = vision.extract_palette(
                vision.bean_pixels(frame, b.contour, belt))
            cols += [p for p in pal]
        if cols:
            lab = np.array([p.lab for p in cols], np.float32)
            model[g] = lab
    cam.set_scene("moving")
    for _ in range(30):                  # let beans fall mid-frame
        cam.read()
    return belt, vision.Classifier(model)


def stage_ms(fn, n=60):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return float(np.mean(ts))


def main(frames=90):
    cam = SimulatedCamera()
    belt, clf = build_model(cam)
    line = Line((0, 360), (1280, 360))
    out = {}
    for traj in (True, False):
        C.FEATURES["trajectory_tracking"] = traj
        T_mod.TTrack._next = 1
        S_mod.Track._next = 1
        sorter = Sorter(_DB(), None, clf, line, _Valve(), session_id=0,
                        belt=belt)
        tim = {"find": 0.0, "cls": 0.0, "track": 0.0, "draw": 0.0}
        cam_ms, enc_ms = [], []
        n = 0
        for _ in range(frames):
            t0 = time.perf_counter()
            frame = cam.read()
            cam_ms.append((time.perf_counter() - t0) * 1000)
            view = sorter.process(frame, True, tim=tim,
                                  t=time.perf_counter())
            t0 = time.perf_counter()
            small = cv2.resize(view, (STREAM_W, STREAM_H))
            cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
            enc_ms.append((time.perf_counter() - t0) * 1000)
            n += 1
        key = "trajectory" if traj else "legacy"
        out[key] = {"cam": float(np.mean(cam_ms)),
                    "find": tim["find"] / n, "cls": tim["cls"] / n,
                    "track": tim["track"] / n, "draw": tim["draw"] / n,
                    "jpeg": float(np.mean(enc_ms))}
    hdr = f"{'stage':<24} {'legacy ms':>10} {'trajectory ms':>14}"
    print(hdr)
    print("-" * len(hdr))
    for st in ("cam", "find", "cls", "track", "draw", "jpeg"):
        print(f"{st:<24} {out['legacy'][st]:>10.2f} "
              f"{out['trajectory'][st]:>14.2f}")
    lt = sum(out["legacy"].values())
    tt = sum(out["trajectory"].values())
    print("-" * len(hdr))
    print(f"{'TOTAL (upper bound fps)':<24} {lt:>10.2f} {tt:>14.2f}")
    print(f"{'implied fps':<24} {1000 / lt:>10.1f} {1000 / tt:>14.1f}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 90)
