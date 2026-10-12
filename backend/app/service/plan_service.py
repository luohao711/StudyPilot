"""学习计划服务：把知识点拆分为每日任务，并在用户确认后保存。"""

from datetime import date

from app.errors import BusinessError
from app.logging_conf import log
from app.repository import course_repo, goal_repo, knowledge_repo, plan_repo
from app.service import ai_service


def generate_plan(course_id: int, available_minutes: int | None = None, start_date: str | None = None) -> dict:
    """生成候选计划（初稿：直接按知识点生成任务，由用户确认后再保存）。"""
    points = knowledge_repo.list_points(course_id)
    goal = goal_repo.get_goal(course_id)
    available_minutes = available_minutes or goal["available_minutes"]

    avg_mastery = sum(p["mastery"] for p in points) / len(points)

    tasks = []
    for point in points:
        tasks.append(
            {
                "point_id": point["point_id"],
                "title": f"复习{point['name']}",
                "date": date.today().isoformat(),
                "minutes": 45,
                "reason": f"掌握度 {point['mastery']}%，低于平均 {avg_mastery:.0f}%",
            }
        )

    log.info("生成候选计划：课程 %s，共 %d 个任务", course_id, len(tasks))
    return {
        "course_id": course_id,
        "exam_date": goal["exam_date"],
        "generator": "llm",
        "ai_available": True,
        "tasks": tasks,
    }


def confirm_plan(course_id: int, tasks: list[dict]) -> dict:
    if not course_repo.get_course(course_id):
        raise BusinessError("课程不存在，请先创建课程", 404, "COURSE_NOT_FOUND")
    if not tasks:
        raise BusinessError("计划内容为空，无法确认", 400, "EMPTY_PLAN")

    plan_id = plan_repo.create_plan(course_id, "rule_template")
    count = plan_repo.replace_tasks(course_id, plan_id, tasks)
    plan_repo.confirm_plan(plan_id)
    log.info("确认学习计划：课程 %s，plan_id=%s，共 %d 个任务", course_id, plan_id, count)
    return {"plan_id": plan_id, "status": "confirmed", "task_count": count}


def current_plan(course_id: int) -> dict:
    plan = plan_repo.latest_plan(course_id)
    return {
        "plan": plan,
        "tasks": plan_repo.list_tasks(course_id),
    }
