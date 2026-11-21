"""Кабинет методиста: курсы, редакции, блоки урока, вопросы теста, публикация и назначения."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db, utcnow
from ..models import (
    ATTEMPT_PASSED, BLOCK_TYPES, QUESTION_TYPES, Assignment, Attempt, Block, BlockProgress, Course, CourseVersion,
    Department, Question, StoredFile, User, new_id,
)
from ..security import Principal, audit, require_methodist
from ..serializers import attempt_brief, block_out, dept_map, iso, question_out, user_out, version_brief
from ..services.packaging import build_manifest, package_summary, validate_for_publish
from ..services.syncsvc import parse_dt

router = APIRouter(prefix="/api", tags=["Курсы и назначения"])


def _version(db: Session, version_id: str, draft_only: bool = False) -> CourseVersion:
    v = db.get(CourseVersion, version_id)
    if not v:
        raise HTTPException(404, detail={"message": "Редакция курса не найдена"})
    if draft_only and v.status != "draft":
        raise HTTPException(409, detail={"code": "published", "message":
                            "Опубликованная редакция не изменяется. Создайте новую редакцию."})
    return v


def _version_full(db: Session, v: CourseVersion) -> dict:
    out = version_brief(v)
    out["blocks"] = [block_out(db, b) for b in v.blocks]
    out["questions"] = [question_out(q) for q in v.questions]
    out["course"] = {"id": v.course.id, "title": v.course.title, "sop_code": v.course.sop_code}
    out["versions"] = [{"id": x.id, "revision": x.revision, "status": x.status} for x in v.course.versions]
    return out


# ---------- курсы ----------

class CourseIn(BaseModel):
    title: str
    sop_code: str = ""
    description: str = ""
    revision: int = 1              # номер редакции исходного документа (СОП может быть уже 3-й редакции)


@router.get("/courses", summary="Список курсов с редакциями")
def list_courses(p: Principal = Depends(require_methodist), db: Session = Depends(get_db)) -> list[dict]:
    out = []
    for c in db.query(Course).filter(Course.archived.is_(False)).order_by(Course.created_at).all():
        versions = []
        for v in c.versions:
            brief = version_brief(v)
            brief["assigned"] = db.query(Assignment).filter(Assignment.version_id == v.id, Assignment.status == "active").count()
            versions.append(brief)
        out.append({"id": c.id, "title": c.title, "sop_code": c.sop_code, "description": c.description,
                    "created_at": iso(c.created_at), "versions": versions})
    return out


@router.post("/courses", summary="Создать курс: появляется черновик редакции 1")
def create_course(body: CourseIn, request: Request, p: Principal = Depends(require_methodist),
                  db: Session = Depends(get_db)) -> dict:
    if not body.title.strip():
        raise HTTPException(400, detail={"message": "Укажите название курса"})
    if not (1 <= body.revision <= 999):
        raise HTTPException(400, detail={"message": "Номер редакции — от 1 до 999"})
    c = Course(id=new_id(), title=body.title.strip(), sop_code=body.sop_code.strip(),
               description=body.description.strip(), created_by=p.user.id)
    v = CourseVersion(id=new_id(), course_id=c.id, revision=body.revision, status="draft", title=c.title,
                      sop_code=c.sop_code, description=c.description)
    db.add_all([c, v])
    audit(db, request, p.user.id, "course_create", "course", c.id)
    db.commit()
    return {"course_id": c.id, "version_id": v.id}


@router.delete("/courses/{course_id}", summary="Убрать курс в архив")
def archive_course(course_id: str, request: Request, p: Principal = Depends(require_methodist),
                   db: Session = Depends(get_db)) -> dict:
    c = db.get(Course, course_id)
    if not c:
        raise HTTPException(404, detail={"message": "Курс не найден"})
    c.archived = True
    audit(db, request, p.user.id, "course_archive", "course", c.id)
    db.commit()
    return {"ok": True}


# ---------- редакции ----------

class VersionPatch(BaseModel):
    title: Optional[str] = None
    sop_code: Optional[str] = None
    description: Optional[str] = None
    change_note: Optional[str] = None
    pass_percent: Optional[int] = None
    max_attempts: Optional[int] = None
    gate_test: Optional[bool] = None
    revision: Optional[int] = None


@router.get("/versions/{version_id}", summary="Редакция курса для редактора")
def get_version(version_id: str, p: Principal = Depends(require_methodist), db: Session = Depends(get_db)) -> dict:
    return _version_full(db, _version(db, version_id))


@router.patch("/versions/{version_id}", summary="Изменить черновик")
def patch_version(version_id: str, body: VersionPatch, p: Principal = Depends(require_methodist),
                  db: Session = Depends(get_db)) -> dict:
    v = _version(db, version_id, draft_only=True)
    if body.revision is not None and body.revision != v.revision:
        released = [x.revision for x in v.course.versions if x.id != v.id]
        if not (1 <= body.revision <= 999):
            raise HTTPException(400, detail={"message": "Номер редакции — от 1 до 999"})
        if released and body.revision <= max(released):
            raise HTTPException(400, detail={"message": "Номер редакции должен быть больше уже опубликованной (%d)" % max(released)})
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(v, key, value.strip() if isinstance(value, str) else value)
    if v.revision == 1 or not any(x.status != "draft" for x in v.course.versions):
        v.course.title, v.course.sop_code, v.course.description = v.title, v.sop_code, v.description
    db.commit()
    return _version_full(db, v)


@router.delete("/versions/{version_id}", summary="Удалить черновик")
def delete_version(version_id: str, request: Request, p: Principal = Depends(require_methodist),
                   db: Session = Depends(get_db)) -> dict:
    v = _version(db, version_id, draft_only=True)
    course = v.course
    db.delete(v)
    db.flush()
    if not db.query(CourseVersion).filter(CourseVersion.course_id == course.id).count():
        course.archived = True
    audit(db, request, p.user.id, "version_delete", "version", version_id)
    db.commit()
    return {"ok": True}


@router.post("/courses/{course_id}/new-version", summary="Новая редакция: копия опубликованной в виде черновика")
def new_version(course_id: str, request: Request, p: Principal = Depends(require_methodist),
                db: Session = Depends(get_db)) -> dict:
    c = db.get(Course, course_id)
    if not c:
        raise HTTPException(404, detail={"message": "Курс не найден"})
    draft = next((v for v in c.versions if v.status == "draft"), None)
    if draft:
        return {"version_id": draft.id, "existing": True}
    src = max((v for v in c.versions if v.status in ("published", "superseded")), key=lambda v: v.revision, default=None)
    if not src:
        raise HTTPException(409, detail={"message": "Нет опубликованной редакции для копирования"})
    v = CourseVersion(id=new_id(), course_id=c.id, revision=max(x.revision for x in c.versions) + 1, status="draft",
                      title=src.title, sop_code=src.sop_code, description=src.description,
                      pass_percent=src.pass_percent, max_attempts=src.max_attempts, gate_test=src.gate_test)
    db.add(v)
    for b in src.blocks:
        db.add(Block(id=new_id(), version_id=v.id, position=b.position, type=b.type, title=b.title,
                     required=b.required, data=json.loads(json.dumps(b.data or {}))))
    for q in src.questions:
        db.add(Question(id=new_id(), version_id=v.id, position=q.position, type=q.type, text=q.text,
                        options=json.loads(json.dumps(q.options or [])), correct=json.loads(json.dumps(q.correct)),
                        points=q.points, criteria=q.criteria))
    audit(db, request, p.user.id, "version_create", "version", v.id, from_revision=src.revision)
    db.commit()
    return {"version_id": v.id, "existing": False}


# ---------- блоки ----------

class BlockIn(BaseModel):
    type: str
    title: str = ""
    required: bool = True
    data: dict[str, Any] = {}


class BlockPatch(BaseModel):
    title: Optional[str] = None
    required: Optional[bool] = None
    data: Optional[dict[str, Any]] = None


class OrderIn(BaseModel):
    ids: list[str]


def _clean_block_data(db: Session, btype: str, data: dict) -> dict:
    if btype == "text":
        return {"text": str(data.get("text") or "")}
    if btype == "link":
        return {"url": str(data.get("url") or "").strip(), "mandatory": bool(data.get("mandatory")),
                "note": str(data.get("note") or ""), "alt_file_id": data.get("alt_file_id") or None}
    if btype == "trainer":
        return {"scenario": data.get("scenario") or {}}
    file_id = data.get("file_id") or None
    if file_id:
        f = db.get(StoredFile, file_id)
        if not f:
            raise HTTPException(400, detail={"message": "Файл не найден"})
        expected = {"pdf": ("pdf",), "video": ("mp4", "m4v", "webm"), "docx": ("docx",), "xlsx": ("xlsx",), "pptx": ("pptx",)}[btype]
        if f.ext not in expected:
            raise HTTPException(400, detail={"message": "Для блока этого вида нужен файл %s" % " / ".join(x.upper() for x in expected)})
    return {"file_id": file_id}


@router.post("/versions/{version_id}/blocks", summary="Добавить блок урока")
def add_block(version_id: str, body: BlockIn, p: Principal = Depends(require_methodist),
              db: Session = Depends(get_db)) -> dict:
    v = _version(db, version_id, draft_only=True)
    if body.type not in BLOCK_TYPES:
        raise HTTPException(400, detail={"message": "Неизвестный вид блока"})
    b = Block(id=new_id(), version_id=v.id, position=len(v.blocks), type=body.type, title=body.title.strip(),
              required=body.required, data=_clean_block_data(db, body.type, body.data))
    if b.type == "link":            # у ссылки один признак обязательности
        b.required = bool(b.data.get("mandatory"))
    db.add(b)
    db.commit()
    return block_out(db, b)


@router.patch("/blocks/{block_id}", summary="Изменить блок")
def patch_block(block_id: str, body: BlockPatch, p: Principal = Depends(require_methodist),
                db: Session = Depends(get_db)) -> dict:
    b = db.get(Block, block_id)
    if not b:
        raise HTTPException(404, detail={"message": "Блок не найден"})
    _version(db, b.version_id, draft_only=True)
    if body.title is not None:
        b.title = body.title.strip()
    if body.required is not None:
        b.required = body.required
    if body.data is not None:
        b.data = _clean_block_data(db, b.type, body.data)
    if b.type == "link":
        b.required = bool((b.data or {}).get("mandatory"))
    db.commit()
    return block_out(db, b)


@router.delete("/blocks/{block_id}", summary="Удалить блок")
def delete_block(block_id: str, p: Principal = Depends(require_methodist), db: Session = Depends(get_db)) -> dict:
    b = db.get(Block, block_id)
    if not b:
        raise HTTPException(404, detail={"message": "Блок не найден"})
    v = _version(db, b.version_id, draft_only=True)
    db.delete(b)
    db.flush()
    db.refresh(v)
    for i, x in enumerate(v.blocks):
        x.position = i
    db.commit()
    return {"ok": True}


@router.post("/versions/{version_id}/blocks/order", summary="Изменить порядок блоков")
def order_blocks(version_id: str, body: OrderIn, p: Principal = Depends(require_methodist),
                 db: Session = Depends(get_db)) -> dict:
    v = _version(db, version_id, draft_only=True)
    by_id = {b.id: b for b in v.blocks}
    if set(body.ids) != set(by_id):
        raise HTTPException(400, detail={"message": "Список блоков устарел, обновите страницу"})
    for i, bid in enumerate(body.ids):
        by_id[bid].position = i
    db.commit()
    db.refresh(v)
    return _version_full(db, v)


# ---------- вопросы ----------

class QuestionIn(BaseModel):
    type: str
    text: str = ""
    options: list[dict[str, Any]] = []
    correct: Any = None
    points: int = 1
    criteria: str = ""


def _clean_question(body: QuestionIn) -> dict:
    if body.type not in QUESTION_TYPES:
        raise HTTPException(400, detail={"message": "Неизвестный тип вопроса"})
    options = []
    for o in body.options:
        oid = str(o.get("id") or new_id()[:8])
        options.append({"id": oid, "text": str(o.get("text") or "").strip()})
    ids = [o["id"] for o in options]
    if len(ids) != len(set(ids)):
        raise HTTPException(400, detail={"message": "Варианты ответа повторяются"})
    correct: Any = None
    if body.type == "single":
        correct = body.correct if body.correct in ids else None
    elif body.type == "multiple":
        correct = [x for x in (body.correct or []) if x in ids]
    elif body.type == "order":
        correct = ids            # эталон — порядок, в котором методист расставил варианты
    else:
        options = []
    return {"type": body.type, "text": body.text.strip(), "options": options, "correct": correct,
            "points": max(1, min(100, int(body.points or 1))), "criteria": body.criteria.strip()}


@router.post("/versions/{version_id}/questions", summary="Добавить вопрос теста")
def add_question(version_id: str, body: QuestionIn, p: Principal = Depends(require_methodist),
                 db: Session = Depends(get_db)) -> dict:
    v = _version(db, version_id, draft_only=True)
    q = Question(id=new_id(), version_id=v.id, position=len(v.questions), **_clean_question(body))
    db.add(q)
    db.commit()
    return question_out(q)


@router.patch("/questions/{question_id}", summary="Изменить вопрос")
def patch_question(question_id: str, body: QuestionIn, p: Principal = Depends(require_methodist),
                   db: Session = Depends(get_db)) -> dict:
    q = db.get(Question, question_id)
    if not q:
        raise HTTPException(404, detail={"message": "Вопрос не найден"})
    _version(db, q.version_id, draft_only=True)
    for key, value in _clean_question(body).items():
        setattr(q, key, value)
    db.commit()
    return question_out(q)


@router.delete("/questions/{question_id}", summary="Удалить вопрос")
def delete_question(question_id: str, p: Principal = Depends(require_methodist), db: Session = Depends(get_db)) -> dict:
    q = db.get(Question, question_id)
    if not q:
        raise HTTPException(404, detail={"message": "Вопрос не найден"})
    v = _version(db, q.version_id, draft_only=True)
    db.delete(q)
    db.flush()
    db.refresh(v)
    for i, x in enumerate(v.questions):
        x.position = i
    db.commit()
    return {"ok": True}


@router.post("/versions/{version_id}/questions/order", summary="Изменить порядок вопросов")
def order_questions(version_id: str, body: OrderIn, p: Principal = Depends(require_methodist),
                    db: Session = Depends(get_db)) -> dict:
    v = _version(db, version_id, draft_only=True)
    by_id = {q.id: q for q in v.questions}
    if set(body.ids) != set(by_id):
        raise HTTPException(400, detail={"message": "Список вопросов устарел, обновите страницу"})
    for i, qid in enumerate(body.ids):
        by_id[qid].position = i
    db.commit()
    db.refresh(v)
    return _version_full(db, v)


# ---------- проверка, предпросмотр, публикация ----------

@router.get("/versions/{version_id}/check", summary="Готовность к публикации и объём пакета")
def check_version(version_id: str, p: Principal = Depends(require_methodist), db: Session = Depends(get_db)) -> dict:
    v = _version(db, version_id)
    result = validate_for_publish(db, v)
    result["package"] = package_summary(db, v)
    prev = _current_published(db, v)
    result["previous"] = _supersede_plan(db, prev) if prev and v.status == "draft" else None
    return result


@router.get("/versions/{version_id}/preview", summary="Курс глазами сотрудника (включая черновик)")
def preview_version(version_id: str, p: Principal = Depends(require_methodist), db: Session = Depends(get_db)) -> dict:
    return build_manifest(db, _version(db, version_id))


def _current_published(db: Session, v: CourseVersion) -> Optional[CourseVersion]:
    return db.query(CourseVersion).filter(CourseVersion.course_id == v.course_id,
                                          CourseVersion.status == "published", CourseVersion.id != v.id).first()


def _passed_user_ids(db: Session, version_id: str) -> set[str]:
    return {a.user_id for a in db.query(Attempt).filter(Attempt.version_id == version_id, Attempt.status == ATTEMPT_PASSED).all()}


def _supersede_plan(db: Session, prev: CourseVersion) -> dict:
    """Кого затронет публикация новой редакции."""
    active = db.query(Assignment).filter(Assignment.version_id == prev.id, Assignment.status == "active").all()
    passed = _passed_user_ids(db, prev.id)
    return {"revision": prev.revision,
            "passed": sum(1 for a in active if a.user_id in passed),
            "not_passed": sum(1 for a in active if a.user_id not in passed)}


class PublishIn(BaseModel):
    reacquaint: bool = True                # назначить повторное ознакомление тем, кто сдал прежнюю редакцию
    due_at: Optional[str] = None


@router.post("/versions/{version_id}/publish", summary="Опубликовать редакцию")
def publish_version(version_id: str, body: PublishIn, request: Request, p: Principal = Depends(require_methodist),
                    db: Session = Depends(get_db)) -> dict:
    v = _version(db, version_id, draft_only=True)
    check = validate_for_publish(db, v)
    if check["errors"]:
        raise HTTPException(422, detail={"code": "not_ready", "message": "Курс не готов к публикации", "errors": check["errors"]})
    prev = _current_published(db, v)
    now = utcnow()
    due = parse_dt(body.due_at)
    moved = reacquainted = 0
    if prev:
        prev.status = "superseded"
        passed = _passed_user_ids(db, prev.id)
        for a in db.query(Assignment).filter(Assignment.version_id == prev.id, Assignment.status == "active").all():
            if a.user_id in passed:
                if body.reacquaint:
                    db.add(Assignment(id=new_id(), course_id=v.course_id, version_id=v.id, user_id=a.user_id,
                                      due_at=due, assigned_by=p.user.id, assigned_at=now, kind="reacquaint",
                                      via_department_id=a.via_department_id))
                    reacquainted += 1
            else:
                # прежняя редакция не сдана — сотрудник получает действующую; уже начатая попытка
                # по старой редакции всё равно будет принята и останется привязанной к ней
                a.status = "superseded"
                db.add(Assignment(id=new_id(), course_id=v.course_id, version_id=v.id, user_id=a.user_id,
                                  due_at=a.due_at or due, assigned_by=p.user.id, assigned_at=now, kind=a.kind,
                                  via_department_id=a.via_department_id))
                moved += 1
    v.status = "published"
    v.published_at = now
    v.published_by = p.user.id
    v.course.title, v.course.sop_code, v.course.description = v.title, v.sop_code, v.description
    audit(db, request, p.user.id, "version_publish", "version", v.id, revision=v.revision,
          moved=moved, reacquainted=reacquainted)
    db.commit()
    return {"version": version_brief(v), "warnings": check["warnings"], "moved": moved, "reacquainted": reacquainted,
            "package": package_summary(db, v)}


# ---------- назначения ----------

class AssignIn(BaseModel):
    version_id: str
    user_ids: list[str] = []
    department_ids: list[str] = []
    due_at: Optional[str] = None


def _assignment_state(db: Session, a: Assignment, attempts: list[Attempt]) -> str:
    if any(x.status == "passed" for x in attempts):
        return "passed"
    if any(x.status == "review" for x in attempts):
        return "review"
    if attempts:
        return "failed"
    started = db.query(BlockProgress).filter(BlockProgress.user_id == a.user_id, BlockProgress.version_id == a.version_id).first()
    return "in_progress" if started else "not_started"


STATE_LABELS = {"passed": "Сдал", "review": "На проверке", "failed": "Не сдал", "in_progress": "Изучает",
                "not_started": "Не начато"}


@router.post("/assignments", summary="Назначить курс сотрудникам и подразделениям")
def assign(body: AssignIn, request: Request, p: Principal = Depends(require_methodist),
           db: Session = Depends(get_db)) -> dict:
    v = _version(db, body.version_id)
    if v.status != "published":
        raise HTTPException(409, detail={"message": "Назначить можно только действующую опубликованную редакцию"})
    due = parse_dt(body.due_at)
    targets: dict[str, Optional[str]] = {}
    for uid in body.user_ids:
        targets.setdefault(uid, None)
    for did in body.department_ids:
        for u in db.query(User).filter(User.department_id == did, User.is_active.is_(True)).all():
            if u.role != "methodist":
                targets.setdefault(u.id, did)
    if not targets:
        raise HTTPException(400, detail={"message": "Выберите сотрудников или подразделение"})
    created, skipped = [], []
    now = utcnow()
    for uid, via in targets.items():
        u = db.get(User, uid)
        if not u or not u.is_active:
            continue
        exists = db.query(Assignment).filter(Assignment.version_id == v.id, Assignment.user_id == uid,
                                             Assignment.status == "active").first()
        if exists:
            skipped.append(u.full_name)
            continue
        db.add(Assignment(id=new_id(), course_id=v.course_id, version_id=v.id, user_id=uid, due_at=due,
                          assigned_by=p.user.id, assigned_at=now, via_department_id=via))
        created.append(u.full_name)
    audit(db, request, p.user.id, "assign", "version", v.id, created=len(created), skipped=len(skipped))
    db.commit()
    return {"created": created, "skipped": skipped}


@router.get("/assignments", summary="Назначения: кому, срок, состояние")
def list_assignments(course_id: str = "", version_id: str = "", kind: str = "", include_inactive: bool = False,
                     p: Principal = Depends(require_methodist), db: Session = Depends(get_db)) -> list[dict]:
    q = db.query(Assignment)
    if course_id:
        q = q.filter(Assignment.course_id == course_id)
    if version_id:
        q = q.filter(Assignment.version_id == version_id)
    if kind:
        q = q.filter(Assignment.kind == kind)
    if not include_inactive:
        q = q.filter(Assignment.status == "active")
    depts = dept_map(db)
    out = []
    for a in q.order_by(Assignment.assigned_at.desc()).all():
        attempts = db.query(Attempt).filter(Attempt.assignment_id == a.id).order_by(Attempt.attempt_no).all()
        state = _assignment_state(db, a, attempts)
        v = a.version
        out.append({
            "id": a.id, "course_id": a.course_id, "version_id": a.version_id, "revision": v.revision,
            "title": v.title, "sop_code": v.sop_code, "kind": a.kind, "status": a.status,
            "due_at": iso(a.due_at), "assigned_at": iso(a.assigned_at),
            "overdue": bool(a.due_at and a.due_at < utcnow() and state not in ("passed",)),
            "user": user_out(a.user, depts), "state": state, "state_label": STATE_LABELS[state],
            "attempts_used": len(attempts), "max_attempts": v.max_attempts,
            "last_attempt": attempt_brief(attempts[-1]) if attempts else None,
            # «изучено X из Y»: материалы этой редакции, отмеченные изученными (с телефона — после синхронизации)
            "blocks_total": len(v.blocks),
            "blocks_done": len({b.block_id for b in db.query(BlockProgress).filter(
                BlockProgress.user_id == a.user_id, BlockProgress.version_id == v.id).all()} & {b.id for b in v.blocks}),
        })
    return out


@router.delete("/assignments/{assignment_id}", summary="Отменить назначение")
def cancel_assignment(assignment_id: str, request: Request, p: Principal = Depends(require_methodist),
                      db: Session = Depends(get_db)) -> dict:
    a = db.get(Assignment, assignment_id)
    if not a:
        raise HTTPException(404, detail={"message": "Назначение не найдено"})
    if db.query(Attempt).filter(Attempt.assignment_id == a.id).first():
        raise HTTPException(409, detail={"message": "По назначению уже есть попытка — отменить нельзя"})
    a.status = "cancelled"
    audit(db, request, p.user.id, "assignment_cancel", "assignment", a.id)
    db.commit()
    return {"ok": True}


# ---------- тренажёр ----------

@router.get("/trainer/scenarios", summary="Готовые сценарии диалогового тренажёра")
def trainer_scenarios(p: Principal = Depends(require_methodist)) -> list[dict]:
    out = []
    for path in sorted((config.ASSETS_DIR / "trainer").glob("*.json")):
        out.append(json.loads(path.read_text(encoding="utf-8")))
    return out
