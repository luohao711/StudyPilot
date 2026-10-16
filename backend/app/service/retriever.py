"""关键词检索：中文按二字切分、英文按单词切分，使用 BM25 排序。

选择关键词检索的理由：资料总量小（几十个片段），没有配置嵌入模型，
BM25 不需要额外的模型服务，排序依据可以逐项核对。

在样例检查中发现两个问题并做了调整：
1. 问答常用词与资料用词不一致（问“数据库保存哪些数据”，资料里写的是“表、字段、主键”），
   加入同义词扩展，扩展词权重低于原问题词；
2. 正文提到的通用词太多，标题相关的片段排不上来，加入标题字段加权。
"""

import math
import re
from collections import Counter

from app.service import kb_service

K1 = 1.5
B = 0.75
# 相关性阈值：根据样例抽查确定。有答案的问题最高分在 15 分以上，
# 与项目无关的问题在 7 分以下，阈值取 10 分可以分开这两类；
# 资料里没有答案但话题相关的问题会落在中间，交给模型判断（返回资料不足）。
MIN_SCORE = 10.0
# 同义词扩展权重：扩展词只作为补充，权重低于原问题词
EXPAND_WEIGHT = 0.6
# 标题字段权重：片段所属章节和小节标题的匹配更重要
TITLE_WEIGHT = 1.2
# 同义词映射表：问答常用说法 → 资料里的写法
SYNONYMS = {
    "数据库": ["表", "字段", "主键", "外键", "数据模型"],
    "数据": ["表", "字段", "实体"],
    "保存": ["写入", "存储"],
    "关系": ["关联", "外键"],
    "界面": ["原型", "页面"],
    "计划": ["排期", "任务"],
    "测试": ["用例", "异常"],
    "掌握度": ["知识点", "更新"],
}

STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "及", "或", "一个", "这个", "那个", "我们", "本人",
    "可以", "进行", "通过", "使用", "以及", "同时", "因为", "所以", "如果", "需要", "the", "and",
    "for", "with", "that", "this", "are", "was", "not", "you", "from",
}

_cache: dict = {"mtime": None, "docs": [], "stats": {}}


def tokenize(text: str) -> list[str]:
    """英文按单词、中文按二字组合切分，便于在没有分词库时也能稳定匹配。"""
    tokens: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_]+", text.lower()):
        if word not in STOPWORDS and len(word) > 1:
            tokens.append(word)
    for run in re.findall(r"[\u4e00-\u9fa5]+", text):
        if len(run) == 1:
            tokens.append(run)
            continue
        for i in range(len(run) - 1):
            bigram = run[i : i + 2]
            if bigram not in STOPWORDS:
                tokens.append(bigram)
    return tokens


def _ensure_index() -> None:
    chunks = kb_service.load_chunks()
    current = kb_service.CHUNK_FILE.stat().st_mtime if kb_service.CHUNK_FILE.exists() else None
    if _cache["mtime"] == current and _cache["docs"]:
        return

    docs = []
    stats = {
        field: {"df": Counter(), "total_len": 0}
        for field in ("body", "title")
    }
    for chunk in chunks:
        body_tokens = tokenize(chunk["text"])
        title_tokens = tokenize(f"{chunk.get('section', '')} {chunk.get('source_title', '')}")
        docs.append(
            {
                "chunk": chunk,
                "body": Counter(body_tokens),
                "body_len": len(body_tokens),
                "title": Counter(title_tokens),
                "title_len": len(title_tokens),
            }
        )
        for field, tokens in (("body", body_tokens), ("title", title_tokens)):
            for token in set(tokens):
                stats[field]["df"][token] += 1
            stats[field]["total_len"] += len(tokens)

    for field in stats:
        stats[field]["avgdl"] = (stats[field]["total_len"] / len(docs)) if docs else 0.0
    _cache.update({"mtime": current, "docs": docs, "stats": stats})


def _score_field(terms: list[str], field: str) -> dict[int, float]:
    """某个字段上的 BM25 分数。"""
    stats = _cache["stats"][field]
    total = len(_cache["docs"])
    scores: dict[int, float] = {}
    for index, doc in enumerate(_cache["docs"]):
        tf_map, length = doc[field], doc[f"{field}_len"]
        score = 0.0
        for token in set(terms):
            tf = tf_map.get(token, 0)
            if not tf:
                continue
            df = stats["df"].get(token, 0)
            idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
            denom = tf + K1 * (1 - B + B * length / (stats["avgdl"] or 1))
            score += idf * tf * (K1 + 1) / denom
        if score > 0:
            scores[index] = score
    return scores


def expand_terms(question: str) -> list[str]:
    """同义词扩展：问答用词与资料用词不一致时补充检索词。"""
    extras: list[str] = []
    for key, values in SYNONYMS.items():
        if key in question:
            extras.extend(values)
    return tokenize(" ".join(extras))


def _combine(terms: list[str], weight: float, scores: dict[int, float]) -> None:
    for index, score in _score_field(terms, "body").items():
        scores[index] = scores.get(index, 0.0) + weight * score
    for index, score in _score_field(terms, "title").items():
        scores[index] = scores.get(index, 0.0) + weight * TITLE_WEIGHT * score


def search(query: str, top_k: int = 3, min_score: float = MIN_SCORE) -> list[dict]:
    """返回按 BM25 分数排序的前 top_k 个片段；分数过低视为没有相关资料。"""
    _ensure_index()
    docs = _cache["docs"]
    if not docs:
        return []
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    scores: dict[int, float] = {}
    _combine(query_tokens, 1.0, scores)
    expanded = expand_terms(query)
    if expanded:
        _combine(expanded, EXPAND_WEIGHT, scores)

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    results = []
    for index, score in ranked[: max(top_k, 1)]:
        if score < min_score:
            break
        chunk = docs[index]["chunk"]
        hit = dict(chunk)
        hit["score"] = round(score, 3)
        hit["matched"] = sorted({t for t in set(query_tokens) if t in chunk["text"]})
        results.append(hit)
    return results
