"""对比测试：同一组固定问题分别用“直接模型”和“RAG”回答，记录质量与开销。

用法：
    python3 -m app.tools.compare_test                    # 两组都跑
    python3 -m app.tools.compare_test --only Q7,Q8       # 只跑指定问题
    python3 -m app.tools.compare_test --mode rag --top-k 4

每次调用都是独立的单轮对话，不复用上一次的上下文。
"""

import argparse
import csv
import json
import time
from pathlib import Path

from app.service import qa_service

DATA_DIR = Path(__file__).resolve().parents[2] / "tests" / "data"
QUESTION_FILE = DATA_DIR / "compare_questions.json"
RESULT_JSON = DATA_DIR / "compare_result.json"
RESULT_CSV = DATA_DIR / "compare_result.csv"


def hit_points(answer: str, points: list[str]) -> list[str]:
    return [point for point in points if point in answer]


def run_one(item: dict, mode: str, top_k: int, prompt_version: str) -> dict:
    result = qa_service.answer_question(item["question"], mode=mode, top_k=top_k, prompt_version=prompt_version)
    answer = result["answer"]
    hit = hit_points(answer, item["expected_points"])
    reliable = [c for c in result["citation_checks"] if c.get("index") and c.get("reliable")]
    return {
        "id": item["id"],
        "type": item["type"],
        "question": item["question"],
        "mode": mode,
        "answer": answer,
        "points_expected": item["expected_points"],
        "points_hit": hit,
        "points_score": f"{len(hit)}/{len(item['expected_points'])}" if item["expected_points"] else "无要求",
        "claims_insufficient": (not result["sufficient"]) or ("资料不足" in answer),
        "expect_insufficient": item["expect_insufficient"],
        "citations": result["citations"],
        "citations_reliable": len(reliable),
        "citation_checks": result["citation_checks"],
        "retrieved": [{"index": r["index"], "chunk_id": r["chunk_id"], "score": r["score"],
                       "source_title": r["source_title"], "section": r["section"]} for r in result["retrieved"]],
        "context": result.get("context", ""),
        "prompt_tokens": result["usage"].get("prompt_tokens", 0),
        "completion_tokens": result["usage"].get("completion_tokens", 0),
        "total_tokens": result["usage"].get("total_tokens", 0),
        "latency_ms": result["latency_ms"],
        "cost_yuan": result["cost_yuan"],
        "skipped_reason": result.get("skipped_reason", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="直接模型 vs RAG 对比测试")
    parser.add_argument("--mode", choices=["rag", "direct", "both"], default="both")
    parser.add_argument("--only", help="只跑部分问题，如 Q7,Q8")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--prompt", choices=["v1", "v2", "v3"], default="v3")
    args = parser.parse_args()

    questions = json.loads(QUESTION_FILE.read_text(encoding="utf-8"))
    if args.only:
        wanted = {q.strip() for q in args.only.split(",")}
        questions = [q for q in questions if q["id"] in wanted]
    modes = ["direct", "rag"] if args.mode == "both" else [args.mode]

    rows: list[dict] = []
    started = time.time()
    for item in questions:
        for mode in modes:
            row = run_one(item, mode, args.top_k, args.prompt)
            rows.append(row)
            print(f"{row['id']} {mode:<7} 要点 {row['points_score']:<5} 引用 {len(row['citations'])} 个 "
                  f"资料不足声明 {'是' if row['claims_insufficient'] else '否'} "
                  f"{row['latency_ms']}ms {row['total_tokens']}tokens {row['cost_yuan']}元")

    summary = {
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "prompt_version": args.prompt,
        "top_k": args.top_k,
        "modes": modes,
        "total_calls": len(rows),
        "total_tokens": sum(r["total_tokens"] for r in rows),
        "total_cost_yuan": round(sum(r["cost_yuan"] for r in rows), 6),
        "total_seconds": round(time.time() - started, 1),
        "rows": rows,
    }
    RESULT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    with RESULT_CSV.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["编号", "类型", "问题", "模式", "回答", "要点命中", "声明资料不足", "引用数",
                         "引用可靠数", "耗时ms", "输入tokens", "输出tokens", "费用元"])
        for row in rows:
            writer.writerow([row["id"], row["type"], row["question"], row["mode"], row["answer"],
                             row["points_score"], "是" if row["claims_insufficient"] else "否",
                             len(row["citations"]), row["citations_reliable"], row["latency_ms"],
                             row["prompt_tokens"], row["completion_tokens"], row["cost_yuan"]])

    print("-" * 78)
    print(f"共 {summary['total_calls']} 次调用，合计 {summary['total_tokens']} tokens，"
          f"费用约 {summary['total_cost_yuan']} 元，用时 {summary['total_seconds']} 秒")
    print(f"明细已保存：{RESULT_JSON.name} / {RESULT_CSV.name}")


if __name__ == "__main__":
    main()
