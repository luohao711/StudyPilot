"""模型服务访问层。

本学期不接入真实模型服务：未配置 LLM_API_KEY 时 is_available() 返回 False，
业务层据此改用规则模板生成候选计划（实验2、实验3约定的非AI降级方案）。
"""

import os

from app.errors import BusinessError
from app.logging_conf import log

LLM_API_KEY = os.environ.get("LLM_API_KEY", "")


def is_available() -> bool:
    return bool(LLM_API_KEY)


def generate_plan_with_llm(course_id: int, points: list[dict], available_minutes: int) -> list[dict]:
    """真实接入模型服务时在这里调用；当前未配置时抛出可识别的业务异常。"""
    if not is_available():
        log.warning("模型服务未配置，课程 %s 改用规则模板生成计划", course_id)
        raise BusinessError("模型服务未配置", 503, "LLM_UNAVAILABLE")
    raise BusinessError("模型服务调用失败", 503, "LLM_UNAVAILABLE")
