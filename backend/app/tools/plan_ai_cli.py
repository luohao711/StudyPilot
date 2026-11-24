"""AI 学习计划原型命令行入口：生成候选计划、显示输入与结果、可选保存。

用法（在 backend 目录执行）：
    python3 -m app.seed --reset
    source ../tmp/env.sh
    python3 -m app.tools.plan_ai_cli --course 软件工程
    python3 -m app.tools.plan_ai_cli --course 软件工程 --version v1 --available 90
    python3 -m app.tools.plan_ai_cli --course 软件工程 --save
"""

import argparse
import json
import sys

from app.errors import BusinessError
from app.planning import config
from app.planning.context import load_input
from app.planning.generator import generate
from app.repository import course_repo, db


def _fmt_minutes(tasks: list[dict]) -> int:
    return sum(task["minutes"] for task in tasks)


def print_plan(result: dict) -> None:
    meta = result["meta"]
    print()
    print(f"课程：{result['course_name']}　考试：{result['exam_date']}　每天可用：{result['available_minutes']} 分钟")
    print(f"计划窗口：{result['window_days'][0]} 至 {result['window_days'][-1]}（{len(result['window_days'])} 天）"
          f"　可覆盖全部知识点：{'是' if result['feasible'] else '否'}")
    print(f"生成方式：{result['generator']}　模型：{meta['model']}　耗时：{meta['latency_ms']} ms　"
          f"tokens：{meta['total_tokens']}　费用约：{meta['cost_yuan']} 元　调用次数：{meta['calls']}")
    if result.get("summary"):
        print(f"计划说明：{result['summary']}")
    if result.get("notes"):
        print(f"取舍说明：{result['notes']}")
    if not result["tasks"]:
        print("模型没有返回任务。")
        return
    print()
    print(f"{'日期':<12}{'知识点':<10}{'时长':>4}  任务　　　　　　　　　理由")
    print("-" * 96)
    for task in result["tasks"]:
        print(f"{task['date']:<12}{task['point']:<10}{task['minutes']:>3}分  {task['title']:<22}{task['reason']}")
    print("-" * 96)
    print(f"共 {len(result['tasks'])} 个任务，合计 {_fmt_minutes(result['tasks'])} 分钟")
    if result.get("save"):
        saved = result["save"]
        print(f"已保存为候选计划：plan_id={saved['plan_id']}，状态 {saved['status']}，任务 {saved['task_count']} 条")
    print(f"运行编号：{result['run_id']}（日志：{config.RUN_LOG_PATH}）")


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 学习计划原型")
    parser.add_argument("--course", default="软件工程", help="课程名称")
    parser.add_argument("--course-id", type=int, default=None, help="课程编号（优先于课程名称）")
    parser.add_argument("--start", default=None, help="开始日期 YYYY-MM-DD，缺省为今天")
    parser.add_argument("--available", type=int, default=None, help="每天可用分钟数，缺省使用考试目标")
    parser.add_argument("--version", default="v1", choices=["v1", "v2"], help="Prompt/原型版本")
    parser.add_argument("--save", action="store_true", help="把结果保存为候选计划（draft）")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON，便于脚本检查")
    args = parser.parse_args()

    db.init_db()
    try:
        course_id = args.course_id
        if not course_id:
            course = course_repo.find_by_name(args.course)
            if not course:
                print(f"课程不存在：{args.course}，请先执行 python3 -m app.seed --reset")
                return 2
            course_id = course["course_id"]
        result = generate(
            course_id,
            version=args.version,
            start_date=args.start,
            available_minutes=args.available,
            save=args.save,
        )
    except BusinessError as exc:
        print(f"生成失败 [{exc.code}] {exc.message}")
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"AI 学习计划原型（{args.version}）")
        print_plan(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
