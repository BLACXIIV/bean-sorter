#!/usr/bin/env python3
"""Scripted engine verification: python scripts/simrun.py [seconds]

Boots engine --simulate on a free port, calibrates it through the HTTP API
(belt colours, a few taps per grade on the sim's static grid, sorting line),
starts a run, polls /api/status and prints the tracking counters."""
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FW, FH = 1280, 720


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


def get(base, path):
    return json.loads(urllib.request.urlopen(base + path, timeout=5).read())


def main(seconds=60, fps_cap=None, dump=None):
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    cmd = [sys.executable, str(ROOT / "engine.py"), "--simulate",
           "--port", str(port)]
    if fps_cap:
        cmd += ["--fps-cap", str(fps_cap)]
    proc = subprocess.Popen(cmd, cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # wait for health
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                if get(base, "/api/health").get("ok"):
                    break
            except Exception:
                time.sleep(0.25)
        else:
            print("FAIL engine did not come up")
            return 1

        print(post(base, "/api/mode", {"mode": "calibrate"}))
        # belt colours: plain belt + one seam
        post(base, "/api/calibrate/select", {"target": "belt"})
        for nx, ny in ((0.5, 0.08), (0.5, 0.5), (0.2, 0.9), (0.8, 0.9)):
            print("belt tap", post(base, "/api/calibrate/click", {"nx": nx, "ny": ny}))
        # background -> sim switches to "static" grid scene
        print(post(base, "/api/calibrate/background", {}))
        time.sleep(0.3)
        # tap each grade: sim grid = columns GRID_X grades 1..4, rows GRID_Y
        for grade, gx in ((1, 160), (2, 480), (3, 800), (4, 1120)):
            post(base, "/api/calibrate/select", {"target": grade})
            for gy in (90, 450):
                r = post(base, "/api/calibrate/click",
                         {"nx": gx / FW, "ny": gy / FH})
                print(f"grade {grade} tap", r.get("ok"), r.get("hex", r.get("error")))
        # sorting line across the middle
        print("line", post(base, "/api/line",
                             {"p1": [0.0, 0.5], "p2": [1.0, 0.5]}))
        # enable trails overlay too
        print("tracks", post(base, "/api/run/show-tracks", {"on": True}))
        print("run", post(base, "/api/mode", {"mode": "run", "batch_name": "sim-60s"}))

        t_end = time.time() + seconds
        drained = False
        while time.time() < t_end and not drained:
            time.sleep(10)
            st = get(base, "/api/status")
            gt = st.get("ground_truth") or {}
            # schedule mode stops spawning at BEAN_SIM_SECS of sim time; the
            # run ends when every bean has physically left the frame (drain),
            # so counted vs truth has no end-of-run off-by-one
            drained = bool(gt) and gt.get("spawned", 0) > 0 \
                and gt.get("active", 1) == 0
            print(f"  t+{int(max(0, t_end - time.time()))}s-left "
                  f"fps={st['fps']} counts={st['counts']} "
                  f"tracking={st.get('tracking')} gt={gt or '-'}")
        print(f"  drain done, active={st.get('ground_truth', {}).get('active')}")
        tr = st.get("tracking") or {}
        print("FINAL", json.dumps(tr), st["counts"])
        if dump:
            Path(dump).parent.mkdir(parents=True, exist_ok=True)
            Path(dump).write_text(json.dumps(st))
        gt = st.get("ground_truth")
        if gt:
            # per-bean fire attribution: every pulse is tagged with the
            # physical bean nearest the line at fire time
            beans = gt.get("beans") or []
            fires = gt.get("fires") or []
            defects = [b for b in beans if b["grade"] == 4 and b["crossed"]]
            goods = [b for b in beans if b["grade"] != 4 and b["crossed"]]
            hit_ids = {f.get("bean_id") for f in fires if f.get("bean_id")}
            d_blown = sum(1 for b in defects if b["id"] in hit_ids)
            g_blown = sum(1 for b in goods if b["id"] in hit_ids)
            unc = sum(1 for f in fires if f.get("uncertain"))
            orphan = sum(1 for f in fires if not f.get("bean_id"))
            print("ATTR "
                  f"defects={len(defects)} blown={d_blown} "
                  f"missed={len(defects) - d_blown} "
                  f"good={len(goods)} good_blown={g_blown} "
                  f"unc_blows={unc} orphan={orphan}")
            missed = [b["id"] for b in defects if b["id"] not in hit_ids]
            if missed:
                print("  missed defect beans:", missed)
            print("GROUND TRUTH", json.dumps(
                {k: v for k, v in gt.items() if k not in ("beans", "fires")}))
            ok = (tr.get("unique_crossed") == gt["crossed"]
                  and st["counts"]["blown"] == gt["defects"])
            print(f"  unique_crossed={tr.get('unique_crossed')} vs "
                  f"crossed={gt['crossed']} | blown={st['counts']['blown']} vs "
                  f"defects={gt['defects']} -> {'MATCH' if ok else 'MISMATCH'}")
            return 0 if ok else 1
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    _s, _cap, _dump = 60, None, None
    for a in sys.argv[1:]:
        if a.startswith("--fps-cap="):
            _cap = float(a.split("=", 1)[1])
        elif a.startswith("--dump="):
            _dump = a.split("=", 1)[1]
        else:
            _s = int(a)
    sys.exit(main(_s, _cap, _dump))
