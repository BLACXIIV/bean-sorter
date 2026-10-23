"""Golden colour tests: fixed BGR pixel arrays -> expected float Lab / hex.

Catches: BGR/RGB channel swaps, 8-bit Lab (L 0..255) vs float Lab (L 0..100),
and non-deterministic palette extraction (shared advancing RNG).

  python tests/test_palette_golden.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import vision                            # noqa: E402

results = []


def report(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


# -------------------------------------------------- independent reference
def ref_bgr_to_lab(b, g, r):
    """sRGB(B=0..255,G,R) -> CIELAB D65, computed by hand (the golden oracle)."""
    def lin(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    rl, gl, bl = lin(r), lin(g), lin(b)
    x = rl * 0.4124 + gl * 0.3576 + bl * 0.1805
    y = rl * 0.2126 + gl * 0.7152 + bl * 0.0722
    z = rl * 0.0193 + gl * 0.1192 + bl * 0.9505
    x, z = x / 0.95047, z / 1.08883              # D65 white point

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return np.array([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)])


def dE(a, b):
    return float(np.linalg.norm(np.asarray(a, float) - np.asarray(b, float)))


# ============================================================ 1. golden Lab
GOLD = [  # (B, G, R) test colours - asymmetric so a channel swap is detectable
    (150, 110, 60),      # sim conveyor blue-brown
    (40, 90, 150),       # grade-1 brown
    (25, 30, 40),        # grade-4 near-black
    (200, 40, 30),       # saturated blue
    (30, 200, 60),       # saturated green
    (60, 60, 220),       # saturated red
    (230, 230, 230),     # near-white
]
ok = True
worst = 0.0
for bgr in GOLD:
    got = vision.bgr_to_lab(np.array([bgr], np.uint8))[0]
    want = ref_bgr_to_lab(*bgr)
    worst = max(worst, dE(got, want))
    ok &= dE(got, want) <= 1.5
report("bgr_to_lab matches hand-computed sRGB->Lab", ok,
       f"worst dE {worst:.3f} over {len(GOLD)} colours")

# ============================================== 2. hex golden (round trip)
ok = True
worst = 0.0
for bgr in GOLD:
    lab = vision.bgr_to_lab(np.array([bgr], np.uint8))[0]
    hx = vision.lab_to_hex(lab)
    rgb = tuple(int(hx[i:i + 2], 16) for i in (1, 3, 5))   # #RRGGBB
    back = ref_bgr_to_lab(rgb[2], rgb[1], rgb[0])          # hex is R,G,B
    worst = max(worst, dE(lab, back))
    ok &= dE(lab, back) <= 1.5
report("lab_to_hex round-trips within dE 1.5", ok, f"worst dE {worst:.3f}")

# ================================= 3. channel order (BGR, not RGB)
bgr = (200, 40, 30)                       # saturated blue in BGR order
lab_bgr = vision.bgr_to_lab(np.array([bgr], np.uint8))[0]
lab_swap = vision.bgr_to_lab(np.array([bgr[::-1]], np.uint8))[0]
d_swap = dE(lab_bgr, lab_swap)
want = ref_bgr_to_lab(*bgr)
report("swapped channels give a DIFFERENT Lab (BGR order proven)",
       d_swap > 1.5 and dE(lab_bgr, want) <= 1.5,
       f"swap dE {d_swap:.1f}; correct-order dE {dE(lab_bgr, want):.3f}")

# ============================================ 4. float Lab (L <= 100)
L_white = float(vision.bgr_to_lab(np.array([(255, 255, 255)], np.uint8))[0][0])
report("white L <= 100 (float Lab, not 8-bit)", L_white <= 100.0,
       f"L={L_white:.2f}")

# ================================== 5. extract_palette deterministic
# > MAX_PIXELS forces the subsample path where a shared RNG would drift.
rng = np.random.default_rng(3)
colA = np.array([40, 90, 150], np.uint8)      # dominant 60%
colB = np.array([50, 70, 100], np.uint8)      # 30%
colC = np.array([25, 30, 40], np.uint8)       # 10% (edge noise)
pix = np.concatenate([np.tile(colA, (600, 1)),
                      np.tile(colB, (300, 1)),
                      np.tile(colC, (100, 1))])
pix = pix[rng.permutation(len(pix))]          # shuffled order

p1 = vision.extract_palette(pix.copy())
p2 = vision.extract_palette(pix.copy())
same_lab = (len(p1) == len(p2) and all(
    np.array_equal(a.lab, b.lab) for a, b in zip(p1, p2)))
same_w = all(a.weight == b.weight for a, b in zip(p1, p2))
report("extract_palette identical on identical input (deterministic)",
       same_lab and same_w,
       "" if (same_lab and same_w) else
       f"pal1={[ (p.hex, round(p.weight,3)) for p in p1]} "
       f"pal2={[(p.hex, round(p.weight,3)) for p in p2]}")

# dominant colour's hex is within dE 3 of the reference Lab of colA
if p1:
    want = ref_bgr_to_lab(*colA)
    hx = p1[0].hex
    rgb = tuple(int(hx[i:i + 2], 16) for i in (1, 3, 5))
    got = ref_bgr_to_lab(rgb[2], rgb[1], rgb[0])
    report("dominant palette hex within dE 3 of true colour",
           dE(got, want) <= 3.0, f"{hx} dE {dE(got, want):.2f}")

print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
