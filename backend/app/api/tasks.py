"""每日任务接口：任务列表、掌握度建议和完成提交。"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.service import task_service

router = APIRouter(prefix="/api/tasks", tags=["task"])


class CompleteIn(BaseModel):
    mastery: int = Field(..., ge=0, le=100, description="用户确认后的掌握度")
    note: str = Field("", max_length=100, description="完成情况备注")


@router.get("")
def list_tasks(course_id: int | None = None, status: str | None = None):
    return {"items": task_service.list_tasks(course_id, status)}


@router.get("/{task_id}/suggestion")
def suggestion(task_id: int):
    return task_service.suggest_mastery(task_id)


@router.post("/{task_id}/complete")
def complete(task_id: int, payload: CompleteIn):
    return task_service.complete_task(task_id, payload.mastery, payload.note)
