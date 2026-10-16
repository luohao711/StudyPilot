"""云端模型调用：DeepSeek Chat Completions。

负责输入组织、超时控制、结构化输出校验和调用失败分类。
密钥只从环境变量 DEEPSEEK_API_KEY 读取，不写入代码和日志。
"""

import json
import os
import time

import httpx
from pydantic import BaseModel, ValidationError

from app.errors import BusinessError
from app.logging_conf import log

DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
DEFAULT_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEFAULT_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "60"))


def api_key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


def is_available() -> bool:
    return bool(api_key())


def mask(key: str) -> str:
    return f"{key[:6]}***{key[-3:]}" if len(key) > 12 else "***"


def _classify(exc: Exception, base_url: str) -> BusinessError:
    if isinstance(exc, httpx.TimeoutException):
        return BusinessError(f"模型服务响应超时（{exc.__class__.__name__}），请稍后重试", 504, "LLM_TIMEOUT")
    if isinstance(exc, httpx.ConnectError):
        return BusinessError(f"无法连接模型服务（{base_url}），请检查网络或服务地址", 503, "LLM_CONNECTION_ERROR")
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 401:
            return BusinessError("模型服务鉴权失败（401），请检查 API Key", 502, "LLM_AUTH_FAILED")
        if code == 429:
            return BusinessError("模型服务限流（429），请稍后重试", 503, "LLM_RATE_LIMITED")
        return BusinessError(f"模型服务返回错误（HTTP {code}）", 502, "LLM_HTTP_ERROR")
    return BusinessError(f"模型调用失败：{exc.__class__.__name__}", 502, "LLM_ERROR")


def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 900,
    timeout: float | None = None,
    json_mode: bool = False,
    base_url: str | None = None,
    api_key_override: str | None = None,
) -> dict:
    """调用模型并返回内容、用量和耗时；失败时抛出带错误码的业务异常。"""
    key = api_key_override if api_key_override is not None else api_key()
    if not key:
        raise BusinessError("未配置 DEEPSEEK_API_KEY，无法调用模型服务", 503, "LLM_NOT_CONFIGURED")

    url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/chat/completions"
    payload = {
        "model": model or DEFAULT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    log.info("调用模型：model=%s，messages=%d 条，输入约 %d 字符", payload["model"], len(messages),
             sum(len(m.get("content", "")) for m in messages))
    started = time.time()
    try:
        resp = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=timeout or DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - 统一分类后抛出业务异常
        error = _classify(exc, url.rsplit("/chat/completions", 1)[0])
        log.error("模型调用失败：%s（%s）", error.message, error.code)
        raise error from exc

    latency_ms = int((time.time() - started) * 1000)
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        log.error("模型返回结构异常：%s", str(data)[:200])
        raise BusinessError("模型返回内容结构异常，无法解析", 502, "LLM_BAD_RESPONSE") from exc

    result = {
        "content": content,
        "model": data.get("model", payload["model"]),
        "usage": data.get("usage", {}),
        "latency_ms": latency_ms,
        "finish_reason": data["choices"][0].get("finish_reason"),
    }
    log.info("模型返回：耗时 %d ms，tokens=%s", latency_ms, result["usage"].get("total_tokens"))
    return result


def chat_json(
    messages: list[dict],
    schema: type[BaseModel],
    *,
    retry_with_error: bool = True,
    **kwargs,
) -> tuple[BaseModel, dict]:
    """要求模型输出 JSON 并校验字段；不合法时带着错误信息重试一次。"""
    first = chat(messages, json_mode=True, **kwargs)
    attempts = [first]
    try:
        return schema.model_validate(json.loads(first["content"])), first
    except (json.JSONDecodeError, ValidationError) as exc:
        error_text = str(exc)[:200]
        log.warning("结构化输出校验失败：%s", error_text)
        if not retry_with_error:
            raise BusinessError("模型输出的 JSON 不符合要求", 502, "LLM_BAD_JSON") from exc

    repair = list(messages) + [
        {"role": "assistant", "content": first["content"]},
        {
            "role": "user",
            "content": f"上一次输出不符合要求：{error_text}。请只返回合法 JSON，字段为 "
                       + "、".join(schema.model_fields.keys()),
        },
    ]
    second = chat(repair, json_mode=True, **kwargs)
    attempts.append(second)
    try:
        data = schema.model_validate(json.loads(second["content"]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise BusinessError("模型两次输出的 JSON 均不符合要求", 502, "LLM_BAD_JSON") from exc

    second["usage"] = {
        "prompt_tokens": sum(a["usage"].get("prompt_tokens", 0) for a in attempts),
        "completion_tokens": sum(a["usage"].get("completion_tokens", 0) for a in attempts),
        "total_tokens": sum(a["usage"].get("total_tokens", 0) for a in attempts),
    }
    second["latency_ms"] = sum(a["latency_ms"] for a in attempts)
    second["retried"] = True
    return data, second
