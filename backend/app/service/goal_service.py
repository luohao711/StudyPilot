"""考试目标业务层：校验课程、日期、分数和每日可用时间。"""

from datetime import date

from app.errors import BusinessError
from app.logging_conf import log
from app.repository import course_repo, goal_repo


def get_goal(course_id: int) -> dict | None:
    return goal_repo.get_goal(course_id)


def save_goal(course_id: int, exam_date: str, target_score: int, available_minutes: int) -> dict:
    if not course_repo.get_course(course_id):
        raise BusinessError("课程不存在，请先创建课程", 404, "COURSE_NOT_FOUND")
    try:
        exam = date.fromisoformat(exam_date)
    except (TypeError, ValueError):
        raise BusinessError("考试日期格式应为 YYYY-MM-DD", 400, "INVALID_DATE_FORMAT") from None
    if exam <= date.today():
        raise BusinessError("考试日期必须晚于今天，请重新填写", 400, "INVALID_EXAM_DATE")
    if not 0 < target_score <= 100:
        raise BusinessError("目标分数应在 1—100 之间", 400, "INVALID_TARGET_SCORE")
    if not 30 <= available_minutes <= 600:
        raise BusinessError("每天可用时间应在 30—600 分钟之间", 400, "INVALID_AVAILABLE_MINUTES")

    goal = goal_repo.save_goal(course_id, exam.isoformat(), target_score, available_minutes)
    log.info("保存考试目标：课程 %s，考试 %s，目标 %s 分", course_id, exam.isoformat(), target_score)
    return goal
