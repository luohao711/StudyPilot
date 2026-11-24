"""AI 计划任务接口：创建、查询、取消、重试和保存结果。

界面不再直接等待一次模型调用：POST 创建任务后立即返回 job_id，页面轮询状态；
任务编号、输入快照、结果、错误和保存计划都落库，刷新后可以继续查看。
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.plans import TaskIn
from app.service import job_service

router = APIRouter(prefix="/api/plans/ai-jobs", tags=["plan-job"])


class CreateJobIn(BaseModel):
    course_id: int = Field(..., ge=1)
    available_minutes: int | None = Field(None, ge=30, le=600, description="每天可用时间，缺省使用考试目标中的设置")
    start_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="开始日期，缺省从今天开始")
    idempotency_key: str | None = Field(None, max_length=64, description="同一键重复提交返回同一个任务")
    simulate: str | None = Field(None, max_length=10, description="仅本地验收：timeout/error/slow")


class ConfirmJobIn(BaseModel):
    tasks: list[TaskIn] | None = Field(None, description="用户在页面上编辑后的任务；缺省使用 AI 原始结果")


@router.post("", status_code=202)
def create_job(payload: CreateJobIn):
    return job_service.create_job(
        payload.course_id,
        available_minutes=payload.available_minutes,
        start_date=payload.start_date,
        idempotency_key=payload.idempotency_key,
        simulate=payload.simulate,
    )


@router.get("")
def list_jobs(course_id: int | None = None, limit: int = 5):
    return job_service.list_jobs(course_id, limit)


@router.get("/{job_id}")
def get_job(job_id: str):
    return job_service.get_job(job_id)


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str):
    return job_service.cancel_job(job_id)


@router.post("/{job_id}/retry")
def retry_job(job_id: str):
    return job_service.retry_job(job_id)


@router.post("/{job_id}/confirm")
def confirm_job(job_id: str, payload: ConfirmJobIn):
    tasks = [task.model_dump() for task in payload.tasks] if payload.tasks is not None else None
    return job_service.confirm_job(job_id, tasks)
