"""测试夹具：每个用例使用独立的临时 SQLite 数据库。"""

import os
import pathlib
import sys

import pytest

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    os.environ["STUDYPILOT_DB"] = str(db_file)

    from app.repository import db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", str(db_file))

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def course(client):
    """创建一门课程，返回课程编号。"""
    resp = client.post("/api/courses", json={"name": "软件工程", "term": "2026春"})
    assert resp.status_code == 201
    return resp.json()["course_id"]
