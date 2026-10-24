"""All tunable settings in one place."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# BEAN_DB points at a different database (dev runs must not mix webcam colours
# with Pi-camera calibration - relative paths resolve under BASE_DIR).
_db = os.environ.get("BEAN_DB")
DB_PATH = (BASE_DIR / _db) if _db and not os.path.isabs(_db) \
    else Path(_db) if _db else BASE_DIR / "data" / "beans.db"

# ---- Camera ---------------------------------------------------------------
FRAME_SIZE = (1280, 720)
FPS = 60                    # requested; real speed is limited by processing
LENS_POSITION = 5.0         # Camera Module 3 manual focus, dioptres = 1 / distance_in_metres
                            # 5.0 -> sharp at ~20 cm, 3.3 -> ~30 cm, 10 -> ~10 cm
EXPOSURE_US = None          # None = auto then lock. For fast belts use e.g. 3000 (+ strong light)
ANALOGUE_GAIN = None        # set together with EXPOSURE_US (e.g. 2.0), otherwise leave None
DISPLAY_SCALE = 1.0         # shrink the window on small screens (e.g. 0.75)
WEBCAM_INDEX = 0            # PC dev fallback camera (change if the wrong webcam opens)

# ---- Solenoid valve ---------------------------------------------------------
VALVE_PIN = 17              # BCM numbering (GPIO17 = physical pin 11)
VALVE_ACTIVE_HIGH = True    # False for active-LOW relay modules
VALVE_DELAY_S = 0.0         # wait after a defect crosses the line before blowing
VALVE_PULSE_S = 0.05        # how long the valve stays open
VALVE_MAX_PULSE_S = 0.5     # hard safety cap

# ---- Bean detection (background subtraction) ---------------------------------
DIFF_THRESHOLD = 30         # higher = less sensitive to shadows/noise
MIN_BEAN_AREA = 400         # px^2, ignore smaller blobs
MAX_BEAN_AREA = 30000       # px^2, ignore larger blobs (touching clumps, hands)

# ---- Conveyor belt colour model ------------------------------------------------
BELT_TOLERANCE = 18.0       # weighted Lab distance; closer pixels count as belt
BELT_L_WEIGHT = 0.5         # <1.0 = brightness matters less (shadows stay belt)
BELT_MAX_COLORS = 8         # max saved conveyor colours
BELT_PATCH = 21             # px square sampled on a belt tap

# ---- Colour model ------------------------------------------------------------
PALETTE_K = 3               # main colours extracted per bean
MAX_PIXELS = 400            # pixels sampled per bean for k-means
MIN_COLOR_WEIGHT = 0.04     # drop colour clusters covering < 4 % of the bean
DEFECT_BIAS = 0.85          # <1.0 makes grade 4 easier to trigger, >1.0 harder
UNKNOWN_DE = 30.0           # bean further than this from EVERY grade = treated as grade 4

# ---- Tracking / decision -------------------------------------------------------
TRACK_MAX_DIST = 80         # px a bean may move between frames (legacy tracker)
TRACK_MAX_MISSED = 8        # frames before a track is dropped (legacy tracker)
MAX_VOTES = 8               # frames used to classify each bean
DEFECT_VOTE_RATIO = 0.3     # bean is grade 4 if >= 30 % of its votes say grade 4
LINE_MARGIN = 0.1           # how far past the line ends (fraction of length) still counts

# ---- Feature flags -------------------------------------------------------------
FEATURES = {
    "trajectory_tracking": True,    # TrajectoryTracker instead of LegacyTracker
}
if os.environ.get("BEAN_LEGACY"):        # dev escape hatch (benchmarks etc.)
    FEATURES["trajectory_tracking"] = False

# ---- Trajectory tracking (tracking.py) -------------------------------------------
TRACK_MIN_HITS = 2            # detections before a tentative track is confirmed
TRACK_MAX_COAST_S = 0.6       # *(0.1-3.0) s a track may coast with no detection
TRACK_GATE_MIN_PX = 30        # min association gate radius, px
TRACK_GATE_SPEED_FACTOR = 1.5 # gate radius = max(GATE_MIN, FACTOR*speed*dt) + 20
TRACK_REID_WINDOW_S = 0.8     # s a lost/coasting track stays eligible for re-id
TRACK_REID_DE = 12.0          # *(3-40) max palette dE for re-identification
TRACK_AREA_RATIO = (0.6, 1.7) # allowed detection/track area ratio (re-id + cost)
TRACK_MIN_SPACING_FACTOR = 0.6  # NMS merge dist = factor * typical bean diameter
DEDUPE_RADIUS_PX = 25         # *(5-100) crossings closer than this are duplicates
DEDUPE_WINDOW_S = 0.15        # *(0.02-1.0) within this window, seconds
TRAIL_LEN = 30                # positions kept per track for the trail overlay
TRACK_HUNGARIAN_MAX = 40      # above n tracks or dets: greedy-by-cost instead
TRACK_MIN_VOTES = 2           # *(1-8) non-suspect votes below -> "uncertain" bean
UNCERTAIN_POLICY = "blow"     # *(blow|pass) uncertain bean: blow = fail-safe
                            # (fires valve, wastes some good beans); pass = save
