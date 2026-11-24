"""原型评测脚本：对同一评测集运行规则基线、v0.5、v0.6，记录输出和检查结果。

评测样例固定保存在 backend/eval/plan_cases.jsonl，每个样例带 base_date，
因此任何时间重跑都使用同一批输入。评测脚本用独立检查器 plan_checks 判断
程序可检查的约束，人工评分从 reports/experiment7/human_scores.json 读取。

用法（在 backend 目录执行）：
    python3 -m app.tools.plan_eval --methods rule,v05 --tag week1
    python3 -m app.tools.plan_eval --methods rule,v05,v06 --tag week2
"""

import argparse
import json
import os
import shutil
import statistics
import tempfile
import time
from datetime import date

from app.errors import BusinessError
from app.planning import config, generator
from app.planning.context import load_input
from app.repository import course_repo, db, goal_repo, knowledge_repo
from app.service import plan_service
from app.tools import plan_checks

METHODS = {
    "rule": {"label": "规则模板（无AI基线）", "generator": "rule_template"},
    "v05": {"label": "v0.5 初版（Prompt v1，直接调用模型）", "generator": "llm_v1"},
    "v06": {"label": "v0.6 修订版（约束预处理+校验+修复+降级）", "generator": "llm_v2"},
}


def load_cases(path: str) -> list[dict]:
    cases = []
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            if line.strip():
                cases.append(json.loads(line))
    return cases


def load_scores(path: str | None) -> dict:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except FileNotFoundError:
        return {}


def seed_case(case: dict) -> tuple[int, str, str]:
    """把样例写入临时数据库，返回 (course_id, 原DB路径, 临时目录)。"""
    tmpdir = tempfile.mkdtemp(prefix="studypilot-plan-eval-")
    original = db.DB_PATH
    db.DB_PATH = os.path.join(tmpdir, "studypilot.db")
    db.init_db()
    data = case["input"]
    course = course_repo.insert_course(data["course"], data.get("term", ""))
    if data.get("exam_date") and data.get("available_minutes"):
        goal_repo.save_goal(course["course_id"], data["exam_date"], data["target_score"], data["available_minutes"])
    for point in data.get("points", []):
        knowledge_repo.insert_point(course["course_id"], point["name"], point["mastery"])
    return course["course_id"], original, tmpdir


def restore_case(original: str, tmpdir: str) -> None:
    db.DB_PATH = original
    shutil.rmtree(tmpdir, ignore_errors=True)


def _rule_tasks(course_id: int, start_date: str | None, available_minutes: int | None) -> list[dict]:
    """规则模板的任务只带 point_id，这里映射回知识点名称，便于统一检查。"""
    tasks = []
    for task in plan_service.generate_plan(
        course_id, available_minutes=available_minutes, start_date=start_date
    ).get("tasks", []):
        point = knowledge_repo.get_point(task["point_id"]) if task.get("point_id") else None
        tasks.append({
            "point": point["name"] if point else "",
            "point_id": task.get("point_id"),
            "title": task["title"],
            "date": task["date"],
            "minutes": task["minutes"],
            "reason": task.get("reason", ""),
        })
    return tasks


def run_case(case: dict, method: str, scores: dict) -> dict:
    """运行一个方法在一个样例上的生成，并做独立检查。"""
    info = METHODS[method]
    base = {"case_id": case["id"], "category": case["category"], "method": method,
            "label": info["label"], "expected_mode": case["expect"]["mode"]}
    start_offset = case["input"].get("start_date")
    available = case["input"].get("available_minutes")
    course_id, original, tmpdir = seed_case(case)
    started = time.time()
    try:
        if method == "rule":
            tasks = _rule_tasks(course_id, start_offset, available)
            latency_ms = int((time.time() - started) * 1000)
            result = {"tasks": tasks, "summary": "", "notes": "", "meta": {
                "model": "rule_template", "latency_ms": latency_ms, "total_tokens": 0,
                "cost_yuan": 0, "calls": 0}}
            status = "success"
            error_code = None
        else:
            version = "v1" if method == "v05" else "v2"
            result = generator.generate(
                course_id,
                version=version,
                start_date=start_offset,
                available_minutes=available,
                today=case["base_date"],
            )
            status = "success"
            error_code = None
    except BusinessError as exc:
        status = "rejected"
        error_code = exc.code
        result = {"tasks": [], "summary": "", "notes": "", "meta": {
            "model": config.MODEL, "latency_ms": int((time.time() - started) * 1000),
            "total_tokens": 0, "cost_yuan": 0, "calls": 0}}
    finally:
        restore_case(original, tmpdir)

    record = dict(base)
    record["status"] = status
    record["error_code"] = error_code
    record["tasks"] = result.get("tasks", [])
    record["summary"] = result.get("summary", "")
    record["notes"] = result.get("notes", "")
    record["meta"] = result.get("meta", {})

    human = (scores.get(case["id"], {}) or {}).get(method)
    if human:
        record["human"] = human

    if case["expect"]["mode"] == "reject":
        if method == "rule":
            record["check"] = None
            record["scored"] = False
            record["passed"] = None
            record["note"] = "规则基线没有输入校验，本类样例只用于检验原型的失败反馈"
        else:
            passed = status == "rejected" and error_code == case["expect"]["error_code"]
            record["check"] = None
            record["scored"] = True
            record["passed"] = passed
    else:
        check = plan_checks.check_plan(
            case,
            result.get("tasks", []),
            summary=result.get("summary", ""),
            notes=result.get("notes", ""),
            generator=info["generator"],
        )
        record["check"] = check
        record["scored"] = True
        record["passed"] = check["passed"]
    return record


def summarize(records: list[dict], methods: list[str]) -> dict:
    summary = {}
    for method in methods:
        rows = [row for row in records if row["method"] == method]
        scored = [row for row in rows if row["scored"]]
        generate = [row for row in rows if row["expected_mode"] == "generate"]
        reject = [row for row in rows if row["expected_mode"] == "reject" and row["scored"]]
        success = [row for row in generate if row["status"] == "success"]
        checks: dict[str, dict] = {}
        for row in generate:
            check = row.get("check") or {}
            for code, entry in (check.get("checks") or {}).items():
                if entry["passed"] is None:
                    continue
                stat = checks.setdefault(code, {"applicable": 0, "passed": 0})
                stat["applicable"] += 1
                stat["passed"] += 1 if entry["passed"] else 0
        human_action = [row["human"]["action"] for row in rows if row.get("human") and "action" in row["human"]]
        human_reason = [row["human"]["reason"] for row in rows if row.get("human") and "reason" in row["human"]]
        summary[method] = {
            "label": METHODS[method]["label"],
            "cases": len(rows),
            "generate_cases": len(generate),
            "generate_passed": sum(1 for row in generate if row.get("passed")),
            "reject_cases": len(reject),
            "reject_passed": sum(1 for row in reject if row.get("passed")),
            "scored_passed": sum(1 for row in scored if row.get("passed")),
            "scored_total": len(scored),
            "avg_latency_ms": round(statistics.mean([row["meta"].get("latency_ms", 0) for row in success]), 0)
            if success else 0,
            "total_tokens": sum(row["meta"].get("total_tokens", 0) for row in rows),
            "total_cost_yuan": round(sum(row["meta"].get("cost_yuan", 0) for row in rows), 6),
            "avg_human_action": round(statistics.mean(human_action), 2) if human_action else None,
            "avg_human_reason": round(statistics.mean(human_reason), 2) if human_reason else None,
            "check_rates": {code: f"{stat['passed']}/{stat['applicable']}" for code, stat in checks.items()},
            "failed_cases": [
                {"case_id": row["case_id"], "issues": (row.get("check") or {}).get("issues", []),
                 "error_code": row.get("error_code")}
                for row in scored if not row.get("passed")
            ],
        }
    return summary


def print_summary(summary: dict) -> None:
    print()
    print("=" * 100)
    print(f"{'方法':<40}{'生成通过':>8}{'拒绝正确':>10}{'平均耗时':>10}{'tokens':>9}{'费用(元)':>10}  人工(可执行/理由)")
    print("-" * 100)
    for method, item in summary.items():
        human = "—"
        if item["avg_human_action"] is not None:
            human = f"{item['avg_human_action']:.2f} / {item['avg_human_reason']:.2f}"
        reject = f"{item['reject_passed']}/{item['reject_cases']}" if item["reject_cases"] else "—"
        print(f"{item['label']:<40}{item['generate_passed']:>5}/{item['generate_cases']:<2}"
              f"{reject:>10}{item['avg_latency_ms']:>8.0f}ms{item['total_tokens']:>9}"
              f"{item['total_cost_yuan']:>10.4f}  {human}")
    print("=" * 100)
    for method, item in summary.items():
        rates = "  ".join(f"{code} {rate}" for code, rate in item["check_rates"].items())
        print(f"{method} 检查通过率：{rates}")
        if item["failed_cases"]:
            failed = "；".join(
                "{}({})".format(row["case_id"], ",".join(row["issues"]) or row["error_code"])
                for row in item["failed_cases"]
            )
            print(f"{method} 失败样例：{failed}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 学习计划原型评测")
    parser.add_argument("--cases", default=str(config.EVAL_SET_PATH))
    parser.add_argument("--methods", default="rule,v05", help="逗号分隔：rule,v05,v06")
    parser.add_argument("--tag", default="run", help="本次评测标签，用于输出文件名")
    parser.add_argument("--out-dir", default=str(config.EVAL_OUTPUT_DIR))
    parser.add_argument("--scores", default=str(config.PROJECT_DIR / "reports" / "experiment7" / "human_scores.json"))
    args = parser.parse_args()

    methods = [name.strip() for name in args.methods.split(",") if name.strip()]
    unknown = [name for name in methods if name not in METHODS]
    if unknown:
        print(f"未知方法：{unknown}，可选 {list(METHODS)}")
        return 2
    cases = load_cases(args.cases)
    scores = load_scores(args.scores)
    os.makedirs(args.out_dir, exist_ok=True)

    all_records: dict[str, list[dict]] = {method: [] for method in methods}
    for case in cases:
        for method in methods:
            record = run_case(case, method, scores)
            all_records[method].append(record)
        line = f"{case['id']} {case['category']:<12}"
        for method in methods:
            row = all_records[method][-1]
            mark = "通过" if row.get("passed") else ("N/A" if row.get("passed") is None else "失败")
            line += f"  {method}={mark}"
            if row.get("check") and row["check"]["issues"]:
                line += "(" + ",".join(row["check"]["issues"]) + ")"
            if row.get("error_code"):
                line += f"({row['error_code']})"
        print(line, flush=True)

    summary = summarize([row for rows in all_records.values() for row in rows], methods)
    for method in methods:
        path = os.path.join(args.out_dir, f"eval-{method}-{args.tag}.jsonl")
        with open(path, "w", encoding="utf-8") as fp:
            for row in all_records[method]:
                fp.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary_path = os.path.join(args.out_dir, f"eval-summary-{args.tag}.json")
    with open(summary_path, "w", encoding="utf-8") as fp:
        json.dump({"tag": args.tag, "cases": len(cases), "methods": methods, "summary": summary},
                  fp, ensure_ascii=False, indent=2)
    print_summary(summary)
    print(f"明细：{args.out_dir}/eval-<method>-{args.tag}.jsonl")
    print(f"汇总：{summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
