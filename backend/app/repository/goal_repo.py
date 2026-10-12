"""考试目标数据访问层。"""

from app.repository.db import get_conn


def get_goal(course_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM exam_goals WHERE course_id = ?", (course_id,)).fetchone()
    return dict(row) if row else None


def save_goal(course_id: int, exam_date: str, target_score: int, available_minutes: int) -> dict:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO exam_goals (course_id, exam_date, target_score, available_minutes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(course_id) DO UPDATE SET
                exam_date = excluded.exam_date,
                target_score = excluded.target_score,
                available_minutes = excluded.available_minutes
            """,
            (course_id, exam_date, target_score, available_minutes),
        )
    return get_goal(course_id)
