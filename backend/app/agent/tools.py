"""StudyPilot 可调用工具：课程列表、任务查询、统计计算和复习任务创建。

访问范围由程序固定：只连接 STUDYPLOT 数据库，只查询当前用户的数据，
SQL 全部由本文件拼接并使用参数绑定，模型不能传入任意表名、列名或 SQL。
"""

import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal

from app.agent.spec import ToolFailure, ToolSpec
from app.errors import BusinessError
from app.repository.db import current_user_id, get_conn


def _window(days: int) -> tuple[str, str]:
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def _tool_db_error(exc: BusinessError) -> ToolFailure:
    return ToolFailure(exc.message, code=exc.code, retriable=exc.code == "DATABASE_UNAVAILABLE",
                       hint="数据库暂时不可用，可以稍后重试只读查询；写操作不要直接重试")


def _course_row(conn, name: str) -> dict:
    user_id = current_user_id(conn)
    row = conn.execute(
        "SELECT course_id, name, term FROM courses WHERE user_id = ? AND name = ?", (user_id, name)
    ).fetchone()
    if row:
        return dict(row)
    names = [r["name"] for r in conn.execute(
        "SELECT name FROM courses WHERE user_id = ? ORDER BY course_id", (user_id,))]
    raise ToolFailure(
        f"课程不存在：{name}",
        code="COURSE_NOT_FOUND",
        hint="当前可用课程：" + ("、".join(names) if names else "（暂无课程）"),
    )


def _task_rows(conn, course_id: int, start: str, end: str, status: str) -> list[dict]:
    sql = """
        SELECT t.task_id, t.title, t.task_date, t.minutes, t.status, t.completed_at,
               k.name AS point_name
        FROM daily_tasks t
        LEFT JOIN knowledge_points k ON k.point_id = t.point_id
        WHERE t.course_id = ? AND t.task_date BETWEEN ? AND ?
    """
    params: list = [course_id, start, end]
    if status != "all":
        sql += " AND t.status = ?"
        params.append(status)
    sql += " ORDER BY t.task_date, t.task_id"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ---------- 参数模型（同时作为 Schema 和校验规则） ----------

class ListCoursesArgs(BaseModel):
    """列出当前用户的课程。无参数。"""
    model_config = ConfigDict(extra="forbid")


class QueryTasksArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course: str = Field(..., min_length=1, max_length=30, description="课程名称，例如“软件工程”")
    days: int = Field(14, ge=1, le=30, description="查询最近多少天（含今天），取值 1—30")
    status: Literal["all", "todo", "done"] = Field("all", description="任务状态过滤：all/todo/done")


class TaskStatsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course: str = Field(..., min_length=1, max_length=30, description="课程名称，例如“软件工程”")
    days: int = Field(14, ge=1, le=30, description="统计最近多少天（含今天），取值 1—30")


class CreateReviewTaskArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course: str = Field(..., min_length=1, max_length=30, description="课程名称，例如“软件工程”")
    point: str = Field(..., min_length=1, max_length=30, description="知识点名称，必须属于该课程")
    minutes: int = Field(30, ge=10, le=60, description="复习时长（分钟），取值 10—60")
    date: str | None = Field(None, description="复习日期 YYYY-MM-DD，缺省为明天")

    @field_validator("date")
    @classmethod
    def _check_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            datetime.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("日期格式应为 YYYY-MM-DD") from exc
        return value


# ---------- 工具实现 ----------

def list_courses(_: ListCoursesArgs) -> dict:
    try:
        with get_conn() as conn:
            user_id = current_user_id(conn)
            rows = conn.execute(
                """
                SELECT c.course_id, c.name, c.term, g.exam_date, g.target_score
                FROM courses c
                LEFT JOIN exam_goals g ON g.course_id = c.course_id
                WHERE c.user_id = ?
                ORDER BY c.course_id
                """,
                (user_id,),
            ).fetchall()
    except BusinessError as exc:
        raise _tool_db_error(exc) from exc
    courses = [
        {
            "course_id": r["course_id"],
            "name": r["name"],
            "term": r["term"],
            "exam_date": r["exam_date"],
            "target_score": r["target_score"],
        }
        for r in rows
    ]
    return {"courses": courses, "count": len(courses)}


def query_tasks(args: QueryTasksArgs) -> dict:
    start, end = _window(args.days)
    try:
        with get_conn() as conn:
            course = _course_row(conn, args.course)
            rows = _task_rows(conn, course["course_id"], start, end, args.status)
    except BusinessError as exc:
        raise _tool_db_error(exc) from exc

    limit = 50
    truncated = len(rows) > limit
    tasks = [
        {
            "task_id": r["task_id"],
            "title": r["title"],
            "date": r["task_date"],
            "minutes": r["minutes"],
            "status": r["status"],
            "point": r["point_name"],
            "completed_at": r["completed_at"],
        }
        for r in rows[:limit]
    ]
    done = sum(1 for r in rows if r["status"] == "done")
    return {
        "course": course["name"],
        "window": {"start": start, "end": end, "days": args.days},
        "status_filter": args.status,
        "count": len(tasks),
        "total_matched": len(rows),
        "truncated": truncated,
        "summary": {"total": len(rows), "done": done, "pending": len(rows) - done},
        "tasks": tasks,
        "hint": "结果超过50条已截断，请缩小天数或按状态过滤" if truncated else "",
    }


def task_stats(args: TaskStatsArgs) -> dict:
    today = datetime.date.today().isoformat()
    start, end = _window(args.days)
    try:
        with get_conn() as conn:
            course = _course_row(conn, args.course)
            rows = _task_rows(conn, course["course_id"], start, end, "all")
            points = [
                dict(r) for r in conn.execute(
                    "SELECT point_id, name, mastery FROM knowledge_points WHERE course_id = ? "
                    "ORDER BY mastery ASC, point_id ASC",
                    (course["course_id"],),
                ).fetchall()
            ]
    except BusinessError as exc:
        raise _tool_db_error(exc) from exc

    total = len(rows)
    done = sum(1 for r in rows if r["status"] == "done")
    pending = total - done
    completion_rate = round(done / total * 100, 1) if total else 0.0
    done_minutes = sum(r["minutes"] for r in rows if r["status"] == "done")
    pending_minutes = sum(r["minutes"] for r in rows if r["status"] != "done")
    overdue = sum(1 for r in rows if r["status"] != "done" and r["task_date"] < today)

    per_point: dict[str, dict] = {}
    for r in rows:
        point = r["point_name"] or "未关联知识点"
        item = per_point.setdefault(point, {"point": point, "total": 0, "done": 0, "minutes": 0})
        item["total"] += 1
        item["minutes"] += r["minutes"]
        if r["status"] == "done":
            item["done"] += 1
    by_point = []
    for item in per_point.values():
        item["pending"] = item["total"] - item["done"]
        item["completion_rate"] = round(item["done"] / item["total"] * 100, 1)
        by_point.append(item)
    by_point.sort(key=lambda x: x["completion_rate"])

    day_minutes: dict[str, int] = {}
    for r in rows:
        day_minutes[r["task_date"]] = day_minutes.get(r["task_date"], 0) + r["minutes"]
    busiest = max(day_minutes.items(), key=lambda kv: kv[1]) if day_minutes else None

    weakest = [
        {"point": p["name"], "mastery": p["mastery"]}
        for p in points[:2]
    ]
    return {
        "course": course["name"],
        "window": {"start": start, "end": end, "days": args.days},
        "total_tasks": total,
        "done_tasks": done,
        "pending_tasks": pending,
        "completion_rate": completion_rate,
        "total_minutes": done_minutes + pending_minutes,
        "done_minutes": done_minutes,
        "pending_minutes": pending_minutes,
        "overdue_pending": overdue,
        "by_point": by_point,
        "weakest_points": weakest,
        "busiest_day": {"date": busiest[0], "minutes": busiest[1]} if busiest else None,
    }


def create_review_task(args: CreateReviewTaskArgs) -> dict:
    today = datetime.date.today()
    target = datetime.date.fromisoformat(args.date) if args.date else today + datetime.timedelta(days=1)
    if target < today:
        raise ToolFailure("复习日期不能早于今天", code="INVALID_DATE", hint="请把 date 设为今天或之后")
    if target > today + datetime.timedelta(days=30):
        raise ToolFailure("复习日期最多只能安排到30天后", code="INVALID_DATE", hint="请把 date 设在30天内")

    try:
        with get_conn() as conn:
            course = _course_row(conn, args.course)
            point = conn.execute(
                "SELECT point_id, name FROM knowledge_points WHERE course_id = ? AND name = ?",
                (course["course_id"], args.point),
            ).fetchone()
            if not point:
                names = [r["name"] for r in conn.execute(
                    "SELECT name FROM knowledge_points WHERE course_id = ? ORDER BY mastery", (course["course_id"],))]
                raise ToolFailure(
                    f"知识点不存在：{args.point}",
                    code="POINT_NOT_FOUND",
                    hint="该课程的知识点：" + ("、".join(names) if names else "（暂无知识点）"),
                )
            title = f"复习：{point['name']}"
            existing = conn.execute(
                "SELECT task_id FROM daily_tasks WHERE course_id = ? AND title = ? AND task_date = ? "
                "ORDER BY task_id LIMIT 1",
                (course["course_id"], title, target.isoformat()),
            ).fetchone()
            if existing:
                return {
                    "created": False,
                    "reason": "ALREADY_EXISTS",
                    "task_id": existing["task_id"],
                    "course": course["name"],
                    "point": point["name"],
                    "title": title,
                    "date": target.isoformat(),
                    "minutes": args.minutes,
                    "hint": "同课程、同标题、同日期的任务已存在，本次没有重复写入",
                }
            plan = conn.execute(
                "SELECT plan_id FROM study_plans WHERE course_id = ? ORDER BY plan_id DESC LIMIT 1",
                (course["course_id"],),
            ).fetchone()
            if plan:
                plan_id = plan["plan_id"]
            else:
                cur = conn.execute(
                    "INSERT INTO study_plans (course_id, status, generator, confirmed_at) "
                    "VALUES (?, 'confirmed', 'agent', datetime('now', 'localtime'))",
                    (course["course_id"],),
                )
                plan_id = cur.lastrowid
            cur = conn.execute(
                "INSERT INTO daily_tasks (plan_id, course_id, point_id, title, task_date, minutes, status, note) "
                "VALUES (?, ?, ?, ?, ?, ?, 'todo', '由Agent创建，用户已确认')",
                (plan_id, course["course_id"], point["point_id"], title, target.isoformat(), args.minutes),
            )
            task_id = cur.lastrowid
    except BusinessError as exc:
        raise _tool_db_error(exc) from exc

    return {
        "created": True,
        "task_id": task_id,
        "course": course["name"],
        "point": point["name"],
        "title": title,
        "date": target.isoformat(),
        "minutes": args.minutes,
        "dedupe": False,
    }


TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="list_courses",
        description="列出当前用户的课程及考试目标（只读）。用于确认课程名称，无参数。",
        args_model=ListCoursesArgs,
        handler=list_courses,
        timeout_s=3.0,
        max_retries=2,
    ),
    ToolSpec(
        name="query_tasks",
        description="查询某课程最近N天的每日任务明细（只读，最多返回50条）。用于核对具体任务。",
        args_model=QueryTasksArgs,
        handler=query_tasks,
        timeout_s=3.0,
        max_retries=2,
    ),
    ToolSpec(
        name="task_stats",
        description="统计某课程最近N天的任务完成率、时长、各知识点完成情况和掌握度最低的知识点（只读）。",
        args_model=TaskStatsArgs,
        handler=task_stats,
        timeout_s=3.0,
        max_retries=2,
    ),
    ToolSpec(
        name="create_review_task",
        description="为某课程的薄弱知识点创建一条待办复习任务（写操作，执行前必须经用户确认）。",
        args_model=CreateReviewTaskArgs,
        handler=create_review_task,
        mutating=True,
        timeout_s=5.0,
        max_retries=0,
        impact="将向 StudyPilot 数据库 daily_tasks 表插入 1 条待办任务，修改学习计划；"
               "同一课程、标题和日期重复调用会去重，不会产生重复任务。",
    ),
]


def build_registry():
    from app.agent.registry import ToolRegistry

    return ToolRegistry(TOOL_SPECS)
