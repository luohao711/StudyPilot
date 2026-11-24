"""实验7 AI 学习计划原型测试：输入校验、结构化输出、保存和运行日志。

测试使用临时数据库和脚本模型，不调用外部模型；真实模型调用在
reports/experiment7/runs/ 和 backend/logs/plan_ai_runs.jsonl 中留证。
"""

import json

import pytest

from app.errors import BusinessError
from app.planning import config, generator
from app.planning.context import load_input
from app.planning.schema import PlanOut
from app.repository import plan_repo
from app.service import llm_client


def _future(days: int) -> str:
    import datetime

    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


@pytest.fixture()
def seeded(client):
    """通过接口创建课程、考试目标和知识点，返回课程编号。"""
    course_id = client.post("/api/courses", json={"name": "软件工程", "term": "2026秋"}).json()["course_id"]
    client.post(
        f"/api/courses/{course_id}/goal",
        json={"exam_date": _future(11), "target_score": 85, "available_minutes": 120},
    )
    for name, mastery in (("需求分析", 62), ("软件架构", 45), ("数据库设计", 38), ("软件测试", 30)):
        client.post(f"/api/courses/{course_id}/points", json={"name": name, "mastery": mastery})
    return course_id


def _fake_chat_json(tasks: list[dict], summary: str = "测试计划"):
    def fake(messages, schema, **kwargs):
        output = PlanOut.model_validate({"summary": summary, "tasks": tasks})
        return output, {
            "content": "{}",
            "model": "fake-model",
            "usage": {"prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180},
            "latency_ms": 12,
            "finish_reason": "stop",
        }

    return fake


def _task(point: str, date: str, minutes: int = 20, title: str | None = None) -> dict:
    return {"point": point, "title": title or f"练习{point}", "date": date, "minutes": minutes,
            "reason": f"{point}掌握度较低，先安排练习"}


def test_input_rejects_missing_goal(client):
    course_id = client.post("/api/courses", json={"name": "无目标课程", "term": "2026秋"}).json()["course_id"]
    with pytest.raises(BusinessError) as error:
        load_input(course_id)
    assert error.value.code == "GOAL_NOT_FOUND"


def test_input_rejects_empty_points(client):
    course_id = client.post("/api/courses", json={"name": "无知识点课程", "term": "2026秋"}).json()["course_id"]
    client.post(f"/api/courses/{course_id}/goal", json={"exam_date": _future(10), "target_score": 80})
    with pytest.raises(BusinessError) as error:
        load_input(course_id)
    assert error.value.code == "EMPTY_KNOWLEDGE_POINTS"


def test_input_rejects_too_many_points(client):
    course_id = client.post("/api/courses", json={"name": "知识点很多", "term": "2026秋"}).json()["course_id"]
    client.post(f"/api/courses/{course_id}/goal", json={"exam_date": _future(30), "target_score": 80})
    for index in range(config.MAX_POINTS + 1):
        client.post(f"/api/courses/{course_id}/points", json={"name": f"知识点{index}", "mastery": 50})
    with pytest.raises(BusinessError) as error:
        load_input(course_id)
    assert error.value.code == "TOO_MANY_POINTS"


def test_input_rejects_invalid_available_minutes(seeded):
    with pytest.raises(BusinessError) as error:
        load_input(seeded, available_minutes=10)
    assert error.value.code == "INVALID_AVAILABLE_MINUTES"


def test_generate_returns_normalized_tasks(seeded, monkeypatch):
    tomorrow = _future(1)
    monkeypatch.setattr(llm_client, "chat_json", _fake_chat_json([_task("软件测试", tomorrow)]))
    result = generator.generate(seeded, version="v1")
    assert result["status"] == "success"
    assert result["generator"] == "llm_v1"
    assert result["tasks"][0]["point"] == "软件测试"
    assert result["tasks"][0]["point_id"] is not None
    assert result["meta"]["total_tokens"] == 180
    assert result["meta"]["cost_yuan"] > 0


def test_save_candidate_creates_draft(seeded, monkeypatch):
    tomorrow = _future(1)
    monkeypatch.setattr(llm_client, "chat_json", _fake_chat_json([_task("软件测试", tomorrow)]))
    result = generator.generate(seeded, version="v1", save=True)
    assert result["save"]["status"] == "draft"
    plan = plan_repo.latest_plan(seeded)
    assert plan["plan_id"] == result["save"]["plan_id"]
    assert plan["status"] == "draft"
    assert plan["generator"] == "llm_v1"


def test_v1_does_not_check_business_constraints(seeded, monkeypatch):
    """v0.5 只校验结构：模型给出考试后的日期也会原样返回，由评测检查发现。"""
    after_exam = _future(40)
    monkeypatch.setattr(llm_client, "chat_json", _fake_chat_json([_task("软件测试", after_exam)]))
    result = generator.generate(seeded, version="v1")
    assert result["status"] == "success"
    assert result["tasks"][0]["date"] == after_exam


def test_llm_error_is_raised(seeded, monkeypatch):
    def failing(messages, schema, **kwargs):
        raise BusinessError("模型服务连接失败", 503, "LLM_CONNECTION_ERROR")

    monkeypatch.setattr(llm_client, "chat_json", failing)
    with pytest.raises(BusinessError) as error:
        generator.generate(seeded, version="v1")
    assert error.value.code == "LLM_CONNECTION_ERROR"


def test_run_log_contains_run_end(tmp_path, seeded, monkeypatch):
    log_path = tmp_path / "plan_runs.jsonl"
    tomorrow = _future(1)
    monkeypatch.setattr(llm_client, "chat_json", _fake_chat_json([_task("软件测试", tomorrow)]))
    generator.generate(seeded, version="v1", log_path=str(log_path))
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    events = [record["event"] for record in records]
    assert "run_start" in events
    assert "model_reply" in events
    assert records[-1]["event"] == "run_end"
    assert records[-1]["status"] == "success"


def test_input_no_available_day(client):
    """开始日期不早于考试日期时没有可排期日期。"""
    exam_date = _future(1)
    course_id = client.post("/api/courses", json={"name": "过期课程", "term": "2026秋"}).json()["course_id"]
    client.post(f"/api/courses/{course_id}/goal", json={"exam_date": exam_date, "target_score": 80})
    client.post(f"/api/courses/{course_id}/points", json={"name": "知识点", "mastery": 50})
    with pytest.raises(BusinessError) as error:
        load_input(course_id, start_date=exam_date)
    assert error.value.code == "NO_AVAILABLE_DAY"

# ---------------- v0.6 修订版：校验、修复、降级与接口 ----------------

from app.planning import validator


def _valid_tasks() -> list[dict]:
    return [
        _task("软件测试", _future(1), 30, "梳理软件测试层次并做黑盒测试练习"),
        _task("数据库设计", _future(1), 30, "梳理ER模型并做范式判断例题"),
        _task("软件架构", _future(2), 30, "梳理架构风格并做质量属性分析"),
        _task("需求分析", _future(3), 20, "做用例图与需求规格说明练习"),
    ]


def test_validator_accepts_valid_plan(seeded):
    inp = load_input(seeded)
    tasks = [
        {"point": "软件测试", "title": "做黑盒测试练习", "date": _future(1), "minutes": 30, "reason": "掌握度30%最低"},
        {"point": "数据库设计", "title": "做范式判断例题", "date": _future(2), "minutes": 30, "reason": "掌握度38%较低"},
        {"point": "软件架构", "title": "梳理架构风格", "date": _future(3), "minutes": 30, "reason": "掌握度45%较低"},
        {"point": "需求分析", "title": "回顾需求分析要点", "date": _future(4), "minutes": 20, "reason": "掌握度62%较高"},
    ]
    assert validator.validate_plan(inp, tasks, "先薄弱后巩固", "") == []


def test_validator_catches_unknown_point_and_date(seeded):
    inp = load_input(seeded)
    tasks = [_task("综合复习", _future(30), 30, "全知识点模拟")]
    codes = {issue.code for issue in validator.validate_plan(inp, tasks, "计划", "说明")}
    assert "UNKNOWN_POINT" in codes
    assert "DATE_OUT_OF_WINDOW" in codes
    assert "MISSING_POINTS" in codes


def test_validator_catches_daily_overload_and_minutes(seeded):
    inp = load_input(seeded)
    tasks = [
        _task("软件测试", _future(1), 120, "长任务一"),
        _task("软件测试", _future(1), 120, "长任务二"),
        _task("软件测试", _future(1), 120, "长任务三"),
    ]
    codes = {issue.code for issue in validator.validate_plan(inp, tasks, "计划", "")}
    assert "DAILY_OVERLOAD" in codes
    assert "TOO_MANY_TASKS_PER_POINT" in codes
    assert "MINUTES_RANGE" not in codes


def test_validator_requires_notes_when_infeasible(client):
    exam = _future(1)
    course_id = client.post("/api/courses", json={"name": "冲刺课程", "term": "2026秋"}).json()["course_id"]
    client.post(f"/api/courses/{course_id}/goal", json={"exam_date": exam, "target_score": 70, "available_minutes": 60})
    for name, mastery in (("知识点一", 20), ("知识点二", 30), ("知识点三", 40), ("知识点四", 50),
                          ("知识点五", 60), ("知识点六", 70)):
        client.post(f"/api/courses/{course_id}/points", json={"name": name, "mastery": mastery})
    inp = load_input(course_id)
    assert inp.feasible is False
    tasks = [{"point": "知识点一", "title": "梳理知识点一", "date": _future(0), "minutes": 20, "reason": "最薄弱"}]
    codes = {issue.code for issue in validator.validate_plan(inp, tasks, "计划", "")}
    assert "NOTES_MISSING" in codes
    assert validator.validate_plan(inp, tasks, "计划", "时间不足，无法全部覆盖，优先安排最薄弱点") == []


def _sequence_chat_json(outputs: list[dict]):
    """按顺序返回预设输出，用于测试修复和降级分支。"""
    queue = list(outputs)

    def fake(messages, schema, **kwargs):
        data = queue.pop(0) if queue else outputs[-1]
        output = PlanOut.model_validate(data)
        return output, {
            "content": "{}",
            "model": "fake-model",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            "latency_ms": 10,
            "finish_reason": "stop",
        }

    return fake


def test_v2_success(seeded, monkeypatch):
    monkeypatch.setattr(llm_client, "chat_json", _sequence_chat_json([
        {"summary": "先薄弱后巩固", "tasks": _valid_tasks()},
    ]))
    result = generator.generate(seeded, version="v2")
    assert result["status"] == "success"
    assert result["generator"] == "llm_v2"
    assert result["checks"]["passed"] is True
    assert result["meta"]["repair_attempts"] == 0


def test_v2_repairs_invalid_first_output(seeded, monkeypatch):
    invalid = {"summary": "计划", "tasks": [_task("综合复习", _future(30), 30, "全知识点模拟")]}
    monkeypatch.setattr(llm_client, "chat_json", _sequence_chat_json([
        invalid,
        {"summary": "先薄弱后巩固", "tasks": _valid_tasks()},
    ]))
    result = generator.generate(seeded, version="v2")
    assert result["status"] == "success"
    assert result["meta"]["repair_attempts"] == 1
    assert result["meta"]["calls"] == 2
    assert result["checks"]["passed"] is True


def test_v2_falls_back_after_two_invalid_outputs(seeded, monkeypatch):
    invalid = {"summary": "计划", "tasks": [_task("综合复习", _future(30), 30, "全知识点模拟")]}
    monkeypatch.setattr(llm_client, "chat_json", _sequence_chat_json([invalid, invalid]))
    result = generator.generate(seeded, version="v2")
    assert result["status"] == "fallback"
    assert result["generator"] == "llm_v2_fallback"
    assert result["fallback_reason"].startswith("模型输出未通过约束校验")
    assert result["checks"]["passed"] is True
    assert {task["point"] for task in result["tasks"]} == {"需求分析", "软件架构", "数据库设计", "软件测试"}


def test_v2_falls_back_on_model_error(seeded, monkeypatch):
    def failing(messages, schema, **kwargs):
        raise BusinessError("模型服务连接失败", 503, "LLM_CONNECTION_ERROR")

    monkeypatch.setattr(llm_client, "chat_json", failing)
    result = generator.generate(seeded, version="v2")
    assert result["status"] == "fallback"
    assert "模型调用失败" in result["fallback_reason"]
    assert result["tasks"]
    assert result["checks"]["passed"] is True


def test_generate_ai_api(client, seeded, monkeypatch):
    monkeypatch.setattr(llm_client, "chat_json", _sequence_chat_json([
        {"summary": "先薄弱后巩固", "tasks": _valid_tasks()},
    ]))
    resp = client.post("/api/plans/generate-ai", json={"course_id": seeded})
    assert resp.status_code == 200
    body = resp.json()
    assert body["generator"] == "llm_v2"
    assert body["checks"]["passed"] is True
    assert len(body["tasks"]) == 4


def test_generate_ai_api_rejects_missing_goal(client):
    course_id = client.post("/api/courses", json={"name": "没有目标", "term": "2026秋"}).json()["course_id"]
    resp = client.post("/api/plans/generate-ai", json={"course_id": course_id})
    assert resp.status_code == 400
    assert resp.json()["code"] == "GOAL_NOT_FOUND"
