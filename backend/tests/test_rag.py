"""RAG 模块的离线测试：不调用模型服务，只检查检索、片段和引用校验。"""

import json
from pathlib import Path

from app.service import kb_service, prompts, qa_service, retriever

QUESTIONS = json.loads(
    (Path(__file__).resolve().parent / "data" / "compare_questions.json").read_text(encoding="utf-8")
)


def test_kb_chunks_have_metadata():
    """片段都要带来源、章节和字数，方便回答里标注出处。"""
    chunks = kb_service.load_chunks()
    assert len(chunks) >= 20
    for chunk in chunks[:10]:
        assert chunk["chunk_id"] and chunk["source_id"]
        assert chunk["source_title"] and chunk["section"]
        assert chunk["chars"] == len(chunk["text"])
        assert chunk["path"].endswith((".md", ".tex"))


def test_tokenize_chinese_and_english():
    tokens = retriever.tokenize("StudyPilot 的掌握度更新逻辑")
    assert "studypilot" in tokens
    assert "掌握" in tokens and "握度" in tokens


def test_retrieval_finds_architecture_chunk():
    """架构问题应该能检索到写明分层的片段。"""
    hits = retriever.search("StudyPilot 的总体架构分为哪几层？", top_k=3)
    assert hits
    assert any("表现层" in hit["text"] for hit in hits)
    assert hits[0]["score"] >= hits[-1]["score"]


def test_retrieval_uses_synonym_expansion():
    """问“数据库保存哪些数据”时，同义词扩展要把数据模型说明检出来。"""
    hits = retriever.search("系统数据库里保存了哪些数据？它们之间是什么关系？", top_k=3)
    assert any(hit["source_id"] == "S5" for hit in hits)


def test_irrelevant_query_returns_nothing():
    """与项目无关的问题不应该返回片段。"""
    assert retriever.search("今天天气怎么样", top_k=3) == []


def test_build_context_numbers_sources():
    hits = retriever.search("模型服务不可用时计划怎么生成？", top_k=2)
    context = qa_service.build_context(hits)
    assert context.startswith("[1] 来源：《")
    assert "[2] 来源：《" in context


def test_verify_citations_detects_invalid_index():
    hits = retriever.search("StudyPilot 的总体架构分为哪几层？", top_k=2)
    answer = "架构分为四层[1]，另外参考[9]。"
    checks = qa_service.verify_citations(answer, hits)
    assert any(item.get("invalid_citations") == [9] for item in checks)
    assert any(item["index"] == 1 for item in checks)


def test_prompt_versions_differ():
    """v2 增加了引用与资料不足要求，v3 增加了缺少参数时追问的要求。"""
    v1 = prompts.rag_messages("问题", "资料", "v1")[0]["content"]
    v2 = prompts.rag_messages("问题", "资料", "v2")[0]["content"]
    v3 = prompts.rag_messages("问题", "资料", "v3")[0]["content"]
    assert "JSON" not in v1
    assert "JSON" in v2 and "资料不足" in v2
    assert "需要补充的信息" in v3


def test_question_validation():
    for bad in ("", "   "):
        try:
            qa_service.answer_question(bad)
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "code", "") == "EMPTY_QUESTION"
        else:
            raise AssertionError("空问题应该被拒绝")


def test_compare_questions_are_complete():
    assert 5 <= len(QUESTIONS) <= 8
    types = {item["type"] for item in QUESTIONS}
    assert "资料无答案" in types and "多片段综合" in types
    for item in QUESTIONS:
        assert item["question"] and item["rule"]
