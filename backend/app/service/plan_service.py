"""学习计划服务：把知识点拆分为每日任务，并在用户确认后保存。

生成候选计划时优先调用模型服务；模型服务不可用（未配置或调用失败）时，
改用规则模板降级生成，保证非AI流程也能走通。
"""

from datetime import date, timedelta

from app.errors import BusinessError
from app.logging_conf import log
from app.repository import course_repo, goal_repo, knowledge_repo, plan_repo
from app.service import ai_service

MIN_TASK_MINUTES = 15
MAX_PLAN_DAYS = 7


def _parse_start(start_date: str | None) -> date:
    if not start_date:
        return date.today()
    try:
        return date.fromisoformat(start_date)
    except ValueError:
        raise BusinessError("开始日期格式应为 YYYY-MM-DD", 400, "INVALID_START_DATE") from None


def _plan_days(start: date, exam_date: date) -> list[date]:
    """可选排期日期：从开始日期到考试前一天，最多 MAX_PLAN_DAYS 天。"""
    days = []
    day = start
    while day < exam_date and len(days) < MAX_PLAN_DAYS:
        days.append(day)
        day += timedelta(days=1)
    return days


def _rule_template_tasks(points: list[dict], days: list[date], available_minutes: int) -> list[dict]:
    """规则模板：掌握度低的知识点优先，任务时长按薄弱程度分配，每天不超出可用时间。"""
    weights = [(100 - point["mastery"]) + 10 for point in points]
    total_weight = sum(weights)

    tasks: list[dict] = []
    load: dict[int, int] = {}
    cursor = 0
    for point, weight in zip(points, weights):
        minutes = max(MIN_TASK_MINUTES, round(available_minutes * weight / total_weight / 5) * 5)
        while cursor < len(days) and load.get(cursor, 0) + minutes > available_minutes:
            cursor += 1
        if cursor >= len(days):
            log.warning("考试前可用天数不足，剩余知识点未排入计划：%s", point["name"])
            break
        tasks.append(
            {
                "point_id": point["point_id"],
                "title": f"复习{point['name']}",
                "date": days[cursor].isoformat(),
                "minutes": minutes,
                "reason": f"掌握度 {point['mastery']}%，安排 {minutes} 分钟"
                + ("，优先安排" if point["mastery"] < 60 else ""),
            }
        )
        load[cursor] = load.get(cursor, 0) + minutes
        cursor += 1
    return tasks


def generate_plan(course_id: int, available_minutes: int | None = None, start_date: str | None = None) -> dict:
    """生成候选计划，返回内容由用户确认后再保存。"""
    if not course_repo.get_course(course_id):
        raise BusinessError("课程不存在，请先创建课程", 404, "COURSE_NOT_FOUND")
    goal = goal_repo.get_goal(course_id)
    if not goal:
        raise BusinessError("请先设置考试目标，再生成学习计划", 400, "GOAL_NOT_FOUND")

    points = knowledge_repo.list_points(course_id)
    if not points:
        raise BusinessError("该课程还没有知识点掌握度，请先添加知识点", 400, "EMPTY_KNOWLEDGE_POINTS")

    exam_date = date.fromisoformat(goal["exam_date"])
    available_minutes = available_minutes or goal["available_minutes"]
    days = _plan_days(_parse_start(start_date), exam_date)
    if not days:
        raise BusinessError("考试日期之前没有可用时间，请先调整考试目标", 400, "NO_AVAILABLE_DAY")

    generator = "llm"
    tasks: list[dict] | None = None
    if ai_service.is_available():
        try:
            tasks = ai_service.generate_plan_with_llm(course_id, points, available_minutes)
        except BusinessError as exc:
            log.warning("模型服务不可用（%s），课程 %s 改用规则模板生成", exc.code, course_id)
    if tasks is None:
        generator = "rule_template"
        tasks = _rule_template_tasks(points, days, available_minutes)

    if not tasks:
        raise BusinessError("可用时间不足，无法生成任务，请增加每天可用时间", 400, "NO_TASK_GENERATED")

    log.info("生成候选计划：课程 %s，生成方式 %s，共 %d 个任务", course_id, generator, len(tasks))
    return {
        "course_id": course_id,
        "exam_date": goal["exam_date"],
        "available_minutes": available_minutes,
        "generator": generator,
        "ai_available": ai_service.is_available(),
        "tasks": tasks,
    }


def confirm_plan(course_id: int, tasks: list[dict]) -> dict:
    if not course_repo.get_course(course_id):
        raise BusinessError("课程不存在，请先创建课程", 404, "COURSE_NOT_FOUND")
    if not tasks:
        raise BusinessError("计划内容为空，无法确认，请先生成或手工添加任务", 400, "EMPTY_PLAN")

    goal = goal_repo.get_goal(course_id)
    if goal:
        for task in tasks:
            if task["date"] >= goal["exam_date"]:
                raise BusinessError(f"任务“{task['title']}”的日期晚于考试日期，请先修改", 400, "TASK_DATE_AFTER_EXAM")

    plan_id = plan_repo.create_plan(course_id, "rule_template")
    count = plan_repo.replace_tasks(course_id, plan_id, tasks)
    plan_repo.confirm_plan(plan_id)
    log.info("确认学习计划：课程 %s，plan_id=%s，共 %d 个任务", course_id, plan_id, count)
    return {"plan_id": plan_id, "status": "confirmed", "task_count": count}


def current_plan(course_id: int) -> dict:
    plan = plan_repo.latest_plan(course_id)
    return {"plan": plan, "tasks": plan_repo.list_tasks(course_id)}
