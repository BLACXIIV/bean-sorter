"""Calibration: capture empty background, then click beans and tell the system their grade."""
import time

import cv2

import config as C
import ui
import vision
from hardware import make_camera

WIN = "Calibration"
HELP = [
    "B = capture EMPTY tray background      1-4 = grade to assign",
    "CLICK a bean = save its main colours as the selected grade",
    "U = undo last   X (twice) = clear selected grade   N = clear markers",
    "M = show detected beans   Q = save & quit",
]


def calibrate(db):
    cam = make_camera(saved_settings=db.get_json("camera"))
    db.set_json("camera", cam.settings)          # same exposure/white balance in operation
    bg = db.load_background()

    state = {"click": None}

    def on_mouse(ev, x, y, flags, param):
        if ev == cv2.EVENT_LBUTTONDOWN:
            state["click"] = ui.to_frame_coords(x, y, C.DISPLAY_SCALE)

    cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WIN, on_mouse)

    grade, msg = 1, ("Press B with an EMPTY tray first" if bg is None
                     else "Background loaded. Place beans, pick grade, click them.")
    marks, show_all, clear_t = [], False, 0.0

    try:
        while True:
            frame = cam.read()

            if state["click"] is not None:
                pt, state["click"] = state["click"], None
                if bg is None:
                    msg = "Capture the background first (B, empty tray)"
                else:
                    bean = vision.bean_at(frame, bg, pt)
                    if bean is None:
                        msg = "No bean detected there - click the bean centre"
                    else:
                        pal = vision.extract_palette(vision.bean_pixels(frame, bean.contour))
                        if not pal:
                            msg = "Bean too small / not enough pixels"
                        else:
                            db.add_sample(grade, int(bean.centroid[0]), int(bean.centroid[1]),
                                          bean.area, pal)
                            marks.append((bean.contour, grade, bean.centroid))
                            msg = f"Saved G{grade}: " + "  ".join(p.hex for p in pal)

            view = frame.copy()
            if show_all and bg is not None:
                for b in vision.find_beans(frame, bg):
                    cv2.drawContours(view, [b.contour], -1, (255, 255, 255), 1)
            for cnt, g, cen in marks:
                cv2.drawContours(view, [cnt], -1, vision.GRADE_BGR[g], 2)
                ui.put_text(view, f"G{g}", (int(cen[0]) - 12, int(cen[1]) + 5),
                            vision.GRADE_BGR[g], 0.6, 2)

            counts = db.counts()
            ui.draw_panel(view, HELP + ["", f"Selected grade: {grade}" + ("  (DEFECT)" if grade == 4 else ""),
                                        msg], color=vision.GRADE_BGR[grade])
            _draw_swatches(view, db, counts)
            ui.show(WIN, view, C.DISPLAY_SCALE)

            k = cv2.waitKey(1) & 0xFF
            if k in (ord("q"), 27):
                break
            elif k in (ord("1"), ord("2"), ord("3"), ord("4")):
                grade = int(chr(k))
            elif k == ord("b"):
                msg = "Capturing background..."
                bg = cam.average(10)
                db.save_background(bg)
                marks.clear()
                msg = "Background saved. Now place beans on the tray."
            elif k == ord("u"):
                g = db.delete_last_sample()
                if g is not None and marks:
                    marks.pop()
                msg = "Undid last sample" if g else "Nothing to undo"
            elif k == ord("n"):
                marks.clear()
                msg = "Markers cleared (saved colours are kept)"
            elif k == ord("m"):
                show_all = not show_all
            elif k == ord("x"):
                if time.time() - clear_t < 3:
                    db.clear_grade(grade)
                    marks[:] = [m for m in marks if m[1] != grade]
                    msg, clear_t = f"Cleared all samples of grade {grade}", 0.0
                else:
                    msg, clear_t = f"Press X again to clear ALL grade {grade} samples", time.time()
    finally:
        cam.close()
        cv2.destroyAllWindows()
    print("Calibration saved:", db.counts())


def _draw_swatches(view, db, counts):
    h = view.shape[0]
    y0 = h - 4 * 26 - 8
    cv2.rectangle(view, (4, y0 - 4), (4 + 90 + 18 * 22, h - 4), (20, 20, 20), -1)
    for i, g in enumerate((1, 2, 3, 4)):
        y = y0 + i * 26
        ui.put_text(view, f"G{g} ({counts[g]})", (10, y + 16), vision.GRADE_BGR[g], 0.5)
        for j, hx in enumerate(db.recent_hex(g, 18)):
            cv2.rectangle(view, (96 + j * 22, y), (96 + j * 22 + 18, y + 20), ui.hex_to_bgr(hx), -1)
