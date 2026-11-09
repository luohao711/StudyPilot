"""接入 Agent 前的工具直接测试：正确参数、错误参数、未知工具和写操作校验。

用法：cd backend && python3 -m app.tools.agent_tools_demo
"""

import json

from app.agent.spec import ToolFailure
from app.agent.tools import build_registry


def _summary(name: str, data: dict) -> str:
    if name == "list_courses":
        return f"返回 {data['count']} 门课程：" + "、".join(c["name"] for c in data["courses"])
    if name == "query_tasks":
        summary = data["summary"]
        return f"返回 {data['count']} 条任务（已完成 {summary['done']}，未完成 {summary['pending']}）"
    if name == "task_stats":
        weakest = "、".join(f"{p['point']}({p['mastery']})" for p in data["weakest_points"])
        return (f"完成率 {data['completion_rate']}%（{data['done_tasks']}/{data['total_tasks']}），"
                f"薄弱点：{weakest}")
    if name == "create_review_task":
        return f"created={data['created']}，task_id={data['task_id']}，{data.get('title', '')}"
    return json.dumps(data, ensure_ascii=False)[:120]


def _invoke(reg, name: str, args: dict, note: str) -> None:
    print(f"[{note}] {name}({json.dumps(args, ensure_ascii=False)})")
    try:
        spec, parsed = reg.validate(name, args)
        output = reg.execute(spec, parsed, timeout_s=3.0)
        print("  <- ok：" + _summary(name, output["data"]))
    except ToolFailure as exc:
        print(f"  <- 程序拒绝：{exc.code}：{exc.message}")
        if exc.hint:
            print("     提示：" + exc.hint)


def main() -> None:
    reg = build_registry()
    count_before = _task_count()
    print("工具白名单：" + "、".join(reg.names()))
    print()

    _invoke(reg, "list_courses", {}, "1 正确参数")
    _invoke(reg, "task_stats", {"course": "软件工程", "days": 14}, "2 正确参数")
    _invoke(reg, "query_tasks", {"course": "软件工程", "days": 14, "status": "todo"}, "3 正确参数")
    _invoke(reg, "task_stats", {"course": "软件工程", "days": 90}, "4 错误参数：超出范围")
    _invoke(reg, "query_tasks", {"course": "软件工程", "days": "十四"}, "5 错误参数：类型错误")
    _invoke(reg, "query_tasks", {"course": "不存在的课程", "days": 14}, "6 错误参数：课程不存在")
    _invoke(reg, "run_sql", {"sql": "DELETE FROM daily_tasks"}, "7 未知工具：不在白名单")
    _invoke(reg, "create_review_task", {"course": "软件工程", "point": "软件测试", "minutes": 99},
            "8 写操作参数错误")
    _invoke(reg, "create_review_task", {"course": "软件工程", "point": "不存在的知识点", "minutes": 30},
            "9 写操作访问范围错误")

    count_after = _task_count()
    print()
    print(f"任务数：调用前 {count_before}，调用后 {count_after}（错误调用没有写入数据）")


def _task_count() -> int:
    from app.repository.db import get_conn

    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM daily_tasks").fetchone()["c"]


if __name__ == "__main__":
    main()
