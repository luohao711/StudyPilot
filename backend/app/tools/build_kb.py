"""知识库构建：读取 kb/sources.json 中的资料，清理、切分并写入 chunks.jsonl。

用法：python3 -m app.tools.build_kb
"""

from app.service import kb_service


def main() -> None:
    meta = kb_service.build_index()
    print(f"构建时间：{meta['built_at']}")
    print(f"切分参数：片段 {meta['chunk_size']} 字，相邻重叠 {meta['chunk_overlap']} 字")
    print(f"片段总数：{meta['total_chunks']}")
    print("")
    print(f"{'编号':<6}{'资料':<32}{'字符数':>8}{'片段数':>8}  更新时间")
    for item in meta["sources"]:
        print(f"{item['source_id']:<6}{item['title']:<32}{item['chars']:>8}{item['chunks']:>8}  {item['updated_at']}")


if __name__ == "__main__":
    main()
