"""FastAPI 应用入口：注册路由、统一异常处理和数据库初始化。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import courses, goals, knowledge, plans, tasks
from app.errors import BusinessError
from app.logging_conf import log
from app.repository.db import DB_PATH, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log.info("StudyPilot 后端启动，数据库：%s", DB_PATH)
    yield
    log.info("StudyPilot 后端停止")


app = FastAPI(title="StudyPilot API", version="0.2.0", lifespan=lifespan)
app.include_router(courses.router)
app.include_router(goals.router)
app.include_router(knowledge.router)
app.include_router(plans.router)
app.include_router(tasks.router)


@app.exception_handler(BusinessError)
async def business_error_handler(request: Request, exc: BusinessError):
    """业务异常：返回统一结构，500 级别记录为错误日志。"""
    if exc.status_code >= 500:
        log.error("业务异常 %s %s -> %s（%s）", request.method, request.url.path, exc.message, exc.code)
    else:
        log.warning("业务异常 %s %s -> %s（%s）", request.method, request.url.path, exc.message, exc.code)
    return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": exc.message})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    log.warning("参数校验失败 %s %s -> %s", request.method, request.url.path, exc.errors()[:1])
    return JSONResponse(
        status_code=422,
        content={"code": "INVALID_PARAMETER", "message": "请求参数不完整或格式错误", "detail": str(exc.errors()[:1])},
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    log.exception("未处理异常 %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"code": "INTERNAL_ERROR", "message": "服务内部错误，请稍后重试"})


@app.get("/api/health")
def health():
    return {"status": "ok", "version": app.version}
