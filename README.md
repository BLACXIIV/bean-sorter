# Bean Sorter – Raspberry Pi 5 + Camera Module 3 (offline)

Python + SQLite. Grades 1–3 pass, grade 4 (defect) is blown out by a solenoid air valve.

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

Falls back to your webcam and a simulated valve automatically (`--dry-run` also forces the simulated
valve). Webcam auto-exposure makes colours drift – do real calibration on the Pi.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py calibrate
.\.venv\Scripts\python.exe main.py run --dry-run
```
Wrong camera opening? Change `WEBCAM_INDEX` in `config.py`.

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
- Defects missed → lower `DEFECT_BIAS` (e.g. 0.7) or calibrate more grade 4 samples.
  Good beans blown → raise `DEFECT_BIAS` (e.g. 1.0) or add more grade 1–3 samples.
- `UNKNOWN_DE`: anything further than this from *every* grade (stones, husks…) is treated as grade 4.
- Moving fast / blurry → strong light, then `EXPOSURE_US = 3000`, `ANALOGUE_GAIN = 2.0`.
- `LENS_POSITION` = 1 / camera-to-tray distance in metres (0.2 m → 5.0).
- Screen smaller than 1280×720 → `DISPLAY_SCALE = 0.75`.

## 5. What is stored (`data/beans.db`)
`samples` (one row per clicked bean) · `grade_colors` (hex + Lab + weight of every colour, per grade) ·
`background` (empty-tray image) · `settings` (locked camera exposure/white balance, sorting line) · `sort_log` (every bean that crossed the line).

## 6. Known limits
- Beans that touch each other are detected as one blob (ignored if too big). Use a vibrating feeder / single-file chute.
- Exposure and white balance are locked at first calibration so colours match later; if you change lights/camera, run `calibrate --fresh`.
- Colour only – no shape/size grading. Test the valve air pressure and nozzle aim with `V` before real runs.
