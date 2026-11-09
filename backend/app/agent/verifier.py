"""结果核验：程序直接查数据库计算成功标准，不采信模型自述。

成功标准（2.1）：
- completion_rate 与数据库算出的值一致；
- total_tasks / done_tasks / pending_tasks 与数据库一致；
- weakest_points 与课程掌握度最低的两个知识点一致；
- suggestion 非空；
- completion_rate < 60 时必须创建复习任务（或记录用户拒绝），且模型声称创建时数据库确有该任务。
"""

import datetime

from app.errors import BusinessError
from app.repository.db import current_user_id, get_conn


def _window(days: int) -> tuple[str, str]:
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def ground_truth(course_name: str, days: int) -> dict:
    """独立查询数据库，得到核验用的基准结果。"""
    start, end = _window(days)
    with get_conn() as conn:
        user_id = current_user_id(conn)
        course = conn.execute(
            "SELECT course_id, name FROM courses WHERE user_id = ? AND name = ?", (user_id, course_name)
        ).fetchone()
        if not course:
            raise BusinessError(f"课程不存在，无法核验：{course_name}", 404, "COURSE_NOT_FOUND")
        rows = [dict(r) for r in conn.execute(
            "SELECT task_id, status, minutes FROM daily_tasks "
            "WHERE course_id = ? AND task_date BETWEEN ? AND ?",
            (course["course_id"], start, end),
        ).fetchall()]
        points = [dict(r) for r in conn.execute(
            "SELECT name, mastery FROM knowledge_points WHERE course_id = ? "
            "ORDER BY mastery ASC, point_id ASC",
            (course["course_id"],),
        ).fetchall()]

    total = len(rows)
    done = sum(1 for r in rows if r["status"] == "done")
    return {
        "course": course["name"],
        "days": days,
        "window": {"start": start, "end": end},
        "total_tasks": total,
        "done_tasks": done,
        "pending_tasks": total - done,
        "completion_rate": round(done / total * 100, 1) if total else 0.0,
        "total_minutes": sum(r["minutes"] for r in rows),
        "weakest_points": [p["name"] for p in points[:2]],
        "masteries": {p["name"]: p["mastery"] for p in points[:2]},
    }


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _point_names(value) -> list[str]:
    names = []
    if not isinstance(value, list):
        return names
    for item in value:
        if isinstance(item, dict):
            names.append(str(item.get("point") or item.get("name") or ""))
        else:
            names.append(str(item))
    return [name for name in names if name]


def _task_exists(task_id) -> bool:
    if not isinstance(task_id, int):
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT task_id FROM daily_tasks WHERE task_id = ? AND title LIKE '复习：%'", (task_id,)
        ).fetchone()
    return row is not None


def verify_success(answer: dict, gt: dict, context: dict | None = None) -> list[dict]:
    """返回逐项核验结果；全部通过才算任务完成。"""
    context = context or {}
    created_ids = context.get("created_task_ids") or []
    denied = context.get("denied_tools") or []
    checks: list[dict] = []

    def add(name: str, ok: bool, expected, actual, detail: str = ""):
        checks.append({"name": name, "ok": bool(ok), "expected": expected, "actual": actual, "detail": detail})

    rate = _number(answer.get("completion_rate"))
    add("完成率", rate is not None and round(rate, 1) == gt["completion_rate"],
        gt["completion_rate"], answer.get("completion_rate"))

    for field, label in (("total_tasks", "任务总数"), ("done_tasks", "已完成任务"), ("pending_tasks", "未完成任务")):
        value = answer.get(field)
        add(label, isinstance(value, int) and value == gt[field], gt[field], value)

    names = _point_names(answer.get("weakest_points"))
    add("薄弱知识点", set(names) == set(gt["weakest_points"]),
        "、".join(gt["weakest_points"]), "、".join(names) or "空")

    suggestion = answer.get("suggestion")
    add("下一步建议", isinstance(suggestion, str) and len(suggestion.strip()) >= 10,
        "非空建议（至少10字）", suggestion)

    claimed = answer.get("review_task_created")
    claimed_id = answer.get("review_task_id")
    if gt["completion_rate"] < 60:
        if claimed is True:
            ok = bool(created_ids) and claimed_id in created_ids and _task_exists(claimed_id)
            add("条件写操作", ok, "完成率低于60%，应创建复习任务且数据库中存在",
                f"created={claimed}, id={claimed_id}")
        else:
            ok = "create_review_task" in denied and not created_ids
            add("条件写操作", ok, "完成率低于60%，创建复习任务或记录用户拒绝，且本次无写入",
                "用户已拒绝/确认超时，未创建" if ok else "声称未创建，但存在写入或没有拒绝记录")
    else:
        ok = claimed is not True and not created_ids
        add("条件写操作", ok, "完成率不低于60%，不应创建写任务", 
            f"created={claimed}, ids={created_ids}")

    add("最终状态字段", answer.get("status") == "success", "success", answer.get("status"))
    return checks