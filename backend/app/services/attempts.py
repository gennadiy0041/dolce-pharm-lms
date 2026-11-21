"""Итог попытки и автоматическая выдача сертификата."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import ATTEMPT_PASSED, Attempt, Certificate, CourseVersion, Department, User, new_id
from .grading import recompute_attempt
from .pdfdocs import build_certificate_pdf
from .storage import store_bytes_as


def _next_certificate_number(db: Session) -> str:
    # Номера выдаются строго по одному: две попытки, сданные в одну секунду (смена возвращается
    # в зону Wi-Fi одновременно), не должны получить один и тот же номер. Блокировка держится
    # до конца транзакции.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("select pg_advisory_xact_lock(815001)"))
    year = utcnow().year
    seq = db.query(Certificate).count() + 1
    while True:
        number = "DP-%d-%05d" % (year, seq)
        if not db.query(Certificate).filter(Certificate.number == number).first():
            return number
        seq += 1


def issue_certificate(db: Session, attempt: Attempt) -> Certificate:
    existing = db.query(Certificate).filter(Certificate.attempt_id == attempt.id).first()
    if existing:
        return existing
    user = db.get(User, attempt.user_id)
    version = db.get(CourseVersion, attempt.version_id)
    dept = db.get(Department, user.department_id) if user.department_id else None
    number = _next_certificate_number(db)
    issued = utcnow()
    pdf = build_certificate_pdf(
        number=number, full_name=user.full_name, personnel_no=user.personnel_no,
        department=dept.name if dept else "", course_title=version.title, sop_code=version.sop_code,
        revision=version.revision, attempt_date=attempt.submitted_at, issued_at=issued,
        score=attempt.score, max_score=attempt.max_score, percent=attempt.percent or 0,
        pass_percent=version.pass_percent,
    )
    stored = store_bytes_as(db, pdf, "Сертификат %s.pdf" % number, "application/pdf", "certificate")
    cert = Certificate(id=new_id(), number=number, attempt_id=attempt.id, user_id=user.id,
                       version_id=version.id, issued_at=issued, file_id=stored.id)
    db.add(cert)
    db.flush()
    return cert


def finalize_if_ready(db: Session, attempt: Attempt, version: Optional[CourseVersion] = None) -> bool:
    """Пересчитывает попытку; при положительном итоге выдаёт сертификат. True — итог выставлен."""
    version = version or db.get(CourseVersion, attempt.version_id)
    done = recompute_attempt(db, attempt, version)
    if done and attempt.status == ATTEMPT_PASSED:
        issue_certificate(db, attempt)
    return done
