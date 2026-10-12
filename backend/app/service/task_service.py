"""每日任务业务层：完成任务、更新掌握度、调整后续计划。"""

from app.errors import BusinessError
from app.logging_conf import log
from app.repository import goal_repo, knowledge_repo, plan_repo


def list_tasks(course_id: int | None = None, status: str | None = None) -> list[dict]:
    return plan_repo.list_tasks(course_id, status)


def suggest_mastery(task_id: int) -> dict:
    """系统根据任务情况给出掌握度建议值，最终由用户确认。"""
    task = plan_repo.get_task(task_id)
    if not task:
        raise BusinessError("任务不存在", 404, "TASK_NOT_FOUND")
    point = knowledge_repo.get_point(task["point_id"]) if task["point_id"] else None
    current = point["mastery"] if point else 0
    return {"point_name": point["name"] if point else task["title"], "current_mastery": current, "suggested_mastery": min(100, current + 10)}


def complete_task(task_id: int, mastery: int, note: str = "") -> dict:
    task = plan_repo.get_task(task_id)
    if not task:
        raise BusinessError("任务不存在或已被删除", 404, "TASK_NOT_FOUND")
    if task["status"] == "done":
        raise BusinessError("该任务已完成，无需重复提交", 409, "TASK_ALREADY_DONE")
    if not 0 <= mastery <= 100:
        raise BusinessError("掌握度应在 0—100 之间", 400, "INVALID_MASTERY")

    plan_repo.complete_task(task_id, note)

    old_mastery = None
    if task["point_id"]:
        point = knowledge_repo.get_point(task["point_id"])
        old_mastery = point["mastery"] if point else None
        knowledge_repo.update_mastery(task["point_id"], mastery)

    log.info("完成任务 %s（%s），掌握度 %s%% -> %s%%", task_id, task["title"], old_mastery, mastery)
    return {
        "task_id": task_id,
        "status": "done",
        "point_id": task["point_id"],
        "old_mastery": old_mastery,
        "new_mastery": mastery,
    }


def adjust_plan(course_id: int) -> dict:
    """把未完成任务重新排到考试日期之前，返回调整结果。"""
    goal = goal_repo.get_goal(course_id)
    if not goal:
        raise BusinessError("请先设置考试目标，再调整计划", 400, "GOAL_NOT_FOUND")
    moved = plan_repo.reschedule_todo_tasks(course_id, goal["exam_date"])
    log.info("调整计划：课程 %s，重新排期 %d 个任务", course_id, moved)
    return {"course_id": course_id, "moved": moved, "exam_date": goal["exam_date"]}
