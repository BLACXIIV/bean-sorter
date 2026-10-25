"""UI click->coordinate mapping, Playwright at viewport 1024x600.

Taps the corners and centre of the camera <img> in operate.php at browser
zoom 100%, 90% and 110%; the nx,ny posted to /api/calibrate/click must equal
the tapped position within 0.01. Also asserts operate.php has no page scroll
at 1024x600.

Requires: PHP CLI (php -S) and playwright (pip install playwright;
playwright install chromium). Prints SKIPPED with the reason when either is
missing - never a silent pass.

  python tests/test_ui_click_mapping.py
"""
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


# a stub engine: records /api/calibrate/click posts, serves canned status
class Stub(BaseHTTPRequestHandler):
    clicks = []

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._json({})

    def do_GET(self):
        if self.path.startswith("/api/status"):
            self._json({"ok": True, "mode": "calibrate", "armed": False,
                        "fps": 20, "counts": {"g1": 0, "g2": 0, "g3": 0,
                                              "g4": 0, "blown": 0},
                        "camera_source": "sim", "valve": "sim",
                        "segmentation": "belt_color", "warnings": [],
                        "selected_target": 1, "line": None,
                        "has_background": True, "belt_count": 2,
                        "show_detected": False, "show_tracks": False,
                        "session_id": None, "message": ""})
        elif self.path.startswith("/stream.mjpg"):
            # a single tiny JPEG then hang briefly is enough for the <img>
            jpg = Path(ROOT / "tests" / "_pixel.jpg")
            body = jpg.read_bytes() if jpg.exists() else b""
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        b = json.loads(self.rfile.read(n) or b"{}")
        if self.path.startswith("/api/calibrate/click"):
            Stub.clicks.append(b)
        self._json({"ok": True})


def main():
    php = shutil.which("php")
    if php is None:
        skipped("ui click mapping", "php CLI not installed")
        print("0/0 checks ran (all skipped)")
        return 0
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        skipped("ui click mapping",
                "playwright not installed "
                "(pip install playwright; playwright install chromium)")
        print("0/0 checks ran (all skipped)")
        return 0

    # write a 1x1 jpeg for the stub stream (generated - the baked base64
    # literal was missing characters and failed to decode)
    import cv2
    import numpy as np
    Path(ROOT / "tests" / "_pixel.jpg").write_bytes(
        cv2.imencode(".jpg", np.zeros((1, 1, 3), np.uint8))[1].tobytes())

    eng_port, web_port = free_port(), free_port()
    stub = ThreadingHTTPServer(("127.0.0.1", eng_port), Stub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    web = subprocess.Popen(
        [php, "-S", f"127.0.0.1:{web_port}", "-t", str(ROOT / "web")],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = f"http://127.0.0.1:{web_port}/operate.php?engine=127.0.0.1:{eng_port}"
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1024, "height": 600})
            page.goto(url, wait_until="networkidle")
            page.wait_for_timeout(500)

            box = page.locator("#cam").bounding_box()
            assert box, "camera <img> not found"

            scroll = page.evaluate(
                "({h: document.documentElement.scrollHeight,"
                "  w: document.documentElement.scrollWidth})")
            report("operate.php no page scroll at 1024x600",
                   scroll["h"] <= 600 and scroll["w"] <= 1024,
                   f"scrollHeight={scroll['h']} scrollWidth={scroll['w']}")

            pts = [(0.02, 0.02), (0.98, 0.02), (0.02, 0.98),
                   (0.98, 0.98), (0.5, 0.5)]
            for zoom in (1.0, 0.9, 1.1):
                page.evaluate(f"document.body.style.zoom = '{zoom}'")
                page.wait_for_timeout(200)
                box = page.locator("#cam").bounding_box()
                Stub.clicks.clear()
                worst = 0.0
                all_ok = True
                for fx, fy in pts:
                    page.mouse.click(box["x"] + box["width"] * fx,
                                     box["y"] + box["height"] * fy)
                    page.wait_for_timeout(250)
                    if not Stub.clicks:
                        all_ok = False
                        continue
                    c = Stub.clicks.pop()
                    err = max(abs(c["nx"] - fx), abs(c["ny"] - fy))
                    worst = max(worst, err)
                    all_ok &= err <= 0.01
                report(f"click mapping zoom {int(zoom * 100)}%",
                       all_ok and len(pts) == 5,
                       f"worst offset {worst:.4f} over {len(pts)} taps")
            browser.close()
        return 0 if all(results) else 1
    finally:
        web.terminate()
        stub.shutdown()


if __name__ == "__main__":
    sys.exit(main())
