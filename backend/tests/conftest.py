"""Общие фикстуры тестов API. Тесты всегда работают в отдельном контуре «test»:
своя база lms_test и свои файлы — демонстрационные данные не затрагиваются."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["LMS_ENV"] = "test"          # до импорта app.config
# тесты конвертера одинаковы на машинах с Microsoft Office и без него (Office проверяется отдельно)
os.environ.setdefault("LMS_MSOFFICE", "off")
BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """Чистая база с демоданными и HTTP-клиент приложения."""
    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient

    from app import config, seed
    from app.services import pg_embedded

    assert config.ENV == "test"
    config.ensure_dirs()
    if config.EMBEDDED_PG:
        pg_embedded.start()
        pg_embedded.drop_database()
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic").replace("%", "%%"))
    command.upgrade(cfg, "head")
    seed.run()
    from app.main import app
    with TestClient(app) as c:
        yield c


def _login(client, login: str, password: str, kind: str = "pc") -> dict:
    r = client.post("/api/auth/login", json={"login": login, "password": password, "device_id": "test-" + login, "device_kind": kind})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


@pytest.fixture(scope="session")
def metodist(client) -> dict:
    return _login(client, "metodist", "metod2026")


@pytest.fixture(scope="session")
def operator(client) -> dict:
    return _login(client, "operator", "smena2026", "phone")


@pytest.fixture(scope="session")
def master(client) -> dict:
    return _login(client, "master", "master2026")


@pytest.fixture(scope="session")
def login(client):
    return lambda name, password="demo2026", kind="pc": _login(client, name, password, kind)
