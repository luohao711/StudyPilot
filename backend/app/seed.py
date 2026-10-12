"""样例数据脚本：初始化、清空和恢复可重复使用的演示数据。

用法（在 backend 目录下执行）：
    python3 -m app.seed            写入样例数据（已有数据时跳过）
    python3 -m app.seed --reset    清空后重新写入样例数据
    python3 -m app.seed --clear    只清空业务数据
"""

import argparse
import datetime
import json
import os

from app.logging_conf import log
from app.repository import db as db_module
from app.repository import course_repo, goal_repo, knowledge_repo
from app.service import goal_service

SEED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seed_data.json")


def load_seed() -> dict:
    with open(SEED_FILE, encoding="utf-8") as fp:
        return json.load(fp)


def write_sample() -> None:
    data = load_seed()
    for course in data["courses"]:
        if course_repo.find_by_name(course["name"]):
            log.info("样例课程已存在，跳过：%s", course["name"])
            continue
        created = course_repo.insert_course(course["name"], course["term"])
        goal = course["goal"]
        exam_date = (datetime.date.today() + datetime.timedelta(days=goal["exam_date_offset_days"])).isoformat()
        goal_service.save_goal(created["course_id"], exam_date, goal["target_score"], goal["available_minutes"])
        for point in course["points"]:
            knowledge_repo.insert_point(created["course_id"], point["name"], point["mastery"])
        log.info("写入样例课程：%s（%d 个知识点）", course["name"], len(course["points"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="StudyPilot 样例数据管理")
    parser.add_argument("--reset", action="store_true", help="清空后重新写入样例数据")
    parser.add_argument("--clear", action="store_true", help="只清空业务数据")
    args = parser.parse_args()

    db_module.init_db()
    log.info("数据库位置：%s", db_module.DB_PATH)

    if args.clear or args.reset:
        db_module.clear_all()
        db_module.init_db()  # 清空后补回默认用户
    if not args.clear:
        write_sample()


if __name__ == "__main__":
    main()
