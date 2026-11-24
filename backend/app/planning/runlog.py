"""运行日志：按 JSONL 记录每次生成的关键输入、模型结果、错误和耗时。

复用实验6的运行日志实现，密钥和个人信息在写入前遮挡；日志文件独立为
backend/logs/plan_ai_runs.jsonl，便于和 Agent 运行记录区分。
"""

import datetime
import secrets

from app.agent.runlog import RunLogger
from app.planning import config


def new_run_id() -> str:
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)


def make_logger(run_id: str, path: str | None = None) -> RunLogger:
    return RunLogger(run_id, str(path or config.RUN_LOG_PATH))
