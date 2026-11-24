"""AI 任务数据访问层：任务状态、输入快照、结果和错误统一落库。

状态更新都带前置条件（如只允许 queued→running、running→succeeded），
避免取消、重复确认和迟到的模型结果互相覆盖。
"""

import sqlite3

from app.repository.db import connect, get_conn

TERMINAL_STATUS = ("succeeded", "failed", "canceled")


def _one(row) -> dict | None:
    return dict(row) if row else None


def create(job_id: str, course_id: int, request_json: str,
           idempotency_key: str | None = None, retry_of: str | None = None) -> dict | None:
    """插入任务；违反“同课程单活跃任务/幂等键唯一”时返回 None，由服务层复用已有任务。"""
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO ai_jobs (job_id, course_id, request_json, idempotency_key, retry_of)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, course_id, request_json, idempotency_key, retry_of),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        return None
    finally:
        conn.close()
    return get(job_id)


def get(job_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM ai_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return _one(row)


def find_active(course_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM ai_jobs WHERE course_id = ? AND status IN ('queued', 'running') "
            "ORDER BY created_at DESC LIMIT 1",
            (course_id,),
        ).fetchone()
    return _one(row)


def find_by_idempotency(key: str) -> dict | None:
    if not key:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM ai_jobs WHERE idempotency_key = ?", (key,)).fetchone()
    return _one(row)


def list_jobs(course_id: int | None = None, limit: int = 5) -> list[dict]:
    sql = "SELECT * FROM ai_jobs WHERE 1 = 1"
    params: list = []
    if course_id:
        sql += " AND course_id = ?"
        params.append(course_id)
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    params.append(max(1, min(limit, 20)))
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def list_non_terminal() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM ai_jobs WHERE status IN ('queued', 'running') ORDER BY created_at"
        ).fetchall()
    return [dict(row) for row in rows]


def mark_running(job_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE ai_jobs SET status = 'running', started_at = datetime('now', 'localtime'), "
            "attempts = attempts + 1 WHERE job_id = ? AND status = 'queued' AND cancel_requested = 0",
            (job_id,),
        )
        return cur.rowcount == 1


def mark_succeeded(job_id: str, result_json: str) -> bool:
    """只允许未取消的执行中任务写入结果；被取消的任务不再接收迟到的结果。"""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE ai_jobs SET status = 'succeeded', result_json = ?, error_code = NULL, "
            "error_message = NULL, finished_at = datetime('now', 'localtime') "
            "WHERE job_id = ? AND status = 'running' AND cancel_requested = 0",
            (result_json, job_id),
        )
        return cur.rowcount == 1


def mark_failed(job_id: str, code: str, message: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE ai_jobs SET status = 'failed', error_code = ?, error_message = ?, "
            "finished_at = datetime('now', 'localtime') "
            "WHERE job_id = ? AND status = 'running' AND cancel_requested = 0",
            (code, message, job_id),
        )
        return cur.rowcount == 1


def mark_canceled(job_id: str) -> bool:
    """取消只停止界面等待和后续结果落库；已经发出的模型请求无法中断，结果会被丢弃。"""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE ai_jobs SET status = 'canceled', cancel_requested = 1, "
            "finished_at = datetime('now', 'localtime') "
            "WHERE job_id = ? AND status IN ('queued', 'running')",
            (job_id,),
        )
        return cur.rowcount == 1


def mark_interrupted(job_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE ai_jobs SET status = 'failed', error_code = 'JOB_INTERRUPTED', "
            "error_message = '服务重启导致任务中断，请重新生成', finished_at = datetime('now', 'localtime') "
            "WHERE job_id = ? AND status IN ('queued', 'running')",
            (job_id,),
        )
        return cur.rowcount == 1


def set_confirmed_plan(job_id: str, plan_id: int) -> bool:
    """保存结果只写一次；并发或重复点击确认时只有一个请求能成功关联计划。"""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE ai_jobs SET confirmed_plan_id = ? WHERE job_id = ? AND confirmed_plan_id IS NULL",
            (plan_id, job_id),
        )
        return cur.rowcount == 1


def cleanup_old(days: int) -> int:
    """清理超过保留期的已结束任务，避免数据库无限增长。"""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM ai_jobs WHERE status IN ('succeeded', 'failed', 'canceled') "
            "AND finished_at IS NOT NULL "
            "AND finished_at < datetime('now', 'localtime', ?)",
            (f"-{max(1, int(days))} days",),
        )
        return cur.rowcount
