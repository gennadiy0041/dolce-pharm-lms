"""Пароли, токены сессий и проверка ролей на сервере."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from . import config
from .db import get_db, utcnow
from .models import AuditLog, AuthToken, Department, User, ROLE_MANAGER, ROLE_METHODIST

_PBKDF2_ROUNDS = 240_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return "pbkdf2_sha256$%d$%s$%s" % (
        _PBKDF2_ROUNDS, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_b64, digest_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt_b64), int(rounds))
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except Exception:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(db: Session, user: User, device_id: str, device_kind: str, user_agent: str) -> str:
    token = secrets.token_urlsafe(32)
    db.add(AuthToken(
        token_hash=_token_hash(token), user_id=user.id, device_id=device_id[:64],
        device_kind="phone" if device_kind == "phone" else "pc", user_agent=user_agent[:400],
        expires_at=utcnow() + timedelta(days=config.TOKEN_TTL_DAYS),
    ))
    return token


def revoke_token(db: Session, token: str) -> None:
    row = db.get(AuthToken, _token_hash(token))
    if row and not row.revoked_at:
        row.revoked_at = utcnow()


class Principal:
    """Кто делает запрос: пользователь и устройство, с которого выдан токен."""

    def __init__(self, user: User, token: AuthToken):
        self.user = user
        self.token = token

    @property
    def is_methodist(self) -> bool:
        return self.user.role == ROLE_METHODIST

    @property
    def is_manager(self) -> bool:
        return self.user.role == ROLE_MANAGER


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def current_principal(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> Principal:
    token = _bearer(authorization)
    if not token:
        raise HTTPException(401, detail={"code": "auth_required", "message": "Требуется вход"})
    row = db.get(AuthToken, _token_hash(token))
    now = utcnow()
    if not row or row.revoked_at or row.expires_at < now:
        raise HTTPException(401, detail={"code": "auth_expired", "message": "Сессия завершена. Войдите заново"})
    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        raise HTTPException(401, detail={"code": "auth_disabled", "message": "Учётная запись отключена"})
    return Principal(user, row)


def require_methodist(p: Principal = Depends(current_principal)) -> Principal:
    if not p.is_methodist:
        raise HTTPException(403, detail={"code": "forbidden", "message": "Доступно только методисту"})
    return p


def require_staff(p: Principal = Depends(current_principal)) -> Principal:
    """Методист или руководитель подразделения (отчёты)."""
    if not (p.is_methodist or p.is_manager):
        raise HTTPException(403, detail={"code": "forbidden", "message": "Недостаточно прав"})
    return p


def managed_department_ids(db: Session, user: User) -> list[str]:
    """Подразделения, которыми руководит пользователь (для ограничения отчётов)."""
    ids = [d.id for d in db.query(Department).filter(Department.manager_id == user.id).all()]
    return ids


def scope_department_ids(db: Session, p: Principal) -> Optional[list[str]]:
    """None — без ограничений (методист); список — только эти подразделения (руководитель)."""
    if p.is_methodist:
        return None
    return managed_department_ids(db, p.user)


def can_view_user(db: Session, p: Principal, target: User) -> bool:
    if p.is_methodist or p.user.id == target.id:
        return True
    if p.is_manager and target.department_id in managed_department_ids(db, p.user):
        return True
    return False


def audit(db: Session, request: Optional[Request], user_id: Optional[str], action: str,
          entity: str = "", entity_id: Optional[str] = None, **details) -> None:
    ip = request.client.host if request and request.client else ""
    db.add(AuditLog(user_id=user_id, action=action, entity=entity, entity_id=entity_id, details=details, ip=ip))
