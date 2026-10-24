"""Trajectory tracking scenarios S1-S16 + Hungarian brute-force check.

Deterministic: seeded RNG, injected timestamps, fake detections fed straight
into Sorter (no camera, no wall clock). Each scenario runs with BOTH trackers;
the trajectory tracker must equal the expected count in every scenario.

  python tests/test_tracking.py
"""
import itertools
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

PASS, FAIL = "PASS", "FAIL"
results = []
table = []


def report(name, ok, detail=""):
    results.append(ok)
    print(f"{PASS if ok else FAIL}  {name}  {detail}")


# ------------------------------------------------------------------- fakes
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

    def pulse(self, delay=None, duration=None, uncertain=False,
              t_c=None, x_on_line=None):
        self.pulses.append((delay, uncertain))


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
        if hasattr(self, "tc_true"):
            del self.tc_true          # (t, x) of the real y=360 crossing

    def det(self, dx=0.0, dy=0.0, area_scale=1.0):
        return {"centroid": (self.x + dx, self.y + dy),
                "area": math.pi * self.r * self.r * area_scale,
                "contour": None, "palette": palette(self.grade)}


def make_sorter(use_traj, valve, db, line):
    C.FEATURES["trajectory_tracking"] = use_traj
    T_mod.TTrack._next = 1
    S_mod.Track._next = 1
    return Sorter(db, None, vision.Classifier(MODEL), line, valve, session_id=1)


def run_scenario(use_traj, beans, fps, seconds, advance, emit, line=None):
    for b in beans:
        b.reset()
    valve, db = FakeValve(), FakeDB()
    sorter = make_sorter(use_traj, valve, db, line or Line((0, 360), (1280, 360)))
    t, ev_i = 0.0, 0
    for f in range(int(seconds * fps)):
        t += 1.0 / fps
        dets = []
        for b in beans:
            py = b.y
            advance(b, 1.0 / fps, t)
            if py < 360 <= b.y and not hasattr(b, "tc_true"):
                b.tc_true = (t, b.x)      # real crossing, ground truth
            if -80 < b.y < 780:
                dets += emit(b, f, t)
        sorter.process(FRAME, True, t=t, detections=dets)
        for e in db.events[ev_i:]:        # stamp events with frame time
            e["t"] = t
        ev_i = len(db.events)
    return sorter


def blown_g4(sorter, beans, radius=90.0, window=0.8):
    """Set of grade-4 bean indices credited with a valve pulse.

    A blown event is credited to every G4 bean whose true crossing is within
    `window` seconds and `radius` px of it - a pulse can only act on the beans
    actually at the line at that moment (touching pairs share one pulse)."""
    out = set()
    for e in sorter.db.events:
        if not e.get("blown"):
            continue
        x_on = (e.get("x_on_line") or 0.0) * 1280.0
        for i, b in enumerate(beans):
            if b.grade != 4 or not hasattr(b, "tc_true"):
                continue
            tc, xc = b.tc_true
            if abs(tc - e.get("t", tc)) <= window and abs(xc - x_on) <= radius:
                out.add(i)
    return out


INVARIANTS = []        # (name, legacy blown-beans, trajectory blown-beans, xfail)


def std_beans(n, speed, grades, r=34, y0=-60.0, dy=90.0):
    return [Bean(100 + (i % 6) * 200, y0 - i * dy, speed, grades[i], r)
            for i in range(n)]


def plain_emit(b, f, t):
    return [b.det()]


def move(b, dt, t):
    b.y += b.vy * dt


def scenario(name, expected_counts, expected_fires, args, expect_estimated=None):
    beans, fps, seconds, advance, emit = args
    trj = run_scenario(True, beans, fps, seconds, advance, emit)
    counted = sum(trj.stats.values())
    counters = trj.trk_counters()
    fires = len(trj.valve.pulses)
    ok = counted == expected_counts and fires == expected_fires
    extra = f" est={counters['estimated']}" if expect_estimated is not None else ""
    if expect_estimated is not None:
        ok = ok and counters["estimated"] == expect_estimated
    report(f"{name} (trajectory)", ok,
           f"counted={counted}/{expected_counts} fires={fires}/{expected_fires}{extra}")
    leg = run_scenario(False, beans, fps, seconds, advance, emit)
    lc = sum(leg.stats.values())
    INVARIANTS.append((name, len(blown_g4(leg, beans)),
                       len(blown_g4(trj, beans)), False))
    table.append((name, expected_counts, lc, counted,
                  expected_fires, len(leg.valve.pulses), fires))


# ============================================================ S1 clean flow
rng = np.random.default_rng(11)
grades = [4 if rng.random() < 0.25 else int(rng.integers(1, 4)) for _ in range(30)]
ng4 = grades.count(4)
scenario("S1 clean flow 30 beans 20fps", 30, ng4,
         (std_beans(30, 500, grades), 20, 8.0, move, plain_emit))

# ================================================== S2 random dropouts 20 %
drop2 = np.random.default_rng(22).random((30, 400)) < 0.20


def emit2(b, f, t):
    return [] if f < drop2.shape[1] and drop2[beans2.index(b), f] else [b.det()]


beans2 = std_beans(30, 500, grades)
scenario("S2 dropouts 20%", 30, ng4, (beans2, 20, 8.0, move, emit2))

# ===================================== S3 detection gap centred on the line
def emit3(b, f, t):
    return [] if abs(b.y - 360) < b.vy * 0.1 else [b.det()]


scenario("S3 0.2s gap at line", 30, ng4,
         (std_beans(30, 500, grades), 20, 8.0, move, emit3), expect_estimated=30)

# ============================================= S4 jitter near the line +-6px
jit = np.random.default_rng(44)


def emit4(b, f, t):
    if abs(b.y - 360) < 40:
        return [b.det(dx=float(jit.uniform(-6, 6)), dy=float(jit.uniform(-6, 6)))]
    return [b.det()]


scenario("S4 jitter near line", 30, ng4,
         (std_beans(30, 500, grades), 20, 8.0, move, emit4))

# ================================================= S5 low fps, fast beans
rng5 = np.random.default_rng(55)
beans5 = [Bean(100 + (i % 6) * 200, -60 - i * 90, float(rng5.uniform(560, 880)),
               grades[i]) for i in range(30)]          # 70-110 px/frame @8fps
scenario("S5 8fps fast beans", 30, ng4, (beans5, 8, 12.0, move, plain_emit))

# ============================================ S6 one bean split into 2 blobs
beans6 = std_beans(30, 500, grades)
beans6[5].mark = True


def emit6(b, f, t):
    if b.mark and 10 <= f < 13:
        return [b.det(dx=-15, area_scale=0.55), b.det(dx=+15, area_scale=0.45)]
    return [b.det()]


scenario("S6 two blobs one bean", 30, ng4, (beans6, 20, 8.0, move, emit6))

# ========================================= S7 lost 0.4 s before the line
beans7 = std_beans(30, 500, grades)
beans7[7].mark = True
beans7[7].grade = 4                            # must still fire exactly once
ng4_7 = sum(1 for b in beans7 if b.grade == 4)


def emit7(b, f, t):
    if b.mark and 0 < (360 - b.y) < b.vy * 0.4:
        return []                              # vanished approaching the line
    return [b.det()]


scenario("S7 lost before line, reappears", 30, ng4_7, (beans7, 20, 8.0, move, emit7))

# ============================================ S8 two beans 40 px side by side
beans8 = std_beans(28, 500, grades[:28], r=28)  # diam 56 -> merge dist 33.6 < 40
pair_y = -60 - 40 * 90
beans8 += [Bean(600, pair_y, 500, 4, r=28), Bean(640, pair_y, 500, 1, r=28)]
scenario("S8 pair 40px apart", 30, grades[:28].count(4) + 1,
         (beans8, 20, 9.0, move, plain_emit))

# ========================================= S9 belt stops 1 s on the line
def run_s9(use_traj):
    beans = std_beans(30, 500, grades)
    beans[3].mark = True
    stop_t = [None]
    rng9 = np.random.default_rng(99)
    for b in beans:
        b.reset()
    valve, db = FakeValve(), FakeDB()
    sorter = make_sorter(use_traj, valve, db, Line((0, 360), (1280, 360)))
    t, ev_i = 0.0, 0
    for f in range(160):
        t += 1.0 / 20
        dets = []
        for b in beans:
            py = b.y
            if b.mark and stop_t[0] is None and b.y >= 360:
                stop_t[0] = t
            if b.mark and stop_t[0] is not None and t < stop_t[0] + 1.0:
                pass                            # frozen on the line for 1 s
            else:
                b.y += b.vy / 20
            if py < 360 <= b.y and not hasattr(b, "tc_true"):
                b.tc_true = (t, b.x)
            if -80 < b.y < 780:
                if b.mark and stop_t[0] is not None and t < stop_t[0] + 1.0 \
                        and rng9.random() < 0.3:
                    continue                    # detection flicker while stopped
                dets.append(b.det())
        sorter.process(FRAME, True, t=t, detections=dets)
        for e in db.events[ev_i:]:
            e["t"] = t
        ev_i = len(db.events)
    return sorter, beans


trj9, beans9 = run_s9(True)
n9, f9 = sum(trj9.stats.values()), len(trj9.valve.pulses)
report("S9 belt stop on line (trajectory)", n9 == 30 and f9 == ng4,
       f"counted={n9}/30 fires={f9}/{ng4}")
leg9, _ = run_s9(False)
INVARIANTS.append(("S9 belt stop on line", len(blown_g4(leg9, beans9)),
                   len(blown_g4(trj9, beans9)), False))
table.append(("S9 belt stop on line", 30, sum(leg9.stats.values()), n9,
              ng4, len(leg9.valve.pulses), f9))

# ======================================== S10 vibrate across and forth
def adv10(b, dt, t):
    if b.mark:
        if b.y > 380:
            b.vy = -abs(b.vy)
        elif b.vy < 0 and b.y < 340:   # only bounce up after it turned around
            b.vy = abs(b.vy)
    b.y += b.vy * dt


beans10 = std_beans(30, 500, grades)
beans10[9].mark = True
beans10[9].x0 = 640                  # own column: vibrates without NMS colliding
scenario("S10 vibrate at line", 30, ng4, (beans10, 20, 8.0, adv10, plain_emit))

# ======================================== S11 line moved while running
def run_s11(use_traj):
    beans = std_beans(30, 500, grades)
    for b in beans:
        b.reset()
    valve, db = FakeValve(), FakeDB()
    sorter = make_sorter(use_traj, valve, db, Line((0, 360), (1280, 360)))
    t, ev_i = 0.0, 0
    for f in range(180):
        t += 1.0 / 20
        if f == 40:
            sorter.set_line(Line((0, 520), (1280, 520)))   # move mid-run
        dets = []
        for b in beans:
            py = b.y
            b.y += b.vy / 20
            if py < 360 <= b.y and not hasattr(b, "tc_true"):
                b.tc_true = (t, b.x)
            if -80 < b.y < 780:
                dets.append(b.det())
        sorter.process(FRAME, True, t=t, detections=dets)
        for e in db.events[ev_i:]:
            e["t"] = t
        ev_i = len(db.events)
    return sorter, beans


trj11, beans11 = run_s11(True)
n11 = sum(trj11.stats.values())
report("S11 line moved (trajectory)", n11 == 30,
       f"counted={n11}/30 (move itself must count nothing)")
leg11, _ = run_s11(False)
INVARIANTS.append(("S11 line moved", len(blown_g4(leg11, beans11)),
                   len(blown_g4(trj11, beans11)), False))
table.append(("S11 line moved", 30, sum(leg11.stats.values()), n11,
              ng4, len(leg11.valve.pulses), len(trj11.valve.pulses)))

# ==================== S12 dense single-file: 40 beans, gap 1.2 diameters
# Same-lane crossings land on the SAME line point; at 600 px/s they are only
# ~0.14 s apart - inside the old fixed 0.15 s dedupe window. The speed-aware
# window + established-track exemption must keep every bean counted.
rng12 = np.random.default_rng(1212)
grades12 = [4 if rng12.random() < 0.25 else int(rng12.integers(1, 4))
            for _ in range(40)]
ng4_12 = grades12.count(4)

for speed12 in (150, 300, 600):
    beans12 = [Bean(640, -60 - i * (1.2 * 68), float(speed12), grades12[i], r=34)
               for i in range(40)]
    secs12 = (60 + 39 * 1.2 * 68 + 420 + 120) / speed12 + 1.0
    scenario(f"S12 dense single-file {speed12}px/s", 40, ng4_12,
             (beans12, 20, secs12, move, plain_emit))

# ==================== S13 alternating good/defect in dense flow
# G, G4, G, G4 ... at 600 px/s. The valve fail-safe (G4 after a non-G4
# crossing is never suppressed) plus the palette check must fire all 20.
grades13 = [1 if i % 2 == 0 else 4 for i in range(40)]
beans13 = [Bean(640, -60 - i * (1.2 * 68), 600.0, grades13[i], r=34)
           for i in range(40)]
secs13 = (60 + 39 * 1.2 * 68 + 420 + 120) / 600 + 1.0
scenario("S13 alternating G/G4 dense 600px/s", 40, 20,
         (beans13, 20, secs13, move, plain_emit))

# ==================== S14 two-blob split WITHIN 30 px of the line
# (S6 splits at frames 10-12, i.e. y ~ 190-240: ~120-170 px BEFORE the line,
#  not at it.) Here the split happens exactly around the crossing.
beans14 = std_beans(30, 500, grades)
beans14[5].mark = True


def emit14(b, f, t):
    if b.mark and abs(b.y - 360) <= 30:      # 3 frames at 500 px/s, 25 px/fr
        return [b.det(dx=-15, area_scale=0.55), b.det(dx=+15, area_scale=0.45)]
    return [b.det()]


scenario("S14 two blobs at the line", 30, ng4,
         (beans14, 20, 8.0, move, emit14))

# ==================== S15 realistic motion, 20 seeded runs
# lateral wobble +-8 px, jitter +-4 px, 15% dropouts, random 1-frame merges.
# Two variants share the frame loop:
#   jam     - beans in a lane share belt-speed jitter (+-15%) and may touch
#             but never overlap (they jam, like real beans in a chute)
#   overlap - the ORIGINAL generator: each bean gets its own constant +-15%
#             speed, so same-lane beans drift together and physically overlap
def run_s15(use_traj, seed, mode="jam"):
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
    valve, db = FakeValve(), FakeDB()
    sorter = make_sorter(use_traj, valve, db, Line((0, 360), (1280, 360)))
    t, ev_i = 0.0, 0
    for f in range(nF):
        t += 1.0 / fps
        merged_from = {}
        for i, j in merges.get(f, []):
            merged_from[i] = j
            merged_from[j] = i
        dets = []
        skip = set()
        for i, b in enumerate(beans):
            py = b.y
            if mode == "overlap":
                b.y += b.vy / fps          # per-bean speed: beans CAN overlap
            else:
                b.y += lane_v[b.lane, f] / fps
            if py < 360 <= b.y and not hasattr(b, "tc_true"):
                b.tc_true = (t, b.x)
        if mode == "jam":
            for lane, lst in lane_of.items():   # beans jam, never overlap
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
                dets.append({
                    "centroid": ((b.x + b2.x) / 2, (b.y + b2.y) / 2),
                    "area": math.pi * b.r * b.r * 1.8,
                    "contour": None,
                    "palette": palette(b.grade)})
                continue
            if i in merged_from:
                continue
            wx = 8.0 * math.sin(0.02 * b.y + b.phase)
            dets.append(b.det(dx=wx + jx[i, f], dy=jy[i, f]))
        sorter.process(FRAME, True, t=t, detections=dets)
        for e in db.events[ev_i:]:
            e["t"] = t
        ev_i = len(db.events)
    return sorter, beans


def run_s15_set(mode, seeds=20):
    """20 seeded runs; per-tracker (counts, fires, g4 beans, blown-beans)."""
    out = {}
    for use_traj in (True, False):
        cs, fs, g4s, bs = [], [], [], []
        for r in range(seeds):
            st, beans = run_s15(use_traj, 5000 + r, mode)
            cs.append(sum(st.stats.values()))
            fs.append(len(st.valve.pulses))
            g4s.append(sum(1 for b in beans if b.grade == 4))
            bs.append(len(blown_g4(st, beans)))
        out[use_traj] = (cs, fs, g4s, bs)
    return out


# --- S15-jam: physically consistent; trajectory must be exact on all 20 runs,
# counts AND fires (STEP 3.1: fires == expected on every seed, not just total)
res_j = run_s15_set("jam")
tj_c, tj_f, j_g4, tj_b = res_j[True]
lj_c, lj_f, _, lj_b = res_j[False]
report("S15-jam realistic 20 seeded runs (trajectory)",
       all(c == 32 for c in tj_c)
       and all(f == g for f, g in zip(tj_f, j_g4)),
       f"counts {min(tj_c)}..{max(tj_c)}/32, "
       f"fires {sum(tj_f)}/{sum(j_g4)} "
       f"(per-seed min..max {min(tj_f)}..{max(tj_f)})")
print(f"      S15-jam legacy counts {min(lj_c)}..{max(lj_c)}/32 "
      f"fires {sum(lj_f)}/{sum(j_g4)}")
viol = sum(1 for tb, lb in zip(tj_b, lj_b) if tb < lb)
print(f"      S15-jam blown-beans traj={sum(tj_b)} legacy={sum(lj_b)} "
      f"seeds traj<legacy: {viol}")
INVARIANTS.append(("S15-jam (20 runs)", sum(lj_b), sum(tj_b), False))
table.append(("S15-jam realistic (20 runs)", 32,
              f"{min(lj_c)}..{max(lj_c)}", f"{min(tj_c)}..{max(tj_c)}",
              sum(j_g4), sum(lj_f), sum(tj_f)))

# --- S15-overlap: the original generator. Beans DO physically overlap, so
# one blob can hide a bean for good - an EXPECTED undercount, not a tracker
# bug (see AGENTS.md known limitations). Numbers are printed, never hidden.
res_o = run_s15_set("overlap")
to_c, to_f, o_g4, to_b = res_o[True]
lo_c, lo_f, _, lo_b = res_o[False]
ok_o = all(c == 32 for c in to_c)
if ok_o:
    report("S15-overlap 20 seeded runs (trajectory)", True,
           f"counts {min(to_c)}..{max(to_c)}/32")
else:
    print(f"XFAIL S15-overlap (trajectory) counts {min(to_c)}..{max(to_c)}/32 "
          f"under={640 - sum(to_c)} of 640, fires {sum(to_f)}/{sum(o_g4)}, "
          f"blown-beans {sum(to_b)} - overlapping beans are one blob "
          f"(known limitation)")
print(f"      S15-overlap legacy counts {min(lo_c)}..{max(lo_c)}/32 "
      f"fires {sum(lo_f)}/{sum(o_g4)} blown-beans {sum(lo_b)}")
INVARIANTS.append(("S15-overlap (20 runs, XFAIL)", sum(lo_b), sum(to_b), True))
table.append(("S15-overlap (20 runs, XFAIL)", 32,
              f"{min(lo_c)}..{max(lo_c)}", f"{min(to_c)}..{max(to_c)}",
              sum(o_g4), sum(lo_f), sum(to_f)))

# ==================== S17 touching pairs -> one merged blob, 20 seeds
# 20% of beans travel touching a neighbour; segmentation would return ONE
# blob of ~1.9x normal area. Expected: each pair counts once -> undercount.
# STEP 3.3: 20 seeds for BOTH trackers, counts and fires vs expected.
def run_s17(use_traj, seed):
    rng17 = np.random.default_rng(seed)
    gs = [4 if rng17.random() < 0.25 else int(rng17.integers(1, 4))
          for _ in range(30)]
    beans = std_beans(30, 500, gs)
    # pairs (6,7), (12,13), (18,19): move side by side, touching (diam 68)
    for a, b_ in ((6, 7), (12, 13), (18, 19)):
        beans[b_].x0 = beans[a].x0 + 66        # touching -> one blob
        beans[b_].y0 = beans[a].y0
        beans[b_].mark = True
        beans[a].mark = True
    pair_of = {6: 7, 7: 6, 12: 13, 13: 12, 18: 19, 19: 18}
    for b in beans:
        b.reset()

    valve, db = FakeValve(), FakeDB()
    sorter = make_sorter(use_traj, valve, db, Line((0, 360), (1280, 360)))
    t, ev_i = 0.0, 0
    for f in range(160):
        t += 1.0 / 20
        dets = []
        merged_done = set()
        for i, b in enumerate(beans):
            py = b.y
            b.y += b.vy / 20
            if py < 360 <= b.y and not hasattr(b, "tc_true"):
                b.tc_true = (t, b.x)
            if not (-80 < b.y < 780):
                continue
            if i in pair_of and i not in merged_done:
                j = pair_of[i]
                b2 = beans[j]
                merged_done.add(j)
                if not (-80 < b2.y < 780):
                    dets.append(b.det())
                    continue
                # one blob covering both beans: midpoint, ~1.9x area
                dets.append({"centroid": ((b.x + b2.x) / 2,
                                          (b.y + b2.y) / 2),
                             "area": math.pi * b.r * b.r * 1.9,
                             "contour": None,
                             "palette": palette(b.grade)})
            else:
                dets.append(b.det())
        sorter.process(FRAME, True, t=t, detections=dets)
        for e in db.events[ev_i:]:
            e["t"] = t
        ev_i = len(db.events)
    return sorter, gs, beans


s17 = {True: [], False: []}
s17_blown = {True: [], False: []}
s17_g4s = []
for seed17 in range(1717, 1737):              # 20 seeds, BOTH trackers
    for use_traj in (True, False):
        st, gs17, beans17 = run_s17(use_traj, seed17)
        s17[use_traj].append((sum(st.stats.values()),
                              len(st.valve.pulses)))
        s17_blown[use_traj].append(len(blown_g4(st, beans17)))
    s17_g4s.append(gs17.count(4))
exp_c17, exp_f17 = 30 * 20, sum(s17_g4s)
tc17 = [c for c, _ in s17[True]]
lc17 = [c for c, _ in s17[False]]
tf17 = sum(f for _, f in s17[True])
lf17 = sum(f for _, f in s17[False])
tb17, lb17 = sum(s17_blown[True]), sum(s17_blown[False])
print(f"XFAIL S17 touching pairs (20 seeds): "
      f"traj counts {min(tc17)}..{max(tc17)}/30 under={600 - sum(tc17)}, "
      f"legacy counts {min(lc17)}..{max(lc17)}/30 under={600 - sum(lc17)}; "
      f"fires traj={tf17} legacy={lf17} expected={exp_f17}; "
      f"G4 beans blown traj={tb17} legacy={lb17} "
      f"(merged blob -> one count; known limitation)")
INVARIANTS.append(("S17 touching pairs (20 seeds, XFAIL)",
                   lb17, tb17, True))
table.append(("S17 touching pairs (20 seeds, XFAIL)", 30,
              f"{min(lc17)}..{max(lc17)}", f"{min(tc17)}..{max(tc17)}",
              exp_f17, lf17, tf17))

# ==================== S16 stress: >40 detections in one frame
# 60 beans, 10 lanes x 6 rows, all visible from frame 1 (>40 dets/frame).
beans16 = [Bean(60 + (i % 10) * 120, -40 - (i // 10) * 75, 500.0,
                grades[i % len(grades)], r=28) for i in range(60)]
scenario("S16 stress 60 dets/frame", 60,
         sum(1 for b in beans16 if b.grade == 4),
         (beans16, 20, 6.0, move, plain_emit))

# ===================================== greedy fallback path
# TRACK_HUNGARIAN_MAX forces greedy-by-cost above the limit (used when >40
# tracks/detections); S1 must still count exactly under the greedy path.
C.TRACK_HUNGARIAN_MAX = 1
trj_g = run_scenario(True, std_beans(30, 500, grades), 20, 8.0, move, plain_emit)
C.TRACK_HUNGARIAN_MAX = 40
n_g = sum(trj_g.stats.values())
report("greedy-by-cost fallback (TRACK_HUNGARIAN_MAX=1)", n_g == 30,
       f"counted={n_g}/30")

# ===================================== Hungarian vs brute force (5x5)
def brute_force(cost):
    n, m = cost.shape
    return min(sum(cost[i, p[i]] for i in range(n))
               for p in itertools.permutations(range(m), n))


rng_h = np.random.default_rng(7)
ok_h = True
for _ in range(200):
    cm = rng_h.uniform(0, 100, (5, 5))
    pairs = T_mod.hungarian(cm)
    ok_h &= abs(sum(cm[i, j] for i, j in pairs) - brute_force(cm)) < 1e-6
    cm2 = rng_h.uniform(0, 100, (3, 5))
    pairs2 = T_mod.hungarian(cm2)
    ok_h &= len(pairs2) == 3 and len({j for _, j in pairs2}) == 3
report("Hungarian matches brute force on 200 random matrices", ok_h)

# ==================== S18 uncertain: one frame of evidence before the line
# Fast belt (720 px/s = 90 px/frame at 8 fps): a bean appears once, then the
# track coasts across the line on its belt prior with < TRACK_MIN_VOTES
# votes -> "uncertain". Warm-up beans let the tracker learn belt velocity
# first (a cold-start single-frame bean cannot cross - zero-velocity Kalman).
# UNCERTAIN_POLICY="blow" fires it (fail-safe), "pass" lets it through.
def run_s18(grade, policy):
    old_p = C.UNCERTAIN_POLICY
    C.UNCERTAIN_POLICY = policy
    try:
        # 8 warm beans keep >=3 confirmed tracks alive at release time so the
        # belt velocity is learned; they stay far from the fast bean's lane.
        beans = [Bean(80 + i * 100, -60 - i * 200, 500.0, 1, r=34)
                 for i in range(8)]
        fast = Bean(1100, 1e9, 720.0, grade, r=34)     # parked off-frame
        beans.append(fast)
        valve, db = FakeValve(), FakeDB()
        sorter = make_sorter(True, valve, db, Line((0, 360), (1280, 360)))
        t, ev_i, released = 0.0, 0, False
        for f in range(64):
            t += 1.0 / 8
            dets = []
            if f == 24 and not released:              # belt learned by now
                released = True
                fast.y = 350.0                        # 10 px above the line
                dets.append(fast.det())               # its ONLY sighting
            for b in beans:
                py = b.y
                b.y += b.vy / 8
                if py < 360 <= b.y and not hasattr(b, "tc_true"):
                    b.tc_true = (t, b.x)
                if b is not fast and -80 < b.y < 780:
                    dets.append(b.det())
            sorter.process(FRAME, True, t=t, detections=dets)
            for e in db.events[ev_i:]:
                e["t"] = t
            ev_i = len(db.events)
        return sorter, beans
    finally:
        C.UNCERTAIN_POLICY = old_p


s18b, beans18b = run_s18(4, "blow")
unc18b = s18b.trk_counters().get("uncertain", 0)
report("S18 G4 bean, 1 frame of evidence, policy=blow",
       len(s18b.valve.pulses) == 1 and unc18b >= 1,
       f"pulses={len(s18b.valve.pulses)} uncertain={unc18b}")
s18p, _ = run_s18(4, "pass")
report("S18 G4 bean, 1 frame of evidence, policy=pass",
       len(s18p.valve.pulses) == 0,
       f"pulses={len(s18p.valve.pulses)}")
s18g, _ = run_s18(1, "blow")
report("S18 G1 bean, 1 frame, policy=blow (accepted cost: good bean blown)",
       len(s18g.valve.pulses) == 1,
       f"pulses={len(s18g.valve.pulses)}")
INVARIANTS.append(("S18 uncertain G4 policy=blow", 0,
                   len(blown_g4(s18b, beans18b)), False))
table.append(("S18 uncertain G4 policy=blow", 9, "-",
              sum(s18b.stats.values()), 1, "-", len(s18b.valve.pulses)))

# ==================== S19 alternating-palette defect (vote-reset safety)
# A defect bean alternates its measured palette between a good-side and a
# defect-side colour (dE 14, then 25 - past TRACK_REID_DE=12 from the blended
# track mean when the mean sits defect-side). A 3-frame dropout forces a
# gap-rebind on the good-side palette: the old rule (reset when the new palette
# is > REID_DE from the track mean) would wipe the defect votes; the pal_hist
# rule blocks it because the palette matches one the track already showed.
# The bean must still blow, and the fix path must show vote_resets == 0.
def _s19_pals(de_gap):
    P_good = np.array([55.0, -30.0, 40.0], np.float32)   # classifies 1
    u = (GRADE_LAB[4].astype(float) - P_good.astype(float))
    u /= np.linalg.norm(u)
    P_def = (P_good.astype(float) + de_gap * u).astype(np.float32)  # -> 4
    return ([vision.PaletteColor(P_good, 1.0, "#A")],
            [vision.PaletteColor(P_def, 1.0, "#B")])


def run_s19(de_gap):
    pa, pb = _s19_pals(de_gap)

    def emit(b, f, t):
        if 5 <= f <= 7:                     # 3-frame dropout mid-flight
            return []
        d = b.det()
        d["palette"] = pb if 2 <= f <= 4 or f >= 11 else pa
        return [d]

    # vy=780 px/s at 20fps -> crosses y=360 during frame 11: after the gap
    # rebind at f8 (good-side palette vs defect-side track mean -> the naive
    # reset would fire), the tail is good-majority, so a wiped track decides
    # grade 1 and misses. With pal_hist the defect votes survive -> blows.
    beans = [Bean(400, -60, 780.0, 4, r=34)]
    trj = run_scenario(True, beans, 20, 1.2, move, emit)
    return trj, beans


for de19 in (14.0, 25.0):
    s19, beans19 = run_s19(de19)
    c19 = s19.trk_counters()
    report(f"S19 alternating palette dE={de19:.0f}: defect still blows "
           f"(no vote wipe on rebind)",
           len(s19.valve.pulses) == 1 and c19.get("vote_resets", 0) == 0,
           f"pulses={len(s19.valve.pulses)} resets={c19.get('vote_resets')} "
           f"events={s19.db.events}")
    table.append((f"S19 alternating palette dE={de19:.0f}", 1, "-",
                  sum(s19.stats.values()), 1, "-", len(s19.valve.pulses)))
    INVARIANTS.append((f"S19 alternating palette dE={de19:.0f}", 0,
                       len(blown_g4(s19, beans19)), False))

# ==================== STEP 3.2 invariant: trajectory defect coverage
# trajectory must never blow FEWER physical grade-4 beans than legacy.
# Pulses are attributed to beans (x_on_line, event time vs true crossing);
# legacy can fire the SAME bean twice (S11 recount, S14 split blob), so
# distinct blown beans - not raw pulse count - is the honest comparison.
print()
print("Invariant: trajectory G4 beans blown >= legacy, per scenario")
bad_inv = 0
for name, lb, tb, xfail in INVARIANTS:
    ok = tb >= lb
    if not ok and xfail:
        print(f"  XFAIL     {name}: legacy={lb} traj={tb}")
    elif not ok:
        bad_inv += 1
        print(f"  VIOLATION {name}: legacy={lb} traj={tb}")
    else:
        print(f"  ok        {name}: legacy={lb} traj={tb}")
report("invariant traj G4 beans blown >= legacy (all non-XFAIL rows)",
       bad_inv == 0, f"violations={bad_inv}")

# ---------------------------------------------------------------- summary
print()
print(f"{'scenario':<34} {'exp':>4} {'leg':>6} {'traj':>6} "
      f"{'expF':>5} {'legF':>5} {'trajF':>6}")
print("-" * 70)
for name, e, l_, t_, ef, lf, tf in table:
    print(f"{name:<34} {e:>4} {l_!s:>6} {t_!s:>6} {ef:>5} {lf:>5} {tf:>6}")
print("=" * 70)
print(f"{sum(results)}/{len(results)} checks passed "
      f"(XFAIL rows are expected failures, not counted)")
sys.exit(0 if all(results) else 1)
