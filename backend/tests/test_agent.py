"""实验6测试：工具层、Harness 循环、限制、人工确认与结果核验。

正常流程必须真实执行工具；模型决策在单元测试中用脚本模型模拟，
真实模型调用见命令行运行记录（app/tools/agent_cli.py）。
"""

import json
import time

import pytest

from app.agent.runlog import summarize

from app.agent import fakes, verifier
from app.agent.harness import AgentHarness
from app.agent.runlog import mask_text
from app.agent.scenario import reset_scenario
from app.agent.spec import ToolFailure
from app.agent.tools import build_registry
from app.repository import db as db_module


@pytest.fixture()
def seeded(client):
    """临时数据库中的样例场景：12 条任务、完成率 41.7%。"""
    summary = reset_scenario()
    assert summary["tasks"] == 12
    return verifier.ground_truth("软件工程", 14)


def _confirm(approved: bool):
    def callback(request):
        return {"approved": approved, "reason": "测试确认", "waited_ms": 10}

    return callback


def _harness(tmp_path, llm, **kwargs) -> AgentHarness:
    kwargs.setdefault("log_path", str(tmp_path / "agent.jsonl"))
    return AgentHarness(build_registry(), llm=llm, **kwargs)


def _success_script(gt: dict, *, with_create: bool = True, claim_created: bool = True) -> list:
    script = [
        fakes.assistant_tool_calls(fakes.tool_call("list_courses", {})),
        fakes.assistant_tool_calls(fakes.tool_call("task_stats", {"course": "软件工程", "days": 14})),
    ]
    if with_create:
        script.append(fakes.assistant_tool_calls(fakes.tool_call(
            "create_review_task", {"course": "软件工程", "point": "软件测试", "minutes": 30})))
    script.append(lambda messages: fakes.assistant_final(fakes.success_answer(
        gt, review_task_id=fakes.last_created_id(messages) if claim_created else None)))
    return script


def _review_task_count() -> int:
    with db_module.get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM daily_tasks WHERE title LIKE '复习：%'").fetchone()["c"]


# ---------------- 工具层 ----------------

def test_tool_list_courses_and_stats(seeded):
    reg = build_registry()
    spec, parsed = reg.validate("list_courses", {})
    courses = reg.execute(spec, parsed, 3.0)["data"]
    assert "软件工程" in {c["name"] for c in courses["courses"]}

    spec, parsed = reg.validate("task_stats", {"course": "软件工程", "days": 14})
    stats = reg.execute(spec, parsed, 3.0)["data"]
    assert stats["completion_rate"] == seeded["completion_rate"] == 41.7
    assert (stats["total_tasks"], stats["done_tasks"], stats["pending_tasks"]) == (12, 5, 7)
    assert [p["point"] for p in stats["weakest_points"]] == seeded["weakest_points"] == ["软件测试", "数据库设计"]
    assert stats["total_minutes"] == 420


def test_tool_query_tasks_filters(seeded):
    reg = build_registry()
    spec, parsed = reg.validate("query_tasks", {"course": "软件工程", "days": 14, "status": "todo"})
    data = reg.execute(spec, parsed, 3.0)["data"]
    assert data["summary"] == {"total": 7, "done": 0, "pending": 7}
    assert all(task["status"] == "todo" for task in data["tasks"])


def test_tool_validation_rejects_bad_arguments(seeded):
    reg = build_registry()
    with pytest.raises(ToolFailure) as error:
        reg.validate("task_stats", {"course": "软件工程", "days": 90})
    assert error.value.code == "INVALID_ARGUMENTS"

    with pytest.raises(ToolFailure) as error:
        reg.validate("query_tasks", {"course": "软件工程", "days": "十四"})
    assert error.value.code == "INVALID_ARGUMENTS"

    with pytest.raises(ToolFailure) as error:
        reg.validate("task_stats", {"course": "软件工程", "days": 7, "sql": "DROP TABLE"})
    assert error.value.code == "INVALID_ARGUMENTS"


def test_tool_rejects_unknown_tool_and_course(seeded):
    reg = build_registry()
    with pytest.raises(ToolFailure) as error:
        reg.validate("run_sql", {"sql": "DELETE FROM daily_tasks"})
    assert error.value.code == "UNKNOWN_TOOL"

    spec, parsed = reg.validate("query_tasks", {"course": "不存在的课程", "days": 14})
    with pytest.raises(ToolFailure) as error:
        reg.execute(spec, parsed, 3.0)
    assert error.value.code == "COURSE_NOT_FOUND"


def test_tool_create_dedupe(seeded):
    reg = build_registry()
    spec, parsed = reg.validate("create_review_task",
                                {"course": "软件工程", "point": "软件测试", "minutes": 30})
    first = reg.execute(spec, parsed, 3.0)["data"]
    assert first["created"] is True
    assert first["title"] == "复习：软件测试"
    second = reg.execute(spec, parsed, 3.0)["data"]
    assert second["created"] is False
    assert second["reason"] == "ALREADY_EXISTS"
    assert second["task_id"] == first["task_id"]
    assert _review_task_count() == 1


# ---------------- Harness 正常与参数错误 ----------------

def test_harness_normal_success(seeded, tmp_path):
    llm = fakes.ScriptedLLM(_success_script(seeded))
    result = _harness(tmp_path, llm, confirm=_confirm(True)).run(
        "统计任务", course="软件工程", days=14)
    assert result.status == "success"
    assert result.error is None
    assert result.steps == 4
    assert result.created_task_ids and len(result.created_task_ids) == 1
    assert all(check["ok"] for check in result.checks)
    assert _review_task_count() == 1

    # 工具调用请求与工具结果按 tool_call_id 配对进入后续上下文
    for call_messages in llm.calls:
        for index, message in enumerate(call_messages):
            if message.get("role") == "assistant" and message.get("tool_calls"):
                expected = {call["id"] for call in message["tool_calls"]}
                following = call_messages[index + 1:index + 1 + len(expected)]
                assert {m.get("tool_call_id") for m in following if m.get("role") == "tool"} == expected

    kinds = [event["kind"] for event in result.trace]
    for kind in ("run_start", "model_call", "tool_result", "confirm", "verify", "final_answer", "run_end"):
        assert kind in kinds

    log_lines = (tmp_path / "agent.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in log_lines]
    assert events[0]["event"] == "run_start"
    assert events[-1]["event"] == "run_end"
    assert events[-1]["status"] == "success"


def test_harness_param_error_feedback(seeded, tmp_path):
    script = [
        fakes.assistant_tool_calls(fakes.tool_call("query_tasks", {"course": "软件工程", "days": 90})),
        *_success_script(seeded),
    ]
    llm = fakes.ScriptedLLM(script)
    result = _harness(tmp_path, llm, confirm=_confirm(True)).run(
        "统计任务", course="软件工程", days=14)
    assert result.status == "success"
    rejected = [e for e in result.trace if e["kind"] == "tool_rejected"]
    assert rejected and rejected[0]["code"] == "INVALID_ARGUMENTS"
    # 程序没有执行错误参数，第一次工具结果以错误信息回传模型
    assert result.steps == 5


def test_harness_unknown_tool_rejected(seeded, tmp_path):
    script = [
        fakes.assistant_tool_calls(fakes.tool_call("run_sql", {"sql": "DELETE FROM daily_tasks"})),
        *_success_script(seeded),
    ]
    result = _harness(tmp_path, fakes.ScriptedLLM(script), confirm=_confirm(True)).run(
        "统计任务", course="软件工程", days=14)
    assert result.status == "success"
    rejected = [e for e in result.trace if e["kind"] == "tool_rejected"]
    assert rejected and rejected[0]["code"] == "UNKNOWN_TOOL"


# ---------------- 限制与失败处理 ----------------

def test_harness_step_limit(seeded, tmp_path):
    script = [fakes.assistant_tool_calls(fakes.tool_call("list_courses", {})) for _ in range(10)]
    result = _harness(tmp_path, fakes.ScriptedLLM(script), max_steps=3).run(
        "统计任务", course="软件工程", days=14)
    assert result.status == "step_limit"
    assert result.steps == 3
    assert result.error["code"] == "STEP_LIMIT"


def test_harness_time_limit(seeded, tmp_path):
    llm = fakes.SlowScriptedLLM(
        [fakes.assistant_tool_calls(fakes.tool_call("list_courses", {}))], delay=0.6)
    result = _harness(tmp_path, llm, deadline_s=0.3).run(
        "统计任务", course="软件工程", days=14)
    assert result.status == "time_limit"
    assert result.duration_ms >= 500
    assert result.error["code"] == "TIME_LIMIT"


def test_harness_tool_exception_feedback(seeded, tmp_path):
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is not a sqlite database")
    original = db_module.DB_PATH
    db_module.DB_PATH = str(broken)
    try:
        script = [
            fakes.assistant_tool_calls(fakes.tool_call("task_stats", {"course": "软件工程", "days": 14})),
            fakes.assistant_final({"status": "failed",
                                   "reason": "数据库暂时不可用，本次统计未完成"}),
        ]
        result = _harness(tmp_path, fakes.ScriptedLLM(script)).run(
            "统计任务", course="软件工程", days=14)
    finally:
        db_module.DB_PATH = original

    assert result.status == "agent_failed"
    failed = [e for e in result.trace if e["kind"] == "tool_result" and not e["ok"]]
    assert failed and failed[0]["code"] == "DATABASE_UNAVAILABLE"
    retries = [e for e in result.trace if e["kind"] == "tool_retry"]
    assert len(retries) == 2  # 只读工具最多重试 2 次后报错


def test_harness_final_not_json(seeded, tmp_path):
    script = [
        {"role": "assistant", "content": "我完成了统计"},
        {"role": "assistant", "content": "已经完成"},
    ]
    result = _harness(tmp_path, fakes.ScriptedLLM(script), max_corrections=1).run(
        "统计任务", course="软件工程", days=14)
    assert result.status == "verify_failed"
    assert result.error["code"] == "INVALID_FINAL"


# ---------------- 人工确认 ----------------

def test_harness_confirmation_approve_and_deny(seeded, tmp_path):
    approve = _harness(tmp_path, fakes.ScriptedLLM(_success_script(seeded)),
                       confirm=_confirm(True)).run("统计任务", course="软件工程", days=14)
    assert approve.status == "success"
    assert len(approve.created_task_ids) == 1
    assert _review_task_count() == 1

    reset_scenario()
    deny = _harness(tmp_path, fakes.ScriptedLLM(_success_script(seeded, claim_created=False)),
                    confirm=_confirm(False)).run("统计任务", course="软件工程", days=14)
    assert deny.status == "success"
    assert deny.created_task_ids == []
    assert deny.denied_tools == ["create_review_task"]
    assert _review_task_count() == 0
    assert all(check["ok"] for check in deny.checks)


# ---------------- 结果核验 ----------------

def test_harness_verify_feedback_then_fix(seeded, tmp_path):
    script = [
        fakes.assistant_tool_calls(fakes.tool_call("task_stats", {"course": "软件工程", "days": 14})),
        fakes.assistant_tool_calls(fakes.tool_call(
            "create_review_task", {"course": "软件工程", "point": "软件测试", "minutes": 30})),
        lambda messages: fakes.assistant_final(fakes.success_answer(
            seeded, review_task_id=fakes.last_created_id(messages), completion_rate=50.0)),
        lambda messages: fakes.assistant_final(fakes.success_answer(
            seeded, review_task_id=fakes.last_created_id(messages))),
    ]
    result = _harness(tmp_path, fakes.ScriptedLLM(script), confirm=_confirm(True)).run(
        "统计任务", course="软件工程", days=14)
    assert result.status == "success"
    rejected = [e for e in result.trace if e["kind"] == "final_rejected"]
    assert rejected and "完成率" in rejected[0]["message"]


def test_harness_verify_failed_after_corrections(seeded, tmp_path):
    wrong = fakes.assistant_final(fakes.success_answer(seeded, completion_rate=50.0))
    script = [
        fakes.assistant_tool_calls(fakes.tool_call("task_stats", {"course": "软件工程", "days": 14})),
        wrong,
        wrong,
    ]
    result = _harness(tmp_path, fakes.ScriptedLLM(script), max_corrections=1).run(
        "统计任务", course="软件工程", days=14)
    assert result.status == "verify_failed"
    assert result.error["code"] == "VERIFY_FAILED"
    assert not all(check["ok"] for check in result.checks)


def test_success_check_rejects_model_claim(seeded):
    answer = fakes.success_answer(seeded, completion_rate=99.9)
    checks = verifier.verify_success(answer, seeded, {})
    assert any(not check["ok"] for check in checks)

    created = fakes.success_answer(seeded, review_task_id=99999)
    checks = verifier.verify_success(created, seeded, {"created_task_ids": [], "denied_tools": []})
    assert any(check["name"] == "条件写操作" and not check["ok"] for check in checks)


# ---------------- 日志与脱敏 ----------------

def test_run_log_masks_secrets_and_records_errors(seeded, tmp_path):
    assert "sk-***" in mask_text("key=sk-abcdef123456")
    result = _harness(tmp_path, fakes.ScriptedLLM(_success_script(seeded)),
                      confirm=_confirm(True)).run("统计任务", course="软件工程", days=14)
    log_text = (tmp_path / "agent.jsonl").read_text(encoding="utf-8")
    assert "sk-abcdef" not in log_text
    lines = [json.loads(line) for line in log_text.splitlines()]
    assert any(line["event"] == "confirm" and line["approved"] for line in lines)
    assert any(line["event"] == "run_end" and line["status"] == "success" for line in lines)

# ---------------- 截止时间与重复写保护 ----------------

def test_confirmation_wait_exceeds_deadline_blocks_write(seeded, tmp_path):
    """确认等待耗尽了总时限：即使同意也不执行写操作。"""
    def slow_confirm(request):
        time.sleep(0.6)
        return {"approved": True, "reason": "慢确认", "waited_ms": 600}

    script = [fakes.assistant_tool_calls(fakes.tool_call(
        "create_review_task", {"course": "软件工程", "point": "软件测试", "minutes": 30}))]
    result = _harness(tmp_path, fakes.ScriptedLLM(script),
                      confirm=slow_confirm, deadline_s=0.4).run(
        "统计任务", course="软件工程", days=14)
    assert result.status == "time_limit"
    assert result.created_task_ids == []
    assert _review_task_count() == 0


def test_write_denied_only_asks_once(seeded, tmp_path):
    """用户拒绝后，模型再次请求同一写操作时程序不再询问、也不执行。"""
    calls = {"count": 0}

    def deny(request):
        calls["count"] += 1
        return {"approved": False, "reason": "测试拒绝", "waited_ms": 5}

    create = fakes.assistant_tool_calls(fakes.tool_call(
        "create_review_task", {"course": "软件工程", "point": "软件测试", "minutes": 30}))
    script = [
        create,
        create,
        lambda messages: fakes.assistant_final(fakes.success_answer(seeded)),
    ]
    result = _harness(tmp_path, fakes.ScriptedLLM(script), confirm=deny).run(
        "统计任务", course="软件工程", days=14)
    assert result.status == "success"
    assert calls["count"] == 1
    assert _review_task_count() == 0
    assert any(event["kind"] == "tool_rejected" and event["code"] == "WRITE_ALREADY_DENIED"
               for event in result.trace)
    assert all(check["ok"] for check in result.checks)


# ---------------- 核验的写操作一致性 ----------------

def _gt(rate: float) -> dict:
    return {"completion_rate": rate, "total_tasks": 10, "done_tasks": 8,
            "pending_tasks": 2, "weakest_points": ["A", "B"]}


def _answer(rate: float, claimed) -> dict:
    return {"status": "success", "completion_rate": rate, "total_tasks": 10,
            "done_tasks": 8, "pending_tasks": 2, "weakest_points": ["A", "B"],
            "review_task_created": claimed, "review_task_id": None,
            "suggestion": "先复习薄弱知识点并完成剩余任务。"}


def test_verify_rejects_phantom_write(seeded):
    """完成率>=60时声称未创建但实际写入，也应判为核验失败。"""
    checks = verifier.verify_success(_answer(80.0, False), _gt(80.0),
                                     {"created_task_ids": [123], "denied_tools": ["create_review_task"]})
    assert any(check["name"] == "条件写操作" and not check["ok"] for check in checks)

    checks = verifier.verify_success(_answer(41.7, False), _gt(41.7),
                                     {"created_task_ids": [123], "denied_tools": ["create_review_task"]})
    assert any(check["name"] == "条件写操作" and not check["ok"] for check in checks)


# ---------------- 超时与展示辅助 ----------------

def test_registry_tool_timeout(seeded):
    from app.agent.registry import ToolRegistry
    from app.agent.spec import ToolFailure, ToolSpec
    from app.agent.tools import ListCoursesArgs

    def slow_handler(args):
        time.sleep(0.3)
        return {}

    spec = ToolSpec(name="slow_tool", description="测试超时", args_model=ListCoursesArgs,
                    handler=slow_handler, timeout_s=0.05)
    registry = ToolRegistry([spec])
    with pytest.raises(ToolFailure) as error:
        registry.execute(spec, ListCoursesArgs(), timeout_s=0.05)
    assert error.value.code == "TOOL_TIMEOUT"
    assert error.value.retriable is True


def test_cli_confirmation_timeout(monkeypatch):
    from app.tools import agent_cli

    monkeypatch.setattr(agent_cli.select, "select", lambda *args: ([], [], []))
    decision = agent_cli.make_confirm("ask", timeout_s=0.01)(None)
    assert decision["approved"] is False
    assert "超时" in decision["reason"]


def test_summarize_keeps_strings_plain():
    assert summarize("hello") == "hello"
    assert summarize({"a": 1}) == '{"a":1}'
