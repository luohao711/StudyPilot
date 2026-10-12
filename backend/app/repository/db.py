"""SQLite 连接与建表。初版只保存用户和课程。"""

import os
import sqlite3
from contextlib import contextmanager

from app.logging_conf import log

DB_PATH = os.environ.get(
    "STUDYPILOT_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "studypilot.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS courses (
    course_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(user_id),
    name       TEXT NOT NULL,
    term       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (user_id, name)
);
"""

SAMPLE_COURSES = [
    ("软件工程", "2026春"),
    ("高等数学", "2026春"),
]


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(with_sample: bool = True) -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        if row["c"] == 0:
            conn.execute("INSERT INTO users (name) VALUES (?)", ("罗心月",))
            log.info("初始化用户：罗心月")
        if with_sample and conn.execute("SELECT COUNT(*) AS c FROM courses").fetchone()["c"] == 0:
            user_id = conn.execute("SELECT user_id FROM users LIMIT 1").fetchone()["user_id"]
            conn.executemany(
                "INSERT INTO courses (user_id, name, term) VALUES (?, ?, ?)",
                [(user_id, name, term) for name, term in SAMPLE_COURSES],
            )
            log.info("写入样例课程 %d 条", len(SAMPLE_COURSES))
