"""计划生成主链路（v0.5 初版）：组织输入 → 调用模型 → 结构化校验 → 输出/保存。

v0.5 只做结构化输出校验，业务约束（日期窗口、每天上限、知识点覆盖、优先级）
留给评测程序检查；v0.6 会把检查前移，并加入有限修复和规则降级。
"""

import time
from datetime import date

from app.errors import BusinessError
from app.planning import config, prompts
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


def generate(
    course_id: int,
    *,
    version: str = "v1",
    start_date: str | None = None,
    available_minutes: int | None = None,
    today: date | str | None = None,
    save: bool = False,
    log_path: str | None = None,
) -> dict:
    """生成候选计划。成功返回 tasks/meta，信息不足或调用失败抛出 BusinessError。"""
    run_id = new_run_id()
    logger = make_logger(run_id, log_path)
    started = time.time()
    try:
        inp = load_input(course_id, start_date=start_date, available_minutes=available_minutes, today=today)
    except BusinessError as exc:
        logger.event(
            "run_end",
            status="rejected",
            error_code=exc.code,
            error=exc.message,
            duration_ms=int((time.time() - started) * 1000),
        )
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

    messages = prompts.plan_messages(inp, version)
    logger.event("model_call", messages=len(messages), prompt_chars=len(messages[0]["content"]))
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
        logger.event(
            "run_end",
            status="llm_error",
            error_code=exc.code,
            error=exc.message,
            duration_ms=int((time.time() - started) * 1000),
        )
        raise

    tasks = normalize_tasks(output.tasks, inp)
    info = _meta(meta, int((time.time() - started) * 1000))
    result = {
        "status": "success",
        "run_id": run_id,
        "generator": f"llm_{version}",
        "prompt_version": version,
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
    logger.event(
        "model_reply",
        tasks=len(tasks),
        summary=output.summary[:120],
        notes=output.notes[:120],
        usage=info.get("total_tokens", 0),
        latency_ms=info["latency_ms"],
    )
    if save:
        result["save"] = save_candidate(inp.course_id, tasks, result["generator"])
        logger.event("save", **result["save"])
    logger.event(
        "run_end",
        status="success",
        tasks=len(tasks),
        duration_ms=info["latency_ms"],
        calls=info["calls"],
        total_tokens=info["total_tokens"],
        cost_yuan=info["cost_yuan"],
    )
    return result
