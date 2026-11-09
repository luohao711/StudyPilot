"""工具规格与统一失败类型：工具层和运行时 Harness 共用。"""

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel


class ToolFailure(Exception):
    """工具调用失败：带错误码、是否可重试和给模型的修正提示。"""

    def __init__(self, message: str, *, code: str = "TOOL_ERROR", retriable: bool = False, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.code = code
        self.retriable = retriable
        self.hint = hint

    def to_dict(self) -> dict:
        data: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            data["hint"] = self.hint
        return data


@dataclass(frozen=True)
class ToolSpec:
    """一个允许被模型调用的工具：名称、参数Schema、执行函数和权限属性。"""

    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[BaseModel], dict]
    mutating: bool = False
    timeout_s: float = 3.0
    max_retries: int = 1
    impact: str = ""

    def openai_schema(self) -> dict:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        schema["additionalProperties"] = False
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": schema},
        }
