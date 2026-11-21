from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db, utcnow
from ..models import Department, Setting, User
from ..security import (
    Principal, _bearer, audit, current_principal, issue_token, managed_department_ids, revoke_token, verify_password,
)
from ..serializers import dept_map, user_out

router = APIRouter(prefix="/api", tags=["Вход"])


class LoginIn(BaseModel):
    login: str
    password: str
    device_id: str = ""
    device_kind: str = "pc"


def client_settings(db: Session) -> dict:
    values = dict(config.DEFAULT_SETTINGS)
    for row in db.query(Setting).all():
        if row.key in values:
            values[row.key] = row.value
    return values


def me_payload(db: Session, user: User) -> dict:
    out = user_out(user, dept_map(db))
    out["managed_department_ids"] = managed_department_ids(db, user)
    return out


@router.get("/health", summary="Проверка доступности сервера")
def health() -> dict:
    return {"ok": True, "time": utcnow().isoformat(), "env": config.ENV}


@router.post("/auth/login", summary="Вход по логину и паролю")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> dict:
    login_value = body.login.strip().lower()
    user = db.query(User).filter(User.login == login_value).first()
    now = utcnow()
    bad = HTTPException(401, detail={"code": "bad_credentials", "message": "Неверный логин или пароль"})
    if not user:
        raise bad
    if user.locked_until and user.locked_until > now:
        minutes = int((user.locked_until - now).total_seconds() // 60) + 1
        raise HTTPException(429, detail={"code": "locked", "message": "Слишком много неудачных попыток. Повторите через %d мин." % minutes})
    if not user.is_active or not verify_password(body.password, user.password_hash):
        user.failed_logins = (user.failed_logins or 0) + 1
        if user.failed_logins >= config.LOGIN_MAX_FAILURES:
            user.locked_until = now + timedelta(minutes=config.LOGIN_LOCK_MINUTES)
            user.failed_logins = 0
        audit(db, request, user.id, "login_failed", "user", user.id)
        db.commit()
        raise bad
    user.failed_logins = 0
    user.locked_until = None
    token = issue_token(db, user, body.device_id, body.device_kind, request.headers.get("user-agent", ""))
    audit(db, request, user.id, "login", "user", user.id, device_kind=body.device_kind)
    db.commit()
    return {"token": token, "user": me_payload(db, user), "server_time": utcnow().isoformat(),
            "settings": client_settings(db)}


@router.post("/auth/logout", summary="Выход: токен отзывается")
def logout(request: Request, authorization: Optional[str] = Header(default=None),
           p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    token = _bearer(authorization)
    if token:
        revoke_token(db, token)
    audit(db, request, p.user.id, "logout", "user", p.user.id)
    db.commit()
    return {"ok": True}


@router.get("/auth/me", summary="Текущий пользователь и настройки клиента")
def me(p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    return {"user": me_payload(db, p.user), "server_time": utcnow().isoformat(), "settings": client_settings(db)}


@router.get("/departments", summary="Подразделения")
def departments(p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.query(Department).order_by(Department.name).all()
    if not p.is_methodist:
        allowed = set(managed_department_ids(db, p.user)) | ({p.user.department_id} if p.user.department_id else set())
        rows = [d for d in rows if d.id in allowed]
    managers = {u.id: u.full_name for u in db.query(User).filter(User.id.in_([d.manager_id for d in rows if d.manager_id])).all()}
    return [{"id": d.id, "name": d.name, "manager_id": d.manager_id, "manager": managers.get(d.manager_id)} for d in rows]
