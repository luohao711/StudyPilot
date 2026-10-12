"""课程数据访问层。"""

from app.repository.db import get_conn


def list_courses() -> list[dict]:
    """返回课程及其考试目标、平均掌握度和待完成任务数。"""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.course_id, c.name, c.term, c.created_at,
                   g.exam_date, g.target_score, g.available_minutes,
                   (SELECT COUNT(*) FROM knowledge_points k WHERE k.course_id = c.course_id) AS point_count,
                   (SELECT ROUND(AVG(k.mastery)) FROM knowledge_points k WHERE k.course_id = c.course_id) AS avg_mastery,
                   (SELECT COUNT(*) FROM daily_tasks t WHERE t.course_id = c.course_id AND t.status = 'todo') AS todo_count
            FROM courses c
            LEFT JOIN exam_goals g ON g.course_id = c.course_id
            ORDER BY c.course_id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_course(course_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM courses WHERE course_id = ?", (course_id,)).fetchone()
    return dict(row) if row else None


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
