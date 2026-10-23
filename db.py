"""SQLite storage: colour collections per grade, camera lock, background, line, log.

Thread-safe: each thread gets its own connection to the same WAL file."""
import json
import sqlite3
import threading

import cv2
import numpy as np

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grade INTEGER NOT NULL CHECK(grade BETWEEN 1 AND 4),
    x INTEGER, y INTEGER, area REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS grade_colors(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    grade INTEGER NOT NULL,
    hex TEXT NOT NULL,
    L REAL, a REAL, b REAL,
    weight REAL
);
CREATE INDEX IF NOT EXISTS idx_gc_grade ON grade_colors(grade);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS background(
    id INTEGER PRIMARY KEY CHECK(id = 1),
    png BLOB NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sort_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT CURRENT_TIMESTAMP,
    grade INTEGER,
    blown INTEGER,
    session_id INTEGER,
    track_id INTEGER,
    crossing_type TEXT,
    x_on_line REAL,
    speed_px_s REAL
);
CREATE TABLE IF NOT EXISTS run_sessions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_name TEXT,
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    ended_at TEXT,
    g1 INTEGER DEFAULT 0,
    g2 INTEGER DEFAULT 0,
    g3 INTEGER DEFAULT 0,
    g4 INTEGER DEFAULT 0,
    blown INTEGER DEFAULT 0,
    suppressed INTEGER DEFAULT 0,
    estimated INTEGER DEFAULT 0,
    lost INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sortlog_ts ON sort_log(ts);
CREATE TABLE IF NOT EXISTS belt_colors(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hex TEXT NOT NULL,
    L REAL, a REAL, b REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

SCHEMA_VERSION = 4


class DB:
    def __init__(self, path=DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(path)
        self._local = threading.local()
        self.conn.executescript(SCHEMA)
        self._migrate()

    @property
    def conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self._path)
            c.execute("PRAGMA busy_timeout=5000")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA journal_mode=WAL")
            self._local.conn = c
        return c

    def _migrate(self):
        v = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if v < 2:
            cols = [r[1] for r in self.conn.execute("PRAGMA table_info(sort_log)")]
            if "session_id" not in cols:
                self.conn.execute("ALTER TABLE sort_log ADD COLUMN session_id INTEGER")
        if v < 4:
            cols = [r[1] for r in self.conn.execute("PRAGMA table_info(sort_log)")]
            for name, ddl in (("track_id", "track_id INTEGER"),
                              ("crossing_type", "crossing_type TEXT"),
                              ("x_on_line", "x_on_line REAL"),
                              ("speed_px_s", "speed_px_s REAL")):
                if name not in cols:
                    self.conn.execute(f"ALTER TABLE sort_log ADD COLUMN {ddl}")
            cols = [r[1] for r in self.conn.execute("PRAGMA table_info(run_sessions)")]
            for name in ("suppressed", "estimated", "lost"):
                if name not in cols:
                    self.conn.execute(
                        f"ALTER TABLE run_sessions ADD COLUMN {name} INTEGER DEFAULT 0")
        if v < SCHEMA_VERSION:
            self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sortlog_session ON sort_log(session_id)")
        self.conn.commit()

    # ---- generic settings (camera lock, sorting line, ...) -----------------
    def set_json(self, key, obj):
        self.conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(obj)))
        self.conn.commit()

    def get_json(self, key, default=None):
        r = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    # ---- empty-tray background ---------------------------------------------
    def save_background(self, img):
        ok, buf = cv2.imencode(".png", img)
        self.conn.execute(
            "INSERT INTO background(id,png) VALUES(1,?) "
            "ON CONFLICT(id) DO UPDATE SET png=excluded.png, updated_at=CURRENT_TIMESTAMP",
            (buf.tobytes(),))
        self.conn.commit()

    def load_background(self):
        r = self.conn.execute("SELECT png FROM background WHERE id=1").fetchone()
        if not r:
            return None
        return cv2.imdecode(np.frombuffer(r[0], np.uint8), cv2.IMREAD_COLOR)

    # ---- colour samples ------------------------------------------------------
    def add_sample(self, grade, x, y, area, palette):
        cur = self.conn.execute(
            "INSERT INTO samples(grade,x,y,area) VALUES(?,?,?,?)", (grade, x, y, area))
        sid = cur.lastrowid
        self.conn.executemany(
            "INSERT INTO grade_colors(sample_id,grade,hex,L,a,b,weight) VALUES(?,?,?,?,?,?,?)",
            [(sid, grade, p.hex, float(p.lab[0]), float(p.lab[1]), float(p.lab[2]), p.weight)
             for p in palette])
        self.conn.commit()
        return sid

    def delete_last_sample(self):
        r = self.conn.execute("SELECT id, grade FROM samples ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            return None
        self.conn.execute("DELETE FROM samples WHERE id=?", (r[0],))
        self.conn.commit()
        return r[1]

    def delete_sample(self, sample_id):
        self.conn.execute("DELETE FROM samples WHERE id=?", (int(sample_id),))
        self.conn.commit()

    def clear_grade(self, grade):
        self.conn.execute("DELETE FROM samples WHERE grade=?", (grade,))
        self.conn.commit()

    def clear_all(self):
        for t in ("samples", "grade_colors", "settings", "background", "sort_log",
                  "belt_colors"):
            self.conn.execute(f"DELETE FROM {t}")
        self.conn.commit()

    def counts(self):
        rows = self.conn.execute("SELECT grade, COUNT(*) FROM samples GROUP BY grade").fetchall()
        out = {1: 0, 2: 0, 3: 0, 4: 0}
        out.update(dict(rows))
        return out

    def recent_hex(self, grade, limit=16):
        rows = self.conn.execute(
            "SELECT hex FROM grade_colors WHERE grade=? ORDER BY id DESC LIMIT ?",
            (grade, limit)).fetchall()
        return [r[0] for r in rows]

    def load_model(self):
        """{grade: float32 array (M, 3) of Lab colours} - the 'collection' for each grade."""
        rows = self.conn.execute("SELECT grade, L, a, b FROM grade_colors").fetchall()
        model = {}
        for g in (1, 2, 3, 4):
            pts = [(L, a, b) for gr, L, a, b in rows if gr == g]
            if pts:
                model[g] = np.array(pts, np.float32)
        return model

    # ---- conveyor belt colours -------------------------------------------------
    def add_belt_color(self, lab, hex):
        cur = self.conn.execute(
            "INSERT INTO belt_colors(hex,L,a,b) VALUES(?,?,?,?)",
            (hex, float(lab[0]), float(lab[1]), float(lab[2])))
        self.conn.commit()
        return cur.lastrowid

    def list_belt_colors(self):
        return self.conn.execute(
            "SELECT id, hex, L, a, b, created_at FROM belt_colors ORDER BY id").fetchall()

    def delete_belt_color(self, color_id):
        self.conn.execute("DELETE FROM belt_colors WHERE id=?", (int(color_id),))
        self.conn.commit()

    def delete_last_belt_color(self):
        r = self.conn.execute(
            "SELECT id FROM belt_colors ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            return None
        self.conn.execute("DELETE FROM belt_colors WHERE id=?", (r[0],))
        self.conn.commit()
        return r[0]

    def clear_belt_colors(self):
        self.conn.execute("DELETE FROM belt_colors")
        self.conn.commit()

    def load_belt_colors(self):
        """float32 (M, 3) Lab array of saved conveyor colours, or None."""
        rows = self.conn.execute("SELECT L, a, b FROM belt_colors").fetchall()
        return np.array(rows, np.float32) if rows else None

    # ---- run sessions --------------------------------------------------------
    def start_session(self, batch_name=None):
        cur = self.conn.execute("INSERT INTO run_sessions(batch_name) VALUES(?)", (batch_name,))
        self.conn.commit()
        return cur.lastrowid

    def end_session(self, session_id, counters=None):
        self.conn.execute(
            "UPDATE run_sessions SET ended_at=CURRENT_TIMESTAMP WHERE id=?", (session_id,))
        if counters:
            self.conn.execute(
                "UPDATE run_sessions SET suppressed=?, estimated=?, lost=? WHERE id=?",
                (int(counters.get("suppressed", 0)),
                 int(counters.get("estimated", 0)),
                 int(counters.get("lost_before_line", 0)), session_id))
        self.conn.commit()

    # ---- operation log -------------------------------------------------------
    def log_event(self, grade, blown, session_id=None, track_id=None,
                  crossing_type=None, x_on_line=None, speed_px_s=None):
        self.conn.execute(
            "INSERT INTO sort_log(grade, blown, session_id, track_id,"
            " crossing_type, x_on_line, speed_px_s) VALUES(?,?,?,?,?,?,?)",
            (grade, int(blown), session_id, track_id, crossing_type,
             x_on_line, speed_px_s))
        if session_id is not None:
            g = int(grade) if grade in (1, 2, 3, 4) else 4
            self.conn.execute(
                f"UPDATE run_sessions SET g{g}=g{g}+1, blown=blown+? WHERE id=?",
                (int(blown), session_id))
        self.conn.commit()
