#!/usr/bin/env python3
"""A3 ground-truth matrix (STEP 2): PAIRED legacy vs trajectory runs.

Every run uses BEAN_SIM_SCHEDULE=1: the simulated camera spawns beans from a
fixed schedule on simulated time (frame index x SIM_DT), so both trackers see
the IDENTICAL bean sequence for the same seed regardless of loop fps. Each run
drains: spawns stop after BEAN_SIM_SECS sim-seconds and the run ends when the
last bean has left the frame, so counted vs truth has no end-of-run off-by-one.

Conditions (drop10 = BEAN_DROP=0.10 detection dropout):
  drop10  25 px/frame  (sim_dt 0.05)
  medium  45 px/frame  (sim_dt 0.125 = 8 fps) + drop10
  harsh   90 px/frame  (sim_dt 0.125 = 8 fps) + drop10 - beyond the legacy gate
5 seeds x 3 conditions x {trajectory, legacy} = 30 runs.

Per-bean attribution (SimulatedValve.fires): every pulse is tagged with the
physical bean nearest the line at fire time -> defects_total/defects_blown/
defects_missed/good_total/good_blown(false blows)/uncertain_blows.

  python scripts/a3_matrix.py            # 30 runs (~60 sim-s each)
"""
import os
import re
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "var"
OUT.mkdir(exist_ok=True)

SIM_SECS = "60"           # spawn window in SIM seconds (drain follows)
SEEDS = [11, 12, 13, 14, 15]
CONDS = [
    ("drop10", {"BEAN_SIM_DT": "0.05",  "BEAN_SIM_SPEED": "500",
                "BEAN_DROP": "0.10"}),                       # 25 px/frame
    ("medium", {"BEAN_SIM_DT": "0.125", "BEAN_SIM_SPEED": "360",
                "BEAN_DROP": "0.10"}),                       # 45 px/frame @8fps
    ("harsh",  {"BEAN_SIM_DT": "0.125", "BEAN_SIM_SPEED": "720",
                "BEAN_DROP": "0.10"}),                       # 90 px/frame @8fps
]
TRACKERS = [("traj", ""), ("legacy", "1")]

# wall-clock ceilings: frames = SIM_SECS/SIM_DT + drain; ~20 fps real
CAPS = {"drop10": 160, "medium": 80, "harsh": 80}

rows = []
for cond, cenv in CONDS:
    for tracker, legacy in TRACKERS:
        for seed in SEEDS:
            tag = f"{cond}_{tracker}_s{seed}"
            logfile = OUT / f"a3_{tag}.txt"
            print(f"=== {tag} ===", flush=True)
            env = dict(os.environ, BEAN_SIM_SCHEDULE="1", BEAN_SIM_SECS=SIM_SECS,
                       BEAN_LEGACY=legacy, BEAN_SIM_SEED=str(seed),
                       BEAN_RECORD=str(OUT / f"a3_{tag}.jsonl"))
            env.update(cenv)
            rec = OUT / f"a3_{tag}.jsonl"
            if rec.exists():
                rec.unlink()
            cmd = [sys.executable, str(ROOT / "scripts" / "simrun.py"),
                   str(CAPS[cond]), f"--dump={OUT / f'a3_{tag}_status.json'}"]
            p = subprocess.run(cmd, cwd=ROOT, env=env,
                               capture_output=True, text=True)
            logfile.write_text(p.stdout + "\n" + p.stderr)
            m = re.search(r"ATTR defects=(\d+) blown=(\d+) missed=(\d+) "
                          r"good=(\d+) good_blown=(\d+) unc_blows=(\d+) "
                          r"orphan=(\d+)", p.stdout)
            if not m:
                print("    no ATTR line - run failed?")
                print("    ", "\n    ".join(p.stdout.splitlines()[-6:]))
                rows.append((cond, tracker, seed) + ("?",) * 7 + (False,))
                continue
            d_tot, d_blown, d_missed, g_tot, g_blown, unc, orph = \
                (int(v) for v in m.groups())
            print(f"    defects {d_blown}/{d_tot} blown, missed={d_missed}, "
                  f"false_blows={g_blown}(+{orph} orphan), unc={unc}")
            rows.append((cond, tracker, seed, d_tot, d_blown, d_missed,
                         g_tot, g_blown, unc, orph, True))

print()
print(f"{'condition':<8}{'tracker':<8}{'seed':>5}{'defects':>9}{'d_blown':>9}"
      f"{'d_miss':>8}{'good':>7}{'f_blows':>9}{'unc':>5}{'orph':>6}")
for r in rows:
    cond, tracker, seed, d_tot, d_blown, d_missed, g_tot, g_blown, unc, orph, \
        ok = r
    print(f"{cond:<8}{tracker:<8}{seed:>5}{d_tot!s:>9}{d_blown!s:>9}"
          f"{d_missed!s:>8}{g_tot!s:>7}{g_blown!s:>9}{unc!s:>5}{orph!s:>6}"
          + ("" if ok else "  RUN-FAILED"))

print("\n--- min/mean/max of defects_missed and false_blows (5 seeds) ---")
print(f"{'condition':<8}{'tracker':<8}"
      f"{'d_missed min/mean/max':>24}{'false_blows min/mean/max':>27}")
for cond, _ in CONDS:
    for tracker, _ in TRACKERS:
        sel = [r for r in rows if r[0] == cond and r[1] == tracker and r[-1]]
        if not sel:
            continue
        dm = [r[5] for r in sel]
        fb = [r[7] for r in sel]
        print(f"{cond:<8}{tracker:<8}"
              f"{min(dm):>8}{statistics.mean(dm):>6.1f}{max(dm):>6}"
              f"{min(fb):>11}{statistics.mean(fb):>6.1f}{max(fb):>6}")
