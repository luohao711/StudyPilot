"""计划约束校验器：把可以程序检查的要求集中在一处，供生成修复和评测共用。

与 v0.5 的区别：v0.5 只做 Pydantic 结构校验；这里的校验在模型返回之后、
保存之前执行，失败时把 problem 清单反馈给模型做一次有限修复，仍失败则
降级为规则模板。检查项与报告中的成功标准一一对应。
"""

from dataclasses import dataclass
from datetime import date

from app.planning import config
from app.planning.context import PlanningInput


@dataclass(frozen=True)
class Issue:
    """一条可读的校验问题，code 用于日志和报告统计。"""

    code: str
    message: str


def _window_index(inp: PlanningInput) -> dict[date, int]:
    return {day: index for index, day in enumerate(inp.window_days)}


def validate_plan(inp: PlanningInput, tasks: list[dict], summary: str, notes: str) -> list[Issue]:
    """返回全部问题；空列表表示通过。"""
    issues: list[Issue] = []
    names = [point["name"] for point in inp.points]
    mastery = {point["name"]: point["mastery"] for point in inp.points}
    index_of = _window_index(inp)
    max_tasks = min(config.MAX_TASKS, len(names) * config.MAX_TASKS_PER_POINT)

    if not summary.strip():
        issues.append(Issue("SUMMARY_MISSING", "summary 不能为空，请用一句话说明计划思路"))
    if not tasks:
        return issues + [Issue("EMPTY_PLAN", "tasks 不能为空")]

    if len(tasks) > max_tasks:
        issues.append(Issue("TOO_MANY_TASKS", f"任务数 {len(tasks)} 超过上限 {max_tasks}"))

    point_counts: dict[str, int] = {}
    daily_minutes: dict[str, int] = {}
    daily_tasks: dict[str, int] = {}
    parsed: list[tuple[dict, date | None]] = []
    for order, task in enumerate(tasks, start=1):
        point = task["point"]
        if point not in names:
            issues.append(Issue("UNKNOWN_POINT", f"第{order}条使用了未给出的知识点“{point}”"))
        else:
            point_counts[point] = point_counts.get(point, 0) + 1
        if not task["title"].strip():
            issues.append(Issue("TITLE_MISSING", f"第{order}条任务标题为空"))
        if not task.get("reason", "").strip():
            issues.append(Issue("REASON_MISSING", f"第{order}条缺少安排理由"))
        elif len(task["reason"]) > 80:
            issues.append(Issue("REASON_TOO_LONG", f"第{order}条理由超过 80 字"))
        try:
            task_date = date.fromisoformat(task["date"])
        except ValueError:
            issues.append(Issue("DATE_INVALID", f"第{order}条日期格式错误：{task['date']}"))
            task_date = None
        if task_date is not None and task_date not in index_of:
            issues.append(Issue("DATE_OUT_OF_WINDOW",
                                f"第{order}条日期 {task['date']} 不在 {inp.window_days[0]} 至 {inp.window_days[-1]} 内"))
        minutes = task["minutes"]
        if not isinstance(minutes, int) or not (config.MIN_TASK_MINUTES <= minutes <= config.MAX_TASK_MINUTES):
            issues.append(Issue("MINUTES_RANGE",
                                f"第{order}条时长 {minutes} 应为 {config.MIN_TASK_MINUTES}—{config.MAX_TASK_MINUTES} 分钟"))
        else:
            daily_minutes[task["date"]] = daily_minutes.get(task["date"], 0) + minutes
            daily_tasks[task["date"]] = daily_tasks.get(task["date"], 0) + 1
        parsed.append((task, task_date))

    for point_name, count in point_counts.items():
        if count > config.MAX_TASKS_PER_POINT:
            issues.append(Issue("TOO_MANY_TASKS_PER_POINT",
                                f"知识点“{point_name}”安排了 {count} 个任务，最多 {config.MAX_TASKS_PER_POINT} 个"))

    for day, minutes in daily_minutes.items():
        if minutes > inp.available_minutes:
            issues.append(Issue("DAILY_OVERLOAD",
                                f"{day} 合计 {minutes} 分钟，超过每天可用 {inp.available_minutes} 分钟"))
    for day, count in daily_tasks.items():
        if count > config.MAX_TASKS_PER_DAY:
            issues.append(Issue("TOO_MANY_TASKS_PER_DAY",
                                f"{day} 安排了 {count} 个任务，每天最多 {config.MAX_TASKS_PER_DAY} 个"))

    covered = {task["point"] for task in tasks if task["point"] in names}
    weak = [name for name in names if mastery[name] < 60]
    strong = [name for name in names if mastery[name] >= 60]

    if inp.feasible:
        missing = [name for name in names if name not in covered]
        if missing:
            issues.append(Issue("MISSING_POINTS", "以下知识点没有安排任务：" + "、".join(missing)))
        if weak and strong:
            weak_days = [index_of[task_date] for task, task_date in parsed
                         if task_date is not None and task["point"] in weak]
            strong_days = [index_of[task_date] for task, task_date in parsed
                           if task_date is not None and task["point"] in strong]
            if weak_days and strong_days and sum(weak_days) / len(weak_days) > sum(strong_days) / len(strong_days) + 0.5:
                issues.append(Issue("PRIORITY_ORDER", "薄弱点安排得比高掌握度知识点更晚，请把薄弱点前移"))
    else:
        weakest = min(names, key=lambda name: mastery[name]) if names else None
        weakest_dates = [task_date for task, task_date in parsed if task["point"] == weakest and task_date]
        all_days = sorted(index_of[task_date] for _, task_date in parsed if task_date is not None)
        if weakest and not weakest_dates:
            issues.append(Issue("WEAKEST_MISSING", f"时间不足时至少应安排最薄弱的知识点“{weakest}”"))
        elif weakest and all_days and index_of[min(weakest_dates)] > all_days[len(all_days) // 2]:
            issues.append(Issue("PRIORITY_ORDER", f"最薄弱点“{weakest}”应安排在任务中位日期之前"))
        keywords = ["不足", "无法", "优先", "取舍", "时间", "至少"]
        if not notes.strip():
            issues.append(Issue("NOTES_MISSING", "预算不足时必须在 notes 中说明取舍"))
        elif not any(word in notes for word in keywords):
            issues.append(Issue("NOTES_MISSING", "notes 需要写明“时间不足/无法全部覆盖/优先安排”等原因"))

    return issues


def format_issues(issues: list[Issue]) -> str:
    return "\n".join(f"- [{issue.code}] {issue.message}" for issue in issues)
