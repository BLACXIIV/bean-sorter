#!/usr/bin/env python3
"""STEP 1 diagnosis: why did trajectory tracking miss defect fires?

Re-runs S15-jam, S15-overlap and S17 with instrumented tracker/sorter, maps
every physical bean's TRUE line crossing to logged crossing events + valve
pulses, and for each missed grade-4 bean prints a per-frame evidence table:
t, det area, merged flag (emit-merged / NMS-dropped / area outside
TRACK_AREA_RATIO vs track median), vote cast, track id/state/revived and the
track's votes at crossing.

  python scripts/diagnose_missed.py
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C
import vision
import sorter as S_mod
import tracking as T_mod
from sorter import Line, Sorter

GRADE_LAB = {1: np.array([70.0, -40.0, 50.0], np.float32),
             2: np.array([55.0, 0.0, -50.0], np.float32),
             3: np.array([45.0, 40.0, 60.0], np.float32),
             4: np.array([20.0, 10.0, 10.0], np.float32)}
MODEL = {g: v.reshape(1, 3) for g, v in GRADE_LAB.items()}
FRAME = np.zeros((720, 1280, 3), np.uint8)


def palette(grade):
    return [vision.PaletteColor(GRADE_LAB[grade].copy(), 1.0, "#000000")]


class FakeValve:
    def __init__(self):
        self.pulses = []

    def pulse(self, delay=None, duration=None):
        self.pulses.append(delay)


class FakeDB:
    def __init__(self):
        self.events = []

    def log_event(self, grade, blown, session_id=None, **kw):
        self.events.append({"grade": grade, "blown": blown, **kw})


class Bean:
    def __init__(self, x, y, vy, grade, r=34, mark=False):
        self.x0, self.y0, self.vy0 = x, y, vy
        self.grade, self.r, self.mark = grade, r, mark
        self.reset()

    def reset(self):
        self.x, self.y, self.vy = self.x0, self.y0, self.vy0

    def det(self, dx=0.0, dy=0.0, area_scale=1.0):
        return {"centroid": (self.x + dx, self.y + dy),
                "area": math.pi * self.r * self.r * area_scale,
                "contour": None, "palette": palette(self.grade)}


# ------------------------------------------------------------ instrumentation
def instrumented_run(beans, emit_frames, fps, n_frames):
    """emit_frames(f, t, beans) -> list[dets]; returns (sorter, log, true_x).

    log: per-frame dict of det->(bean idxs, flags, vote, track, state...).
    true_x[i] = (t, x) when physical bean i's y crossed 360."""
    C.FEATURES["trajectory_tracking"] = True
    T_mod.TTrack._next = 1
    for b in beans:
        b.reset()
    valve, db = FakeValve(), FakeDB()
    sorter = Sorter(db, None, vision.Classifier(MODEL),
                    Line((0, 360), (1280, 360)), valve, session_id=1)

    frames_log = []
    crossings_log = []
    true_x = [None] * len(beans)
    prev_y = [b.y for b in beans]

    # spy: which input detections does NMS drop?
    orig_nms = T_mod.TrajectoryTracker._nms
    def nms_spy(self, dets):
        kept = orig_nms(self, dets)
        for d in dets:
            if all(d is not k for k in kept):
                d["_nms_dropped"] = True
        return kept
    T_mod.TrajectoryTracker._nms = nms_spy

    # spy: log every _count call with the track's vote state
    orig_count = Sorter._count
    def count_spy(self, tr, t_c, point, along, crossing_type, armed, now):
        crossings_log.append({
            "t_c": t_c, "point": point, "track_id": tr.id,
            "type": crossing_type, "votes": dict(tr.votes),
            "grade_decided": tr.decide(), "state": tr.state,
            "revived": tr.revived, "hits": tr.hits,
            "deduped": None})
        before = self.tracker.suppressed
        orig_count(self, tr, t_c, point, along, crossing_type, armed, now)
        crossings_log[-1]["deduped"] = self.tracker.suppressed > before
    S_mod.Sorter._count = count_spy
    try:
        t = 0.0
        for f in range(n_frames):
            t += 1.0 / fps
            dets = emit_frames(f, t, beans)
            for i, b in enumerate(beans):
                if prev_y[i] < 360 <= b.y and true_x[i] is None:
                    true_x[i] = (t, b.x)
            res = sorter.tracker.update(dets, t)
            track_of = {id(det): tr for tr, det in res["votes"]}
            # mirror Sorter.process order: votes first, then crossings
            for tr, det in res["votes"]:
                if sum(tr.votes.values()) < C.MAX_VOTES:
                    pal = det.get("palette")
                    if pal:
                        g = sorter.clf.classify(pal)[0]
                        tr.votes[g] += 1
                        det["_vote"] = g
            for cr in res["crossings"]:
                sorter._count(cr.track, cr.t_c, cr.point, cr.along,
                            cr.crossing_type, True, t)
            rows = [{"det": det, "track": track_of.get(id(det))}
                    for det in dets]
            frames_log.append({"f": f, "t": t, "rows": rows})
            prev_y = [b.y for b in beans]
    finally:
        T_mod.TrajectoryTracker._nms = orig_nms
        S_mod.Sorter._count = orig_count
    return sorter, frames_log, crossings_log, true_x


def analyse(name, beans, emit_frames, fps, n_frames):
    sorter, flog, xlog, true_x = instrumented_run(beans, emit_frames, fps, n_frames)
    print(f"\n{'='*72}\n{name}: crossings={len(xlog)} "
          f"pulses={len(sorter.valve.pulses)} "
          f"g4_beans={sum(1 for b in beans if b.grade == 4)}")
    missed = []
    # one firing crossing event can only account for ONE physical bean
    used_ev = set()
    g4beans = [(i, true_x[i]) for i, b in enumerate(beans)
               if b.grade == 4 and true_x[i] is not None]
    for i, (tc, xc) in g4beans:
        best, best_d = None, 1e9
        for ci, c in enumerate(xlog):
            if ci in used_ev or c["grade_decided"] != 4 or c["deduped"]:
                continue
            d = abs(c["t_c"] - tc) + abs(c["point"][0] - xc) / 200.0
            if abs(c["t_c"] - tc) < 0.6 \
                    and math.hypot(c["point"][0] - xc,
                                   c["point"][1] - 360) < 60 and d < best_d:
                best, best_d = ci, d
        if best is None:
            missed.append((i, beans[i], tc, xc))
        else:
            used_ev.add(best)
    print(f"missed grade-4 beans: {[i for i,_,_,_ in missed]}")

    # per-frame evidence for each missed bean
    for i, b, tc, xc in missed:
        print(f"\n--- bean #{i} (grade 4, true crossing t={tc:.3f} x={xc:.0f}) "
              f"---")
        print(f"{'f':>4} {'t':>7} {'y':>7} {'area':>8} {'flags':>22} "
              f"{'vote':>5} {'trk':>4} {'state':>10} {'rev':>4} {'hits':>5}")
        for fr in flog:
            for row in fr["rows"]:
                det, tr = row["det"], row["track"]
                bi = det.get("_bean", -1)
                if isinstance(bi, list):
                    mine = i in bi
                else:
                    mine = bi == i
                if not mine:
                    continue
                y = det["centroid"][1]
                if not (tc - 3.0 < fr["t"] < tc + 0.5 and -100 < y < 780):
                    continue
                flags = []
                if det.get("_emit_merged"):
                    flags.append("emit-merged")
                if det.get("_nms_dropped"):
                    flags.append("NMS-dropped")
                if tr is None:
                    flags.append("no-track")
                else:
                    ratio = det["area"] / max(1e-6, tr.area)
                    lo, hi = C.TRACK_AREA_RATIO
                    if not (lo <= ratio <= hi):
                        flags.append(f"area-ratio={ratio:.2f}")
                print(f"{fr['f']:>4} {fr['t']:>7.3f} {y:>7.1f} "
                      f"{det['area']:>8.0f} {','.join(flags) or '-':>22} "
                      f"{str(det.get('_vote')):>5} "
                      f"{(tr.id if tr else '-'):>4} "
                      f"{(tr.state if tr else '?'):>10} "
                      f"{str(tr.revived if tr else '?'):>4} "
                      f"{(tr.hits if tr else '?'):>5}")
        # crossings near this bean
        for c in xlog:
            if abs(c["t_c"] - tc) < 0.6 and abs(c["point"][0] - xc) < 80:
                print(f"    crossing ev: trk={c['track_id']} t_c={c['t_c']:.3f} "
                      f"type={c['type']} grade={c['grade_decided']} "
                      f"votes={c['votes']} deduped={c['deduped']} "
                      f"state={c['state']} revived={c['revived']} hits={c['hits']}")
    return sorter


def move(b, dt, t):
    b.y += b.vy * dt


# ------------------------------------------------------------- S15 generator
def s15_emit_factory(seed, mode):
    rng = np.random.default_rng(seed)
    lanes, rows = 8, 4
    n = lanes * rows
    beans = []
    lane_of = {}
    for i in range(n):
        g = 4 if rng.random() < 0.25 else int(rng.integers(1, 4))
        v = (500.0 * (1.0 + rng.uniform(-0.15, 0.15))
             if mode == "overlap" else 0.0)
        b = Bean(80 + (i % lanes) * 140, -60 - (i // lanes) * 110, v, g, r=30)
        b.phase = float(rng.uniform(0, 2 * math.pi))
        b.lane = i % lanes
        beans.append(b)
        lane_of.setdefault(b.lane, []).append(b)
    fps, seconds = 20, 10.0
    nF = int(seconds * fps)
    lane_v = 500.0 * (1.0 + rng.uniform(-0.15, 0.15, (lanes, nF)))
    drop = rng.random((n, nF)) < 0.15
    jx = rng.uniform(-4, 4, (n, nF))
    jy = rng.uniform(-4, 4, (n, nF))
    merges = {}
    for _ in range(8):
        f = int(rng.integers(0, nF))
        row = int(rng.integers(0, rows))
        lane = int(rng.integers(0, lanes - 1))
        merges.setdefault(f, []).append((row * lanes + lane,
                                         row * lanes + lane + 1))

    def emit(f, t, beans):
        merged_from = {}
        for i, j in merges.get(f, []):
            merged_from[i] = j
            merged_from[j] = i
        dets, skip = [], set()
        for i, b in enumerate(beans):
            if mode == "overlap":
                b.y += b.vy / fps
            else:
                b.y += lane_v[b.lane, f] / fps
        if mode == "jam":
            for lane, lst in lane_of.items():
                lst.sort(key=lambda b: -b.y)
                for a, b2 in zip(lst, lst[1:]):
                    if a.y - b2.y < 70:
                        b2.y = a.y - 70
        for i, b in enumerate(beans):
            if not (-80 < b.y < 780) or drop[i, f] or i in skip:
                continue
            if i in merged_from and merged_from[i] > i \
                    and -80 < beans[merged_from[i]].y < 780:
                j = merged_from[i]
                skip.add(j)
                b2 = beans[j]
                d = {"centroid": ((b.x + b2.x) / 2, (b.y + b2.y) / 2),
                     "area": math.pi * b.r * b.r * 1.8,
                     "contour": None, "palette": palette(b.grade),
                     "_bean": [i, j], "_emit_merged": True}
                dets.append(d)
                continue
            if i in merged_from:
                continue
            wx = 8.0 * math.sin(0.02 * b.y + b.phase)
            d = b.det(dx=wx + jx[i, f], dy=jy[i, f])
            d["_bean"] = i
            dets.append(d)
        return dets
    return beans, emit, fps, nF


def s17_factory():
    rng17 = np.random.default_rng(1717)
    gs = [4 if rng17.random() < 0.25 else int(rng17.integers(1, 4))
          for _ in range(30)]
    beans = [Bean(100 + (i % 6) * 200, -60 - i * 90, 500.0, gs[i])
             for i in range(30)]
    for a, b_ in ((6, 7), (12, 13), (18, 19)):
        beans[b_].x0 = beans[a].x0 + 66
        beans[b_].y0 = beans[a].y0
        beans[b_].mark = True
        beans[a].mark = True
    pair_of = {6: 7, 7: 6, 12: 13, 13: 12, 18: 19, 19: 18}
    for b in beans:
        b.reset()

    def emit(f, t, beans):
        dets, done = [], set()
        for i, b in enumerate(beans):
            b.y += b.vy / 20
            if not (-80 < b.y < 780):
                continue
            if i in pair_of and i not in done:
                j = pair_of[i]
                b2 = beans[j]
                done.add(j)
                if not (-80 < b2.y < 780):
                    d = b.det(); d["_bean"] = i
                    dets.append(d)
                    continue
                d = {"centroid": ((b.x + b2.x) / 2, (b.y + b2.y) / 2),
                     "area": math.pi * b.r * b.r * 1.9, "contour": None,
                     "palette": palette(b.grade), "_bean": [i, j],
                     "_emit_merged": True}
                dets.append(d)
            else:
                d = b.det(); d["_bean"] = i
                dets.append(d)
        return dets
    return beans, emit, 20, 160


# ------------------------------------------------------------------- run all
print("Scanning S15-jam seeds 5000-5019 for fires < expected ...")
for seed in range(5000, 5020):
    beans, emit, fps, nF = s15_emit_factory(seed, "jam")
    s, fl, xl, tx = instrumented_run(beans, emit, fps, nF)
    ng4 = sum(1 for b in beans if b.grade == 4)
    if len(s.valve.pulses) < ng4:
        print(f"\n>>> seed {seed}: fires={len(s.valve.pulses)} expected={ng4}")
        analyse(f"S15-jam seed {seed}", beans, emit, fps, nF)

print("\nScanning S15-overlap seeds ...")
for seed in range(5000, 5020):
    beans, emit, fps, nF = s15_emit_factory(seed, "overlap")
    s, fl, xl, tx = instrumented_run(beans, emit, fps, nF)
    ng4 = sum(1 for b in beans if b.grade == 4)
    if len(s.valve.pulses) < ng4:
        print(f"\n>>> seed {seed}: fires={len(s.valve.pulses)} expected={ng4}")
        analyse(f"S15-overlap seed {seed}", beans, emit, fps, nF)

beans, emit, fps, nF = s17_factory()
analyse("S17 touching pairs", beans, emit, fps, nF)
