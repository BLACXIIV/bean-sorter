"""Operation: draw the sorting line, then grade beans live and blow grade 4 at the line."""
import time

import cv2

import config as C
import ui
import vision
from hardware import make_camera, make_valve
from sorter import Line, Sorter

WIN = "Bean Sorter"


# ----------------------------------------------------------------- line setup
def setup_line(cam, db):
    saved = db.get_json("line")
    state = {"pts": [tuple(p) for p in saved] if saved else []}

    def on_mouse(ev, x, y, flags, param):
        if ev == cv2.EVENT_LBUTTONDOWN:
            if len(state["pts"]) >= 2:
                state["pts"] = []
            state["pts"].append(ui.to_frame_coords(x, y, C.DISPLAY_SCALE))

    cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WIN, on_mouse)
    while True:
        frame = cam.read()
        pts = state["pts"]
        for p in pts:
            cv2.circle(frame, p, 6, (0, 255, 255), -1)
        if len(pts) == 2:
            cv2.line(frame, pts[0], pts[1], (0, 255, 255), 2)
        ui.draw_panel(frame, ["DRAW THE SORTING LINE",
                              "Click 2 points across the bean path (beans must be visible BEFORE the line)",
                              "ENTER = start sorting    R = redo    Q = quit"])
        ui.show(WIN, frame, C.DISPLAY_SCALE)
        k = cv2.waitKey(1) & 0xFF
        if k in (13, 10) and len(pts) == 2:
            db.set_json("line", pts)
            return Line(pts[0], pts[1])
        if k == ord("r"):
            state["pts"] = []
        if k in (ord("q"), 27):
            return None


# ------------------------------------------------------------------ main loop
def operate(db, dry_run=False):
    bg = db.load_background()
    if bg is None:
        print("No background saved. Run:  python3 main.py calibrate")
        return 1
    clf = vision.Classifier(db.load_model())
    if not clf.ready():
        print("Calibration incomplete: need grade 4 plus at least one of grades 1-3.",
              db.counts())
        return 1

    cam = make_camera(saved_settings=db.get_json("camera"))
    valve = make_valve(dry_run)
    try:
        line = setup_line(cam, db)
        if line is None:
            return 0
        _loop(cam, db, valve, bg, clf, line)
    finally:
        valve.close()
        cam.close()
        cv2.destroyAllWindows()
    return 0


def _loop(cam, db, valve, bg, clf, line):
    sorter = Sorter(db, bg, clf, line, valve)
    fps, t_prev = 0.0, time.perf_counter()
    armed = True

    while True:
        frame = cam.read()
        now = time.perf_counter()
        view = sorter.process(frame, armed, t=now)

        fps = 0.9 * fps + 0.1 / max(1e-6, now - t_prev)
        t_prev = now
        stats, blown = sorter.stats, sorter.blown
        ui.draw_panel(view, [
            ("ARMED - valve active" if armed else "DISARMED - valve OFF (counting only)")
            + f"   {fps:4.1f} fps",
            f"G1:{stats['g1']}  G2:{stats['g2']}  G3:{stats['g3']}  G4:{stats['g4']}   blown:{blown}",
            "A = arm/disarm   V = test valve   L = redraw line   Q = quit"],
            color=(0, 255, 0) if armed else (0, 200, 255))
        ui.show(WIN, view, C.DISPLAY_SCALE)

        k = cv2.waitKey(1) & 0xFF
        if k in (ord("q"), 27):
            break
        elif k == ord("a"):
            armed = not armed
        elif k == ord("v"):
            valve.pulse(delay=0)
        elif k == ord("l"):
            new = setup_line(cam, db)
            if new is None:
                break
            sorter.set_line(new)
