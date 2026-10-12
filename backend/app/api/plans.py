"""学习计划接口：生成候选计划、确认计划、调整计划。"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.service import plan_service, task_service

router = APIRouter(prefix="/api/plans", tags=["plan"])


class GenerateIn(BaseModel):
    course_id: int
    available_minutes: int | None = Field(None, ge=30, le=600, description="每天可用时间，缺省使用考试目标中的设置")
    start_date: str | None = Field(None, description="开始日期，缺省从今天开始")


class TaskIn(BaseModel):
    point_id: int | None = None
    title: str = Field(..., min_length=1, max_length=50)
    date: str
    minutes: int = Field(..., ge=10, le=300)


class ConfirmIn(BaseModel):
    course_id: int
    tasks: list[TaskIn]


class AdjustIn(BaseModel):
    course_id: int


@router.post("/generate")
def generate(payload: GenerateIn):
    return plan_service.generate_plan(payload.course_id, payload.available_minutes, payload.start_date)


@router.post("/confirm")
def confirm(payload: ConfirmIn):
    return plan_service.confirm_plan(payload.course_id, [t.model_dump() for t in payload.tasks])


@router.get("/current")
def current(course_id: int):
    return plan_service.current_plan(course_id)


@router.post("/adjust")
def adjust(payload: AdjustIn):
    return task_service.adjust_plan(payload.course_id)
