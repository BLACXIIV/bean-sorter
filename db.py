"""SQLite storage: colour collections per grade, camera lock, background, line, log."""
import json
import sqlite3

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
    blown INTEGER
);
"""


class DB:
    def __init__(self, path=DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

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

    def clear_grade(self, grade):
        self.conn.execute("DELETE FROM samples WHERE grade=?", (grade,))
        self.conn.commit()

    def clear_all(self):
        for t in ("samples", "grade_colors", "settings", "background", "sort_log"):
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

    # ---- operation log -------------------------------------------------------
    def log_event(self, grade, blown):
        self.conn.execute("INSERT INTO sort_log(grade, blown) VALUES(?,?)", (grade, int(blown)))
        self.conn.commit()
