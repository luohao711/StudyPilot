"""实验6 Agent 命令行入口：实际调用模型与工具，写操作前由用户确认。

用法（在 backend 目录执行）：
    python3 -m app.tools.agent_seed --reset
    printf 'y\n' | python3 -m app.tools.agent_cli --confirm ask
    python3 -m app.tools.agent_cli --confirm no
"""

import argparse
import select
import sys
import time

from app.agent.display import print_event
from app.agent.harness import AgentHarness
from app.agent.tools import build_registry
from app.errors import BusinessError

DEFAULT_TASK = (
    "统计课程“{course}”最近{days}天的任务完成情况，找出掌握度最低的2个知识点并给出下一步建议；"
    "如果完成率低于60%，为最薄弱的知识点创建一条30分钟复习任务（需要我确认）。"
)


def make_confirm(policy: str, timeout_s: float = 20.0):
    """返回确认回调：ask 从标准输入读取，yes/no 用于演示和自动化。"""

    def confirm(request):
        started = time.monotonic()

        if policy == "yes":
            return {"approved": True, "reason": "--confirm yes（演示自动同意）",
                    "waited_ms": int((time.monotonic() - started) * 1000)}
        if policy == "no":
            return {"approved": False, "reason": "--confirm no（演示自动拒绝）",
                    "waited_ms": int((time.monotonic() - started) * 1000)}

        print(f"  输入 y 同意，其他输入或 {timeout_s:.0f} 秒无输入则拒绝", flush=True)
        sys.stdout.write("  > ")
        sys.stdout.flush()
        ready, _, _ = select.select([sys.stdin], [], [], timeout_s)
        waited_ms = int((time.monotonic() - started) * 1000)
        if not ready:
            print("\n  （等待超时，按拒绝处理）", flush=True)
            return {"approved": False, "reason": "确认等待超时", "waited_ms": waited_ms}
        answer = sys.stdin.readline().strip().lower()
        print("\n  用户输入：" + (answer or "（空）"), flush=True)
        approved = answer in {"y", "yes", "是", "同意"}
        return {"approved": approved, "reason": "用户同意" if approved else "用户拒绝", "waited_ms": waited_ms}

    return confirm


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="StudyPilot 单Agent命令行入口")
    parser.add_argument("--course", default="软件工程", help="课程名称")
    parser.add_argument("--days", type=int, default=14, help="统计最近多少天")
    parser.add_argument("--objective", default=None,
                        help="任务目标（缺省按课程和天数生成）")
    parser.add_argument("--max-steps", type=int, default=8, help="最大执行步数")
    parser.add_argument("--deadline", type=float, default=90.0, help="总运行时限（秒）")
    parser.add_argument("--llm-timeout", type=float, default=30.0, help="单次模型调用超时（秒）")
    parser.add_argument("--confirm", choices=["ask", "yes", "no"], default="ask", help="写操作确认策略")
    parser.add_argument("--confirm-timeout", type=float, default=20.0, help="等待用户确认的秒数")
    parser.add_argument("--max-tokens", type=int, default=900, help="模型单次输出上限")
    parser.add_argument("--log", default=None, help="运行日志路径（默认 backend/logs/agent_runs.jsonl）")
    args = parser.parse_args(argv)
    objective = args.objective or DEFAULT_TASK.format(course=args.course, days=args.days)

    harness = AgentHarness(
        build_registry(),
        max_steps=args.max_steps,
        deadline_s=args.deadline,
        llm_timeout_s=args.llm_timeout,
        confirm=make_confirm(args.confirm, args.confirm_timeout),
        log_path=args.log,
        observer=print_event,
        max_tokens=args.max_tokens,
    )
    try:
        result = harness.run(objective, course=args.course, days=args.days)
    except BusinessError as exc:
        print(f"运行失败：{exc.code} {exc.message}", flush=True)
        return 1
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())