"""AI 任务运行日志：JSONL 记录任务创建、开始、结束、取消、重试和迟到结果。

与实验6/7的运行日志分开保存为 backend/logs/ai_jobs.jsonl，便于按 job_id 追踪
一次任务跨接口、后台线程和模型调用的完整过程。
"""

import datetime
import json
import threading

from app.integration import config

_lock = threading.Lock()


def event(job_id: str, event: str, **fields) -> None:
    record = {
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "job_id": job_id,
        "event": event,
        **fields,
    }
    line = json.dumps(record, ensure_ascii=False)
    with _lock:
        config.JOB_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(config.JOB_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
