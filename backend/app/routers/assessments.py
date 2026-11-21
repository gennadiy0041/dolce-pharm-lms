"""Оценка 180/360. В режиме 180° — самооценка и руководитель, в режиме 360° — ещё и коллеги.

Правило расчёта: по каждой компетенции берутся только выставленные оценки (1–5).
Пропущенная оценка не считается нулём и не входит ни в среднее, ни в число ответов.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..models import Assessment, AssessmentRater, Competency, Department, User, new_id
from ..security import Principal, audit, current_principal, require_methodist
from ..serializers import dept_map, iso, user_out

router = APIRouter(prefix="/api", tags=["Оценка 180/360"])

ROLE_LABELS = {"self": "Самооценка", "manager": "Руководитель", "peer": "Коллега"}


def _avg(values: list[int]) -> Optional[float]:
    return round(sum(values) / len(values), 2) if values else None


def _score(r: AssessmentRater, cid: str) -> Optional[int]:
    v = (r.scores or {}).get(cid)
    return v if isinstance(v, int) and 1 <= v <= 5 else None


def summary(db: Session, a: Assessment) -> dict:
    comps = {c.id: c for c in db.query(Competency).filter(Competency.id.in_(a.competency_ids or [])).all()}
    depts = dept_map(db)
    submitted = [r for r in a.raters if r.submitted_at]
    rows = []
    for cid in a.competency_ids or []:
        c = comps.get(cid)
        if not c:
            continue
        self_s = [s for r in submitted if r.role == "self" for s in [_score(r, cid)] if s is not None]
        mgr_s = [s for r in submitted if r.role == "manager" for s in [_score(r, cid)] if s is not None]
        peer_s = [s for r in submitted if r.role == "peer" for s in [_score(r, cid)] if s is not None]
        others = mgr_s + peer_s
        rows.append({
            "competency_id": cid, "name": c.name, "description": c.description,
            "self": self_s[0] if self_s else None, "manager": _avg(mgr_s),
            "peers_avg": _avg(peer_s), "peers_count": len(peer_s),
            "others_avg": _avg(others), "answers_count": len(self_s) + len(others),
            "gap": round(self_s[0] - _avg(others), 2) if self_s and others else None,
        })
    # комментарии: самооценка и руководитель — с ролью, коллеги — без имён (иначе коллеги пишут осторожнее)
    order = {"self": 0, "manager": 1, "peer": 2}
    comments = [{"role": r.role, "role_label": ROLE_LABELS[r.role], "text": r.comment,
                 "submitted_at": iso(r.submitted_at)}
                for r in sorted(submitted, key=lambda r: (order.get(r.role, 3), r.submitted_at)) if (r.comment or "").strip()]
    return {
        "id": a.id, "title": a.title, "mode": a.mode, "status": a.status, "created_at": iso(a.created_at),
        "subject": user_out(a.subject, depts),
        "raters": [{"id": r.id, "role": r.role, "role_label": ROLE_LABELS[r.role], "full_name": r.rater.full_name,
                    "rater_id": r.rater_id, "submitted_at": iso(r.submitted_at)} for r in a.raters],
        "expected": len(a.raters), "received": len(submitted),
        "rows": rows, "comments": comments,
        "explanation": (
            "Самооценка и оценка руководителя показаны как есть. «Среднее коллег» — среднее арифметическое оценок "
            "коллег, которые выставили оценку по этой компетенции. «Оценка окружения» — среднее по руководителю и "
            "коллегам вместе. «Расхождение» — самооценка минус оценка окружения. Пропуск не считается нулём: "
            "он не входит ни в среднее, ни в число ответов."),
    }


@router.get("/competencies", summary="Справочник компетенций")
def competencies(p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> list[dict]:
    return [{"id": c.id, "name": c.name, "description": c.description}
            for c in db.query(Competency).order_by(Competency.name).all()]


class CompetencyIn(BaseModel):
    name: str
    description: str = ""


@router.post("/competencies", summary="Добавить компетенцию")
def add_competency(body: CompetencyIn, p: Principal = Depends(require_methodist), db: Session = Depends(get_db)) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(400, detail={"message": "Укажите название компетенции"})
    if db.query(Competency).filter(Competency.name == name).first():
        raise HTTPException(409, detail={"message": "Такая компетенция уже есть"})
    c = Competency(id=new_id(), name=name, description=body.description.strip())
    db.add(c)
    db.commit()
    return {"id": c.id, "name": c.name, "description": c.description}


class AssessmentIn(BaseModel):
    subject_id: str
    mode: str = "360"
    title: str = ""
    competency_ids: list[str]
    manager_id: Optional[str] = None
    peer_ids: list[str] = []


@router.get("/assessments", summary="Все оценки 180/360 (методист)")
def list_assessments(p: Principal = Depends(require_methodist), db: Session = Depends(get_db)) -> list[dict]:
    return [summary(db, a) for a in db.query(Assessment).order_by(Assessment.created_at.desc()).all()]


@router.post("/assessments", summary="Запустить оценку 180° или 360°")
def create_assessment(body: AssessmentIn, request: Request, p: Principal = Depends(require_methodist),
                      db: Session = Depends(get_db)) -> dict:
    subject = db.get(User, body.subject_id)
    if not subject:
        raise HTTPException(400, detail={"message": "Выберите сотрудника"})
    if body.mode not in ("180", "360"):
        raise HTTPException(400, detail={"message": "Режим — 180 или 360"})
    comp_ids = [c for c in body.competency_ids if db.get(Competency, c)]
    if not comp_ids:
        raise HTTPException(400, detail={"message": "Выберите хотя бы одну компетенцию"})
    manager_id = body.manager_id
    if not manager_id and subject.department_id:
        dept = db.get(Department, subject.department_id)
        manager_id = dept.manager_id if dept else None
    if not manager_id or manager_id == subject.id or not db.get(User, manager_id):
        raise HTTPException(400, detail={"message": "Укажите руководителя, который оценит сотрудника"})
    peers = [x for x in dict.fromkeys(body.peer_ids) if x not in (subject.id, manager_id) and db.get(User, x)]
    if body.mode == "360" and not peers:
        raise HTTPException(400, detail={"message": "Для оценки 360° добавьте хотя бы одного коллегу"})
    a = Assessment(id=new_id(), title=body.title.strip() or "Оценка %s°: %s" % (body.mode, subject.full_name),
                   subject_id=subject.id, mode=body.mode, created_by=p.user.id, competency_ids=comp_ids)
    db.add(a)
    db.add(AssessmentRater(id=new_id(), assessment_id=a.id, rater_id=subject.id, role="self", scores={}))
    db.add(AssessmentRater(id=new_id(), assessment_id=a.id, rater_id=manager_id, role="manager", scores={}))
    if body.mode == "360":
        for pid in peers:
            db.add(AssessmentRater(id=new_id(), assessment_id=a.id, rater_id=pid, role="peer", scores={}))
    audit(db, request, p.user.id, "assessment_create", "assessment", a.id, mode=body.mode)
    db.commit()
    db.refresh(a)
    return summary(db, a)


@router.get("/assessments/{assessment_id}", summary="Сводка по оценке")
def get_assessment(assessment_id: str, p: Principal = Depends(require_methodist), db: Session = Depends(get_db)) -> dict:
    a = db.get(Assessment, assessment_id)
    if not a:
        raise HTTPException(404, detail={"message": "Оценка не найдена"})
    return summary(db, a)


@router.post("/assessments/{assessment_id}/close", summary="Завершить сбор ответов")
def close_assessment(assessment_id: str, request: Request, p: Principal = Depends(require_methodist),
                     db: Session = Depends(get_db)) -> dict:
    a = db.get(Assessment, assessment_id)
    if not a:
        raise HTTPException(404, detail={"message": "Оценка не найдена"})
    if a.status != "closed":
        a.status = "closed"
        audit(db, request, p.user.id, "assessment_close", "assessment", a.id,
              received=sum(1 for r in a.raters if r.submitted_at), expected=len(a.raters))
        db.commit()
    return summary(db, a)


# ---------- участник опроса ----------

@router.get("/me/assessments", summary="Опросы, в которых я оцениваю")
def my_assessment_tasks(p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> list[dict]:
    out = []
    rows = db.query(AssessmentRater).filter(AssessmentRater.rater_id == p.user.id).all()
    for r in rows:
        a = r.assessment
        comps = {c.id: c for c in db.query(Competency).filter(Competency.id.in_(a.competency_ids or [])).all()}
        out.append({
            "rater_row_id": r.id, "assessment_id": a.id, "title": a.title, "mode": a.mode, "status": a.status,
            "role": r.role, "role_label": ROLE_LABELS[r.role],
            "subject_name": a.subject.full_name, "subject_position": a.subject.position,
            "submitted_at": iso(r.submitted_at), "scores": r.scores or {}, "comment": r.comment,
            "competencies": [{"id": cid, "name": comps[cid].name, "description": comps[cid].description}
                             for cid in a.competency_ids if cid in comps],
            "created_at": iso(a.created_at),
        })
    out.sort(key=lambda x: (x["submitted_at"] is not None, x["created_at"]), reverse=False)
    return out


class ResponseIn(BaseModel):
    scores: dict[str, Optional[int]]
    comment: str = ""


@router.post("/me/assessments/{rater_row_id}", summary="Отправить или изменить свои оценки")
def submit_response(rater_row_id: str, body: ResponseIn, request: Request,
                    p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    r = db.get(AssessmentRater, rater_row_id)
    if not r or r.rater_id != p.user.id:
        raise HTTPException(404, detail={"message": "Опрос не найден"})
    a = r.assessment
    if a.status != "open":
        raise HTTPException(409, detail={"message": "Сбор ответов завершён"})
    clean: dict[str, Optional[int]] = {}
    for cid in a.competency_ids or []:
        v = body.scores.get(cid)
        if v is None:
            clean[cid] = None
        elif isinstance(v, int) and 1 <= v <= 5:
            clean[cid] = v
        else:
            raise HTTPException(400, detail={"message": "Оценка — целое число от 1 до 5 или пропуск"})
    if all(v is None for v in clean.values()):
        raise HTTPException(400, detail={"message": "Поставьте хотя бы одну оценку"})
    r.scores = clean
    r.comment = body.comment.strip()[:2000]
    r.submitted_at = utcnow()
    audit(db, request, p.user.id, "assessment_response", "assessment", a.id, role=r.role)
    db.commit()
    return {"ok": True, "submitted_at": iso(r.submitted_at)}
