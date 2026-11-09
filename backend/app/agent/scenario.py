"""实验6样例场景：为“软件工程”课程写入最近14天的任务数据。

数据只写入本机 StudyPilot SQLite 数据库，可重复执行（会重置该课程的计划与任务）。
完成率为 5/12≈41.7%，掌握度最低的两个知识点是“软件测试”（30）和“数据库设计”（38）。
"""

import datetime

from app.repository import db as db_module
from app.repository.db import current_user_id, get_conn

SCENARIO_COURSE = "软件工程"
SCENARIO_TERM = "2026春"
SCENARIO_POINTS = [
    ("需求分析", 62),
    ("软件架构", 45),
    ("数据库设计", 38),
    ("软件测试", 30),
]
# (相对今天的天数偏移, 知识点, 任务标题, 时长分钟, 状态)
SCENARIO_TASKS = [
    (-12, "需求分析", "需求条目整理", 40, "done"),
    (-10, "软件架构", "架构分层练习", 45, "done"),
    (-8, "数据库设计", "ER 图练习", 30, "done"),
    (-6, "软件测试", "等价类划分练习", 40, "done"),
    (-4, "需求分析", "用例图练习", 35, "done"),
    (-9, "软件架构", "组件图练习", 30, "todo"),
    (-7, "数据库设计", "范式判断练习", 35, "todo"),
    (-5, "软件测试", "边界值分析", 30, "todo"),
    (-3, "数据库设计", "SQL 联表练习", 40, "todo"),
    (-2, "软件测试", "测试用例评审", 35, "todo"),
    (-1, "软件架构", "架构风格对比", 30, "todo"),
    (0, "需求分析", "需求评审准备", 30, "todo"),
]


def reset_scenario(course_name: str = SCENARIO_COURSE) -> dict:
    """重置样例课程的计划与任务，返回写入结果摘要。"""
    db_module.init_db()
    today = datetime.date.today()
    with get_conn() as conn:
        user_id = current_user_id(conn)
        row = conn.execute(
            "SELECT course_id FROM courses WHERE user_id = ? AND name = ?", (user_id, course_name)
        ).fetchone()
        if row:
            course_id = row["course_id"]
        else:
            cur = conn.execute(
                "INSERT INTO courses (user_id, name, term) VALUES (?, ?, ?)",
                (user_id, course_name, SCENARIO_TERM),
            )
            course_id = cur.lastrowid

        exam_date = (today + datetime.timedelta(days=30)).isoformat()
        goal = conn.execute("SELECT goal_id FROM exam_goals WHERE course_id = ?", (course_id,)).fetchone()
        if goal:
            conn.execute(
                "UPDATE exam_goals SET exam_date = ?, target_score = 85, available_minutes = 120 WHERE course_id = ?",
                (exam_date, course_id),
            )
        else:
            conn.execute(
                "INSERT INTO exam_goals (course_id, exam_date, target_score, available_minutes) VALUES (?, ?, 85, 120)",
                (course_id, exam_date),
            )

        point_ids: dict[str, int] = {}
        for name, mastery in SCENARIO_POINTS:
            point = conn.execute(
                "SELECT point_id FROM knowledge_points WHERE course_id = ? AND name = ?", (course_id, name)
            ).fetchone()
            if point:
                point_ids[name] = point["point_id"]
                conn.execute(
                    "UPDATE knowledge_points SET mastery = ?, updated_at = datetime('now', 'localtime') WHERE point_id = ?",
                    (mastery, point["point_id"]),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO knowledge_points (course_id, name, mastery) VALUES (?, ?, ?)",
                    (course_id, name, mastery),
                )
                point_ids[name] = cur.lastrowid

        conn.execute("DELETE FROM daily_tasks WHERE course_id = ?", (course_id,))
        conn.execute("DELETE FROM study_plans WHERE course_id = ?", (course_id,))
        cur = conn.execute(
            "INSERT INTO study_plans (course_id, status, generator, confirmed_at) "
            "VALUES (?, 'confirmed', 'seed', datetime('now', 'localtime'))",
            (course_id,),
        )
        plan_id = cur.lastrowid

        done = 0
        for offset, point_name, title, minutes, status in SCENARIO_TASKS:
            task_date = (today + datetime.timedelta(days=offset)).isoformat()
            completed_at = f"{task_date} 18:00:00" if status == "done" else None
            conn.execute(
                "INSERT INTO daily_tasks (plan_id, course_id, point_id, title, task_date, minutes, status, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (plan_id, course_id, point_ids[point_name], title, task_date, minutes, status, completed_at),
            )
            if status == "done":
                done += 1

    return {
        "course": course_name,
        "course_id": course_id,
        "plan_id": plan_id,
        "tasks": len(SCENARIO_TASKS),
        "done": done,
        "pending": len(SCENARIO_TASKS) - done,
        "completion_rate": round(done / len(SCENARIO_TASKS) * 100, 1),
        "points": len(SCENARIO_POINTS),
        "today": today.isoformat(),
    }
