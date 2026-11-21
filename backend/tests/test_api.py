"""Критические проверки API: сквозной путь курса, синхронизация, время, аттестация, редакции, роли, выгрузки.

Тесты идут по порядку и используют общую базу контура «test» (см. conftest.py).
Запуск из папки backend:  ..\\.venv\\Scripts\\python.exe -m pytest tests -q
"""
from __future__ import annotations

import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pypdf

MATERIALS = Path(__file__).resolve().parents[1] / "demo" / "materials"
SAMPLES = Path(__file__).resolve().parents[2] / "samples"
ctx: dict = {}


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def ev(kind: str, payload: dict, at: datetime | None = None, ev_id: str | None = None) -> dict:
    return {"id": ev_id or str(uuid.uuid4()), "type": kind, "device_time": iso(at or now()), "payload": payload}


def sync(client, headers, events: list[dict], **extra) -> dict:
    body = {"device_id": "dev-test", "sent_at": iso(now()), "trigger": "enqueue", "app_state": "visible", "events": events}
    body.update(extra)
    r = client.post("/api/sync/events", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return {x["id"]: x for x in r.json()["results"]}


def upload(client, headers, name: str, data: bytes | None = None) -> dict:
    content = data if data is not None else (MATERIALS / name).read_bytes()
    r = client.post("/api/files", headers=headers, files={"file": (name, content)})
    assert r.status_code == 200, r.text
    return r.json()


def users_by_login(client, headers) -> dict:
    return {u["login"]: u for u in client.get("/api/users", headers=headers).json()}


def answers_for(version: dict, correct: bool = True, open_text: str = "Сообщу мастеру смены") -> dict:
    out = {}
    for q in version["questions"]:
        ids = [o["id"] for o in q["options"]]
        if q["type"] == "single":
            out[q["id"]] = q["correct"] if correct else next(i for i in ids if i != q["correct"])
        elif q["type"] == "multiple":
            out[q["id"]] = q["correct"] if correct else ids[:1] if ids[:1] != q["correct"] else ids
        elif q["type"] == "order":
            out[q["id"]] = q["correct"] if correct else list(reversed(q["correct"]))
        else:
            out[q["id"]] = open_text
    return out


def submit(client, headers, assignment_id: str, version: dict, answers: dict, attempt_id: str | None = None, **extra) -> tuple[str, dict]:
    attempt_id = attempt_id or str(uuid.uuid4())
    e = ev("attempt.submit", {"attempt_id": attempt_id, "assignment_id": assignment_id, "version_id": version["id"],
                              "started_at": iso(now() - timedelta(minutes=3)), "submitted_at": iso(now()), "answers": answers})
    res = sync(client, headers, [e], **extra)
    return attempt_id, res[e["id"]]


def my_assignment(client, headers, version_id: str) -> dict:
    state = client.get("/api/me/state", headers=headers).json()
    return next(a for a in state["assignments"] if a["version_id"] == version_id)


# ---------- вход и роли ----------

def test_health_and_auth(client, operator):
    assert client.get("/api/health").json()["ok"] is True
    assert client.post("/api/auth/login", json={"login": "operator", "password": "не тот"}).status_code == 401
    assert client.get("/api/me/state").status_code == 401
    me = client.get("/api/auth/me", headers=operator).json()
    assert me["user"]["full_name"] == "Ибраев Тимур Асхатович"
    assert me["settings"]["heartbeat_seconds"] == 15


def test_role_boundaries_basic(client, operator, master):
    assert client.get("/api/users", headers=operator).status_code == 403
    assert client.get("/api/reports/attempts", headers=operator).status_code == 403
    assert client.get("/api/courses", headers=master).status_code == 403
    assert client.post("/api/courses", headers=master, json={"title": "x"}).status_code == 403
    assert client.post("/api/files", headers=operator, files={"file": ("a.pdf", b"%PDF-1.4")}).status_code == 403
    assert client.get("/api/reports/attempts", headers=master).status_code == 200


# ---------- новый курс без SCORM ----------

def test_create_course_from_scratch(client, metodist, operator):
    r = client.post("/api/courses", headers=metodist, json={"title": "Проверочный курс: все виды блоков", "sop_code": "СОП-ТЕСТ-001"})
    assert r.status_code == 200
    vid = r.json()["version_id"]
    scenarios = client.get("/api/trainer/scenarios", headers=metodist).json()
    assert {"sop-revision", "clean-zone-glove"} <= {s["id"] for s in scenarios}     # методист выбирает из нескольких
    for s in scenarios:                                    # каждый сценарий цельный: переходы ведут в существующие узлы
        nodes = s["nodes"]
        assert s["start"] in nodes, s["id"]
        for key, n in nodes.items():
            assert n.get("ending") or all(c["next"] in nodes for c in n["choices"]), (s["id"], key)
        assert len({n["outcome"] for n in nodes.values() if n.get("ending")}) >= 2, s["id"]
    scenario = next(s for s in scenarios if s["id"] == "sop-revision")
    blocks = [
        {"type": "text", "title": "Введение", "data": {"text": "# Заголовок\n\nТекст урока."}},
        {"type": "pdf", "title": "Инструкция", "data": {"file_id": upload(client, metodist, "sop-ok-015-red1.pdf")["id"]}},
        {"type": "video", "title": "Видео", "required": False, "data": {"file_id": upload(client, metodist, "video-sop-check.mp4")["id"]}},
        {"type": "docx", "title": "Word", "required": False, "data": {"file_id": upload(client, metodist, "checklist-sop.docx")["id"]}},
        {"type": "xlsx", "title": "Excel", "required": False, "data": {"file_id": upload(client, metodist, "reestr-sop.xlsx")["id"]}},
        {"type": "pptx", "title": "PowerPoint", "required": False, "data": {"file_id": upload(client, metodist, "pyatiminutka-sop.pptx")["id"]}},
        {"type": "link", "title": "Справка", "required": False, "data": {"url": "https://example.org/gmp", "mandatory": False}},
        {"type": "trainer", "title": "Тренажёр", "required": False, "data": {"scenario": scenario}},
    ]
    for b in blocks:
        assert client.post(f"/api/versions/{vid}/blocks", headers=metodist, json=b).status_code == 200, b["type"]

    def opt(*texts):
        return [{"id": "o%d" % i, "text": t} for i, t in enumerate(texts)]

    questions = [
        {"type": "single", "text": "Один верный", "options": opt("да", "нет", "может быть"), "correct": "o0", "points": 1},
        {"type": "multiple", "text": "Несколько верных", "options": opt("а", "б", "в", "г"), "correct": ["o0", "o2"], "points": 2},
        {"type": "order", "text": "Порядок", "options": opt("первый", "второй", "третий", "четвёртый"), "points": 2},
        {"type": "open", "text": "Что сделаете при расхождении?", "points": 2, "criteria": "2 — остановка и сообщение мастеру"},
    ]
    for q in questions:
        assert client.post(f"/api/versions/{vid}/questions", headers=metodist, json=q).status_code == 200
    assert client.patch(f"/api/versions/{vid}", headers=metodist, json={"pass_percent": 80, "max_attempts": 2}).status_code == 200

    check = client.get(f"/api/versions/{vid}/check", headers=metodist).json()
    assert check["errors"] == [], check["errors"]
    assert any("Нужен интернет" in w for w in check["warnings"])
    assert check["package"]["total_size"] > 2_000_000

    full = client.get(f"/api/versions/{vid}", headers=metodist).json()
    office = [b for b in full["blocks"] if b["type"] in ("docx", "xlsx", "pptx")]
    assert all(b["file"]["conversion_status"] == "ok" and b["view_file"]["ext"] == "pdf" for b in office)
    pptx = next(b for b in office if b["type"] == "pptx")
    assert pptx["view_file"]["pages"] == 6          # все слайды попали в копию

    # сотруднику черновик недоступен
    assert client.get(f"/api/me/packages/{vid}", headers=operator).status_code == 403
    pub = client.post(f"/api/versions/{vid}/publish", headers=metodist, json={})
    assert pub.status_code == 200, pub.text
    # опубликованную редакцию изменить нельзя
    assert client.patch(f"/api/versions/{vid}", headers=metodist, json={"title": "другое"}).status_code == 409
    assert client.post(f"/api/versions/{vid}/blocks", headers=metodist, json={"type": "text", "data": {"text": "x"}}).status_code == 409

    users = users_by_login(client, metodist)
    r = client.post("/api/assignments", headers=metodist, json={
        "version_id": vid, "user_ids": [users["operator"]["id"], users["kim.a"]["id"]], "due_at": iso(now() + timedelta(days=3))})
    assert r.status_code == 200 and len(r.json()["created"]) == 2
    # повторное назначение не создаёт дубль
    r = client.post("/api/assignments", headers=metodist, json={"version_id": vid, "user_ids": [users["operator"]["id"]]})
    assert r.json()["created"] == [] and len(r.json()["skipped"]) == 1

    ctx["vid"] = vid
    ctx["version"] = client.get(f"/api/versions/{vid}", headers=metodist).json()
    ctx["users"] = users


def test_course_visible_to_employee_without_answer_key(client, operator, login):
    a = my_assignment(client, operator, ctx["vid"])
    assert a["state"] == "todo" and a["version"]["title"].startswith("Проверочный курс")
    assert a["package"]["total_size"] > 2_000_000
    manifest = client.get(f"/api/me/packages/{ctx['vid']}", headers=operator).json()
    raw = json.dumps(manifest, ensure_ascii=False)
    assert '"correct"' not in raw and '"criteria"' not in raw          # ключ ответов на устройство не попадает
    assert {b["type"] for b in manifest["blocks"]} == {"text", "pdf", "video", "docx", "xlsx", "pptx", "link", "trainer"}
    link = next(b for b in manifest["blocks"] if b["type"] == "link")
    assert link["needs_internet"] is True
    docx = next(b for b in manifest["blocks"] if b["type"] == "docx")
    assert docx["file"]["ext"] == "docx" and docx["view"]["ext"] == "pdf"   # исходник и читаемая копия
    order_q = next(q for q in manifest["questions"] if q["type"] == "order")
    full_q = next(q for q in ctx["version"]["questions"] if q["type"] == "order")
    assert [o["id"] for o in order_q["options"]] != full_q["correct"]       # порядок перемешан
    assert sum(f["size"] for f in manifest["files"]) < manifest["total_size"]
    # сотруднику без назначения пакет и файлы недоступны
    stranger = login("tulegenov.a")
    assert client.get(f"/api/me/packages/{ctx['vid']}", headers=stranger).status_code == 403
    video = next(b for b in manifest["blocks"] if b["type"] == "video")["file"]
    assert client.get(f"/api/files/{video['id']}/content", headers=stranger).status_code == 403
    ctx["manifest"] = manifest


def test_file_range_download(client, operator):
    video = next(b for b in ctx["manifest"]["blocks"] if b["type"] == "video")["file"]
    data = (MATERIALS / "video-sop-check.mp4").read_bytes()
    r = client.get(f"/api/files/{video['id']}/content", headers={**operator, "Range": "bytes=1000-1999"})
    assert r.status_code == 206 and r.content == data[1000:2000]
    assert r.headers["content-range"] == f"bytes 1000-1999/{len(data)}"
    r = client.get(f"/api/files/{video['id']}/content", headers={**operator, "Range": f"bytes={len(data) + 5}-"})
    assert r.status_code == 416
    assert len(client.get(f"/api/files/{video['id']}/content", headers=operator).content) == len(data)


# ---------- синхронизация: приём, повтор, частичный успех ----------

def test_attempt_goes_to_review_and_appears_in_report(client, metodist, operator):
    before = client.get("/api/reports/attempts", headers=metodist).json()["rows"]
    assert all(r["version_id"] != ctx["vid"] for r in before)            # до доставки записи о сдаче нет
    a = my_assignment(client, operator, ctx["vid"])
    answers = answers_for(ctx["version"], True, "лосось")
    restored = now() - timedelta(seconds=4)
    attempt_id, res = submit(client, operator, a["id"], ctx["version"], answers,
                             restored_at=iso(restored), trigger="online", app_state="visible")
    assert res["status"] == "accepted"
    rows = [r for r in client.get("/api/reports/attempts", headers=metodist).json()["rows"] if r["attempt_id"] == attempt_id]
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "review" and row["percent"] is None and row["source"] == "phone"
    assert row["score"] == 5 and row["max_score"] == 7
    assert row["open_answers"][0]["answer"] == "лосось"
    assert row["revision"] == 1 and 3 <= row["delay_seconds"] <= 6 and row["sync_trigger"] == "online"
    state = my_assignment(client, operator, ctx["vid"])
    assert state["state"] == "review" and state["attempts"][0]["status_label"] == "На проверке"
    ctx["attempt_op"] = attempt_id
    ctx["answers_op"] = answers
    ctx["assignment_op"] = a["id"]


def test_resend_is_idempotent(client, metodist, operator):
    """Потеря подтверждения и повторная отправка: одна попытка, один результат, одно начисление времени."""
    a_id = ctx["assignment_op"]
    start = now() - timedelta(minutes=30)
    interval = ev("time.interval", {"interval_id": str(uuid.uuid4()), "kind": "material", "version_id": ctx["vid"],
                                    "block_id": ctx["version"]["blocks"][0]["id"], "start": iso(start), "end": iso(start + timedelta(seconds=45))})
    same_attempt = ev("attempt.submit", {"attempt_id": ctx["attempt_op"], "assignment_id": a_id, "version_id": ctx["vid"],
                                         "submitted_at": iso(now()), "answers": ctx["answers_op"]})
    first = sync(client, operator, [interval, same_attempt])
    assert first[interval["id"]]["status"] == "accepted"
    assert first[same_attempt["id"]]["status"] == "accepted"            # та же попытка под новым ID события — не создаётся заново
    second = sync(client, operator, [interval, same_attempt])
    assert {r["status"] for r in second.values()} == {"duplicate"}
    rows = [r for r in client.get("/api/reports/attempts", headers=metodist).json()["rows"] if r["version_id"] == ctx["vid"]]
    assert len(rows) == 1 and rows[0]["material_seconds"] == 45
    assert len(my_assignment(client, operator, ctx["vid"])["attempts"]) == 1


def test_partial_success_and_rejections(client, operator, login):
    t = now() - timedelta(hours=2)
    good = ev("time.interval", {"kind": "platform", "start": iso(t), "end": iso(t + timedelta(seconds=30))})
    bad_interval = ev("time.interval", {"kind": "platform", "start": iso(t), "end": iso(t - timedelta(seconds=5))})
    unknown = ev("что-то.новое", {})
    foreign = ev("attempt.submit", {"attempt_id": str(uuid.uuid4()), "assignment_id": ctx["assignment_op"],
                                    "version_id": ctx["vid"], "submitted_at": iso(now()), "answers": {}})
    res = sync(client, operator, [good, bad_interval, unknown])
    assert res[good["id"]]["status"] == "accepted"
    assert res[bad_interval["id"]] == {"id": bad_interval["id"], "status": "rejected", "code": "bad_interval", "reason": "Конец интервала раньше начала."}
    assert res[unknown["id"]]["code"] == "unknown_type"
    again = sync(client, operator, [good, bad_interval])
    assert again[good["id"]]["status"] == "duplicate" and again[bad_interval["id"]]["status"] == "rejected"
    # чужое назначение
    kim = login("kim.a")
    res = sync(client, kim, [foreign])
    assert res[foreign["id"]]["code"] == "not_assigned"
    # чужое событие нельзя «присвоить» повторной отправкой
    res = sync(client, kim, [good])
    assert res[good["id"]]["code"] == "foreign_event"
    known = client.post("/api/sync/status", headers=operator, json={"ids": [good["id"], bad_interval["id"], "нет-такого"]}).json()["known"]
    assert known == {good["id"]: "accepted", bad_interval["id"]: "rejected"}


# ---------- проверка методистом, сертификат, «Не сдал», лимит попыток ----------

def test_review_gives_final_result_and_certificate(client, metodist, operator, master, login):
    aid = ctx["attempt_op"]
    detail = client.get(f"/api/attempts/{aid}", headers=metodist).json()
    open_q = next(i for i in detail["items"] if i["type"] == "open")
    assert open_q["answer"] == "лосось" and open_q["criteria"]
    # сотрудник видит свой результат, но без ключа ответов
    mine = client.get(f"/api/attempts/{aid}", headers=operator).json()
    assert all("correct" not in i and "criteria" not in i for i in mine["items"])
    assert client.post(f"/api/attempts/{aid}/review", headers=operator, json={"grades": []}).status_code == 403
    assert client.post(f"/api/attempts/{aid}/review", headers=metodist, json={"grades": []}).status_code == 400
    r = client.post(f"/api/attempts/{aid}/review", headers=metodist,
                    json={"grades": [{"question_id": open_q["question_id"], "points": 3}]})
    assert r.status_code == 400                                          # больше максимума
    r = client.post(f"/api/attempts/{aid}/review", headers=metodist,
                    json={"grades": [{"question_id": open_q["question_id"], "points": 2, "comment": "Верно"}], "comment": "Хорошо"})
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "passed" and done["percent"] == 100.0 and done["certificate_number"].startswith("DP-")
    # после выдачи сертификата оценка не меняется
    assert client.post(f"/api/attempts/{aid}/review", headers=metodist,
                       json={"grades": [{"question_id": open_q["question_id"], "points": 0}]}).status_code == 409

    state = client.get("/api/me/state", headers=operator).json()
    cert = next(c for c in state["certificates"] if c["attempt_id"] == aid)
    pdf = client.get(f"/api/files/{cert['file_id']}/content", headers=operator)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    text = " ".join(pypdf.PdfReader(io.BytesIO(pdf.content)).pages[0].extract_text().split())
    assert "Ибраев Тимур Асхатович" in text and "СОП-ТЕСТ-001" in text and "редакция 1" in text and "7 из 7" in text
    # границы доступа к документу: свой руководитель — да, чужие — нет
    assert client.get(f"/api/files/{cert['file_id']}/content", headers=master).status_code == 200
    assert client.get(f"/api/files/{cert['file_id']}/content", headers=login("kim.a")).status_code == 403
    assert client.get(f"/api/files/{cert['file_id']}/content", headers=login("nurlanova.a")).status_code == 403
    assert client.get(f"/api/attempts/{aid}", headers=login("kim.a")).status_code == 403
    ctx["cert_file"] = cert["file_id"]


def test_failed_then_passed_and_attempt_limit(client, metodist, login):
    kim = login("kim.a", kind="phone")
    a = my_assignment(client, kim, ctx["vid"])
    open_id = next(q["id"] for q in ctx["version"]["questions"] if q["type"] == "open")
    first, res = submit(client, kim, a["id"], ctx["version"], answers_for(ctx["version"], False, "не знаю"))
    assert res["status"] == "accepted"
    r = client.post(f"/api/attempts/{first}/review", headers=metodist, json={"grades": [{"question_id": open_id, "points": 0}]}).json()
    assert r["status"] == "failed" and r["percent"] == 0.0 and r["certificate_id"] is None
    assert my_assignment(client, kim, ctx["vid"])["state"] == "failed"
    second, res = submit(client, kim, a["id"], ctx["version"], answers_for(ctx["version"], True, "остановлюсь и скажу мастеру"))
    assert res["status"] == "accepted"
    r = client.post(f"/api/attempts/{second}/review", headers=metodist, json={"grades": [{"question_id": open_id, "points": 1}]}).json()
    assert r["status"] == "passed" and r["percent"] == 85.7 and r["attempt_no"] == 2
    _, res = submit(client, kim, a["id"], ctx["version"], answers_for(ctx["version"], True))
    assert res["status"] == "rejected" and res["code"] in ("already_passed", "attempts_exceeded")
    ctx["kim_attempts"] = (first, second)


# ---------- время ----------

def test_time_accounting_union_and_sessions(client, metodist, login):
    abdr = login("abdrakhmanov.r", kind="phone")
    uid = ctx["users"]["abdrakhmanov.r"]["id"]
    t0 = now() - timedelta(hours=5)
    s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
    block = ctx["version"]["blocks"][1]["id"]
    events = [
        ev("session.start", {"session_id": s1, "started_at": iso(t0 - timedelta(seconds=10))}),
        # материал: ровно 60 секунд двумя кусками по 30
        ev("time.interval", {"kind": "material", "session_id": s1, "version_id": ctx["vid"], "block_id": block, "start": iso(t0), "end": iso(t0 + timedelta(seconds=30))}),
        ev("time.interval", {"kind": "material", "session_id": s1, "version_id": ctx["vid"], "block_id": block, "start": iso(t0 + timedelta(seconds=30)), "end": iso(t0 + timedelta(seconds=60))}),
        # платформа: 80 секунд, внутри — вторая вкладка с пересечением
        ev("time.interval", {"kind": "platform", "session_id": s1, "start": iso(t0 - timedelta(seconds=10)), "end": iso(t0 + timedelta(seconds=70))}),
        ev("time.interval", {"kind": "platform", "session_id": s2, "start": iso(t0 + timedelta(seconds=20)), "end": iso(t0 + timedelta(seconds=50))}),
        ev("session.start", {"session_id": s2, "started_at": iso(t0 + timedelta(seconds=20))}),
        ev("session.end", {"session_id": s1, "ended_at": iso(t0 + timedelta(seconds=75))}),
        # аварийно закрытая вкладка: интервал помечен как оценка
        ev("time.interval", {"kind": "platform", "session_id": s2, "start": iso(t0 + timedelta(seconds=50)), "end": iso(t0 + timedelta(seconds=55)), "estimated": True}),
    ]
    assert {r["status"] for r in sync(client, abdr, events).values()} == {"accepted"}
    data = client.get(f"/api/reports/sessions?user_id={uid}", headers=metodist).json()
    total = data["totals"][0]
    assert total["platform_seconds"] == 80          # пересечения объединены, ночь между сессиями не добавляется
    assert total["material_seconds"] == 60
    assert data["materials"][0]["seconds"] == 60
    sessions = {s["id"]: s for s in data["sessions"]}
    assert sessions[s1]["end_kind"] == "logout" and sessions[s1]["active_seconds"] == 80
    assert sessions[s2]["end_kind"] == "estimated" and sessions[s2]["estimated_intervals"] == 1
    # повторная отправка тех же интервалов ничего не добавляет
    sync(client, abdr, events)
    assert client.get(f"/api/reports/sessions?user_id={uid}", headers=metodist).json()["totals"][0]["platform_seconds"] == 80


# ---------- протокол и выгрузки ----------

def test_protocol_matches_attempts(client, metodist):
    course_id = ctx["version"]["course_id"]
    today = now().astimezone(timezone(timedelta(hours=5))).strftime("%Y-%m-%d")
    q = f"course_id={course_id}&date_from={today}&date_to={today}"
    data = client.get(f"/api/protocol?{q}", headers=metodist).json()
    by_name = {r["full_name"]: r for r in data["rows"]}
    assert set(by_name) == {"Ибраев Тимур Асхатович", "Ким Алина Викторовна"}
    assert by_name["Ибраев Тимур Асхатович"]["attempt_id"] == ctx["attempt_op"]
    assert by_name["Ким Алина Викторовна"]["attempt_id"] == ctx["kim_attempts"][1]      # последняя попытка
    assert data["summary"] == {"total": 2, "passed": 2, "failed": 0, "pending": 0}
    all_attempts = client.get(f"/api/protocol?{q}&latest_only=false", headers=metodist).json()
    assert all_attempts["summary"] == {"total": 3, "passed": 2, "failed": 1, "pending": 0}

    csv_bytes = client.get(f"/api/protocol.csv?{q}", headers=metodist).content
    assert csv_bytes.startswith(b"\xef\xbb\xbf")
    lines = csv_bytes.decode("utf-8-sig").strip().split("\r\n")
    assert lines[0].split(";")[:4] == ["№", "ФИО", "Табельный номер", "Подразделение"]
    kim_line = next(l for l in lines if "Ким Алина" in l).split(";")
    assert kim_line[5:10] == ["1", "6", "7", "85,7", "Аттестован"] and kim_line[10] == ctx["kim_attempts"][1]

    pdf = client.get(f"/api/protocol.pdf?{q}", headers=metodist)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    text = " ".join(" ".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf.content)).pages).split())
    for needle in ("СВОДНЫЙ ПРОТОКОЛ", "Ибраев Тимур Асхатович", "Ким Алина Викторовна", "7 из 7", "6 из 7", "Аттестован", "Проверочный курс"):
        assert needle in text, needle

    report = client.get(f"/api/reports/attempts.csv?course_id={course_id}", headers=metodist).content.decode("utf-8-sig")
    assert report.splitlines()[0].startswith("ФИО;Табельный номер;Подразделение;Курс;Код СОП;Редакция")
    assert report.count("Проверочный курс") == 3


def test_pending_review_listed_separately(client, metodist, login):
    users = ctx["users"]
    client.post("/api/assignments", headers=metodist, json={"version_id": ctx["vid"], "user_ids": [users["smagulova.zh"]["id"]]})
    smag = login("smagulova.zh")
    a = my_assignment(client, smag, ctx["vid"])
    aid, res = submit(client, smag, a["id"], ctx["version"], answers_for(ctx["version"], True, "жду проверки"))
    assert res["status"] == "accepted"
    data = client.get(f"/api/protocol?course_id={ctx['version']['course_id']}", headers=metodist).json()
    assert [r["attempt_id"] for r in data["pending"]] == [aid] and data["summary"]["pending"] == 1
    assert aid not in [r["attempt_id"] for r in data["rows"]]
    assert any(x["id"] == aid for x in client.get("/api/review/queue", headers=metodist).json())


def test_manager_sees_only_own_department(client, metodist, master, login):
    all_rows = client.get("/api/reports/attempts", headers=metodist).json()["rows"]
    dept_rows = client.get("/api/reports/attempts", headers=master).json()["rows"]
    assert dept_rows and all(r["department"] == "Участок таблетирования" for r in dept_rows)
    other = login("nurlanova.a")                       # руководитель другого участка
    assert client.get("/api/reports/attempts", headers=other).json()["rows"] == []
    assert client.get(f"/api/attempts/{ctx['attempt_op']}", headers=other).status_code == 403
    assert client.get(f"/api/attempts/{ctx['attempt_op']}", headers=master).status_code == 200
    assert len(all_rows) >= len(dept_rows)
    # чужое подразделение в фильтре не расширяет доступ
    foreign_dept = ctx["users"]["nurlanova.a"]["department_id"]
    assert client.get(f"/api/reports/attempts?department_id={foreign_dept}", headers=master).json()["rows"] == []


# ---------- новая редакция ----------

def test_new_revision_keeps_old_attempts(client, metodist, login):
    courses = client.get("/api/courses", headers=metodist).json()
    course = next(c for c in courses if c["sop_code"] == "СОП-ОК-015")
    v1 = next(v for v in course["versions"] if v["revision"] == 1)
    v2 = next(v for v in course["versions"] if v["revision"] == 2)
    assert v1["status"] == "published" and v2["status"] == "draft"
    full1 = client.get(f"/api/versions/{v1['id']}", headers=metodist).json()

    abdr, kim = login("abdrakhmanov.r"), login("kim.a")
    a_abdr = my_assignment(client, abdr, v1["id"])
    a_kim = my_assignment(client, kim, v1["id"])
    passed_id, res = submit(client, abdr, a_abdr["id"], full1, answers_for(full1, True))
    assert res["status"] == "accepted"
    assert my_assignment(client, abdr, v1["id"])["state"] == "passed"     # без открытых вопросов итог сразу

    plan = client.get(f"/api/versions/{v2['id']}/check", headers=metodist).json()
    assert plan["errors"] == [] and plan["previous"] == {"revision": 1, "passed": 1, "not_passed": 1}
    pub = client.post(f"/api/versions/{v2['id']}/publish", headers=metodist, json={"reacquaint": True, "due_at": iso(now() + timedelta(days=5))}).json()
    assert pub["moved"] == 1 and pub["reacquainted"] == 1

    # сдавший редакцию 1 получил повторное ознакомление с редакцией 2
    re = my_assignment(client, abdr, v2["id"])
    assert re["kind"] == "reacquaint" and re["version"]["revision"] == 2 and re["state"] == "todo"
    listed = client.get(f"/api/assignments?version_id={v2['id']}&kind=reacquaint", headers=metodist).json()
    assert [x["user"]["login"] for x in listed] == ["abdrakhmanov.r"]

    # сотрудник был без сети и сдаёт уже после публикации — попытка принимается по редакции 1
    late_id, res = submit(client, kim, a_kim["id"], full1, answers_for(full1, True))
    assert res["status"] == "accepted"
    rows = {r["attempt_id"]: r for r in client.get(f"/api/reports/attempts?course_id={course['id']}", headers=metodist).json()["rows"]}
    assert rows[passed_id]["revision"] == 1 and rows[late_id]["revision"] == 1
    protocol = client.get(f"/api/protocol?course_id={course['id']}", headers=metodist).json()
    assert protocol["revisions"] == [1] and {r["revision"] for r in protocol["rows"]} == {1}
    cert = next(c for c in client.get("/api/me/state", headers=abdr).json()["certificates"] if c["attempt_id"] == passed_id)
    assert cert["revision"] == 1

    # новый сотрудник получает действующую редакцию 2; старую назначить нельзя
    petr_id = ctx["users"]["petrenko.o"]["id"]
    assert client.post("/api/assignments", headers=metodist, json={"version_id": v1["id"], "user_ids": [petr_id]}).status_code == 409
    assert client.post("/api/assignments", headers=metodist, json={"version_id": v2["id"], "user_ids": [petr_id]}).status_code == 200
    got = my_assignment(client, login("petrenko.o"), v2["id"])
    assert got["version"]["revision"] == 2 and got["version"]["questions_count"] == 3
    ctx["v2"] = v2["id"]


# ---------- публикация: ссылки и ошибки конвертации ----------

def test_publish_guards(client, metodist):
    vid = client.post("/api/courses", headers=metodist, json={"title": "Курс с обязательной ссылкой"}).json()["version_id"]
    link = client.post(f"/api/versions/{vid}/blocks", headers=metodist, json={
        "type": "link", "title": "Обязательная", "data": {"url": "https://example.org/sop", "mandatory": True}}).json()
    errors = client.get(f"/api/versions/{vid}/check", headers=metodist).json()["errors"]
    assert any("обязательная внешняя ссылка" in e for e in errors)
    r = client.post(f"/api/versions/{vid}/publish", headers=metodist, json={})
    assert r.status_code == 422 and r.json()["detail"]["errors"]
    alt = upload(client, metodist, "pamyatka-nesootvetstvie.pdf")
    client.patch(f"/api/blocks/{link['id']}", headers=metodist, json={"data": {"url": "https://example.org/sop", "mandatory": True, "alt_file_id": alt["id"]}})
    assert client.get(f"/api/versions/{vid}/check", headers=metodist).json()["errors"] == []

    # не Office-файл под видом DOCX отклоняется при загрузке
    r = client.post("/api/files", headers=metodist, files={"file": ("битый.docx", b"this is not a zip")})
    assert r.status_code == 400 and "DOCX" in r.json()["detail"]["message"]
    r = client.post("/api/files", headers=metodist, files={"file": ("virus.exe", b"MZ")})
    assert r.status_code == 400
    # архив, который не является документом: загрузка проходит, но ошибка конвертации видна и блокирует публикацию
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "не документ")
    broken = upload(client, metodist, "сломанный.docx", buf.getvalue())
    assert broken["conversion_status"] == "error" and broken["conversion_error"]
    client.post(f"/api/versions/{vid}/blocks", headers=metodist, json={"type": "docx", "title": "Сломанный", "data": {"file_id": broken["id"]}})
    errors = client.get(f"/api/versions/{vid}/check", headers=metodist).json()["errors"]
    assert any("нет читаемой копии" in e for e in errors)
    assert client.post(f"/api/versions/{vid}/publish", headers=metodist, json={}).status_code == 422


# ---------- импорт и экспорт сотрудников ----------

def test_users_import_export(client, metodist):
    data = (SAMPLES / "employees_import.csv").read_bytes()
    prev = client.post("/api/users/import/preview", headers=metodist, files={"file": ("staff.csv", data)}).json()
    assert prev["summary"] == {"new": 4, "exists": 1, "error": 1}
    statuses = {r["personnel_no"]: r["status"] for r in prev["rows"]}
    assert statuses["10234"] == "exists" and statuses[""] == "error" and statuses["30101"] == "new"
    done = client.post("/api/users/import/commit", headers=metodist, files={"file": ("staff.csv", data)}).json()
    assert len(done["created"]) == 4 and done["skipped_existing"] == 1 and done["errors"] == 1
    # созданный сотрудник может войти с выданным паролем
    c = done["created"][0]
    assert client.post("/api/auth/login", json={"login": c["login"], "password": c["password"]}).status_code == 200
    # повторный импорт того же файла никого не создаёт
    again = client.post("/api/users/import/commit", headers=metodist, files={"file": ("staff.csv", data)}).json()
    assert again["created"] == [] and again["skipped_existing"] == 5
    # файл из Excel в Windows-1251 с запятыми
    cp = "Табельный номер,ФИО,Подразделение\r\n30201,Ахметов Ержан Болатович,Склад готовой продукции\r\n".encode("cp1251")
    prev = client.post("/api/users/import/preview", headers=metodist, files={"file": ("cp.csv", cp)}).json()
    assert prev["encoding"] == "Windows-1251" and prev["rows"][0]["full_name"] == "Ахметов Ержан Болатович" and prev["rows"][0]["status"] == "new"
    # руководитель, импортированный в подразделение, где руководитель уже есть: методист получает предупреждение
    second = ("Табельный номер;ФИО;Подразделение;Должность;Роль\r\n"
              "30301;Жунусов Арман Бекович;Участок фасовки и упаковки;Мастер смены;Руководитель\r\n").encode("utf-8-sig")
    res = client.post("/api/users/import/commit", headers=metodist, files={"file": ("boss.csv", second)}).json()
    assert len(res["created"]) == 1 and "уже есть руководитель" in res["created"][0]["note"]
    assert all(c["note"] is None for c in done["created"])

    export = client.get("/api/users/export.csv", headers=metodist)
    assert export.content.startswith(b"\xef\xbb\xbf") and "attachment" in export.headers["content-disposition"]
    text = export.content.decode("utf-8-sig")
    assert text.splitlines()[0] == "Табельный номер;ФИО;Подразделение;Должность;Роль;Логин;Активен"
    assert "Жумабаев Алмас Кайратович" in text and text.count("10234;") == 1


# ---------- библиотека ----------

def test_library_search(client, operator, metodist):
    def titles(q="", category=""):
        r = client.get("/api/library", headers=operator, params={"q": q, "category": category}).json()
        return sorted(i["title"] for i in r["items"])

    def ranked(q):
        return client.get("/api/library", headers=operator, params={"q": q}).json()["items"]

    assert len(titles()) == 7
    coded = {"Памятка: в смене оказалась инструкция другой редакции", "Пятиминутка: та ли редакция у вас в руках?"}
    for q in ("соп-ок-015", "СОП ОК 015", "сопок015", "ок-015"):     # код СОП в любой записи, регистр не важен
        found = ranked(q)
        assert {i["title"] for i in found[:2]} == coded, q        # совпадение по коду — первым
        assert all(i["score"] < found[1]["score"] for i in found[2:]), q
    assert titles("ГЛОССАРИЙ") == ["Краткий глоссарий для оператора"]
    hygiene = "Гигиена и переодевание при входе в чистую зону"
    assert ranked("переодевание гигиена")[0]["title"] == hygiene   # слова в любом порядке
    assert ranked("гигиены при переодевании")[0]["title"] == hygiene  # другие окончания
    assert titles(category="Реестры") == ["Реестр действующих редакций СОП — участок таблетирования"]
    assert titles("нет такого слова") == []
    # слово, которое есть только в тексте документа: материал находится, и показывается отрывок
    items = client.get("/api/library", headers=operator).json()["items"]
    shown = " ".join("%s %s %s %s" % (i["title"], i["sop_code"], i["category"], i["description"]) for i in items).lower()
    doc = next(i for i in items if i["title"] == hygiene)
    assert len(doc["text"]) > 200                                   # текст документа приходит для поиска без сети
    shown = shown.replace("ё", "е")
    word = next(w for w in re.findall(r"[а-я]{7,}", doc["text"].lower().replace("ё", "е")) if w[:5] not in shown)
    hit = next(i for i in ranked(word) if i["title"] == hygiene)
    assert hit["snippet"] and word[:5] in hit["snippet"].lower().replace("ё", "е")
    item = next(i for i in client.get("/api/library", headers=operator).json()["items"] if i["file"]["ext"] == "xlsx" and "Реестр" in i["title"])
    assert item["view"]["ext"] == "pdf"
    pdf = client.get(f"/api/files/{item['view']['id']}/content", headers=operator)
    assert pdf.status_code == 200
    text = " ".join(" ".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf.content)).pages).split())
    for column in ("Код СОП", "Название", "Действующая редакция", "Дата введения", "Ответственный"):
        assert column.replace(" ", "") in text.replace(" ", ""), column          # все столбцы таблицы читаются
    assert client.post("/api/library", headers=operator, data={"title": "x"}, files={"file": ("a.pdf", b"%PDF")}).status_code == 403
    added = client.post("/api/library", headers=metodist, data={"title": "Новый справочник", "category": "Справочники", "sop_code": "СОП-Х-1"},
                        files={"file": ("glossary.pdf", (MATERIALS / "glossary-gmp.pdf").read_bytes())})
    assert added.status_code == 200 and titles("сОп-х-1") == ["Новый справочник"]
    assert ranked("СОПХ1")[0]["title"] == "Новый справочник"
    assert client.delete(f"/api/library/{added.json()['id']}", headers=metodist).status_code == 200
    assert titles("СОП-Х-1") == []


# ---------- оценка 180/360 ----------

def test_assessment_360_recalculates(client, metodist, operator, master, login):
    a = client.get("/api/assessments", headers=metodist).json()[0]
    c1, c2, c3 = [r["competency_id"] for r in a["rows"]]
    assert a["received"] == 0 and all(r["self"] is None and r["answers_count"] == 0 for r in a["rows"])

    def answer(headers, scores):
        task = next(t for t in client.get("/api/me/assessments", headers=headers).json() if t["title"] == a["title"])
        r = client.post(f"/api/me/assessments/{task['rater_row_id']}", headers=headers, json={"scores": scores})
        assert r.status_code == 200, r.text
        return task["rater_row_id"]

    kim, abdr = login("kim.a"), login("abdrakhmanov.r")
    answer(operator, {c1: 4, c2: 5, c3: 3})
    answer(master, {c1: 3, c2: None, c3: 4})
    kim_row = answer(kim, {c1: 5, c2: 4, c3: None})
    answer(abdr, {c1: 3, c2: 2, c3: None})
    rows = {r["competency_id"]: r for r in client.get(f"/api/assessments/{a['id']}", headers=metodist).json()["rows"]}
    assert (rows[c1]["self"], rows[c1]["manager"], rows[c1]["peers_avg"], rows[c1]["peers_count"], rows[c1]["answers_count"]) == (4, 3.0, 4.0, 2, 4)
    assert rows[c1]["others_avg"] == 3.67 and rows[c1]["gap"] == 0.33
    assert rows[c2]["manager"] is None and rows[c2]["peers_avg"] == 3.0 and rows[c2]["answers_count"] == 3    # пропуск — не ноль
    assert rows[c3]["peers_avg"] is None and rows[c3]["peers_count"] == 0 and rows[c3]["others_avg"] == 4.0 and rows[c3]["answers_count"] == 2
    # одна изменённая оценка меняет сводку
    answer(kim, {c1: 1, c2: 4, c3: None})
    assert client.get(f"/api/assessments/{a['id']}", headers=metodist).json()["rows"][0]["peers_avg"] == 2.0
    # посторонний не участвует и не может ответить за другого
    smag = login("smagulova.zh")
    assert client.get("/api/me/assessments", headers=smag).json() == []
    assert client.post(f"/api/me/assessments/{kim_row}", headers=smag, json={"scores": {c1: 5}}).status_code == 404
    assert client.post(f"/api/me/assessments/{kim_row}", headers=kim, json={"scores": {c1: 9}}).status_code == 400
    assert client.get("/api/assessments", headers=operator).status_code == 403

    # режим 180°: только самооценка и руководитель
    users = ctx["users"]
    r = client.post("/api/assessments", headers=metodist, json={"subject_id": users["petrenko.o"]["id"], "mode": "180", "competency_ids": [c1, c2]})
    assert r.status_code == 200 and sorted(x["role"] for x in r.json()["raters"]) == ["manager", "self"]
    r = client.post("/api/assessments", headers=metodist, json={"subject_id": users["petrenko.o"]["id"], "mode": "360", "competency_ids": [c1]})
    assert r.status_code == 400                                          # для 360° нужны коллеги

    # комментарии видны методисту: самооценка — с ролью, коллеги — без имён
    task = next(t for t in client.get("/api/me/assessments", headers=kim).json() if t["title"] == a["title"])
    assert client.post(f"/api/me/assessments/{task['rater_row_id']}", headers=kim,
                       json={"scores": {c1: 2}, "comment": "Хорошо передаёт смену"}).status_code == 200
    summary = client.get(f"/api/assessments/{a['id']}", headers=metodist).json()
    assert {"role": "peer", "role_label": "Коллега", "text": "Хорошо передаёт смену"}.items() <= summary["comments"][-1].items()
    assert "Ким" not in json.dumps(summary["comments"], ensure_ascii=False)
    # новая компетенция добавляется из окна запуска; повтор названия отклоняется
    new = client.post("/api/competencies", headers=metodist, json={"name": "Работа с оборудованием", "description": "Без замечаний"})
    assert new.status_code == 200
    assert client.post("/api/competencies", headers=metodist, json={"name": "Работа с оборудованием"}).status_code == 409
    assert client.post("/api/competencies", headers=operator, json={"name": "Другое"}).status_code == 403
    # «Завершить сбор»: сводка фиксируется, участники больше не могут менять оценки
    closed = client.post(f"/api/assessments/{a['id']}/close", headers=metodist)
    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    assert client.post(f"/api/me/assessments/{task['rater_row_id']}", headers=kim, json={"scores": {c1: 5}}).status_code == 409
    assert client.post(f"/api/assessments/{a['id']}/close", headers=operator).status_code == 403


# ---------- тренажёр ----------

def test_trainer_result_saved(client, metodist, operator):
    trainer = next(b for b in ctx["version"]["blocks"] if b["type"] == "trainer")
    endings = [k for k, n in trainer["data"]["scenario"]["nodes"].items() if n.get("ending")]
    assert len(endings) >= 2 and len({trainer["data"]["scenario"]["nodes"][k]["outcome"] for k in endings}) >= 2
    e = ev("trainer.result", {"result_id": str(uuid.uuid4()), "version_id": ctx["vid"], "block_id": trainer["id"],
                              "ending_id": "end_good", "ending_title": "Смена началась по действующей редакции", "outcome": "good",
                              "path": [{"node": "start", "choice": "tell"}], "finished_at": iso(now())})
    assert sync(client, operator, [e])[e["id"]]["status"] == "accepted"
    assert sync(client, operator, [e])[e["id"]]["status"] == "duplicate"
    rows = client.get("/api/reports/trainer", headers=metodist).json()
    assert len(rows) == 1 and rows[0]["outcome"] == "good" and rows[0]["full_name"] == "Ибраев Тимур Асхатович"
    state = client.get("/api/me/state", headers=operator).json()
    assert state["trainer_results"][0]["ending_id"] == "end_good"


# ---------- настройки, служебное, выход ----------

def test_settings_and_system(client, metodist, operator):
    assert client.patch("/api/settings", headers=operator, json={"idle_timeout_seconds": 60}).status_code == 403
    assert client.patch("/api/settings", headers=metodist, json={"idle_timeout_seconds": 5}).status_code == 400
    assert client.patch("/api/settings", headers=metodist, json={"idle_timeout_seconds": 180}).json()["idle_timeout_seconds"] == 180
    assert client.get("/api/auth/me", headers=operator).json()["settings"]["idle_timeout_seconds"] == 180
    client.patch("/api/settings", headers=metodist, json={"idle_timeout_seconds": 120})
    info = client.get("/api/system/info", headers=metodist).json()
    assert info["env"] == "test" and "qr_app" in (info["lan"][0] if info["lan"] else {"qr_app": ""})
    assert client.get("/api/system/info", headers=operator).status_code == 403
    spec = client.get("/api/openapi.json").json()
    assert "/api/sync/events" in spec["paths"] and "/api/protocol.pdf" in spec["paths"]
    assert "Установите сертификат" in client.get("/phone").text


# ---------- исправления по итогам аудита ----------

def test_manager_created_in_product_sees_department(client, metodist):
    """Руководитель, заведённый через импорт или вручную, получает своё подразделение в отчётах."""
    users = users_by_login(client, metodist)
    boss = next(u for u in users.values() if u["full_name"] == "Сергеева Наталья Юрьевна")       # из образца CSV
    assert boss["role"] == "manager"
    depts = {d["id"]: d for d in client.get("/api/departments", headers=metodist).json()}
    assert depts[boss["department_id"]]["manager_id"] == boss["id"]
    created = client.post("/api/users", headers=metodist, json={
        "personnel_no": "40001", "full_name": "Байжанов Руслан Маратович", "role": "manager",
        "department_id": None, "position": "Начальник смены"}).json()
    h = {"Authorization": "Bearer " + client.post("/api/auth/login", json={
        "login": created["login"], "password": created["initial_password"]}).json()["token"]}
    assert client.get("/api/reports/attempts", headers=h).json()["rows"] == []                    # подразделения ещё нет
    # перевод руководителем в отдел, где руководителя нет, даёт доступ к его отчётам
    edu = next(d for d in depts.values() if d["name"] == "Отдел обучения")
    assert edu["manager_id"] is None
    client.patch(f"/api/users/{created['id']}", headers=metodist, json={"department_id": edu["id"]})
    assert client.get("/api/auth/me", headers=h).json()["user"]["managed_department_ids"] == [edu["id"]]
    # занятое место руководителя не перехватывается
    tab = next(d for d in depts.values() if d["name"] == "Участок таблетирования")
    client.patch(f"/api/users/{created['id']}", headers=metodist, json={"department_id": tab["id"]})
    after = {d["id"]: d for d in client.get("/api/departments", headers=metodist).json()}
    assert after[tab["id"]]["manager_id"] == tab["manager_id"] and after[edu["id"]]["manager_id"] is None
    # о том, что место занято, методист узнаёт сразу при создании второго руководителя
    second = client.post("/api/users", headers=metodist, json={
        "personnel_no": "40002", "full_name": "Омаров Серик Талгатович", "role": "manager", "department_id": tab["id"]}).json()
    assert "уже есть руководитель" in second["note"]
    # прежнего руководителя отключили — место переходит к руководителю, заведённому раньше остальных
    client.patch(f"/api/users/{tab['manager_id']}", headers=metodist, json={"is_active": False})
    after = {d["id"]: d for d in client.get("/api/departments", headers=metodist).json()}
    assert after[tab["id"]]["manager_id"] == created["id"]
    assert client.get("/api/auth/me", headers=h).json()["user"]["managed_department_ids"] == [tab["id"]]
    client.patch(f"/api/users/{tab['manager_id']}", headers=metodist, json={"is_active": True})
    # смена пароля завершает открытые сеансы
    client.patch(f"/api/users/{created['id']}", headers=metodist, json={"password": "новый-пароль-1"})
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_revision_number_and_link_rules(client, metodist):
    r = client.post("/api/courses", headers=metodist, json={"title": "Курс по редакции 4", "sop_code": "СОП-Х-9", "revision": 4})
    vid = r.json()["version_id"]
    v = client.get(f"/api/versions/{vid}", headers=metodist).json()
    assert v["revision"] == 4
    assert client.patch(f"/api/versions/{vid}", headers=metodist, json={"revision": 0}).status_code == 400
    assert client.patch(f"/api/versions/{vid}", headers=metodist, json={"revision": 5}).json()["revision"] == 5
    # ссылка, отмеченная только общим флажком «обязательный материал», тоже требует локальную копию
    block = client.post(f"/api/versions/{vid}/blocks", headers=metodist, json={
        "type": "link", "title": "Ссылка", "required": True, "data": {"url": "https://example.org/a", "mandatory": False}}).json()
    assert block["required"] is False                    # признак обязательности у ссылки один
    client.patch(f"/api/blocks/{block['id']}", headers=metodist, json={"data": {"url": "https://example.org/a", "mandatory": True}})
    full = client.get(f"/api/versions/{vid}", headers=metodist).json()
    assert full["blocks"][0]["required"] is True
    assert any("обязательная внешняя ссылка" in e for e in client.get(f"/api/versions/{vid}/check", headers=metodist).json()["errors"])
    # новая редакция нумеруется от заданного номера
    client.patch(f"/api/blocks/{block['id']}", headers=metodist, json={"data": {"url": "https://example.org/a", "mandatory": False}})
    client.post(f"/api/versions/{vid}/blocks", headers=metodist, json={"type": "text", "title": "Текст", "data": {"text": "Содержание"}})
    assert client.post(f"/api/versions/{vid}/publish", headers=metodist, json={}).status_code == 200
    nxt = client.post(f"/api/courses/{v['course_id']}/new-version", headers=metodist).json()["version_id"]
    assert client.get(f"/api/versions/{nxt}", headers=metodist).json()["revision"] == 6
    assert client.patch(f"/api/versions/{nxt}", headers=metodist, json={"revision": 5}).status_code == 400


def test_attempt_detail_hides_order_key(client, metodist, login):
    kim = login("kim.a")
    first = ctx["kim_attempts"][0]
    order_q = next(q for q in ctx["version"]["questions"] if q["type"] == "order")
    mine = client.get(f"/api/attempts/{first}", headers=kim).json()
    item = next(i for i in mine["items"] if i["type"] == "order")
    assert [o["id"] for o in item["options"]] != order_q["correct"]              # сотруднику — вперемешку
    full = client.get(f"/api/attempts/{first}", headers=metodist).json()
    assert [o["id"] for o in next(i for i in full["items"] if i["type"] == "order")["options"]] == order_q["correct"]


def test_package_hash_ignores_revision_status(client, metodist, login):
    """Замена редакции новой не должна выглядеть для телефона как обновление старого пакета."""
    v2 = client.get(f"/api/versions/{ctx['v2']}", headers=metodist).json()
    before = client.get(f"/api/versions/{ctx['v2']}/check", headers=metodist).json()["package"]["manifest_hash"]
    nxt = client.post(f"/api/courses/{v2['course_id']}/new-version", headers=metodist).json()["version_id"]
    assert client.post(f"/api/versions/{nxt}/publish", headers=metodist, json={"reacquaint": False}).status_code == 200
    assert client.get(f"/api/versions/{ctx['v2']}", headers=metodist).json()["status"] == "superseded"
    assert client.get(f"/api/versions/{ctx['v2']}/check", headers=metodist).json()["package"]["manifest_hash"] == before


def test_csv_import_rejects_non_csv_cleanly(client, metodist):
    utf16 = "Табельный номер;ФИО\r\n50001;Тестов Тест Тестович\r\n".encode("utf-16")
    r = client.post("/api/users/import/preview", headers=metodist, files={"file": ("u16.csv", utf16)})
    assert r.status_code == 200 and r.json()["encoding"] == "UTF-16" and r.json()["rows"][0]["status"] == "new"
    xlsx = (MATERIALS / "reestr-sop.xlsx").read_bytes()
    r = client.post("/api/users/import/preview", headers=metodist, files={"file": ("staff.xlsx", xlsx)})
    assert r.status_code == 400 and "CSV" in r.json()["detail"]["message"]
    r = client.post("/api/users/import/preview", headers=metodist, files={"file": ("bin.csv", b"a;b\r\n\x00\x00\x01;\x02\r\n")})
    assert r.status_code == 400


def test_trainer_payload_cannot_break_report(client, metodist, operator, login):
    e = ev("trainer.result", {"result_id": str(uuid.uuid4()), "version_id": ctx["vid"], "block_id": "x", "ending_id": "end_bad",
                              "ending_title": "t", "outcome": "bad", "path": 5, "finished_at": iso(now())})
    assert sync(client, operator, [e])[e["id"]]["status"] == "accepted"
    assert client.get("/api/reports/trainer", headers=metodist).status_code == 200
    # руководитель другого участка чужих результатов не видит: отбор по своим сотрудникам идёт в запросе, до ограничения строк
    assert len(client.get("/api/reports/trainer", headers=metodist).json()) >= 2
    assert client.get("/api/reports/trainer", headers=login("nurlanova.a")).json() == []
    # слишком длинный идентификатор отклоняет только свою запись — остальные записи пакета принимаются
    t = now() - timedelta(hours=6)
    bad = ev("trainer.result", {"result_id": "x" * 40, "version_id": ctx["vid"], "ending_id": "e", "finished_at": iso(now())})
    good = ev("time.interval", {"kind": "platform", "start": iso(t), "end": iso(t + timedelta(seconds=10))})
    res = sync(client, operator, [bad, good])
    assert res[bad["id"]]["status"] == "rejected" and res[bad["id"]]["code"] == "bad_payload"
    assert res[good["id"]]["status"] == "accepted"


def test_library_time_reported(client, metodist, operator):
    item = client.get("/api/library", headers=operator).json()["items"][0]
    t = now() - timedelta(hours=3)
    e = ev("time.interval", {"kind": "material", "library_item_id": item["id"], "start": iso(t), "end": iso(t + timedelta(seconds=40))})
    assert sync(client, operator, [e])[e["id"]]["status"] == "accepted"
    uid = ctx["users"]["operator"]["id"]
    mats = client.get(f"/api/reports/sessions?user_id={uid}", headers=metodist).json()["materials"]
    lib = [m for m in mats if m["course_title"] == "Библиотека"]
    assert len(lib) == 1 and lib[0]["block_title"] == item["title"] and lib[0]["seconds"] == 40


def test_time_report_export_matches_screen(client, metodist, login, operator):
    """«Время и входы»: выгрузка в Excel и CSV совпадает с экраном и включает тех, кто только читал материалы."""
    from openpyxl import load_workbook

    from app import config

    li = login("li.s", kind="phone")
    uid = users_by_login(client, metodist)["li.s"]["id"]
    item = client.get("/api/library", headers=li).json()["items"][0]
    tz = config.LOCAL_TZ
    day = (now().astimezone(tz) - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    at = lambda h, m, s=0: (day + timedelta(hours=h, minutes=m, seconds=s)).astimezone(timezone.utc)  # noqa: E731
    sa, sb, sm, sc = (str(uuid.uuid4()) for _ in range(4))
    events = [
        # сеанс A: вход 09:00, 10 минут на платформе, из них 3 минуты — справочник библиотеки, явный выход в 09:12
        ev("session.start", {"session_id": sa, "started_at": iso(at(9, 0))}),
        ev("time.interval", {"kind": "platform", "session_id": sa, "start": iso(at(9, 0)), "end": iso(at(9, 10))}),
        ev("time.interval", {"kind": "material", "session_id": sa, "library_item_id": item["id"], "start": iso(at(9, 2)), "end": iso(at(9, 5))}),
        ev("session.end", {"session_id": sa, "ended_at": iso(at(9, 12))}),
        # сеанс B: второе посещение в 14:00, выхода нет — оценка по последней записи
        ev("session.start", {"session_id": sb, "started_at": iso(at(14, 0))}),
        ev("time.interval", {"kind": "platform", "session_id": sb, "start": iso(at(14, 0)), "end": iso(at(14, 3))}),
        # сеанс через полночь: из 5 минут в период попадают 3 (00:00–00:03)
        ev("session.start", {"session_id": sm, "started_at": iso(at(-1, 58))}),
        ev("time.interval", {"kind": "platform", "session_id": sm, "start": iso(at(-1, 58)), "end": iso(at(0, 3))}),
        # сеанс, который идёт прямо сейчас (вне выбранного дня)
        ev("session.start", {"session_id": sc, "started_at": iso(now() - timedelta(minutes=1))}),
        ev("time.interval", {"kind": "platform", "session_id": sc, "start": iso(now() - timedelta(seconds=60)), "end": iso(now() - timedelta(seconds=10))}),
    ]
    assert {r["status"] for r in sync(client, li, events).values()} == {"accepted"}
    q = f"?user_id={uid}&date_from={day:%Y-%m-%d}&date_to={day:%Y-%m-%d}"
    screen = client.get("/api/reports/sessions" + q, headers=metodist).json()
    total = screen["totals"][0]
    assert total["full_name"] == "Ли Светлана Олеговна"          # тест не сдавала — в отчёте всё равно есть
    assert total["platform_seconds"] == 10 * 60 + 3 * 60 + 3 * 60
    assert total["material_seconds"] == total["library_seconds"] == 3 * 60
    assert total["sessions"] == 3
    kinds = {s["id"]: s["end_kind"] for s in screen["sessions"]}
    assert kinds == {sa: "logout", sb: "estimated", sm: "estimated"}
    # без фильтра периода — сеанс, который идёт сейчас, отмечен «сеанс идёт»
    live = client.get(f"/api/reports/sessions?user_id={uid}", headers=metodist).json()
    assert {s["id"]: s["end_kind"] for s in live["sessions"]}[sc] == "open"

    # данные с телефона пришли позже: ещё 5 минут в сеансе A, сделанные утром того же дня
    late = ev("time.interval", {"kind": "platform", "session_id": sa, "start": iso(at(9, 20)), "end": iso(at(9, 25))})
    assert sync(client, li, [late])[late["id"]]["status"] == "accepted"
    screen = client.get("/api/reports/sessions" + q, headers=metodist).json()
    total = screen["totals"][0]
    assert total["platform_seconds"] == 21 * 60
    assert {s["id"]: s["active_seconds"] for s in screen["sessions"]}[sa] == 15 * 60

    # Excel: те же числа, кириллица, все листы
    r = client.get("/api/reports/sessions.xlsx" + q, headers=metodist)
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Итого по сотрудникам", "Входы и выходы", "По материалам", "Тренажёр", "Параметры"]
    ws = wb["Итого по сотрудникам"]
    head = [c.value for c in ws[1]]
    row = {h: c.value for h, c in zip(head, ws[2])}
    assert row["ФИО"] == "Ли Светлана Олеговна"
    # длительность хранится как время Excel (формат [ч]:мм:сс): openpyxl читает её как timedelta
    secs = lambda v: round(v.total_seconds() if isinstance(v, timedelta) else v * 86400)  # noqa: E731
    assert secs(row["Время на платформе"]) == total["platform_seconds"]
    assert secs(row["Из него — изучение материалов"]) == total["material_seconds"]
    assert row["Входов (сеансов)"] == 3
    sessions_sheet = wb["Входы и выходы"]
    assert sessions_sheet.max_row - 1 == len(screen["sessions"]) == 3
    labels = {c.value for c in sessions_sheet["F"][1:]}
    assert labels == {"явный выход", "оценка по последней записи"}
    assert wb["По материалам"]["G2"].value == item["title"]

    # CSV: BOM для Excel, точка с запятой, те же секунды
    r = client.get("/api/reports/sessions.csv" + q + "&table=totals", headers=metodist)
    assert r.content.startswith(b"\xef\xbb\xbf")
    lines = r.content.decode("utf-8-sig").splitlines()
    head = lines[0].split(";")
    values = dict(zip(head, lines[1].split(";")))
    assert values["ФИО"] == "Ли Светлана Олеговна"
    assert int(values["Время на платформе, секунд"]) == total["platform_seconds"]
    assert values["Время на платформе, ч:мм:сс"] == "0:21:00"
    r = client.get("/api/reports/sessions.csv" + q + "&table=sessions", headers=metodist)
    assert len(r.content.decode("utf-8-sig").splitlines()) == 1 + 3

    # права: руководитель чужого подразделения сотрудницу не видит, руководитель её отдела — видит
    other = client.get("/api/reports/sessions.xlsx" + q, headers=login("nurlanova.a"))
    assert load_workbook(io.BytesIO(other.content))["Итого по сотрудникам"].max_row == 1
    own = client.get("/api/reports/sessions" + q, headers=login("akhmetova.g")).json()
    assert own["totals"][0]["platform_seconds"] == total["platform_seconds"]
    assert client.get("/api/reports/sessions.xlsx" + q, headers=operator).status_code == 403


def test_logout_revokes_token(client):
    r = client.post("/api/auth/login", json={"login": "li.s", "password": "demo2026"})
    h = {"Authorization": "Bearer " + r.json()["token"]}
    assert client.get("/api/me/state", headers=h).status_code == 200
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    r = client.get("/api/me/state", headers=h)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "auth_expired"
