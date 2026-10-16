"""知识库：资料清单、文本清理、切分入库和片段读取。

资料来源为本人编写的 StudyPilot 项目文档，全部可以公开，不包含个人隐私数据。
更新方法：修改源文件后重新执行 python3 -m app.tools.build_kb。
"""

import json
import os
import re
import time
from pathlib import Path

from app.errors import BusinessError
from app.logging_conf import log

REPO_ROOT = Path(__file__).resolve().parents[3]
KB_DIR = REPO_ROOT / "backend" / "kb"
SOURCE_MANIFEST = KB_DIR / "sources.json"
CHUNK_FILE = KB_DIR / "chunks.jsonl"
META_FILE = KB_DIR / "index_meta.json"

CHUNK_SIZE = 420
CHUNK_OVERLAP = 80

_cache: dict = {"mtime": None, "chunks": []}


def load_manifest() -> list[dict]:
    if not SOURCE_MANIFEST.exists():
        raise BusinessError("知识库资料清单不存在，请先运行 build_kb", 500, "KB_MANIFEST_MISSING")
    return json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))


def latex_to_text(tex: str) -> str:
    """把 LaTeX 报告转成纯文本，只保留标题、段落和表格内容。"""
    if "\\begin{document}" in tex:
        tex = tex.split("\\begin{document}", 1)[1]
    tex = tex.split("\\end{document}", 1)[0]
    tex = re.sub(r"\\includegraphics\[[^\]]*\]\{[^}]*\}", "", tex)

    def table_to_text(match: re.Match) -> str:
        body = match.group(0)
        body = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?(\{[^{}]*\})*", " ", body)
        rows = [row.strip() for row in body.split("\\\\") if row.strip()]
        lines = []
        for row in rows:
            cells = [cell.strip(" {}") for cell in row.split("&")]
            cells = [cell for cell in cells if cell]
            if cells:
                lines.append(" | ".join(cells))
        return "\n" + "\n".join(lines) + "\n"

    tex = re.sub(r"\\begin\{tabular.*?\\end\{tabular[a-z]*\}", table_to_text, tex, flags=re.S)
    tex = re.sub(r"\\begin\{(table|figure)\}\[?[^\]]*\]?|\\end\{(table|figure)\}", "\n", tex)
    tex = re.sub(r"\\(toprule|midrule|bottomrule|centering|small|footnotesize|scriptsize|noindent)\b", "", tex)
    tex = re.sub(r"\\caption\{([^}]*)\}", r"\n图表：\1\n", tex)
    tex = re.sub(r"\\section\*?\{([^}]*)\}", r"\n## \1\n", tex)
    tex = re.sub(r"\\subsection\*?\{([^}]*)\}", r"\n### \1\n", tex)
    tex = re.sub(r"\\subsubsection\*?\{([^}]*)\}", r"\n#### \1\n", tex)
    tex = re.sub(r"\\item\s*", "\n- ", tex)
    tex = re.sub(r"\\(textbf|texttt|emph|url|underline)\{([^}]*)\}", r"\2", tex)
    tex = re.sub(r"\\label\{[^}]*\}|\\ref\{([^}]*)\}", r"\1", tex)
    tex = tex.replace("\\\\", "\n").replace("\\%", "%").replace("\\_", "_").replace("\\&", "&")
    tex = re.sub(r"\\begin\{itemize\}|\\end\{itemize\}", "\n", tex)
    tex = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?(\{[^}]*\})?", " ", tex)
    tex = re.sub(r"^[\]\[{}$&#_^~|\\\s]+$", "", tex, flags=re.M)  # 去掉只剩符号的行
    tex = re.sub(r"[ \t]+", " ", tex)
    tex = re.sub(r"\n\s*\n\s*\n+", "\n\n", tex)
    return tex.strip()


def read_document(source: dict) -> str:
    path = REPO_ROOT / source["path"]
    if not path.exists():
        raise BusinessError(f"资料文件不存在：{source['path']}", 500, "KB_SOURCE_MISSING")
    raw = path.read_text(encoding="utf-8")
    text = latex_to_text(raw) if source.get("type") == "latex" else raw
    text = text.replace("\r\n", "\n").replace("\u3000", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_sections(text: str) -> list[tuple[str, str]]:
    """按 Markdown 标题切分，返回（标题路径, 正文）。"""
    sections: list[tuple[str, str]] = []
    stack: list[str] = []
    title = "正文"
    buffer: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^(#{1,4})\s+(.*)$", line.strip())
        if match:
            if buffer:
                sections.append((" / ".join(stack) or title, "\n".join(buffer).strip()))
                buffer = []
            level = len(match.group(1))
            stack = stack[: level - 1]
            stack.append(match.group(2).strip())
            title = match.group(2).strip()
            continue
        buffer.append(line)
    if buffer:
        sections.append((" / ".join(stack) or title, "\n".join(buffer).strip()))
    return [(t, b) for t, b in sections if len(b) >= 40]


def _split_paragraphs(body: str, size: int, overlap: int) -> list[tuple[str, bool]]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    chunks: list[tuple[str, bool]] = []
    current = ""
    for paragraph in paragraphs:
        pieces = [paragraph]
        if len(paragraph) > size:
            pieces = [s for s in re.split(r"(?<=[。！？；])", paragraph) if s.strip()]
        for piece in pieces:
            if current and len(current) + len(piece) + 1 > size:
                chunks.append((current.strip(), len(chunks) > 0))
                tail = current[-overlap:] if overlap else ""
                current = (tail + "\n" + piece) if tail else piece
            else:
                current = f"{current}\n{piece}".strip() if current else piece
    if current.strip():
        chunks.append((current.strip(), len(chunks) > 0))
    return chunks


def build_index() -> dict:
    """读取全部资料、切分并写入 chunks.jsonl。"""
    manifest = load_manifest()
    KB_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    stats: list[dict] = []
    for source in manifest:
        text = read_document(source)
        path = REPO_ROOT / source["path"]
        updated = time.strftime("%Y-%m-%d", time.localtime(path.stat().st_mtime))
        count = 0
        for title, body in _split_sections(text):
            for chunk, has_overlap in _split_paragraphs(body, CHUNK_SIZE, CHUNK_OVERLAP):
                count += 1
                rows.append(
                    {
                        "chunk_id": f"{source['source_id']}-{count:03d}",
                        "source_id": source["source_id"],
                        "source_title": source["title"],
                        "path": source["path"],
                        "section": title,
                        "text": chunk,
                        "chars": len(chunk),
                        "overlap_prev": has_overlap,
                        "updated_at": updated,
                    }
                )
        stats.append({"source_id": source["source_id"], "title": source["title"], "path": source["path"],
                      "chars": len(text), "chunks": count, "updated_at": updated})
        log.info("切分资料：%s -> %d 个片段", source["title"], count)

    with CHUNK_FILE.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")
    meta = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "total_chunks": len(rows),
        "sources": stats,
    }
    META_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _cache["mtime"] = None
    log.info("知识库构建完成：%d 篇资料，%d 个片段", len(stats), len(rows))
    return meta


def load_chunks() -> list[dict]:
    if not CHUNK_FILE.exists():
        raise BusinessError("知识库尚未构建，请先运行 python3 -m app.tools.build_kb", 400, "KB_NOT_READY")
    mtime = os.path.getmtime(CHUNK_FILE)
    if _cache["mtime"] != mtime:
        with CHUNK_FILE.open(encoding="utf-8") as fp:
            _cache["chunks"] = [json.loads(line) for line in fp if line.strip()]
        _cache["mtime"] = mtime
    return _cache["chunks"]


def meta() -> dict:
    if not META_FILE.exists():
        return {"total_chunks": 0, "sources": []}
    return json.loads(META_FILE.read_text(encoding="utf-8"))
