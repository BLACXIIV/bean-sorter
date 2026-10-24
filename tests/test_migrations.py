"""Fixture DBs from every previous schema version -> migrate -> data intact.

v1: sort_log has no session_id and no tracking columns (user_version=1)
v2: session_id exists, tracking columns do not        (user_version=2)
v3: same schema as v2                                 (user_version=3)
v4: tracking columns exist, samples.source does not   (user_version=4)

  python tests/test_migrations.py
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


BASE_SCHEMA = """
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
    ts TEXT DEFAULT CURRENT_TIMESTAMP, grade INTEGER, blown INTEGER
    {extra_sort_cols});
CREATE TABLE run_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_name TEXT, started_at TEXT DEFAULT CURRENT_TIMESTAMP, ended_at TEXT,
    g1 INTEGER DEFAULT 0, g2 INTEGER DEFAULT 0, g3 INTEGER DEFAULT 0,
    g4 INTEGER DEFAULT 0, blown INTEGER DEFAULT 0
    {extra_sess_cols});
CREATE TABLE belt_colors(id INTEGER PRIMARY KEY AUTOINCREMENT,
    hex TEXT NOT NULL, L REAL, a REAL, b REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""


def make_fixture(path, version):
    """Build a fixture DB at an old user_version with old data."""
    con = sqlite3.connect(path)
    extra = "" if version < 2 else ", session_id INTEGER"
    if version >= 4:
        extra += ", track_id INTEGER, crossing_type TEXT, x_on_line REAL," \
                 " speed_px_s REAL"
    extra_sess = (", suppressed INTEGER DEFAULT 0, estimated INTEGER DEFAULT 0,"
                  " lost INTEGER DEFAULT 0" if version >= 4 else "")
    con.executescript(BASE_SCHEMA.format(extra_sort_cols=extra,
                                         extra_sess_cols=extra_sess))
    con.execute(f"PRAGMA user_version={version}")
    sid = ",5" if version >= 2 else ""
    con.execute(f"INSERT INTO sort_log(grade,blown{' ,session_id' if version >= 2 else ''})"
                f" VALUES(4,1{sid})")
    con.execute("INSERT INTO run_sessions(batch_name,g2,g4,blown) VALUES('old',7,3,3)")
    con.execute("INSERT INTO samples(grade,x,y,area) VALUES(2,30,40,600)")
    con.execute("INSERT INTO settings(key,value) VALUES('k1','v1')")
    con.commit()
    con.close()


with tempfile.TemporaryDirectory() as td:
    for version in (1, 2, 3, 4):
        path = Path(td) / f"v{version}.db"
        make_fixture(path, version)
        db = DB(path)                                    # runs the migration
        v = db.conn.execute("PRAGMA user_version").fetchone()[0]
        report(f"v{version}: user_version bumped", v == SCHEMA_VERSION, f"v={v}")

        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(sort_log)")}
        need = {"session_id", "track_id", "crossing_type", "x_on_line",
                "speed_px_s"}
        report(f"v{version}: sort_log columns present", need <= cols,
               f"missing={need - cols}")

        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(run_sessions)")}
        need = {"suppressed", "estimated", "lost"}
        report(f"v{version}: run_sessions columns present", need <= cols,
               f"missing={need - cols}")

        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(samples)")}
        report(f"v{version}: samples.source present", "source" in cols,
               f"missing={'source' if 'source' not in cols else '-'}")
        r = db.conn.execute("SELECT source FROM samples").fetchone()
        report(f"v{version}: old sample source defaulted", r == ("bean",), str(r))

        r = db.conn.execute("SELECT grade, blown FROM sort_log").fetchone()
        report(f"v{version}: old sort_log row intact", r == (4, 1), str(r))
        r = db.conn.execute(
            "SELECT batch_name, g2, g4, blown FROM run_sessions").fetchone()
        report(f"v{version}: old run_sessions intact", r == ("old", 7, 3, 3),
               str(r))
        r = db.conn.execute("SELECT value FROM settings WHERE key='k1'").fetchone()
        report(f"v{version}: settings intact", r == ("v1",), str(r))
        db.conn.close()     # release the handle (Windows cleanup)

print("=" * 50)
print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
