"""Calibration flow over HTTP against engine --simulate on a free port.

Covers: error codes on bad taps (WRONG_MODE, BAD_REQUEST, PATCH_NOT_UNIFORM;
other AGENTS 6.4 codes are wired but not producible in the sim -> SKIPPED),
belt taps saved, background capture, one tap per grade's beans, samples
persisted in sqlite, then a short run asserting counts and blown == g4.

  python tests/test_calibration_flow.py
"""
import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
results = []


def report(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def skipped(name, reason):
    print(f"SKIPPED  {name}  ({reason})")


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
    try:
        return json.loads(urllib.request.urlopen(req, timeout=8).read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def get(base, path):
    return json.loads(urllib.request.urlopen(base + path, timeout=8).read())


def main():
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db_path = Path(td) / "calib.db"
        import os
        env = dict(os.environ, BEAN_DB=str(db_path))
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "engine.py"), "--simulate",
             "--port", str(port)],
            cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 10
            while time.time() < deadline:
                try:
                    if get(base, "/api/health").get("ok"):
                        break
                except Exception:
                    time.sleep(0.25)
            else:
                report("engine starts", False, "health never came up")
                return 1
            report("engine starts", True)

            # ---- error codes on bad taps
            r = post(base, "/api/calibrate/click", {"nx": 0.5, "ny": 0.5})
            report("tap in idle -> code WRONG_MODE",
                   not r.get("ok") and r.get("code") == "WRONG_MODE",
                   f"code={r.get('code')}")
            assert post(base, "/api/mode", {"mode": "calibrate"}).get("ok")

            r = post(base, "/api/calibrate/click", {"nx": "x"})
            report("malformed tap -> code BAD_REQUEST",
                   not r.get("ok") and r.get("code") == "BAD_REQUEST",
                   f"code={r.get('code')}")

            for code, why in (
                    ("NO_BEAN_AT_POINT", "patch fallback always succeeds in sim"),
                    ("NO_SEGMENTATION_SOURCE", "patch fallback succeeds"),
                    ("PATCH_OK?TOO_FEW_PIXELS", "sim beans always have pixels"),
                    ("BELT_LIMIT", "needs BELT_MAX_COLORS distinct belt spots"),
                    ("CAMERA_ERROR", "sim camera never fails")):
                skipped(f"error code {code}", why)

            # ---- real calibration flow
            post(base, "/api/calibrate/select", {"target": "belt"})
            n_saved = 0
            for nx, ny in ((0.2, 0.1), (0.8, 0.1), (0.5, 0.85), (0.2, 0.95)):
                r = post(base, "/api/calibrate/click", {"nx": nx, "ny": ny})
                n_saved += 1 if r.get("ok") else 0
            st = get(base, "/api/status")
            # the sim belt is uniform: extra taps are dedup'd "already covered"
            report("4 belt taps accepted (sim dedups to 1 colour)",
                   n_saved == 4 and st["belt_count"] >= 1,
                   f"accepted={n_saved}/4 belt_count={st['belt_count']}")

            r = post(base, "/api/calibrate/background", {})
            report("background captured", r.get("ok") is True)
            time.sleep(0.3)

            # belt target + tap straddling a bean edge in the static grid:
            # half dark bean / half blue belt = non-uniform patch
            post(base, "/api/calibrate/select", {"target": "belt"})
            r = post(base, "/api/calibrate/click",
                     {"nx": 200 / 1280, "ny": 90 / 720})
            report("belt tap on bean edge -> code PATCH_NOT_UNIFORM",
                   not r.get("ok") and r.get("code") == "PATCH_NOT_UNIFORM",
                   f"code={r.get('code')} err={r.get('error', '')[:60]}")

            for grade, gx in ((1, 160), (2, 480), (3, 800), (4, 1120)):
                post(base, "/api/calibrate/select", {"target": grade})
                ok_any = False
                for gy in (90, 270, 450):
                    r = post(base, "/api/calibrate/click",
                             {"nx": gx / 1280, "ny": gy / 720})
                    ok_any |= bool(r.get("ok"))
                report(f"grade {grade} bean tap saved", ok_any,
                       f"hex={r.get('hex')}")

            con = sqlite3.connect(str(db_path))
            cnt = dict(con.execute(
                "SELECT grade, COUNT(*) FROM samples GROUP BY grade").fetchall())
            con.close()
            report("samples persisted per grade in sqlite",
                   all(cnt.get(g, 0) >= 1 for g in (1, 2, 3, 4)),
                   f"samples={cnt}")

            post(base, "/api/line", {"p1": [0, 0.5], "p2": [1, 0.5]})
            post(base, "/api/arm", {"armed": True})
            r = post(base, "/api/mode", {"mode": "run", "batch_name": "t"})
            report("run mode accepted after calibration", r.get("ok") is True,
                   f"{r.get('error', '')}")
            time.sleep(15)
            st = get(base, "/api/status")
            c = st["counts"]
            report("run counts beans",
                   sum(c[g] for g in ("g1", "g2", "g3", "g4")) > 0,
                   f"counts={c}")
            report("blown == grade-4 counted (defects only)",
                   c["blown"] == c["g4"] and c["blown"] > 0,
                   f"blown={c['blown']} g4={c['g4']}")
            return 0 if all(results) else 1
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    rc = main()
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(rc if rc is not None else 1)
