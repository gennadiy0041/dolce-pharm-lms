"""Встроенный PostgreSQL: работает из пакета pgserver без установки в систему.

Бинарники копируются в каталог данных (путь без кириллицы — иначе initdb на Windows падает),
кластер создаётся один раз, сервер слушает только 127.0.0.1. Для промышленного развёртывания
задаётся LMS_DATABASE_URL, и этот модуль не используется.

Жизненный цикл на Windows. PostgreSQL запускается в собственной скрытой консоли, в отдельной
группе процессов и (если система позволяет) вне задания (job) окна start.bat. Раньше он наследовал
консоль окна: закрытие окна посылало сигнал всем процессам этой консоли и останавливало базу,
а принудительное завершение окна обрывало запуск новых процессов базы (ошибка 0xC0000142 в журнале
postgres.log) — база останавливалась сама. Теперь окно сервера и база независимы: базу штатно
останавливает сам run.py — по Ctrl+C или при закрытии окна (см. run.py), а при повторном запуске
уже работающая база используется как есть.

После аварийного завершения (выключили питание, процесс убили) в каталоге остаётся postmaster.pid.
Если номер процесса из него уже занят другой программой, pg_ctl считал бы, что база работает,
и не запускал её. Такой файл распознаётся и убирается; при запуске PostgreSQL сам восстанавливает
данные по журналу (WAL).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from .. import config

_QUIET = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def _exe(name: str) -> str:
    return str(config.PG_BIN_DIR / "bin" / (name + (".exe" if WINDOWS else "")))


def ensure_binaries() -> None:
    marker = config.PG_BIN_DIR / "bin" / ("postgres.exe" if WINDOWS else "postgres")
    if marker.exists():
        return
    import pgserver

    src = Path(pgserver.__file__).parent / "pginstall"
    if not src.exists():
        raise RuntimeError("В пакете pgserver нет бинарников PostgreSQL. Установите зависимости: setup.bat")
    print("  Копирую PostgreSQL в %s (один раз)…" % config.PG_BIN_DIR)
    if config.PG_BIN_DIR.exists():
        shutil.rmtree(config.PG_BIN_DIR)
    shutil.copytree(src, config.PG_BIN_DIR)


def run_detached(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Запуск служебной программы PostgreSQL вне консоли и задания окна сервера.

    Процессы базы, порождённые таким вызовом, получают собственную скрытую консоль: закрытие окна
    start.bat их не затрагивает, и окна консоли не мелькают.
    """
    if not WINDOWS:
        return subprocess.run(args, timeout=timeout, start_new_session=True, **_QUIET)
    flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        return subprocess.run(args, timeout=timeout, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **_QUIET)
    except OSError:
        # задание окна не разрешает выход из него — остаёмся внутри, но со своей консолью
        return subprocess.run(args, timeout=timeout, creationflags=flags, **_QUIET)


def _pid_file(data_dir: Path) -> Path:
    return data_dir / "postmaster.pid"


def _pid_alive_postgres(pid: int) -> bool:
    if not WINDOWS:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        try:
            return "postgres" in Path("/proc/%d/comm" % pid).read_text()
        except OSError:
            return True
    out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"], capture_output=True,
                         text=True, errors="replace", creationflags=CREATE_NO_WINDOW, timeout=15)
    return "postgres" in out.stdout.lower()


def clear_stale_pid(data_dir: Optional[Path] = None) -> bool:
    """Убирает postmaster.pid, оставшийся после аварийного завершения. True — файл был лишним."""
    pid_file = _pid_file(data_dir or config.PG_DATA_DIR)
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip())
    except (OSError, ValueError, IndexError):
        pid = 0
    if pid and _pid_alive_postgres(pid):
        return False
    try:
        pid_file.unlink()
    except OSError:
        return False
    return True


def is_running(data_dir: Optional[Path] = None) -> bool:
    data_dir = data_dir or config.PG_DATA_DIR
    if not _pid_file(data_dir).exists():
        return False
    r = subprocess.run([_exe("pg_ctl"), "-D", str(data_dir), "status"],
                       creationflags=CREATE_NO_WINDOW if WINDOWS else 0, **_QUIET)
    return r.returncode == 0


def _connect(dbname: str, port: Optional[int] = None):
    import psycopg
    return psycopg.connect("postgresql://%s@127.0.0.1:%d/%s" % (config.PG_USER, port or config.PG_PORT, dbname),
                           connect_timeout=5, autocommit=True)


def start_cluster(data_dir: Path, port: int, log: Path) -> None:
    """Запускает кластер (если ещё не запущен) и ждёт, пока он примет подключения."""
    if clear_stale_pid(data_dir):
        print("  Убран файл postmaster.pid, оставшийся после аварийного завершения базы.")
    if not is_running(data_dir):
        # вывод pg_ctl нельзя читать через канал: postgres наследует его и вызов зависает
        r = run_detached([_exe("pg_ctl"), "-D", str(data_dir), "-l", str(log),
                          "-o", "-p %d -c listen_addresses=127.0.0.1" % port, "-w", "-t", "120", "start"], timeout=180)
        if r.returncode != 0:
            tail = log.read_text(encoding="utf-8", errors="replace")[-1500:] if log.exists() else ""
            raise RuntimeError("PostgreSQL не запустился (порт %d занят?).\n%s" % (port, tail))


def start() -> None:
    """Запускает сервер (если ещё не запущен) и создаёт базу контура."""
    config.ensure_dirs()
    ensure_binaries()
    if not (config.PG_DATA_DIR / "PG_VERSION").exists():
        print("  Создаю кластер базы данных (один раз, ~15 секунд)…")
        r = subprocess.run([_exe("initdb"), "-D", str(config.PG_DATA_DIR), "-U", config.PG_USER, "-A", "trust",
                            "-E", "UTF8", "--locale=C"], capture_output=True, text=True, timeout=300,
                           creationflags=CREATE_NO_WINDOW if WINDOWS else 0)
        if r.returncode != 0:
            raise RuntimeError("initdb завершился с ошибкой:\n" + (r.stderr or r.stdout)[-1500:])
    start_cluster(config.PG_DATA_DIR, config.PG_PORT, config.DATA_ROOT / "postgres.log")
    last = None
    for _ in range(60):
        try:
            with _connect("postgres") as c:
                exists = c.execute("select 1 from pg_database where datname = %s", (config.DB_NAME,)).fetchone()
                if not exists:
                    c.execute('create database "%s" encoding \'UTF8\' template template0' % config.DB_NAME)
            return
        except Exception as exc:  # сервер ещё поднимается или восстанавливается после сбоя
            last = exc
            time.sleep(0.5)
    raise RuntimeError("Не удалось подключиться к PostgreSQL: %s. Журнал базы: %s"
                       % (last, config.DATA_ROOT / "postgres.log"))


def stop(data_dir: Optional[Path] = None, timeout: int = 30) -> None:
    data_dir = data_dir or config.PG_DATA_DIR
    if _pid_file(data_dir).exists():
        subprocess.run([_exe("pg_ctl"), "-D", str(data_dir), "-m", "fast", "-w", "-t", str(timeout), "stop"],
                       timeout=timeout + 30, creationflags=CREATE_NO_WINDOW if WINDOWS else 0, **_QUIET)


def drop_database() -> None:
    """Полная очистка базы контура (для повторной загрузки демоданных)."""
    with _connect("postgres") as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname = %s and pid <> pg_backend_pid()",
                  (config.DB_NAME,))
        c.execute('drop database if exists "%s"' % config.DB_NAME)
        c.execute('create database "%s" encoding \'UTF8\' template template0' % config.DB_NAME)


def backup(target: Path) -> Path:
    """Резервная копия базы контура средствами pg_dump."""
    target.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([_exe("pg_dump"), "-h", "127.0.0.1", "-p", str(config.PG_PORT), "-U", config.PG_USER,
                        "-F", "c", "-f", str(target), config.DB_NAME], capture_output=True, text=True, timeout=600,
                       creationflags=CREATE_NO_WINDOW if WINDOWS else 0)
    if r.returncode != 0:
        raise RuntimeError("pg_dump: " + r.stderr[-800:])
    return target
