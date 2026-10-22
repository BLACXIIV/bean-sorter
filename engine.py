#!/usr/bin/env python3
"""Web engine: the ONLY process that owns the camera and the valve.

Flask JSON API + MJPEG stream for the PHP front end. Binds 127.0.0.1 only.

  python3 engine.py [--simulate] [--port 5000]

Modes: idle | calibrate | run. The valve is OFF on start, on signals and on
exit, and the engine starts DISARMED until a run is started.
"""
import argparse
import logging
import signal
import sys
import threading
import time
from collections import deque

logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("engine")

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request

import config as C
import ui
import vision
from db import DB
from hardware import SimulatedValve, make_camera, make_valve
from sorter import Line, Sorter, calibrate_click

FRAME_W, FRAME_H = C.FRAME_SIZE
STREAM_W, STREAM_H = 640, 360
JPEG_QUALITY = 70
STREAM_FPS = 15
ALLOWED_ORIGINS = {"http://127.0.0.1:8080", "http://localhost:8080"}

# settings keys the web UI may tune (validated server-side by PHP)
TUNABLES = ("DEFECT_BIAS", "UNKNOWN_DE", "VALVE_DELAY_S", "VALVE_PULSE_S",
            "DIFF_THRESHOLD", "MIN_BEAN_AREA", "MAX_BEAN_AREA", "DEFECT_VOTE_RATIO",
            "BELT_TOLERANCE", "BELT_L_WEIGHT")


class Engine:
    def __init__(self, camera_source="pi", valve_source="real"):
        self.db = DB()
        self.camera_source = camera_source
        self.valve_source = valve_source
        self.sim = camera_source == "sim"
        self.cam = make_camera(camera_source,
                               saved_settings=self.db.get_json("camera"))
        if self.cam.settings:
            self.db.set_json("camera", self.cam.settings)   # same as calibrate.py
        if valve_source == "real":
            try:
                import gpiozero  # noqa: F401
            except ImportError:
                self.cam.close()
                raise SystemExit(
                    "[valve] gpiozero not installed - no real valve possible; "
                    "rerun with --valve sim for testing")
            self.valve = make_valve()
        else:
            self.valve = SimulatedValve()
        self.apply_tunables()

        self.mode = "idle"          # idle | calibrate | run
        self.armed = False
        self.selected_target = 1    # "belt" | 1 | 2 | 3 | 4
        self.show_detected = False
        self.message = "Engine started"
        self.session_id = None
        self.sorter = None
        self.bg = self.db.load_background()
        self.belt = self._load_belt()
        self.clf = vision.Classifier(self.db.load_model())
        self.line = self._load_line()
        self.undo_stack = deque(maxlen=20)   # ("sample"|"belt", row id)

        self.fps = 0.0
        self.jpeg = None
        self.stream_clients = 0
        self.cond = threading.Condition()
        self.lock = threading.Lock()
        self._stop = False
        self._t0 = time.perf_counter()
        # stage timings: ms totals + frame counts, reset each log window
        self.tim = {"cam": 0.0, "jpeg": 0.0, "find": 0.0, "cls": 0.0, "draw": 0.0}
        self.tim_n = 0

    # ---------------------------------------------------------------- helpers
    def _load_line(self):
        pts = self.db.get_json("line")
        if pts and len(pts) == 2:
            return Line(tuple(pts[0]), tuple(pts[1]))
        return None

    def _load_belt(self):
        colors = self.db.load_belt_colors()
        return vision.BeltModel(colors) if colors is not None else None

    def segmentation(self):
        if self.belt is not None:
            return "belt_color"
        return "background" if self.bg is not None else "none"

    def _warnings(self):
        return vision.belt_warnings(self.db.load_belt_colors(),
                                    self.clf.model, C.BELT_TOLERANCE,
                                    C.BELT_L_WEIGHT)

    def apply_tunables(self):
        saved = self.db.get_json("tunables") or {}
        for k in TUNABLES:
            if k in saved:
                setattr(C, k, saved[k])

    def stop(self):
        self._stop = True

    def close(self):
        # valve OFF first, always
        try:
            self.valve.close()
        finally:
            self.cam.close()

    # ------------------------------------------------------------- main loop
    def loop(self):
        t_prev, last_enc = time.perf_counter(), 0.0
        last_log = t_prev
        while not self._stop:
            t_read0 = time.perf_counter()
            frame = self.cam.read()
            now = time.perf_counter()
            self.tim["cam"] += (now - t_read0) * 1000
            self.tim_n += 1
            if self.mode == "run" and self.sorter is not None:
                self.sorter.process(frame, self.armed, self.tim)
            elif self.mode == "calibrate":
                if self.show_detected:
                    beans = vision.find_beans(frame, self.bg, self.belt) \
                        if (self.belt is not None or self.bg is not None) else []
                    if self.belt is not None:
                        mask = self.belt.bean_mask(frame)
                        frame[mask == 0] = \
                            (frame[mask == 0] * 0.4).astype(np.uint8)
                    # every blob gets a predicted-grade tint + label
                    for b in beans:
                        grade = None
                        if self.clf.model:
                            pal = vision.extract_palette(
                                vision.bean_pixels(frame, b.contour,
                                                   self.belt))
                            if pal:
                                grade = self.clf.classify(pal)[0]
                        col = vision.GRADE_BGR.get(grade, (160, 160, 160))
                        cv2.drawContours(frame, [b.contour], -1, col, 2)
                        ui.put_text(
                            frame, "?" if grade is None else f"G{grade}",
                            (int(b.centroid[0]) - 12, int(b.centroid[1]) + 5),
                            col, 0.6, 2)
                label = ("BELT" if self.selected_target == "belt"
                         else f"grade {self.selected_target}")
                ui.draw_panel(frame, [f"CALIBRATE - {label}", self.message])
            else:
                if self.line is not None:
                    cv2.line(frame, self.line.p1, self.line.p2, (0, 255, 255), 2)
                ui.draw_panel(frame, ["IDLE", self.message])
            self.fps = 0.9 * self.fps + 0.1 / max(1e-6, now - t_prev)
            t_prev = now

            # encode only while a client is watching, max 15 fps
            if self.stream_clients and now - last_enc >= 1.0 / STREAM_FPS:
                t_enc0 = time.perf_counter()
                small = cv2.resize(frame, (STREAM_W, STREAM_H), interpolation=cv2.INTER_AREA)
                ok, jpg = cv2.imencode(".jpg", small,
                                       [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                self.tim["jpeg"] += (time.perf_counter() - t_enc0) * 1000
                if ok:
                    with self.cond:
                        self.jpeg = jpg.tobytes()
                        self.cond.notify_all()
                    last_enc = now

            # per-stage timing log every 5 s (DEBUG)
            if now - last_log >= 5.0:
                n = max(1, self.tim_n)
                log.debug("ms/frame  cam=%.1f find=%.1f cls=%.1f draw=%.1f "
                          "jpeg=%.1f  -> %.1f fps (%d frames)",
                          self.tim["cam"] / n, self.tim["find"] / n,
                          self.tim["cls"] / n, self.tim["draw"] / n,
                          self.tim["jpeg"] / n, self.fps, self.tim_n)
                for k in self.tim:
                    self.tim[k] = 0.0
                self.tim_n = 0
                last_log = now

    # ----------------------------------------------------------------- modes
    def set_mode(self, mode, batch_name=None):
        if mode not in ("idle", "calibrate", "run"):
            return "bad mode"
        with self.lock:
            if self.mode == "run" and mode != "run":
                if self.session_id is not None:
                    self.db.end_session(self.session_id)
                self.session_id, self.sorter, self.armed = None, None, False
            if mode == "run":
                if self.segmentation() == "none":
                    return ("No detection source - tap the conveyor colour "
                            "in CALIBRATE or capture a background")
                self.clf = vision.Classifier(self.db.load_model())
                if not self.clf.ready():
                    return "Calibration incomplete: need grade 4 plus one of grades 1-3"
                if self.line is None:
                    return "No sorting line - draw it first"
                self.session_id = self.db.start_session(batch_name)
                self.sorter = Sorter(self.db, self.bg, self.clf, self.line,
                                     self.valve, self.session_id, belt=self.belt)
                self.armed = True
                if self.sim:
                    self.cam.set_scene("moving")
            elif mode == "calibrate" and self.sim:
                self.cam.set_scene("empty")
            self.mode = mode
            self.message = f"Mode: {mode}"
        return None

    def status(self):
        s = self.sorter
        counts = {f"g{g}": s.stats[f"g{g}"] for g in (1, 2, 3, 4)} if s \
            else {f"g{g}": 0 for g in (1, 2, 3, 4)}
        counts["blown"] = s.blown if s else 0
        line = None
        if self.line is not None:
            line = {"p1": [self.line.p1[0] / FRAME_W, self.line.p1[1] / FRAME_H],
                    "p2": [self.line.p2[0] / FRAME_W, self.line.p2[1] / FRAME_H]}
        return {"mode": self.mode, "armed": self.armed, "fps": round(self.fps, 1),
                "sim": self.sim or self.valve_source == "sim",
                "camera_source": ("webcam" if self.camera_source.startswith("webcam")
                                  else self.camera_source),
                "valve": self.valve_source,
                "has_background": self.bg is not None,
                "line": line, "selected_target": self.selected_target,
                "selected_grade": (self.selected_target
                                   if isinstance(self.selected_target, int) else None),
                "segmentation": self.segmentation(),
                "belt_count": len(self.belt.colors) if self.belt is not None else 0,
                "warnings": self._warnings(),
                "show_detected": self.show_detected,
                "session_id": self.session_id, "counts": counts,
                "message": self.message}


E = None          # set in main()
app = Flask(__name__)


def ok(**kw):
    return jsonify({"ok": True, **kw})


def err(msg):
    return jsonify({"ok": False, "error": msg}), 400


def body():
    return request.get_json(silent=True) or {}


@app.before_request
def preflight():
    # CORS preflight: answer 200 immediately; after_request adds the headers.
    if request.method == "OPTIONS":
        return "", 200


@app.after_request
def cors(resp):
    origin = request.headers.get("Origin")
    if origin in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return resp


@app.route("/stream.mjpg")
def stream():
    def gen():
        E.stream_clients += 1
        try:
            while True:
                with E.cond:
                    E.cond.wait(timeout=5)
                    jpg = E.jpeg
                if jpg is None:
                    continue
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                       + jpg + b"\r\n")
        finally:
            E.stream_clients -= 1
    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/health")
def api_health():
    # lock-free liveness probe
    return jsonify(ok=True, uptime_s=round(time.perf_counter() - E._t0, 1),
                   fps=round(E.fps, 1), loop_ms=round(1000 / max(E.fps, 0.1), 1))


@app.route("/api/status")
def api_status():
    return jsonify(E.status())


@app.route("/api/mode", methods=["POST", "OPTIONS"])
def api_mode():
    b = body()
    res = E.set_mode(b.get("mode"), b.get("batch_name"))
    return err(res) if res else ok(mode=E.mode)


@app.route("/api/arm", methods=["POST", "OPTIONS"])
def api_arm():
    E.armed = bool(body().get("armed"))
    return ok(armed=E.armed)


@app.route("/api/calibrate/background", methods=["POST", "OPTIONS"])
def api_bg():
    if E.mode != "calibrate":
        return err("switch to calibrate mode first")
    E.message = "Capturing background..."
    E.bg = E.cam.average(10)
    E.db.save_background(E.bg)
    if E.sim:
        E.cam.set_scene("static")
    E.message = "Background saved"
    return ok()


def _norm_target(b):
    """Request body -> 'belt' | 1..4, honouring both target= and legacy grade=."""
    t = b.get("target", b.get("grade"))
    if t == "belt" or t in (1, 2, 3, 4):
        return t
    if isinstance(t, str) and t.isdigit() and int(t) in (1, 2, 3, 4):
        return int(t)
    return None


@app.route("/api/calibrate/select", methods=["POST", "OPTIONS"])
def api_select():
    t = _norm_target(body())
    if t is None:
        return err('target must be "belt" or 1-4')
    E.selected_target = t
    return ok(target=t)


def _belt_click(frame, pt):
    """BELT_PATCH square around pt -> median Lab colour, uniformity checked.
    Returns (error_string | None, response)."""
    p = C.BELT_PATCH // 2
    x0, x1 = int(max(0, pt[0] - p)), int(min(FRAME_W, pt[0] + p + 1))
    y0, y1 = int(max(0, pt[1] - p)), int(min(FRAME_H, pt[1] + p + 1))
    patch = frame[y0:y1, x0:x1]
    lab = vision.bgr_to_lab(patch.reshape(-1, 3))
    med = np.median(lab, axis=0)
    std = lab.std(axis=0)
    if std[1] > 10 or std[2] > 10 or std[0] > 25:
        return "Patch not uniform - tap plain belt, not a bean or seam", None
    if E.belt is not None:
        diff = E.belt.colors - med
        diff[:, 0] *= C.BELT_L_WEIGHT
        if float(np.sqrt((diff * diff).sum(axis=1)).min()) < 2.0:
            return None, ok(note="already covered")
    if E.db.conn.execute("SELECT COUNT(*) FROM belt_colors").fetchone()[0] \
            >= C.BELT_MAX_COLORS:
        return f"max {C.BELT_MAX_COLORS} conveyor colours - clear one first", None
    hexv = vision.lab_to_hex(med)
    cid = E.db.add_belt_color(med, hexv)
    E.belt = E._load_belt()
    E.undo_stack.append(("belt", cid))
    E.message = "Belt colour saved"
    return None, ok(hex=hexv, id=cid, warnings=E._warnings())


def _patch_palette(frame, pt):
    """Direct colour grab at a tap point - no bean detection (dev helper)."""
    r = C.BELT_PATCH // 2
    x, y = int(pt[0]), int(pt[1])
    patch = frame[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1]
    if patch.size == 0:
        return None, (x, y), 0
    return (vision.extract_palette(patch.reshape(-1, 3)), (x, y),
            patch.shape[0] * patch.shape[1])


@app.route("/api/calibrate/click", methods=["POST", "OPTIONS"])
def api_click():
    if E.mode != "calibrate":
        return err("switch to calibrate mode first")
    b = body()
    try:
        pt = (float(b["nx"]) * FRAME_W, float(b["ny"]) * FRAME_H)
    except (KeyError, TypeError, ValueError):
        return err("need nx, ny in 0..1")
    frame = E.cam.read()
    if E.selected_target == "belt":
        error, resp = _belt_click(frame, pt)
        return err(error) if error else resp
    pal = None
    if E.segmentation() != "none":
        res = calibrate_click(E.db, frame, E.bg, pt, E.selected_target, E.belt)
        if not isinstance(res, str):          # bean blob found -> normal save
            _, _, pal = res
        elif not res.startswith("No bean detected"):
            return err(res)
    if pal is None:
        # dev fallback: grab the colour patch under the tap even when no bean
        # blob (or no detection source at all) is there
        pal, (x, y), area = _patch_palette(frame, pt)
        if not pal:
            return err("No colour at that point")
        E.db.add_sample(E.selected_target, x, y, area, pal)
        E.message = f"Saved G{E.selected_target} (patch)"
    else:
        E.message = f"Saved G{E.selected_target}"
    sid = E.db.conn.execute("SELECT id FROM samples ORDER BY id DESC LIMIT 1") \
        .fetchone()[0]
    E.undo_stack.append(("sample", sid))
    E.clf = vision.Classifier(E.db.load_model())
    E.message = f"Saved G{E.selected_target}"
    return ok(grade=E.selected_target, hex=[p.hex for p in pal],
              warnings=E._warnings())


@app.route("/api/calibrate/undo", methods=["POST", "OPTIONS"])
def api_undo():
    while E.undo_stack:
        kind, rid = E.undo_stack.pop()
        if kind == "belt":
            E.db.delete_belt_color(rid)
            E.belt = E._load_belt()
            return ok(kind="belt", warnings=E._warnings())
        E.db.delete_sample(rid)
        E.clf = vision.Classifier(E.db.load_model())
        return ok(kind="sample", warnings=E._warnings())
    return err("nothing to undo")


@app.route("/api/calibrate/clear", methods=["POST", "OPTIONS"])
def api_clear():
    t = _norm_target(body())
    if t == "belt":
        E.db.clear_belt_colors()
        E.belt = E._load_belt()
        return ok(warnings=E._warnings())
    if t is None:
        return err('target must be "belt" or 1-4')
    E.db.clear_grade(t)
    E.clf = vision.Classifier(E.db.load_model())
    return ok(warnings=E._warnings())


@app.route("/api/calibrate/show-detected", methods=["POST", "OPTIONS"])
def api_show():
    E.show_detected = bool(body().get("on"))
    return ok(on=E.show_detected)


@app.route("/api/line", methods=["POST", "OPTIONS"])
def api_line():
    b = body()
    if b.get("clear"):
        if E.mode == "run":
            E.set_mode("idle")          # no line -> nothing to sort on
        E.db.set_json("line", None)
        E.line = None
        return ok()
    try:
        p1 = (int(float(b["p1"][0]) * FRAME_W), int(float(b["p1"][1]) * FRAME_H))
        p2 = (int(float(b["p2"][0]) * FRAME_W), int(float(b["p2"][1]) * FRAME_H))
    except (KeyError, TypeError, ValueError, IndexError):
        return err('need {"p1":[nx,ny],"p2":[nx,ny]}')
    E.db.set_json("line", [list(p1), list(p2)])
    E.line = Line(p1, p2)
    if E.sorter is not None:
        E.sorter.set_line(E.line)
    return ok()


@app.route("/api/valve/test", methods=["POST", "OPTIONS"])
def api_valve():
    E.valve.pulse(delay=0)
    return ok()


@app.route("/api/settings/reload", methods=["POST", "OPTIONS"])
def api_reload():
    E.apply_tunables()
    E.belt = E._load_belt()          # PHP admin may have changed belt colours too
    E.clf = vision.Classifier(E.db.load_model())
    return ok(tunables={k: getattr(C, k) for k in TUNABLES})


def list_cameras():
    """Probe webcam indices 0-4 (the Pi camera isn't a VideoCapture device)."""
    for i in range(5):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW if sys.platform == "win32" else 0)
        if cap.isOpened():
            ok, fr = cap.read()
            res = f"{fr.shape[1]}x{fr.shape[0]}" if ok else "opens but no frame"
            print(f"  index {i}: {res} ({cap.getBackendName()})")
        else:
            print(f"  index {i}: not present")
        cap.release()


def main():
    global E
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--simulate", action="store_true",
                    help="shortcut for --camera sim --valve sim")
    ap.add_argument("--camera", default="pi", metavar="SRC",
                    help="pi | webcam | webcam:N (device index) | sim")
    ap.add_argument("--valve", choices=["real", "sim"], default="real")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--list-cameras", action="store_true",
                    help="probe webcam indices 0-4 and exit")
    args = ap.parse_args()

    if args.list_cameras:
        list_cameras()
        return
    cam_src = args.camera
    valve_src = args.valve
    if args.simulate:
        cam_src, valve_src = "sim", "sim"

    E = Engine(cam_src, valve_src)
    threading.Thread(target=E.loop, daemon=True).start()

    def _sig(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _sig)

    try:
        app.run(host="127.0.0.1", port=args.port, threaded=True,
                debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        E.stop()
        E.close()        # valve OFF, camera released
        print("engine stopped, valve off")


if __name__ == "__main__":
    main()
