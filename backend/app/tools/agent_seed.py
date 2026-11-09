"""实验6样例数据入口：写入或重置“软件工程”最近14天的任务数据。"""

import argparse

from app.agent.scenario import reset_scenario
from app.logging_conf import log


def main() -> None:
    parser = argparse.ArgumentParser(description="写入实验6 Agent 样例数据")
    parser.add_argument("--course", default="软件工程", help="课程名称")
    parser.add_argument("--reset", action="store_true", help="兼容写法：本命令总是重置该课程的样例数据")
    parser.add_argument("--show", action="store_true", help="打印写入的每日任务明细")
    args = parser.parse_args()
    summary = reset_scenario(args.course)
    log.info("样例数据已写入：%s", summary)
    print("样例数据已重置：")
    print(f"  课程：{summary['course']}（course_id={summary['course_id']}，plan_id={summary['plan_id']}）")
    print(f"  最近14天任务：{summary['tasks']} 条，已完成 {summary['done']} 条，未完成 {summary['pending']} 条，"
          f"完成率 {summary['completion_rate']}%")
    print(f"  知识点：{summary['points']} 个，今天：{summary['today']}")
    if args.show:
        print()
        print("每日任务明细：")
        print(f"  {'日期':<12}{'状态':<6}{'时长':>5}  任务")
        from app.repository.db import get_conn

        with get_conn() as conn:
            rows = conn.execute(
                "SELECT task_date, status, minutes, title FROM daily_tasks "
                "WHERE course_id = ? ORDER BY task_date, task_id", (summary["course_id"],)
            ).fetchall()
        for row in rows:
            print(f"  {row['task_date']:<12}{row['status']:<6}{row['minutes']:>3}分  {row['title']}")


if __name__ == "__main__":
    main()