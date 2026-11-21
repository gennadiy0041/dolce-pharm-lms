"""Модель данных LMS.

Идентификаторы — строковые UUID. Попытки, интервалы времени, сессии и события очереди получают ID
на устройстве сотрудника: повторная отправка той же записи не создаёт дубликат.
Все моменты времени хранятся в UTC с часовым поясом.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow


def new_id() -> str:
    return str(uuid.uuid4())


ID = String(36)
TS = DateTime(timezone=True)

ROLE_EMPLOYEE = "employee"
ROLE_MANAGER = "manager"
ROLE_METHODIST = "methodist"
ROLES = (ROLE_EMPLOYEE, ROLE_MANAGER, ROLE_METHODIST)
ROLE_LABELS = {ROLE_EMPLOYEE: "Сотрудник", ROLE_MANAGER: "Руководитель", ROLE_METHODIST: "Методист"}

BLOCK_TYPES = ("text", "link", "pdf", "video", "docx", "xlsx", "pptx", "trainer")
FILE_BLOCK_TYPES = ("pdf", "video", "docx", "xlsx", "pptx")
QUESTION_TYPES = ("single", "multiple", "order", "open")

ATTEMPT_REVIEW = "review"      # На проверке
ATTEMPT_PASSED = "passed"      # Сдал
ATTEMPT_FAILED = "failed"      # Не сдал
ATTEMPT_LABELS = {ATTEMPT_REVIEW: "На проверке", ATTEMPT_PASSED: "Сдал", ATTEMPT_FAILED: "Не сдал"}


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    manager_id: Mapped[Optional[str]] = mapped_column(ID, ForeignKey("users.id", use_alter=True, name="fk_dept_manager"), nullable=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    personnel_no: Mapped[str] = mapped_column(String(40), unique=True)      # табельный номер
    full_name: Mapped[str] = mapped_column(String(200))
    login: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default=ROLE_EMPLOYEE)
    department_id: Mapped[Optional[str]] = mapped_column(ID, ForeignKey("departments.id"), nullable=True)
    position: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)

    department: Mapped[Optional[Department]] = relationship(foreign_keys=[department_id])


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id"), index=True)
    device_id: Mapped[str] = mapped_column(String(64), default="")
    device_kind: Mapped[str] = mapped_column(String(10), default="pc")       # pc | phone
    user_agent: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TS)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class StoredFile(Base):
    """Файл в локальном хранилище. Для Office хранится исходник и ссылка на читаемую PDF-копию."""
    __tablename__ = "files"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String(120))
    ext: Mapped[str] = mapped_column(String(12), default="")
    original_name: Mapped[str] = mapped_column(String(300))
    path: Mapped[str] = mapped_column(String(400))                           # относительно FILES_DIR
    kind: Mapped[str] = mapped_column(String(20), default="source")          # source | converted | certificate
    uploaded_by: Mapped[Optional[str]] = mapped_column(ID, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    converted_file_id: Mapped[Optional[str]] = mapped_column(ID, ForeignKey("files.id"), nullable=True)
    conversion_status: Mapped[str] = mapped_column(String(12), default="none")   # none | ok | error
    conversion_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    conversion_warnings: Mapped[Any] = mapped_column(JSON, default=list)
    converter: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    pages: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    converted: Mapped[Optional["StoredFile"]] = relationship(remote_side=[id], foreign_keys=[converted_file_id])


class Course(Base):
    __tablename__ = "courses"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(300))
    sop_code: Mapped[str] = mapped_column(String(60), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ID, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)

    versions: Mapped[list["CourseVersion"]] = relationship(back_populates="course", order_by="CourseVersion.revision")


class CourseVersion(Base):
    """Редакция курса. Опубликованная редакция не меняется: попытки и документы ссылаются на неё."""
    __tablename__ = "course_versions"
    __table_args__ = (UniqueConstraint("course_id", "revision", name="uq_version_revision"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(ID, ForeignKey("courses.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(12), default="draft")         # draft | published | superseded
    title: Mapped[str] = mapped_column(String(300))
    sop_code: Mapped[str] = mapped_column(String(60), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    change_note: Mapped[str] = mapped_column(Text, default="")
    pass_percent: Mapped[int] = mapped_column(Integer, default=80)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    gate_test: Mapped[bool] = mapped_column(Boolean, default=False)         # тест только после обязательных материалов
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    published_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    published_by: Mapped[Optional[str]] = mapped_column(ID, ForeignKey("users.id"), nullable=True)

    course: Mapped[Course] = relationship(back_populates="versions")
    blocks: Mapped[list["Block"]] = relationship(back_populates="version", order_by="Block.position", cascade="all, delete-orphan")
    questions: Mapped[list["Question"]] = relationship(back_populates="version", order_by="Question.position", cascade="all, delete-orphan")


class Block(Base):
    __tablename__ = "blocks"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ID, ForeignKey("course_versions.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    type: Mapped[str] = mapped_column(String(12))
    title: Mapped[str] = mapped_column(String(300), default="")
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    # text: {text}; link: {url, mandatory, alt_file_id}; файловые: {file_id}; trainer: {scenario}
    data: Mapped[Any] = mapped_column(JSON, default=dict)

    version: Mapped[CourseVersion] = relationship(back_populates="blocks")


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ID, ForeignKey("course_versions.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    type: Mapped[str] = mapped_column(String(12))
    text: Mapped[str] = mapped_column(Text)
    options: Mapped[Any] = mapped_column(JSON, default=list)   # [{id, text}]; для order — в правильном порядке
    correct: Mapped[Any] = mapped_column(JSON, default=None, nullable=True)  # single: id; multiple/order: [id]
    points: Mapped[int] = mapped_column(Integer, default=1)
    criteria: Mapped[str] = mapped_column(Text, default="")   # критерии проверки открытого ответа

    version: Mapped[CourseVersion] = relationship(back_populates="questions")


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (Index("ix_assign_user", "user_id", "status"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(ID, ForeignKey("courses.id"), index=True)
    version_id: Mapped[str] = mapped_column(ID, ForeignKey("course_versions.id"), index=True)
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id"))
    due_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    assigned_by: Mapped[Optional[str]] = mapped_column(ID, ForeignKey("users.id"), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    kind: Mapped[str] = mapped_column(String(12), default="initial")        # initial | reacquaint
    status: Mapped[str] = mapped_column(String(12), default="active")       # active | superseded | cancelled
    via_department_id: Mapped[Optional[str]] = mapped_column(ID, ForeignKey("departments.id"), nullable=True)

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    version: Mapped[CourseVersion] = relationship()


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (Index("ix_attempt_user_version", "user_id", "version_id"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True)                    # создаётся на устройстве
    assignment_id: Mapped[str] = mapped_column(ID, ForeignKey("assignments.id"), index=True)
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id"))
    version_id: Mapped[str] = mapped_column(ID, ForeignKey("course_versions.id"))
    session_id: Mapped[Optional[str]] = mapped_column(ID, nullable=True)
    device_id: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(10), default="pc")           # pc | phone
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)    # время устройства
    submitted_at: Mapped[datetime] = mapped_column(TS)                       # время устройства
    received_at: Mapped[datetime] = mapped_column(TS, default=utcnow)        # время сервера
    sync_batch_id: Mapped[Optional[str]] = mapped_column(ID, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default=ATTEMPT_REVIEW)
    auto_points: Mapped[float] = mapped_column(Float, default=0)
    manual_points: Mapped[float] = mapped_column(Float, default=0)
    score: Mapped[float] = mapped_column(Float, default=0)
    max_score: Mapped[float] = mapped_column(Float, default=0)
    percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # итог только после проверки
    reviewed_by: Mapped[Optional[str]] = mapped_column(ID, ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    review_comment: Mapped[str] = mapped_column(Text, default="")
    finalized_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    version: Mapped[CourseVersion] = relationship()
    answers: Mapped[list["AttemptAnswer"]] = relationship(back_populates="attempt", cascade="all, delete-orphan")


class AttemptAnswer(Base):
    __tablename__ = "attempt_answers"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id", name="uq_answer"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    attempt_id: Mapped[str] = mapped_column(ID, ForeignKey("attempts.id"), index=True)
    question_id: Mapped[str] = mapped_column(ID, ForeignKey("questions.id"))
    answer: Mapped[Any] = mapped_column(JSON, nullable=True)
    is_correct: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)   # None — открытый, ждёт проверки
    points: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_points: Mapped[float] = mapped_column(Float, default=1)
    comment: Mapped[str] = mapped_column(Text, default="")

    attempt: Mapped[Attempt] = relationship(back_populates="answers")
    question: Mapped[Question] = relationship()


class Certificate(Base):
    __tablename__ = "certificates"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    attempt_id: Mapped[str] = mapped_column(ID, ForeignKey("attempts.id"), unique=True)
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id"), index=True)
    version_id: Mapped[str] = mapped_column(ID, ForeignKey("course_versions.id"))
    issued_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    file_id: Mapped[str] = mapped_column(ID, ForeignKey("files.id"))


class SyncBatch(Base):
    """Одна отправка очереди с устройства. Хранит, чем была вызвана отправка и сколько прошло с возврата связи."""
    __tablename__ = "sync_batches"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id"), index=True)
    device_id: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(10), default="pc")
    received_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    sent_at_device: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    restored_at_device: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    delay_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # от возврата связи до приёма
    trigger: Mapped[str] = mapped_column(String(30), default="")            # online | visible | timer | startup | background-sync | manual | enqueue
    app_state: Mapped[str] = mapped_column(String(20), default="")          # visible | hidden | closed
    user_agent: Mapped[str] = mapped_column(String(400), default="")
    events_total: Mapped[int] = mapped_column(Integer, default=0)
    events_accepted: Mapped[int] = mapped_column(Integer, default=0)
    events_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    events_rejected: Mapped[int] = mapped_column(Integer, default=0)


class SyncEvent(Base):
    __tablename__ = "sync_events"
    id: Mapped[str] = mapped_column(ID, primary_key=True)                    # стабильный ID с устройства
    batch_id: Mapped[str] = mapped_column(ID, ForeignKey("sync_batches.id"), index=True)
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id"), index=True)
    device_id: Mapped[str] = mapped_column(String(64), default="")
    type: Mapped[str] = mapped_column(String(40))
    attempt_id: Mapped[Optional[str]] = mapped_column(ID, nullable=True)
    version_id: Mapped[Optional[str]] = mapped_column(ID, nullable=True)
    device_time: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    received_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    status: Mapped[str] = mapped_column(String(12), default="accepted")     # accepted | rejected
    reject_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    reject_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[Any] = mapped_column(JSON, default=dict)


class LearnSession(Base):
    """Сеанс работы с платформой: вход (или открытие приложения) → явный выход либо оценка по последней записи."""
    __tablename__ = "learn_sessions"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id"), index=True)
    device_id: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(10), default="pc")
    started_at: Mapped[datetime] = mapped_column(TS)
    ended_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)     # только явный выход
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    received_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class TimeInterval(Base):
    """Интервал активности. kind=platform — активное время в приложении, material — изучение блока курса."""
    __tablename__ = "time_intervals"
    __table_args__ = (Index("ix_interval_user_start", "user_id", "started_at"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id"))
    device_id: Mapped[str] = mapped_column(String(64), default="")
    session_id: Mapped[Optional[str]] = mapped_column(ID, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(12))
    version_id: Mapped[Optional[str]] = mapped_column(ID, nullable=True, index=True)
    block_id: Mapped[Optional[str]] = mapped_column(ID, nullable=True)
    library_item_id: Mapped[Optional[str]] = mapped_column(ID, nullable=True)
    started_at: Mapped[datetime] = mapped_column(TS)
    ended_at: Mapped[datetime] = mapped_column(TS)
    source: Mapped[str] = mapped_column(String(10), default="pc")
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)         # конец восстановлен после аварийного закрытия
    received_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class BlockProgress(Base):
    __tablename__ = "block_progress"
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id"), primary_key=True)
    version_id: Mapped[str] = mapped_column(ID, ForeignKey("course_versions.id"), primary_key=True)
    block_id: Mapped[str] = mapped_column(ID, primary_key=True)
    completed_at: Mapped[datetime] = mapped_column(TS)
    received_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class CoursePosition(Base):
    """Последний открытый шаг — чтобы продолжить с того же места на другом устройстве."""
    __tablename__ = "course_positions"
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id"), primary_key=True)
    version_id: Mapped[str] = mapped_column(ID, ForeignKey("course_versions.id"), primary_key=True)
    block_id: Mapped[str] = mapped_column(ID)
    updated_at: Mapped[datetime] = mapped_column(TS)


class LibraryItem(Base):
    __tablename__ = "library_items"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(300))
    sop_code: Mapped[str] = mapped_column(String(60), default="")
    category: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    file_id: Mapped[str] = mapped_column(ID, ForeignKey("files.id"))
    created_by: Mapped[Optional[str]] = mapped_column(ID, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    file: Mapped[StoredFile] = relationship()


class TrainerResult(Base):
    __tablename__ = "trainer_results"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id"), index=True)
    version_id: Mapped[str] = mapped_column(ID, ForeignKey("course_versions.id"))
    block_id: Mapped[str] = mapped_column(ID)
    ending_id: Mapped[str] = mapped_column(String(60))
    ending_title: Mapped[str] = mapped_column(String(300), default="")
    outcome: Mapped[str] = mapped_column(String(12), default="")            # good | bad | mixed
    path: Mapped[Any] = mapped_column(JSON, default=list)                   # выбранные реплики по шагам
    finished_at: Mapped[datetime] = mapped_column(TS)
    received_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    source: Mapped[str] = mapped_column(String(10), default="pc")

    user: Mapped[User] = relationship()


class Competency(Base):
    __tablename__ = "competencies"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")


class Assessment(Base):
    """Оценка 180/360: 180 — самооценка и руководитель, 360 — плюс коллеги."""
    __tablename__ = "assessments"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(300))
    subject_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id"))
    mode: Mapped[str] = mapped_column(String(4), default="360")
    status: Mapped[str] = mapped_column(String(10), default="open")         # open | closed
    created_by: Mapped[Optional[str]] = mapped_column(ID, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    competency_ids: Mapped[Any] = mapped_column(JSON, default=list)

    subject: Mapped[User] = relationship(foreign_keys=[subject_id])
    raters: Mapped[list["AssessmentRater"]] = relationship(back_populates="assessment", cascade="all, delete-orphan")


class AssessmentRater(Base):
    __tablename__ = "assessment_raters"
    __table_args__ = (UniqueConstraint("assessment_id", "rater_id", name="uq_rater"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(ID, ForeignKey("assessments.id"), index=True)
    rater_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(10))                           # self | manager | peer
    submitted_at: Mapped[Optional[datetime]] = mapped_column(TS, nullable=True)
    scores: Mapped[Any] = mapped_column(JSON, default=dict)                 # {competency_id: 1..5 | null}
    comment: Mapped[str] = mapped_column(Text, default="")

    assessment: Mapped[Assessment] = relationship(back_populates="raters")
    rater: Mapped[User] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(ID, nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(40), default="")
    entity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    details: Mapped[Any] = mapped_column(JSON, default=dict)
    ip: Mapped[str] = mapped_column(String(60), default="")
