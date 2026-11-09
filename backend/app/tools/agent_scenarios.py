"""实验6异常与确认场景复现：模型决策由脚本模拟，工具与 Harness 都是真实实现。

用法（在 backend 目录执行）：
    python3 -m app.tools.agent_scenarios param_error
    python3 -m app.tools.agent_scenarios tool_exception
    python3 -m app.tools.agent_scenarios step_limit
    python3 -m app.tools.agent_scenarios time_limit
    python3 -m app.tools.agent_scenarios confirm_approve
    python3 -m app.tools.agent_scenarios confirm_deny
    python3 -m app.tools.agent_scenarios verify_fix
"""

import argparse
import os
import tempfile

from app.agent.display import print_event
from app.agent.fakes import (
    ScriptedLLM, SlowScriptedLLM, assistant_final, assistant_tool_calls,
    last_created_id, success_answer, tool_call,
)
from app.agent.harness import AgentHarness
from app.agent.scenario import reset_scenario
from app.agent.tools import build_registry
from app.agent.verifier import ground_truth
from app.repository import db as db_module

COURSE = "软件工程"
DAYS = 14
OBJECTIVE = (
    "统计课程“软件工程”最近14天的任务完成情况，找出掌握度最低的2个知识点并给出下一步建议；"
    "如果完成率低于60%，为最薄弱的知识点创建一条30分钟复习任务（需要我确认）。"
)
DESCRIPTIONS = {
    "param_error": "模型第一次给出 days=90（超出 Schema），程序拒绝并回传错误，模型修正后完成",
    "tool_exception": "数据库不可用，只读工具重试后失败，模型报告任务失败",
    "step_limit": "模型反复请求工具，达到最大执行步数后被终止",
    "time_limit": "模型调用耗时超过总运行时限，运行被终止",
    "confirm_approve": "写操作经用户同意后执行，任务写入数据库",
    "confirm_deny": "写操作被用户拒绝，不执行，模型按未创建任务收尾",
    "verify_fix": "最终回答数字与数据库不符，程序核验后反馈，模型修正后通过",
}


def _auto(approved: bool):
    def confirm(request):
        return {"approved": approved, "reason": "演示：用户同意" if approved else "演示：用户拒绝",
                "waited_ms": 800}

    return confirm


def _final_success(messages):
    gt = ground_truth(COURSE, DAYS)
    return assistant_final(success_answer(gt, review_task_id=last_created_id(messages)))


def _final_no_create(messages):
    gt = ground_truth(COURSE, DAYS)
    return assistant_final(success_answer(gt, review_task_id=None))


def _final_wrong_rate(messages):
    gt = ground_truth(COURSE, DAYS)
    return assistant_final(success_answer(gt, review_task_id=last_created_id(messages),
                                          completion_rate=50.0))


def _stats_call():
    return assistant_tool_calls(tool_call("task_stats", {"course": COURSE, "days": DAYS}))


def _create_call():
    return assistant_tool_calls(tool_call(
        "create_review_task", {"course": COURSE, "point": "软件测试", "minutes": 30}))


def _run(harness: AgentHarness, name: str):
    print("=" * 88)
    print(f"场景：{name}")
    print("说明：" + DESCRIPTIONS[name])
    print("=" * 88)
    result = harness.run(OBJECTIVE, course=COURSE, days=DAYS)
    print()
    print(f"场景结束：{name} -> status={result.status}，steps={result.steps}")
    return result


def scenario_param_error():
    reset_scenario(COURSE)
    script = [
        assistant_tool_calls(tool_call("query_tasks", {"course": COURSE, "days": 90})),
        assistant_tool_calls(tool_call("list_courses", {})),
        _stats_call(),
        _create_call(),
        _final_success,
    ]
    return _run(AgentHarness(build_registry(), log_path="logs/agent_runs.jsonl", llm=ScriptedLLM(script), confirm=_auto(True),
                             observer=print_event), "param_error")


def scenario_tool_exception():
    reset_scenario(COURSE)
    handle, broken = tempfile.mkstemp(suffix=".db", prefix="broken-")
    os.close(handle)
    with open(broken, "wb") as fp:
        fp.write(b"this is not a sqlite database")
    original = db_module.DB_PATH
    db_module.DB_PATH = broken
    try:
        script = [
            _stats_call(),
            assistant_final({"status": "failed",
                             "reason": "数据库暂时不可用，无法读取学习任务数据，本次统计未完成"}),
        ]
        return _run(AgentHarness(build_registry(), log_path="logs/agent_runs.jsonl", llm=ScriptedLLM(script),
                                 observer=print_event), "tool_exception")
    finally:
        db_module.DB_PATH = original
        os.unlink(broken)
        print("（数据库路径已恢复）", flush=True)


def scenario_step_limit():
    reset_scenario(COURSE)
    script = [assistant_tool_calls(tool_call("list_courses", {})) for _ in range(10)]
    return _run(AgentHarness(build_registry(), log_path="logs/agent_runs.jsonl", llm=ScriptedLLM(script), max_steps=3,
                             observer=print_event), "step_limit")


def scenario_time_limit():
    reset_scenario(COURSE)
    script = [assistant_tool_calls(tool_call("list_courses", {}))]
    return _run(AgentHarness(build_registry(), log_path="logs/agent_runs.jsonl", llm=SlowScriptedLLM(script, delay=1.5),
                             deadline_s=1.0, observer=print_event), "time_limit")


def scenario_confirm(approved: bool):
    reset_scenario(COURSE)
    script = [
        assistant_tool_calls(tool_call("list_courses", {})),
        _stats_call(),
        _create_call(),
        _final_success if approved else _final_no_create,
    ]
    return _run(AgentHarness(build_registry(), log_path="logs/agent_runs.jsonl", llm=ScriptedLLM(script), confirm=_auto(approved),
                             observer=print_event), "confirm_approve" if approved else "confirm_deny")


def scenario_verify_fix():
    reset_scenario(COURSE)
    script = [
        assistant_tool_calls(tool_call("list_courses", {})),
        _stats_call(),
        _create_call(),
        _final_wrong_rate,
        _final_success,
    ]
    return _run(AgentHarness(build_registry(), log_path="logs/agent_runs.jsonl", llm=ScriptedLLM(script),
                             confirm=_auto(True), observer=print_event), "verify_fix")


SCENARIOS = {
    "param_error": scenario_param_error,
    "tool_exception": scenario_tool_exception,
    "step_limit": scenario_step_limit,
    "time_limit": scenario_time_limit,
    "confirm_approve": lambda: scenario_confirm(True),
    "confirm_deny": lambda: scenario_confirm(False),
    "verify_fix": scenario_verify_fix,
}


def _quiet_console() -> None:
    """演示时不在终端重复打印数据库异常的完整堆栈，文件日志仍保留。"""
    import logging

    from app.logging_conf import log

    for handler in log.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            handler.setLevel(logging.CRITICAL)


def main() -> None:
    parser = argparse.ArgumentParser(description="实验6 Agent 异常与确认场景")
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    args = parser.parse_args()
    _quiet_console()
    SCENARIOS[args.scenario]()


if __name__ == "__main__":
    main()