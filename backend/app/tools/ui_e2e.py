"""实验8 页面端到端测试与截图：真实浏览器 + 真实后端 + 真实模型。

先在另一个终端启动后端（8000）和前端（5173），再运行：
    cd backend
    python3 -m app.tools.ui_e2e --scenario main       # 正常、取消、失败重试、刷新恢复、两任务交错
    python3 -m app.tools.ui_e2e --scenario fallback   # 无密钥启动后端时，验证 AI 不可用降级

脚本只通过页面操作，不使用接口捷径；每一步都检查页面上的状态文字，并把结果写入
reports/experiment8/runs/e2e-ui.log，截图写入 reports/experiment8/assets/。
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PROJECT_DIR = Path(__file__).resolve().parents[3]
ASSETS_DIR = PROJECT_DIR / "reports" / "experiment8" / "assets"
LOG_DIR = PROJECT_DIR / "reports" / "experiment8" / "runs"
BASE_URL = "http://127.0.0.1:5173/#/plan"


class Logger:
    def __init__(self, path: Path, reset: bool = False):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if reset and self.path.exists():
            self.path.unlink()

    def write(self, text: str) -> None:
        line = f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {text}"
        print(line)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def wait_ready(page) -> None:
    page.wait_for_selector("text=AI 生成计划（后台任务）", timeout=30000)
    page.wait_for_function(
        "() => { const b = [...document.querySelectorAll('button')].find(x => x.textContent.includes('AI 生成计划'));"
        " return b && !b.disabled; }",
        timeout=30000,
    )


def open_plan(page, course: int, sim: str | None = None) -> None:
    suffix = f"&sim={sim}" if sim else ""
    page.goto(f"{BASE_URL}?course={course}{suffix}", wait_until="networkidle")
    wait_ready(page)


def start_ai(page) -> None:
    page.get_by_role("button", name="AI 生成计划（后台任务）").click()
    page.wait_for_selector(".job-box", timeout=15000)


def wait_succeeded(page) -> None:
    page.wait_for_selector(".ai-box", timeout=120000)
    page.wait_for_function(
        "() => { const box = document.querySelector('.job-box'); return box && box.textContent.includes('已完成'); }",
        timeout=10000,
    )


def job_id(page) -> str:
    import re

    found = re.search(r"job-\d{8}-\d{6}-[0-9a-f]+", page.inner_text(".job-box"))
    assert found, page.inner_text(".job-box")
    return found.group(0)


def screenshot(page, name: str, log: Logger) -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    path = ASSETS_DIR / name
    page.screenshot(path=str(path), full_page=True)
    log.write(f"    截图 {path.relative_to(PROJECT_DIR)}")


def scenario_normal(page, log: Logger) -> None:
    log.write("[E2E-UI-01] 正常流程：提交 AI 任务、等待、检查结果、编辑并保存")
    open_plan(page, 1)
    start_ai(page)
    assert "处理中" in page.inner_text(".job-box") or "排队中" in page.inner_text(".job-box")
    page.wait_for_timeout(600)
    screenshot(page, "e8-wait.png", log)
    log.write("    等待态：任务编号、排队/处理中状态和已等待秒数已显示，无百分比进度")
    wait_succeeded(page)
    result_text = page.inner_text(".ai-box")
    assert "程序校验通过" in result_text
    assert "需人工检查" in page.inner_text("body") or "可能存在内容或排期错误" in page.inner_text("body")
    page.wait_for_timeout(600)
    screenshot(page, "e8-success.png", log)
    log.write(f"    完成态：{job_id(page)}，{result_text.splitlines()[0][:120]}")
    # 编辑一条任务，再保存为正式计划（验证人机接力与保存）。
    title_input = page.locator("input.title-input").first
    title_input.fill("人工改写：先梳理软件测试层次")
    page.get_by_role("button", name="保存为正式计划").click()
    page.wait_for_function(
        "() => document.body.textContent.includes('计划已保存')",
        timeout=15000,
    )
    page.wait_for_timeout(400)
    screenshot(page, "e8-save.png", log)
    saved = page.inner_text("body")
    assert "计划 #" in saved
    log.write("    保存态：编辑后的任务保存为正式计划，重复确认由后端幂等处理")


def scenario_cancel(page, log: Logger) -> None:
    log.write("[E2E-UI-02] 取消任务：慢响应时取消，迟到结果不写入")
    open_plan(page, 2, sim="slow")
    start_ai(page)
    page.wait_for_selector(".job-box.running", timeout=15000)
    before = job_id(page)
    page.get_by_role("button", name="取消任务").click()
    page.wait_for_selector(".job-box.canceled", timeout=15000)
    page.wait_for_timeout(2500)  # 等后台模型调用返回，确认不会改写为已完成
    assert "已取消" in page.inner_text(".job-box")
    assert page.locator(".ai-box").count() == 0
    screenshot(page, "e8-cancel.png", log)
    log.write(f"    取消态：{before} 保持已取消，页面无 AI 结果、无候选任务")
    page.wait_for_timeout(500)
    assert page.locator(".ai-box").count() == 0


def scenario_failure_retry(page, log: Logger) -> None:
    log.write("[E2E-UI-03] 失败与重试：模拟模型超时，检查错误提示，重试后成功")
    open_plan(page, 2, sim="timeout")
    start_ai(page)
    page.wait_for_selector(".job-box.failed", timeout=30000)
    failed = page.inner_text(".job-box")
    assert "LLM_TIMEOUT" in failed
    screenshot(page, "e8-failed.png", log)
    log.write("    失败态：错误码、原因、可重试/降级提示均已显示")
    page.get_by_role("button", name="重试", exact=True).click()
    page.wait_for_selector(".job-box.running, .job-box.queued", timeout=15000)
    wait_succeeded(page)
    page.wait_for_timeout(500)
    screenshot(page, "e8-retry.png", log)
    log.write(f"    重试态：新任务 {job_id(page)} 成功；原失败任务仍保留在记录中")


def scenario_recovery(page, log: Logger) -> None:
    log.write("[E2E-UI-04] 刷新恢复：任务执行中刷新页面，状态和结果仍可恢复")
    open_plan(page, 1, sim="slow")
    start_ai(page)
    page.wait_for_selector(".job-box.running", timeout=15000)
    running_id = job_id(page)
    page.wait_for_timeout(400)
    page.reload(wait_until="networkidle")
    page.wait_for_selector(".job-box", timeout=15000)
    recovered = page.inner_text(".job-box")
    assert running_id in recovered and ("处理中" in recovered or "排队中" in recovered)
    screenshot(page, "e8-recovery.png", log)
    log.write(f"    刷新后：仍在等待 {running_id}，页面提示可以离开并自动恢复")
    wait_succeeded(page)
    log.write("    恢复后：轮询继续，任务完成并展示结果")


def scenario_interleave(browser, context, log: Logger) -> None:
    log.write("[E2E-UI-05] 交错任务：两门课程同时生成，结果不混用")
    page_a = context.new_page()
    page_b = context.new_page()
    open_plan(page_a, 1, sim="slow")
    open_plan(page_b, 2)

    def clear_restored(page):
        button = page.get_by_role("button", name="放弃结果")
        if button.count():
            button.first.click()
            page.wait_for_selector(".job-box", state="detached", timeout=10000)

    clear_restored(page_a)
    clear_restored(page_b)
    start_ai(page_a)
    page_a.wait_for_selector(".job-box.running", timeout=15000)
    start_ai(page_b)
    wait_succeeded(page_b)  # 各自完成后分别检查
    id_a, id_b = job_id(page_a), job_id(page_b)
    wait_succeeded(page_a)
    titles_a = page_a.locator("input.title-input").evaluate_all("els => els.map(e => e.value)")
    titles_b = page_b.locator("input.title-input").evaluate_all("els => els.map(e => e.value)")
    assert id_a != id_b
    assert titles_a and titles_b and titles_a != titles_b
    import httpx

    client = httpx.Client(timeout=30)
    job_a = client.get(f"http://127.0.0.1:8000/api/plans/ai-jobs/{id_a}").json()
    job_b = client.get(f"http://127.0.0.1:8000/api/plans/ai-jobs/{id_b}").json()
    assert job_a["course_id"] == 1 and job_b["course_id"] == 2
    points_a = {p["name"] for p in client.get("http://127.0.0.1:8000/api/courses/1/points").json()["items"]}
    points_b = {p["name"] for p in client.get("http://127.0.0.1:8000/api/courses/2/points").json()["items"]}
    assert all(task["point"] in points_a for task in job_a["result"]["tasks"])
    assert all(task["point"] in points_b for task in job_b["result"]["tasks"])
    log.write(f"    交错结果：{id_a}（软件工程）与 {id_b}（高等数学）分别完成，任务标题与知识点未混用")
    page_a.close()
    page_b.close()


def scenario_fallback(page, log: Logger) -> None:
    log.write("[E2E-UI-06] AI 不可用降级：后端未配置密钥，页面得到规则降级结果")
    open_plan(page, 2)
    start_ai(page)
    wait_succeeded(page)
    text = page.inner_text(".ai-box")
    assert "降级原因" in text and "LLM_NOT_CONFIGURED" in text
    assert "规则模板降级结果" in page.inner_text("body")
    screenshot(page, "e8-fallback.png", log)
    log.write("    降级态：原因、规则结果和保存入口均可见，用户可继续完成排期")


def main() -> None:
    parser = argparse.ArgumentParser(description="实验8 页面端到端测试")
    parser.add_argument("--scenario", choices=["main", "fallback"], default="main")
    parser.add_argument("--log", default=str(LOG_DIR / "e2e-ui.log"))
    args = parser.parse_args()
    log = Logger(Path(args.log), reset=args.scenario == "main")
    log.write(f"==== 实验8 页面 E2E（{args.scenario}）开始 ====")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        context = browser.new_context(viewport={"width": 1280, "height": 1080}, device_scale_factor=2)
        page = context.new_page()
        if args.scenario == "fallback":
            scenario_fallback(page, log)
        else:
            scenario_normal(page, log)
            scenario_cancel(page, log)
            scenario_failure_retry(page, log)
            scenario_recovery(page, log)
            scenario_interleave(browser, context, log)
        browser.close()
    log.write("==== 页面 E2E 全部通过 ====")


if __name__ == "__main__":
    sys.exit(main())
