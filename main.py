#!/usr/bin/env python3
"""Bean sorter - Raspberry Pi 5 + Camera Module 3 + solenoid valve (offline).

  python3 main.py calibrate [--fresh]   teach the system grades 1-4
  python3 main.py run [--dry-run]       sort (dry-run = no GPIO, valve only printed)
  python3 main.py status                show what is stored in SQLite
  python3 main.py valve-test            pulse the valve 3 times
  python3 main.py reset                 wipe everything
"""
import argparse
import time

import config as C
from db import DB


def cmd_status(db):
    counts = db.counts()
    print("Samples per grade :", counts)
    for g in (1, 2, 3, 4):
        print(f"  G{g} recent colours:", " ".join(db.recent_hex(g, 12)) or "-")
    print("Background saved  :", db.load_background() is not None)
    print("Camera lock       :", db.get_json("camera"))
    print("Sorting line      :", db.get_json("line"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("calibrate")
    c.add_argument("--fresh", action="store_true", help="wipe all data first")
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    sub.add_parser("valve-test")
    sub.add_parser("reset")
    args = ap.parse_args()
    db = DB()

    if args.cmd == "calibrate":
        if args.fresh:
            db.clear_all()
        from calibrate import calibrate
        calibrate(db)
    elif args.cmd == "run":
        from operate import operate
        raise SystemExit(operate(db, dry_run=args.dry_run))
    elif args.cmd == "status":
        cmd_status(db)
    elif args.cmd == "valve-test":
        from hardware import make_valve
        v = make_valve()
        try:
            for i in range(3):
                print(f"pulse {i + 1}/3 ({C.VALVE_PULSE_S * 1000:.0f} ms)")
                v.pulse(delay=0)
                time.sleep(1.0)
        finally:
            v.close()
    elif args.cmd == "reset":
        if input("Type YES to erase ALL calibration data: ") == "YES":
            db.clear_all()
            print("Erased.")


if __name__ == "__main__":
    main()
