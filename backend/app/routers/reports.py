"""Отчёты, проверка открытых ответов, сертификаты и сводный протокол."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db, utcnow
from ..models import (
    ATTEMPT_LABELS, ATTEMPT_REVIEW, Attempt, AttemptAnswer, Block, Certificate, Course, CourseVersion, LearnSession,
    LibraryItem, Question, SyncBatch, TimeInterval, TrainerResult, User,
)
from ..security import Principal, audit, can_view_user, current_principal, require_methodist, require_staff, scope_department_ids
from ..serializers import attempt_brief, dept_map, iso, user_out
from ..services.attempts import finalize_if_ready
from ..services.csvio import csv_response, to_csv_bytes
from ..services.grading import shuffled_options
from ..services.pdfdocs import build_protocol_pdf, fmt_dt, fmt_num
from ..services import timereport
from ..services.timecalc import union_seconds

router = APIRouter(prefix="/api", tags=["Отчёты и аттестация"])

SOURCE_LABELS = {"pc": "ПК", "phone": "Телефон"}
TRIGGER_LABELS = {
    "online": "событие возврата сети", "visible": "возврат в приложение", "timer": "повтор по таймеру",
    "startup": "открытие приложения", "background-sync": "фоновая отправка (Background Sync)",
    "manual": "кнопка «Отправить»", "enqueue": "сразу после сохранения",
}
STATE_LABELS = {"visible": "приложение открыто", "hidden": "приложение свёрнуто", "closed": "приложение закрыто"}


def local_day(value: Optional[str], end: bool = False) -> Optional[datetime]:
    """«2026-11-20» → начало (или конец) этого дня по местному времени, в UTC."""
    if not value:
        return None
    try:
        d = datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=config.LOCAL_TZ)
    except ValueError:
        raise HTTPException(400, detail={"message": "Дата должна быть в виде ГГГГ-ММ-ДД"})
    return d + timedelta(days=1) if end else d


def _scoped_users(db: Session, p: Principal, department_id: str = "", user_id: str = "") -> dict[str, User]:
    scope = scope_department_ids(db, p)
    q = db.query(User)
    if scope is not None:
        if not scope:
            return {}
        q = q.filter(User.department_id.in_(scope))
    if department_id:
        q = q.filter(User.department_id == department_id)
    if user_id:
        q = q.filter(User.id == user_id)
    return {u.id: u for u in q.all()}


def _intervals_by_user(db: Session, user_ids: list[str], frm: Optional[datetime] = None,
                       to: Optional[datetime] = None) -> dict[str, list[TimeInterval]]:
    """Интервалы активности пользователей. Период отбирается в базе, а не после загрузки всей истории:
    отчёты опрашиваются экраном методиста каждые несколько секунд."""
    out: dict[str, list[TimeInterval]] = defaultdict(list)
    if user_ids:
        q = db.query(TimeInterval).filter(TimeInterval.user_id.in_(user_ids))
        if frm:
            q = q.filter(TimeInterval.ended_at >= frm)
        if to:
            q = q.filter(TimeInterval.started_at < to)
        for iv in q.all():
            out[iv.user_id].append(iv)
    return out


def build_report(db: Session, p: Principal, *, department_id: str = "", user_id: str = "", course_id: str = "",
                 version_id: str = "", date_from: str = "", date_to: str = "", result: str = "") -> list[dict]:
    users = _scoped_users(db, p, department_id, user_id)
    if not users:
        return []
    frm, to = local_day(date_from), local_day(date_to, end=True)
    q = db.query(Attempt).filter(Attempt.user_id.in_(list(users)))
    if version_id:
        q = q.filter(Attempt.version_id == version_id)
    if result in ATTEMPT_LABELS:
        q = q.filter(Attempt.status == result)
    if frm:
        q = q.filter(Attempt.submitted_at >= frm)
    if to:
        q = q.filter(Attempt.submitted_at < to)
    attempts = q.order_by(Attempt.received_at.desc()).all()
    versions = {v.id: v for v in db.query(CourseVersion).all()}
    if course_id:
        attempts = [a for a in attempts if versions[a.version_id].course_id == course_id]
    depts = dept_map(db)
    intervals = _intervals_by_user(db, list({a.user_id for a in attempts}), frm, to)
    sessions = {s.id: s for s in db.query(LearnSession).filter(
        LearnSession.id.in_([a.session_id for a in attempts if a.session_id])).all()} if attempts else {}
    batches = {b.id: b for b in db.query(SyncBatch).filter(
        SyncBatch.id.in_([a.sync_batch_id for a in attempts if a.sync_batch_id])).all()} if attempts else {}
    certs = {c.attempt_id: c for c in db.query(Certificate).filter(
        Certificate.attempt_id.in_([a.id for a in attempts])).all()} if attempts else {}
    questions = {q.id: q for q in db.query(Question).filter(
        Question.version_id.in_(list({a.version_id for a in attempts}))).all()} if attempts else {}
    open_answers: dict[str, list[dict]] = defaultdict(list)
    if attempts:
        for ans in db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id.in_([a.id for a in attempts])).all():
            qn = questions.get(ans.question_id)
            if qn and qn.type == "open":
                open_answers[ans.attempt_id].append({
                    "question_id": qn.id, "question": qn.text, "answer": ans.answer or "",
                    "points": ans.points, "max_points": ans.max_points, "position": qn.position})

    rows = []
    for a in attempts:
        u, v = users[a.user_id], versions[a.version_id]
        ivs = intervals.get(a.user_id, [])
        platform = union_seconds(((i.started_at, i.ended_at) for i in ivs), frm, to)
        material = union_seconds(((i.started_at, i.ended_at) for i in ivs
                                  if i.kind == "material" and i.version_id == a.version_id), frm, to)
        s = sessions.get(a.session_id) if a.session_id else None
        b = batches.get(a.sync_batch_id) if a.sync_batch_id else None
        cert = certs.get(a.id)
        dept = depts.get(u.department_id) if u.department_id else None
        rows.append({
            "attempt_id": a.id, "attempt_no": a.attempt_no,
            "user_id": u.id, "full_name": u.full_name, "personnel_no": u.personnel_no,
            "department_id": u.department_id, "department": dept.name if dept else "",
            "course_id": v.course_id, "course_title": v.title, "sop_code": v.sop_code,
            "version_id": v.id, "revision": v.revision,
            "login_at": iso(s.started_at) if s else None,
            "logout_at": iso(s.ended_at or s.last_activity_at) if s else None,
            "logout_estimated": bool(s and not s.ended_at),
            "platform_seconds": platform, "material_seconds": material,
            "submitted_at": iso(a.submitted_at), "received_at": iso(a.received_at),
            "delay_seconds": b.delay_seconds if b else None,
            "sync_trigger": b.trigger if b else "", "sync_trigger_label": TRIGGER_LABELS.get(b.trigger, b.trigger) if b else "",
            "sync_app_state": b.app_state if b else "", "sync_app_state_label": STATE_LABELS.get(b.app_state, b.app_state) if b else "",
            "score": a.score, "max_score": a.max_score, "percent": a.percent, "pass_percent": v.pass_percent,
            "status": a.status, "status_label": ATTEMPT_LABELS.get(a.status, a.status),
            "source": a.source, "source_label": SOURCE_LABELS.get(a.source, a.source),
            "open_answers": sorted(open_answers.get(a.id, []), key=lambda x: x["position"]),
            "reviewed_at": iso(a.reviewed_at),
            "certificate_id": cert.id if cert else None, "certificate_number": cert.number if cert else None,
            "certificate_file_id": cert.file_id if cert else None,
        })
    return rows


def _hms(seconds: Optional[int]) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


@router.get("/reports/attempts", summary="Отчёт по попыткам с фильтрами")
def report_attempts(department_id: str = "", user_id: str = "", course_id: str = "", version_id: str = "",
                    date_from: str = "", date_to: str = "", result: str = "",
                    p: Principal = Depends(require_staff), db: Session = Depends(get_db)) -> dict:
    rows = build_report(db, p, department_id=department_id, user_id=user_id, course_id=course_id,
                        version_id=version_id, date_from=date_from, date_to=date_to, result=result)
    return {"rows": rows, "server_time": utcnow().isoformat(), "tz": config.TZ_LABEL,
            "review_queue": sum(1 for r in rows if r["status"] == ATTEMPT_REVIEW)}


@router.get("/reports/attempts.csv", summary="Экспорт отчёта в CSV для Excel")
def report_attempts_csv(department_id: str = "", user_id: str = "", course_id: str = "", version_id: str = "",
                        date_from: str = "", date_to: str = "", result: str = "",
                        p: Principal = Depends(require_staff), db: Session = Depends(get_db)):
    rows = build_report(db, p, department_id=department_id, user_id=user_id, course_id=course_id,
                        version_id=version_id, date_from=date_from, date_to=date_to, result=result)
    header = ["ФИО", "Табельный номер", "Подразделение", "Курс", "Код СОП", "Редакция", "Вход", "Выход",
              "Выход — оценка", "Время на платформе", "Время изучения материала", "Дата попытки (устройство)",
              "Получено сервером", "Доставка после возврата связи, с", "Баллы", "Максимум", "Процент", "Итог",
              "Источник", "Попытка №", "Сертификат", "ID попытки"]

    def dt(x):
        return fmt_dt(datetime.fromisoformat(x)) if x else ""

    data = to_csv_bytes(header, [[
        r["full_name"], r["personnel_no"], r["department"], r["course_title"], r["sop_code"], r["revision"],
        dt(r["login_at"]), dt(r["logout_at"]), "да" if r["logout_estimated"] and r["logout_at"] else "",
        _hms(r["platform_seconds"]), _hms(r["material_seconds"]), dt(r["submitted_at"]), dt(r["received_at"]),
        "" if r["delay_seconds"] is None else fmt_num(r["delay_seconds"]),
        fmt_num(r["score"]), fmt_num(r["max_score"]), "" if r["percent"] is None else fmt_num(r["percent"]),
        r["status_label"], r["source_label"], r["attempt_no"], r["certificate_number"] or "", r["attempt_id"],
    ] for r in rows])
    return csv_response(data, "Отчёт по обучению.csv")


@router.get("/reports/sessions", summary="Входы, выходы и активное время")
def report_sessions(department_id: str = "", user_id: str = "", date_from: str = "", date_to: str = "",
                    p: Principal = Depends(require_staff), db: Session = Depends(get_db)) -> dict:
    data = _time_report(db, p, department_id, user_id, date_from, date_to)
    # на экране — последние 500 сеансов; итоги считаются по всем, выгрузка содержит все строки
    data["sessions"] = data["sessions"][:500]
    return data


def _time_report(db: Session, p: Principal, department_id: str, user_id: str, date_from: str, date_to: str) -> dict:
    """Один расчёт «Времени и входов» для экрана и для выгрузки (см. services/timereport.py)."""
    users = _scoped_users(db, p, department_id, user_id)
    frm, to = local_day(date_from), local_day(date_to, end=True)
    return timereport.compute(db, users, dept_map(db), frm, to, utcnow())


def _time_report_params(db: Session, p: Principal, department_id: str, user_id: str, date_from: str, date_to: str,
                        data: dict) -> list[tuple[str, str]]:
    depts = dept_map(db)
    user = db.get(User, user_id) if user_id else None
    period = "%s — %s" % (date_from or "с начала учёта", date_to or "по сегодня")
    scope = scope_department_ids(db, p)
    scope_label = "все подразделения" if scope is None else (
        ", ".join(depts[d].name for d in scope if d in depts) or "нет подразделений")
    return [
        ("Отчёт", "Время и входы — Dolce-Pharm LMS"),
        ("Период (дата устройства, %s)" % config.TZ_LABEL, period),
        ("Подразделение", depts[department_id].name if department_id in depts else "все в пределах прав"),
        ("Сотрудник", user.full_name if user else "все"),
        ("Доступ", "%s: %s" % (p.user.full_name, scope_label)),
        ("Сформирован", fmt_dt(utcnow()) + " (%s)" % config.TZ_LABEL),
        ("Сотрудников в отчёте", str(len(data["totals"]))),
        ("Сеансов (входов)", str(data["sessions_total"])),
        ("Как считается время", "Только активное время: экран виден и сотрудник что-то делает, для видео — пока оно "
                                "играет. Пересекающиеся интервалы (две вкладки, ПК и телефон, повторная доставка) "
                                "объединяются и не складываются дважды. Время за пределами периода отрезается."),
        ("Как определён выход", "явный выход — кнопка «Выйти»; сеанс идёт — выхода нет, активность была в последние "
                                "5 минут; оценка по последней записи — приложение закрыли или телефон ещё не передал "
                                "данные (время выхода = последняя полученная активность)."),
        ("Данные с телефонов", "Приходят после появления связи. Интервал несёт время устройства, поэтому поздно "
                               "доставленные данные попадают в тот день, когда человек учился. Повторная выгрузка "
                               "после синхронизации покажет их полностью."),
    ]


def _time_report_name(date_from: str, date_to: str, ext: str) -> str:
    stamp = utcnow().astimezone(config.LOCAL_TZ).strftime("%Y-%m-%d")
    period = ("%s_%s" % (date_from or "начало", date_to or stamp)) if (date_from or date_to) else stamp
    return "Время и входы %s.%s" % (period, ext)


@router.get("/reports/sessions.xlsx", summary="Выгрузка «Время и входы» в Excel: итоги, сеансы, материалы, тренажёр")
def report_sessions_xlsx(department_id: str = "", user_id: str = "", date_from: str = "", date_to: str = "",
                         p: Principal = Depends(require_staff), db: Session = Depends(get_db)):
    data = _time_report(db, p, department_id, user_id, date_from, date_to)
    params = _time_report_params(db, p, department_id, user_id, date_from, date_to, data)
    body = timereport.build_xlsx(data, params)
    name = _time_report_name(date_from, date_to, "xlsx")
    ascii_name = name.encode("ascii", "ignore").decode() or "time-report.xlsx"
    return Response(content=body, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={
        "Content-Disposition": "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, quote(name)),
    })


@router.get("/reports/sessions.csv", summary="Выгрузка «Время и входы» в CSV: одна таблица на файл")
def report_sessions_csv(table: str = "sessions", department_id: str = "", user_id: str = "", date_from: str = "",
                        date_to: str = "", p: Principal = Depends(require_staff), db: Session = Depends(get_db)):
    if table not in timereport.TABLES:
        raise HTTPException(400, detail={"message": "Таблица: totals, sessions, materials или trainer"})
    data = _time_report(db, p, department_id, user_id, date_from, date_to)
    header, rows = timereport.csv_rows(data, table)
    title = timereport.TABLES[table][0]
    return csv_response(to_csv_bytes(header, rows), _time_report_name(date_from, date_to, "csv").replace(
        "Время и входы", "Время и входы — %s" % title.lower()))


@router.get("/reports/trainer", summary="Результаты диалогового тренажёра")
def report_trainer(department_id: str = "", user_id: str = "", date_from: str = "", date_to: str = "",
                   p: Principal = Depends(require_staff), db: Session = Depends(get_db)) -> list[dict]:
    # те же фильтры и права, что у остальных вкладок «Времени и входов»
    return _time_report(db, p, department_id, user_id, date_from, date_to)["trainer"]


# ---------- попытка и проверка ----------

def _attempt_detail(db: Session, a: Attempt, reveal_key: bool) -> dict:
    v = db.get(CourseVersion, a.version_id)
    answers = {x.question_id: x for x in db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == a.id).all()}
    cert = db.query(Certificate).filter(Certificate.attempt_id == a.id).first()
    items = []
    for q in v.questions:
        ans = answers.get(q.id)
        item = {
            # у «последовательности» варианты хранятся в правильном порядке — сотруднику их
            # показываем перемешанными, иначе результат раскрывал бы ключ перед следующей попыткой
            "question_id": q.id, "type": q.type, "text": q.text,
            "options": (q.options or []) if reveal_key else shuffled_options(q), "max_points": q.points,
            "answer": ans.answer if ans else None, "is_correct": ans.is_correct if ans else None,
            "points": ans.points if ans else None, "comment": ans.comment if ans else "",
        }
        if reveal_key:
            item["correct"] = q.correct
            item["criteria"] = q.criteria
        items.append(item)
    u = db.get(User, a.user_id)
    out = attempt_brief(a, cert)
    out.update({
        "user": user_out(u, dept_map(db)), "course_title": v.title, "sop_code": v.sop_code, "revision": v.revision,
        "pass_percent": v.pass_percent, "auto_points": a.auto_points, "manual_points": a.manual_points,
        "items": items, "certificate_file_id": cert.file_id if cert else None,
    })
    return out


@router.get("/attempts/{attempt_id}", summary="Попытка: ответы и оценки")
def get_attempt(attempt_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    a = db.get(Attempt, attempt_id)
    if not a:
        raise HTTPException(404, detail={"message": "Попытка не найдена"})
    owner = db.get(User, a.user_id)
    if not can_view_user(db, p, owner):
        raise HTTPException(403, detail={"code": "forbidden", "message": "Результат другого сотрудника недоступен"})
    return _attempt_detail(db, a, reveal_key=p.is_methodist)


@router.get("/review/queue", summary="Очередь проверки открытых ответов")
def review_queue(p: Principal = Depends(require_methodist), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.query(Attempt).filter(Attempt.status == ATTEMPT_REVIEW).order_by(Attempt.received_at).all()
    depts = dept_map(db)
    out = []
    for a in rows:
        v = db.get(CourseVersion, a.version_id)
        item = attempt_brief(a)
        item.update({"user": user_out(a.user, depts), "course_title": v.title, "revision": v.revision})
        out.append(item)
    return out


class GradeIn(BaseModel):
    question_id: str
    points: float
    comment: str = ""


class ReviewIn(BaseModel):
    grades: list[GradeIn]
    comment: str = ""


@router.post("/attempts/{attempt_id}/review", summary="Оценить открытые ответы и выставить итог")
def review_attempt(attempt_id: str, body: ReviewIn, request: Request, p: Principal = Depends(require_methodist),
                   db: Session = Depends(get_db)) -> dict:
    a = db.get(Attempt, attempt_id)
    if not a:
        raise HTTPException(404, detail={"message": "Попытка не найдена"})
    if db.query(Certificate).filter(Certificate.attempt_id == a.id).first():
        raise HTTPException(409, detail={"message": "По попытке уже выдан сертификат — оценка не меняется"})
    v = db.get(CourseVersion, a.version_id)
    open_ids = {q.id: q for q in v.questions if q.type == "open"}
    answers = {x.question_id: x for x in db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == a.id).all()}
    for g in body.grades:
        q = open_ids.get(g.question_id)
        ans = answers.get(g.question_id)
        if not q or not ans:
            raise HTTPException(400, detail={"message": "Оценивать вручную можно только открытые вопросы этой попытки"})
        if g.points < 0 or g.points > q.points:
            raise HTTPException(400, detail={"message": "Баллы за вопрос — от 0 до %d" % q.points})
        ans.points = float(g.points)
        ans.is_correct = g.points >= q.points
        ans.comment = g.comment.strip()
    missing = [q for qid, q in open_ids.items() if answers.get(qid) is None or answers[qid].points is None]
    if missing:
        raise HTTPException(400, detail={"message": "Оцените все открытые ответы (осталось: %d)" % len(missing)})
    a.reviewed_by = p.user.id
    a.reviewed_at = utcnow()
    a.review_comment = body.comment.strip()
    db.flush()
    finalize_if_ready(db, a, v)
    audit(db, request, p.user.id, "attempt_review", "attempt", a.id, status=a.status, percent=a.percent)
    db.commit()
    return _attempt_detail(db, a, reveal_key=True)


# ---------- сводный протокол ----------

def protocol_data(db: Session, p: Principal, *, course_id: str, version_id: str = "", date_from: str = "",
                  date_to: str = "", department_id: str = "", latest_only: bool = True) -> dict:
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(404, detail={"message": "Выберите программу (курс)"})
    rows = build_report(db, p, department_id=department_id, course_id=course_id, version_id=version_id,
                        date_from=date_from, date_to=date_to)
    for r in rows:
        r["submitted_at"] = datetime.fromisoformat(r["submitted_at"])
    rows.sort(key=lambda r: (r["full_name"], r["submitted_at"]))
    done = [r for r in rows if r["status"] != ATTEMPT_REVIEW]
    pending = [r for r in rows if r["status"] == ATTEMPT_REVIEW]
    if latest_only:
        last: dict[str, dict] = {}
        for r in done:                      # итог сотрудника — его последняя завершённая проверка за период
            last[r["user_id"]] = r
        done = sorted(last.values(), key=lambda r: r["full_name"])
    depts = dept_map(db)
    # Шапка протокола берётся из опубликованных редакций, а не из черновика: название и код СОП
    # должны соответствовать тому, что сотрудники действительно сдавали.
    in_rows = {r["version_id"] for r in done + pending}
    released = [v for v in course.versions if v.status != "draft"] or list(course.versions)
    chosen = [v for v in released if v.id == version_id]            # явно выбранная редакция
    shown = chosen or [v for v in released if v.id in in_rows] or released
    if not shown:                                                   # у курса не осталось ни одной редакции
        raise HTTPException(404, detail={"message": "У этой программы нет опубликованных редакций"})
    head = max(shown, key=lambda v: v.revision)
    if department_id in depts:
        department = depts[department_id].name
    else:
        scope = scope_department_ids(db, p)          # руководитель видит только свои подразделения
        department = ", ".join(sorted(depts[d].name for d in scope if d in depts)) if scope else None
    return {
        "course_id": course.id, "course_title": head.title, "sop_code": head.sop_code,
        "revisions": sorted({r["revision"] for r in done + pending}),
        "date_from": local_day(date_from), "date_to": (local_day(date_to, end=True) - timedelta(seconds=1)) if date_to else None,
        "department": department,
        "rows": done, "pending": pending, "latest_only": latest_only,
    }


def _protocol_number(data: dict) -> str:
    stamp = utcnow().astimezone(config.LOCAL_TZ)
    return "%s-%s" % ((data["sop_code"] or "ПР").replace(" ", ""), stamp.strftime("%Y%m%d-%H%M"))


def _protocol_json_row(r: dict) -> dict:
    out = dict(r)
    out["submitted_at"] = iso(r["submitted_at"])
    out["result_label"] = {"passed": "Аттестован", "failed": "Не аттестован"}.get(r["status"], "На проверке")
    return out


@router.get("/protocol", summary="Сводный протокол: выборка для экрана")
def protocol(course_id: str, version_id: str = "", date_from: str = "", date_to: str = "", department_id: str = "",
             latest_only: bool = True, p: Principal = Depends(require_staff), db: Session = Depends(get_db)) -> dict:
    data = protocol_data(db, p, course_id=course_id, version_id=version_id, date_from=date_from, date_to=date_to,
                         department_id=department_id, latest_only=latest_only)
    return {
        "course_title": data["course_title"], "sop_code": data["sop_code"], "revisions": data["revisions"],
        "department": data["department"], "latest_only": latest_only,
        "rows": [_protocol_json_row(r) for r in data["rows"]],
        "pending": [_protocol_json_row(r) for r in data["pending"]],
        "summary": {"total": len(data["rows"]), "passed": sum(1 for r in data["rows"] if r["status"] == "passed"),
                    "failed": sum(1 for r in data["rows"] if r["status"] == "failed"), "pending": len(data["pending"])},
    }


@router.get("/protocol.pdf", summary="Сводный протокол: печатный PDF")
def protocol_pdf(request: Request, course_id: str, version_id: str = "", date_from: str = "", date_to: str = "",
                 department_id: str = "", latest_only: bool = True,
                 p: Principal = Depends(require_staff), db: Session = Depends(get_db)):
    data = protocol_data(db, p, course_id=course_id, version_id=version_id, date_from=date_from, date_to=date_to,
                         department_id=department_id, latest_only=latest_only)
    number = _protocol_number(data)
    pdf = build_protocol_pdf(
        course_title=data["course_title"], sop_code=data["sop_code"], revisions=data["revisions"],
        date_from=data["date_from"], date_to=data["date_to"], department=data["department"],
        rows=data["rows"], pending=data["pending"], generated_at=utcnow(), generated_by=p.user.full_name, number=number)
    audit(db, request, p.user.id, "protocol_pdf", "course", course_id, rows=len(data["rows"]), pending=len(data["pending"]))
    db.commit()
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": "inline; filename=\"protocol-%s.pdf\"" % number.encode("ascii", "ignore").decode(),
        "X-Protocol-Number": number.encode("ascii", "ignore").decode(), "Cache-Control": "no-store"})


@router.get("/protocol.csv", summary="Сводный протокол: таблица CSV из той же выборки")
def protocol_csv(course_id: str, version_id: str = "", date_from: str = "", date_to: str = "", department_id: str = "",
                 latest_only: bool = True, p: Principal = Depends(require_staff), db: Session = Depends(get_db)):
    data = protocol_data(db, p, course_id=course_id, version_id=version_id, date_from=date_from, date_to=date_to,
                         department_id=department_id, latest_only=latest_only)
    header = ["№", "ФИО", "Табельный номер", "Подразделение", "Дата проверки", "Редакция", "Баллы", "Максимум",
              "Процент", "Итог", "ID попытки"]

    def line(i: int, r: dict, label: str) -> list:
        return [i, r["full_name"], r["personnel_no"], r["department"], fmt_dt(r["submitted_at"]), r["revision"],
                fmt_num(r["score"]), fmt_num(r["max_score"]), "" if r["percent"] is None else fmt_num(r["percent"]),
                label, r["attempt_id"]]

    lines = [line(i, r, "Аттестован" if r["status"] == "passed" else "Не аттестован") for i, r in enumerate(data["rows"], 1)]
    lines += [line(i, r, "На проверке") for i, r in enumerate(data["pending"], len(lines) + 1)]
    return csv_response(to_csv_bytes(header, lines), "Протокол %s.csv" % (data["sop_code"] or "аттестации"))
