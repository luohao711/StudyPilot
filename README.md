# StudyPilot 课程实验报告

项目：StudyPilot——基于大语言模型的个性化学习任务规划与知识追踪系统

学生信息：一班，24111302025，罗心月

## 目录说明

- `backend/`：FastAPI + SQLite 后端，按接口层、业务层、数据访问层组织。
- `web/`：Vue 3 + Vite 前端，包含首页、学习计划页和学习进度页。
- `reports/experiment2/`：实验2原始题目、LaTeX源文件和最终PDF。
- `reports/experiment3/`：实验3原始题目、LaTeX源文件、设计图和最终PDF。
- `reports/experiment4/`：实验4原始题目、LaTeX源文件、运行截图和最终PDF。
- `reports/experiment5/`：实验5 LaTeX源文件、运行截图和最终PDF。
- `reports/experiment6/`：实验6 LaTeX源文件、循环图、运行截图、运行记录和最终PDF。

## 运行后端

```bash
cd backend
python3 -m app.seed --reset          # 写入样例数据（--clear 只清空）
python3 -m uvicorn app.main:app --port 8000
```

接口文档：http://127.0.0.1:8000/docs ，运行日志：`backend/logs/app.log`。
未配置 `LLM_API_KEY` 时，计划生成使用规则模板降级，不调用模型服务。

## 资料问答（RAG）

知识库资料登记在 `backend/kb/sources.json`，构建与使用：

```bash
cd backend
python3 -m app.tools.build_kb                          # 切分资料，生成 kb/chunks.jsonl
export DEEPSEEK_API_KEY=sk-xxx                         # 模型密钥只从环境变量读取
python3 -m app.tools.ask_cli "知识点掌握度怎么更新？"     # 命令行问答
python3 -m app.tools.ask_cli "计划怎么生成？" --show-retrieval --only-context
python3 -m app.tools.inspect_chunks --source S2 --limit 3   # 片段抽查
python3 -m app.tools.context_check "总体架构分几层？"        # 上下文检查与精简
python3 -m app.tools.compare_test                      # 直接模型 vs RAG 对比测试
python3 -m app.tools.fail_demo                         # 调用失败处理演示
```

页面入口：前端「资料问答」页（`#/ask`），接口 `POST /api/qa/ask`，参数
`question`、`mode`（rag/direct）、`top_k`、`prompt_version`（v1/v2/v3）。

## Agent（实验6）

单 Agent 运行时位于 `backend/app/agent/`，包含工具白名单、参数校验、写操作人工确认、
步数/超时/重试限制、结果核验和 JSONL 运行日志。任务是“统计课程最近 14 天任务完成情况并给出建议”，
完成率低于 60% 时为最薄弱知识点创建 30 分钟复习任务（写操作需确认）。

```bash
cd backend
python3 -m app.tools.agent_seed --reset --show     # 写入12条样例任务，完成率41.7%
source ../tmp/env.sh                                # 仅本机：从 DSH 凭据导出 DEEPSEEK_API_KEY
python3 -m app.tools.agent_cli --confirm ask        # 写操作前输入 y 同意、其他拒绝
python3 -m app.tools.agent_cli --confirm no         # 演示拒绝
python3 -m app.tools.agent_cli --max-steps 3 --confirm yes
```

```bash
cd backend
python3 -m app.tools.agent_tools_demo              # 接入 Agent 前直接测试工具（正确/错误参数）
python3 -m app.tools.agent_scenarios param_error    # 也可用 tool_exception / step_limit /
                                                    # time_limit / confirm_approve / confirm_deny / verify_fix
python3 -m app.tools.agent_log --last               # 查看最近一次运行的 JSONL 日志
python3 -m app.tools.agent_log --last-success
```

关键限制：最多 8 步，每步最多 3 个工具调用；总时限 90 秒；模型单次超时 30 秒、可重试 1 次；
只读工具超时 3 秒、最多重试 2 次；写工具超时 5 秒、不自动重试；确认等待 20 秒且计入总时限（拒绝后不再重复询问）；
最终回答由程序核对数据库后判定，不通过最多修正 2 次。日志写入 `backend/logs/agent_runs.jsonl`。

## AI 学习计划原型（实验7）

核心 AI 能力是“按掌握度生成可执行的考前计划”：输入课程、考试目标、知识点掌握度和
每天可用时间，模型输出每日任务；程序再检查日期窗口、每天时长上限、知识点覆盖、
薄弱点优先级和任务时长，不合格时反馈给模型修复一次，仍不合格则改用规则模板降级，
保证页面始终能拿到可执行计划。

```bash
cd backend
python3 -m app.seed --reset                         # 样例课程与知识点
source ../tmp/env.sh                                # 仅本机：从 DSH 凭据导出 DEEPSEEK_API_KEY
python3 -m app.tools.plan_ai_cli --course 软件工程 --start 2026-11-24 --version v2 --save
python3 -m app.tools.plan_ai_cli --course 软件工程 --version v1   # 初版对照（只做结构校验）
python3 -m app.tools.plan_eval --methods rule,v05,v06 --tag week2  # 同一评测集对比三种方案
```

- 评测集：`backend/eval/plan_cases.jsonl`（15 个样例，固定 `base_date`，可重复运行）。
- 评测明细与汇总：`reports/experiment7/runs/eval-*.jsonl`、`eval-summary-*.json`。
- 运行日志：`backend/logs/plan_ai_runs.jsonl`（含校验、修复、降级事件，密钥遮挡）。
- 配置集中在 `backend/app/planning/config.py`：模型、温度、输出上限、超时、修复次数、
  价格和评测路径；密钥只从 `DEEPSEEK_API_KEY` 环境变量读取。
- 接口：`POST /api/plans/generate-ai`（参数 course_id、available_minutes、start_date、save）；
  前端「学习计划」页有“AI 生成计划（带检查）”按钮，显示校验结果、修复次数、耗时和费用。

## 运行前端

```bash
cd web
npm install
npm run dev        # http://localhost:5173
```

## 测试

```bash
cd backend
python3 -m pytest tests -v
```

测试使用临时数据库，覆盖课程、考试目标、知识点、计划生成、任务完成和数据库不可用等情况；
`tests/test_rag.py` 覆盖知识库片段元数据、分词、检索排序、同义词扩展、引用校验和Prompt版本；
`tests/test_agent.py` 覆盖工具参数校验、访问范围、重复调用去重、步数/时间限制、工具异常、
人工确认同意/拒绝和结果核验，共 23 个用例；`tests/test_planning.py` 覆盖 AI 计划原型的输入校验、
约束校验、修复、降级、保存和接口，共 20 个用例；加上实验4、实验5的用例，全套共 63 个，
全部离线执行（正常流程的真实模型调用见命令行运行记录）。

## 编译实验报告

进入对应实验目录（如 `reports/experiment3/`）后执行：

```bash
xelatex 实验3-软件架构与界面设计.tex
xelatex 实验3-软件架构与界面设计.tex
```

使用 XeLaTeX 编译，以保证中文正常显示。

## 版本记录

- `v0.1`：可运行初版，项目骨架 + 课程保存与查询。
- `v0.2`：测试修订版，核心业务闭环、异常处理、日志和测试。
- `v0.3`：资料问答（LLM 调用、Prompt 迭代、知识库切分、BM25 检索、RAG 链路与对比测试）。
- `v0.4`：工具调用与单 Agent 运行时（工具白名单与参数校验、Agent 循环、人工确认、步数/超时/重试限制、结果核验与运行日志）。
- `v0.5`：AI 学习计划初版原型（直接调用模型、结构化输出、CLI、15 个样例的评测集与规则基线对比）。
- `v0.6`：AI 学习计划修订版（约束预处理、Prompt v2、程序校验、有限修复、规则降级、接口与页面入口）。