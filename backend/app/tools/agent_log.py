"""查看 Agent 运行日志：按运行编号汇总事件，便于核对执行过程。

用法（在 backend 目录执行）：
    python3 -m app.tools.agent_log --last
    python3 -m app.tools.agent_log --run-id 20261109-100052-563ac4
"""

import argparse
import json

from app.agent.runlog import AGENT_LOG


def _short(value, limit: int = 90) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _format(record: dict) -> str:
    timestamp = record.get("ts", "").split("T")[-1]
    event = record.get("event", "")
    parts = []
    for key, value in record.items():
        if key in {"run_id", "ts", "event"}:
            continue
        parts.append(f"{key}={_short(value)}")
    return f"{timestamp} {event:<12} " + " ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="查看 Agent 运行日志")
    parser.add_argument("--run-id", default=None, help="指定运行编号，缺省取最近一次运行")
    parser.add_argument("--path", default=AGENT_LOG, help="日志文件路径")
    parser.add_argument("--last", action="store_true", help="显示最近一次运行")
    parser.add_argument("--last-success", action="store_true", help="显示最近一次成功完成的运行")
    args = parser.parse_args()

    try:
        with open(args.path, encoding="utf-8") as fp:
            records = [json.loads(line) for line in fp if line.strip()]
    except FileNotFoundError:
        print(f"日志文件不存在：{args.path}")
        return
    if not records:
        print("暂无运行记录")
        return

    if args.run_id:
        run_id = args.run_id
    elif args.last_success:
        success = [r for r in records
                   if r["event"] == "run_end" and r.get("status") == "success"]
        if not success:
            print("没有成功完成的运行记录")
            return
        run_id = success[-1]["run_id"]
    else:
        run_id = records[-1]["run_id"]
    events = [record for record in records if record["run_id"] == run_id]
    print(f"日志文件：{args.path}")
    print(f"运行编号：{run_id}（{len(events)} 条事件）")
    print("-" * 88)
    for record in events:
        print(_format(record))


if __name__ == "__main__":
    main()