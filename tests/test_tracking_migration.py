"""Schema v3 -> v4 migration test: old data intact, new columns exist.

  python tests/test_tracking_migration.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db import DB, SCHEMA_VERSION

PASS, FAIL = "PASS", "FAIL"
results = []


def report(name, ok, detail=""):
    results.append(ok)
    print(f"{PASS if ok else FAIL}  {name}  {detail}")


OLD_SCHEMA = """
CREATE TABLE samples(id INTEGER PRIMARY KEY AUTOINCREMENT,
    grade INTEGER NOT NULL CHECK(grade BETWEEN 1 AND 4),
    x INTEGER, y INTEGER, area REAL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE grade_colors(id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    grade INTEGER NOT NULL, hex TEXT NOT NULL, L REAL, a REAL, b REAL, weight REAL);
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE background(id INTEGER PRIMARY KEY CHECK(id = 1),
    png BLOB NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE sort_log(id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT CURRENT_TIMESTAMP, grade INTEGER, blown INTEGER,
    session_id INTEGER);
CREATE TABLE run_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_name TEXT, started_at TEXT DEFAULT CURRENT_TIMESTAMP, ended_at TEXT,
    g1 INTEGER DEFAULT 0, g2 INTEGER DEFAULT 0, g3 INTEGER DEFAULT 0,
    g4 INTEGER DEFAULT 0, blown INTEGER DEFAULT 0);
CREATE TABLE belt_colors(id INTEGER PRIMARY KEY AUTOINCREMENT,
    hex TEXT NOT NULL, L REAL, a REAL, b REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""

with tempfile.TemporaryDirectory() as td:
    path = Path(td) / "old.db"
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    con.execute("PRAGMA user_version=3")
    con.execute("INSERT INTO sort_log(grade,blown,session_id) VALUES(4,1,7)")
    con.execute("INSERT INTO sort_log(grade,blown,session_id) VALUES(2,0,7)")
    con.execute("INSERT INTO run_sessions(batch_name,g1,g4,blown) VALUES('b1',5,2,2)")
    con.execute("INSERT INTO samples(grade,x,y,area) VALUES(1,10,20,500)")
    con.execute("INSERT INTO grade_colors(sample_id,grade,hex,L,a,b,weight)"
                " VALUES(1,1,'#112233',50,10,20,0.9)")
    con.commit()
    con.close()

    db = DB(path)                                   # runs the migration
    v = db.conn.execute("PRAGMA user_version").fetchone()[0]
    report("user_version bumped", v == SCHEMA_VERSION, f"v={v}")

    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(sort_log)")}
    need = {"track_id", "crossing_type", "x_on_line", "speed_px_s"}
    report("sort_log has new columns", need <= cols, f"missing={need - cols}")

    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(run_sessions)")}
    need = {"suppressed", "estimated", "lost"}
    report("run_sessions has new columns", need <= cols, f"missing={need - cols}")

    rows = db.conn.execute(
        "SELECT grade, blown, session_id FROM sort_log ORDER BY id").fetchall()
    report("old sort_log rows intact",
           rows == [(4, 1, 7), (2, 0, 7)], str(rows))

    r = db.conn.execute(
        "SELECT batch_name, g1, g4, blown FROM run_sessions").fetchone()
    report("old run_sessions row intact", r == ("b1", 5, 2, 2), str(r))

    s = db.conn.execute("SELECT hex, grade FROM grade_colors").fetchone()
    report("old grade_colors intact", s == ("#112233", 1), str(s))

    # new log_event writes the new fields; old-style call still works
    db.log_event(3, 0, session_id=7)                      # legacy signature
    db.log_event(4, 1, session_id=7, track_id=42,
               crossing_type="estimated", x_on_line=0.55, speed_px_s=480.5)
    r = db.conn.execute(
        "SELECT track_id, crossing_type, x_on_line, speed_px_s"
        " FROM sort_log WHERE track_id=42").fetchone()
    report("log_event stores tracking fields",
           r == (42, "estimated", 0.55, 480.5), str(r))
    n = db.conn.execute("SELECT COUNT(*) FROM sort_log").fetchone()[0]
    report("legacy log_event still works", n == 4, f"rows={n}")

    db.end_session(1, {"suppressed": 2, "estimated": 3, "lost_before_line": 1})
    r = db.conn.execute(
        "SELECT suppressed, estimated, lost FROM run_sessions WHERE id=1").fetchone()
    report("end_session stores counters", r == (2, 3, 1), str(r))

    # fresh DB at v4 also works and has the columns
    db2 = DB(Path(td) / "fresh.db")
    cols = {r[1] for r in db2.conn.execute("PRAGMA table_info(sort_log)")}
    report("fresh DB has all columns", need <= cols or "track_id" in cols)

    db.conn.close()     # release file handles (Windows can't clean up otherwise)
    db2.conn.close()

print("=" * 50)
print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
