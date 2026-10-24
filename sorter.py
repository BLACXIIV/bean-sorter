"""Sorting pipeline shared by the OpenCV UI and the web engine.

Line/Track/Tracker moved from operate.py; Sorter wraps the per-frame work of
the old operate._loop; calibrate_click moved from calibrate.py.

Tracker selection: config.FEATURES["trajectory_tracking"] picks
tracking.TrajectoryTracker (Kalman + Hungarian, one identity per bean); when
off, the original frame-indexed tracker runs as LegacyTracker, unchanged.

Detections everywhere are dicts {"centroid", "area", "contour", "palette"};
tests may inject them via Sorter.process(..., detections=...) with synthetic
timestamps via t=."""
import math
import os
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

import config as C
import ui
import vision
from tracking import TrajectoryTracker


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


class LegacyTracker:
    """Original greedy nearest-neighbour tracker (frame-indexed). Unchanged
    except that detections are dicts instead of Bean namedtuples."""

    def __init__(self):
        self.tracks = []

    def update(self, beans, idx):
        cand = []
        for ti, t in enumerate(self.tracks):
            p = t.predict()
            for bi, b in enumerate(beans):
                d = math.hypot(b["centroid"][0] - p[0], b["centroid"][1] - p[1])
                if d <= C.TRACK_MAX_DIST:
                    cand.append((d, ti, bi))
        cand.sort()
        used_t, used_b, matched = set(), set(), []
        for d, ti, bi in cand:
            if ti in used_t or bi in used_b:
                continue
            used_t.add(ti)
            used_b.add(bi)
            self.tracks[ti].update(beans[bi]["centroid"], idx)
            matched.append((self.tracks[ti], beans[bi]))
        for bi, b in enumerate(beans):
            if bi not in used_b:
                t = Track(b["centroid"], idx)
                self.tracks.append(t)
                matched.append((t, b))
        self.tracks = [t for t in self.tracks if idx - t.last_seen <= C.TRACK_MAX_MISSED]
        return matched


# ------------------------------------------------------------------- sorting
class Sorter:
    """One frame in -> annotated frame copy out. Counts beans crossing `line`;
    grade-4 crossings pulse the valve while armed.

    process(frame, armed, tim, t, detections): t = frame time in seconds
    (perf_counter taken right after camera.read; default = now), detections =
    injected detections for tests (None = detect on the frame)."""

    def __init__(self, db, bg, classifier, line, valve, session_id=None, belt=None):
        self.db, self.bg, self.clf, self.line, self.valve = db, bg, classifier, line, valve
        self.belt = belt
        self.session_id = session_id
        self.use_traj = bool(C.FEATURES.get("trajectory_tracking"))
        self.tracker = TrajectoryTracker(line) if self.use_traj else LegacyTracker()
        self.show_tracks = False
        self.stats = Counter()
        self.blown = 0
        self.n_uncertain = 0
        self.flash_until = 0.0
        self.idx = 0
        self._rec = None
        rec = os.environ.get("BEAN_RECORD")
        if rec:
            Path(rec).parent.mkdir(parents=True, exist_ok=True)
            self._rec = open(rec, "a", encoding="utf-8")   # JSONL, one line/frame
        # BEAN_DROP=0.1 randomly drops detections before the tracker (testing)
        self._drop_p = float(os.environ.get("BEAN_DROP") or 0.0)
        self._drop_rng = np.random.default_rng(0)

    def set_line(self, line):
        """New sorting line, keeps counters (same as the old 'l' key)."""
        self.line = line
        if self.use_traj:
            self.tracker.set_line(line)
        else:
            self.tracker = LegacyTracker()

    # ------------------------------------------------------------ detections
    def _detect(self, frame):
        dets = []
        for b in vision.find_beans(frame, self.bg, self.belt):
            if self._drop_p and self._drop_rng.random() < self._drop_p:
                continue
            dets.append({"centroid": b.centroid, "area": b.area,
                         "contour": b.contour, "palette": None})
            # palettes are extracted ON DEMAND (vote loop, re-id) - not here
        return dets

    def _palette(self, frame, det):
        if det.get("palette") is not None:
            return det["palette"]
        if det.get("contour") is None:
            return None
        pal = vision.extract_palette(
            vision.bean_pixels(frame, det["contour"], self.belt))
        det["palette"] = pal                 # cache: tracker reuses it
        return pal

    # ---------------------------------------------------------------- process
    def process(self, frame, armed=True, tim=None, t=None, detections=None,
                annotate=True):
        self.idx += 1
        t = time.perf_counter() if t is None else t
        t0 = time.perf_counter()
        if detections is None:
            detections = self._detect(frame)
        if tim is not None:
            tim["find"] += (time.perf_counter() - t0) * 1000

        matched = []
        if self.use_traj:
            tu = time.perf_counter()
            res = self.tracker.update(
                detections, t, get_pal=lambda d: self._palette(frame, d))
            if tim is not None:
                tim["track"] += (time.perf_counter() - tu) * 1000
            for tr, det in res["votes"]:
                det["_track"] = tr.id
                det["_tstate"] = tr.state
                det["_counted"] = tr.counted
                # a suspect detection (NMS-merged blob, area far off the
                # track's own median) never casts a vote
                if not det.get("_suspect") \
                        and sum(tr.votes.values()) < C.MAX_VOTES:
                    tc = time.perf_counter()
                    pal = self._palette(frame, det)
                    if pal:
                        g = self.clf.classify(pal)[0]
                        tr.votes[g] += 1
                        det["_vote"] = g
                    if tim is not None:
                        tim["cls"] += (time.perf_counter() - tc) * 1000
                matched.append((tr, det))
            for cr in res["crossings"]:
                self._count(cr.track, cr.t_c, cr.point, cr.along,
                            cr.crossing_type, armed, t)
        else:
            matched = self.tracker.update(detections, self.idx)
            for tr, det in matched:
                det["_track"] = tr.id
                det["_tstate"] = "live"
                if sum(tr.votes.values()) < C.MAX_VOTES:
                    tc = time.perf_counter()
                    pal = self._palette(frame, det)
                    if pal:
                        g = self.clf.classify(pal)[0]
                        tr.votes[g] += 1
                        det["_vote"] = g
                    if tim is not None:
                        tim["cls"] += (time.perf_counter() - tc) * 1000
                grade = tr.decide()
                s = self.line.side(tr.pos)
                crossed = (tr.prev_side is not None
                           and (tr.prev_side >= 0) != (s >= 0)
                           and -C.LINE_MARGIN <= self.line.along(tr.pos) <= 1 + C.LINE_MARGIN)
                if crossed and not tr.done and grade is not None:
                    tr.done = True
                    along = min(1.0, max(0.0, self.line.along(tr.pos)))
                    self._register(tr, grade, t, tr.pos, along, "observed", armed)
                tr.prev_side = s

        if self._rec is not None:
            self._record(t, detections)

        # ---- overlay: drawn ONLY on a copy, after all analysis of this frame.
        # annotate=False (nobody watching the stream) skips the copy entirely.
        if not annotate:
            return frame
        view = frame.copy()
        td = time.perf_counter()
        self._now = t
        self._draw(view, matched)
        if tim is not None:
            tim["draw"] += (time.perf_counter() - td) * 1000
        return view

    # ------------------------------------------------------- counting / firing
    def _count(self, tr, t_c, point, along, crossing_type, armed, now):
        if self.use_traj:
            grade, uncertain = tr.decide_safe()
            if uncertain:
                self.n_uncertain += 1
                crossing_type = "uncertain"
                if C.UNCERTAIN_POLICY == "blow":
                    grade = 4           # fail-safe: blow the unknown bean
                else:                   # "pass": count it, never fire
                    armed = False
                    if grade is None:
                        grade = 1
        else:
            grade = tr.decide()
        if grade is None:
            return
        if self.tracker.dedupe_check(tr, point, t_c, grade):
            return                                          # duplicate: no count
        self.tracker.register_crossing(point, t_c, grade, crossing_type, tr.pal)
        self._register(tr, grade, t_c, point, along, crossing_type, armed,
                       now=now)

    def _register(self, tr, grade, t_c, point, along, crossing_type, armed,
                  now=None):
        now = time.perf_counter() if now is None else now
        self.stats[f"g{grade}"] += 1
        fire = (grade == 4 and armed)
        if fire:
            delay = max(0.0, t_c + float(C.VALVE_DELAY_S) - now)
            self.valve.pulse(delay=delay,
                             uncertain=(crossing_type == "uncertain"),
                             t_c=t_c, x_on_line=point[0])
            self.blown += 1
            self.flash_until = now + 0.25
        self.db.log_event(grade, fire, self.session_id,
                          track_id=getattr(tr, "id", None),
                          crossing_type=crossing_type,
                          x_on_line=along, speed_px_s=getattr(tr, "speed_px_s", 0.0))

    def _record(self, t, detections):
        import json
        line = {"t": round(t, 4), "detections": [
            {"x": round(float(d["centroid"][0]), 1),
             "y": round(float(d["centroid"][1]), 1),
             "area": round(float(d["area"]), 1),
             "palette": [[round(float(v), 3) for v in p.lab]
                         + [round(float(p.weight), 4)] for p in (d.get("palette") or [])],
             "vote": d.get("_vote"), "track": d.get("_track"),
             "tstate": d.get("_tstate"), "counted": d.get("_counted"),
             "suspect": bool(d.get("_suspect"))} for d in detections]}
        self._rec.write(json.dumps(line) + "\n")
        self._rec.flush()

    # ----------------------------------------------------------------- overlay
    def _draw(self, view, matched):
        for tr, det in matched:
            grade = tr.decide()
            col = vision.GRADE_BGR.get(grade, (160, 160, 160))
            coasting = getattr(tr, "state", "") == "coasting"
            if coasting:
                col = tuple(int(x * 0.45) for x in col)
            cnt = det.get("contour")
            if cnt is not None:
                cv2.drawContours(view, [cnt], -1, col, 2)
            else:
                r = int(math.sqrt(max(1.0, det["area"]) / math.pi))
                cv2.circle(view, (int(det["centroid"][0]), int(det["centroid"][1])),
                           r, col, 1)
            label = "?" if grade is None else f"G{grade}"
            if self.show_tracks:
                label += f" #{tr.id}"
            ui.put_text(view, label,
                        (int(det["centroid"][0]) - 12, int(det["centroid"][1]) + 5),
                        col, 0.6, 2)
            if self.show_tracks and getattr(tr, "trail", None):
                pts = list(tr.trail)
                for i in range(1, len(pts)):
                    f = i / len(pts)
                    cc = tuple(int(x * (0.25 + 0.6 * f)) for x in col)
                    a = (int(pts[i - 1][0]), int(pts[i - 1][1]))
                    b = (int(pts[i][0]), int(pts[i][1]))
                    if coasting and i % 2 == 0:
                        continue                    # dashed trail while coasting
                    cv2.line(view, a, b, cc, 1, cv2.LINE_AA)

        line_col = (0, 0, 255) if getattr(self, "_now", 0) < self.flash_until \
            else (0, 255, 255)
        cv2.line(view, self.line.p1, self.line.p2, line_col, 3)

    # --------------------------------------------------------------- counters
    def trk_counters(self):
        c = self.tracker.counters() if self.use_traj else {
            "active": len(self.tracker.tracks), "coasting": 0,
            "unique_crossed": sum(self.stats.values()), "suppressed": 0,
            "estimated": 0, "vote_resets": 0, "lost_before_line": 0}
        c["uncertain"] = self.n_uncertain
        c["enabled"] = self.use_traj
        return c


# -------------------------------------------------------------- calibration
def calibrate_click(db, frame, bg, pt, grade, belt=None):
    """Save the palette of the bean under pixel point `pt` as `grade`.

    Returns (contour, centroid, palette) on success, or an error string."""
    bean = vision.bean_at(frame, bg, pt, belt)
    if bean is None:
        return "No bean detected there - click the bean centre"
    pal = vision.extract_palette(vision.bean_pixels(frame, bean.contour, belt))
    if not pal:
        return "Bean too small / not enough pixels"
    db.add_sample(grade, int(bean.centroid[0]), int(bean.centroid[1]), bean.area, pal)
    return bean.contour, bean.centroid, pal
