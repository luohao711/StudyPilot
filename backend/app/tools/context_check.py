"""上下文检查：查看检索片段里的重复与无关内容，并比较精简前后的回答。

用法：python3 -m app.tools.context_check "StudyPilot 的总体架构分为哪几层？"
"""

import argparse
import re

from app.service import llm_client, prompts, qa_service, retriever


def duplicate_ratio(texts: list[str]) -> tuple[int, list[str]]:
    """统计相邻片段之间重复的句子（切分时保留了重叠部分）。"""
    seen: set[str] = set()
    duplicated: list[str] = []
    duplicated_chars = 0
    for text in texts:
        for sentence in [s.strip() for s in re.split(r"[。\n]", text) if len(s.strip()) >= 12]:
            if sentence in seen:
                duplicated.append(sentence)
                duplicated_chars += len(sentence)
            seen.add(sentence)
    return duplicated_chars, duplicated


def main() -> None:
    parser = argparse.ArgumentParser(description="上下文精简检查")
    parser.add_argument("question")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--min-score", type=float, default=retriever.MIN_SCORE,
                        help="相关性阈值，调低可以看到更多边缘片段")
    args = parser.parse_args()

    hits = retriever.search(args.question, top_k=args.top_k, min_score=args.min_score)
    if not hits:
        print("没有检索到片段")
        return

    context = qa_service.build_context(hits)
    duplicated_chars, duplicated = duplicate_ratio([h["text"] for h in hits])
    print(f"问题：{args.question}")
    print(f"检索片段：{len(hits)} 个")
    for index, hit in enumerate(hits, start=1):
        overlap = "有" if hit["overlap_prev"] else "无"
        print(f"  [{index}] {hit['chunk_id']}  分数 {hit['score']:<7} 字数 {hit['chars']:<5} "
              f"与前一片段重叠：{overlap}  《{hit['source_title']}》 {hit['section']}")
    print(f"上下文总字数：{len(context)}    重复句子字数：{duplicated_chars}")
    for sentence in duplicated[:3]:
        print(f"  重复内容示例：{sentence[:40]}…")
    print("-" * 78)

    messages = prompts.rag_messages(args.question, context, "v3")
    result = llm_client.chat_json(messages, prompts.RagAnswer, temperature=0.2, max_tokens=700)
    answer_full, raw_full = result
    print(f"【完整上下文】prompt tokens {raw_full['usage'].get('prompt_tokens')}    回答：{answer_full.answer[:150]}")

    trimmed_hits = [hit for hit in hits if hit["score"] >= 0.6 * hits[0]["score"]]
    trimmed_context = qa_service.build_context(trimmed_hits)
    messages2 = prompts.rag_messages(args.question, trimmed_context, "v3")
    answer_trim, raw_trim = llm_client.chat_json(messages2, prompts.RagAnswer, temperature=0.2, max_tokens=700)
    print(f"【精简上下文】只保留分数不低于最高分 60% 的片段：{[h['chunk_id'] for h in trimmed_hits]}")
    print(f"              上下文字数 {len(trimmed_context)}（原 {len(context)}，减少 "
          f"{round((1 - len(trimmed_context) / len(context)) * 100)}%）  prompt tokens "
          f"{raw_trim['usage'].get('prompt_tokens')}    回答：{answer_trim.answer[:150]}")
    print(f"              精简后引用：{sorted(set(answer_trim.citations))}    是否仍然依据资料：{answer_trim.sufficient}")


if __name__ == "__main__":
    main()
