"""AI 任务日志查看：按 job_id 汇总创建、开始、取消、成功、失败和迟到结果。

用法（在 backend 目录执行）：
    python3 -m app.tools.job_log --last 12
    python3 -m app.tools.job_log --job job-20261124-090501-d27d66
"""

import argparse
import json

from app.integration import config


def load(path) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    except FileNotFoundError:
        return []


def main() -> None:
    parser = argparse.ArgumentParser(description="查看 AI 任务运行日志")
    parser.add_argument("--last", type=int, default=10, help="显示最近 N 条事件")
    parser.add_argument("--job", help="只显示指定任务编号的事件")
    args = parser.parse_args()

    records = load(config.JOB_LOG_PATH)
    if args.job:
        records = [record for record in records if record.get("job_id") == args.job]
        records = records[-args.last:]
    else:
        records = records[-args.last:]

    if not records:
        print("暂无 AI 任务日志")
        return
    for record in records:
        detail = {key: value for key, value in record.items() if key not in ("time", "job_id", "event")}
        print(f"{record['time']}  {record['event']:<22} {record['job_id']}  {json.dumps(detail, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
