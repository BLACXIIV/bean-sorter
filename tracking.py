"""Trajectory tracking: every physical bean keeps ONE identity from first
sight to the sorting line, and is counted (and fired on) exactly once.

Detections are dicts:
    {"centroid": (x, y), "area": px^2, "contour": np.array | None,
     "palette": [vision.PaletteColor] | None}

All times are seconds (time.perf_counter or injected test time). All
thresholds live in config.py and are read at call time, so the same code
behaves identically at 30 fps (laptop) and 10 fps (Pi).
"""
import math
import time
from collections import Counter, deque, namedtuple

import numpy as np

import config as C
import vision                       # read-only: palette distance via Classifier

BIG = 1e9                          # forbidden-pair cost in assignment
REID_SPEED_FLOOR = 600.0           # px/s assumed worst-case for the re-id gate

Crossing = namedtuple("Crossing", "track t_c point along crossing_type")


# ---------------------------------------------------------------- Hungarian
def hungarian(cost):
    """Min-cost assignment, pure numpy O(n^2 * m). cost: (n, m) array.
    Returns [(row, col)] pairs; rows/cols with cost >= BIG may still pair -
    callers must filter those out."""
    cost = np.asarray(cost, dtype=np.float64)
    n, m = cost.shape
    if n == 0 or m == 0:
        return []
    if n > m:
        return [(j, i) for i, j in hungarian(cost.T)]
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [float("inf")] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta, j1 = float("inf"), 0
            row = cost[i0 - 1]
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = row[j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    return [(p[j] - 1, j - 1) for j in range(1, m + 1) if p[j] > 0]


# ------------------------------------------------------------------- Kalman
class _Kalman:
    """State [x, y, vx, vy], constant velocity, variable dt, numpy only."""

    def __init__(self, pos):
        self.x = np.array([pos[0], pos[1], 0.0, 0.0], np.float64)
        self.P = np.diag([25.0, 25.0, 1.0e4, 1.0e4])

    @staticmethod
    def _f(dt):
        return np.array([[1, 0, dt, 0], [0, 1, 0, dt],
                         [0, 0, 1, 0], [0, 0, 0, 1]], np.float64)

    @staticmethod
    def _q(dt):
        q = 9000.0                    # white-noise acceleration model
        d2, d3, d4 = dt * dt, dt ** 3, dt ** 4
        return q * np.array([[d4 / 4, 0, d3 / 2, 0],
                             [0, d4 / 4, 0, d3 / 2],
                             [d3 / 2, 0, d2, 0],
                             [0, d3 / 2, 0, d2]], np.float64)

    def predict(self, dt):
        f = self._f(dt)
        self.x = f @ self.x
        self.P = f @ self.P @ f.T + self._q(dt)

    def update(self, pos):
        h = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], np.float64)
        z = np.array([pos[0], pos[1]], np.float64)
        y = z - h @ self.x
        s = h @ self.P @ h.T + np.diag([9.0, 9.0])   # ~3 px measurement noise
        k = self.P @ h.T @ np.linalg.inv(s)
        self.x = self.x + k @ y
        self.P = (np.eye(4) - k @ h) @ self.P

    @property
    def pos(self):
        return (float(self.x[0]), float(self.x[1]))


# ------------------------------------------------------------- geometry
def _seg_hit(p, q, a, b, margin):
    """Segment p->q vs line a->b. Returns (s, u): s = fraction along p->q of the
    intersection, u = fraction along a->b (margin extends past the ends)."""
    d1x, d1y = q[0] - p[0], q[1] - p[1]
    d2x, d2y = b[0] - a[0], b[1] - a[1]
    den = d1x * d2y - d1y * d2x
    if abs(den) < 1e-9:
        return None
    s = ((a[0] - p[0]) * d2y - (a[1] - p[1]) * d2x) / den
    u = ((a[0] - p[0]) * d1y - (a[1] - p[1]) * d1x) / den
    if 0.0 <= s <= 1.0 and -margin <= u <= 1.0 + margin:
        return s, u
    return None


def _det_mean_lab(det):
    """Weighted mean Lab of a detection's palette, or None."""
    pal = det.get("palette")
    if not pal:
        return None
    lab = np.array([p.lab for p in pal], np.float32)
    w = np.array([p.weight for p in pal], np.float32)
    wsum = float(w.sum())
    return None if wsum < 1e-6 else (lab * w[:, None]).sum(axis=0) / wsum


# -------------------------------------------------------------------- track
class TTrack:
    """One physical bean. Votes, counted flag, palette mean and trail live here."""

    _next = 1

    def __init__(self, det, t):
        self.id = TTrack._next
        TTrack._next += 1
        self.kf = _Kalman(det["centroid"])
        self.pos = (float(det["centroid"][0]), float(det["centroid"][1]))
        self.seg_prev = None              # last position used for crossing tests
        self.seg_prev_t = None
        self.last_t = t                   # last time a detection matched
        self.hits = 1
        self.state = "tentative"          # tentative|confirmed|coasting|lost
        self.votes = Counter()
        self.counted = False
        self.crossing_type = None
        self.trail = deque(maxlen=int(C.TRAIL_LEN))
        self.trail.append(self.pos)
        self.area = float(det["area"])
        self.pal = None                   # running mean Lab (re-id appearance)
        self.pal_w = 0.0
        self.coast = 0.0                  # seconds since last detection
        self.lost_t = None
        self.revived = False              # True once re-identified after a loss
        self._absorb(det)

    def _absorb(self, det):
        self.area = 0.7 * self.area + 0.3 * float(det["area"])
        pal = det.get("palette")
        if pal:
            lab = np.array([p.lab for p in pal], np.float32)
            w = np.array([p.weight for p in pal], np.float32)
            wsum = float(w.sum())
            if wsum > 1e-6:
                mean = (lab * w[:, None]).sum(axis=0) / wsum
                if self.pal is None:
                    self.pal = mean
                else:
                    self.pal = (self.pal * self.pal_w + mean * wsum) \
                        / (self.pal_w + wsum)
                self.pal_w += wsum

    @property
    def speed_px_s(self):
        return math.hypot(self.kf.x[2], self.kf.x[3])

    def decide(self):
        # identical voting rule to sorter.Track.decide (kept identical on purpose)
        total = sum(self.votes.values())
        if total == 0:
            return None
        if self.votes[4] >= 1 and self.votes[4] / total >= C.DEFECT_VOTE_RATIO:
            return 4
        good = {g: n for g, n in self.votes.items() if g != 4}
        return max(good, key=good.get) if good else 4


# ------------------------------------------------------------------ tracker
class TrajectoryTracker:
    """Kalman + Hungarian association + lifecycle + re-id + segment crossings
    + duplicate suppression."""

    def __init__(self, line=None):
        self.line = line
        self.tracks = []                  # live: tentative + confirmed + coasting
        self.lost = []                    # recently lost (re-id pool)
        self.registry = deque(maxlen=200)  # counted crossings (t_c, pt, grade)
        self.suppressed = 0
        self.estimated = 0
        self.lost_before_line = 0
        self.unique_crossed = 0
        self._last_t = None
        self._belt = None                 # learned belt unit direction
        self._belt_speed = 0.0

    # ---------------------------------------------------------------- public
    def set_line(self, line):
        """Line moved while running: drop previous positions so the move itself
        never produces a crossing."""
        self.line = line
        for tr in self.tracks + self.lost:
            tr.seg_prev = None
            tr.seg_prev_t = None

    def update(self, dets, t):
        """One frame. Returns {"votes": [(track, det)], "crossings": [Crossing]}.
        The caller adds grade votes first, then handles crossings."""
        dt = 1e-3 if self._last_t is None else max(1e-4, t - self._last_t)
        self._last_t = t
        crossings = []
        for tr in self.tracks:
            tr.kf.predict(dt)
        dets = self._nms(dets)
        self._learn_belt()

        pairs = self._assign(dets, dt)
        matched_t, matched_d, votes = set(), set(), []
        for ti, di in pairs:
            tr = self.tracks[ti]
            self._match(tr, dets[di], t, dt, crossings)
            matched_t.add(id(tr))
            matched_d.add(di)
            votes.append((tr, dets[di]))

        # re-id: unmatched detections vs recently lost / coasting tracks
        for di, det in enumerate(dets):
            if di in matched_d:
                continue
            tr = self._reid(det, t, dt)
            if tr is not None:
                tr.revived = True
                self._match(tr, det, t, dt, crossings)
                matched_t.add(id(tr))
                matched_d.add(di)
                votes.append((tr, det))

        # unmatched live tracks coast along their Kalman prediction
        survivors = []
        for tr in self.tracks:
            if id(tr) in matched_t:
                survivors.append(tr)
                continue
            tr.state = "coasting"
            tr.coast += dt
            self._advance(tr, tr.kf.pos, t, crossings)
            tr.trail.append(tr.pos)
            if tr.coast > float(C.TRACK_MAX_COAST_S):
                tr.state = "lost"
                tr.lost_t = t
                self.lost.append(tr)
                if not tr.counted:
                    self.lost_before_line += 1
            else:
                survivors.append(tr)
        self.tracks = survivors

        # brand new tracks for still-unmatched detections
        for di, det in enumerate(dets):
            if di in matched_d:
                continue
            tr = TTrack(det, t)
            self.tracks.append(tr)
            votes.append((tr, det))

        w = float(C.TRACK_REID_WINDOW_S)
        self.lost = [tr for tr in self.lost if t - tr.lost_t <= w]
        return {"votes": votes, "crossings": crossings}

    # ------------------------------------------------------------- internals
    def _gate(self, tr, dt):
        spd = max(tr.speed_px_s, self._belt_speed)
        return max(float(C.TRACK_GATE_MIN_PX),
                   float(C.TRACK_GATE_SPEED_FACTOR) * spd * dt) + 20.0

    def _learn_belt(self):
        vs = [tr.kf.x[2:] for tr in self.tracks
              if tr.state == "confirmed" and tr.speed_px_s > 5.0]
        if len(vs) < 3:
            return
        v = np.median(np.array(vs), axis=0)
        n = float(np.hypot(v[0], v[1]))
        if n > 1.0:
            self._belt = v / n
            self._belt_speed = n

    def _typical_diameter(self):
        areas = [tr.area for tr in self.tracks if tr.state == "confirmed"]
        if areas:
            a = float(np.median(areas))
        else:
            a = (float(C.MIN_BEAN_AREA) + float(C.MAX_BEAN_AREA)) / 2.0
        return 2.0 * math.sqrt(a / math.pi)

    def _nms(self, dets):
        """Merge detections closer than factor * typical diameter (one bean
        reported as two blobs). Keeps the larger blob."""
        lim = float(C.TRACK_MIN_SPACING_FACTOR) * self._typical_diameter()
        keep = []
        for d in sorted(dets, key=lambda d: -float(d["area"])):
            cx, cy = d["centroid"]
            if all(math.hypot(cx - k["centroid"][0], cy - k["centroid"][1])
                   >= lim for k in keep):
                keep.append(d)
        return keep

    def _assign(self, dets, dt):
        """Hungarian over gated cost: Kalman distance + direction penalty +
        area-ratio penalty + appearance (palette) term. The palette term keeps
        tracks from swapping when two same-speed beans pass close together."""
        n, m = len(self.tracks), len(dets)
        if n == 0 or m == 0:
            return []
        det_lab = [_det_mean_lab(d) for d in dets]
        cost = np.full((n, m), BIG, np.float64)
        for ti, tr in enumerate(self.tracks):
            gate = self._gate(tr, dt)
            px, py = tr.kf.pos
            for di, det in enumerate(dets):
                dx = det["centroid"][0] - px
                dy = det["centroid"][1] - py
                dist = math.hypot(dx, dy)
                if dist > gate:
                    continue
                c = dist
                if self._belt is not None and dist > 1.0:
                    implied = (dx * self._belt[0] + dy * self._belt[1]) / dist
                    if implied < -0.3:          # moves against the belt
                        c += gate
                ratio = float(det["area"]) / max(1e-6, tr.area)
                c += 60.0 * abs(math.log(max(1e-3, ratio)))
                if tr.pal is not None and det_lab[di] is not None:
                    c += 5.0 * float(np.linalg.norm(det_lab[di] - tr.pal))
                cost[ti, di] = c
        if max(n, m) > int(C.TRACK_HUNGARIAN_MAX):
            return self._greedy(cost)
        return [(ti, di) for ti, di in hungarian(cost) if cost[ti, di] < BIG / 2]

    @staticmethod
    def _greedy(cost):
        """Greedy-by-cost assignment for very crowded frames where the
        O(n^2*m) Hungarian would be too slow in pure Python."""
        pairs = sorted((float(cost[ti, di]), ti, di)
                       for ti in range(cost.shape[0])
                       for di in range(cost.shape[1])
                       if cost[ti, di] < BIG / 2)
        used_t, used_d, out = set(), set(), []
        for c, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            out.append((ti, di))
        return out

    def _reid(self, det, t, dt):
        """Match a stray detection back to a recently lost/coasting track:
        enlarged gate + area ratio + palette dE. Two passes: uncounted tracks
        first (a new bean must not inherit a counted identity); counted tracks
        only as a fallback so a still-visible bean keeps ONE identity instead
        of spawning a ghost track that would count it twice."""
        pool = [tr for tr in self.lost if t - tr.lost_t <= C.TRACK_REID_WINDOW_S]
        pool += [tr for tr in self.tracks if tr.last_t < t]  # unmatched this frame
        return (self._reid_scan(det, t, dt, [tr for tr in pool if not tr.counted])
                or self._reid_scan(det, t, dt, [tr for tr in pool if tr.counted]))

    def _reid_scan(self, det, t, dt, pool):
        lo, hi = C.TRACK_AREA_RATIO
        best, best_d = None, float("inf")
        for tr in pool:
            # gate covers prediction error over the blind interval; a full
            # reversal doubles the effective displacement (2x speed term);
            # capped so stale tracks cannot steal far-away new beans
            gap = max(dt, t - tr.last_t)
            gate = min(160.0, float(C.TRACK_GATE_MIN_PX) + 20.0 +
                       2.0 * float(C.TRACK_GATE_SPEED_FACTOR) *
                       max(tr.speed_px_s, self._belt_speed, REID_SPEED_FLOOR) * gap)
            px, py = tr.kf.pos
            d = math.hypot(det["centroid"][0] - px, det["centroid"][1] - py)
            if d > gate or d >= best_d:
                continue
            if not (lo <= float(det["area"]) / max(1e-6, tr.area) <= hi):
                continue
            pal = det.get("palette")
            if tr.pal is not None and pal:
                ref = {0: np.asarray(tr.pal, np.float32).reshape(1, 3)}
                if vision.Classifier(ref).scores(pal)[0] > C.TRACK_REID_DE:
                    continue
            best, best_d = tr, d
        return best

    def _match(self, tr, det, t, dt, crossings):
        tr.kf.update(det["centroid"])
        tr.hits += 1
        tr.last_t = t
        tr.coast = 0.0
        tr.state = "confirmed" if tr.hits >= int(C.TRACK_MIN_HITS) else "tentative"
        tr._absorb(det)
        pos = (float(det["centroid"][0]), float(det["centroid"][1]))
        tr.pos = pos
        tr.trail.append(pos)
        self._advance(tr, pos, t, crossings)
        if tr in self.lost:
            self.lost.remove(tr)
            if tr not in self.tracks:
                self.tracks.append(tr)

    def _advance(self, tr, pos, t, crossings):
        """Move the track's reference point to `pos` at time `t`; test the
        segment (seg_prev -> pos) against the line and emit one crossing."""
        if tr.seg_prev is not None and not tr.counted and self.line is not None:
            hit = _seg_hit(tr.seg_prev, pos, self.line.p1, self.line.p2,
                           float(C.LINE_MARGIN))
            if hit is not None:
                s, u = hit
                t_c = tr.seg_prev_t + s * (t - tr.seg_prev_t)
                pt = (tr.seg_prev[0] + s * (pos[0] - tr.seg_prev[0]),
                      tr.seg_prev[1] + s * (pos[1] - tr.seg_prev[1]))
                tr.counted = True
                tr.crossing_type = ("estimated" if tr.state == "coasting"
                                    else "tentative" if tr.state == "tentative"
                                    else "observed")
                crossings.append(Crossing(tr, t_c, pt, u, tr.crossing_type))
        tr.seg_prev = pos
        tr.seg_prev_t = t

    # ------------------------------------------------- duplicate suppression
    def dedupe_check(self, tr, pt, t_c, grade):
        """True ONLY for a genuine duplicate, never for a real bean.

        Suppression requires ALL of:
          (1) the crossing track is weakly established: fewer than 5 hits,
              tentative, revived by re-id, or crossing_type "estimated"
              (a track with its own continuous history is real - exempt);
          (2) inside DEDUPE_RADIUS_PX and the speed-aware window
              min(DEDUPE_WINDOW_S, 0.5*diameter/speed) of a counted crossing;
          (3) palette dE to that crossing's bean below TRACK_REID_DE.
        Fail-safe: a grade-4 crossing right after a counted crossing that
        was NOT grade 4 can never be suppressed (count it and fire)."""
        established = (tr.hits >= 5 and tr.state != "tentative"
                       and not tr.revived and tr.crossing_type != "estimated")
        if established:
            return False
        r = float(C.DEDUPE_RADIUS_PX)
        spd = max(tr.speed_px_s, self._belt_speed, 1.0)
        w = min(float(C.DEDUPE_WINDOW_S),
                0.5 * self._typical_diameter() / spd)
        for tc, pp, g, pal in self.registry:
            if abs(t_c - tc) > w or \
                    math.hypot(pt[0] - pp[0], pt[1] - pp[1]) > r:
                continue
            if grade == 4 and g != 4:
                continue                    # fail-safe: real defect after pass
            if tr.pal is None or pal is None:
                continue                    # no appearance evidence -> count it
            if float(np.linalg.norm(tr.pal - pal)) >= float(C.TRACK_REID_DE):
                continue                    # different-looking bean
            self.suppressed += 1
            return True
        return False

    def register_crossing(self, pt, t_c, grade, crossing_type, pal=None):
        self.registry.append(
            (t_c, pt, grade, None if pal is None
             else np.array(pal, np.float32)))
        self.unique_crossed += 1
        if crossing_type == "estimated":
            self.estimated += 1

    # ------------------------------------------------------------- reporting
    def counters(self):
        return {"active": sum(1 for t in self.tracks if t.state != "coasting"),
                "coasting": sum(1 for t in self.tracks if t.state == "coasting"),
                "unique_crossed": self.unique_crossed,
                "suppressed": self.suppressed,
                "estimated": self.estimated,
                "lost_before_line": self.lost_before_line}
