#!/usr/bin/env python3
"""Web engine: the ONLY process that owns the camera and the valve.

Flask JSON API + MJPEG stream for the PHP front end. Binds 127.0.0.1 only.

  python3 engine.py [--simulate] [--port 5000]

Modes: idle | calibrate | run. The valve is OFF on start, on signals and on
exit, and the engine starts DISARMED until a run is started.
"""
import argparse
import signal
import threading
import time

import cv2
from flask import Flask, Response, jsonify, request

import config as C
import ui
import vision
from db import DB
from hardware import SimulatedCamera, SimulatedValve, make_camera, make_valve
from sorter import Line, Sorter, calibrate_click

FRAME_W, FRAME_H = C.FRAME_SIZE
STREAM_W, STREAM_H = 640, 360
JPEG_QUALITY = 70
STREAM_FPS = 15
ALLOWED_ORIGINS = {"http://127.0.0.1:8080", "http://localhost:8080"}

# settings keys the web UI may tune (validated server-side by PHP)
TUNABLES = ("DEFECT_BIAS", "UNKNOWN_DE", "VALVE_DELAY_S", "VALVE_PULSE_S",
            "DIFF_THRESHOLD", "MIN_BEAN_AREA", "MAX_BEAN_AREA", "DEFECT_VOTE_RATIO")


class Engine:
    def __init__(self, simulate):
        self.db = DB()
        self.sim = simulate
        if simulate:
            self.cam = SimulatedCamera()
            self.valve = SimulatedValve()
        else:
            self.cam = make_camera(saved_settings=self.db.get_json("camera"))
            self.db.set_json("camera", self.cam.settings)   # same as calibrate.py
            self.valve = make_valve()
        self.apply_tunables()

        self.mode = "idle"          # idle | calibrate | run
        self.armed = False
        self.selected_grade = 1
        self.show_detected = False
        self.message = "Engine started"
        self.session_id = None
        self.sorter = None
        self.bg = self.db.load_background()
        self.clf = vision.Classifier(self.db.load_model())
        self.line = self._load_line()

        self.fps = 0.0
        self.jpeg = None
        self.stream_clients = 0
        self.cond = threading.Condition()
        self.lock = threading.Lock()
        self._stop = False

    # ---------------------------------------------------------------- helpers
    def _load_line(self):
        pts = self.db.get_json("line")
        if pts and len(pts) == 2:
            return Line(tuple(pts[0]), tuple(pts[1]))
        return None

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
        while not self._stop:
            frame = self.cam.read()
            now = time.perf_counter()
            if self.mode == "run" and self.sorter is not None:
                self.sorter.process(frame, self.armed)
            elif self.mode == "calibrate":
                if self.show_detected and self.bg is not None:
                    for b in vision.find_beans(frame, self.bg):
                        cv2.drawContours(frame, [b.contour], -1, (255, 255, 255), 1)
                ui.draw_panel(frame, [f"CALIBRATE - grade {self.selected_grade}", self.message])
            else:
                if self.line is not None:
                    cv2.line(frame, self.line.p1, self.line.p2, (0, 255, 255), 2)
                ui.draw_panel(frame, ["IDLE", self.message])
            self.fps = 0.9 * self.fps + 0.1 / max(1e-6, now - t_prev)
            t_prev = now

            # encode only while a client is watching, max 15 fps
            if self.stream_clients and now - last_enc >= 1.0 / STREAM_FPS:
                small = cv2.resize(frame, (STREAM_W, STREAM_H), interpolation=cv2.INTER_AREA)
                ok, jpg = cv2.imencode(".jpg", small,
                                       [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if ok:
                    with self.cond:
                        self.jpeg = jpg.tobytes()
                        self.cond.notify_all()
                    last_enc = now

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
                if self.bg is None:
                    return "No background saved - capture it in CALIBRATE first"
                self.clf = vision.Classifier(self.db.load_model())
                if not self.clf.ready():
                    return "Calibration incomplete: need grade 4 plus one of grades 1-3"
                if self.line is None:
                    return "No sorting line - draw it first"
                self.session_id = self.db.start_session(batch_name)
                self.sorter = Sorter(self.db, self.bg, self.clf, self.line,
                                     self.valve, self.session_id)
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
                "sim": self.sim, "has_background": self.bg is not None,
                "line": line, "selected_grade": self.selected_grade,
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


@app.route("/api/calibrate/select", methods=["POST", "OPTIONS"])
def api_select():
    g = body().get("grade")
    if g not in (1, 2, 3, 4):
        return err("grade must be 1-4")
    E.selected_grade = g
    return ok(grade=g)


@app.route("/api/calibrate/click", methods=["POST", "OPTIONS"])
def api_click():
    if E.mode != "calibrate":
        return err("switch to calibrate mode first")
    if E.bg is None:
        return err("capture the background first")
    b = body()
    try:
        pt = (float(b["nx"]) * FRAME_W, float(b["ny"]) * FRAME_H)
    except (KeyError, TypeError, ValueError):
        return err("need nx, ny in 0..1")
    frame = E.cam.read()
    res = calibrate_click(E.db, frame, E.bg, pt, E.selected_grade)
    if isinstance(res, str):
        return err(res)
    _, _, pal = res
    E.clf = vision.Classifier(E.db.load_model())
    E.message = f"Saved G{E.selected_grade}"
    return ok(grade=E.selected_grade, hex=[p.hex for p in pal])


@app.route("/api/calibrate/undo", methods=["POST", "OPTIONS"])
def api_undo():
    g = E.db.delete_last_sample()
    E.clf = vision.Classifier(E.db.load_model())
    return ok(grade=g) if g else err("nothing to undo")


@app.route("/api/calibrate/clear", methods=["POST", "OPTIONS"])
def api_clear():
    g = body().get("grade")
    if g not in (1, 2, 3, 4):
        return err("grade must be 1-4")
    E.db.clear_grade(g)
    E.clf = vision.Classifier(E.db.load_model())
    return ok()


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
    return ok(tunables={k: getattr(C, k) for k in TUNABLES})


def main():
    global E
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--simulate", action="store_true",
                    help="SimulatedCamera + SimulatedValve, no hardware needed")
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args()

    E = Engine(args.simulate)
    threading.Thread(target=E.loop, daemon=True).start()

    def _sig(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _sig)

    try:
        app.run(host="127.0.0.1", port=args.port, threaded=True)
    except KeyboardInterrupt:
        pass
    finally:
        E.stop()
        E.close()        # valve OFF, camera released
        print("engine stopped, valve off")


if __name__ == "__main__":
    main()
