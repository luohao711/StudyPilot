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
