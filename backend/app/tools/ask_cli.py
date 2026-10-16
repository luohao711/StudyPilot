"""命令行问答入口：展示检索片段、实际发送给模型的输入和最终回答。

用法：
    python3 -m app.tools.ask_cli "StudyPilot 的总体架构分为哪几层？"
    python3 -m app.tools.ask_cli "计划怎么生成？" --mode direct
    python3 -m app.tools.ask_cli "掌握度怎么更新？" --show-context
"""

import argparse
import json

from app.service import llm_client, prompts, qa_service, retriever


def main() -> None:
    parser = argparse.ArgumentParser(description="StudyPilot 资料问答（命令行）")
    parser.add_argument("question", help="用户问题")
    parser.add_argument("--mode", choices=["rag", "direct"], default="rag")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--prompt", choices=["v1", "v2", "v3"], default="v3")
    parser.add_argument("--show-context", action="store_true", help="打印实际发送给模型的完整输入")
    parser.add_argument("--show-retrieval", action="store_true", help="只做检索，不调用模型")
    parser.add_argument("--only-context", action="store_true", help="只打印发送给模型的完整输入，不调用模型")
    args = parser.parse_args()

    print(f"模型：{llm_client.DEFAULT_MODEL}    密钥：{'已配置 ' + llm_client.mask(llm_client.api_key()) if llm_client.is_available() else '未配置'}")
    print(f"问题：{args.question}")
    print(f"模式：{args.mode}    Prompt 版本：{args.prompt}    Top-k：{args.top_k}")
    print("-" * 78)

    hits = retriever.search(args.question, top_k=args.top_k)
    if args.mode == "rag":
        print(f"检索到 {len(hits)} 个片段：")
        for index, hit in enumerate(hits, start=1):
            print(f"  [{index}] 分数 {hit['score']:<7} 《{hit['source_title']}》 {hit['section']}（{hit['chunk_id']}）")
        print("-" * 78)
    if args.show_retrieval:
        for index, hit in enumerate(hits, start=1):
            print(f"[{index}] {hit['text'][:150]}…")
        return

    if args.show_context:
        if args.mode == "rag":
            messages = prompts.rag_messages(args.question, qa_service.build_context(hits), args.prompt)
        else:
            messages = prompts.direct_messages(args.question, args.prompt)
        print("实际发送给模型的输入（messages）：")
        print(json.dumps(messages, ensure_ascii=False, indent=2)[:2600])
        print("-" * 78)
        if args.only_context:
            return

    result = qa_service.answer_question(args.question, mode=args.mode, top_k=args.top_k, prompt_version=args.prompt)
    print("回答：")
    print(result["answer"])
    print("-" * 78)
    print(f"是否依据资料回答：{'是' if result['sufficient'] else '否（资料不足）'}")
    if result["citations"]:
        print("引用：")
        for item in result["citation_checks"]:
            if item.get("index"):
                print(f"  [{item['index']}] {item['source_title']} / {item['section']}，"
                      f"词语重合度 {item['overlap']}，{'对应' if item['reliable'] else '需人工复核'}")
    usage = result["usage"] or {}
    print(f"耗时：{result['latency_ms']} ms    输入 {usage.get('prompt_tokens', 0)} tokens，"
          f"输出 {usage.get('completion_tokens', 0)} tokens，合计 {usage.get('total_tokens', 0)} tokens    "
          f"费用约 {result['cost_yuan']} 元")
    if result.get("skipped_reason"):
        print(f"说明：{result['skipped_reason']}（未调用模型）")


if __name__ == "__main__":
    main()
