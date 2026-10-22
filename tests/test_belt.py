"""Conveyor-colour model checks (no pytest needed):  python3 tests/test_belt.py"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C
import vision
from hardware import SimulatedCamera

PASS, FAIL = "PASS", "FAIL"
results = []


def report(name, ok, detail=""):
    results.append(ok)
    print(f"{PASS if ok else FAIL}  {name}  {detail}")


def belt_frame(cam):
    cam.set_scene("empty")
    return cam.read()


def add_beans(cam, frame, spots):
    for i, (x, y) in enumerate(spots):
        cam._draw_bean(frame, x, y, (i % 4) + 1, cam.BEAN_R)
    return frame


def sample_lab(frame, x, y, p=10):
    patch = frame[y - p:y + p + 1, x - p:x + p + 1]
    return np.median(vision.bgr_to_lab(patch.reshape(-1, 3)), axis=0)


cam = SimulatedCamera()
SPOTS = [(160, 60), (480, 60), (800, 60), (1120, 60),
         (160, 660), (480, 660), (800, 660), (1120, 660)]   # 8 beans, off the seams

# (a) belt model from sampled belt colours -> exactly 8 beans
base = belt_frame(cam)
belt_lab = np.array([sample_lab(base, 640, 60),     # plain belt
                     sample_lab(base, 640, 180),    # seam 1
                     sample_lab(base, 640, 360),    # seam 2
                     sample_lab(base, 640, 540)], np.float32)  # seam 3
belt = vision.BeltModel(belt_lab)
scene = add_beans(cam, base.copy(), SPOTS)
n = len(vision.find_beans(scene, None, belt))
report("a) belt mode finds exactly 8 beans", n == 8, f"found {n}")

# (b) belt texture shifted 37 px vs saved background
bg = belt_frame(cam)
shifted = np.roll(belt_frame(cam), 37, axis=0)
scene_b = add_beans(cam, shifted.copy(), SPOTS)
n_belt = len(vision.find_beans(scene_b, None, belt))
n_bg = len(vision.find_beans(scene_b, bg))
print(f"      shifted belt: belt_mode={n_belt}  background_mode={n_bg}")
report("b) belt mode still finds 8 on shifted texture", n_belt == 8,
       f"background mode found {n_bg} (expected worse)")

# (c) bean_pixels returns zero pixels within tolerance of the belt
bean = vision.bean_at(scene, None, (160, 60), belt)
pix = vision.bean_pixels(scene, bean.contour, belt)
tol, lw = belt._params()
lab = vision.bgr_to_lab(pix)
diff = lab[:, None, :] - belt_lab[None, :, :]
diff[..., 0] *= lw
inside = (np.sqrt((diff * diff).sum(axis=2)).min(axis=1) < tol).sum()
report("c) bean_pixels drops all belt-coloured pixels", inside == 0,
       f"{inside} belt-like pixels left of {len(pix)}")

# (d) belt=None path identical to the old call
old = vision.find_beans(scene, bg)
new = vision.find_beans(scene, bg, None)
same = len(old) == len(new) and all(
    np.allclose(a.centroid, b.centroid) for a, b in zip(old, new))
report("d) belt=None behaves exactly as before", same,
       f"{len(old)} vs {len(new)} beans")

# (e) belt_warnings: near colour warns, far colour does not
grade_model = {}
for g in (1, 2, 3, 4):
    lab = vision.bgr_to_lab(np.array([SimulatedCamera.GRADE_BGR[g]], np.uint8))
    grade_model[g] = lab
near = grade_model[4][0].copy(); near[1] += 3.0     # 3 dE from a grade-4 colour
w = vision.belt_warnings(near.reshape(1, 3), grade_model, C.BELT_TOLERANCE, C.BELT_L_WEIGHT)
ok_near = any("Grade 4" in s for s in w)
far = np.array([[95.0, 60.0, -100.0]], np.float32)
w2 = vision.belt_warnings(far, grade_model, C.BELT_TOLERANCE, C.BELT_L_WEIGHT)
report("e) belt_warnings near->Grade 4, far->[]", ok_near and w2 == [],
       f"near={w} far={w2}")

# (f) timing of bean_mask on 1280x720 (dev machine)
belt.bean_mask(scene)  # warm-up
t0 = time.perf_counter()
N = 20
for _ in range(N):
    belt.bean_mask(scene)
ms = (time.perf_counter() - t0) / N * 1000
print(f"      bean_mask: {ms:.2f} ms/frame on this dev machine (Pi: NOT VERIFIED)")
report("f) bean_mask timing measured", True, f"{ms:.2f} ms/frame")

print()
print("=" * 50)
print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
