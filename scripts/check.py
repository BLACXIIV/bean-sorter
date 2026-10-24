#!/usr/bin/env python3
"""Static checks + tests. Cross-platform:  python scripts/check.py [--strict]

- py_compile every project .py
- php -l every project PHP file (SKIPPED with a note when php is absent)
- run every tests/test_*.py (non-zero exit = fail)
- SKIPPED lines inside test output are collected and printed at the end;
  --strict makes any SKIPPED check a non-zero exit.
"""
import py_compile
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PASS, FAIL = "PASS", "FAIL"
STRICT = "--strict" in sys.argv
results = []
skipped = []          # (item, reason)


def report(name, ok, detail=""):
    results.append((name, ok))
    print(f"{PASS if ok else FAIL}  {name}  {detail}")


# ---- py_compile ------------------------------------------------------------
py_files = [p for p in ROOT.rglob("*.py")
            if not any(part.startswith(".venv") for part in p.parts)
            and "__pycache__" not in p.parts]
bad = []
for p in py_files:
    try:
        py_compile.compile(str(p), doraise=True)
    except py_compile.PyCompileError as e:
        bad.append(f"{p.relative_to(ROOT)}: {e}")
report(f"py_compile ({len(py_files)} files)", not bad, "; ".join(bad[:3]))

# ---- php -l ------------------------------------------------------------------
php = shutil.which("php")
php_files = [p for p in (ROOT / "web").rglob("*.php") if "vendor" not in p.parts]
if php is None:
    print(f"SKIPPED  php -l ({len(php_files)} files)  (php not installed)")
    skipped.append(("php -l", "php not installed"))
else:
    bad = []
    for p in php_files:
        r = subprocess.run([php, "-l", str(p)], capture_output=True, text=True)
        if r.returncode != 0:
            bad.append(f"{p.relative_to(ROOT)}: {r.stdout.strip()[:120]}")
    report(f"php -l ({len(php_files)} files, vendor skipped)", not bad,
           "; ".join(bad[:3]))

# ---- tests -------------------------------------------------------------------
SKIP_RE = re.compile(r"^\s*SKIPPED\s+(.+?)(?:\s{2,}|\s*\()(.*)$", re.M)
for p in sorted((ROOT / "tests").glob("test_*.py")):
    r = subprocess.run([sys.executable, str(p)], capture_output=True, text=True,
                       cwd=ROOT)
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    report(p.name, r.returncode == 0,
           tail if r.returncode == 0 else (r.stderr.strip()[-200:] or tail))
    for m in SKIP_RE.finditer(r.stdout):
        skipped.append((f"{p.name}: {m.group(1).strip()}",
                        m.group(2).strip().strip("()") or "-"))

print("=" * 50)
if skipped:
    print(f"SKIPPED  {len(skipped)} check(s):")
    for item, reason in skipped:
        print(f"         - {item}  ({reason})")
ok = all(r[1] for r in results) and not (STRICT and skipped)
print(f"{sum(1 for r in results if r[1])}/{len(results)} checks passed"
      + (f", {len(skipped)} skipped" if skipped else "")
      + ("  [strict: SKIPPED counts as failure]" if STRICT and skipped else ""))
sys.exit(0 if ok else 1)
