"""课程数据访问层。"""

from app.repository.db import get_conn


def list_courses() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT course_id, name, term, created_at FROM courses ORDER BY course_id"
        ).fetchall()
    return [dict(row) for row in rows]


def find_by_name(name: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM courses WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def insert_course(name: str, term: str) -> dict:
    with get_conn() as conn:
        user_id = conn.execute("SELECT user_id FROM users LIMIT 1").fetchone()["user_id"]
        cur = conn.execute(
            "INSERT INTO courses (user_id, name, term) VALUES (?, ?, ?)", (user_id, name, term)
        )
        course_id = cur.lastrowid
    return {"course_id": course_id, "name": name, "term": term}
