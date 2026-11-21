"""Замер автодоставки на физическом телефоне.

Скрипт работает на ноутбуке рядом с сервером и записывает два независимых ряда событий:

  1. когда телефон пропал из сети и когда снова в ней появился (ping по адресу телефона);
  2. когда сервер принял отправку очереди и какую попытку она принесла (читается из базы).

Задержка считается как «сервер принял запись» минус «телефон снова отвечает в сети». Момент возврата
связи берётся снаружи, а не из самого приложения, поэтому замер годится и для закрытого приложения.
Погрешность: шаг опроса сети (0,5 с) плюс время, за которое телефон после включения Wi-Fi получает адрес.

    python tools/phone_watch.py --phone-ip 192.168.137.25 --label run1

Останавливается по Ctrl+C. Журнал: e2e/results/phone/<label>.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("LMS_ENV", "demo")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def ping(ip: str) -> bool:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run(["ping", "-n", "1", "-w", "700", ip], capture_output=True, text=True, timeout=4,
                             creationflags=flags, errors="replace").stdout
    except (subprocess.TimeoutExpired, OSError):
        return False
    return "TTL=" in out.upper()


def ip_by_mac(mac: str) -> str | None:
    """Адрес телефона по его MAC из таблицы соседей ноутбука: после переподключения адрес мог смениться."""
    want = mac.lower().replace(":", "-")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=5, creationflags=flags, errors="replace").stdout
    except (subprocess.TimeoutExpired, OSError):
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].lower() == want:
            return parts[0]
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Замер автодоставки: сеть телефона и приём записей сервером")
    ap.add_argument("--phone-ip", required=True, help="адрес телефона в локальной сети")
    ap.add_argument("--phone-mac", default="", help="MAC телефона в этой сети — чтобы найти его, если адрес сменится")
    ap.add_argument("--label", default="run", help="имя прогона (имя файла журнала)")
    ap.add_argument("--seconds", type=int, default=0, help="остановиться через столько секунд (0 — до Ctrl+C)")
    args = ap.parse_args()

    from app.db import SessionLocal
    from app.models import Attempt, AttemptAnswer, Question, SyncBatch, User

    out_dir = ROOT / "e2e" / "results" / "phone"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ("%s.jsonl" % args.label)

    def emit(kind: str, **data) -> None:
        row = {"at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "kind": kind, **data}
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        local = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print("%s  %-14s %s" % (local, kind, json.dumps(data, ensure_ascii=False)), flush=True)

    started = datetime.now(timezone.utc)
    seen_batches: set[str] = set()
    seen_attempts: dict[str, str] = {}
    with SessionLocal() as db:
        seen_batches = {x for (x,) in db.query(SyncBatch.id).all()}
        seen_attempts = {a.id: a.status for a in db.query(Attempt).all()}

    reachable: bool | None = None
    back_at: datetime | None = None           # когда телефон последний раз вернулся в сеть
    lost_at: datetime | None = None
    emit("start", phone_ip=args.phone_ip, label=args.label, known_batches=len(seen_batches), known_attempts=len(seen_attempts))

    misses = 0
    first_miss_at: datetime | None = None
    try:
        while True:
            now = datetime.now(timezone.utc)
            ok = ping(args.phone_ip)
            if not ok and args.phone_mac and reachable is False:
                found = ip_by_mac(args.phone_mac)
                if found and found != args.phone_ip:
                    emit("phone-ip-changed", old=args.phone_ip, new=found)
                    args.phone_ip = found
                    ok = ping(args.phone_ip)
            # Одиночный потерянный ответ по Wi-Fi — обычное дело (телефон экономит заряд).
            # «Пропал из сети» считаем после трёх подряд неудач; «вернулся» — по первому же ответу.
            if ok:
                misses = 0
                if reachable is not True:
                    if reachable is False:
                        back_at = now
                        emit("phone-online", offline_seconds=round((now - lost_at).total_seconds(), 1) if lost_at else None)
                    reachable = True
            else:
                if misses == 0:
                    first_miss_at = now
                misses += 1
                if misses >= 3 and reachable is not False:
                    lost_at = first_miss_at
                    emit("phone-offline", since=first_miss_at.isoformat(timespec="milliseconds") if first_miss_at else None)
                    reachable = False

            with SessionLocal() as db:
                for b in db.query(SyncBatch).filter(SyncBatch.received_at >= started).order_by(SyncBatch.received_at).all():
                    if b.id in seen_batches:
                        continue
                    seen_batches.add(b.id)
                    user = db.get(User, b.user_id)
                    since_back = round((b.received_at - back_at).total_seconds(), 1) if back_at and lost_at else None
                    emit("server-batch", user=user.login if user else "?", trigger=b.trigger, app_state=b.app_state,
                         source=b.source, events=b.events_total, accepted=b.events_accepted, duplicate=b.events_duplicate,
                         rejected=b.events_rejected, app_reported_delay=b.delay_seconds,
                         seconds_after_phone_online=since_back,
                         received_at=b.received_at.isoformat(timespec="milliseconds"))
                for a in db.query(Attempt).order_by(Attempt.received_at).all():
                    if seen_attempts.get(a.id) == a.status:
                        continue
                    first = a.id not in seen_attempts
                    seen_attempts[a.id] = a.status
                    user = db.get(User, a.user_id)
                    open_answers = [str(x.answer) for x in db.query(AttemptAnswer).join(Question, Question.id == AttemptAnswer.question_id)
                                    .filter(AttemptAnswer.attempt_id == a.id, Question.type == "open").all()]
                    since_back = round((a.received_at - back_at).total_seconds(), 1) if first and back_at and lost_at else None
                    emit("attempt" if first else "attempt-status", user=user.login if user else "?",
                         revision=a.version.revision, sop=a.version.sop_code, status=a.status, attempt_no=a.attempt_no,
                         percent=a.percent, source=a.source, open_answers=open_answers,
                         seconds_after_phone_online=since_back,
                         received_at=a.received_at.isoformat(timespec="milliseconds"))

            if args.seconds and (datetime.now(timezone.utc) - started).total_seconds() >= args.seconds:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    emit("stop")


if __name__ == "__main__":
    main()
