"""Настройки приложения. Всё задаётся переменными окружения LMS_*, значения по умолчанию
подобраны для запуска на одном ноутбуке без установки чего-либо в систему."""
from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
ASSETS_DIR = BACKEND_DIR / "app" / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
DEMO_DIR = BACKEND_DIR / "demo"
FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"

# Контур: demo — основной для защиты, test — отдельный для автотестов и повторной загрузки демоданных.
ENV = os.environ.get("LMS_ENV", "demo").strip().lower() or "demo"


def _default_data_root() -> Path:
    """Данные лежат вне папки проекта: путь без кириллицы (требование PostgreSQL на Windows)
    и вне OneDrive (синхронизатор не должен трогать файлы работающей базы)."""
    override = os.environ.get("LMS_DATA_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "DolcePharmLMS"
        if not str(root).isascii():
            root = Path("C:/DolcePharmLMS")
        return root
    return PROJECT_DIR / "data"


DATA_ROOT = _default_data_root()
ENV_DIR = DATA_ROOT / ENV                 # данные конкретного контура
FILES_DIR = ENV_DIR / "files"             # загруженные материалы, PDF-копии, сертификаты
LOG_DIR = ENV_DIR / "logs"
BACKUP_DIR = ENV_DIR / "backups"
CERT_DIR = DATA_ROOT / "certs"            # корневой сертификат общий для контуров
PG_BIN_DIR = DATA_ROOT / "pgsql"          # копия бинарников PostgreSQL в ASCII-пути
PG_DATA_DIR = DATA_ROOT / "pgdata"

PG_PORT = int(os.environ.get("LMS_PG_PORT", "54329"))
PG_USER = "lms"
DB_NAME = "lms" if ENV == "demo" else f"lms_{ENV}"
# Внешний PostgreSQL (например, на сервере предприятия): postgresql+psycopg://user:pass@host:5432/db
DATABASE_URL = os.environ.get("LMS_DATABASE_URL") or f"postgresql+psycopg://{PG_USER}@127.0.0.1:{PG_PORT}/{DB_NAME}"
EMBEDDED_PG = not os.environ.get("LMS_DATABASE_URL")

_default_https = 8443 if ENV == "demo" else 8543
_default_http = 8000 if ENV == "demo" else 8100
HTTPS_PORT = int(os.environ.get("LMS_HTTPS_PORT", str(_default_https)))
HTTP_PORT = int(os.environ.get("LMS_HTTP_PORT", str(_default_http)))

# Казахстан живёт в едином поясе UTC+5 без перехода на летнее время.
UTC_OFFSET_HOURS = float(os.environ.get("LMS_UTC_OFFSET_HOURS", "5"))
LOCAL_TZ = timezone(timedelta(hours=UTC_OFFSET_HOURS))
TZ_LABEL = "UTC%+g" % UTC_OFFSET_HOURS

TOKEN_TTL_DAYS = int(os.environ.get("LMS_TOKEN_TTL_DAYS", "30"))
MAX_UPLOAD_MB = int(os.environ.get("LMS_MAX_UPLOAD_MB", "300"))
LOGIN_MAX_FAILURES = 8
LOGIN_LOCK_MINUTES = 5

ORG_NAME = "Dolce-Pharm"
SAMPLE_NOTICE = "Учебный образец. Данные вымышлены."

# Значения по умолчанию для настроек, которые методист меняет в интерфейсе (таблица settings).
DEFAULT_SETTINGS = {
    "heartbeat_seconds": 15,        # шаг записи интервалов активности
    "idle_timeout_seconds": 120,    # пауза учёта времени при бездействии
    "reading_idle_seconds": 90,     # пауза учёта чтения материала без действий пользователя
    "local_retention_days": 14,     # через сколько дней после синхронизации предлагать очистку телефона
}


def ensure_dirs() -> None:
    for d in (DATA_ROOT, ENV_DIR, FILES_DIR, LOG_DIR, BACKUP_DIR, CERT_DIR):
        d.mkdir(parents=True, exist_ok=True)
