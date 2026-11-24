"""输入组织：从项目数据库读取课程、考试目标和知识点，计算计划窗口与预算。

所有“能不能生成”的判断都在调用模型之前由程序完成；返回的错误码和提示
直接作为失败反馈展示给用户，不消耗模型调用。
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.errors import BusinessError
from app.planning import config
from app.repository import course_repo, goal_repo, knowledge_repo


def _parse_date(value: str | None, field_name: str) -> date:
    if not value:
        raise BusinessError(f"缺少{field_name}，请先补充", 400, "INVALID_START_DATE")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise BusinessError(f"{field_name}格式应为 YYYY-MM-DD", 400, "INVALID_START_DATE") from None


@dataclass
class PlanningInput:
    """一次计划生成的完整输入，生成后不再访问数据库。"""

    course_id: int
    course_name: str
    term: str
    exam_date: date
    target_score: int
    available_minutes: int
    start_date: date
    today: date
    points: list[dict]
    window_days: list[date] = field(default_factory=list)
    feasible: bool = True

    @property
    def total_budget(self) -> int:
        return len(self.window_days) * self.available_minutes

    @property
    def min_required_minutes(self) -> int:
        return len(self.points) * config.MIN_PER_POINT_MINUTES

    @property
    def weak_points(self) -> list[dict]:
        return [point for point in self.points if point["mastery"] < 60]

    @property
    def strong_points(self) -> list[dict]:
        return [point for point in self.points if point["mastery"] >= 60]


def load_input(
    course_id: int,
    *,
    start_date: str | None = None,
    available_minutes: int | None = None,
    today: date | str | None = None,
) -> PlanningInput:
    """读取并校验生成计划所需的输入；信息不足或超出范围时抛出 BusinessError。"""
    course = course_repo.get_course(course_id)
    if not course:
        raise BusinessError("课程不存在，请先创建课程", 404, "COURSE_NOT_FOUND")
    goal = goal_repo.get_goal(course_id)
    if not goal:
        raise BusinessError("请先设置考试目标（考试日期、目标分和每天可用时间），再生成学习计划", 400, "GOAL_NOT_FOUND")

    points = knowledge_repo.list_points(course_id)
    if not points:
        raise BusinessError("该课程还没有知识点掌握度，请先添加知识点再生成计划", 400, "EMPTY_KNOWLEDGE_POINTS")
    if len(points) > config.MAX_POINTS:
        raise BusinessError(
            f"知识点数量为 {len(points)}，超过原型单次处理上限 {config.MAX_POINTS}，请拆分为多个学习阶段",
            400,
            "TOO_MANY_POINTS",
        )

    minutes = available_minutes if available_minutes is not None else goal["available_minutes"]
    if not isinstance(minutes, int) or not (config.MIN_AVAILABLE_MINUTES <= minutes <= config.MAX_AVAILABLE_MINUTES):
        raise BusinessError(
            f"每天可用时间应为 {config.MIN_AVAILABLE_MINUTES}—{config.MAX_AVAILABLE_MINUTES} 分钟，当前为 {minutes}",
            400,
            "INVALID_AVAILABLE_MINUTES",
        )

    base = date.today()
    if isinstance(today, str):
        base = _parse_date(today, "基准日期")
    elif isinstance(today, date):
        base = today

    start = _parse_date(start_date, "开始日期") if start_date else base
    if start < base:
        start = base

    exam = date.fromisoformat(goal["exam_date"])
    if exam <= start:
        raise BusinessError("考试日期之前没有可用时间，请先调整考试目标或开始日期", 400, "NO_AVAILABLE_DAY")

    days: list[date] = []
    cursor = start
    while cursor < exam and len(days) < config.MAX_PLAN_DAYS:
        days.append(cursor)
        cursor += timedelta(days=1)
    if not days:
        raise BusinessError("考试日期之前没有可用时间，请先调整考试目标或开始日期", 400, "NO_AVAILABLE_DAY")

    feasible = (len(points) * config.MIN_PER_POINT_MINUTES <= len(days) * minutes
                and len(points) <= len(days) * config.MAX_TASKS_PER_DAY)
    return PlanningInput(
        course_id=course_id,
        course_name=course["name"],
        term=course["term"],
        exam_date=exam,
        target_score=goal["target_score"],
        available_minutes=minutes,
        start_date=start,
        today=base,
        points=points,
        window_days=days,
        feasible=feasible,
    )


def prompt_context(inp: PlanningInput) -> dict:
    """把输入整理成提示词需要的文本块，数字和窗口均由程序计算。"""
    point_lines = "\n".join(
        f"- {point['name']}：掌握度 {point['mastery']}%" for point in inp.points
    )
    weak = "、".join(f"{p['name']}({p['mastery']}%)" for p in inp.weak_points) or "无"
    return {
        "course": inp.course_name,
        "term": inp.term,
        "exam_date": inp.exam_date.isoformat(),
        "start_date": inp.start_date.isoformat(),
        "window": f"{inp.window_days[0].isoformat()} 至 {inp.window_days[-1].isoformat()}（共 {len(inp.window_days)} 天）",
        "window_first": inp.window_days[0].isoformat(),
        "window_last": inp.window_days[-1].isoformat(),
        "day_count": len(inp.window_days),
        "available_minutes": inp.available_minutes,
        "total_budget": inp.total_budget,
        "min_required_minutes": inp.min_required_minutes,
        "max_tasks": min(config.MAX_TASKS, len(inp.points) * config.MAX_TASKS_PER_POINT),
        "max_tasks_per_day": config.MAX_TASKS_PER_DAY,
        "max_tasks_per_point": config.MAX_TASKS_PER_POINT,
        "feasible": "可以覆盖全部知识点" if inp.feasible else "无法覆盖全部知识点，必须按薄弱程度取舍并在 notes 中说明",
        "feasible_note": "可以覆盖全部知识点，每个知识点至少安排1个任务" if inp.feasible else "预算不足，无法覆盖全部知识点：按掌握度从低到高安排尽可能多的知识点，并在 notes 中说明",
        "points": point_lines,
        "weak_points": weak,
    }
