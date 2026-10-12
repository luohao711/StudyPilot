"""知识点与掌握度接口。"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.service import knowledge_service

router = APIRouter(prefix="/api/courses", tags=["knowledge"])


class PointIn(BaseModel):
    name: str = Field(..., max_length=20, description="知识点名称")
    mastery: int = Field(0, ge=0, le=100, description="当前掌握度")


class MasteryIn(BaseModel):
    mastery: int = Field(..., ge=0, le=100)


@router.get("/{course_id}/points")
def list_points(course_id: int):
    return {"items": knowledge_service.list_points(course_id)}


@router.post("/{course_id}/points", status_code=201)
def add_point(course_id: int, payload: PointIn):
    return knowledge_service.add_point(course_id, payload.name, payload.mastery)


@router.put("/{course_id}/points/{point_id}")
def update_mastery(course_id: int, point_id: int, payload: MasteryIn):
    return knowledge_service.update_mastery(course_id, point_id, payload.mastery)
