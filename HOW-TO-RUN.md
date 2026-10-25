# How to run the Bean Sorter

Two processes always run together:

- **Engine** (`engine.py`) — Flask on `http://127.0.0.1:5000`. The only process that opens the camera and the valve.
- **Web UI** (`php -S 127.0.0.1:8080 -t web`) — touch interface at `http://127.0.0.1:8080/operate.php`.

The launcher scripts start both, wait until healthy, and open the browser.

---

## Part 1 — Raspberry Pi (production, real valve)

### Step 0: wire the hardware (power off)

| Part | Connection |
|---|---|
| Camera Module 3 | CSI port on the Pi 5 (22-pin cable), pointing straight down at the belt |
| Valve driver input | Pi **GPIO17** (physical pin 11) → logic-level MOSFET / relay module IN |
| Solenoid valve | Its **own** 12/24 V supply through the driver — never from the Pi |
| Flyback diode | 1N4007/1N5819 across the coil, stripe to +V |
| Grounds | Pi GND and valve-supply GND connected together |

Active-LOW relay board → set `VALVE_ACTIVE_HIGH = False` in `config.py`.
Lighting: even, bright, shadow-free LED light; matte belt/background of a colour
different from the beans. Same lighting for calibration and operation — always.

### Step 1: flash the OS

1. On a PC, run **Raspberry Pi Imager** → Raspberry Pi OS **64-bit (desktop)**.
   In the gear/customisation step set hostname, user/password, Wi-Fi or plan
   Ethernet, and enable SSH if you want headless access.
2. Boot the Pi with the camera already attached, log in.

### Step 2: get the code onto the Pi

Either clone (needs internet once):

```bash
git clone https://github.com/BLACXIIV/bean-sorter.git && cd bean-sorter
```

or copy the folder from the PC (USB stick / `scp -r bean-sorter pi@<pi-ip>:~`).

### Step 3: one-time install

```bash
cd ~/bean-sorter
./install.sh
```

Installs via apt: `python3-flask picamera2 opencv numpy gpiozero lgpio`,
`php-cli php-sqlite3 php-zip php-gd php-mbstring php-xml`, `chromium`, and drops
a **Bean Sorter** icon on the desktop + apps menu. Internet needed once here;
everything runs offline afterwards.

Optional — start automatically at every boot:

```bash
./install.sh --autostart
```

### Step 4: check the camera

```bash
rpicam-hello --list-cameras
```

Camera Module 3 must appear. If not: re-seat the CSI cable (contacts face the
right way, both ends) and reboot.

### Step 5: first valve test (before any beans)

The engine must **not** be running (it owns the GPIO). In a terminal:

```bash
cd ~/bean-sorter
python3 main.py valve-test     # valve should click 3 times
```

No click → check GPIO17 wiring, common ground, valve PSU, and
`VALVE_ACTIVE_HIGH` in `config.py`. Never continue past this step with a valve
that does not click.

### Step 6: start the app

Double-click the **Bean Sorter** desktop icon — it starts the engine (Pi camera +
real valve), the PHP UI, and a fullscreen Chromium kiosk at 1024×600.

Or from a terminal (same thing, plus you can pass options):

```bash
./start_bean_sorter.sh                # production: --camera pi --valve real
./start_bean_sorter.sh --simulate     # simulation mode: fake beans, no GPIO
BEAN_LAN=1 ./start_bean_sorter.sh     # also reachable at http://<pi-ip>:8080
```

First boot shows the login for admin pages: default password `admin123`
(you will be forced to change it).

### Step 7: calibrate (touch screen)

In the UI, **CALIBRATE** tab:

1. Tap **BELT** → tap 3–5 spots on the moving belt (bright, normal and shadowed areas).
2. *(Optional)* tap **Background** with an empty belt to store a background frame.
3. Tap **G1** → tap beans of grade 1, one tap per bean. Repeat for **G2, G3, G4 DEFECT**
   — 10–30 beans per grade, every defect type for G4.
   A `patch` badge on a sample means the bean outline wasn't found and a guarded
   colour patch was saved instead — check it looks like the bean colour.
4. **Undo** removes the last sample; **Clear** empties the selected target.
   **Show detected** draws what the camera currently sees as beans.

### Step 8: set the line and run

In the **OPERATE** tab:

1. **Draw line** → tap 2 points across the bean path, downstream of where beans
   are first seen. The camera must see each bean *before* it reaches the line.
2. **Test valve** → pulse without a run; confirm air aim/timing.
3. **ARM** — the engine starts disarmed every boot; nothing fires until armed.
4. **START** → beans are tracked, graded, counted; grade-4 beans crossing the
   line fire the valve. **STOP** ends the run (valve off).

Tune `VALVE_DELAY_S` (line→nozzle distance ÷ belt speed) and `VALVE_PULSE_S`
in `config.py` or Admin → Settings.

### Step 9: stopping

- Closing the kiosk window stops the engine and PHP — the valve is always turned
  **OFF** on exit, crash, SIGTERM/SIGINT.
- From a terminal: Ctrl+C in `start_bean_sorter.sh`.
- If a second start "does nothing": a previous run is still alive — the launcher
  just re-opens the kiosk. Kill leftovers: `pkill -f engine.py; pkill -f "php -S"`.

### Pi troubleshooting

| Symptom | Check |
|---|---|
| Black/frozen camera image | `rpicam-hello --list-cameras`; another process holding the camera (`pkill -f engine.py`) |
| Taps save nothing | read the toast code — `WRONG_MODE`, `NO_BEAN_AT_POINT`, `PATCH_NOT_UNIFORM` … then `logs/engine.log` |
| Valve never fires | engine must be ARMED + a run started; `main.py valve-test` with engine stopped |
| Wrong grades | recalibrate under the *same* light; Admin → Settings `DEFECT_BIAS` |
| Logs | `logs/engine.log`, `logs/php.log` (stack traces, never silent) |

----------------------------------------------------------------------------------

## Part 2 — Windows laptop (dev / testing, simulated valve)

Runs the identical app with a webcam and a simulated valve. Database is
`data/dev.db` — deliberately separate; webcam colours are **not** valid for the
Pi camera, never copy it to the Pi.

### Step 1: install Python

1. Download Python 3.11+ from python.org → install → tick **"Add python.exe to PATH"**.
2. Verify in a *new* terminal:
   ```powershell
   py --version
   ```
   If `python` opens the Microsoft Store or says "No module named numpy", use
   `py` or the full `.venv` path everywhere below — the Store stub has no packages.

### Step 2: install PHP CLI

Easiest:

```powershell
winget install PHP.PHP.8.2
```

(or unzip a PHP build and add it to PATH). Then enable extensions — find the
loaded `php.ini` with `php --ini`, and make sure these lines are uncommented:

```ini
extension=pdo_sqlite
extension=sqlite3
extension=zip
extension=gd
extension=mbstring
extension=xml
```

Verify:

```powershell
php --version
php -m | findstr /i "sqlite zip gd mbstring xml"
```

(`php_zip`/`php_gd`/`mbstring`/`xml` are needed by Admin → export; pdo_sqlite by everything.)

### Step 3: get the code + Python deps

```powershell
git clone https://github.com/BLACXIIV/bean-sorter.git
cd bean-sorter
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

### Step 4: run it

```powershell
.\run_dev.bat
```

It asks which camera to use:

- **laptop webcam** (default `webcam`, or `webcam:1` for a second USB camera)
- **sim** — simulated camera + simulated valve, fake beans on a belt (no hardware at all)
- **pi** — a Pi Camera Module 3 if attached

It starts the engine (`--valve sim`, dev database), the PHP server, opens
`http://127.0.0.1:8080/operate.php`, and stops everything on Enter/Ctrl+C.
Skip the menu: `run_dev.bat sim`, `run_dev.bat webcam:1`.

Manual equivalent (two terminals):

```powershell
.\.venv\Scripts\python.exe engine.py --camera webcam --valve sim
php -S 127.0.0.1:8080 -t web
```

### Step 5: use it

Same flow as the Pi (Part 1, steps 7–8): **CALIBRATE** → belt spots, grades by
tapping beans; **OPERATE** → Draw line → ARM → START. Everything runs with the
simulated valve — check the log/counters to see "fires".

### Step 6: legacy GUI tools (optional, engine must be stopped)

```powershell
.\.venv\Scripts\python.exe main.py calibrate    # OpenCV window calibration
.\.venv\Scripts\python.exe main.py run --dry-run
.\.venv\Scripts\python.exe main.py status
```

### Step 7: run the tests (dev laptops only)

```powershell
.\.venv\Scripts\python.exe scripts\check.py     # compile + php -l (if php) + all tests
.\.venv\Scripts\python.exe scripts\smoke.py     # clean boot + health + calib click
.\.venv\Scripts\python.exe scripts\simrun.py 120 # calibrated sim run, asserts counts == truth
```

`test_ui_click_mapping.py` additionally needs `playwright install chromium`.

### Windows troubleshooting

| Symptom | Check |
|---|---|
| `No module named 'numpy'` | you're on the Store stub — use `.\.venv\Scripts\python.exe` |
| Blank operate.php | PHP not running: `php -S 127.0.0.1:8080 -t web`; `php -m` for pdo_sqlite |
| Webcam won't open | wrong index — `run_dev.bat webcam:1`, or `--list-cameras` |
| Colours drift | webcams auto-expose; webcam calibration is UI-testing only — real calibration happens on the Pi |
| Ports busy | something else on 5000/8080 — `netstat -ano \| findstr ":5000 :8080"` |

---

## Quick reference

| | Pi (production) | Windows (dev) |
|---|---|---|
| Start | `install.sh` once → double-click **Bean Sorter** | `run_dev.bat` |
| Manual start | `./start_bean_sorter.sh` | engine + `php -S` (two terminals) |
| Engine | `engine.py --camera pi --valve real` | `engine.py --camera webcam --valve sim` |
| UI | `http://127.0.0.1:8080/operate.php` (kiosk) | same URL in browser |
| Database | `data/beans.db` | `data/dev.db` |
| Stop | close kiosk / Ctrl+C → valve OFF | Enter or Ctrl+C in `run_dev.bat` |
| Logs | `logs/engine.log`, `logs/php.log` | `logs/dev-engine.log`, `logs/dev-php.log` |
