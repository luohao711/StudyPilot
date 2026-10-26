"""知识库构建：读取 kb/sources.json 中的资料，清理、切分并写入 chunks.jsonl。

用法：python3 -m app.tools.build_kb
"""

import unicodedata

from app.service import kb_service


def _display_width(text: str) -> int:
    """中文按 2 列、英文按 1 列计算宽度，避免表格在终端里错位。"""
    return sum(2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1 for ch in text)


def _pad(text: str, width: int, right: bool = False) -> str:
    spaces = " " * max(width - _display_width(text), 0)
    return spaces + text if right else text + spaces


def main() -> None:
    meta = kb_service.build_index()
    print(f"构建时间：{meta['built_at']}")
    print(f"切分参数：片段 {meta['chunk_size']} 字，相邻重叠 {meta['chunk_overlap']} 字")
    print(f"片段总数：{meta['total_chunks']}")
    print("")
    print(f"{_pad('编号', 6)}{_pad('资料', 34)}{_pad('字符数', 9, True)}{_pad('片段数', 9, True)}  更新时间")
    for item in meta["sources"]:
        print(f"{_pad(item['source_id'], 6)}{_pad(item['title'], 34)}"
              f"{_pad(str(item['chars']), 9, True)}{_pad(str(item['chunks']), 9, True)}  {item['updated_at']}")


if __name__ == "__main__":
    main()
