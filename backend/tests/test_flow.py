"""StudyPilot 基础功能测试：覆盖正常流程、无效输入、数据不存在和依赖不可用。"""

import datetime

from app.logging_conf import LOG_FILE
from app.repository import db as db_module


def _future(days: int = 30) -> str:
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def _points(client, course_id):
    return client.get(f"/api/courses/{course_id}/points").json()["items"]


def _prepare_plan_inputs(client, course_id, points=(("需求分析", 40), ("软件架构", 60), ("软件测试", 30))):
    client.post(
        f"/api/courses/{course_id}/goal",
        json={"exam_date": _future(30), "target_score": 85},
    )
    for name, mastery in points:
        client.post(f"/api/courses/{course_id}/points", json={"name": name, "mastery": mastery})
    return client.get(f"/api/courses/{course_id}/points").json()["items"]


def test_t1_create_course(client):
    """T1 创建课程成功并可以查询到。"""
    resp = client.post("/api/courses", json={"name": "软件工程", "term": "2026春"})
    assert resp.status_code == 201
    assert resp.json()["name"] == "软件工程"

    items = client.get("/api/courses").json()["items"]
    assert [c["name"] for c in items] == ["软件工程"]


def test_t2_duplicate_course(client, course):
    """T2 重复课程名被拒绝，返回 409 和提示信息。"""
    resp = client.post("/api/courses", json={"name": "软件工程", "term": "2026春"})
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "COURSE_DUPLICATED"
    assert "已存在" in body["message"]


def test_t3_blank_course_name(client):
    """T3 课程名称为空时给出校验提示，不写入数据。"""
    resp = client.post("/api/courses", json={"name": "   ", "term": "2026春"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "EMPTY_COURSE_NAME"
    assert client.get("/api/courses").json()["items"] == []


def test_t4_exam_date_in_the_past(client, course):
    """T4 考试日期早于今天时拒绝保存考试目标。"""
    resp = client.post(
        f"/api/courses/{course}/goal",
        json={"exam_date": "2020-01-01", "target_score": 85},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_EXAM_DATE"


def test_t5_generate_plan_without_points(client, course):
    """T5 没有知识点掌握度时不能生成计划，应返回明确提示。"""
    client.post(f"/api/courses/{course}/goal", json={"exam_date": _future(30), "target_score": 85})
    resp = client.post("/api/plans/generate", json={"course_id": course, "available_minutes": 120})
    assert resp.status_code == 400
    assert resp.json()["code"] == "EMPTY_KNOWLEDGE_POINTS"


def test_t6_generate_plan_rules(client, course):
    """T6 生成的候选任务覆盖全部知识点，且不超出可用时间和考试日期。"""
    _prepare_plan_inputs(client, course)
    resp = client.post("/api/plans/generate", json={"course_id": course, "available_minutes": 120})
    assert resp.status_code == 200
    data = resp.json()

    tasks = data["tasks"]
    assert len(tasks) == 3
    assert {t["point_id"] for t in tasks} == {p["point_id"] for p in _points(client, course)}

    exam_date = data["exam_date"]
    per_day: dict[str, int] = {}
    for task in tasks:
        assert task["date"] < exam_date
        assert task["minutes"] >= 10
        per_day[task["date"]] = per_day.get(task["date"], 0) + task["minutes"]
    assert all(minutes <= 120 for minutes in per_day.values())

    # 掌握度低的知识点应排在前面
    mastery = {p["point_id"]: p["mastery"] for p in _points(client, course)}
    assert mastery[tasks[0]["point_id"]] <= mastery[tasks[-1]["point_id"]]


def test_t7_confirm_plan_creates_tasks(client, course):
    """T7 确认计划后任务写入数据库，可在任务列表中查询。"""
    _prepare_plan_inputs(client, course)
    plan = client.post("/api/plans/generate", json={"course_id": course, "available_minutes": 120}).json()
    resp = client.post("/api/plans/confirm", json={"course_id": course, "tasks": plan["tasks"]})
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"

    saved = client.get(f"/api/tasks?course_id={course}").json()["items"]
    assert len(saved) == 3
    assert all(t["status"] == "todo" for t in saved)


def test_t8_complete_task_updates_mastery(client, course):
    """T8 完成任务并确认掌握度后，任务状态和知识点掌握度同时更新。"""
    _prepare_plan_inputs(client, course)
    plan = client.post("/api/plans/generate", json={"course_id": course, "available_minutes": 120}).json()
    client.post("/api/plans/confirm", json={"course_id": course, "tasks": plan["tasks"]})
    task = client.get(f"/api/tasks?course_id={course}").json()["items"][0]

    resp = client.post(f"/api/tasks/{task['task_id']}/complete", json={"mastery": 75, "note": "已完成习题"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"

    point = [p for p in _points(client, course) if p["point_id"] == task["point_id"]][0]
    assert point["mastery"] == 75
    assert task["task_id"] not in [t["task_id"] for t in client.get(f"/api/tasks?course_id={course}&status=todo").json()["items"]]


def test_t9_complete_missing_task(client, course):
    """T9 完成不存在的任务时返回 404，程序不崩溃。"""
    resp = client.post("/api/tasks/9999/complete", json={"mastery": 60})
    assert resp.status_code == 404
    assert resp.json()["code"] == "TASK_NOT_FOUND"


def test_t10_database_unavailable(client, tmp_path, monkeypatch):
    """T10 数据库文件损坏时返回统一错误信息，并记录异常日志。"""
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is not a sqlite database")
    monkeypatch.setattr(db_module, "DB_PATH", str(broken))

    resp = client.get("/api/courses")
    assert resp.status_code == 500
    assert resp.json()["code"] == "DATABASE_UNAVAILABLE"
    assert "数据库" in resp.json()["message"]

    content = open(LOG_FILE, encoding="utf-8").read()
    assert "数据库操作失败" in content
