"""Кабинет сотрудника: назначения, пакет для работы без сети, приём очереди, библиотека."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..models import (
    Assignment, AssessmentRater, Attempt, BlockProgress, Certificate, CoursePosition, CourseVersion, LibraryItem,
    StoredFile, SyncEvent, TrainerResult, new_id,
)
from ..security import Principal, audit, current_principal, require_methodist
from ..serializers import attempt_brief, iso
from ..services.packaging import build_manifest, package_summary
from ..services import libsearch
from ..services.storage import OFFICE, UploadError, abs_path, file_info, store_stream
from ..services.syncsvc import process_batch

router = APIRouter(prefix="/api", tags=["Кабинет сотрудника"])


def _assignment_out(db: Session, a: Assignment, certs: dict[str, Certificate]) -> dict:
    v = a.version
    attempts = db.query(Attempt).filter(Attempt.assignment_id == a.id).order_by(Attempt.attempt_no).all()
    if any(x.status == "passed" for x in attempts):
        state = "passed"
    elif any(x.status == "review" for x in attempts):
        state = "review"
    elif attempts and len(attempts) >= v.max_attempts:
        state = "exhausted"
    elif attempts:
        state = "failed"
    else:
        state = "todo"
    done = [r.block_id for r in db.query(BlockProgress).filter(
        BlockProgress.user_id == a.user_id, BlockProgress.version_id == v.id).all()]
    pos = db.get(CoursePosition, (a.user_id, v.id))
    return {
        "id": a.id, "course_id": a.course_id, "version_id": v.id, "kind": a.kind, "status": a.status,
        "due_at": iso(a.due_at), "assigned_at": iso(a.assigned_at), "state": state,
        "version": {
            "id": v.id, "title": v.title, "sop_code": v.sop_code, "revision": v.revision, "status": v.status,
            "description": v.description, "change_note": v.change_note, "pass_percent": v.pass_percent,
            "max_attempts": v.max_attempts, "gate_test": v.gate_test,
            "blocks_count": len(v.blocks), "questions_count": len(v.questions),
            "required_block_ids": [b.id for b in v.blocks if b.required],
        },
        "package": package_summary(db, v),
        "attempts": [attempt_brief(x, certs.get(x.id)) for x in attempts],
        "completed_block_ids": done,
        "last_block_id": pos.block_id if pos else None,
    }


@router.get("/me/state", summary="Мои назначения, результаты и документы")
def my_state(p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    user = p.user
    certs_all = db.query(Certificate).filter(Certificate.user_id == user.id).order_by(Certificate.issued_at.desc()).all()
    certs = {c.attempt_id: c for c in certs_all}
    rows = db.query(Assignment).filter(Assignment.user_id == user.id, Assignment.status != "cancelled") \
        .order_by(Assignment.assigned_at.desc()).all()
    assignments = []
    for a in rows:
        # Назначение, заменённое новой редакцией, остаётся в ответе, если сотрудник успел с ним поработать:
        # сдавал тест или открывал материалы. Тогда приложение покажет «Редакция заменена новой»,
        # а не «Задание не найдено» посреди урока.
        touched = a.status == "active" \
            or db.query(Attempt).filter(Attempt.assignment_id == a.id).first() is not None \
            or db.get(CoursePosition, (a.user_id, a.version_id)) is not None \
            or db.query(BlockProgress).filter(BlockProgress.user_id == a.user_id,
                                              BlockProgress.version_id == a.version_id).first() is not None
        if touched:
            assignments.append(_assignment_out(db, a, certs))
    versions = {c.version_id: db.get(CourseVersion, c.version_id) for c in certs_all}
    pending_assessments = db.query(AssessmentRater).filter(
        AssessmentRater.rater_id == user.id, AssessmentRater.submitted_at.is_(None)).count()
    trainer = db.query(TrainerResult).filter(TrainerResult.user_id == user.id).order_by(TrainerResult.finished_at.desc()).all()
    return {
        "server_time": utcnow().isoformat(),
        "assignments": assignments,
        "certificates": [{
            "id": c.id, "number": c.number, "issued_at": iso(c.issued_at), "attempt_id": c.attempt_id,
            "file_id": c.file_id, "title": versions[c.version_id].title, "sop_code": versions[c.version_id].sop_code,
            "revision": versions[c.version_id].revision,
        } for c in certs_all],
        "pending_assessments": pending_assessments,
        "trainer_results": [{
            "id": t.id, "version_id": t.version_id, "block_id": t.block_id, "ending_id": t.ending_id,
            "ending_title": t.ending_title, "outcome": t.outcome, "finished_at": iso(t.finished_at),
        } for t in trainer],
    }


@router.get("/me/packages/{version_id}", summary="Манифест пакета редакции для скачивания")
def my_package(version_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    v = db.get(CourseVersion, version_id)
    if not v:
        raise HTTPException(404, detail={"message": "Редакция курса не найдена"})
    if not p.is_methodist:
        if v.status == "draft":
            raise HTTPException(403, detail={"code": "forbidden", "message": "Курс не опубликован"})
        allowed = db.query(Assignment).filter(
            Assignment.user_id == p.user.id, Assignment.version_id == v.id, Assignment.status != "cancelled").first()
        if not allowed:
            raise HTTPException(403, detail={"code": "forbidden", "message": "Курс вам не назначен"})
    return build_manifest(db, v)


class SyncIn(BaseModel):
    device_id: str = ""
    sent_at: Optional[str] = None
    restored_at: Optional[str] = None
    trigger: str = ""
    app_state: str = ""
    events: list[dict[str, Any]] = []


@router.post("/sync/events", summary="Приём очереди событий с устройства (идемпотентно)")
def sync_events(body: SyncIn, request: Request, p: Principal = Depends(current_principal),
                db: Session = Depends(get_db)) -> dict:
    if len(body.events) > 500:
        raise HTTPException(413, detail={"message": "Слишком много записей в одной отправке"})
    return process_batch(
        db, p.user, device_id=body.device_id or p.token.device_id, source=p.token.device_kind,
        user_agent=request.headers.get("user-agent", ""), body=body.model_dump(),
    )


class IdsIn(BaseModel):
    ids: list[str]


@router.post("/sync/status", summary="Какие записи сервер уже зафиксировал")
def sync_status(body: IdsIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    if not body.ids:
        return {"known": {}}
    rows = db.query(SyncEvent).filter(SyncEvent.id.in_(body.ids[:500]), SyncEvent.user_id == p.user.id).all()
    return {"known": {r.id: r.status for r in rows}}


# ---------- библиотека ----------

def _library_view(db: Session, item: LibraryItem) -> Optional[StoredFile]:
    f = item.file
    if f.ext in OFFICE:
        return db.get(StoredFile, f.converted_file_id) if f.converted_file_id else None
    return f


def _library_text(db: Session, item: LibraryItem) -> str:
    view = _library_view(db, item)
    if not view or view.ext != "pdf":
        return ""
    return libsearch.document_text(abs_path(view), view.sha256)


def _library_out(db: Session, item: LibraryItem, text: Optional[str] = None) -> dict:
    f = item.file
    view = _library_view(db, item)

    def ref(x: Optional[StoredFile]) -> Optional[dict]:
        if not x:
            return None
        return {"id": x.id, "sha256": x.sha256, "size": x.size, "mime": x.mime, "name": x.original_name,
                "ext": x.ext, "pages": x.pages}

    return {
        "id": item.id, "title": item.title, "sop_code": item.sop_code, "category": item.category,
        "description": item.description, "updated_at": iso(item.updated_at),
        "file": ref(f), "view": ref(view),
        "size": f.size + (view.size if view and view.id != f.id else 0),
        "conversion_status": f.conversion_status, "conversion_error": f.conversion_error,
        # текст документа — для поиска без сети на телефоне (тот же алгоритм, что и здесь)
        "text": (text if text is not None else _library_text(db, item))[:libsearch.TEXT_LIMIT],
    }


@router.get("/library", summary="Библиотека: поиск по словам в названии, коде СОП, категории, описании и тексте документа")
def library(q: str = "", category: str = "", p: Principal = Depends(current_principal),
            db: Session = Depends(get_db)) -> dict:
    items = db.query(LibraryItem).filter(LibraryItem.is_active.is_(True)).order_by(LibraryItem.title).all()
    terms = libsearch.query_terms(q)
    found = []
    for item in items:
        if category and item.category != category:
            continue
        text = _library_text(db, item)
        weight = libsearch.score({"title": item.title, "sop_code": item.sop_code, "category": item.category,
                                  "description": item.description, "text": text}, terms)
        if weight is None:
            continue
        row = _library_out(db, item, text)
        if terms:
            row["score"] = weight
            visible = libsearch.score({"title": item.title, "sop_code": item.sop_code, "category": item.category,
                                       "description": item.description}, terms)
            # отрывок из текста — только когда слов нет ни в названии, ни в описании
            row["snippet"] = libsearch.snippet(text, terms) if visible is None else None
        found.append(row)
    if terms:
        found.sort(key=lambda r: -r["score"])           # порядок по названию сохраняется при равном весе
    categories = sorted({i.category for i in items if i.category})
    return {"items": found, "categories": categories}


@router.post("/library", summary="Добавить справочник в библиотеку")
def library_add(request: Request, title: str = Form(...), sop_code: str = Form(""), category: str = Form(""),
                description: str = Form(""), file: UploadFile = File(...),
                p: Principal = Depends(require_methodist), db: Session = Depends(get_db)) -> dict:
    if not title.strip():
        raise HTTPException(400, detail={"message": "Укажите название"})
    try:
        stored = store_stream(db, file.file, file.filename or "file", p.user.id)
    except UploadError as exc:
        db.rollback()
        raise HTTPException(400, detail={"code": "bad_file", "message": str(exc)})
    item = LibraryItem(id=new_id(), title=title.strip(), sop_code=sop_code.strip(), category=category.strip(),
                       description=description.strip(), file_id=stored.id, created_by=p.user.id)
    db.add(item)
    audit(db, request, p.user.id, "library_add", "library", item.id)
    db.commit()
    db.refresh(item)
    return _library_out(db, item)      # заодно извлекается и запоминается текст документа для поиска


class LibraryPatch(BaseModel):
    title: Optional[str] = None
    sop_code: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None


@router.patch("/library/{item_id}", summary="Изменить описание справочника")
def library_patch(item_id: str, body: LibraryPatch, p: Principal = Depends(require_methodist),
                  db: Session = Depends(get_db)) -> dict:
    item = db.get(LibraryItem, item_id)
    if not item:
        raise HTTPException(404, detail={"message": "Материал не найден"})
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(item, key, value.strip())
    item.updated_at = utcnow()
    db.commit()
    return _library_out(db, item)


@router.delete("/library/{item_id}", summary="Убрать справочник из библиотеки")
def library_delete(item_id: str, request: Request, p: Principal = Depends(require_methodist),
                   db: Session = Depends(get_db)) -> dict:
    item = db.get(LibraryItem, item_id)
    if not item:
        raise HTTPException(404, detail={"message": "Материал не найден"})
    item.is_active = False
    audit(db, request, p.user.id, "library_delete", "library", item.id)
    db.commit()
    return {"ok": True}
