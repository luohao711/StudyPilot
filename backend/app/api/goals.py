"""考试目标接口。"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.service import goal_service

router = APIRouter(prefix="/api/courses", tags=["goal"])


class GoalIn(BaseModel):
    exam_date: str = Field(..., description="考试日期，格式 YYYY-MM-DD")
    target_score: int = Field(85, ge=1, le=100, description="目标分数")
    available_minutes: int = Field(120, ge=30, le=600, description="每天可用学习时间（分钟）")


@router.get("/{course_id}/goal")
def get_goal(course_id: int):
    return {"goal": goal_service.get_goal(course_id)}


@router.post("/{course_id}/goal")
def save_goal(course_id: int, payload: GoalIn):
    return goal_service.save_goal(course_id, payload.exam_date, payload.target_score, payload.available_minutes)
