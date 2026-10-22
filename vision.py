"""Bean detection, colour-palette extraction and grade classification."""
from collections import namedtuple

import cv2
import numpy as np

import config as C

cv2.setRNGSeed(0)

Bean = namedtuple("Bean", "contour centroid area")
PaletteColor = namedtuple("PaletteColor", "lab weight hex")

# BGR colours used for drawing
GRADE_BGR = {1: (0, 200, 0), 2: (200, 200, 0), 3: (0, 165, 255), 4: (0, 0, 255)}

_K3 = np.ones((3, 3), np.uint8)
_K5 = np.ones((5, 5), np.uint8)
_RNG = np.random.default_rng(0)


# ---------------------------------------------------------------- detection
def segment(frame, bg):
    diff = cv2.absdiff(frame, bg).max(axis=2)
    diff = cv2.GaussianBlur(diff, (5, 5), 0)
    _, mask = cv2.threshold(diff, C.DIFF_THRESHOLD, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _K3)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _K5)
    return mask


def find_beans(frame, bg):
    mask = segment(frame, bg)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    beans = []
    for c in cnts:
        a = cv2.contourArea(c)
        if a < C.MIN_BEAN_AREA or a > C.MAX_BEAN_AREA:
            continue
        m = cv2.moments(c)
        if m["m00"] == 0:
            continue
        beans.append(Bean(c, (m["m10"] / m["m00"], m["m01"] / m["m00"]), a))
    return beans


def bean_at(frame, bg, pt):
    """The detected bean under (or within 6 px of) the clicked point."""
    best, best_d = None, -1e9
    for b in find_beans(frame, bg):
        d = cv2.pointPolygonTest(b.contour, (float(pt[0]), float(pt[1])), True)
        if d >= -6 and d > best_d:
            best, best_d = b, d
    return best


def bean_pixels(frame, contour):
    """BGR pixels inside the bean (edges eroded away, specular glare removed)."""
    x, y, w, h = cv2.boundingRect(contour)
    roi = frame[y:y + h, x:x + w]
    mask = np.zeros((h, w), np.uint8)
    shifted = (contour - np.array([x, y], dtype=np.int32)).astype(np.int32)
    cv2.drawContours(mask, [shifted], -1, 255, -1)
    eroded = cv2.erode(mask, _K5)
    if cv2.countNonZero(eroded) < 30:
        eroded = mask
    pix = roi[eroded > 0]
    keep = pix.min(axis=1) < 235
    if keep.sum() >= 30:
        pix = pix[keep]
    return pix


# ------------------------------------------------------------------- colour
def bgr_to_lab(pix_bgr):
    arr = (pix_bgr.astype(np.float32) / 255.0).reshape(-1, 1, 3)
    return np.ascontiguousarray(cv2.cvtColor(arr, cv2.COLOR_BGR2Lab).reshape(-1, 3))


def lab_to_hex(lab):
    arr = np.asarray(lab, np.float32).reshape(1, 1, 3)
    bgr = cv2.cvtColor(arr, cv2.COLOR_Lab2BGR)[0, 0]
    b, g, r = np.clip(np.round(bgr * 255), 0, 255).astype(int)
    return "#%02X%02X%02X" % (r, g, b)


def extract_palette(pix_bgr, k=None):
    """Main colours of a bean via k-means in Lab space -> [PaletteColor], biggest first."""
    k = k or C.PALETTE_K
    if len(pix_bgr) < 20:
        return []
    if len(pix_bgr) > C.MAX_PIXELS:
        pix_bgr = pix_bgr[_RNG.choice(len(pix_bgr), C.MAX_PIXELS, replace=False)]
    lab = bgr_to_lab(pix_bgr)
    k = min(k, len(lab))
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.5)
    _, labels, centers = cv2.kmeans(lab, k, None, crit, 2, cv2.KMEANS_PP_CENTERS)
    w = np.bincount(labels.ravel(), minlength=k).astype(np.float32)
    w /= w.sum()
    out = [PaletteColor(centers[i].copy(), float(w[i]), lab_to_hex(centers[i]))
           for i in np.argsort(-w) if w[i] >= C.MIN_COLOR_WEIGHT]
    s = sum(p.weight for p in out)
    return [PaletteColor(p.lab, p.weight / s, p.hex) for p in out]


# ----------------------------------------------------------- classification
class Classifier:
    """Compares a bean's palette with each grade's saved colour collection.

    score(grade) = sum over bean colours of  weight * (distance to the nearest
    saved colour of that grade)   -> lower is better, units are Lab delta-E.
    """

    def __init__(self, model):
        self.model = {g: v for g, v in model.items() if len(v)}

    def ready(self):
        return 4 in self.model and any(g in self.model for g in (1, 2, 3))

    def scores(self, palette):
        colors = np.array([p.lab for p in palette], np.float32)
        w = np.array([p.weight for p in palette], np.float32)
        out = {}
        for g, ref in self.model.items():
            d = np.linalg.norm(colors[:, None, :] - ref[None, :, :], axis=2)
            out[g] = float((d.min(axis=1) * w).sum())
        return out

    def classify(self, palette):
        raw = self.scores(palette)
        biased = dict(raw)
        if 4 in biased:
            biased[4] *= C.DEFECT_BIAS
        best = min(biased, key=biased.get)
        if raw[best] > C.UNKNOWN_DE:      # looks like nothing we know -> reject it
            best = 4
        return best, raw
