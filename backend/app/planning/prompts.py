"""Prompt 模板：v1 为第1周初版，v2 为根据评测结果修改后的版本。

v1 只给出输入信息和 JSON 字段，不写业务约束，用于检验“直接调用模型”的效果；
v2 在 v0.6 中加入窗口、预算、覆盖、优先级和输出长度规则，并给一个可覆盖的
边界示例。v2 的规则和 validator 中的程序检查一一对应，便于修复提示引用。
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

PLAN_V2_HEADER = """你是 StudyPilot 的学习计划生成器，面向大学生考前复习。请根据下面的输入生成每天可以直接执行的任务。

课程：{course}（{term}）
考试日期：{exam_date}
计划窗口：{window}（任务日期只能在这个范围内，最晚 {window_last}）
每天可用时间：{available_minutes} 分钟（当天所有任务合计不得超过）
窗口总预算：{total_budget} 分钟；知识点最少需要：{min_required_minutes} 分钟
{feasible_note}
知识点掌握度（百分数）：
{points}
最薄弱知识点：{weak_points}
任务数量上限：{max_tasks} 个；每天最多 {max_tasks_per_day} 个；每个知识点最多 {max_tasks_per_point} 个。
"""

PLAN_V2_RULES = r"""
硬性规则（必须全部满足，否则计划无效）：
1. 只能使用“知识点掌握度”中列出的知识点名称，不得新增名称，也不得使用“综合复习”等未列出的名称。
2. 任务日期必须在计划窗口内，且早于考试日期。
3. 每天所有任务的分钟数合计不得超过每天可用时间。
4. 可以覆盖全部知识点时，每个知识点至少安排 1 个任务、最多 2 个任务。
5. 掌握度低于 60 的知识点要优先安排在更早的日期；薄弱点的平均安排日期不得晚于掌握度不低于 60 的知识点。
6. 单个任务 10—120 分钟；每天最多 3 个任务；总任务数不超过上限。
7. 任务标题要写清具体动作和内容（如梳理概念、做例题、错题复盘、限时模拟），不能只写“复习X”。
8. summary 不超过 120 字；reason 不超过 60 字，要说明该任务与掌握度或考试日期的关系。
9. 如果预算不足、无法覆盖全部知识点，按掌握度从低到高安排，尽可能多覆盖，并在 notes 中写明“时间不足，无法全部覆盖”以及优先安排的理由。
10. 只输出 JSON，不要 Markdown 代码块、不要额外解释。

输出 JSON 格式：
{"summary": "计划说明", "tasks": [{"point": "知识点名称", "title": "任务名称", "date": "YYYY-MM-DD", "minutes": 30, "reason": "安排理由"}], "notes": "取舍说明"}

示例（窗口 2026-11-24 至 2026-11-26，每天 60 分钟，3 个知识点，可以覆盖）：
{"summary": "三天内先安排掌握度最低的图，再安排树和线性表，每天不超过60分钟。", "tasks": [{"point": "图", "title": "图的基本概念与存储结构梳理", "date": "2026-11-24", "minutes": 30, "reason": "掌握度40%最低，先梳理基础"}, {"point": "图", "title": "DFS与BFS遍历练习", "date": "2026-11-25", "minutes": 30, "reason": "第二天用练习巩固薄弱点"}, {"point": "树", "title": "二叉树性质与遍历练习", "date": "2026-11-25", "minutes": 30, "reason": "掌握度60%，安排练习"}, {"point": "线性表", "title": "线性表核心操作回顾", "date": "2026-11-26", "minutes": 20, "reason": "掌握度85%，快速回顾保持记忆"}, {"point": "树", "title": "树与图综合题复盘", "date": "2026-11-26", "minutes": 40, "reason": "综合检验薄弱点"}], "notes": "可以覆盖全部知识点"}

请根据上面的输入生成计划，只输出 JSON。
"""

PLAN_TEMPLATES = {"v1": PLAN_V1, "v2": PLAN_V2_HEADER + PLAN_V2_RULES}


def plan_messages(inp: PlanningInput, version: str = "v1") -> list[dict]:
    template = PLAN_TEMPLATES.get(version)
    if template is None:
        raise ValueError(f"未知的 Prompt 版本：{version}")
    context = prompt_context(inp)
    if version == "v2":
        content = PLAN_V2_HEADER.format(**context) + PLAN_V2_RULES
    else:
        content = template.format(**context)
    return [{"role": "user", "content": content}]
