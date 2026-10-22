"""Sorting pipeline shared by the OpenCV UI and the web engine.

Line/Track/Tracker moved from operate.py; Sorter wraps the per-frame work of
the old operate._loop; calibrate_click moved from calibrate.py. Behaviour
is identical - only the location changed."""
import math
import time
from collections import Counter

import cv2

import config as C
import ui
import vision


# ------------------------------------------------------------------ geometry
class Line:
    def __init__(self, p1, p2):
        self.p1, self.p2 = p1, p2
        self.dx, self.dy = p2[0] - p1[0], p2[1] - p1[1]
        self.len2 = float(self.dx ** 2 + self.dy ** 2) or 1.0

    def side(self, p):
        return self.dx * (p[1] - self.p1[1]) - self.dy * (p[0] - self.p1[0])

    def along(self, p):
        return ((p[0] - self.p1[0]) * self.dx + (p[1] - self.p1[1]) * self.dy) / self.len2


# ------------------------------------------------------------------ tracking
class Track:
    _next = 1

    def __init__(self, pos, idx):
        self.id = Track._next
        Track._next += 1
        self.pos, self.vel, self.last_seen = pos, (0.0, 0.0), idx
        self.votes = Counter()
        self.prev_side = None
        self.done = False

    def predict(self):
        return (self.pos[0] + self.vel[0], self.pos[1] + self.vel[1])

    def update(self, pos, idx):
        dt = max(1, idx - self.last_seen)
        nv = ((pos[0] - self.pos[0]) / dt, (pos[1] - self.pos[1]) / dt)
        self.vel = (0.5 * self.vel[0] + 0.5 * nv[0], 0.5 * self.vel[1] + 0.5 * nv[1])
        self.pos, self.last_seen = pos, idx

    def decide(self):
        total = sum(self.votes.values())
        if total == 0:
            return None
        if self.votes[4] >= 1 and self.votes[4] / total >= C.DEFECT_VOTE_RATIO:
            return 4
        good = {g: n for g, n in self.votes.items() if g != 4}
        return max(good, key=good.get) if good else 4


class Tracker:
    def __init__(self):
        self.tracks = []

    def update(self, beans, idx):
        cand = []
        for ti, t in enumerate(self.tracks):
            p = t.predict()
            for bi, b in enumerate(beans):
                d = math.hypot(b.centroid[0] - p[0], b.centroid[1] - p[1])
                if d <= C.TRACK_MAX_DIST:
                    cand.append((d, ti, bi))
        cand.sort()
        used_t, used_b, matched = set(), set(), []
        for d, ti, bi in cand:
            if ti in used_t or bi in used_b:
                continue
            used_t.add(ti)
            used_b.add(bi)
            self.tracks[ti].update(beans[bi].centroid, idx)
            matched.append((self.tracks[ti], beans[bi]))
        for bi, b in enumerate(beans):
            if bi not in used_b:
                t = Track(b.centroid, idx)
                self.tracks.append(t)
                matched.append((t, b))
        self.tracks = [t for t in self.tracks if idx - t.last_seen <= C.TRACK_MAX_MISSED]
        return matched


# ------------------------------------------------------------------- sorting
class Sorter:
    """One frame in -> annotated frame out. Counts beans crossing `line`;
    grade-4 crossings pulse the valve while armed."""

    def __init__(self, db, bg, classifier, line, valve, session_id=None):
        self.db, self.bg, self.clf, self.line, self.valve = db, bg, classifier, line, valve
        self.session_id = session_id
        self.tracker = Tracker()
        self.stats = Counter()
        self.blown = 0
        self.flash_until = 0.0
        self.idx = 0

    def set_line(self, line):
        """New sorting line, keeps counters (same as the old 'l' key)."""
        self.line, self.tracker = line, Tracker()

    def process(self, frame, armed=True):
        self.idx += 1
        matched = self.tracker.update(vision.find_beans(frame, self.bg), self.idx)
        now = time.perf_counter()

        for t, b in matched:
            if sum(t.votes.values()) < C.MAX_VOTES:
                pal = vision.extract_palette(vision.bean_pixels(frame, b.contour))
                if pal:
                    t.votes[self.clf.classify(pal)[0]] += 1
            grade = t.decide()

            s = self.line.side(t.pos)
            crossed = (t.prev_side is not None and (t.prev_side >= 0) != (s >= 0)
                       and -C.LINE_MARGIN <= self.line.along(t.pos) <= 1 + C.LINE_MARGIN)
            if crossed and not t.done and grade is not None:
                t.done = True
                self.stats[f"g{grade}"] += 1
                fire = (grade == 4 and armed)
                if fire:
                    self.valve.pulse()
                    self.blown += 1
                    self.flash_until = now + 0.25
                self.db.log_event(grade, fire, self.session_id)
            t.prev_side = s

            col = vision.GRADE_BGR.get(grade, (160, 160, 160))
            cv2.drawContours(frame, [b.contour], -1, col, 2)
            ui.put_text(frame, "?" if grade is None else f"G{grade}",
                        (int(b.centroid[0]) - 12, int(b.centroid[1]) + 5), col, 0.6, 2)

        line_col = (0, 0, 255) if now < self.flash_until else (0, 255, 255)
        cv2.line(frame, self.line.p1, self.line.p2, line_col, 3)
        return frame


# -------------------------------------------------------------- calibration
def calibrate_click(db, frame, bg, pt, grade):
    """Save the palette of the bean under pixel point `pt` as `grade`.

    Returns (contour, centroid, palette) on success, or an error string."""
    bean = vision.bean_at(frame, bg, pt)
    if bean is None:
        return "No bean detected there - click the bean centre"
    pal = vision.extract_palette(vision.bean_pixels(frame, bean.contour))
    if not pal:
        return "Bean too small / not enough pixels"
    db.add_sample(grade, int(bean.centroid[0]), int(bean.centroid[1]), bean.area, pal)
    return bean.contour, bean.centroid, pal
