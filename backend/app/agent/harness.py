"""运行时 Harness：组织模型与工具运行，执行限制、人工确认、结果核验和日志。

控制流程：
接收目标 → 组装上下文 → 调用模型 → 若有工具调用：白名单/参数检查 →（写操作人工确认）
→ 执行工具 → 结果按 tool_call_id 回传 → 继续循环；若为最终回答 → 程序核验成功标准
→ 通过则结束，不通过则有次数上限地反馈修正。
步数、单次超时、总时限、重试上限和写操作确认都由本文件执行，不依赖提示词。
"""

import datetime
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable

from app.agent.prompts import AGENT_SYSTEM_PROMPT
from app.agent.registry import ToolRegistry
from app.agent.runlog import RunLogger, summarize
from app.agent.spec import ToolFailure
from app.agent.verifier import ground_truth, verify_success
from app.errors import BusinessError
from app.logging_conf import log

STATUS_SUCCESS = "success"
STATUS_STEP_LIMIT = "step_limit"
STATUS_TIME_LIMIT = "time_limit"
STATUS_LLM_ERROR = "llm_error"
STATUS_VERIFY_FAILED = "verify_failed"
STATUS_AGENT_FAILED = "agent_failed"

RETRIABLE_LLM_CODES = {"LLM_TIMEOUT", "LLM_RATE_LIMITED", "LLM_CONNECTION_ERROR", "LLM_HTTP_ERROR"}


def _error_result(exc: ToolFailure) -> dict:
    return {"ok": False, "error": exc.to_dict()}


def _parse_tool_arguments(raw) -> dict:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ToolFailure("工具参数不是合法 JSON", code="INVALID_ARGUMENTS",
                              hint="arguments 必须是 JSON 对象字符串") from exc
        if not isinstance(value, dict):
            raise ToolFailure("工具参数必须是 JSON 对象", code="INVALID_ARGUMENTS")
        return value
    raise ToolFailure("工具参数类型不支持", code="INVALID_ARGUMENTS")


def _parse_final_content(content) -> tuple[dict | None, str | None]:
    if not isinstance(content, str) or not content.strip():
        return None, "最终回答为空"
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None, "不是合法 JSON"
        else:
            return None, "不是合法 JSON"
    if not isinstance(data, dict):
        return None, "JSON 顶层必须是对象"
    return data, None


def _summarize_result(name: str, data: dict) -> str:
    try:
        if name == "list_courses":
            names = "、".join(c["name"] for c in data.get("courses", []))
            return f"返回 {data.get('count', 0)} 门课程：{names}"
        if name == "query_tasks":
            summary = data.get("summary", {})
            text = (f"返回 {data.get('count', 0)} 条任务（已完成 {summary.get('done', 0)}，"
                    f"未完成 {summary.get('pending', 0)}）")
            if data.get("truncated"):
                text += "，结果已截断"
            return text
        if name == "task_stats":
            weakest = "、".join(f"{p['point']}({p['mastery']})" for p in data.get("weakest_points", []))
            return (f"完成率 {data.get('completion_rate')}%（{data.get('done_tasks')}/{data.get('total_tasks')}），"
                    f"总时长 {data.get('total_minutes')} 分钟，薄弱点：{weakest}")
        if name == "create_review_task":
            if data.get("created"):
                return (f"已创建任务 #{data.get('task_id')}：{data.get('title')}"
                        f"（{data.get('date')}，{data.get('minutes')} 分钟）")
            return f"未重复创建：任务 #{data.get('task_id')} 已存在（{data.get('date')}）"
    except (KeyError, TypeError):
        pass
    return summarize(data, 160)


@dataclass
class ConfirmationRequest:
    run_id: str
    step: int
    tool: str
    args: dict
    impact: str
    timeout_s: float = 20.0


@dataclass
class RunResult:
    run_id: str
    status: str
    objective: str
    course: str
    days: int
    steps: int
    model_calls: int
    tool_calls: int
    duration_ms: int
    tokens: int
    final_answer: dict | None
    checks: list[dict]
    error: dict | None
    created_task_ids: list[int]
    denied_tools: list[str]
    max_steps: int
    deadline_s: float
    log_path: str
    trace: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_SUCCESS

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "objective": self.objective,
            "course": self.course,
            "days": self.days,
            "steps": self.steps,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "duration_ms": self.duration_ms,
            "tokens": self.tokens,
            "final_answer": self.final_answer,
            "checks": self.checks,
            "error": self.error,
            "created_task_ids": self.created_task_ids,
            "denied_tools": self.denied_tools,
            "max_steps": self.max_steps,
            "deadline_s": self.deadline_s,
            "log_path": self.log_path,
        }


class AgentHarness:
    """单 Agent 运行时：模型决定下一步，程序负责检查、执行、限制和记录。"""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        max_steps: int = 8,
        deadline_s: float = 90.0,
        llm_timeout_s: float = 30.0,
        max_llm_retries: int = 1,
        max_corrections: int = 2,
        max_tool_calls_per_step: int = 3,
        confirm: Callable[[ConfirmationRequest], dict] | None = None,
        llm: Callable | None = None,
        log_path: str | None = None,
        observer: Callable[[dict], None] | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 900,
    ):
        from app.service import llm_client

        self.registry = registry
        self.max_steps = max_steps
        self.deadline_s = deadline_s
        self.llm_timeout_s = llm_timeout_s
        self.max_llm_retries = max_llm_retries
        self.max_corrections = max_corrections
        self.max_tool_calls_per_step = max_tool_calls_per_step
        self.confirm = confirm
        self.llm = llm or llm_client.chat_tools
        self.log_path = log_path
        self.observer = observer
        self.system_prompt = system_prompt or AGENT_SYSTEM_PROMPT
        self.temperature = temperature
        self.max_tokens = max_tokens

    # ---------- 运行 ----------

    def run(self, objective: str, *, course: str, days: int, run_id: str | None = None) -> RunResult:
        self.run_id = run_id or f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
        self.started = time.monotonic()
        self.tokens = 0
        self.model_calls = 0
        self.tool_calls = 0
        self.created_task_ids: list[int] = []
        self.denied_tools: list[str] = []
        self.trace: list[dict] = []
        self.logger = RunLogger(self.run_id, self.log_path)

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": objective},
        ]
        status = STATUS_VERIFY_FAILED
        final_answer = None
        checks: list[dict] = []
        error = None
        step = 0
        corrections = 0

        self.logger.event(
            "run_start", objective=objective, course=course, days=days,
            max_steps=self.max_steps, deadline_s=self.deadline_s,
            llm_timeout_s=self.llm_timeout_s, max_llm_retries=self.max_llm_retries,
            max_corrections=self.max_corrections, tools=self.registry.names(),
        )
        self._emit(
            "run_start", run_id=self.run_id, objective=objective,
            limits={"max_steps": self.max_steps, "deadline_s": self.deadline_s,
                    "llm_timeout_s": self.llm_timeout_s, "max_llm_retries": self.max_llm_retries},
            tools=self.registry.names(),
        )

        while True:
            if step >= self.max_steps:
                status = STATUS_STEP_LIMIT
                error = {"code": "STEP_LIMIT",
                         "message": f"达到最大执行步数 {self.max_steps}，任务未完成"}
                break
            if self._remaining() <= 0:
                status = STATUS_TIME_LIMIT
                error = {"code": "TIME_LIMIT",
                         "message": f"达到总运行时限 {self.deadline_s:.0f} 秒，任务未完成"}
                break

            step += 1
            try:
                response = self._call_model(messages, step)
            except BusinessError as exc:
                if self._remaining() <= 0 or exc.code == "TIME_LIMIT":
                    status = STATUS_TIME_LIMIT
                    error = {"code": "TIME_LIMIT",
                             "message": f"达到总运行时限 {self.deadline_s:.0f} 秒（{exc.message}）"}
                else:
                    status = STATUS_LLM_ERROR
                    error = {"code": exc.code, "message": exc.message}
                self._emit("llm_error", step=step, code=error["code"], message=error["message"])
                break

            usage = response.get("usage") or {}
            self.tokens += int(usage.get("total_tokens") or 0)
            self.model_calls += 1
            message = response.get("message") or {}
            content = message.get("content") or ""
            tool_calls = message.get("tool_calls") or []
            self.logger.event(
                "model_reply", step=step, latency_ms=response.get("latency_ms"),
                tokens=usage.get("total_tokens"),
                tool_calls=[(c.get("function") or {}).get("name") for c in tool_calls],
                content=summarize(content, 200),
            )
            self._emit(
                "model_reply", step=step, latency_ms=response.get("latency_ms"),
                tokens=usage.get("total_tokens"), content=content,
                tool_calls=[{"name": (c.get("function") or {}).get("name"),
                             "arguments": (c.get("function") or {}).get("arguments")}
                            for c in tool_calls],
            )

            if self._remaining() <= 0:
                status = STATUS_TIME_LIMIT
                error = {"code": "TIME_LIMIT",
                         "message": f"达到总运行时限 {self.deadline_s:.0f} 秒，任务未完成"}
                break

            if tool_calls:
                assistant = {"role": "assistant", "content": content}
                assistant["tool_calls"] = tool_calls
                messages.append(assistant)
                deadline_hit = False
                for index, call in enumerate(tool_calls):
                    call_id = call.get("id") or f"call-{step}-{index}"
                    tool_name = (call.get("function") or {}).get("name") or ""
                    if index >= self.max_tool_calls_per_step:
                        result = _error_result(ToolFailure(
                            f"一步最多允许 {self.max_tool_calls_per_step} 个工具调用",
                            code="TOO_MANY_TOOL_CALLS", hint="请减少一次请求中的工具数量",
                        ))
                        self.logger.event("tool_rejected", step=step, tool=tool_name,
                                          code="TOO_MANY_TOOL_CALLS", message="超过每步工具调用上限")
                        self._emit("tool_rejected", step=step, tool=tool_name,
                                   code="TOO_MANY_TOOL_CALLS", message="超过每步工具调用上限")
                    elif deadline_hit or self._remaining() <= 0:
                        deadline_hit = True
                        result = _error_result(ToolFailure(
                            "总运行时限已到，该工具调用未执行", code="TIME_LIMIT",
                            hint="运行已结束，不执行未开始的调用",
                        ))
                        self.logger.event("tool_skipped", step=step, tool=tool_name, code="TIME_LIMIT")
                        self._emit("tool_skipped", step=step, tool=tool_name, code="TIME_LIMIT")
                    else:
                        call_id, result = self._handle_tool_call(call, call_id, step)
                    messages.append({
                        "role": "tool", "tool_call_id": call_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                if deadline_hit:
                    status = STATUS_TIME_LIMIT
                    error = {"code": "TIME_LIMIT",
                             "message": f"达到总运行时限 {self.deadline_s:.0f} 秒，任务未完成"}
                    break
                continue

            # ---- 最终回答：先解析，再由程序核验 ----
            parsed, parse_error = _parse_final_content(content)
            if parse_error:
                corrections += 1
                self.logger.event("final_rejected", step=step, error="INVALID_FINAL", message=parse_error)
                self._emit("final_rejected", step=step, error="INVALID_FINAL", message=parse_error)
                if corrections > self.max_corrections:
                    status = STATUS_VERIFY_FAILED
                    error = {"code": "INVALID_FINAL",
                             "message": f"最终回答不是约定的 JSON，已修正 {self.max_corrections} 次仍未通过：{parse_error}"}
                    break
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content":
                                 f"你的输出不是约定的 JSON（{parse_error}）。请只输出 JSON 对象，"
                                 "不要 Markdown 代码围栏或其他文字。"})
                continue

            if parsed.get("status") == "failed":
                status = STATUS_AGENT_FAILED
                final_answer = parsed
                error = {"code": "AGENT_REPORTED_FAILURE",
                         "message": str(parsed.get("reason") or "模型报告无法完成任务")}
                break

            try:
                gt = ground_truth(course, days)
            except BusinessError as exc:
                status = STATUS_VERIFY_FAILED
                error = {"code": exc.code, "message": f"无法读取数据库核对结果：{exc.message}"}
                self._emit("verify_error", step=step, message=error["message"])
                break

            checks = verify_success(parsed, gt, {
                "created_task_ids": self.created_task_ids,
                "denied_tools": self.denied_tools,
            })
            passed = all(check["ok"] for check in checks)
            self.logger.event("verify", step=step, passed=passed,
                             checks=[{k: c[k] for k in ("name", "ok", "expected", "actual")} for c in checks])
            self._emit("verify", step=step, passed=passed, checks=checks,
                       ground_truth=gt)
            if passed:
                status = STATUS_SUCCESS
                final_answer = parsed
                break

            corrections += 1
            failed = [c for c in checks if not c["ok"]]
            detail = "；".join(
                f"{c['name']}（期望：{c['expected']}，实际：{c['actual']}）" for c in failed
            )
            if corrections > self.max_corrections:
                status = STATUS_VERIFY_FAILED
                final_answer = parsed
                error = {"code": "VERIFY_FAILED",
                         "message": f"程序核验不通过，已修正 {self.max_corrections} 次仍未通过：{detail}"}
                break
            self.logger.event("final_rejected", step=step, error="VERIFY_FAILED", message=detail)
            self._emit("final_rejected", step=step, error="VERIFY_FAILED", message=detail)
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content":
                             f"程序核验未通过：{detail}。请调用工具核对数据，并只输出修正后的 JSON。"})

        if final_answer is not None:
            self._emit("final_answer", answer=final_answer)

        duration_ms = int((time.monotonic() - self.started) * 1000)
        if error is None and status != STATUS_SUCCESS:
            error = {"code": "RUN_INCOMPLETE", "message": "运行结束但未得到可核验的最终回答"}
        self.logger.event("run_end", status=status, steps=step, model_calls=self.model_calls,
                          tool_calls=self.tool_calls, duration_ms=duration_ms, tokens=self.tokens,
                          error=error, checks=checks,
                          created_task_ids=self.created_task_ids, denied_tools=self.denied_tools)
        self._emit("run_end", status=status, steps=step, duration_ms=duration_ms,
                   tokens=self.tokens, model_calls=self.model_calls, tool_calls=self.tool_calls,
                   error=error, log_path=self.logger.path)
        log.info("Agent 运行结束：run_id=%s，status=%s，steps=%d，耗时 %d ms，tokens=%d",
                 self.run_id, status, step, duration_ms, self.tokens)

        return RunResult(
            run_id=self.run_id,
            status=status,
            objective=objective,
            course=course,
            days=days,
            steps=step,
            model_calls=self.model_calls,
            tool_calls=self.tool_calls,
            duration_ms=duration_ms,
            tokens=self.tokens,
            final_answer=final_answer,
            checks=checks,
            error=error,
            created_task_ids=list(self.created_task_ids),
            denied_tools=list(self.denied_tools),
            max_steps=self.max_steps,
            deadline_s=self.deadline_s,
            log_path=self.logger.path,
            trace=self.trace,
        )

    # ---------- 模型与工具 ----------

    def _remaining(self) -> float:
        return self.deadline_s - (time.monotonic() - self.started)

    def _emit(self, kind: str, **fields) -> None:
        event = {"kind": kind, "ts": datetime.datetime.now().isoformat(timespec="milliseconds")}
        event.update(fields)
        self.trace.append(event)
        if self.observer is not None:
            try:
                self.observer(event)
            except Exception:  # noqa: BLE001 - 观察者异常不影响运行
                log.exception("trace observer 处理失败")

    def _call_model(self, messages: list[dict], step: int) -> dict:
        attempts = 0
        while True:
            attempts += 1
            remaining = self._remaining()
            if remaining <= 0:
                raise BusinessError("总运行时限已到", 504, "TIME_LIMIT")
            timeout = min(self.llm_timeout_s, remaining)
            try:
                response = self.llm(
                    messages, self.registry.openai_tools(), timeout=timeout,
                    temperature=self.temperature, max_tokens=self.max_tokens,
                )
            except BusinessError as exc:
                if exc.code in RETRIABLE_LLM_CODES and attempts <= self.max_llm_retries and self._remaining() > 1:
                    self.logger.event("llm_retry", step=step, attempt=attempts, code=exc.code)
                    self._emit("llm_retry", step=step, attempt=attempts, code=exc.code, message=exc.message)
                    continue
                raise
            self.logger.event("model_call", step=step, attempt=attempts,
                              latency_ms=response.get("latency_ms"), messages=len(messages))
            self._emit("model_call", step=step, attempt=attempts,
                       latency_ms=response.get("latency_ms"),
                       tokens=(response.get("usage") or {}).get("total_tokens"),
                       messages=len(messages))
            return response

    def _handle_tool_call(self, call: dict, call_id: str, step: int) -> tuple[str, dict]:
        function = call.get("function") or {}
        name = function.get("name") or ""
        raw_args = function.get("arguments")
        try:
            args = _parse_tool_arguments(raw_args)
            self.logger.event("tool_request", step=step, call_id=call_id, tool=name,
                              args=summarize(args, 200))
            spec, parsed = self.registry.validate(name, args)
        except ToolFailure as exc:
            self.tool_calls += 1
            self.logger.event("tool_rejected", step=step, tool=name, code=exc.code, message=exc.message)
            self._emit("tool_rejected", step=step, tool=name, code=exc.code, message=exc.message)
            return call_id, _error_result(exc)

        args_dict = parsed.model_dump()
        if spec.mutating:
            if spec.name in self.denied_tools:
                exc = ToolFailure("该写操作此前已被用户拒绝，本次运行不再请求确认，也不会执行",
                                  code="WRITE_ALREADY_DENIED",
                                  hint="按未创建任务继续给出最终回答，不要再次请求写操作")
                self.tool_calls += 1
                self.logger.event("tool_rejected", step=step, tool=spec.name,
                                  code=exc.code, message=exc.message)
                self._emit("tool_rejected", step=step, tool=spec.name,
                           code=exc.code, message=exc.message)
                return call_id, _error_result(exc)
            if self._remaining() <= 0:
                exc = ToolFailure("总运行时限已到，写操作没有请求确认，也没有执行",
                                  code="TIME_LIMIT", hint="本次运行已结束，不执行写操作")
                self.tool_calls += 1
                self.logger.event("tool_rejected", step=step, tool=spec.name,
                                  code="TIME_LIMIT", message=exc.message)
                self._emit("tool_rejected", step=step, tool=spec.name,
                           code="TIME_LIMIT", message=exc.message)
                return call_id, _error_result(exc)
            if self.confirm is None:
                exc = ToolFailure("写操作需要人工确认，但当前运行没有确认通道，已拒绝执行",
                                  code="CONFIRM_UNAVAILABLE", hint="本次未执行任何写操作")
                self.denied_tools.append(spec.name)
                self.tool_calls += 1
                self.logger.event("confirm", step=step, tool=spec.name, args=args_dict,
                                  approved=False, reason="NO_CONFIRM_CHANNEL", waited_ms=0)
                self._emit("confirm", step=step, tool=spec.name, args=args_dict,
                           impact=spec.impact, approved=False, reason="没有确认通道", waited_ms=0)
                return call_id, _error_result(exc)

            request = ConfirmationRequest(
                run_id=self.run_id, step=step, tool=spec.name, args=args_dict,
                impact=spec.impact, timeout_s=min(20.0, max(0.0, self._remaining())),
            )
            self._emit("confirm_request", step=step, tool=spec.name, args=args_dict,
                       impact=spec.impact, timeout_s=request.timeout_s)
            decision = self.confirm(request) or {}
            approved = bool(decision.get("approved"))
            waited_ms = int(decision.get("waited_ms") or 0)
            reason = str(decision.get("reason") or ("用户同意" if approved else "用户拒绝"))
            self.logger.event("confirm", step=step, tool=spec.name, args=args_dict,
                              approved=approved, reason=reason, waited_ms=waited_ms)
            self._emit("confirm", step=step, tool=spec.name, approved=approved,
                       reason=reason, waited_ms=waited_ms)
            if not approved:
                self.denied_tools.append(spec.name)
                self.tool_calls += 1
                return call_id, _error_result(ToolFailure(
                    "用户未同意该操作，工具没有执行", code="USER_DENIED",
                    hint="不要再次请求写操作，按未创建任务继续给出最终回答",
                ))
            if self._remaining() <= 0:
                self.tool_calls += 1
                self.logger.event("tool_rejected", step=step, tool=spec.name,
                                  code="TIME_LIMIT", message="确认等待后总时限已到，写操作未执行")
                self._emit("tool_rejected", step=step, tool=spec.name,
                           code="TIME_LIMIT", message="确认等待后总时限已到，写操作未执行")
                return call_id, _error_result(ToolFailure(
                    "确认等待后总运行时限已到，写操作没有执行", code="TIME_LIMIT",
                    hint="本次运行已结束，不执行写操作",
                ))

        attempts = 0
        while True:
            attempts += 1
            try:
                output = self.registry.execute(
                    spec, parsed,
                    timeout_s=min(spec.timeout_s, max(0.2, self._remaining())),
                )
            except ToolFailure as exc:
                if (exc.retriable and not spec.mutating and attempts <= spec.max_retries
                        and self._remaining() > 1):
                    self.logger.event("tool_retry", step=step, tool=spec.name,
                                      attempt=attempts, code=exc.code)
                    self._emit("tool_retry", step=step, tool=spec.name,
                               attempt=attempts, code=exc.code, message=exc.message)
                    continue
                self.tool_calls += 1
                self.logger.event("tool_result", step=step, tool=spec.name, ok=False,
                                  attempt=attempts, code=exc.code, message=exc.message,
                                  latency_ms=None)
                self._emit("tool_result", step=step, tool=spec.name, ok=False,
                           attempt=attempts, code=exc.code, message=exc.message,
                           summary=f"{exc.code}：{exc.message}")
                return call_id, _error_result(exc)

            data = output["data"]
            if spec.mutating and data.get("created") and data.get("task_id") is not None:
                self.created_task_ids.append(data["task_id"])
            summary = _summarize_result(spec.name, data)
            self.tool_calls += 1
            self.logger.event("tool_result", step=step, tool=spec.name, ok=True,
                              attempt=attempts, latency_ms=output["latency_ms"],
                              summary=summary)
            self._emit("tool_result", step=step, tool=spec.name, ok=True,
                       attempt=attempts, latency_ms=output["latency_ms"], summary=summary)
            return call_id, {"ok": True, "data": data, "latency_ms": output["latency_ms"]}