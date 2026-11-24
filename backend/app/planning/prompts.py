"""Prompt 模板：v1 为第1周初版，v2 为根据评测结果修改后的版本。

v1 只给出输入信息和 JSON 字段，不写业务约束，用于检验“直接调用模型”的效果；
v2 在 v0.6 中加入窗口、预算、覆盖和排序规则，并给一个边界示例。
"""

from app.planning.context import PlanningInput, prompt_context

PLAN_V1 = """你是 StudyPilot 的学习计划助手，负责把课程复习要求排成每日任务。

课程：{course}（{term}）
考试日期：{exam_date}
今天：{start_date}
每天可用时间：{available_minutes} 分钟
计划窗口：{window}
知识点掌握度：
{points}
最薄弱知识点：{weak_points}

请只输出 JSON，不要输出解释文字，格式为：
{{"summary": "计划说明", "tasks": [{{"point": "知识点名称", "title": "任务名称", "date": "YYYY-MM-DD", "minutes": 60, "reason": "安排理由"}}], "notes": "需要说明的取舍"}}
"""

PLAN_TEMPLATES = {"v1": PLAN_V1}


def plan_messages(inp: PlanningInput, version: str = "v1") -> list[dict]:
    template = PLAN_TEMPLATES.get(version)
    if template is None:
        raise ValueError(f"未知的 Prompt 版本：{version}")
    context = prompt_context(inp)
    return [{"role": "user", "content": template.format(**context)}]
