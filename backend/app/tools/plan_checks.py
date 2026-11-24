"""评测用独立检查器：只根据评测样例和输出检查，不调用生成器内部逻辑。

检查项分成程序可判定的硬约束（日期、每天时长、覆盖、优先级、任务时长、
不可行时的说明）和生成方式相关的附加项（summary）。人工评分单独保存在
human_scores.json，由评测脚本合并汇总。
"""

from datetime import date

from app.planning import config


def window_days(case: dict) -> list[date]:
    start = date.fromisoformat(case["input"]["start_date"])
    exam = date.fromisoformat(case["input"]["exam_date"])
    days = []
    cursor = start
    while cursor < exam and len(days) < config.MAX_PLAN_DAYS:
        days.append(cursor)
        cursor = date.fromordinal(cursor.toordinal() + 1)
    return days


def is_feasible(case: dict) -> bool:
    days = window_days(case)
    points = case["input"]["points"]
    minutes = int(case["input"]["available_minutes"] or 0)
    return len(points) * config.MIN_PER_POINT_MINUTES <= len(days) * minutes


def _entry(passed, detail: str = "") -> dict:
    return {"passed": passed, "detail": detail}


def check_plan(case: dict, tasks: list[dict], *, summary: str = "", notes: str = "",
               generator: str = "") -> dict:
    """返回逐项检查结果；passed 为所有适用检查项均通过。"""
    names = [point["name"] for point in case["input"]["points"]]
    mastery = {point["name"]: point["mastery"] for point in case["input"]["points"]}
    days = window_days(case)
    available = int(case["input"]["available_minutes"])
    feasible = is_feasible(case)
    checks: dict[str, dict] = {}
    issues: list[str] = []

    def add(code: str, passed, detail: str = "") -> None:
        checks[code] = _entry(passed, detail)
        if passed is False:
            issues.append(code)

    if not tasks:
        add("schema", False, "没有任务")
        return {"passed": False, "checks": checks, "issues": issues, "coverage_rate": 0,
                "task_count": 0, "total_minutes": 0}

    # 1. 结构：字段、日期格式、知识点名称、时长类型
    schema_problems = []
    parsed: list[dict] = []
    for index, task in enumerate(tasks):
        point = str(task.get("point", "")).strip()
        title = str(task.get("title", "")).strip()
        date_text = str(task.get("date", "")).strip()
        minutes = task.get("minutes")
        if point not in names:
            schema_problems.append(f"第{index + 1}条知识点“{point}”不在输入中")
        if not title:
            schema_problems.append(f"第{index + 1}条缺少任务名称")
        try:
            task_date = date.fromisoformat(date_text)
        except ValueError:
            schema_problems.append(f"第{index + 1}条日期格式错误")
            task_date = None
        if not isinstance(minutes, int) or isinstance(minutes, bool):
            schema_problems.append(f"第{index + 1}条时长不是整数")
        parsed.append({"point": point, "title": title, "date": task_date, "minutes": minutes})
    add("schema", not schema_problems, "；".join(schema_problems) or "字段完整")

    # 2. 日期窗口：开始日期至考试前一天
    exam = date.fromisoformat(case["input"]["exam_date"])
    date_problems = [f"第{i + 1}条日期越界（{t['date']}）" for i, t in enumerate(parsed)
                     if t["date"] is None or not (days[0] <= t["date"] < exam)]
    add("date", not date_problems, "；".join(date_problems) or f"{days[0]} 至 {days[-1]}")

    # 3. 每天总时长不超过可用时间
    daily: dict[date, int] = {}
    for task in parsed:
        if task["date"] is not None and isinstance(task["minutes"], int):
            daily[task["date"]] = daily.get(task["date"], 0) + task["minutes"]
    overload = [f"{day} 共 {minutes} 分钟" for day, minutes in daily.items() if minutes > available]
    add("daily_cap", not overload, "；".join(overload) or f"每天不超过 {available} 分钟")

    # 4. 知识点覆盖：可覆盖时要求全覆盖；不可覆盖时不适用
    covered = {task["point"] for task in parsed if task["point"] in names}
    coverage_rate = round(len(covered) / len(names), 3) if names else 0
    if feasible:
        missing = [name for name in names if name not in covered]
        add("coverage", not missing, "缺少：" + "、".join(missing) if missing else f"覆盖 {len(covered)}/{len(names)}")
    else:
        add("coverage", None, f"窗口预算不足，不要求全覆盖（覆盖 {len(covered)}/{len(names)}）")

    # 5. 优先级：可覆盖时薄弱点平均排期不晚于掌握度高的点；不可覆盖时最薄弱点必须被安排
    weak = [name for name in names if mastery[name] < 60]
    strong = [name for name in names if mastery[name] >= 60]
    if feasible and weak and strong:
        index = {day: position for position, day in enumerate(days)}
        weak_dates = [index[t["date"]] for t in parsed if t["point"] in weak and t["date"] in index]
        strong_dates = [index[t["date"]] for t in parsed if t["point"] in strong and t["date"] in index]
        if weak_dates and strong_dates:
            weak_mean = sum(weak_dates) / len(weak_dates)
            strong_mean = sum(strong_dates) / len(strong_dates)
            add("priority", weak_mean <= strong_mean,
                f"薄弱点平均第 {weak_mean:.1f} 天，高掌握度平均第 {strong_mean:.1f} 天")
        else:
            add("priority", None, "缺少薄弱点或高掌握度任务，无法比较")
    elif not feasible:
        weakest = min(names, key=lambda name: mastery[name]) if names else None
        weakest_dates = [t["date"] for t in parsed if t["point"] == weakest and t["date"] is not None]
        all_dates = sorted(t["date"] for t in parsed if t["date"] is not None)
        if weakest and weakest_dates and all_dates:
            median = all_dates[len(all_dates) // 2]
            add("priority", min(weakest_dates) <= median,
                f"最薄弱点“{weakest}”首次安排 {min(weakest_dates)}，任务中位日期 {median}")
        else:
            add("priority", False, f"未安排最薄弱点“{weakest}”")
    else:
        add("priority", None, "没有同时存在薄弱点和高掌握度点，不适用")

    # 6. 单个任务时长范围
    bad_minutes = [f"第{i + 1}条 {t['minutes']} 分钟" for i, t in enumerate(parsed)
                   if not isinstance(t["minutes"], int) or not (config.MIN_TASK_MINUTES <= t["minutes"] <= config.MAX_TASK_MINUTES)]
    add("minutes", not bad_minutes, "；".join(bad_minutes) or f"{config.MIN_TASK_MINUTES}—{config.MAX_TASK_MINUTES} 分钟")

    # 7. 总时长不超过窗口总预算
    total_minutes = sum(t["minutes"] for t in parsed if isinstance(t["minutes"], int))
    budget = len(days) * available
    add("capacity", total_minutes <= budget, f"总 {total_minutes} 分钟，预算 {budget} 分钟")

    # 8. 不可覆盖时必须在 notes 中说明取舍
    if not feasible:
        keywords = ["不足", "无法", "优先", "取舍", "时间", "至少"]
        noted = bool(notes.strip()) and any(word in notes for word in keywords)
        add("notes", noted, "notes=" + (notes[:80] or "（空）"))
    else:
        add("notes", None, "可覆盖全部知识点，不强制 notes")

    # 9. 模型生成的计划应有 summary（规则模板没有该字段，记为不适用）
    if generator.startswith("llm"):
        add("summary", bool(summary.strip()), summary[:80] or "（空）")
    else:
        add("summary", None, "规则模板无 summary 字段，不适用")

    applicable = [entry["passed"] for entry in checks.values() if entry["passed"] is not None]
    passed = bool(applicable) and all(applicable)
    return {
        "passed": passed,
        "checks": checks,
        "issues": issues,
        "coverage_rate": coverage_rate,
        "task_count": len(parsed),
        "total_minutes": total_minutes,
    }
