"""计划生成主链路：v0.5 直接调用模型，v0.6 增加约束预处理、校验、修复和降级。

v1 路径（初版）保留为对照：只做 Pydantic 结构化校验，业务约束交给评测检查；
v2 路径（修订版）在模型返回后用 validator 检查，失败时把问题反馈给模型修复
一次，仍失败或模型调用失败则改用规则模板降级，保证用户始终能得到可执行计划。
"""

import time
from datetime import date

from app.errors import BusinessError
from app.planning import config, prompts, validator
from app.planning.context import PlanningInput, load_input
from app.planning.runlog import make_logger, new_run_id
from app.planning.schema import PlanOut
from app.repository import plan_repo
from app.repository.db import get_conn
from app.service import llm_client


def _meta(meta: dict, latency_ms: int) -> dict:
    usage = meta.get("usage", {}) or {}
    prompt_tokens = int(usage.get("prompt_tokens", 0))
    completion_tokens = int(usage.get("completion_tokens", 0))
    cost = (prompt_tokens * config.PRICE_INPUT_PER_M + completion_tokens * config.PRICE_OUTPUT_PER_M) / 1_000_000
    return {
        "model": meta.get("model", config.MODEL),
        "latency_ms": latency_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": int(usage.get("total_tokens", 0)),
        "cost_yuan": round(cost, 6),
        "calls": 2 if meta.get("retried") else 1,
        "retried": bool(meta.get("retried")),
    }


def _add_usage(total: dict, meta: dict, latency_ms: int) -> None:
    """累计多次模型调用的 token、费用和耗时。"""
    usage = meta.get("usage", {}) or {}
    total["prompt_tokens"] += int(usage.get("prompt_tokens", 0))
    total["completion_tokens"] += int(usage.get("completion_tokens", 0))
    total["total_tokens"] += int(usage.get("total_tokens", 0))
    total["latency_ms"] += latency_ms
    total["calls"] += 2 if meta.get("retried") else 1
    total["model"] = meta.get("model", config.MODEL)


def _meta_from_total(total: dict) -> dict:
    cost = (total["prompt_tokens"] * config.PRICE_INPUT_PER_M
            + total["completion_tokens"] * config.PRICE_OUTPUT_PER_M) / 1_000_000
    return {
        "model": total.get("model", config.MODEL),
        "latency_ms": int(total["latency_ms"]),
        "prompt_tokens": total["prompt_tokens"],
        "completion_tokens": total["completion_tokens"],
        "total_tokens": total["total_tokens"],
        "cost_yuan": round(cost, 6),
        "calls": total["calls"],
        "retried": total["calls"] > 1,
    }


def normalize_tasks(tasks: list, inp: PlanningInput) -> list[dict]:
    """把模型返回的任务转成项目内部格式，知识点名称映射为 point_id。"""
    point_ids = {point["name"]: point["point_id"] for point in inp.points}
    normalized = []
    for item in tasks:
        point_name = item.point.strip()
        normalized.append({
            "point": point_name,
            "point_id": point_ids.get(point_name),
            "title": item.title.strip(),
            "date": item.date.strip(),
            "minutes": int(item.minutes),
            "reason": item.reason.strip(),
        })
    # 只按日期稳定排序，保留模型在同一天内给出的先后顺序，便于检查薄弱点是否优先。
    normalized.sort(key=lambda task: task["date"])
    return normalized


def save_candidate(course_id: int, tasks: list[dict], generator: str) -> dict:
    """把生成结果保存为 draft 计划，供用户在页面上查看后再确认。"""
    plan_id = plan_repo.create_plan(course_id, generator)
    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO daily_tasks (plan_id, course_id, point_id, title, task_date, minutes, status)
            VALUES (?, ?, ?, ?, ?, ?, 'todo')
            """,
            [
                (plan_id, course_id, task.get("point_id"), task["title"], task["date"], int(task["minutes"]))
                for task in tasks
            ],
        )
    return {"plan_id": plan_id, "status": "draft", "task_count": len(tasks)}


def rule_fallback(inp: PlanningInput) -> tuple[list[dict], str, str]:
    """规则降级：按掌握度从低到高、每天不超过可用时间和任务数上限排期。"""
    tasks: list[dict] = []
    omitted: list[str] = []
    day_index = 0
    day_minutes = 0
    day_tasks = 0

    def next_day() -> None:
        nonlocal day_index, day_minutes, day_tasks
        day_index += 1
        day_minutes = 0
        day_tasks = 0

    for point in sorted(inp.points, key=lambda item: (item["mastery"], item["point_id"])):
        minutes = 25 if point["mastery"] < 60 else 20
        while day_index < len(inp.window_days):
            if day_minutes + minutes <= inp.available_minutes and day_tasks < config.MAX_TASKS_PER_DAY:
                break
            next_day()
        if day_index >= len(inp.window_days):
            omitted.append(point["name"])
            continue
        tasks.append({
            "point": point["name"],
            "point_id": point["point_id"],
            "title": f"复习{point['name']}：梳理要点并做练习题",
            "date": inp.window_days[day_index].isoformat(),
            "minutes": minutes,
            "reason": f"掌握度 {point['mastery']}%，{'优先安排' if point['mastery'] < 60 else '保持记忆'}",
        })
        day_minutes += minutes
        day_tasks += 1

    covered = len({task["point"] for task in tasks})
    if omitted:
        summary = f"按掌握度从低到高安排 {len(tasks)} 个复习任务，覆盖 {covered}/{len(inp.points)} 个知识点。"
        notes = (f"时间不足，无法全部覆盖：已优先安排 {len(tasks)} 个最薄弱的知识点，暂未安排 "
                 + "、".join(omitted) + "；建议增加每天可用时间或提前开始复习。")
    else:
        summary = f"按掌握度从低到高安排 {len(tasks)} 个复习任务，覆盖全部 {len(inp.points)} 个知识点。"
        notes = "规则降级计划：按掌握度从低到高安排，每天不超过可用时间。"
    return tasks, summary, notes


def _run_v1(inp: PlanningInput, logger, run_id: str, started: float) -> dict:
    messages = prompts.plan_messages(inp, "v1")
    logger.event("model_call", version="v1", messages=len(messages), prompt_chars=len(messages[0]["content"]))
    try:
        output, meta = llm_client.chat_json(
            messages,
            PlanOut,
            model=config.MODEL,
            temperature=config.TEMPERATURE,
            max_tokens=config.MAX_TOKENS,
            timeout=config.TIMEOUT,
        )
    except BusinessError as exc:
        logger.event("run_end", status="llm_error", error_code=exc.code, error=exc.message,
                     duration_ms=int((time.time() - started) * 1000))
        raise
    tasks = normalize_tasks(output.tasks, inp)
    info = _meta(meta, int((time.time() - started) * 1000))
    logger.event("model_reply", version="v1", tasks=len(tasks), summary=output.summary[:120],
                 notes=output.notes[:120], usage=info["total_tokens"], latency_ms=info["latency_ms"])
    return {
        "status": "success",
        "run_id": run_id,
        "generator": "llm_v1",
        "prompt_version": "v1",
        "course_id": inp.course_id,
        "course_name": inp.course_name,
        "exam_date": inp.exam_date.isoformat(),
        "start_date": inp.start_date.isoformat(),
        "available_minutes": inp.available_minutes,
        "window_days": [day.isoformat() for day in inp.window_days],
        "feasible": inp.feasible,
        "summary": output.summary.strip(),
        "notes": output.notes.strip(),
        "tasks": tasks,
        "meta": info,
    }


def _run_v2(inp: PlanningInput, logger, run_id: str, started: float) -> dict:
    messages = prompts.plan_messages(inp, "v2")
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
             "latency_ms": 0, "calls": 0, "model": config.MODEL}
    attempts: list[dict] = []
    output = None
    tasks: list[dict] = []
    issues: list[validator.Issue] = []
    fallback_reason = ""

    for attempt in range(1, config.MAX_REPAIR + 2):
        logger.event("model_call", version="v2", attempt=attempt, messages=len(messages),
                     prompt_chars=sum(len(item.get("content", "")) for item in messages))
        attempt_started = time.time()
        try:
            out, meta = llm_client.chat_json(
                messages,
                PlanOut,
                model=config.MODEL,
                temperature=config.TEMPERATURE,
                max_tokens=config.MAX_TOKENS,
                timeout=config.TIMEOUT,
            )
        except BusinessError as exc:
            # 失败请求也计入调用次数和耗时，避免降级时显示“调用 0 次”
            total["calls"] += 1
            total["latency_ms"] += max(0, int((time.time() - attempt_started) * 1000))
            fallback_reason = f"模型调用失败（{exc.code}）：{exc.message}"
            logger.event("llm_error", attempt=attempt, error_code=exc.code, error=exc.message)
            break
        _add_usage(total, meta, int(meta.get("latency_ms", 0)))
        tasks = normalize_tasks(out.tasks, inp)
        issues = validator.validate_plan(inp, tasks, out.summary, out.notes)
        attempts.append({
            "attempt": attempt,
            "tasks": len(tasks),
            "passed": not issues,
            "issues": [issue.code for issue in issues],
        })
        logger.event("validate", attempt=attempt, passed=not issues, tasks=len(tasks),
                     issues=[issue.code for issue in issues])
        if not issues:
            output = out
            break
        fallback_reason = "模型输出未通过约束校验：" + "、".join(issue.code for issue in issues)
        if attempt <= config.MAX_REPAIR:
            logger.event("repair", attempt=attempt, issues=[issue.code for issue in issues])
            messages = messages + [
                {"role": "assistant", "content": out.model_dump_json()},
                {"role": "user", "content": (
                    "上一次输出没有通过程序校验，请逐条修正下面的问题，保持 JSON 字段不变：\n"
                    + validator.format_issues(issues)
                    + "\n请只输出修正后的 JSON。"
                )},
            ]

    info = _meta_from_total(total)
    info["repair_attempts"] = len(attempts) - 1 if attempts else 0

    if output is not None and not issues:
        result = {
            "status": "success",
            "run_id": run_id,
            "generator": "llm_v2",
            "prompt_version": "v2",
            "course_id": inp.course_id,
            "course_name": inp.course_name,
            "exam_date": inp.exam_date.isoformat(),
            "start_date": inp.start_date.isoformat(),
            "available_minutes": inp.available_minutes,
            "window_days": [day.isoformat() for day in inp.window_days],
            "feasible": inp.feasible,
            "summary": output.summary.strip(),
            "notes": output.notes.strip(),
            "tasks": tasks,
            "checks": {"passed": True, "issues": [], "attempts": attempts},
            "meta": info,
        }
        logger.event("model_reply", tasks=len(tasks), summary=output.summary[:120],
                     notes=output.notes[:120], usage=info["total_tokens"], latency_ms=info["latency_ms"])
        return result

    # 降级：模型调用失败或两次输出都不合格时，返回规则模板计划并说明原因
    fallback_tasks, summary, notes = rule_fallback(inp)
    fallback_issues = validator.validate_plan(inp, fallback_tasks, summary, notes)
    logger.event("fallback", reason=fallback_reason, tasks=len(fallback_tasks),
                 check_passed=not fallback_issues, issues=[issue.code for issue in fallback_issues])
    return {
        "status": "fallback",
        "run_id": run_id,
        "generator": "llm_v2_fallback",
        "prompt_version": "v2",
        "course_id": inp.course_id,
        "course_name": inp.course_name,
        "exam_date": inp.exam_date.isoformat(),
        "start_date": inp.start_date.isoformat(),
        "available_minutes": inp.available_minutes,
        "window_days": [day.isoformat() for day in inp.window_days],
        "feasible": inp.feasible,
        "summary": summary,
        "notes": notes,
        "tasks": fallback_tasks,
        "checks": {"passed": not fallback_issues, "issues": [issue.code for issue in fallback_issues],
                   "attempts": attempts},
        "fallback_reason": fallback_reason,
        "meta": info,
    }


def generate(
    course_id: int,
    *,
    version: str = config.PROMPT_VERSION,
    start_date: str | None = None,
    available_minutes: int | None = None,
    today: date | str | None = None,
    save: bool = False,
    log_path: str | None = None,
) -> dict:
    """生成候选计划。信息不足时抛出 BusinessError；模型失败时按版本决定是否降级。"""
    run_id = new_run_id()
    logger = make_logger(run_id, log_path)
    started = time.time()
    try:
        inp = load_input(course_id, start_date=start_date, available_minutes=available_minutes, today=today)
    except BusinessError as exc:
        logger.event("run_end", status="rejected", error_code=exc.code, error=exc.message,
                     duration_ms=int((time.time() - started) * 1000))
        raise

    logger.event(
        "run_start",
        course=inp.course_name,
        exam_date=inp.exam_date.isoformat(),
        start_date=inp.start_date.isoformat(),
        available_minutes=inp.available_minutes,
        window_days=len(inp.window_days),
        points=len(inp.points),
        feasible=inp.feasible,
        version=version,
        model=config.MODEL,
        temperature=config.TEMPERATURE,
        max_tokens=config.MAX_TOKENS,
    )

    result = _run_v1(inp, logger, run_id, started) if version == "v1" else _run_v2(inp, logger, run_id, started)

    if save:
        result["save"] = save_candidate(inp.course_id, result["tasks"], result["generator"])
        logger.event("save", **result["save"])
    logger.event(
        "run_end",
        status=result["status"],
        generator=result["generator"],
        tasks=len(result["tasks"]),
        duration_ms=result["meta"]["latency_ms"],
        calls=result["meta"]["calls"],
        total_tokens=result["meta"]["total_tokens"],
        cost_yuan=result["meta"]["cost_yuan"],
    )
    return result
