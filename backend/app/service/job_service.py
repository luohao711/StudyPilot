"""AI 任务服务：把生成计划的能力包装成可查询、可取消、可重试、可恢复的任务。

调用链：接口创建任务 → 线程池执行 → 生成器读数据库并调用模型 → 结果/错误落库；
页面只拿 job_id 轮询，不在请求里长时间等待。状态转换和确认保存都在这一层收口。
"""

import datetime
import json
import secrets
import threading
import time

from app.errors import BusinessError
from app.integration import config as job_config, joblog, runner
from app.logging_conf import log
from app.planning import config as plan_config, generator
from app.planning.context import load_input
from app.repository import job_repo, plan_repo
from app.service import plan_service

STATUS_LABELS = {
    "queued": "排队中",
    "running": "处理中",
    "succeeded": "已完成",
    "failed": "失败",
    "canceled": "已取消",
}
SIMULATIONS = {"timeout", "error", "slow"}

_create_lock = threading.Lock()
_confirm_lock = threading.Lock()


def new_job_id() -> str:
    return "job-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)


def _get_or_404(job_id: str) -> dict:
    job = job_repo.get(job_id)
    if not job:
        raise BusinessError("任务不存在或已清理，请重新生成", 404, "JOB_NOT_FOUND")
    return job


def _parse_start_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise BusinessError("开始日期格式应为 YYYY-MM-DD", 400, "INVALID_START_DATE") from None
    return value


def _validate_simulate(value: str | None) -> None:
    if not value:
        return
    if not job_config.SIMULATION_ENABLED:
        raise BusinessError("当前运行未开启故障演示开关，不能使用 simulate 参数", 400, "INVALID_SIMULATION")
    if value not in SIMULATIONS:
        raise BusinessError("simulate 只支持 timeout、error、slow", 400, "INVALID_SIMULATION")


def serialize(job: dict, *, deduplicated: bool = False) -> dict:
    result = json.loads(job["result_json"]) if job.get("result_json") else None
    error = None
    if job.get("error_code"):
        error = {"code": job["error_code"], "message": job.get("error_message") or "任务失败"}
    return {
        "job_id": job["job_id"],
        "course_id": job["course_id"],
        "status": job["status"],
        "status_label": STATUS_LABELS.get(job["status"], job["status"]),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "attempts": job.get("attempts", 0),
        "cancel_requested": bool(job.get("cancel_requested")),
        "retry_of": job.get("retry_of"),
        "confirmed_plan_id": job.get("confirmed_plan_id"),
        "result": result,
        "error": error,
        "deduplicated": deduplicated,
    }


def _request_payload(course_id: int, start_date: str | None,
                     available_minutes: int | None, simulate: str | None) -> str:
    return json.dumps(
        {
            "course_id": course_id,
            "start_date": start_date,
            "available_minutes": available_minutes,
            "simulate": simulate,
        },
        ensure_ascii=False,
    )


def _create_locked(course_id: int, start_date: str | None, available_minutes: int | None,
                   idempotency_key: str | None, simulate: str | None,
                   retry_of: str | None = None) -> dict:
    """创建任务；重复提交复用已有任务，不重复调用模型。"""
    if idempotency_key:
        existing = job_repo.find_by_idempotency(idempotency_key)
        if existing:
            joblog.event(existing["job_id"], "duplicate_request", via="idempotency_key")
            return serialize(existing, deduplicated=True)

    active = job_repo.find_active(course_id)
    if active:
        joblog.event(active["job_id"], "duplicate_request", via="course_active")
        return serialize(active, deduplicated=True)

    job_id = new_job_id()
    payload = _request_payload(course_id, start_date, available_minutes, simulate)
    job = job_repo.create(job_id, course_id, payload, idempotency_key, retry_of)
    if job is None:
        existing = (job_repo.find_by_idempotency(idempotency_key) if idempotency_key else None) \
            or job_repo.find_active(course_id)
        if existing:
            return serialize(existing, deduplicated=True)
        raise BusinessError("任务创建冲突，请稍后重试", 409, "JOB_CONFLICT")
    joblog.event(job_id, "job_created", course_id=course_id, available_minutes=available_minutes,
                 start_date=start_date, simulate=simulate, retry_of=retry_of)
    runner.submit(execute_job, job_id)
    return serialize(job)


def create_job(course_id: int, *, available_minutes: int | None = None,
               start_date: str | None = None, idempotency_key: str | None = None,
               simulate: str | None = None) -> dict:
    """同步做输入校验（不消耗模型调用），通过后创建异步任务并立即返回。"""
    start_date = _parse_start_date(start_date)
    _validate_simulate(simulate)
    if idempotency_key is not None and (not isinstance(idempotency_key, str) or len(idempotency_key) > 64):
        raise BusinessError("幂等键应为不超过 64 字符的字符串", 400, "INVALID_IDEMPOTENCY_KEY")
    # 前置校验：课程、考试目标、知识点、窗口和预算；不合法时不创建任务。
    load_input(course_id, start_date=start_date, available_minutes=available_minutes)
    with _create_lock:
        return _create_locked(course_id, start_date, available_minutes, idempotency_key, simulate)


def get_job(job_id: str) -> dict:
    return serialize(_get_or_404(job_id))


def list_jobs(course_id: int | None = None, limit: int = 5) -> dict:
    items = [serialize(job) for job in job_repo.list_jobs(course_id, limit)]
    return {"items": items, "total": len(items)}


def cancel_job(job_id: str) -> dict:
    job = _get_or_404(job_id)
    if job["status"] == "canceled":
        return serialize(job)
    if job["status"] not in ("queued", "running"):
        raise BusinessError(f"任务{STATUS_LABELS.get(job['status'], job['status'])}，不能再取消", 409,
                            "JOB_ALREADY_FINISHED")
    if not job_repo.mark_canceled(job_id):
        current = _get_or_404(job_id)
        return serialize(current)
    joblog.event(job_id, "job_canceled", note="停止界面等待，迟到的模型结果将被丢弃")
    log.info("AI 任务已取消：%s", job_id)
    return serialize(_get_or_404(job_id))


def retry_job(job_id: str) -> dict:
    """失败或取消后重试：创建新的任务编号，保留 retry_of 便于追踪，不继承故障演示参数。"""
    old = _get_or_404(job_id)
    if old["status"] not in ("failed", "canceled"):
        raise BusinessError(f"任务当前为{STATUS_LABELS.get(old['status'], old['status'])}，只有失败或已取消的任务可以重试",
                            409, "JOB_NOT_RETRYABLE")
    request = json.loads(old["request_json"])
    load_input(
        old["course_id"],
        start_date=request.get("start_date"),
        available_minutes=request.get("available_minutes"),
    )
    with _create_lock:
        return _create_locked(
            old["course_id"],
            request.get("start_date"),
            request.get("available_minutes"),
            None,
            None,
            retry_of=job_id,
        )


def _validate_tasks(tasks) -> list[dict]:
    if not isinstance(tasks, list) or not tasks:
        raise BusinessError("计划内容为空，无法保存", 400, "EMPTY_PLAN")
    if len(tasks) > 40:
        raise BusinessError("任务数量超过 40 个，请精简后再保存", 400, "TOO_MANY_TASKS")
    cleaned = []
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            raise BusinessError(f"第 {index} 个任务格式不正确", 400, "INVALID_TASK")
        title = str(task.get("title") or "").strip()
        if not (1 <= len(title) <= 50):
            raise BusinessError(f"第 {index} 个任务标题应为 1—50 个字符", 400, "INVALID_TASK_TITLE")
        try:
            datetime.date.fromisoformat(str(task.get("date") or ""))
        except ValueError:
            raise BusinessError(f"第 {index} 个任务日期格式应为 YYYY-MM-DD", 400, "INVALID_TASK_DATE") from None
        minutes = task.get("minutes")
        if not isinstance(minutes, int) or isinstance(minutes, bool) or not (10 <= minutes <= 300):
            raise BusinessError(f"第 {index} 个任务时长应为 10—300 分钟的整数", 400, "INVALID_TASK_MINUTES")
        point_id = task.get("point_id")
        if point_id is not None and (not isinstance(point_id, int) or isinstance(point_id, bool)):
            raise BusinessError(f"第 {index} 个任务的知识点编号不合法", 400, "INVALID_TASK_POINT")
        cleaned.append({
            "point_id": point_id,
            "title": title,
            "date": str(task["date"]),
            "minutes": minutes,
        })
    return cleaned


def confirm_job(job_id: str, tasks=None) -> dict:
    """把任务结果（或用户编辑后的任务）保存为正式计划；重复确认不重复写入。"""
    job = _get_or_404(job_id)
    if job.get("confirmed_plan_id"):
        plan = plan_repo.get_plan(job["confirmed_plan_id"])
        return {"plan_id": job["confirmed_plan_id"], "status": "confirmed",
                "task_count": plan_repo.count_plan_tasks(job["confirmed_plan_id"]),
                "duplicate": True, "job_id": job_id, "plan": plan}
    if job["status"] != "succeeded":
        raise BusinessError(
            f"任务当前为{STATUS_LABELS.get(job['status'], job['status'])}，只有生成成功的任务才能保存",
            409,
            "JOB_NOT_SUCCEEDED",
        )
    result = json.loads(job["result_json"])
    tasks = _validate_tasks(tasks if tasks is not None else result.get("tasks"))
    with _confirm_lock:
        job = _get_or_404(job_id)
        if job.get("confirmed_plan_id"):
            return {"plan_id": job["confirmed_plan_id"], "status": "confirmed",
                    "task_count": plan_repo.count_plan_tasks(job["confirmed_plan_id"]),
                    "duplicate": True, "job_id": job_id,
                    "plan": plan_repo.get_plan(job["confirmed_plan_id"])}
        plan = plan_service.confirm_plan(job["course_id"], tasks, generator=result.get("generator", "ai_plan"))
        if not job_repo.set_confirmed_plan(job_id, plan["plan_id"]):
            raise BusinessError("任务已被保存，请刷新查看最新计划", 409, "JOB_ALREADY_CONFIRMED")
        joblog.event(job_id, "job_confirmed", plan_id=plan["plan_id"], tasks=len(tasks))
        return {**plan, "duplicate": False, "job_id": job_id,
                "plan": plan_repo.get_plan(plan["plan_id"])}


def execute_job(job_id: str) -> None:
    """后台执行：状态先 running，成功/失败/取消都要有终态；取消后迟到的结果直接丢弃。"""
    job = job_repo.get(job_id)
    if not job or job["status"] != "queued":
        return
    if not job_repo.mark_running(job_id):
        return
    request = json.loads(job["request_json"])
    simulate = request.get("simulate")
    started = time.time()
    joblog.event(job_id, "job_started", course_id=job["course_id"], simulate=simulate)
    try:
        if simulate == "slow":
            time.sleep(job_config.SIMULATION_DELAY_SECONDS)
        if simulate == "timeout":
            time.sleep(min(1.0, job_config.SIMULATION_DELAY_SECONDS))
            raise BusinessError("模型服务响应超时（演示），任务未完成，可重试或改用规则计划", 504, "LLM_TIMEOUT")
        if simulate == "error":
            raise BusinessError("无法连接模型服务（演示），任务未完成，可重试或改用规则计划", 503,
                                "LLM_CONNECTION_ERROR")

        result = generator.generate(
            job["course_id"],
            version=plan_config.PROMPT_VERSION,
            start_date=request.get("start_date"),
            available_minutes=request.get("available_minutes"),
            save=False,
        )
        elapsed = int((time.time() - started) * 1000)
        if job_repo.get(job_id)["cancel_requested"]:
            joblog.event(job_id, "late_result_discarded", elapsed_ms=elapsed, tasks=len(result.get("tasks", [])))
            return
        if not job_repo.mark_succeeded(job_id, json.dumps(result, ensure_ascii=False)):
            joblog.event(job_id, "late_result_discarded", elapsed_ms=elapsed, note="状态已被取消或改变")
            return
        joblog.event(job_id, "job_succeeded", elapsed_ms=elapsed, result_status=result.get("status"),
                     tasks=len(result.get("tasks", [])), calls=result.get("meta", {}).get("calls", 0))
        log.info("AI 任务完成：%s，状态 %s，任务 %d 个，耗时 %d ms", job_id, result.get("status"),
                 len(result.get("tasks", [])), elapsed)
    except BusinessError as exc:
        if job_repo.get(job_id)["cancel_requested"]:
            joblog.event(job_id, "late_error_discarded", error_code=exc.code)
            return
        job_repo.mark_failed(job_id, exc.code, exc.message)
        joblog.event(job_id, "job_failed", error_code=exc.code, error=exc.message,
                     elapsed_ms=int((time.time() - started) * 1000))
        log.warning("AI 任务失败：%s（%s）", exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001 - 后台线程必须收口，不能把异常留在 future 里
        log.exception("AI 任务未处理异常：%s", job_id)
        if job_repo.get(job_id)["cancel_requested"]:
            joblog.event(job_id, "late_error_discarded", error_code="INTERNAL_ERROR")
            return
        job_repo.mark_failed(job_id, "INTERNAL_ERROR", "服务内部错误，请重试或改用规则生成")
        joblog.event(job_id, "job_failed", error_code="INTERNAL_ERROR", error=exc.__class__.__name__)


def recover_interrupted() -> int:
    """服务启动时把上次进程遗留的排队/执行中任务标记为中断，避免永远“处理中”。"""
    count = 0
    for job in job_repo.list_non_terminal():
        if job_repo.mark_interrupted(job["job_id"]):
            count += 1
            joblog.event(job["job_id"], "job_interrupted", note="服务重启，任务中断")
    if count:
        log.warning("启动恢复：%d 个 AI 任务被标记为中断", count)
    return count


def cleanup_old_jobs(days: int | None = None) -> int:
    days = job_config.JOB_RETENTION_DAYS if days is None else days
    count = job_repo.cleanup_old(days)
    if count:
        log.info("清理 %d 个超过 %d 天保留期的 AI 任务", count, days)
    return count
