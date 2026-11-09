"""工具注册表：白名单检查、参数校验、超时执行和只读重试。"""

import concurrent.futures
import time

from pydantic import BaseModel, ValidationError

from app.agent.spec import ToolFailure, ToolSpec


class ToolRegistry:
    """只允许执行注册表中列出的工具；模型不能传入任意函数名或命令。"""

    def __init__(self, specs: list[ToolSpec]):
        self._specs = {spec.name: spec for spec in specs}

    def names(self) -> list[str]:
        return list(self._specs)

    def openai_tools(self) -> list[dict]:
        return [spec.openai_schema() for spec in self._specs.values()]

    def require(self, name: str) -> ToolSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise ToolFailure(
                f"工具不在允许列表中：{name}",
                code="UNKNOWN_TOOL",
                hint="允许的工具：" + "、".join(self._specs) + "；不能执行任意函数名或命令",
            )
        return spec

    def validate(self, name: str, raw_args: object) -> tuple[ToolSpec, BaseModel]:
        spec = self.require(name)
        if not isinstance(raw_args, dict):
            raise ToolFailure("工具参数必须是 JSON 对象", code="INVALID_ARGUMENTS",
                              hint="按工具 Schema 提供参数对象")
        try:
            parsed = spec.args_model.model_validate(raw_args)
        except ValidationError as exc:
            details = []
            for err in exc.errors():
                location = ".".join(str(part) for part in err["loc"]) or "参数"
                details.append(f"{location}: {err['msg']}")
            raise ToolFailure(
                "参数校验失败：" + "；".join(details),
                code="INVALID_ARGUMENTS",
                hint="请按工具 Schema 修正参数后重试，程序没有执行该工具",
            ) from exc
        return spec, parsed

    def execute(self, spec: ToolSpec, parsed: BaseModel, timeout_s: float) -> dict:
        """在单独线程中执行工具，超过 timeout_s 时停止等待并抛出 TOOL_TIMEOUT。

        注意：线程无法被强制终止，读工具在后台继续执行没有副作用；写工具超时后
        状态可能不明，Harness 不会自动重试，而是要求先查询核对。
        """
        started = time.monotonic()
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"tool-{spec.name}"
        )
        try:
            future = executor.submit(spec.handler, parsed)
            try:
                data = future.result(timeout=timeout_s)
            except concurrent.futures.TimeoutError as exc:
                raise ToolFailure(
                    f"工具执行超过 {timeout_s:.1f} 秒仍未返回",
                    code="TOOL_TIMEOUT",
                    retriable=not spec.mutating,
                    hint=("读操作可以重试；写操作超时后状态未知，必须先查询核对，不能直接重试"
                          if spec.mutating else "可以稍后重试"),
                ) from exc
        except ToolFailure:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转成工具错误回传模型
            raise ToolFailure(
                f"工具执行异常：{exc.__class__.__name__}",
                code="TOOL_EXCEPTION",
                retriable=False,
                hint="该错误不可自动重试，请向用户说明失败原因",
            ) from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        return {"data": data, "latency_ms": int((time.monotonic() - started) * 1000)}
