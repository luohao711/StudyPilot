# StudyPilot 课程实验报告

项目：StudyPilot——基于大语言模型的个性化学习任务规划与知识追踪系统

学生信息：一班，24111302025，罗心月

## 目录说明

- `backend/`：FastAPI + SQLite 后端，按接口层、业务层、数据访问层组织。
- `web/`：Vue 3 + Vite 前端，包含首页、学习计划页和学习进度页。
- `reports/experiment2/`：实验2原始题目、LaTeX源文件和最终PDF。
- `reports/experiment3/`：实验3原始题目、LaTeX源文件、设计图和最终PDF。
- `reports/experiment4/`：实验4原始题目、LaTeX源文件、运行截图和最终PDF。

## 运行后端

```bash
cd backend
python3 -m app.seed --reset          # 写入样例数据（--clear 只清空）
python3 -m uvicorn app.main:app --port 8000
```

接口文档：http://127.0.0.1:8000/docs ，运行日志：`backend/logs/app.log`。
未配置 `LLM_API_KEY` 时，计划生成使用规则模板降级，不调用模型服务。

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

测试使用临时数据库，覆盖课程、考试目标、知识点、计划生成、任务完成和数据库不可用等情况。

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
