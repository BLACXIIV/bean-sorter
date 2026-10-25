# AGENTS.md - Bean Sorter

Read this file fully before every task. Update it in the same commit whenever you add an endpoint, tool, UI token or rule.

## 1. What this is
Offline bean grading machine: Raspberry Pi 5 + Camera Module 3 + one solenoid air valve. Camera watches beans on a conveyor. Grades 1-3 pass. Grade 4 (defect) crossing the user's line fires the valve.
The SAME code must run on a Windows/Linux laptop (laptop or USB webcam, simulated valve) and on the Pi. Only the capture layer (hardware.py camera classes) and the valve driver may differ.

## 2. Architecture
- engine.py: Flask on 127.0.0.1:5000. The ONLY process that opens the camera and the valve. Modes: idle | calibrate | run. MJPEG at /stream.mjpg, JSON at /api/*.
- sorter.py: per-frame sorting (tracker, line crossing, valve trigger).
- tracking.py: TrajectoryTracker - Kalman + Hungarian + re-id; every physical bean keeps ONE identity to the line, counted (and blown if grade 4) exactly once. Selected by config.FEATURES["trajectory_tracking"] (False = LegacyTracker in sorter.py).
- vision.py: colour pipeline. PROTECTED (section 5).
- hardware.py: Camera classes (Pi, Webcam, Simulated), Valve, SimulatedValve.
- db.py: SQLite schema, migrations, queries (data/beans.db, WAL). config.py: default tunables (override via settings key "tunables").
- web/: PHP 8.2 app, document root. operate.php, admin/*, api/*, inc/*, assets/css/app.css, assets/js/*.
- calibrate.py, operate.py, main.py: legacy OpenCV GUI and CLI tools. Keep working.
- start_bean_sorter.sh, BeanSorter.desktop, install.sh, run_dev.*: launchers.
- tests/, scripts/: regression safety net (section 4).
Flow: camera -> frame normalization (BGR uint8, exactly config.FRAME_SIZE 1280x720) -> segmentation (conveyor colour, else background) -> bean contour -> bean pixels -> palette -> classify -> votes -> line crossing -> valve.
Engine API additions: POST /api/run/show-tracks {on:bool} (also allowed in idle) toggles the trail/ID overlay; /api/status gains "tracking" (active, coasting, unique_crossed, suppressed, estimated, uncertain, lost_before_line, vote_resets, enabled) and "ground_truth" (sim camera only: active/spawned/crossed/defects physically crossing the line, plus per-bean `beans[]` = id/grade/crossed/cross_t/frames_visible and `fires[]` = valve pulses attributed to the physical bean nearest the line). Uncertain tracks (< TRACK_MIN_VOTES non-suspect votes) act per UNCERTAIN_POLICY: "blow" (default, fail-safe - costs some good beans) or "pass" (never fires). Vote resets: on a gap/re-id rebind, votes are cleared ONLY if the new palette is > TRACK_REID_DE from the track mean AND from every palette in the track's pal_hist; each reset counts in tracking.vote_resets and run_sessions.vote_resets (schema v6). BEAN_RECORD=<file> logs one JSON line per frame for tools/replay_tracking.py <file> (off by default); BEAN_DROP=<p> drops detections before the tracker; BEAN_LEGACY=1 forces LegacyTracker. Sim schedule mode: BEAN_SIM_SCHEDULE=1 + BEAN_SIM_SECS=<sim seconds> + BEAN_SIM_SPEED=<px/s> + BEAN_SIM_DT=<frame dt> + BEAN_SIM_SEED=<n> spawns beans on a fixed frame-indexed schedule (paired legacy/trajectory comparisons use the identical bean sequence) and the sorter reads camera.sim_t instead of wall clock.
Calibration patch fallback: a GRADE tap that finds no bean outline samples a BELT_PATCH patch instead - accepted only when the patch is uniform (same std check as belt taps), its weighted distance to EVERY belt colour is >= BELT_TOLERANCE, and (no belt colours) it differs from the stored background by > DIFF_THRESHOLD. Otherwise the real code (NO_BEAN_AT_POINT / NO_SEGMENTATION_SOURCE) stands. Accepted patch samples return {"source":"patch"} and persist samples.source="patch" (schema v5, default "bean"); the operate grade list and admin/grades.php show a small "patch" badge.

## 3. Commands (cross-platform, use Python runners, not shell-only scripts)
- python engine.py --simulate            (simulated camera + valve; --fps-cap N throttles the loop, e.g. 8 to simulate a slow CPU)
- python engine.py --camera auto --valve auto
- php -S 127.0.0.1:8080 -t web
- python scripts/check.py [--strict]     (static checks + all tests; SKIPPED lines are collected and summarised, --strict makes any skip a failure)
- python scripts/smoke.py                (clean boot test)
- python scripts/simrun.py <s> [--dump=f.json]  (calibrated sim run; drains, then asserts tracker counts == camera ground truth and prints ATTR defects/blown/missed/good/good_blown/unc_blows/orphan from per-bean fire attribution. BEAN_SIM_SCHEDULE=1 uses the fixed spawn schedule)
- python scripts/a3_matrix.py            (paired schedule, 5 seeds x {drop10, medium 45px/f, harsh 90px/f} @8fps x both trackers -> min/mean/max of defects_missed and false_blows)
- python scripts/diagnose_missed.py      (instruments NMS + votes, prints per-frame evidence for every missed grade-4 bean)
- python scripts/miss_detail.py <rec.jsonl> <status.json>  (per-missed-defect evidence: frames visible, detections, votes, track ids, state at crossing; classifies lost_before_line tracks as fragmented/ghost)
- python scripts/bench_tracking.py       (tracker update() ms/call at 10/30/60 detections)
- python scripts/profile_stages.py       (per-stage frame cost, tracking on/off)
- python tests/test_ui_click_mapping.py  (needs: pip install playwright; playwright install chromium; php CLI)
- python -m vermin --target=3.11- .      (Pi compat audit; Pi runs Python 3.11 Bookworm / 3.13 Trixie)
If scripts/check.py, scripts/smoke.py or tests/ do not exist, creating them (section 4) is your FIRST task before any feature.

## 4. Definition of done (every task, no exceptions)
1. python scripts/check.py passes: py_compile on all .py, php -l on all PHP, then tests:
   - tests/test_color_pipeline.py: synthetic belt + beans; asserts bean count, grade of each bean, palette hex within dE 3 of expected, no conveyor-like pixels in bean pixels.
   - tests/test_palette_golden.py: fixed pixel arrays -> expected Lab/hex (catches BGR/RGB swaps and 8-bit vs float Lab mistakes).
   - tests/test_calibration_flow.py: engine --simulate via HTTP: select belt, tap belt spots, tap every grade, assert samples saved, error "code" fields on bad taps, then run and assert valve fires only for grade 4.
   - tests/test_migrations.py: open fixture DBs from every previous schema version (v1/v2/v3), migrate, assert old data intact.
   - tests/test_tracking.py: deterministic synthetic-detections scenarios run through BOTH LegacyTracker and TrajectoryTracker with injected timestamps (dropouts, coasting across the line, jitter, low fps, two-blob duplicates, re-id after loss, side-by-side pairs, belt stop, vibration, line move, dense single-file at 150/300/600 px/s, alternating good/defect dense flow, blob split at the line, realistic motion x20 seeded runs with EXACT fires assertion, touching pairs x20 seeds, >40-detections stress, S18 one-frame evidence under both UNCERTAIN_POLICY values, S19 alternating good/defect palette dE 14 and 25 across a gap rebind - defect votes must survive); the summary table prints expected/legacy/trajectory counts AND fires and enforces trajectory fires >= legacy fires on every non-XFAIL row. Rows may be XFAIL (expected failure): the real numbers are always printed and the row is not counted as a check. Current XFAILs: S15-overlap (physically impossible lane overlap merges blobs -> undercount) and S17 touching pairs (one merged blob -> undercount). Also validates Hungarian vs brute force and the greedy-by-cost fallback (TRACK_HUNGARIAN_MAX).
   - tests/test_time_source.py: runs the real Engine.loop() with a no-sim_t camera stub, a non-schedule SimulatedCamera, and a schedule-mode sim - asserts sorter t is always perf_counter unless the camera exposes sim_t, and that sim steps equal BEAN_SIM_DT exactly.
   - tests/test_tracking_migration.py: migrate a previous-schema fixture DB, assert old rows intact and the v4 tracking columns (sort_log.track_id/crossing_type/x_on_line/speed_px_s, run_sessions.suppressed/estimated/lost) exist.
   - tests/test_ui_click_mapping.py (Playwright, viewport 1024x600): tap corners and centre of the camera image, also at browser zoom 90% and 110%; posted nx,ny must equal the tap position within 0.01.
2. python scripts/smoke.py passes: clean start of engine --simulate and PHP, /api/health OK in under 10 s, operate.php returns 200, one scripted calibration click succeeds.
3. UI changes: screenshots at EXACTLY 1024x600 attached; operate.php has no scrolling (scrollHeight <= 600, scrollWidth <= 1024).
4. The app still STARTS on a clean checkout. A feature that breaks startup is not done.
5. Final report in the format of section 14.

## 5. Colour pipeline contract (PROTECTED)
Protected in vision.py: segment/find_beans, bean_at, bean_pixels, BeltModel, bgr_to_lab, lab_to_hex, extract_palette, Classifier, belt_warnings.
Invariants:
- Frames are BGR uint8. Hex is RGB. Convert exactly once, only inside lab_to_hex/bgr_to_lab.
- Lab is float32 from float BGR/255 (L 0..100). NEVER mix with 8-bit OpenCV Lab (L 0..255). All stored colours (DB) are float Lab.
- Colour distance is Lab dE (conveyor distance uses BELT_L_WEIGHT). Never compare colours in BGR/RGB.
- Bean pixels: eroded contour, glare removed, conveyor-like pixels removed, fallback to unfiltered if < 30 pixels.
- Palette: k-means K=3 in Lab, clusters under MIN_COLOR_WEIGHT dropped, weights renormalized. Same input pixels must always give the same palette (deterministic seed). Known exception you may fix once with a golden test: sampling must use a fresh default_rng(0) per call, not a shared advancing generator.
- Area thresholds are in px^2 at 1280x720. Any camera that gives another size is normalized BEFORE analysis (centre-crop to 16:9, never stretch, then resize).
Change rules: do not modify protected functions unless the task explicitly says so. If you must: update the golden tests in the same commit and include a before/after accuracy table (section 7) in the report. Additive optional parameters with identical default behaviour are allowed.

## 6. Why colour picking breaks, and the rules that prevent it
1. Click mapping: UI sends NORMALIZED coords nx=(clientX-rect.left)/rect.width, ny likewise, using getBoundingClientRect() of the displayed <img>. Never hardcode 640/360, never assume zoom 100%. Overlays above the image (toasts, banners, panels) must use pointer-events:none unless they are interactive. Layout changes must pass test_ui_click_mapping.
2. State: a tap is handled by selected_target (belt|1|2|3|4) and mode == calibrate. Never add new conditions to the click path without a test. Changing mode must not clear selected_target unintentionally.
3. Stale models: the engine owns ONE function reload_models() that rebuilds Classifier and BeltModel from the DB. Call it after every calibration write/undo/clear, settings reload, camera switch and entering run. Never keep another copy of colour data anywhere. PHP admin deletions call POST /api/settings/reload.
4. No silent failure: every rejected tap returns {"ok":false,"code":...,"error":"human text"} and the UI shows it. Codes: WRONG_MODE, NO_SEGMENTATION_SOURCE, NO_BEAN_AT_POINT, PATCH_NOT_UNIFORM, TOO_FEW_PIXELS, BELT_LIMIT, CAMERA_ERROR, BAD_REQUEST. Never swallow exceptions; log stack traces to logs/engine.log.
5. Frame immutability: analysis runs on the raw frame. Draw overlays only on view = frame.copy() AFTER all analysis of that frame. Never analyse a frame you drew on.
6. One camera owner: only engine.py opens a camera, only through the Camera interface in hardware.py. Never call cv2.VideoCapture, Picamera2 or gpiozero from anywhere else (a second opener makes the camera busy and picking "does nothing").
7. Camera lock: exposure and white balance are locked and saved in settings key "camera"; no feature may re-enable auto exposure/white balance or change camera controls after calibration. A webcam that cannot be locked must show a visible warning.
8. Thresholds: changing a default in config.py (tolerances, areas, bias) requires re-running test_color_pipeline and listing the change in the report. Tunable names stay stable.
9. Locks and threads: hold locks only to copy small state, never during find_beans, k-means, JPEG encoding or sleeping. A slow loop must not make the API time out.
10. Calibration belongs to a camera: settings key "calibration_camera" is stored at calibration; a mismatch with the active camera produces a status warning, not a block.

## 7. Accuracy rules
- Lock the camera, use constant diffuse lighting, no daylight. Colours from different cameras or lighting are not comparable: recalibrate.
- At least 10-30 beans per grade, both sides of the bean, every defect type for grade 4. Pick belt colours in bright, normal and shadowed areas (3-5 spots).
- Warn when a grade's colours are within BELT_TOLERANCE+5 of the conveyor (belt_warnings).
- Any change that touches detection, palette, classification or tolerances must report accuracy on tests/fixtures (synthetic) as a confusion matrix plus per-grade precision/recall, before and after. Synthetic results never replace real-bean validation on the Pi: tools/evaluate.py runs labelled images in data/validation/ through the pipeline and prints the same table.
- Do not "improve accuracy" by changing thresholds blindly. Measure first.

## 8. API and database compatibility
- Never rename or remove an existing endpoint, JSON field or DB column. Add new ones. UI code must read status fields defensively (missing field = default).
- DB changes are additive migrations through PRAGMA user_version: idempotent, tested from fixture DBs. Never DROP or rename. Every connection uses WAL and busy_timeout 5000.
- Calibration tables are written by the engine. PHP may read them, and delete only through the engine reload rule (6.3).
- Timestamps are stored UTC and displayed in Asia/Manila.
- New features sit behind a flag in config.FEATURES (default off until tests pass) and live in new modules where possible.

## 9. UI design system (web/assets/css/app.css is the only stylesheet)
Dark mode only. Tokens (do not invent colours):
  --bg:#111111 page; --panel:#151515 cards, top bar, modals; --line:#343434 borders, dividers, inputs, secondary buttons;
  --text:#E6E6E6; --muted:#9A9A9A;
  --g1:#00C800; --g2:#00C8C8; --g3:#FFA500; --g4:#FF0000 (match the camera overlay);
  --ok:var(--g1); --warn:var(--g3); --danger:var(--g4);
  --radius:8px; --gap:8px; --tap:48px; system font stack, body >= 15px, buttons 17-18px, counters 28-32px.
Rules:
- Target screen 1024x600 touch. operate.php fits exactly with no page scroll. Admin pages scroll only inside the content area under a fixed top bar. Every tappable control >= 48px, no hover-only behaviour, no right-click.
- Layout of operate.php: top bar 44px, camera 640x360 on the left with the control area below, grade/conveyor list on the right.
- Reuse components: .btn, .btn-primary, .btn-secondary, .btn-danger, .btn-grade[data-grade], .chip, .toast, .banner(.warn/.error), .modal, .accordion, .table, .swatch. Add new components to app.css with the .c- prefix. No inline styles, no new colours, no CDN, no external fonts or icons (inline SVG only).
- Feedback: success toast 3 s auto-dismiss; errors show the code text from the API; banners for persistent conditions only (engine down needs 3 failed polls and clears itself).
- Detection labels: `G1`-`G4` only for a colour that actually matches a calibrated grade. A bean farther than `UNKNOWN_DE` from EVERY grade draws `?` in grey (Classifier.classify_tagged) - it still votes/fires as grade 4 (fail-safe), but the label must never claim a real match. `?engine=host:port` on operate.php overrides the engine base (tests/dev).
- Overlay toggles default ON: `show_tracks` (trail + `#id` on track labels) and `show_detected`. In run mode `show_detected` dims all non-bean-mask pixels x0.4 (same as calibrate, on the annotated view copy only - never before analysis) and adds a SMALL lowercase label (`g1`-`g4` / `?`, dimmed) above each det's track label = this frame's own classification vs the track's decided grade; dets past MAX_VOTES get one extra cached-palette classify. Toggled from calibrate AND run panels via POST /api/calibrate/show-detected (same engine flag).
- Destructive actions (clear grade, delete, shutdown) always use a confirm modal.
- Any new screen needs a 1024x600 screenshot in the report.

## 10. Hardware safety
- Valve OFF at engine start, on SIGTERM/SIGINT, on crash and on exit. Engine starts DISARMED. Pulse length capped by VALVE_MAX_PULSE_S.
- The ONLY code that energizes the valve is Valve.pulse / SimulatedValve.pulse. Tests never use the real valve.

## 11. Platform parity (laptop vs Raspberry Pi)
- Pi-only imports (picamera2, libcamera, gpiozero) stay lazy inside the class that needs them. Use pathlib, no hardcoded paths, no OS-specific commands outside the launcher scripts.
- Windows: PHP built-in server is single-process, so PHP must never stream or long-poll.
- Do not claim anything about Pi performance or real hardware unless you ran it there.

## 12. Protocol for adding a feature
1. Read this file and the files your feature touches. State which layer(s): engine, vision, sorter, DB, API, PHP, UI.
2. Write the plan in 5 lines or fewer, including which protected functions you will NOT touch.
3. Add or extend tests first when behaviour is involved.
4. Implement additively (new module, new endpoint, new column, flag off).
5. Run check.py and smoke.py, fix, repeat. Then the UI screenshots.
6. Update this file and README.md if you added commands, endpoints, tokens or rules.
7. Do not reformat unrelated files. Never commit data/*.db, logs/, var/. New dependencies need a reason and must be added to requirements-dev.txt, install.sh and README in the same commit (the Pi is offline).

## 13a. KNOWN LIMITATIONS (measured, tests mark XFAIL - do not "fix" silently)
- Touching beans are ONE blob to segmentation: S17 x20 seeds (20% touching at 1.8-2.1x area) -> trajectory counts 27..27/30, legacy 30..30/30 (legacy "wins" counts only by splitting merged blobs into ghost tracks); fires: expected 168, legacy 168, trajectory 178 (uncertain-blow overfires the merged beans - fail-safe cost). Physical fix only: feeder/chute spacing. Also breaks TRACK_AREA_RATIO re-id.
- Same-lane beans overlapping for many frames (S15-overlap, physically impossible but stress-relevant): merged detections -> 30-32 of 32 counted over 20 seeds. The physically consistent variant (S15-jam) is exact.
- Merged-blob frames can give a track one wrong-grade vote (S15-jam fires 165/166). decide() semantics unchanged.

## 13. Debugging "colour will not pick" / "it does not run"
1. GET /api/health and /api/status: mode, selected_target, segmentation, warnings, message.
2. Read the exact code/error returned by POST /api/calibrate/click and the last 50 lines of logs/engine.log.
3. Run tests/test_ui_click_mapping.py, then tests/test_calibration_flow.py. The first failing test names the broken layer.
4. If it worked before: git bisect with python scripts/smoke.py as the test.
5. Fix the cause and add a test that would have caught it.

## 14. Report format
Files changed, exact commands run with REAL pasted output, screenshots, then three sections: DONE AND VERIFIED, DONE BUT NOT VERIFIED, NOT DONE. Nothing is "verified" unless you ran it and pasted the output. Real camera, Raspberry Pi frame rate, real valve and real beans are NOT VERIFIED unless actually tested. List every default or threshold you changed.
