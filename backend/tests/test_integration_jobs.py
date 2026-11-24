"""实验8 集成测试：AI 任务状态、重复提交、取消、重试、恢复与保存。

测试不访问真实模型：用脚本函数替换生成器，模拟成功、降级、慢响应和失败；
后台线程由应用启动的线程池执行，用例只通过接口观察状态，和页面行为一致。
"""

import json
import time

import pytest

from app.errors import BusinessError
from app.integration import config as job_config
from app.repository import db as db_module, job_repo
from app.service import job_service


def _future(days: int) -> str:
    import datetime

    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


@pytest.fixture(autouse=True)
def temp_job_log(tmp_path, monkeypatch):
    """测试事件写入临时日志，避免污染 backend/logs/ai_jobs.jsonl。"""
    monkeypatch.setattr(job_config, "JOB_LOG_PATH", tmp_path / "ai_jobs.jsonl")


@pytest.fixture()
def seeded(client):
    """创建一门带考试目标和知识点的课程，返回 (course_id, points)。"""
    course_id = client.post("/api/courses", json={"name": "软件工程", "term": "2026秋"}).json()["course_id"]
    client.post(
        f"/api/courses/{course_id}/goal",
        json={"exam_date": _future(12), "target_score": 85, "available_minutes": 120},
    )
    for name, mastery in (("需求分析", 62), ("软件架构", 45), ("软件测试", 30)):
        client.post(f"/api/courses/{course_id}/points", json={"name": name, "mastery": mastery})
    points = client.get(f"/api/courses/{course_id}/points").json()["items"]
    return course_id, points


def _result(course_id: int, points: list[dict], status: str = "success", reason: str = "") -> dict:
    tasks = [
        {
            "point": point["name"],
            "point_id": point["point_id"],
            "title": f"复习{point['name']}：梳理要点并做题",
            "date": _future(1),
            "minutes": 30,
            "reason": f"掌握度 {point['mastery']}%，优先安排",
        }
        for point in points[:2]
    ]
    result = {
        "status": status,
        "run_id": "run-fake",
        "generator": "llm_v2" if status == "success" else "llm_v2_fallback",
        "prompt_version": "v2",
        "course_id": course_id,
        "course_name": "软件工程",
        "exam_date": _future(12),
        "summary": "按掌握度从低到高安排复习。",
        "notes": "优先安排薄弱点。",
        "tasks": tasks,
        "checks": {"passed": True, "issues": [], "attempts": []},
        "meta": {"model": "fake-model", "latency_ms": 12, "total_tokens": 180,
                 "cost_yuan": 0.0012, "calls": 1, "repair_attempts": 0},
    }
    if reason:
        result["fallback_reason"] = reason
    return result


def _fake_generate(course_id: int, points: list[dict], *, delay: float = 0.0,
                   status: str = "success", reason: str = ""):
    def generate(_course_id, *, version=None, start_date=None, available_minutes=None,
                 save=False, log_path=None, today=None):
        if delay:
            time.sleep(delay)
        return _result(course_id, points, status, reason)

    return generate


def _wait(client, job_id: str, timeout: float = 6.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/plans/ai-jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed", "canceled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"任务未在 {timeout}s 内结束：{job}")


# —— 正常流程：提交、等待、完成、编辑后保存 —————————————————————————


def test_normal_job_and_confirm(client, seeded, monkeypatch):
    course_id, points = seeded
    monkeypatch.setattr(job_service.generator, "generate", _fake_generate(course_id, points))
    created = client.post("/api/plans/ai-jobs", json={"course_id": course_id})
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    assert created.json()["status"] in ("queued", "running")

    job = _wait(client, job_id)
    assert job["status"] == "succeeded"
    assert job["result"]["tasks"][0]["title"].startswith("复习")
    assert job["confirmed_plan_id"] is None

    confirmed = client.post(f"/api/plans/ai-jobs/{job_id}/confirm", json={"tasks": job["result"]["tasks"]})
    assert confirmed.status_code == 200
    body = confirmed.json()
    assert body["task_count"] == 2 and body["duplicate"] is False
    tasks = client.get(f"/api/tasks?course_id={course_id}").json()["items"]
    assert len(tasks) == 2
    assert client.get(f"/api/plans/ai-jobs/{job_id}").json()["confirmed_plan_id"] == body["plan_id"]


def test_confirm_uses_edited_tasks(client, seeded, monkeypatch):
    course_id, points = seeded
    monkeypatch.setattr(job_service.generator, "generate", _fake_generate(course_id, points))
    job = _wait(client, client.post("/api/plans/ai-jobs", json={"course_id": course_id}).json()["job_id"])
    edited = [dict(task) for task in job["result"]["tasks"]]
    edited[0]["title"] = "手工改写：先做需求分析真题"
    edited[0]["minutes"] = 45
    body = client.post(f"/api/plans/ai-jobs/{job['job_id']}/confirm", json={"tasks": edited}).json()
    tasks = client.get(f"/api/tasks?course_id={course_id}").json()["items"]
    assert body["task_count"] == 2
    assert any(task["title"] == "手工改写：先做需求分析真题" and task["minutes"] == 45 for task in tasks)


# —— 输入校验与重复提交 —————————————————————————————————————


def test_invalid_input_creates_no_job(client):
    course_id = client.post("/api/courses", json={"name": "没有目标", "term": "2026秋"}).json()["course_id"]
    resp = client.post("/api/plans/ai-jobs", json={"course_id": course_id})
    assert resp.status_code == 400
    assert resp.json()["code"] == "GOAL_NOT_FOUND"
    assert client.get(f"/api/plans/ai-jobs?course_id={course_id}").json()["items"] == []


def test_duplicate_submit_returns_active_job(client, seeded, monkeypatch):
    course_id, points = seeded
    monkeypatch.setattr(job_service.generator, "generate", _fake_generate(course_id, points, delay=0.5))
    first = client.post("/api/plans/ai-jobs", json={"course_id": course_id}).json()
    second = client.post("/api/plans/ai-jobs", json={"course_id": course_id}).json()
    assert second["job_id"] == first["job_id"]
    assert second["deduplicated"] is True
    assert len(client.get(f"/api/plans/ai-jobs?course_id={course_id}").json()["items"]) == 1
    _wait(client, first["job_id"])


def test_idempotency_key_reuses_finished_job(client, seeded, monkeypatch):
    course_id, points = seeded
    monkeypatch.setattr(job_service.generator, "generate", _fake_generate(course_id, points))
    first = client.post("/api/plans/ai-jobs",
                        json={"course_id": course_id, "idempotency_key": "click-001"}).json()
    _wait(client, first["job_id"])
    again = client.post("/api/plans/ai-jobs",
                        json={"course_id": course_id, "idempotency_key": "click-001"}).json()
    assert again["job_id"] == first["job_id"]
    assert again["deduplicated"] is True
    assert len(client.get(f"/api/plans/ai-jobs?course_id={course_id}").json()["items"]) == 1


def test_parameter_validation(client, seeded):
    course_id, _ = seeded
    assert client.post("/api/plans/ai-jobs", json={"course_id": course_id, "available_minutes": 5}).status_code == 422
    assert client.post("/api/plans/ai-jobs", json={"course_id": course_id, "start_date": "2026/11/25"}).status_code == 422


# —— 取消、失败、重试与降级 —————————————————————————————————


def test_cancel_running_discards_late_result(client, seeded, monkeypatch):
    course_id, points = seeded
    monkeypatch.setattr(job_service.generator, "generate", _fake_generate(course_id, points, delay=0.5))
    job_id = client.post("/api/plans/ai-jobs", json={"course_id": course_id}).json()["job_id"]
    deadline = time.time() + 2
    while time.time() < deadline:
        if client.get(f"/api/plans/ai-jobs/{job_id}").json()["status"] == "running":
            break
        time.sleep(0.02)
    canceled = client.post(f"/api/plans/ai-jobs/{job_id}/cancel").json()
    assert canceled["status"] == "canceled" and canceled["cancel_requested"] is True
    time.sleep(0.8)  # 等模型调用返回，确认迟到结果被丢弃
    after = client.get(f"/api/plans/ai-jobs/{job_id}").json()
    assert after["status"] == "canceled" and after["result"] is None
    assert client.get(f"/api/tasks?course_id={course_id}").json()["items"] == []
    assert client.post(f"/api/plans/ai-jobs/{job_id}/confirm", json={}).json()["code"] == "JOB_NOT_SUCCEEDED"


def test_cancel_queued_job(client, seeded, monkeypatch):
    course_id, _ = seeded
    monkeypatch.setattr(job_service.runner, "submit", lambda *args, **kwargs: None)
    job_id = client.post("/api/plans/ai-jobs", json={"course_id": course_id}).json()["job_id"]
    assert client.get(f"/api/plans/ai-jobs/{job_id}").json()["status"] == "queued"
    assert client.post(f"/api/plans/ai-jobs/{job_id}/cancel").json()["status"] == "canceled"
    assert job_service.recover_interrupted() == 0  # 已取消的任务不会被启动恢复改写
    assert client.get(f"/api/plans/ai-jobs/{job_id}").json()["status"] == "canceled"


def test_failure_and_retry(client, seeded, monkeypatch):
    course_id, points = seeded
    monkeypatch.setattr(job_config, "SIMULATION_ENABLED", True)
    monkeypatch.setattr(job_config, "SIMULATION_DELAY_SECONDS", 0.05)
    failed = _wait(client, client.post(
        "/api/plans/ai-jobs", json={"course_id": course_id, "simulate": "timeout"}).json()["job_id"])
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "LLM_TIMEOUT"
    assert "超时" in failed["error"]["message"]

    monkeypatch.setattr(job_service.generator, "generate", _fake_generate(course_id, points))
    retried = client.post(f"/api/plans/ai-jobs/{failed['job_id']}/retry")
    assert retried.status_code == 200
    new_job = retried.json()
    assert new_job["job_id"] != failed["job_id"]
    assert new_job["retry_of"] == failed["job_id"]
    done = _wait(client, new_job["job_id"])
    assert done["status"] == "succeeded"


def test_retry_rejected_for_succeeded_job(client, seeded, monkeypatch):
    course_id, points = seeded
    monkeypatch.setattr(job_service.generator, "generate", _fake_generate(course_id, points))
    job = _wait(client, client.post("/api/plans/ai-jobs", json={"course_id": course_id}).json()["job_id"])
    resp = client.post(f"/api/plans/ai-jobs/{job['job_id']}/retry")
    assert resp.status_code == 409 and resp.json()["code"] == "JOB_NOT_RETRYABLE"


def test_fallback_result_is_visible(client, seeded, monkeypatch):
    course_id, points = seeded
    monkeypatch.setattr(
        job_service.generator, "generate",
        _fake_generate(course_id, points, status="fallback", reason="模型调用失败（LLM_TIMEOUT）"))
    job = _wait(client, client.post("/api/plans/ai-jobs", json={"course_id": course_id}).json()["job_id"])
    assert job["status"] == "succeeded"
    assert job["result"]["status"] == "fallback"
    assert "LLM_TIMEOUT" in job["result"]["fallback_reason"]


# —— 持久化、恢复、并发与保存约束 —————————————————————————————


def test_confirm_idempotent(client, seeded, monkeypatch):
    course_id, points = seeded
    monkeypatch.setattr(job_service.generator, "generate", _fake_generate(course_id, points))
    job = _wait(client, client.post("/api/plans/ai-jobs", json={"course_id": course_id}).json()["job_id"])
    first = client.post(f"/api/plans/ai-jobs/{job['job_id']}/confirm", json={}).json()
    second = client.post(f"/api/plans/ai-jobs/{job['job_id']}/confirm", json={}).json()
    assert second["duplicate"] is True and second["plan_id"] == first["plan_id"]
    with db_module.get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM study_plans WHERE course_id = ?", (course_id,)).fetchone()["c"]
    assert count == 1


def test_confirm_rejects_bad_tasks(client, seeded, monkeypatch):
    course_id, points = seeded
    monkeypatch.setattr(job_service.generator, "generate", _fake_generate(course_id, points))
    job = _wait(client, client.post("/api/plans/ai-jobs", json={"course_id": course_id}).json()["job_id"])
    bad_title = client.post(f"/api/plans/ai-jobs/{job['job_id']}/confirm",
                            json={"tasks": [{"title": " ", "date": _future(1), "minutes": 30}]})
    assert bad_title.status_code == 400 and bad_title.json()["code"] == "INVALID_TASK_TITLE"
    late = client.post(f"/api/plans/ai-jobs/{job['job_id']}/confirm",
                       json={"tasks": [{"title": "考试后任务", "date": _future(30), "minutes": 30}]})
    assert late.status_code == 400 and late.json()["code"] == "TASK_DATE_AFTER_EXAM"
    assert client.get(f"/api/plans/ai-jobs/{job['job_id']}").json()["confirmed_plan_id"] is None


def test_recovery_marks_interrupted_job(client, seeded, monkeypatch):
    course_id, _ = seeded
    monkeypatch.setattr(job_service.runner, "submit", lambda *args, **kwargs: None)
    job_id = client.post("/api/plans/ai-jobs", json={"course_id": course_id}).json()["job_id"]
    assert job_service.recover_interrupted() == 1
    job = client.get(f"/api/plans/ai-jobs/{job_id}").json()
    assert job["status"] == "failed" and job["error"]["code"] == "JOB_INTERRUPTED"
    assert "重新生成" in job["error"]["message"]


def test_list_jobs_recovers_result_after_refresh(client, seeded, monkeypatch):
    course_id, points = seeded
    monkeypatch.setattr(job_service.generator, "generate", _fake_generate(course_id, points))
    job = _wait(client, client.post("/api/plans/ai-jobs", json={"course_id": course_id}).json()["job_id"])
    items = client.get(f"/api/plans/ai-jobs?course_id={course_id}").json()["items"]
    assert len(items) == 1
    assert items[0]["job_id"] == job["job_id"]
    assert items[0]["status"] == "succeeded"
    assert len(items[0]["result"]["tasks"]) == 2


def test_interleaved_two_courses_do_not_mix(client, seeded, monkeypatch):
    course_a, points_a = seeded
    goal_date = _future(12)
    course_b = client.post("/api/courses", json={"name": "高等数学", "term": "2026秋"}).json()["course_id"]
    client.post(f"/api/courses/{course_b}/goal",
                json={"exam_date": goal_date, "target_score": 80, "available_minutes": 90})
    for name, mastery in (("极限", 40), ("导数", 55), ("积分", 70)):
        client.post(f"/api/courses/{course_b}/points", json={"name": name, "mastery": mastery})
    points_b = client.get(f"/api/courses/{course_b}/points").json()["items"]

    slow = _fake_generate(course_a, points_a, delay=0.4)
    fast = _fake_generate(course_b, points_b)

    def dispatch(course_id_arg, **kwargs):
        return slow(course_id_arg, **kwargs) if course_id_arg == course_a else fast(course_id_arg, **kwargs)

    monkeypatch.setattr(job_service.generator, "generate", dispatch)
    job_a = client.post("/api/plans/ai-jobs", json={"course_id": course_a}).json()["job_id"]
    job_b = client.post("/api/plans/ai-jobs", json={"course_id": course_b}).json()["job_id"]
    assert job_a != job_b
    done_b = _wait(client, job_b)
    done_a = _wait(client, job_a)
    # B 先返回、A 后返回，两个任务的结果仍各自对应自己的课程和知识点。
    assert done_b["course_id"] == course_b and done_a["course_id"] == course_a
    assert all(task["point"] in {p["name"] for p in points_a} for task in done_a["result"]["tasks"])
    assert all(task["point"] in {p["name"] for p in points_b} for task in done_b["result"]["tasks"])
    client.post(f"/api/plans/ai-jobs/{job_a}/confirm", json={})
    client.post(f"/api/plans/ai-jobs/{job_b}/confirm", json={})
    tasks_a = client.get(f"/api/tasks?course_id={course_a}").json()["items"]
    tasks_b = client.get(f"/api/tasks?course_id={course_b}").json()["items"]
    assert {task["point_name"] for task in tasks_a} <= {p["name"] for p in points_a}
    assert {task["point_name"] for task in tasks_b} <= {p["name"] for p in points_b}


def test_simulation_disabled_by_default(client, seeded, monkeypatch):
    course_id, _ = seeded
    monkeypatch.setattr(job_config, "SIMULATION_ENABLED", False)
    resp = client.post("/api/plans/ai-jobs", json={"course_id": course_id, "simulate": "error"})
    assert resp.status_code == 400 and resp.json()["code"] == "INVALID_SIMULATION"


def test_unknown_job_returns_404(client):
    assert client.get("/api/plans/ai-jobs/job-not-exist").json()["code"] == "JOB_NOT_FOUND"
    assert client.post("/api/plans/ai-jobs/job-not-exist/cancel").status_code == 404
    assert client.post("/api/plans/ai-jobs/job-not-exist/retry").status_code == 404


def test_cleanup_removes_only_old_finished_jobs(client, seeded, monkeypatch):
    course_id, points = seeded
    monkeypatch.setattr(job_service.generator, "generate", _fake_generate(course_id, points))
    job = _wait(client, client.post("/api/plans/ai-jobs", json={"course_id": course_id}).json()["job_id"])
    assert job["status"] == "succeeded"
    with db_module.get_conn() as conn:  # 把结束时间改到 10 天前，模拟超过保留期的记录
        conn.execute("UPDATE ai_jobs SET finished_at = datetime('now', 'localtime', '-10 days') WHERE job_id = ?",
                     (job["job_id"],))
    assert job_service.cleanup_old_jobs(7) == 1
    assert client.get(f"/api/plans/ai-jobs/{job['job_id']}").status_code == 404


def test_concurrent_duplicate_submit_returns_same_job(client, seeded, monkeypatch):
    """并发双击：唯一约束冲突应复用已有任务，而不是抛“数据库不可用”。"""
    import concurrent.futures

    course_id, points = seeded
    monkeypatch.setattr(job_service.generator, "generate", _fake_generate(course_id, points, delay=0.4))

    def submit():
        return client.post("/api/plans/ai-jobs", json={"course_id": course_id})

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: submit(), range(2)))
    assert all(resp.status_code == 202 for resp in responses), [r.text for r in responses]
    assert responses[0].json()["job_id"] == responses[1].json()["job_id"]
    assert len(client.get(f"/api/plans/ai-jobs?course_id={course_id}").json()["items"]) == 1
    _wait(client, responses[0].json()["job_id"])
