"""实验8 版本对比：同一门课程、同一个模型下，比较 v0.6 同步入口与 v0.7 任务式入口。

v0.6：页面直接 POST /api/plans/generate-ai，请求一直等到模型返回（实验7的页面入口）。
v0.7：POST /api/plans/ai-jobs 立即返回任务编号，页面轮询状态；可取消、可恢复、可重试。

在前后端已启动、后端配置了真实模型密钥时运行：
    cd backend
    python3 -m app.tools.integration_compare --course 1 --rounds 3

输出 reports/experiment8/runs/integration-compare.json，报告中的表8-12使用该文件。
"""

import argparse
import json
import statistics
import time
from pathlib import Path

import httpx

PROJECT_DIR = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_DIR / "reports" / "experiment8" / "runs"
BASE = "http://127.0.0.1:8000"


def wait_terminal(client: httpx.Client, job_id: str, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"{BASE}/api/plans/ai-jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed", "canceled"):
            return job
        time.sleep(0.1)
    raise RuntimeError(f"job {job_id} 未在 {timeout}s 内结束")


def run_sync(client: httpx.Client, course_id: int) -> dict:
    started = time.time()
    resp = client.post(f"{BASE}/api/plans/generate-ai", json={"course_id": course_id}, timeout=120)
    elapsed = int((time.time() - started) * 1000)
    data = resp.json()
    return {
        "mode": "v0.6_sync",
        "http_status": resp.status_code,
        "submit_ms": elapsed,
        "total_ms": elapsed,
        "status": data.get("status"),
        "checks_passed": (data.get("checks") or {}).get("passed"),
        "tasks": len(data.get("tasks", [])),
        "total_tokens": (data.get("meta") or {}).get("total_tokens", 0),
        "cost_yuan": (data.get("meta") or {}).get("cost_yuan", 0),
        "cancellable": False,
        "recoverable_after_refresh": False,
        "duplicate_submit_deduped": False,
    }


def run_async(client: httpx.Client, course_id: int, *, duplicate_probe: bool = False) -> dict:
    started = time.time()
    resp = client.post(f"{BASE}/api/plans/ai-jobs", json={"course_id": course_id}, timeout=30)
    submit_ms = int((time.time() - started) * 1000)
    created = resp.json()
    job_id = created["job_id"]
    dedupe = None
    if duplicate_probe:
        dedupe_started = time.time()
        again = client.post(f"{BASE}/api/plans/ai-jobs", json={"course_id": course_id}, timeout=30).json()
        dedupe = {
            "same_job": again["job_id"] == job_id,
            "deduplicated": again.get("deduplicated", False),
            "submit_ms": int((time.time() - dedupe_started) * 1000),
        }
    job = wait_terminal(client, job_id, timeout=180)
    total_ms = int((time.time() - started) * 1000)
    result = job.get("result") or {}
    return {
        "mode": "v0.7_async",
        "http_status": resp.status_code,
        "job_id": job_id,
        "submit_ms": submit_ms,
        "total_ms": total_ms,
        "status": job["status"],
        "result_status": result.get("status"),
        "checks_passed": (result.get("checks") or {}).get("passed"),
        "tasks": len(result.get("tasks", [])),
        "total_tokens": (result.get("meta") or {}).get("total_tokens", 0),
        "cost_yuan": (result.get("meta") or {}).get("cost_yuan", 0),
        "cancellable": True,
        "recoverable_after_refresh": True,
        "duplicate_probe": dedupe,
    }


def summarize(rows: list[dict]) -> dict:
    def agg(mode: str, field: str) -> dict:
        values = [row[field] for row in rows if row["mode"] == mode and row.get(field) is not None]
        if not values:
            return {"avg": 0, "min": 0, "max": 0}
        return {"avg": round(statistics.mean(values), 1), "min": min(values), "max": max(values)}

    return {
        "rounds": len([row for row in rows if row["mode"] == "v0.7_async"]),
        "v0.6_submit_ms": agg("v0.6_sync", "submit_ms"),
        "v0.7_submit_ms": agg("v0.7_async", "submit_ms"),
        "v0.6_total_ms": agg("v0.6_sync", "total_ms"),
        "v0.7_total_ms": agg("v0.7_async", "total_ms"),
        "v0.6_checks_passed": sum(1 for row in rows if row["mode"] == "v0.6_sync" and row.get("checks_passed")),
        "v0.7_checks_passed": sum(1 for row in rows if row["mode"] == "v0.7_async" and row.get("checks_passed")),
        "v0.6_failures": sum(1 for row in rows if row["mode"] == "v0.6_sync" and row.get("status") != "success"),
        "v0.7_failures": sum(1 for row in rows if row["mode"] == "v0.7_async" and row.get("status") != "succeeded"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="实验8 v0.6/v0.7 集成方式对比")
    parser.add_argument("--course", type=int, default=1)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--out", default=str(OUTPUT_DIR / "integration-compare.json"))
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    with httpx.Client(timeout=180) as client:
        health = client.get(f"{BASE}/api/health").json()
        assert health["version"].startswith("0.7"), health
        for index in range(args.rounds):
            rows.append(run_sync(client, args.course))
            rows.append(run_async(client, args.course, duplicate_probe=index == 0))
    summary = summarize(rows)
    output = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "course_id": args.course,
        "model": "deepseek-chat",
        "rows": rows,
        "summary": summary,
    }
    Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("saved", args.out)


if __name__ == "__main__":
    main()
