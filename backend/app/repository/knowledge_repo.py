"""知识点数据访问层。"""

from app.repository.db import get_conn


def list_points(course_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM knowledge_points WHERE course_id = ? ORDER BY mastery, point_id", (course_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_point(point_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM knowledge_points WHERE point_id = ?", (point_id,)).fetchone()
    return dict(row) if row else None


def find_by_name(course_id: int, name: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM knowledge_points WHERE course_id = ? AND name = ?", (course_id, name)
        ).fetchone()
    return dict(row) if row else None


def insert_point(course_id: int, name: str, mastery: int) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO knowledge_points (course_id, name, mastery) VALUES (?, ?, ?)", (course_id, name, mastery)
        )
        point_id = cur.lastrowid
    return get_point(point_id)


def update_mastery(point_id: int, mastery: int) -> dict:
    with get_conn() as conn:
        conn.execute(
            "UPDATE knowledge_points SET mastery = ?, updated_at = datetime('now', 'localtime') WHERE point_id = ?",
            (mastery, point_id),
        )
    return get_point(point_id)


def average_mastery(course_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT AVG(mastery) AS avg_mastery, COUNT(*) AS total FROM knowledge_points WHERE course_id = ?",
            (course_id,),
        ).fetchone()
    return round(row["avg_mastery"]) if row["total"] else 0
