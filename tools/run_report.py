"""Сводка прогона на физическом телефоне — то, что видит методист, одним списком.

Берёт данные через тот же REST API, что и кабинет методиста: строки отчёта по попыткам,
время изучения по материалам, протокол (JSON и PDF). PDF сохраняется рядом с журналом прогона.

    python tools/run_report.py --login operator --label run1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("LMS_ENV", "demo")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", default="operator", help="логин сотрудника, чей прогон проверяется")
    ap.add_argument("--label", default="run", help="имя прогона — для имени файла протокола")
    args = ap.parse_args()

    from app import config, seed

    base = "http://localhost:%d" % config.HTTP_PORT
    name, password = next((a[0], a[1]) for a in seed.DEMO_ACCOUNTS if a[0] == "metodist")   # демодоступ из seed.py

    def call(path: str, body: dict | None = None, token: str = "", raw: bool = False):
        req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                     data=json.dumps(body).encode() if body is not None else None)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        return data if raw else json.loads(data)

    token = call("/api/auth/login", {"login": name, "password": password, "device_id": "run-report", "device_kind": "pc"})["token"]
    users = call("/api/users", token=token)
    user = next(u for u in users if u["login"] == args.login)

    rows = call("/api/reports/attempts?" + urllib.parse.urlencode({"user_id": user["id"]}), token=token)["rows"]
    keys = ("full_name", "course_title", "sop_code", "revision", "attempt_no", "status_label", "score", "max_score", "percent",
            "pass_percent", "material_seconds", "platform_seconds", "login_at", "logout_at", "submitted_at", "received_at",
            "source_label", "sync_trigger", "sync_trigger_label", "sync_app_state_label", "delay_seconds", "certificate_number")
    print("=== попытки сотрудника (%d) ===" % len(rows))
    for r in rows:
        print(json.dumps({k: r.get(k) for k in keys}, ensure_ascii=False, indent=1))
        print("  открытые ответы:", [a["answer"] for a in r["open_answers"]])

    sessions = call("/api/reports/sessions?" + urllib.parse.urlencode({"user_id": user["id"]}), token=token)
    print("\n=== время по материалам ===")
    for m in sessions["materials"]:
        print("  %4d с  %s · ред. %s · %s  [%s]" % (m["seconds"], m["course_title"], m["revision"], m["block_title"], ", ".join(m["sources"])))
    for t in sessions["totals"]:
        print("  итого: на платформе %d с, изучение материалов %d с" % (t["platform_seconds"], t["material_seconds"]))
    print("  сеансы:", [(s["source_label"], s["end_kind"], s["active_seconds"]) for s in sessions["sessions"]][:6])

    for course_id in sorted({r["course_id"] for r in rows}):
        q = urllib.parse.urlencode({"course_id": course_id})
        data = call("/api/protocol?" + q, token=token)
        print("\n=== протокол: %s ===" % data.get("title"))
        for r in data.get("rows", []):
            if r.get("full_name") == user["full_name"]:
                print(json.dumps({k: r.get(k) for k in ("full_name", "department", "revision", "submitted_at", "score", "max_score",
                                                         "percent", "result_label", "attempt_no", "certificate_number")},
                                 ensure_ascii=False, indent=1))
        pdf = call("/api/protocol.pdf?" + q, token=token, raw=True)
        out = ROOT / "e2e" / "results" / "phone" / ("%s-protocol.pdf" % args.label)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(pdf)
        text = ""
        try:
            from pypdf import PdfReader
            text = " ".join((p.extract_text() or "") for p in PdfReader(str(out)).pages)
        except Exception as exc:          # noqa: BLE001
            text = "(текст PDF не извлечён: %s)" % exc
        print("  PDF: %s, %d байт, начинается с %r" % (out.name, len(pdf), pdf[:5]))
        print("  в PDF есть ФИО сотрудника:", user["full_name"] in text.replace("\n", " "))
        print("  фрагмент PDF:", " ".join(text.split())[:420])


if __name__ == "__main__":
    main()
