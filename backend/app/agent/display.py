"""终端展示：把 Harness 事件格式化成易读的中文行（宽度适配终端截图）。"""

import json
import unicodedata

STATUS_TEXT = {
    "success": "成功完成",
    "step_limit": "达到最大执行步数",
    "time_limit": "达到总运行时限",
    "llm_error": "模型调用失败",
    "verify_failed": "结果核验不通过",
    "agent_failed": "模型报告无法完成",
}

CHECK_MARK = "通过"
CHECK_FAIL = "未通过"


def _char_width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1


def wrap(text: str, width: int = 88) -> list[str]:
    lines, current, used = [], "", 0
    for ch in str(text):
        w = _char_width(ch)
        if used + w > width and current:
            lines.append(current)
            current, used = ch, w
        else:
            current += ch
            used += w
    if current:
        lines.append(current)
    return lines or [""]


def _json_one_line(value, limit: int = 150) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_event(event: dict) -> list[str]:
    kind = event.get("kind")
    if kind == "run_start":
        limits = event.get("limits", {})
        return [
            f"运行编号：{event.get('run_id')}",
            *wrap(f"目标：{event.get('objective')}"),
            f"限制：最多 {limits.get('max_steps')} 步；总时限 {limits.get('deadline_s'):.0f} 秒；"
            f"单次模型超时 {limits.get('llm_timeout_s'):.0f} 秒；模型重试 {limits.get('max_llm_retries')} 次",
            "允许工具：" + "、".join(event.get("tools", [])),
            "-" * 88,
        ]
    if kind == "model_call":
        return [f"[step {event.get('step')}] 调用模型（第 {event.get('attempt')} 次，上下文 {event.get('messages')} 条）"
                f"... {event.get('latency_ms')} ms，tokens={event.get('tokens')}"]
    if kind == "model_reply":
        lines = []
        for call in event.get("tool_calls", []):
            args = call.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    pass
            lines.append("  模型请求工具：" + call.get("name") + "(" + _json_one_line(args) + ")")
        return lines
    if kind == "tool_result":
        step = event.get("step")
        tool = event.get("tool")
        if event.get("ok"):
            return [f"  -> ok：{event.get('summary')}（{event.get('latency_ms')} ms）"]
        return [*wrap(f"  -> 失败（{event.get('code')}）：{event.get('message')}", 86)]
    if kind == "tool_rejected":
        return [*wrap(f"  -> 程序拒绝执行（{event.get('code')}）：{event.get('message')}", 86)]
    if kind == "tool_retry":
        return [*wrap(f"  -> 工具重试（第 {event.get('attempt')} 次，{event.get('code')}）："
                     f"{event.get('message')}", 86)]
    if kind == "tool_skipped":
        return [f"  -> 未执行：{event.get('tool')}（总时限已到）"]
    if kind == "confirm_request":
        return [
            "",
            "[确认] 写操作需要人工确认",
            f"  工具：{event.get('tool')}",
            *wrap("  参数：" + _json_one_line(event.get("args"), 200), 86),
            *((lambda lines: [("  影响：" + lines[0])] + ["        " + line for line in lines[1:]])(
                wrap(event.get("impact", ""), 84))),
        ]
    if kind == "confirm":
        approved = event.get("approved")
        return [f"  => {'用户同意' if approved else '用户拒绝或等待超时'}（等待 {event.get('waited_ms')} ms）"]
    if kind == "verify":
        lines = ["[程序核验]" + ("全部通过" if event.get("passed") else "未通过")]
        for check in event.get("checks", []):
            mark = CHECK_MARK if check.get("ok") else CHECK_FAIL
            lines.append("  " + mark + f"：{check.get('name')} = {check.get('actual')}"
                         f"（期望 {check.get('expected')}）")
        return lines
    if kind == "final_rejected":
        return ["  [核验反馈] " + line for line in wrap(event.get("message", ""), 82)]
    if kind == "verify_error":
        return ["  [核验失败] " + line for line in wrap(event.get("message", ""), 82)]
    if kind == "final_answer":
        payload = json.dumps(event.get("answer"), ensure_ascii=False, indent=2)
        return ["[最终回答]", *payload.splitlines()]
    if kind == "llm_retry":
        return [f"  [模型重试] 第 {event.get('attempt')} 次，{event.get('code')}：{event.get('message')}"]
    if kind == "llm_error":
        return [*wrap(f"[模型调用失败] {event.get('code')}：{event.get('message')}", 86)]
    if kind == "run_end":
        status = event.get("status")
        return [
            "",
            "-" * 88,
            f"运行结果：{status}（{STATUS_TEXT.get(status, status)}）",
            f"步数：{event.get('steps')}（模型调用 {event.get('model_calls')} 次，"
            f"工具调用 {event.get('tool_calls')} 次），耗时 {event.get('duration_ms')} ms，"
            f"tokens={event.get('tokens')}",
            *(["错误：" + _json_one_line(event.get("error"), 140)] if event.get("error") else []),
            f"日志：{event.get('log_path')}",
        ]
    return []


def print_event(event: dict) -> None:
    for line in format_event(event):
        print(line, flush=True)