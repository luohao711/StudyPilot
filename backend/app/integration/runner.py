"""后台执行器：用固定大小的线程池执行 AI 任务，接口只负责创建和查询。

没有引入消息队列：本项目是单机单用户软件，线程池加 SQLite 任务表已经能满足
“提交后立即返回、页面轮询、刷新可恢复”的集成要求；服务重启后由启动逻辑把
未结束任务标记为中断，不需要额外的中间件。
"""

import threading
from concurrent.futures import ThreadPoolExecutor

from app.integration import config

_pool: ThreadPoolExecutor | None = None
_lock = threading.Lock()


def start() -> None:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=config.JOB_WORKERS, thread_name_prefix="ai-job")


def submit(fn, *args) -> None:
    """提交任务；执行器随应用启动创建，未启动时（如单测直接调用）按需创建。"""
    start()
    assert _pool is not None
    _pool.submit(fn, *args)


def shutdown(wait: bool = False) -> None:
    global _pool
    with _lock:
        if _pool is not None:
            _pool.shutdown(wait=wait, cancel_futures=True)
            _pool = None
