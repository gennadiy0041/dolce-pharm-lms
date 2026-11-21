"""Манифест пакета редакции курса для работы без сети и проверка готовности к публикации."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models import FILE_BLOCK_TYPES, Block, CourseVersion, StoredFile
from .grading import shuffled_options
from .storage import OFFICE


def _file_ref(f: StoredFile) -> dict:
    return {"id": f.id, "sha256": f.sha256, "size": f.size, "mime": f.mime, "name": f.original_name,
            "ext": f.ext, "pages": f.pages}


def _view_of(db: Session, f: Optional[StoredFile]) -> Optional[StoredFile]:
    """Что показывать на экране: PDF как есть, видео как есть, Office — PDF-копию."""
    if not f:
        return None
    if f.ext in OFFICE:
        return db.get(StoredFile, f.converted_file_id) if f.converted_file_id else None
    return f


def block_payload(db: Session, b: Block, files: dict[str, dict]) -> dict:
    data = b.data or {}
    out: dict[str, Any] = {"id": b.id, "type": b.type, "title": b.title, "required": b.required, "position": b.position}

    def attach(file_id: Optional[str]) -> None:
        src = db.get(StoredFile, file_id) if file_id else None
        if not src:
            return
        out["file"] = _file_ref(src)
        files[src.id] = out["file"]
        view = _view_of(db, src)
        if view:
            out["view"] = _file_ref(view)
            files[view.id] = out["view"]

    if b.type == "text":
        out["text"] = data.get("text", "")
    elif b.type == "link":
        out["url"] = data.get("url", "")
        out["mandatory"] = bool(data.get("mandatory"))
        out["note"] = data.get("note", "")
        attach(data.get("alt_file_id"))
        out["needs_internet"] = "view" not in out
    elif b.type in FILE_BLOCK_TYPES:
        attach(data.get("file_id"))
    elif b.type == "trainer":
        out["scenario"] = data.get("scenario") or {}
    return out


def build_manifest(db: Session, v: CourseVersion) -> dict:
    files: dict[str, dict] = {}
    blocks = [block_payload(db, b, files) for b in v.blocks]
    questions = [{
        "id": q.id, "type": q.type, "text": q.text, "points": q.points, "position": q.position,
        "options": shuffled_options(q) if q.type != "open" else [],
    } for q in v.questions]
    # один и тот же файл может использоваться дважды — на телефон он скачивается один раз
    unique: dict[str, dict] = {}
    for ref in files.values():
        unique.setdefault(ref["sha256"], ref)
    body = {
        "format": 1,
        "version_id": v.id, "course_id": v.course_id, "title": v.title, "sop_code": v.sop_code,
        "revision": v.revision, "description": v.description, "change_note": v.change_note,
        "status": v.status, "published_at": v.published_at.isoformat() if v.published_at else None,
        "pass_percent": v.pass_percent, "max_attempts": v.max_attempts, "gate_test": v.gate_test,
        "blocks": blocks, "questions": questions, "files": list(unique.values()),
    }
    # Контрольная сумма считается только по содержимому. Состояние редакции (действует / заменена)
    # в неё не входит: иначе после выхода новой редакции телефоны решили бы, что старый пакет «обновился».
    hashed = {k: val for k, val in body.items() if k != "status"}
    canonical = json.dumps(hashed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    body["content_size"] = len(canonical)
    body["total_size"] = len(canonical) + sum(f["size"] for f in unique.values())
    body["manifest_hash"] = hashlib.sha256(canonical).hexdigest()
    return body


def package_summary(db: Session, v: CourseVersion) -> dict:
    m = build_manifest(db, v)
    return {"total_size": m["total_size"], "manifest_hash": m["manifest_hash"], "files": len(m["files"]),
            "blocks": len(m["blocks"]), "questions": len(m["questions"])}


def validate_for_publish(db: Session, v: CourseVersion) -> dict:
    """Ошибки блокируют публикацию, предупреждения — нет."""
    errors: list[str] = []
    warnings: list[str] = []
    if not v.title.strip():
        errors.append("Укажите название курса.")
    if not v.blocks:
        errors.append("Добавьте хотя бы один материал урока.")
    for i, b in enumerate(v.blocks, 1):
        name = "Блок %d «%s»" % (i, b.title or b.type)
        data = b.data or {}
        if b.type == "text":
            if not (data.get("text") or "").strip():
                errors.append("%s: пустой текст." % name)
        elif b.type == "link":
            url = (data.get("url") or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                errors.append("%s: укажите адрес ссылки, начиная с http:// или https://." % name)
            alt = db.get(StoredFile, data.get("alt_file_id")) if data.get("alt_file_id") else None
            alt_ok = bool(alt) and (alt.ext not in OFFICE or alt.conversion_status == "ok")
            # обязательной считается ссылка, отмеченная любым из двух способов
            if (data.get("mandatory") or b.required) and not alt_ok:
                errors.append("%s: обязательная внешняя ссылка недоступна без сети. Прикрепите локальную копию "
                              "материала или сделайте ссылку справочной." % name)
            elif not alt_ok:
                warnings.append("%s: справочная ссылка без локальной копии — у сотрудника будет пометка «Нужен интернет»." % name)
        elif b.type in FILE_BLOCK_TYPES:
            f = db.get(StoredFile, data.get("file_id")) if data.get("file_id") else None
            if not f:
                errors.append("%s: файл не загружен." % name)
            elif f.ext in OFFICE and f.conversion_status != "ok":
                errors.append("%s: нет читаемой копии для работы без сети (%s). Исправьте файл и загрузите снова "
                              "или приложите PDF." % (name, f.conversion_error or "ошибка конвертации"))
            elif f.ext in OFFICE and f.conversion_warnings:
                warnings.append("%s: %s" % (name, "; ".join(f.conversion_warnings)))
        elif b.type == "trainer":
            sc = data.get("scenario") or {}
            if not sc.get("nodes"):
                errors.append("%s: сценарий тренажёра пуст." % name)
    for i, q in enumerate(v.questions, 1):
        name = "Вопрос %d" % i
        if not q.text.strip():
            errors.append("%s: нет текста вопроса." % name)
        if q.points < 1:
            errors.append("%s: баллы должны быть не меньше 1." % name)
        opts = q.options or []
        ids = [o.get("id") for o in opts]
        if q.type in ("single", "multiple", "order"):
            if len(opts) < 2:
                errors.append("%s: нужно минимум два варианта." % name)
            if any(not (o.get("text") or "").strip() for o in opts):
                errors.append("%s: есть пустой вариант ответа." % name)
        if q.type == "single" and q.correct not in ids:
            errors.append("%s: отметьте правильный вариант." % name)
        if q.type == "multiple":
            c = q.correct or []
            if not c or any(x not in ids for x in c):
                errors.append("%s: отметьте правильные варианты." % name)
        if q.type == "order" and list(q.correct or []) != ids:
            errors.append("%s: порядок вариантов не сохранён." % name)
        if q.type == "open" and not q.criteria.strip():
            warnings.append("%s: не заданы критерии проверки открытого ответа." % name)
    if not v.questions:
        warnings.append("В курсе нет теста: аттестация и сертификат формироваться не будут.")
    if not (1 <= v.pass_percent <= 100):
        errors.append("Проходной процент должен быть от 1 до 100.")
    if v.max_attempts < 1:
        errors.append("Число попыток должно быть не меньше 1.")
    return {"errors": errors, "warnings": warnings}
