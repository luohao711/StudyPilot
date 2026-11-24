"""AI 任务集成配置：并发数、保留期、运行日志和本地演示开关集中在此处。

这些参数只影响“软件怎么等、怎么存、怎么取消”，不改变模型能力本身；
模型、Prompt 和生成参数仍由 app/planning/config.py 管理。
"""

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]

# 后台执行线程数：单机单进程部署，最多同时执行两个生成任务。
JOB_WORKERS = int(os.environ.get("AI_JOB_WORKERS", "2"))
# 已结束任务的保留天数；启动时清理更早的记录。
JOB_RETENTION_DAYS = int(os.environ.get("AI_JOB_RETENTION_DAYS", "7"))
JOB_LOG_PATH = BACKEND_DIR / "logs" / "ai_jobs.jsonl"

# 演示开关：仅在本地验收时启用，可通过 simulate 参数触发超时/服务异常/慢响应。
# 默认关闭，关闭时接口拒绝 simulate 参数（400 INVALID_SIMULATION）。
SIMULATION_ENABLED = os.environ.get("AI_JOB_SIMULATION", "0") == "1"
SIMULATION_DELAY_SECONDS = float(os.environ.get("AI_JOB_SIMULATION_DELAY", "2.0"))
