"""Проверка жизненного цикла встроенного PostgreSQL на Windows — на копии данных.

Рабочая база не останавливается и не изменяется: с неё снимается штатная копия (pg_basebackup),
и все опыты идут на копии, запущенной на отдельном порту.

    python tools/pg_lifecycle_check.py            — все сценарии, отчёт в e2e/results/pg-lifecycle.json

Сценарии:
  copy        копия живой базы запускается и отдаёт те же данные;
  crash       процессы копии принудительно завершаются (как при отключении питания), повторный запуск
              восстанавливает базу по журналу, данные на месте;
  stale-pid   в postmaster.pid остался номер процесса, который теперь занят другой программой:
              pg_ctl считает базу запущенной; новый запуск распознаёт это и базу поднимает;
  window-old  база запускается из отдельного окна консоли прежним способом, окно закрывается крестиком;
  window-new  то же новым способом (своя скрытая консоль) — база должна продолжить работу.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("LMS_ENV", "demo")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import config  # noqa: E402
from app.services import pg_embedded as pg  # noqa: E402

PORT = 54398
WORK = config.DATA_ROOT / "lifecycle-check"
COPY = WORK / "pgdata-copy"
LOG = WORK / "postgres-copy.log"


def connect(timeout: int = 5):
    import psycopg
    return psycopg.connect("postgresql://%s@127.0.0.1:%d/%s" % (config.PG_USER, PORT, "lms"),
                           connect_timeout=timeout, autocommit=True)


def snapshot() -> dict:
    with connect() as c:
        return {
            "users": c.execute("select count(*) from users").fetchone()[0],
            "attempts": c.execute("select count(*) from attempts").fetchone()[0],
            "intervals": c.execute("select count(*) from time_intervals").fetchone()[0],
            "zashita_assignments": c.execute(
                "select count(*) from assignments a join users u on u.id = a.user_id where u.login = 'zashita'").fetchone()[0],
        }


def accepts_new_connections(tries: int = 3) -> bool:
    """Каждое подключение — новый процесс базы: именно его запуск ломался после закрытия окна."""
    for _ in range(tries):
        try:
            with connect(timeout=3) as c:
                c.execute("select 1")
            return True
        except Exception:
            time.sleep(1)
    return False


def postmaster_pid() -> int:
    return int((COPY / "postmaster.pid").read_text().splitlines()[0])


def pg_ctl_status() -> int:
    return subprocess.run([pg._exe("pg_ctl"), "-D", str(COPY), "status"], capture_output=True,
                          creationflags=pg.CREATE_NO_WINDOW).returncode


def stop_copy() -> None:
    if (COPY / "postmaster.pid").exists():
        subprocess.run([pg._exe("pg_ctl"), "-D", str(COPY), "-m", "fast", "-w", "-t", "30", "stop"],
                       capture_output=True, creationflags=pg.CREATE_NO_WINDOW, timeout=60)


def wait_port(open_: bool, seconds: int = 60) -> bool:
    import socket
    end = time.time() + seconds
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
                ok = True
        except OSError:
            ok = False
        if ok == open_:
            return True
        time.sleep(0.5)
    return False


def hold(method: str) -> None:
    """Запуск копии из отдельного окна консоли и закрытие этого окна крестиком (WM_CLOSE)."""
    import ctypes
    args = [pg._exe("pg_ctl"), "-D", str(COPY), "-l", str(LOG), "-o", "-p %d -c listen_addresses=127.0.0.1" % PORT,
            "-w", "-t", "120", "start"]
    if method == "old":
        subprocess.run(args, timeout=180, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        pg.run_detached(args, timeout=180)
    (WORK / ("held-%s.flag" % method)).write_text("started")
    time.sleep(2)
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)      # WM_CLOSE — как нажать крестик окна
    time.sleep(30)


def window_case(method: str) -> dict:
    stop_copy()
    flag = WORK / ("held-%s.flag" % method)
    flag.unlink(missing_ok=True)
    # conhost.exe — классическое окно консоли, у которого есть крестик (как у окна start.bat)
    proc = subprocess.Popen(["conhost.exe", sys.executable, str(Path(__file__)), "hold", method],
                            cwd=str(ROOT), creationflags=0x00000010)    # CREATE_NEW_CONSOLE
    end = time.time() + 90
    while time.time() < end and not flag.exists():
        time.sleep(0.5)
    started = flag.exists()
    time.sleep(8)                                                       # окно закрыто, система разослала сигналы
    window_alive = proc.poll() is None
    alive = accepts_new_connections()
    log_tail = LOG.read_text(encoding="utf-8", errors="replace")[-600:] if LOG.exists() else ""
    stop_copy()
    if (COPY / "postmaster.pid").exists():
        pg.clear_stale_pid(COPY)
    return {"started": started, "window_still_open": window_alive, "db_accepts_new_connections_after_close": alive,
            "log_tail": log_tail.strip().splitlines()[-3:]}


def main() -> None:
    if len(sys.argv) > 2 and sys.argv[1] == "hold":
        hold(sys.argv[2])
        return
    report: dict = {"at": datetime.now().isoformat(timespec="seconds"), "port": PORT}
    stop_copy() if COPY.exists() else None
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)

    print("1. Копия живой базы (pg_basebackup)…")
    r = subprocess.run([pg._exe("pg_basebackup"), "-h", "127.0.0.1", "-p", str(config.PG_PORT), "-U", config.PG_USER,
                        "-D", str(COPY), "-Fp", "-Xs", "-c", "fast"], capture_output=True, text=True,
                       creationflags=pg.CREATE_NO_WINDOW, timeout=600)
    if r.returncode != 0:
        print(r.stderr)
        sys.exit(1)
    pg.start_cluster(COPY, PORT, LOG)
    base = snapshot()
    report["copy"] = {"ok": True, "data": base}
    print("   данные копии:", base)

    print("2. Аварийное завершение и восстановление…")
    pid = postmaster_pid()
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, creationflags=pg.CREATE_NO_WINDOW)
    wait_port(False, 20)
    stale_left = (COPY / "postmaster.pid").exists()
    t0 = time.time()
    pg.start_cluster(COPY, PORT, LOG)
    ok = accepts_new_connections()
    after = snapshot() if ok else {}
    log = LOG.read_text(encoding="utf-8", errors="replace")
    report["crash"] = {
        "pid_file_left_after_kill": stale_left, "restarted": ok, "seconds_to_restart": round(time.time() - t0, 1),
        "data_same": after == base, "recovery_in_log": "redo" in log or "not properly shut down" in log,
    }
    print("  ", report["crash"])

    print("3. postmaster.pid с номером чужого процесса…")
    stop_copy()
    pid_lines = ["%d" % os.getpid(), str(COPY), "0", str(PORT), "", "127.0.0.1", "0 0", "ready"]
    (COPY / "postmaster.pid").write_text("\n".join(pid_lines) + "\n")
    status_before = pg_ctl_status()        # 0 — pg_ctl думает, что база работает
    pg.start_cluster(COPY, PORT, LOG)
    report["stale_pid"] = {"pg_ctl_status_with_foreign_pid": "считает запущенной" if status_before == 0 else "не запущена",
                           "started_after_fix": accepts_new_connections()}
    print("  ", report["stale_pid"])
    stop_copy()

    print("4. Закрытие окна: прежний способ запуска…")
    report["window_old"] = window_case("old")
    print("  ", {k: v for k, v in report["window_old"].items() if k != "log_tail"})
    print("5. Закрытие окна: новый способ запуска…")
    report["window_new"] = window_case("new")
    print("  ", {k: v for k, v in report["window_new"].items() if k != "log_tail"})

    stop_copy()
    out = ROOT / "e2e" / "results" / "pg-lifecycle.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.rmtree(WORK, ignore_errors=True)
    print("Отчёт:", out)


if __name__ == "__main__":
    main()
