"""Запуск Dolce Pharm LMS одной командой.

    python run.py                 — запустить сервер (база, миграции, демоданные при первом запуске, HTTPS)
    python run.py seed            — загрузить демоданные, если база пустая
    python run.py reset-demo      — стереть данные контура и загрузить демоданные заново
    python run.py stage-account   — учётная запись для защиты: неиспользованная, без сброса остальных данных
    python run.py migrate         — применить миграции
    python run.py make-migration "описание"  — создать миграцию по изменениям моделей
    python run.py backup          — резервная копия базы и файлов
    python run.py openapi         — сохранить описание API в docs/openapi.json
    python run.py stop-db         — остановить встроенный PostgreSQL

Контур выбирается переменной LMS_ENV: demo (по умолчанию) или test (отдельная база, файлы и порты).
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from app import config  # noqa: E402
from app.services import pg_embedded  # noqa: E402

SERVERS_DIR = config.DATA_ROOT / "servers"


def _alembic_cfg():
    from alembic.config import Config
    cfg = Config(str(ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend" / "alembic").replace("%", "%%"))
    return cfg


def prepare_db() -> None:
    config.ensure_dirs()
    if config.EMBEDDED_PG:
        pg_embedded.start()
    from alembic import command
    command.upgrade(_alembic_cfg(), "head")


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _other_servers_alive() -> bool:
    if not SERVERS_DIR.exists():
        return False
    for f in SERVERS_DIR.glob("*.json"):
        if f.stem == config.ENV:
            continue
        try:
            if _port_open(int(json.loads(f.read_text())["http_port"])):
                return True
        except Exception:
            pass
    return False


def cmd_serve() -> None:
    import uvicorn

    from app.services import certs, netinfo

    print("Dolce Pharm LMS · контур «%s»" % config.ENV)
    if _port_open(config.HTTP_PORT):
        print("\nПорт %d уже занят — похоже, сервер уже запущен. Закройте его окно и повторите." % config.HTTP_PORT)
        sys.exit(1)
    if config.ENV == "demo":
        from app.services import hotspot
        note = hotspot.ensure_on()
        if note:
            print("  " + note)
    # База, уже работающая до запуска (её оставил прежний сервер или тестовый контур), остаётся работать.
    # Файл postmaster.pid, оставшийся после сбоя, сначала убирается — иначе такая база считалась бы запущенной.
    pg_started_here = False
    if config.EMBEDDED_PG:
        pg_embedded.clear_stale_pid()
        pg_started_here = not pg_embedded.is_running()
    print("  База данных…")
    prepare_db()
    from app import seed
    if seed.is_empty():
        print("  Первая загрузка демоданных…")
        seed.run()
    cert, key, names = certs.ensure_server_cert()
    if not (config.FRONTEND_DIST / "index.html").exists():
        print("\n  ВНИМАНИЕ: клиент не собран. Выполните setup.bat (или npm run build в папке frontend).")

    SERVERS_DIR.mkdir(parents=True, exist_ok=True)
    marker = SERVERS_DIR / ("%s.json" % config.ENV)
    marker.write_text(json.dumps({"pid": os.getpid(), "http_port": config.HTTP_PORT}))

    line = "─" * 66
    print("\n" + line)
    print("  Методист (этот ноутбук):   http://localhost:%d" % config.HTTP_PORT)
    urls = netinfo.lan_urls()
    if urls:
        for u in urls:
            print("  Телефон, первый раз:       %s" % u["setup_url"])
            print("  Телефон, платформа:        %s%s" % (u["app_url"], "   ← постоянный адрес (хот-спот)" if u["stable"] else ""))
        if not any(u["stable"] for u in urls):
            print("  Адрес выдан сетью Wi-Fi и может смениться. Постоянный — https://%s:%d/ —" % (netinfo.HOTSPOT_IP, config.HTTPS_PORT))
            print("  работает, когда на ноутбуке включён «Мобильный хот-спот».")
    else:
        print("  Сеть не найдена: подключите ноутбук к Wi-Fi или включите мобильный хот-спот.")
    print("  Описание API:              http://localhost:%d/api/docs" % config.HTTP_PORT)
    print("  Данные и журналы:          %s" % config.DATA_ROOT)
    print(line)
    print("  Остановка: Ctrl+C или закрыть это окно — база данных остановится штатно.\n")

    stopped = {"done": False}

    def shutdown_db() -> None:
        """Штатная остановка: по Ctrl+C, при закрытии окна, выходе из Windows и выключении."""
        if stopped["done"]:
            return
        stopped["done"] = True
        marker.unlink(missing_ok=True)
        if pg_started_here and not _other_servers_alive():
            print("Останавливаю базу данных…")
            pg_embedded.stop(timeout=4)

    _on_console_close(shutdown_db)

    common = dict(log_level="warning", access_log=False, timeout_keep_alive=30)
    http = uvicorn.Server(uvicorn.Config("app.main:app", host="0.0.0.0", port=config.HTTP_PORT, **common))
    https = uvicorn.Server(uvicorn.Config("app.main:app", host="0.0.0.0", port=config.HTTPS_PORT,
                                          ssl_certfile=str(cert), ssl_keyfile=str(key), **common))
    https.install_signal_handlers = lambda: None     # сигнал обрабатывает первый сервер и останавливает оба

    async def main() -> None:
        async def run_https() -> None:
            await https.serve()

        task = asyncio.create_task(run_https())
        try:
            await http.serve()
        finally:
            https.should_exit = True
            await task

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_db()
        print("Сервер остановлен.")


_console_handler = None      # ссылка держится, иначе сборщик мусора уберёт обработчик


def _on_console_close(callback) -> None:
    """Windows: при закрытии окна крестиком процессу дают ~5 секунд — успеваем остановить базу.

    Ctrl+C сюда не попадает (обработчик возвращает False, и Python получает KeyboardInterrupt как обычно).
    """
    global _console_handler
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    close_events = {2, 5, 6}           # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT

    def handler(event: int) -> bool:
        if event in close_events:
            try:
                callback()
            except Exception:
                pass
        return False

    _console_handler = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)(handler)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler, True)


def cmd_seed(reset: bool = False) -> None:
    if reset:
        config.ensure_dirs()
        if config.EMBEDDED_PG:
            pg_embedded.start()
            pg_embedded.drop_database()
        else:
            from app.db import Base, engine
            Base.metadata.drop_all(engine)
            with engine.begin() as c:
                c.exec_driver_sql("drop table if exists alembic_version")
    prepare_db()
    from app import seed
    if reset or seed.is_empty():
        seed.run()
        print("Демоданные загружены (контур «%s»)." % config.ENV)
    else:
        print("В базе уже есть данные. Для полной перезагрузки: python run.py reset-demo")


def cmd_backup() -> None:
    import shutil
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = config.BACKUP_DIR / stamp
    target.mkdir(parents=True, exist_ok=True)
    if config.EMBEDDED_PG:
        pg_embedded.start()
        pg_embedded.backup(target / "database.dump")
    else:
        print("Внешняя база: сделайте pg_dump средствами сервера БД.")
    shutil.make_archive(str(target / "files"), "zip", config.FILES_DIR)
    print("Резервная копия: %s" % target)


def cmd_openapi() -> None:
    from app.main import app
    out = ROOT / "docs" / "openapi.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=1), encoding="utf-8")
    print("Сохранено: %s" % out)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "serve":
        cmd_serve()
    elif cmd == "seed":
        cmd_seed()
    elif cmd == "reset-demo":
        cmd_seed(reset=True)
    elif cmd == "migrate":
        prepare_db()
        print("Миграции применены.")
    elif cmd == "make-migration":
        config.ensure_dirs()
        if config.EMBEDDED_PG:
            pg_embedded.start()
        from alembic import command
        command.upgrade(_alembic_cfg(), "head")
        command.revision(_alembic_cfg(), message=sys.argv[2] if len(sys.argv) > 2 else "изменения", autogenerate=True)
    elif cmd == "stage-account":
        prepare_db()
        from app import seed
        if seed.is_empty():
            seed.run()
        acc = seed.ensure_stage_account()
        print("Учётная запись для защиты: логин «%s», %s." % (acc["login"], acc["full_name"]))
        print("Пароль — в README.md, раздел «Демодоступы». Назначений и попыток у неё нет%s."
              % (" (создана сейчас: прежняя уже использована)" if acc["created"] else ""))
    elif cmd == "backup":
        cmd_backup()
    elif cmd == "openapi":
        cmd_openapi()
    elif cmd == "stop-db":
        pg_embedded.stop()
        print("PostgreSQL остановлен.")
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
