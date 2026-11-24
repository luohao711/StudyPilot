"""AI 学习计划原型配置：模型、Prompt版本、生成参数和评测路径集中在此处。

模型名称、温度、输出上限、超时、修复次数和价格都从环境变量读取，
密钥只由 llm_client 从 DEEPSEEK_API_KEY 环境变量读取，不进入代码和仓库。
"""

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent

# 模型与生成参数：换模型或调参只改这里，便于版本之间比较。
MODEL = os.environ.get("PLAN_AI_MODEL", os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
TEMPERATURE = float(os.environ.get("PLAN_AI_TEMPERATURE", "0.3"))
MAX_TOKENS = int(os.environ.get("PLAN_AI_MAX_TOKENS", "2000"))
TIMEOUT = float(os.environ.get("PLAN_AI_TIMEOUT", "45"))
PROMPT_VERSION = os.environ.get("PLAN_AI_PROMPT_VERSION", "v1")

# 计划窗口与规模限制：由程序执行，不依赖提示词。
MAX_PLAN_DAYS = 14
MAX_POINTS = 20
MIN_PER_POINT_MINUTES = 15
MIN_TASK_MINUTES = 10
MAX_TASK_MINUTES = 120
MIN_AVAILABLE_MINUTES = 30
MAX_AVAILABLE_MINUTES = 600

# 估算费用（元/百万 tokens）：按 DeepSeek 官网 deepseek-chat 标准价估算，仅用于报告。
PRICE_INPUT_PER_M = float(os.environ.get("PLAN_AI_PRICE_INPUT", "2.0"))
PRICE_OUTPUT_PER_M = float(os.environ.get("PLAN_AI_PRICE_OUTPUT", "8.0"))

# 评测与日志路径
EVAL_SET_PATH = BACKEND_DIR / "eval" / "plan_cases.jsonl"
EVAL_OUTPUT_DIR = PROJECT_DIR / "reports" / "experiment7" / "runs"
RUN_LOG_PATH = BACKEND_DIR / "logs" / "plan_ai_runs.jsonl"
