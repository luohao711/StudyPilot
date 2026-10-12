"""课程接口：输入校验由 Pydantic 完成，业务规则由业务层完成。"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.service import course_service

router = APIRouter(prefix="/api", tags=["course"])


class CourseIn(BaseModel):
    name: str = Field(..., max_length=30, description="课程名称")
    term: str = Field("", max_length=20, description="学期")


@router.get("/courses")
def list_courses():
    return {"items": course_service.list_courses()}


@router.post("/courses", status_code=201)
def create_course(payload: CourseIn):
    return course_service.create_course(payload.name, payload.term)
