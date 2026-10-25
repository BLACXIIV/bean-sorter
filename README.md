# Bean Sorter – Raspberry Pi 5 + Camera Module 3 (offline)

Python + SQLite. Grades 1–3 pass, grade 4 (defect) is blown out by a solenoid air valve.

**Step-by-step runbook for both platforms: [HOW-TO-RUN.md](HOW-TO-RUN.md)**

## 1. Hardware

| Part | Connection |
|---|---|
| Camera Module 3 | CSI port CAM0 / CAM1 on the Pi 5 (needs the Pi 5 22-pin cable) |
| Valve driver | Pi **GPIO17** (physical pin 11) → input of a **logic-level MOSFET module** or **relay module** |
| Solenoid valve | Own power supply (12/24 V or whatever the valve needs) through the driver. **Never power it from the Pi.** |
| Flyback diode | 1N4007 / 1N5819 across the solenoid coil (stripe to +V) – protects the driver |
| Grounds | Pi GND and valve-supply GND must be common |

Active-LOW relay board? Set `VALVE_ACTIVE_HIGH = False` in `config.py`.
Camera: mount it looking straight down, **even, bright, shadow-free light** (LED strips), plain matte background
of a colour different from the beans (white / blue / green). Same lighting for calibration and operation.

## 2. Install (Raspberry Pi OS Bookworm, desktop)

Flash the SD with **Raspberry Pi Imager** (64-bit desktop OS; set hostname/user and enable SSH in
the customisation settings), plug Ethernet into the router, then on the Pi:

```bash
sudo apt update
sudo apt install -y git python3-picamera2 python3-opencv python3-numpy python3-gpiozero python3-lgpio
git clone https://github.com/BLACXIIV/bean-sorter.git && cd bean-sorter
rpicam-hello --list-cameras        # Camera Module 3 should be listed
```
Use the apt OpenCV (not `pip install opencv-python`) – the pip wheel conflicts with Picamera2's Qt.
Push code edits from the PC (`git push`), then `git pull` on the Pi. No internet is needed afterwards.

## 2b. Windows PC (dev / testing, no GPIO)

The full web UI + engine runs on a laptop - see **Laptop dev run** below
(`run_dev.bat`: webcam + simulated valve + separate dev database).
The legacy OpenCV tools fall back to your webcam and a simulated valve automatically
(`--dry-run` also forces the simulated valve). Webcam auto-exposure makes colours
drift – do real calibration on the Pi.
To pick a specific USB webcam: `engine.py --camera webcam:1` or `--list-cameras`.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py calibrate
.\.venv\Scripts\python.exe main.py run --dry-run
```
Wrong camera opening? Change `WEBCAM_INDEX` in `config.py`.

## 2c. Web UI (touch screen, offline)

`install.sh` on the Pi installs everything once, then the **Bean Sorter** desktop icon runs
`start_bean_sorter.sh`: Flask engine on `127.0.0.1:5000` (owns camera + valve) + PHP on
`127.0.0.1:8080` + Chromium kiosk at 1024x600. Admin password defaults to `admin123`
(forced change on first login). Closing the kiosk stops everything with the valve OFF.
The OpenCV windows (`main.py calibrate`/`run`) still work when the engine is not running.

Engine arguments: `--camera pi|webcam|webcam:N|sim` (default `pi`), `--valve real|sim`
(default `real`), `--simulate` = `--camera sim --valve sim`, `--list-cameras` probes
webcam indices 0-4, `--port N`.

### Laptop dev run (no Pi, no GPIO)

`run_dev.bat` (Windows) / `run_dev.sh` (Linux/macOS): sets `BEAN_DB=data/dev.db`
(**separate database** – webcam colours are not valid for the Pi camera, never mix
them), asks which camera to use (laptop webcam / Pi Camera Module 3 / external USB
index / simulated), starts `engine.py --camera <pick> --valve sim` and
`php -S 127.0.0.1:8080 -t web`, opens the browser at operate.php, and stops
everything (and closes the app window) on Ctrl+C or Enter. Pass a camera source to
skip the menu: `run_dev.bat webcam:1`, `run_dev.bat sim`, `run_dev.bat pi`. Deps:
`pip install -r requirements-dev.txt` (flask, opencv, numpy) + PHP CLI with sqlite3.

Webcam auto white balance / exposure can drift – webcam colours are for **UI/flow
testing only**; always recalibrate on the Pi.

### Dev/test tooling (laptop only, never needed on the Pi)

```powershell
pip install -r requirements-dev.txt
playwright install chromium        # only for tests/test_ui_click_mapping.py
python scripts/check.py            # py_compile + php -l + all tests/test_*.py (--strict: any SKIPPED = fail)
python scripts/smoke.py            # clean boot: engine health, calib click, operate.php
python scripts/simrun.py 120       # calibrated sim run: drains the sim, then asserts tracker counts == ground truth
python scripts/a3_matrix.py        # paired schedule, 5 seeds x {drop10, medium 45px/f, harsh 90px/f} @8fps x {traj, legacy}
python scripts/diagnose_missed.py  # per-frame evidence for every missed grade-4 bean (NMS, votes, binds)
python scripts/miss_detail.py var/a3_x.jsonl var/a3_x_status.json  # per-missed-defect evidence + ghost classification
python tests/test_ui_click_mapping.py   # needs php CLI + playwright
python -m vermin --target=3.11- .  # Python 3.11 compatibility audit
```

`engine.py` also accepts `--fps-cap N` (sleep to at most N fps in the processing
loop; used to simulate a slow CPU such as 8 fps).

## 3. Use

```bash
python3 main.py valve-test        # check wiring first (valve clicks 3x)
python3 main.py calibrate         # teach grades 1-4   (add --fresh to start from zero)
python3 main.py run               # sort   (add --dry-run to test without GPIO)
python3 main.py status            # see stored hex colours per grade
```

### Calibration (window "Calibration")
1. Empty tray → press **B** (stores the background).
2. Put test beans on the tray, spread apart (not touching).
3. Press **1 / 2 / 3 / 4** to choose the grade, then **click each bean** of that grade.
   The system takes the bean's *whole* colour set (3 dominant colours via k-means in Lab space, each with its hex
   and share of the bean) and saves it to the grade's collection in SQLite.
4. Repeat with different beans – **10–30 beans per grade** (especially grade 4, with every kind of defect) gives the best result.
5. **U** undo, **X X** clear a grade, **M** show what the camera detects, **Q** save & quit.

### Operation (window "Bean Sorter")
1. Draw the line: click 2 points across the bean path, **Enter**. (Saved for next time.)
   The camera must see the beans *before* they reach the line.
2. Each bean is tracked, graded over several frames, and when its centre crosses the line:
   grade 1–3 → pass, grade 4 → valve pulses.
3. Keys: **A** arm/disarm valve, **V** test pulse, **L** redraw line, **Q** quit.

## 4. Tuning (`config.py`)
- `VALVE_DELAY_S` = (distance line→nozzle ÷ bean speed) – small latency. Start at 0 and adjust. `VALVE_PULSE_S` 0.03–0.1 s.
- Trajectory tracking (`tracking.py`, `FEATURES["trajectory_tracking"]`, default on): every bean keeps ONE identity
  from first sight to the line via a Kalman filter + Hungarian association, so each physical bean is counted
  (and blown, if grade 4) exactly once - even through detection dropouts (`TRACK_MAX_COAST_S`), brief occlusion,
  low frame rates, or beans vibrating on the line. `FEATURES["trajectory_tracking"] = False` restores the old
  nearest-neighbour tracker (`LegacyTracker`).
- Tunables (seconds and pixels, frame-rate independent; the marked * are editable in admin → Settings):
  `TRACK_MIN_HITS`, `TRACK_MAX_COAST_S`*, `TRACK_GATE_MIN_PX`, `TRACK_GATE_SPEED_FACTOR`,
  `TRACK_REID_WINDOW_S`, `TRACK_REID_DE`*, `TRACK_AREA_RATIO`, `TRACK_MIN_SPACING_FACTOR`,
  `DEDUPE_RADIUS_PX`*, `DEDUPE_WINDOW_S`*, `TRAIL_LEN`, `TRACK_HUNGARIAN_MAX`,
  `TRACK_MIN_VOTES`* (non-suspect votes below this at the line = "uncertain", default 2),
  `UNCERTAIN_POLICY`* = `"blow"` (default: fail-safe, uncertain beans actuate the valve - costs some good beans)
  or `"pass"` (uncertain beans count but never fire). Uncertain crossings are stored with
  `crossing_type="uncertain"` and counted in `/api/status` → `tracking.uncertain`.
- Vote resets: when a coasting/re-identified track binds a detection whose palette is far
  from the track mean AND far from every palette the track has shown, its votes restart
  (a genuinely different bean). Each reset is counted in `tracking.vote_resets` and in the
  run summary (`run_sessions.vote_resets`, schema v6, shown in Admin → Reports).
- Calibration patch fallback: tapping empty belt in grade mode still returns
  `NO_BEAN_AT_POINT`, but a tap where no bean outline is found MAY save a guarded patch
  sample (uniform, not belt-like, differs from background). It returns `source:"patch"`,
  persists `samples.source="patch"` (schema v5) and shows a `patch` badge in the grade
  lists.
- Duplicate suppression is fail-safe: it can only drop a weakly-established track
  (tentative / re-identified / estimated / <5 hits) that matches a recent crossing in
  position, a speed-aware time window AND palette dE - a real bean is never suppressed.
- Operate screen: the **Tracks** button (`POST /api/run/show-tracks`) draws fading trails + `#id` labels, and a
  line under the counters shows unique crossings / suppressed duplicates / estimated crossings / beans lost.
- `BEAN_RECORD=<file>` makes the engine append one JSON line per frame (t, detections, palettes, votes) for
  offline tuning; `python tools/replay_tracking.py <file>` replays it through the tracker and prints counts,
  suppressed/estimated/lost and per-track summaries. Off by default.
- Verification hooks (dev only): `BEAN_DROP=0.1` randomly drops detections before the tracker,
  `BEAN_LEGACY=1` forces the legacy tracker; SimulatedCamera counts physical line crossings
  (`/api/status` -> `ground_truth`), and `scripts/simrun.py <seconds>` runs a calibrated sim,
  drains until the frame is empty, then asserts tracked crossings == physical crossings and
  blown == physical defects. `BEAN_SIM_SCHEDULE=1` (+ `BEAN_SIM_SECS`, `BEAN_SIM_SPEED` px/s,
  `BEAN_SIM_DT` frame dt, `BEAN_SIM_SEED`) makes the sim spawn beans on a fixed frame-indexed
  schedule with sim time fed to the tracker - identical bean sequences for paired
  legacy/trajectory comparisons (scripts/a3_matrix.py). Per physical bean the sim records
  id/grade/crossed/cross_t/frames_visible, and every valve pulse is attributed to the bean
  nearest the line at fire time (`ground_truth.fires[]`), so a run reports
  defects blown/missed, false blows on good beans and uncertain blows (`simrun` ATTR line).
- Defects missed → lower `DEFECT_BIAS` (e.g. 0.7) or calibrate more grade 4 samples.
  Good beans blown → raise `DEFECT_BIAS` (e.g. 1.0) or add more grade 1–3 samples.
- `UNKNOWN_DE`: anything further than this from *every* grade (stones, husks…) is treated as grade 4 —
  it still fires the valve (fail-safe), but the camera label shows `?` instead of `G4` so unrecognised
  colours are not mistaken for real defect matches (`Classifier.classify_tagged`).
- Moving fast / blurry → strong light, then `EXPOSURE_US = 3000`, `ANALOGUE_GAIN = 2.0`.
- `LENS_POSITION` = 1 / camera-to-tray distance in metres (0.2 m → 5.0).
- Screen smaller than 1280×720 → `DISPLAY_SCALE = 0.75`.

## 5. What is stored (`data/beans.db`)
`samples` (one row per clicked bean) · `grade_colors` (hex + Lab + weight of every colour, per grade) ·
`background` (empty-tray image) · `settings` (locked camera exposure/white balance, sorting line) · `sort_log`
(every bean that crossed the line, with `track_id`, `crossing_type` observed/estimated/tentative, `x_on_line`,
`speed_px_s`) · `run_sessions` (per run, incl. `suppressed`, `estimated`, `lost` counters).

## 6. Known limits
- **Beans that touch each other are detected as ONE blob** (measured: S17 x20 seeds,
  20% of beans travelling touching at 1.8–2.1× bean area → trajectory counts
  27..27/30, legacy 30..30/30 only by ghost-splitting the merged blob; XFAIL).
  A merged blob also fails `TRACK_AREA_RATIO` re-id. Use a vibrating feeder /
  single-file chute — this is a segmentation limit, not fixable by tracking.
- Beans that overlap in the same lane for many frames (impossible physically, but
  the S15-overlap stress case) merge into one detection → undercount
  (measured: 30–32 counted of 32 over 20 seeds, XFAIL).
- Exposure and white balance are locked at first calibration so colours match later; if you change lights/camera, run `calibrate --fresh`.
- Colour only – no shape/size grading. Test the valve air pressure and nozzle aim with `V` before real runs.
