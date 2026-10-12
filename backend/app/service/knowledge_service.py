"""知识点业务层：校验名称与掌握度。"""

from app.errors import BusinessError
from app.logging_conf import log
from app.repository import course_repo, knowledge_repo


def list_points(course_id: int) -> list[dict]:
    return knowledge_repo.list_points(course_id)


def add_point(course_id: int, name: str, mastery: int) -> dict:
    if not course_repo.get_course(course_id):
        raise BusinessError("课程不存在，请先创建课程", 404, "COURSE_NOT_FOUND")
    name = (name or "").strip()
    if not name:
        raise BusinessError("知识点名称不能为空", 400, "EMPTY_POINT_NAME")
    if len(name) > 20:
        raise BusinessError("知识点名称不能超过20个字符", 400, "POINT_NAME_TOO_LONG")
    if not 0 <= mastery <= 100:
        raise BusinessError("掌握度应在 0—100 之间", 400, "INVALID_MASTERY")
    if knowledge_repo.find_by_name(course_id, name):
        raise BusinessError(f"知识点“{name}”已存在", 409, "POINT_DUPLICATED")

    point = knowledge_repo.insert_point(course_id, name, mastery)
    log.info("新增知识点：课程 %s，%s，掌握度 %s%%", course_id, name, mastery)
    return point


def update_mastery(course_id: int, point_id: int, mastery: int) -> dict:
    point = knowledge_repo.get_point(point_id)
    if not point or point["course_id"] != course_id:
        raise BusinessError("知识点不存在", 404, "POINT_NOT_FOUND")
    if not 0 <= mastery <= 100:
        raise BusinessError("掌握度应在 0—100 之间", 400, "INVALID_MASTERY")
    updated = knowledge_repo.update_mastery(point_id, mastery)
    log.info("手动调整掌握度：%s %s%% -> %s%%", point["name"], point["mastery"], mastery)
    return updated
