"""Проверка ответов. Объективные вопросы сервер проверяет сам по ключу той редакции,
по которой сдавалась попытка. Открытые ответы оценивает методист."""
from __future__ import annotations

import random
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import (
    ATTEMPT_FAILED, ATTEMPT_PASSED, ATTEMPT_REVIEW, Attempt, AttemptAnswer, CourseVersion, Question, new_id,
)


def grade_objective(q: Question, answer: Any) -> tuple[Optional[bool], Optional[float]]:
    """Возвращает (верно?, баллы). Для открытого вопроса — (None, None): нужна ручная проверка."""
    if q.type == "open":
        return None, None
    ok = False
    if q.type == "single":
        ok = isinstance(answer, str) and answer == q.correct
    elif q.type == "multiple":
        ok = isinstance(answer, list) and len(answer) == len(set(answer)) and set(answer) == set(q.correct or [])
    elif q.type == "order":
        ok = isinstance(answer, list) and list(answer) == list(q.correct or [])
    return ok, float(q.points) if ok else 0.0


def shuffled_options(q: Question) -> list[dict]:
    """Варианты для показа сотруднику. Для «последовательности» порядок перемешан детерминированно
    (один и тот же у всех устройств) и гарантированно отличается от эталона."""
    opts = [{"id": o["id"], "text": o["text"]} for o in (q.options or [])]
    if q.type != "order" or len(opts) < 2:
        return opts
    rnd = random.Random(q.id)
    shuffled = opts[:]
    for _ in range(12):
        rnd.shuffle(shuffled)
        if [o["id"] for o in shuffled] != [o["id"] for o in opts]:
            break
    else:
        shuffled = opts[1:] + opts[:1]
    return shuffled


def create_attempt_answers(db: Session, attempt: Attempt, version: CourseVersion, answers: dict[str, Any]) -> None:
    for q in version.questions:
        raw = answers.get(q.id)
        if q.type == "open" and isinstance(raw, str):
            raw = raw.strip()[:5000]
        ok, pts = grade_objective(q, raw)
        db.add(AttemptAnswer(
            id=new_id(), attempt_id=attempt.id, question_id=q.id, answer=raw,
            is_correct=ok, points=pts, max_points=float(q.points),
        ))
    db.flush()


def recompute_attempt(db: Session, attempt: Attempt, version: CourseVersion) -> bool:
    """Пересчитывает баллы. Возвращает True, если попытка получила итог (все ответы оценены)."""
    rows = db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id == attempt.id).all()
    by_q = {q.id: q for q in version.questions}
    auto = sum(a.points or 0 for a in rows if by_q.get(a.question_id) and by_q[a.question_id].type != "open")
    manual = sum(a.points or 0 for a in rows if by_q.get(a.question_id) and by_q[a.question_id].type == "open")
    max_score = sum(a.max_points for a in rows)
    pending = [a for a in rows if a.points is None]
    attempt.auto_points = auto
    attempt.manual_points = manual
    attempt.score = auto + manual
    attempt.max_score = max_score
    if pending:
        attempt.status = ATTEMPT_REVIEW
        attempt.percent = None
        attempt.finalized_at = None
        return False
    percent = round(100.0 * attempt.score / max_score, 1) if max_score > 0 else 100.0
    attempt.percent = percent
    attempt.status = ATTEMPT_PASSED if percent >= version.pass_percent else ATTEMPT_FAILED
    attempt.finalized_at = utcnow()
    return True
