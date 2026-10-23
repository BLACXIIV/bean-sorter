#!/usr/bin/env python3
"""Static checks + tests. Cross-platform:  python scripts/check.py

- py_compile every project .py
- php -l every project PHP file (skipped with a note when php is absent)
- run every tests/test_*.py (non-zero exit = fail)
"""
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PASS, FAIL = "PASS", "FAIL"
results = []


def report(name, ok, detail=""):
    results.append((name, ok))
    print(f"{PASS if ok else FAIL}  {name}  {detail}")


# ---- py_compile ------------------------------------------------------------
py_files = [p for p in ROOT.rglob("*.py")
            if ".venv" not in p.parts and "__pycache__" not in p.parts]
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
    print(f"NOTE  php not installed - skipping php -l on {len(php_files)} files")
else:
    bad = []
    for p in php_files:
        r = subprocess.run([php, "-l", str(p)], capture_output=True, text=True)
        if r.returncode != 0:
            bad.append(f"{p.relative_to(ROOT)}: {r.stdout.strip()[:120]}")
    report(f"php -l ({len(php_files)} files, vendor skipped)", not bad,
           "; ".join(bad[:3]))

# ---- tests -------------------------------------------------------------------
for p in sorted((ROOT / "tests").glob("test_*.py")):
    r = subprocess.run([sys.executable, str(p)], capture_output=True, text=True,
                       cwd=ROOT)
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    report(p.name, r.returncode == 0,
           tail if r.returncode == 0 else (r.stderr.strip()[-200:] or tail))

print("=" * 50)
ok = all(r[1] for r in results)
print(f"{sum(1 for r in results if r[1])}/{len(results)} checks passed")
sys.exit(0 if ok else 1)
