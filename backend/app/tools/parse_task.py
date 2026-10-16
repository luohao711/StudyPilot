"""结构化输出演示：把自然语言学习要求解析成结构化任务，并校验字段。

用法：
    python3 -m app.tools.parse_task "明天晚上复习软件测试，大概 40 分钟"
    python3 -m app.tools.parse_task "复习一下" --bad-schema    # 演示校验失败与重试
"""

import argparse
import datetime

from pydantic import BaseModel, Field

from app.errors import BusinessError
from app.service import llm_client, prompts


class StrictTask(BaseModel):
    """故意要求一个模型不会返回的字段，用于演示校验失败。"""

    title: str
    minutes: int
    priority: str = Field(..., description="优先级")
    owner: str = Field(..., description="负责人")


def main() -> None:
    parser = argparse.ArgumentParser(description="学习任务结构化解析")
    parser.add_argument("text", help="学习要求原文")
    parser.add_argument("--bad-schema", action="store_true", help="使用不匹配的结构做校验失败演示")
    args = parser.parse_args()

    today = datetime.date.today().isoformat()
    messages = prompts.task_parse_messages(args.text, today)
    schema = StrictTask if args.bad_schema else prompts.TaskDraft
    print(f"输入：{args.text}")
    print(f"今天：{today}    输出结构：{list(schema.model_fields.keys())}")
    print("-" * 78)
    try:
        data, raw = llm_client.chat_json(messages, schema, temperature=0)
        print(f"校验通过：{data.model_dump()}")
        print(f"重试次数：{'1 次（首次不合法）' if raw.get('retried') else '0 次'}")
        print(f"耗时 {raw['latency_ms']} ms    tokens {raw['usage'].get('total_tokens')}")
    except BusinessError as exc:
        print(f"错误码：{exc.code}")
        print(f"错误信息：{exc.message}")


if __name__ == "__main__":
    main()
