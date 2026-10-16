"""片段抽查：查看切分结果的正文与元数据。

用法：
    python3 -m app.tools.inspect_chunks --source S2 --limit 3
    python3 -m app.tools.inspect_chunks --chunk S2-003
"""

import argparse
import json

from app.service import kb_service


def show(chunk: dict) -> None:
    print("=" * 78)
    print(f"片段编号：{chunk['chunk_id']}    来源：{chunk['source_title']}（{chunk['source_id']}）")
    print(f"章节：{chunk['section']}")
    print(f"字数：{chunk['chars']}    与前一片段重叠：{'是' if chunk['overlap_prev'] else '否'}    "
          f"资料更新时间：{chunk['updated_at']}")
    print(f"原文件：{chunk['path']}")
    print("-" * 78)
    print(chunk["text"])
    print("")


def main() -> None:
    parser = argparse.ArgumentParser(description="知识库片段抽查")
    parser.add_argument("--source", help="按资料编号筛选，如 S2")
    parser.add_argument("--chunk", help="只看某个片段，如 S2-003")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--json", action="store_true", help="以 JSON 输出元数据")
    args = parser.parse_args()

    chunks = kb_service.load_chunks()
    if args.chunk:
        chunks = [c for c in chunks if c["chunk_id"] == args.chunk]
    elif args.source:
        chunks = [c for c in chunks if c["source_id"] == args.source]
    selected = chunks[args.offset : args.offset + args.limit]
    if not selected:
        print("没有找到符合条件的片段")
        return

    if args.json:
        for chunk in selected:
            meta = {k: v for k, v in chunk.items() if k != "text"}
            print(json.dumps(meta, ensure_ascii=False))
        return
    for chunk in selected:
        show(chunk)
    print(f"共 {len(chunks)} 个片段，本次显示 {len(selected)} 个")


if __name__ == "__main__":
    main()
