"""学习计划与每日任务数据访问层。"""

from app.repository.db import get_conn


def latest_plan(course_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM study_plans WHERE course_id = ? ORDER BY plan_id DESC LIMIT 1", (course_id,)
        ).fetchone()
    return dict(row) if row else None


def create_plan(course_id: int, generator: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO study_plans (course_id, status, generator) VALUES (?, 'draft', ?)", (course_id, generator)
        )
        return cur.lastrowid


def confirm_plan(plan_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE study_plans SET status = 'confirmed', confirmed_at = datetime('now', 'localtime') WHERE plan_id = ?",
            (plan_id,),
        )


def replace_tasks(course_id: int, plan_id: int, tasks: list[dict]) -> int:
    """确认计划时覆盖该课程旧任务，避免重复排期。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM daily_tasks WHERE course_id = ?", (course_id,))
        conn.executemany(
            """
            INSERT INTO daily_tasks (plan_id, course_id, point_id, title, task_date, minutes, status)
            VALUES (?, ?, ?, ?, ?, ?, 'todo')
            """,
            [
                (plan_id, course_id, t.get("point_id"), t["title"], t["date"], int(t["minutes"]))
                for t in tasks
            ],
        )
    return len(tasks)


def list_tasks(course_id: int | None = None, status: str | None = None) -> list[dict]:
    sql = """
        SELECT t.*, k.name AS point_name, c.name AS course_name
        FROM daily_tasks t
        LEFT JOIN knowledge_points k ON k.point_id = t.point_id
        LEFT JOIN courses c ON c.course_id = t.course_id
        WHERE 1 = 1
    """
    params: list = []
    if course_id:
        sql += " AND t.course_id = ?"
        params.append(course_id)
    if status:
        sql += " AND t.status = ?"
        params.append(status)
    sql += " ORDER BY t.task_date, t.task_id"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_task(task_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM daily_tasks WHERE task_id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def complete_task(task_id: int, note: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE daily_tasks SET status = 'done', note = ?, completed_at = datetime('now', 'localtime') WHERE task_id = ?",
            (note, task_id),
        )


def reschedule_todo_tasks(course_id: int, exam_date: str) -> int:
    """把未完成任务重新排到考试日期之前，返回调整的任务数。"""
    from datetime import date, timedelta

    pending = [t for t in list_tasks(course_id, "todo")]
    if not pending:
        return 0
    limit = date.fromisoformat(exam_date) - timedelta(days=1)
    today = date.today()
    moved = 0
    with get_conn() as conn:
        for index, task in enumerate(pending):
            target = today + timedelta(days=index // 2)
            if target > limit:
                target = limit
            if target.isoformat() != task["task_date"]:
                conn.execute("UPDATE daily_tasks SET task_date = ? WHERE task_id = ?", (target.isoformat(), task["task_id"]))
                moved += 1
    return moved
