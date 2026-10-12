"""SQLite 连接、建表和样例数据处理。

数据库访问失败统一转成 DATABASE_UNAVAILABLE 业务异常，避免接口直接抛出底层错误。
"""

import os
import sqlite3
from contextlib import contextmanager

from app.errors import BusinessError
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

CREATE TABLE IF NOT EXISTS exam_goals (
    goal_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id         INTEGER NOT NULL UNIQUE REFERENCES courses(course_id) ON DELETE CASCADE,
    exam_date         TEXT NOT NULL,
    target_score      INTEGER NOT NULL,
    available_minutes INTEGER NOT NULL DEFAULT 120,
    created_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS knowledge_points (
    point_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id  INTEGER NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    mastery    INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (course_id, name)
);

CREATE TABLE IF NOT EXISTS study_plans (
    plan_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id    INTEGER NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'draft',
    generator    TEXT NOT NULL DEFAULT 'rule_template',
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS daily_tasks (
    task_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id      INTEGER NOT NULL REFERENCES study_plans(plan_id) ON DELETE CASCADE,
    course_id    INTEGER NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,
    point_id     INTEGER REFERENCES knowledge_points(point_id),
    title        TEXT NOT NULL,
    task_date    TEXT NOT NULL,
    minutes      INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'todo',
    note         TEXT NOT NULL DEFAULT '',
    completed_at TEXT
);
"""

TABLES = ["daily_tasks", "study_plans", "knowledge_points", "exam_goals", "courses", "users"]


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn():
    try:
        conn = connect()
    except sqlite3.Error as exc:
        log.exception("数据库连接失败：%s", exc)
        raise BusinessError("数据库暂时不可用，请稍后重试", 500, "DATABASE_UNAVAILABLE") from exc
    try:
        yield conn
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        log.exception("数据库操作失败：%s", exc)
        raise BusinessError("数据库暂时不可用，请稍后重试", 500, "DATABASE_UNAVAILABLE") from exc
    finally:
        conn.close()


def init_db() -> None:
    """建表并保证存在一个用户；样例数据通过 python3 -m app.seed 单独写入。"""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] == 0:
            conn.execute("INSERT INTO users (name) VALUES (?)", ("罗心月",))
            log.info("初始化用户：罗心月")


def current_user_id(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT user_id FROM users LIMIT 1").fetchone()["user_id"]


def clear_all() -> None:
    with get_conn() as conn:
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM sqlite_sequence")
    log.info("已清空全部业务数据")
