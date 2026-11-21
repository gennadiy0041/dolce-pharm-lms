from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from .models import (
    ATTEMPT_LABELS, ROLE_LABELS, Attempt, Block, Certificate, CourseVersion, Department, Question, StoredFile, User,
)
from .services.storage import file_info


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def dept_map(db: Session) -> dict[str, Department]:
    return {d.id: d for d in db.query(Department).all()}


def user_out(u: User, depts: Optional[dict[str, Department]] = None) -> dict:
    dept = depts.get(u.department_id) if depts and u.department_id else None
    return {
        "id": u.id, "personnel_no": u.personnel_no, "full_name": u.full_name, "login": u.login, "role": u.role,
        "role_label": ROLE_LABELS.get(u.role, u.role), "department_id": u.department_id,
        "department": dept.name if dept else None, "position": u.position, "is_active": u.is_active,
    }


def version_brief(v: CourseVersion) -> dict:
    return {
        "id": v.id, "course_id": v.course_id, "revision": v.revision, "status": v.status, "title": v.title,
        "sop_code": v.sop_code, "description": v.description, "change_note": v.change_note,
        "pass_percent": v.pass_percent, "max_attempts": v.max_attempts, "gate_test": v.gate_test,
        "created_at": iso(v.created_at), "published_at": iso(v.published_at),
        "blocks_count": len(v.blocks), "questions_count": len(v.questions),
    }


def block_out(db: Session, b: Block) -> dict:
    """Блок для редактора методиста: с подробностями о файлах и ошибках конвертации."""
    data = dict(b.data or {})
    out = {"id": b.id, "type": b.type, "title": b.title, "required": b.required, "position": b.position, "data": data}
    file_id = data.get("file_id") or data.get("alt_file_id")
    if file_id:
        f = db.get(StoredFile, file_id)
        out["file"] = file_info(f)
        if f and f.converted_file_id:
            out["view_file"] = file_info(db.get(StoredFile, f.converted_file_id))
    return out


def question_out(q: Question, with_key: bool = True) -> dict:
    out = {"id": q.id, "type": q.type, "text": q.text, "options": q.options or [], "points": q.points,
           "position": q.position}
    if with_key:
        out["correct"] = q.correct
        out["criteria"] = q.criteria
    return out


def attempt_brief(a: Attempt, cert: Optional[Certificate] = None) -> dict:
    return {
        "id": a.id, "assignment_id": a.assignment_id, "version_id": a.version_id, "attempt_no": a.attempt_no,
        "status": a.status, "status_label": ATTEMPT_LABELS.get(a.status, a.status),
        "score": a.score, "max_score": a.max_score, "percent": a.percent,
        "submitted_at": iso(a.submitted_at), "received_at": iso(a.received_at), "reviewed_at": iso(a.reviewed_at),
        "review_comment": a.review_comment, "source": a.source,
        "certificate_id": cert.id if cert else None, "certificate_number": cert.number if cert else None,
    }
