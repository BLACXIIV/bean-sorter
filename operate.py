"""Operation: draw the sorting line, then grade beans live and blow grade 4 at the line."""
import math
import time
from collections import Counter

import cv2

import config as C
import ui
import vision
from hardware import make_camera, make_valve

WIN = "Bean Sorter"


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


# ----------------------------------------------------------------- line setup
def setup_line(cam, db):
    saved = db.get_json("line")
    state = {"pts": [tuple(p) for p in saved] if saved else []}

    def on_mouse(ev, x, y, flags, param):
        if ev == cv2.EVENT_LBUTTONDOWN:
            if len(state["pts"]) >= 2:
                state["pts"] = []
            state["pts"].append(ui.to_frame_coords(x, y, C.DISPLAY_SCALE))

    cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WIN, on_mouse)
    while True:
        frame = cam.read()
        pts = state["pts"]
        for p in pts:
            cv2.circle(frame, p, 6, (0, 255, 255), -1)
        if len(pts) == 2:
            cv2.line(frame, pts[0], pts[1], (0, 255, 255), 2)
        ui.draw_panel(frame, ["DRAW THE SORTING LINE",
                              "Click 2 points across the bean path (beans must be visible BEFORE the line)",
                              "ENTER = start sorting    R = redo    Q = quit"])
        ui.show(WIN, frame, C.DISPLAY_SCALE)
        k = cv2.waitKey(1) & 0xFF
        if k in (13, 10) and len(pts) == 2:
            db.set_json("line", pts)
            return Line(pts[0], pts[1])
        if k == ord("r"):
            state["pts"] = []
        if k in (ord("q"), 27):
            return None


# ------------------------------------------------------------------ main loop
def operate(db, dry_run=False):
    bg = db.load_background()
    if bg is None:
        print("No background saved. Run:  python3 main.py calibrate")
        return 1
    clf = vision.Classifier(db.load_model())
    if not clf.ready():
        print("Calibration incomplete: need grade 4 plus at least one of grades 1-3.",
              db.counts())
        return 1

    cam = make_camera(saved_settings=db.get_json("camera"))
    valve = make_valve(dry_run)
    try:
        line = setup_line(cam, db)
        if line is None:
            return 0
        _loop(cam, db, valve, bg, clf, line)
    finally:
        valve.close()
        cam.close()
        cv2.destroyAllWindows()
    return 0


def _loop(cam, db, valve, bg, clf, line):
    tracker, stats, blown = Tracker(), Counter(), 0
    idx, fps, t_prev = 0, 0.0, time.perf_counter()
    armed, flash_until = True, 0.0

    while True:
        frame = cam.read()
        idx += 1
        matched = tracker.update(vision.find_beans(frame, bg), idx)
        now = time.perf_counter()

        for t, b in matched:
            # classify this bean for its first few frames, then trust the votes
            if sum(t.votes.values()) < C.MAX_VOTES:
                pal = vision.extract_palette(vision.bean_pixels(frame, b.contour))
                if pal:
                    t.votes[clf.classify(pal)[0]] += 1
            grade = t.decide()

            s = line.side(t.pos)
            crossed = (t.prev_side is not None and (t.prev_side >= 0) != (s >= 0)
                       and -C.LINE_MARGIN <= line.along(t.pos) <= 1 + C.LINE_MARGIN)
            if crossed and not t.done and grade is not None:
                t.done = True
                stats[f"g{grade}"] += 1
                fire = (grade == 4 and armed)
                if fire:
                    valve.pulse()
                    blown += 1
                    flash_until = now + 0.25
                db.log_event(grade, fire)
            t.prev_side = s

            col = vision.GRADE_BGR.get(grade, (160, 160, 160))
            cv2.drawContours(frame, [b.contour], -1, col, 2)
            ui.put_text(frame, "?" if grade is None else f"G{grade}",
                        (int(b.centroid[0]) - 12, int(b.centroid[1]) + 5), col, 0.6, 2)

        line_col = (0, 0, 255) if now < flash_until else (0, 255, 255)
        cv2.line(frame, line.p1, line.p2, line_col, 3)

        fps = 0.9 * fps + 0.1 / max(1e-6, now - t_prev)
        t_prev = now
        ui.draw_panel(frame, [
            ("ARMED - valve active" if armed else "DISARMED - valve OFF (counting only)")
            + f"   {fps:4.1f} fps",
            f"G1:{stats['g1']}  G2:{stats['g2']}  G3:{stats['g3']}  G4:{stats['g4']}   blown:{blown}",
            "A = arm/disarm   V = test valve   L = redraw line   Q = quit"],
            color=(0, 255, 0) if armed else (0, 200, 255))
        ui.show(WIN, frame, C.DISPLAY_SCALE)

        k = cv2.waitKey(1) & 0xFF
        if k in (ord("q"), 27):
            break
        elif k == ord("a"):
            armed = not armed
        elif k == ord("v"):
            valve.pulse(delay=0)
        elif k == ord("l"):
            new = setup_line(cam, db)
            if new is None:
                break
            line, tracker = new, Tracker()
