"""运行日志：每次运行按 JSONL 记录事件，密钥和个人信息在写入前遮挡。

日志字段包括：运行编号、时间、事件类型、步骤、工具与参数摘要、结果摘要、
错误类型、耗时和最终状态。
"""

import datetime
import json
import os
import re
import threading

LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs"
)
AGENT_LOG = os.path.join(LOG_DIR, "agent_runs.jsonl")

_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{6,}")


def mask_text(text: str) -> str:
    return _SECRET_PATTERN.sub("sk-***", text)


def _mask(value):
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {str(k): _mask(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v) for v in value]
    return value


def summarize(value, limit: int = 300) -> str:
    """把工具结果压成一行摘要，便于写日志和终端展示。"""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(_mask(value), ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(value)
    text = mask_text(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class RunLogger:
    def __init__(self, run_id: str, path: str | None = None):
        self.run_id = run_id
        self.path = path or AGENT_LOG
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def event(self, event: str, **fields) -> dict:
        record = {
            "run_id": self.run_id,
            "ts": datetime.datetime.now().isoformat(timespec="milliseconds"),
            "event": event,
        }
        record.update(_mask(fields))
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fp:
                fp.write(line + "\n")
        return record