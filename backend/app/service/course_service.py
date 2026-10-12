"""课程业务层：负责字段校验和重复检查。"""

from app.errors import BusinessError
from app.logging_conf import log
from app.repository import course_repo


def create_course(name: str, term: str) -> dict:
    name = (name or "").strip()
    term = (term or "").strip()
    if not name:
        raise BusinessError("课程名称不能为空", 400, "EMPTY_COURSE_NAME")
    if len(name) > 30:
        raise BusinessError("课程名称不能超过30个字符", 400, "COURSE_NAME_TOO_LONG")
    if course_repo.find_by_name(name):
        raise BusinessError(f"课程“{name}”已存在", 409, "COURSE_DUPLICATED")
    course = course_repo.insert_course(name, term)
    log.info("创建课程成功：%s（id=%s）", name, course["course_id"])
    return course


def list_courses() -> list[dict]:
    return course_repo.list_courses()
