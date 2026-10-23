"""Synthetic end-to-end colour pipeline: belt segmentation -> beans -> palette
-> classify, on THREE belt colours (blue, white, gray).

Asserts: bean count, grade of each bean, dominant palette hex within dE 3,
no conveyor-like pixels survive bean_pixels, and belt_warnings fires when a
grade colour is within BELT_TOLERANCE+5 of the belt.

  python tests/test_color_pipeline.py
"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C                      # noqa: E402
import vision                           # noqa: E402

results = []


def report(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def ref_lab(bgr):
    px = np.array([bgr], np.uint8)
    return vision.bgr_to_lab(px)[0]      # Lab oracle = pipeline's own convert


def dE(a, b):
    return float(np.linalg.norm(np.asarray(a, float) - np.asarray(b, float)))


def hex_to_lab(hx):
    rgb = tuple(int(hx[i:i + 2], 16) for i in (1, 3, 5))
    return ref_lab((rgb[2], rgb[1], rgb[0]))


W, H = C.FRAME_SIZE
BELTS = {"blue": (150, 110, 60), "white": (225, 225, 225), "gray": (128, 128, 128)}
# bean colours far from every belt in weighted Lab
BEANS = {1: (40, 90, 150), 2: (45, 80, 125), 3: (50, 70, 100), 4: (25, 30, 40)}
POS = [(240, 180), (640, 180), (1040, 180), (640, 540)]

rng = np.random.default_rng(9)

for belt_name, belt_bgr in BELTS.items():
    frame = np.tile(np.array(belt_bgr, np.uint8), (H, W, 1))
    noise = rng.integers(-5, 6, (H, W, 3), dtype=np.int16)
    frame = (frame.astype(np.int16) + noise).astype(np.uint8)
    for (x, y), (g, bgr) in zip(POS, BEANS.items()):
        cv2.circle(frame, (x, y), 40, bgr, -1, cv2.LINE_AA)

    belt = vision.BeltModel(np.stack([ref_lab(belt_bgr)]))
    beans = vision.find_beans(frame, None, belt)
    report(f"[{belt_name}] bean count", len(beans) == 4,
           f"found {len(beans)} of 4")

    # model: each grade's true colour in Lab
    model = {g: ref_lab(bgr).reshape(1, 3) for g, bgr in BEANS.items()}
    clf = vision.Classifier(model)

    ok_g, ok_hex, worst = True, True, 0.0
    for b in beans:
        g_want = min(BEANS, key=lambda g: dE(
            ref_lab(BEANS[g]), ref_lab(
                tuple(int(v) for v in
                      frame[int(b.centroid[1]), int(b.centroid[0])]))))
        pix = vision.bean_pixels(frame, b.contour, belt)
        pal = vision.extract_palette(pix)
        g_got, _ = clf.classify(pal)
        ok_g &= (g_got == g_want)
        if pal:
            worst = max(worst, dE(hex_to_lab(pal[0].hex), ref_lab(BEANS[g_want])))
            ok_hex &= dE(hex_to_lab(pal[0].hex), ref_lab(BEANS[g_want])) <= 3.0
    report(f"[{belt_name}] every bean's grade correct", ok_g)
    report(f"[{belt_name}] dominant hex within dE 3", ok_hex,
           f"worst {worst:.2f}")

    # no conveyor-like pixels inside the detected beans
    px = vision.bean_pixels(frame, beans[0].contour, belt)
    tol, lw = C.BELT_TOLERANCE, C.BELT_L_WEIGHT
    lab = vision.bgr_to_lab(px)
    d2 = (lw * (lab[:, 0] - belt.colors[0][0])) ** 2 \
        + (lab[:, 1] - belt.colors[0][1]) ** 2 \
        + (lab[:, 2] - belt.colors[0][2]) ** 2
    n_belt = int((d2 < tol * tol).sum())
    report(f"[{belt_name}] bean_pixels keeps no conveyor-like pixels",
           n_belt <= int(0.02 * len(px)),
           f"{n_belt}/{len(px)} belt-like")

# belt_warnings fires when a grade colour sits within tolerance+5 of belt
belt_lab = np.stack([ref_lab(BELTS["blue"])])
near = belt_lab[0] + np.array([0.0, 3.0, 0.0])      # ~3 dE away -> inside tol+5
warn = vision.belt_warnings(belt_lab, {2: near.reshape(1, 3)},
                            C.BELT_TOLERANCE, C.BELT_L_WEIGHT)
report("belt_warnings fires for belt-like grade colour",
       len(warn) == 1 and "Grade 2" in warn[0], warn[0] if warn else "none")
far = np.array([10.0, 120.0, 120.0])                # far away -> no warning
warn2 = vision.belt_warnings(belt_lab, {2: far.reshape(1, 3)},
                             C.BELT_TOLERANCE, C.BELT_L_WEIGHT)
report("belt_warnings quiet for distant grade colour", len(warn2) == 0)

print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
