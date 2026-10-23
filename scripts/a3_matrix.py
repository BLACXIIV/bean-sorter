#!/usr/bin/env python3
"""A3 ground-truth matrix: legacy vs trajectory, clean / 10% dropout /
10% dropout at 8fps cap, 120 s each (wall time).

Runs simrun.py once per condition with the right env vars, tees each run's
output to var/a3_<cond>_<tracker>.txt, then prints the comparison table.

  python scripts/a3_matrix.py
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "var"
OUT.mkdir(exist_ok=True)

CONDS = [
    ("clean",        {"BEAN_DROP": ""}),
    ("drop10",       {"BEAN_DROP": "0.10"}),
    ("drop10_fps8",  {"BEAN_DROP": "0.10", "FPS_CAP": "8"}),
]
TRACKERS = [("traj", ""), ("legacy", "1")]

rows = []
for cond, cenv in CONDS:
    for tracker, legacy in TRACKERS:
        tag = f"{cond}_{tracker}"
        logfile = OUT / f"a3_{tag}.txt"
        print(f"=== {tag} ===", flush=True)
        env = dict(os.environ, **cenv)
        env["BEAN_LEGACY"] = legacy
        cmd = [sys.executable, str(ROOT / "scripts" / "simrun.py"), "120"]
        cap = cenv.get("FPS_CAP")
        if cap:
            cmd.append(f"--fps-cap={cap}")
        p = subprocess.run(cmd, cwd=ROOT, env=env,
                           capture_output=True, text=True)
        logfile.write_text(p.stdout + "\n" + p.stderr)
        tail = [l for l in p.stdout.splitlines()
                if "unique_crossed" in l or "GROUND TRUTH" in l or "FINAL" in l]
        for l in tail:
            print("   ", l)
        m = re.search(r"unique_crossed=(\d+) vs crossed=(\d+) \| "
                      r"blown=(\d+) vs defects=(\d+)", p.stdout)
        sp = re.search(r'"suppressed": (\d+)', p.stdout)
        es = re.search(r'"estimated": (\d+)', p.stdout)
        rows.append((cond, tracker,
                     m.group(2) if m else "?", m.group(1) if m else "?",
                     m.group(4) if m else "?", m.group(3) if m else "?",
                     sp.group(1) if sp else "-", es.group(1) if es else "-",
                     p.returncode == 0))

print()
print(f"{'condition':<14}{'tracker':<9}{'truth':>7}{'counted':>9}"
      f"{'defects':>9}{'blown':>7}{'suppr':>7}{'est':>5}  result")
for cond, tracker, truth, counted, defects, blown, sp, es, ok in rows:
    print(f"{cond:<14}{tracker:<9}{truth:>7}{counted:>9}{defects:>9}"
          f"{blown:>7}{sp:>7}{es:>5}  {'MATCH' if ok else 'MISMATCH'}")
sys.exit(0 if all(r[-1] for r in rows) else 1)
