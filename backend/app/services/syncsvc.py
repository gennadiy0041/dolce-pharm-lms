"""Приём очереди событий с устройства сотрудника.

Каждое событие имеет стабильный ID, созданный на устройстве. Событие обрабатывается в собственной
точке сохранения: сбой одного не откатывает остальные, а повторная отправка уже принятого события
возвращает «duplicate» и ничего не меняет — одна попытка, один результат, одно начисление времени.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import (
    ATTEMPT_PASSED, Assignment, Attempt, BlockProgress, CoursePosition, CourseVersion, LearnSession, SyncBatch,
    SyncEvent, TimeInterval, TrainerResult, User, new_id,
)
from .attempts import finalize_if_ready
from .grading import create_attempt_answers

MAX_INTERVAL = timedelta(hours=4)


class Reject(Exception):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def parse_dt(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _need_dt(payload: dict, key: str) -> datetime:
    dt = parse_dt(payload.get(key))
    if not dt:
        raise Reject("bad_time", "В записи нет корректного времени («%s»)." % key)
    return dt


def _touch_session(db: Session, user: User, batch: SyncBatch, session_id: Optional[str],
                   started: datetime, activity: datetime) -> None:
    if not session_id:
        return
    s = db.get(LearnSession, session_id)
    if s is None:
        s = LearnSession(id=session_id, user_id=user.id, device_id=batch.device_id, source=batch.source,
                         started_at=started, last_activity_at=activity)
        db.add(s)
    elif s.user_id != user.id:
        return
    if not s.last_activity_at or activity > s.last_activity_at:
        s.last_activity_at = activity
    if started < s.started_at:
        s.started_at = started


def _ev_session_start(db: Session, user: User, batch: SyncBatch, p: dict) -> None:
    sid = p.get("session_id")
    if not sid:
        raise Reject("bad_payload", "Нет идентификатора сеанса.")
    at = _need_dt(p, "started_at")
    _touch_session(db, user, batch, sid, at, at)


def _ev_session_end(db: Session, user: User, batch: SyncBatch, p: dict) -> None:
    sid = p.get("session_id")
    if not sid:
        raise Reject("bad_payload", "Нет идентификатора сеанса.")
    at = _need_dt(p, "ended_at")
    _touch_session(db, user, batch, sid, at, at)
    db.flush()
    s = db.get(LearnSession, sid)
    if s and s.user_id == user.id:
        s.ended_at = at


def _ev_interval(db: Session, user: User, batch: SyncBatch, p: dict, event_id: str) -> None:
    start, end = _need_dt(p, "start"), _need_dt(p, "end")
    kind = p.get("kind")
    if kind not in ("platform", "material"):
        raise Reject("bad_payload", "Неизвестный вид интервала времени.")
    if end < start:
        raise Reject("bad_interval", "Конец интервала раньше начала.")
    if end - start > MAX_INTERVAL:
        raise Reject("bad_interval", "Интервал активности длиннее 4 часов не принимается.")
    iid = p.get("interval_id") or event_id
    if db.get(TimeInterval, iid):
        return
    db.add(TimeInterval(
        id=iid, user_id=user.id, device_id=batch.device_id, session_id=p.get("session_id"), kind=kind,
        version_id=p.get("version_id"), block_id=p.get("block_id"), library_item_id=p.get("library_item_id"),
        started_at=start, ended_at=end, source=batch.source, estimated=bool(p.get("estimated")),
    ))
    _touch_session(db, user, batch, p.get("session_id"), start, end)


def _ev_block_done(db: Session, user: User, batch: SyncBatch, p: dict) -> None:
    vid, bid = p.get("version_id"), p.get("block_id")
    if not vid or not bid or not db.get(CourseVersion, vid):
        raise Reject("bad_payload", "Материал не найден.")
    at = _need_dt(p, "at")
    if not db.get(BlockProgress, (user.id, vid, bid)):
        db.add(BlockProgress(user_id=user.id, version_id=vid, block_id=bid, completed_at=at))


def _ev_position(db: Session, user: User, batch: SyncBatch, p: dict) -> None:
    vid, bid = p.get("version_id"), p.get("block_id")
    if not vid or not bid or not db.get(CourseVersion, vid):
        raise Reject("bad_payload", "Материал не найден.")
    at = _need_dt(p, "at")
    row = db.get(CoursePosition, (user.id, vid))
    if row is None:
        db.add(CoursePosition(user_id=user.id, version_id=vid, block_id=bid, updated_at=at))
    elif at >= row.updated_at:
        row.block_id, row.updated_at = bid, at


def _ev_trainer(db: Session, user: User, batch: SyncBatch, p: dict, event_id: str) -> None:
    rid = p.get("result_id") or event_id
    if db.get(TrainerResult, rid):
        return
    vid = p.get("version_id")
    if not vid or not db.get(CourseVersion, vid):
        raise Reject("bad_payload", "Курс тренажёра не найден.")
    path = p.get("path") if isinstance(p.get("path"), list) else []
    db.add(TrainerResult(
        id=rid, user_id=user.id, version_id=vid, block_id=str(p.get("block_id") or "")[:36],
        ending_id=str(p.get("ending_id") or "")[:60], ending_title=str(p.get("ending_title") or "")[:300],
        outcome=str(p.get("outcome") or "")[:12], path=path[:200],
        finished_at=_need_dt(p, "finished_at"), source=batch.source,
    ))


def _ev_attempt(db: Session, user: User, batch: SyncBatch, p: dict) -> Optional[str]:
    attempt_id = p.get("attempt_id")
    if not attempt_id:
        raise Reject("bad_payload", "Нет идентификатора попытки.")
    existing = db.get(Attempt, attempt_id)
    if existing:
        if existing.user_id != user.id:
            raise Reject("foreign_attempt", "Попытка принадлежит другому сотруднику.")
        return attempt_id                        # уже принята раньше — ничего не меняем
    assignment = db.get(Assignment, p.get("assignment_id") or "")
    if not assignment or assignment.user_id != user.id:
        raise Reject("not_assigned", "Курс не назначен этому сотруднику.")
    if assignment.status == "cancelled":
        raise Reject("assignment_cancelled", "Назначение отменено отделом обучения.")
    version = db.get(CourseVersion, p.get("version_id") or "")
    if not version or version.id != assignment.version_id:
        raise Reject("version_mismatch", "Редакция курса в ответах не совпадает с назначенной.")
    if version.status == "draft":
        raise Reject("not_published", "Редакция курса не опубликована.")
    if not version.questions:
        raise Reject("no_test", "В этой редакции курса нет теста.")
    prior = db.query(Attempt).filter(Attempt.assignment_id == assignment.id).all()
    if any(a.status == ATTEMPT_PASSED for a in prior):
        raise Reject("already_passed", "Аттестация по этому назначению уже пройдена.")
    if len(prior) >= version.max_attempts:
        raise Reject("attempts_exceeded", "Использованы все попытки (%d)." % version.max_attempts)
    answers = p.get("answers")
    if not isinstance(answers, dict):
        raise Reject("bad_payload", "Нет ответов.")
    attempt = Attempt(
        id=attempt_id, assignment_id=assignment.id, user_id=user.id, version_id=version.id,
        session_id=p.get("session_id"), device_id=batch.device_id, source=batch.source,
        attempt_no=len(prior) + 1, started_at=parse_dt(p.get("started_at")),
        submitted_at=_need_dt(p, "submitted_at"), received_at=batch.received_at, sync_batch_id=batch.id,
    )
    db.add(attempt)
    db.flush()
    create_attempt_answers(db, attempt, version, answers)
    finalize_if_ready(db, attempt, version)
    return attempt_id


def process_batch(db: Session, user: User, *, device_id: str, source: str, user_agent: str, body: dict) -> dict:
    now = utcnow()
    sent = parse_dt(body.get("sent_at"))
    restored = parse_dt(body.get("restored_at"))
    delay = None
    if sent and restored:
        delay = max(0.0, (sent - restored).total_seconds())
    events = body.get("events") or []
    batch = SyncBatch(
        id=new_id(), user_id=user.id, device_id=(device_id or "")[:64], source=source, received_at=now,
        sent_at_device=sent, restored_at_device=restored, delay_seconds=delay,
        trigger=str(body.get("trigger") or "")[:30], app_state=str(body.get("app_state") or "")[:20],
        user_agent=user_agent[:400], events_total=len(events),
        events_accepted=0, events_duplicate=0, events_rejected=0,
    )
    db.add(batch)
    db.flush()

    results = []
    for ev in events:
        ev_id = str(ev.get("id") or "")
        ev_type = str(ev.get("type") or "")
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        if not ev_id or len(ev_id) > 36:
            results.append({"id": ev_id, "status": "rejected", "code": "bad_id", "reason": "Запись без идентификатора."})
            batch.events_rejected += 1
            continue
        known = db.get(SyncEvent, ev_id)
        if known:
            if known.user_id != user.id:
                results.append({"id": ev_id, "status": "rejected", "code": "foreign_event",
                                "reason": "Запись принадлежит другому сотруднику."})
                batch.events_rejected += 1
            elif known.status == "rejected":
                results.append({"id": ev_id, "status": "rejected", "code": known.reject_code, "reason": known.reject_reason})
                batch.events_rejected += 1
            else:
                results.append({"id": ev_id, "status": "duplicate"})
                batch.events_duplicate += 1
            continue

        # идентификаторы из содержимого записи: строка не длиннее 36 символов, иначе запись отклоняется целиком
        bad_key = next((k for k in ("attempt_id", "assignment_id", "version_id", "session_id", "interval_id",
                                    "result_id", "block_id", "library_item_id")
                        if payload.get(k) is not None and not (isinstance(payload[k], str) and len(payload[k]) <= 36)), None)
        if bad_key:
            reason = "Недопустимый идентификатор в записи («%s»)." % bad_key
            try:
                with db.begin_nested():
                    db.add(SyncEvent(id=ev_id, batch_id=batch.id, user_id=user.id, device_id=batch.device_id,
                                     type=ev_type[:40], device_time=parse_dt(ev.get("device_time")), received_at=now,
                                     payload={}, status="rejected", reject_code="bad_payload", reject_reason=reason))
                    db.flush()
            except IntegrityError:
                pass
            results.append({"id": ev_id, "status": "rejected", "code": "bad_payload", "reason": reason})
            batch.events_rejected += 1
            continue

        record = SyncEvent(id=ev_id, batch_id=batch.id, user_id=user.id, device_id=batch.device_id, type=ev_type[:40],
                           device_time=parse_dt(ev.get("device_time")), received_at=now, payload=payload,
                           version_id=payload.get("version_id"))
        try:
            with db.begin_nested():
                if ev_type == "attempt.submit":
                    record.attempt_id = _ev_attempt(db, user, batch, payload)
                elif ev_type == "time.interval":
                    _ev_interval(db, user, batch, payload, ev_id)
                elif ev_type == "session.start":
                    _ev_session_start(db, user, batch, payload)
                elif ev_type == "session.end":
                    _ev_session_end(db, user, batch, payload)
                elif ev_type == "block.done":
                    _ev_block_done(db, user, batch, payload)
                elif ev_type == "position":
                    _ev_position(db, user, batch, payload)
                elif ev_type == "trainer.result":
                    _ev_trainer(db, user, batch, payload, ev_id)
                else:
                    raise Reject("unknown_type", "Неизвестный тип записи «%s»." % ev_type)
                db.add(record)
                db.flush()
            results.append({"id": ev_id, "status": "accepted"})
            batch.events_accepted += 1
        except Reject as r:
            # отклонение фиксируется, чтобы повторная отправка получала тот же ответ
            rejected = SyncEvent(id=ev_id, batch_id=batch.id, user_id=user.id, device_id=batch.device_id,
                                 type=ev_type[:40], device_time=parse_dt(ev.get("device_time")), received_at=now,
                                 payload=payload, status="rejected", reject_code=r.code, reject_reason=r.reason,
                                 attempt_id=payload.get("attempt_id"), version_id=payload.get("version_id"))
            try:
                with db.begin_nested():
                    db.add(rejected)
                    db.flush()
            except IntegrityError:
                pass
            results.append({"id": ev_id, "status": "rejected", "code": r.code, "reason": r.reason})
            batch.events_rejected += 1
        except DataError:
            # Значение не помещается в столбец (например, идентификатор длиннее 36 символов). Такую запись
            # повторять бессмысленно: отклоняем её одну, остальные записи пакета обрабатываются дальше.
            reason = "Запись содержит недопустимые данные и не может быть сохранена."
            try:
                with db.begin_nested():
                    db.add(SyncEvent(id=ev_id, batch_id=batch.id, user_id=user.id, device_id=batch.device_id,
                                     type=ev_type[:40], device_time=parse_dt(ev.get("device_time")), received_at=now,
                                     payload={}, status="rejected", reject_code="bad_payload", reject_reason=reason))
                    db.flush()
            except (IntegrityError, DataError):
                pass
            results.append({"id": ev_id, "status": "rejected", "code": "bad_payload", "reason": reason})
            batch.events_rejected += 1
        except IntegrityError:
            # Параллельная доставка той же записи из второй вкладки или фоновой задачи: запись уже
            # зафиксирована другим запросом. Любой другой конфликт подтверждать нельзя — иначе
            # устройство удалит запись из очереди, а на сервере её нет. Просим повторить позже.
            again = db.get(SyncEvent, ev_id)
            if again is not None and again.user_id == user.id and again.status == "accepted":
                results.append({"id": ev_id, "status": "duplicate"})
                batch.events_duplicate += 1
            else:
                results.append({"id": ev_id, "status": "retry", "code": "conflict",
                                "reason": "Запись будет отправлена повторно."})
    db.commit()
    return {"server_time": utcnow().isoformat(), "batch_id": batch.id, "results": results}
