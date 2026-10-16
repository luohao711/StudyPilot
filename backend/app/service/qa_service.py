"""RAG 最小链路：检索 → 组织上下文 → 调用模型 → 校验引用 → 返回答案与出处。"""

import re
import time

from app.errors import BusinessError
from app.logging_conf import log
from app.service import kb_service, llm_client, prompts, retriever

MAX_QUESTION_CHARS = 200
DEFAULT_TOP_K = 3
# 费用估算口径：模型 deepseek-chat（官方旧别名，实际由 DeepSeek-V4.1-Flash 提供）。
# 官网高峰价：输入 $0.3/百万 tokens（缓存未命中）、输出 $1.2/百万 tokens，
# 按汇率 7.1 折算约 2.13 元 / 8.52 元，这里取 2 元和 8 元做保守近似。
PRICE_INPUT_PER_M = 2.0
PRICE_OUTPUT_PER_M = 8.0
INSUFFICIENT_TEXT = "资料不足：知识库中没有找到与该问题相关的内容，无法回答。"


def build_context(hits: list[dict]) -> str:
    lines = []
    for index, hit in enumerate(hits, start=1):
        lines.append(f"[{index}] 来源：《{hit['source_title']}》 章节：{hit['section']}")
        lines.append(hit["text"])
        lines.append("")
    return "\n".join(lines).strip()


def estimate_cost(usage: dict) -> float:
    """按 DeepSeek 官网价格估算费用（元），输入 2 元/百万 tokens，输出 8 元/百万 tokens。"""
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    return round(prompt / 1_000_000 * PRICE_INPUT_PER_M + completion / 1_000_000 * PRICE_OUTPUT_PER_M, 6)


def verify_citations(answer: str, hits: list[dict]) -> list[dict]:
    """检查回答里的引用编号是否有效，以及被引用片段与回答是否有词语重合。"""
    answer_tokens = set(retriever.tokenize(answer))
    checks = []
    for index, hit in enumerate(hits, start=1):
        cited = f"[{index}]" in answer
        chunk_tokens = set(retriever.tokenize(hit["text"]))
        overlap = len(answer_tokens & chunk_tokens) / len(chunk_tokens) if chunk_tokens else 0.0
        if cited:
            checks.append(
                {
                    "index": index,
                    "chunk_id": hit["chunk_id"],
                    "source_title": hit["source_title"],
                    "section": hit["section"],
                    "overlap": round(overlap, 3),
                    "reliable": overlap >= 0.12,
                }
            )
    invalid = [int(n) for n in re.findall(r"\[(\d+)\]", answer) if int(n) > len(hits) or int(n) < 1]
    if invalid:
        checks.append({"index": None, "invalid_citations": invalid, "reliable": False})
    return checks


def _empty_retrieval(question: str, mode: str, top_k: int, prompt_version: str) -> dict:
    log.warning("检索无结果，未调用模型：%s", question[:40])
    return {
        "question": question,
        "mode": mode,
        "prompt_version": prompt_version,
        "answer": INSUFFICIENT_TEXT,
        "citations": [],
        "sufficient": False,
        "retrieved": [],
        "citation_checks": [],
        "usage": {},
        "latency_ms": 0,
        "model": None,
        "cost_yuan": 0.0,
        "skipped_reason": "NO_RETRIEVAL",
        "context_chars": 0,
    }


def answer_question(
    question: str,
    mode: str = "rag",
    top_k: int = DEFAULT_TOP_K,
    prompt_version: str = "v2",
    temperature: float = 0.2,
) -> dict:
    """完整走一次问答流程；rag 模式会先检索资料，direct 模式直接问模型。"""
    question = (question or "").strip()
    if not question:
        raise BusinessError("问题不能为空", 400, "EMPTY_QUESTION")
    if len(question) > MAX_QUESTION_CHARS:
        raise BusinessError(f"问题过长，请控制在 {MAX_QUESTION_CHARS} 字以内", 400, "QUESTION_TOO_LONG")
    if mode not in {"rag", "direct"}:
        raise BusinessError("mode 只能是 rag 或 direct", 400, "INVALID_MODE")
    if prompt_version not in {"v1", "v2", "v3"}:
        raise BusinessError("prompt_version 只能是 v1、v2 或 v3", 400, "INVALID_PROMPT_VERSION")

    hits: list[dict] = []
    context = ""
    if mode == "rag":
        hits = retriever.search(question, top_k=top_k)
        if not hits:
            return _empty_retrieval(question, mode, top_k, prompt_version)
        context = build_context(hits)

    started = time.time()
    if mode == "rag" and prompt_version in {"v2", "v3"}:
        messages = prompts.rag_messages(question, context, prompt_version)
        parsed, raw = llm_client.chat_json(messages, prompts.RagAnswer, temperature=temperature, max_tokens=700)
        answer, citations, sufficient = parsed.answer, sorted(set(parsed.citations)), parsed.sufficient
    else:
        messages = (
            prompts.rag_messages(question, context, "v1")
            if mode == "rag"
            else prompts.direct_messages(question, prompt_version)
        )
        raw = llm_client.chat(messages, temperature=temperature, max_tokens=700)
        answer = raw["content"].strip()
        citations = sorted({int(n) for n in re.findall(r"\[(\d+)\]", answer)})
        sufficient = "资料不足" not in answer

    result = {
        "question": question,
        "mode": mode,
        "prompt_version": prompt_version,
        "answer": answer,
        "citations": [c for c in citations if 1 <= c <= len(hits)] if mode == "rag" else [],
        "sufficient": sufficient,
        "retrieved": [
            {
                "index": i,
                "chunk_id": hit["chunk_id"],
                "source_title": hit["source_title"],
                "section": hit["section"],
                "path": hit["path"],
                "score": hit["score"],
                "chars": hit["chars"],
                "preview": hit["text"][:120] + ("…" if hit["chars"] > 120 else ""),
            }
            for i, hit in enumerate(hits, start=1)
        ],
        "citation_checks": verify_citations(answer, hits) if mode == "rag" else [],
        "usage": raw["usage"],
        "latency_ms": raw["latency_ms"],
        "model": raw["model"],
        "cost_yuan": estimate_cost(raw["usage"]),
        "context_chars": len(context),
        "total_ms": int((time.time() - started) * 1000),
    }
    log.info(
        "问答完成：mode=%s，prompt=%s，检索 %d 个片段，耗时 %d ms，tokens=%s，引用 %s",
        mode, prompt_version, len(hits), result["latency_ms"], result["usage"].get("total_tokens"), result["citations"],
    )
    return result


def kb_overview() -> dict:
    meta = kb_service.meta()
    meta["chunk_size"] = kb_service.CHUNK_SIZE
    meta["chunk_overlap"] = kb_service.CHUNK_OVERLAP
    meta["retrieval"] = "BM25 关键词检索（中文二字切分）"
    return meta


def list_chunks(source_id: str | None = None, limit: int = 5, offset: int = 0) -> list[dict]:
    chunks = kb_service.load_chunks()
    if source_id:
        chunks = [c for c in chunks if c["source_id"] == source_id]
    return chunks[offset : offset + limit]
