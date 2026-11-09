"""脚本模型：测试与演示用，不调用远程接口，按预设顺序返回模型消息。"""

import json
import time

from app.errors import BusinessError


def tool_call(name: str, args: dict, call_id: str | None = None) -> dict:
    return {
        "id": call_id or f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def assistant_tool_calls(*calls: dict) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": list(calls)}


def assistant_final(payload: dict) -> dict:
    return {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)}


def success_answer(gt: dict, *, review_task_id: int | None = None,
                   completion_rate: float | None = None,
                   weakest_points: list[str] | None = None) -> dict:
    return {
        "status": "success",
        "completion_rate": gt["completion_rate"] if completion_rate is None else completion_rate,
        "total_tasks": gt["total_tasks"],
        "done_tasks": gt["done_tasks"],
        "pending_tasks": gt["pending_tasks"],
        "weakest_points": list(gt["weakest_points"]) if weakest_points is None else weakest_points,
        "review_task_created": review_task_id is not None,
        "review_task_id": review_task_id,
        "suggestion": "先补齐最薄弱的知识点，再安排一次综合练习并复核未完成任务。",
    }


def last_created_id(messages: list[dict]) -> int | None:
    for message in reversed(messages):
        if message.get("role") != "tool":
            continue
        try:
            data = json.loads(message.get("content") or "{}")
        except json.JSONDecodeError:
            continue
        payload = data.get("data") or {}
        if payload.get("created") and payload.get("task_id") is not None:
            return payload["task_id"]
    return None


class ScriptedLLM:
    """按脚本返回消息；脚本项可以是消息、可调用对象或异常。"""

    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[list[dict]] = []

    def __call__(self, messages: list[dict], tools: list[dict], *, timeout=None, **kwargs) -> dict:
        self.calls.append(messages)
        if not self.script:
            raise BusinessError("脚本模型已用尽", 502, "SCRIPT_EXHAUSTED")
        item = self.script.pop(0)
        if callable(item):
            item = item(messages)
        if isinstance(item, Exception):
            raise item
        return {
            "message": item,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "latency_ms": 5,
            "finish_reason": "tool_calls" if item.get("tool_calls") else "stop",
        }


class SlowScriptedLLM(ScriptedLLM):
    """模拟模型调用耗时超过总运行时限。"""

    def __init__(self, script: list, delay: float = 1.5):
        super().__init__(script)
        self.delay = delay

    def __call__(self, messages: list[dict], tools: list[dict], *, timeout=None, **kwargs) -> dict:
        time.sleep(self.delay)
        return super().__call__(messages, tools, timeout=timeout, **kwargs)
