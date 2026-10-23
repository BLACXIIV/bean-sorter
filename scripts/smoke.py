#!/usr/bin/env python3
"""Clean-boot smoke test:  python scripts/smoke.py

Starts `engine.py --simulate` on a free port, waits for /api/health (<=10 s),
GETs /api/status, performs one scripted calibration click, and - when the
php CLI exists - also serves web/ and GETs operate.php (expects HTTP 200).
Prints PASS / FAIL / SKIPPED (with reason); exit non-zero on failure.
"""
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ok_all = True


def report(name, ok, detail=""):
    global ok_all
    ok_all &= ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def post(base, path, obj):
    req = urllib.request.Request(base + path, method="POST",
                                 data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def main():
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "engine.py"), "--simulate", "--port", str(port)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 10
        health = None
        while time.time() < deadline:
            try:
                health = json.loads(urllib.request.urlopen(
                    base + "/api/health", timeout=1).read())
                break
            except Exception:
                if proc.poll() is not None:
                    report("engine boot", False,
                           f"exited early (code {proc.returncode})")
                    return 1
                time.sleep(0.25)
        report("/api/health OK within 10 s",
               bool(health and health.get("ok")), f"fps={health and health.get('fps')}")
        status = json.loads(urllib.request.urlopen(base + "/api/status",
                                                   timeout=2).read())
        report("/api/status fields", "mode" in status and "counts" in status,
               f"mode={status.get('mode')}")

        # one scripted calibration click
        post(base, "/api/mode", {"mode": "calibrate"})
        post(base, "/api/calibrate/select", {"target": "belt"})
        r = post(base, "/api/calibrate/click", {"nx": 0.5, "ny": 0.08})
        report("scripted calibration click", r.get("ok") is True,
               str(r)[:80])

        # PHP app (skipped when the php CLI is absent)
        php = shutil.which("php")
        if php is None:
            print("SKIPPED  operate.php 200  (php CLI not installed)")
        else:
            wport = free_port()
            wproc = subprocess.Popen(
                [php, "-S", f"127.0.0.1:{wport}", "-t", str(ROOT / "web")],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                code = None
                deadline = time.time() + 10
                while time.time() < deadline:
                    try:
                        code = urllib.request.urlopen(
                            f"http://127.0.0.1:{wport}/operate.php",
                            timeout=2).status
                        break
                    except Exception:
                        time.sleep(0.25)
                report("operate.php returns 200", code == 200, f"code={code}")
            finally:
                wproc.terminate()
        return 0 if ok_all else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
