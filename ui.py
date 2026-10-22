"""Small OpenCV drawing helpers."""
import cv2

FONT = cv2.FONT_HERSHEY_SIMPLEX


def put_text(img, text, org, color=(255, 255, 255), scale=0.6, thick=1):
    cv2.putText(img, text, org, FONT, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, text, org, FONT, scale, color, thick, cv2.LINE_AA)


def draw_panel(img, lines, origin=(8, 8), color=(255, 255, 255)):
    """Dark box with several lines of text."""
    scale, lh = 0.55, 22
    w = max(cv2.getTextSize(t, FONT, scale, 1)[0][0] for t in lines) + 16
    h = lh * len(lines) + 10
    x, y = origin
    roi = img[y:y + h, x:x + w]
    if roi.size:
        img[y:y + h, x:x + w] = (roi * 0.35).astype(roi.dtype)
    for i, t in enumerate(lines):
        put_text(img, t, (x + 8, y + 20 + i * lh), color, scale)


def hex_to_bgr(h):
    h = h.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


def to_frame_coords(x, y, scale):
    return int(x / scale), int(y / scale)


def show(win, img, scale=1.0):
    if scale != 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    cv2.imshow(win, img)
