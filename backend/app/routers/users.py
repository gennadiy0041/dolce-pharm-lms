"""Пользователи: список, создание, экспорт и импорт CSV с предпросмотром."""
from __future__ import annotations

import re
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

import csv

from ..db import get_db, utcnow
from ..models import ROLE_LABELS, ROLES, AuthToken, Department, Setting, User, new_id
from ..security import Principal, audit, hash_password, require_methodist
from ..serializers import dept_map, user_out
from ..services.csvio import csv_response, decode_csv, parse_csv, to_csv_bytes

router = APIRouter(prefix="/api", tags=["Пользователи"])

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ә": "a", "ғ": "g", "қ": "k", "ң": "n", "ө": "o", "ұ": "u", "ү": "u", "һ": "h", "і": "i",
}


def translit(s: str) -> str:
    return "".join(_TRANSLIT.get(ch, ch) for ch in s.lower())


def make_login(db: Session, full_name: str, taken: set[str]) -> str:
    parts = [p for p in re.split(r"\s+", full_name.strip()) if p]
    base = translit(parts[0]) if parts else "user"
    if len(parts) > 1:
        base += "." + translit(parts[1])[:1]
    base = re.sub(r"[^a-z0-9.]", "", base) or "user"
    candidate, n = base, 1
    while candidate in taken or db.query(User).filter(User.login == candidate).first():
        n += 1
        candidate = "%s%d" % (base, n)
    taken.add(candidate)
    return candidate


def sync_manager(db: Session, u: User) -> Optional[str]:
    """Связывает руководителя с его подразделением. Область видимости руководителя в отчётах
    определяется полем departments.manager_id, поэтому его нужно обновлять при создании,
    изменении и импорте сотрудников.

    Возвращает пояснение, если сотрудник с ролью руководителя не стал руководителем подразделения,
    потому что это место занято."""

    def eligible(x: Optional[User], dept_id: str) -> bool:
        return x is not None and x.is_active and x.role == "manager" and x.department_id == dept_id

    touched = {u.department_id} if u.department_id else set()
    for d in db.query(Department).filter(Department.manager_id == u.id).all():
        if not eligible(u, d.id):
            d.manager_id = None             # место освобождается
            touched.add(d.id)
    note = None
    for dept_id in touched:
        d = db.get(Department, dept_id)
        if d is None:
            continue
        holder = db.get(User, d.manager_id) if d.manager_id else None
        if eligible(holder, d.id):
            if eligible(u, d.id) and holder.id != u.id:
                note = ("В подразделении «%s» уже есть руководитель — %s. Отчёты подразделения видит он; "
                        "новый руководитель получит доступ, когда прежний будет отключён или переведён."
                        % (d.name, holder.full_name))
            continue
        # место свободно (или занято тем, кто уже не руководит) — его получает действующий руководитель
        # этого подразделения, заведённый раньше остальных
        successor = db.query(User).filter(User.department_id == d.id, User.role == "manager",
                                          User.is_active.is_(True)).order_by(User.created_at, User.id).first()
        d.manager_id = successor.id if successor else None
    return note


class UserIn(BaseModel):
    personnel_no: str
    full_name: str
    login: Optional[str] = None
    password: Optional[str] = None
    role: str = "employee"
    department_id: Optional[str] = None
    position: str = ""


class UserPatch(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    department_id: Optional[str] = None
    position: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


def _norm(s: str) -> str:
    return s.casefold().replace("ё", "е")


@router.get("/users", summary="Список пользователей (методист)")
def list_users(q: str = "", department_id: str = "", role: str = "",
               p: Principal = Depends(require_methodist), db: Session = Depends(get_db)) -> list[dict]:
    depts = dept_map(db)
    rows = db.query(User).order_by(User.full_name).all()
    needle = _norm(q.strip())
    out = []
    for u in rows:
        if department_id and u.department_id != department_id:
            continue
        if role and u.role != role:
            continue
        if needle and needle not in _norm("%s %s %s" % (u.full_name, u.personnel_no, u.login)):
            continue
        out.append(user_out(u, depts))
    return out


@router.post("/users", summary="Создать пользователя")
def create_user(body: UserIn, request: Request, p: Principal = Depends(require_methodist),
                db: Session = Depends(get_db)) -> dict:
    pn = body.personnel_no.strip()
    if not pn or not body.full_name.strip():
        raise HTTPException(400, detail={"message": "Укажите табельный номер и ФИО"})
    if body.role not in ROLES:
        raise HTTPException(400, detail={"message": "Неизвестная роль"})
    if db.query(User).filter(User.personnel_no == pn).first():
        raise HTTPException(409, detail={"message": "Сотрудник с табельным номером %s уже есть" % pn})
    login = (body.login or "").strip().lower() or make_login(db, body.full_name, set())
    if db.query(User).filter(User.login == login).first():
        raise HTTPException(409, detail={"message": "Логин %s уже занят" % login})
    password = body.password or secrets.token_urlsafe(6)
    u = User(id=new_id(), personnel_no=pn, full_name=body.full_name.strip(), login=login,
             password_hash=hash_password(password), role=body.role, department_id=body.department_id or None,
             position=body.position.strip())
    db.add(u)
    db.flush()
    note = sync_manager(db, u)
    audit(db, request, p.user.id, "user_create", "user", u.id)
    db.commit()
    out = user_out(u, dept_map(db))
    out["initial_password"] = password
    out["note"] = note
    return out


@router.patch("/users/{user_id}", summary="Изменить пользователя")
def patch_user(user_id: str, body: UserPatch, request: Request, p: Principal = Depends(require_methodist),
               db: Session = Depends(get_db)) -> dict:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, detail={"message": "Пользователь не найден"})
    if body.full_name is not None:
        u.full_name = body.full_name.strip()
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(400, detail={"message": "Неизвестная роль"})
        if u.id == p.user.id and body.role != "methodist":
            raise HTTPException(400, detail={"message": "Нельзя снять роль методиста с самого себя — иначе управлять "
                                                        "платформой станет некому. Это может сделать другой методист."})
        u.role = body.role
    if body.department_id is not None:
        u.department_id = body.department_id or None
    if body.position is not None:
        u.position = body.position
    if body.is_active is not None:
        if u.id == p.user.id and not body.is_active:
            raise HTTPException(400, detail={"message": "Нельзя отключить собственную учётную запись"})
        u.is_active = body.is_active
    if body.password:
        if len(body.password) < 6:
            raise HTTPException(400, detail={"message": "Пароль — не короче 6 символов"})
        u.password_hash = hash_password(body.password)
        # смена пароля завершает все открытые сеансы сотрудника на всех устройствах
        now = utcnow()
        for t in db.query(AuthToken).filter(AuthToken.user_id == u.id, AuthToken.revoked_at.is_(None)).all():
            t.revoked_at = now
    db.flush()
    note = sync_manager(db, u)
    audit(db, request, p.user.id, "user_update", "user", u.id)
    db.commit()
    out = user_out(u, dept_map(db))
    out["note"] = note
    return out


@router.get("/users/export.csv", summary="Экспорт списка пользователей в CSV для Excel")
def export_users(p: Principal = Depends(require_methodist), db: Session = Depends(get_db)):
    depts = dept_map(db)
    rows = db.query(User).order_by(User.full_name).all()
    data = to_csv_bytes(
        ["Табельный номер", "ФИО", "Подразделение", "Должность", "Роль", "Логин", "Активен"],
        [[u.personnel_no, u.full_name, depts[u.department_id].name if u.department_id in depts else "",
          u.position, ROLE_LABELS.get(u.role, u.role), u.login, "да" if u.is_active else "нет"] for u in rows],
    )
    return csv_response(data, "Сотрудники.csv")


# ---------- импорт ----------

_COLS = {
    "personnel_no": ("табельный номер", "табельный", "таб. номер", "таб. №", "таб №", "personnel_no"),
    "full_name": ("фио", "ф.и.о.", "сотрудник", "full_name"),
    "department": ("подразделение", "отдел", "участок", "department"),
    "position": ("должность", "position"),
    "role": ("роль", "role"),
    "login": ("логин", "login"),
}
_ROLE_BY_LABEL = {"сотрудник": "employee", "руководитель": "manager", "методист": "methodist",
                  "employee": "employee", "manager": "manager", "methodist": "methodist", "": "employee"}


def _analyze(db: Session, data: bytes) -> dict:
    if data[:4] in (b"PK\x03\x04", b"\xd0\xcf\x11\xe0"):          # подписи XLSX (ZIP) и старого XLS
        raise HTTPException(400, detail={"message": "Это файл Excel, а не CSV. В Excel выберите «Сохранить как» → «CSV UTF-8 (разделитель — запятая)»."})
    try:
        text, encoding = decode_csv(data)
        header, rows, delimiter = parse_csv(text)
    except (csv.Error, UnicodeError):
        raise HTTPException(400, detail={"message": "Файл не похож на CSV. Сохраните таблицу как «CSV UTF-8» и загрузите снова."})
    if not header:
        raise HTTPException(400, detail={"message": "Файл пустой или не похож на CSV"})
    index: dict[str, int] = {}
    for i, name in enumerate(header):
        key = name.strip().casefold()
        for field, aliases in _COLS.items():
            if key in aliases and field not in index:
                index[field] = i
    missing = [label for field, label in (("personnel_no", "Табельный номер"), ("full_name", "ФИО")) if field not in index]
    if missing:
        raise HTTPException(400, detail={"message": "В файле нет обязательных столбцов: %s. Скачайте образец." % ", ".join(missing)})
    existing = {u.personnel_no: u for u in db.query(User).all()}
    depts = {d.name.casefold(): d for d in db.query(Department).all()}
    seen: set[str] = set()
    taken: set[str] = set()
    out = []
    for n, row in enumerate(rows, 2):
        def cell(field: str) -> str:
            i = index.get(field)
            return row[i].strip() if i is not None and i < len(row) else ""

        item = {"line": n, "personnel_no": cell("personnel_no"), "full_name": cell("full_name"),
                "department": cell("department"), "position": cell("position"),
                "role": _ROLE_BY_LABEL.get(cell("role").casefold()), "login": cell("login").lower(),
                "status": "new", "problems": [], "notes": []}
        if not item["personnel_no"]:
            item["problems"].append("нет табельного номера")
        if not item["full_name"]:
            item["problems"].append("нет ФИО")
        elif len(item["full_name"].split()) < 2:
            item["problems"].append("ФИО должно содержать фамилию и имя")
        if item["role"] is None:
            item["problems"].append("неизвестная роль «%s»" % cell("role"))
        # слишком длинное значение — обычно признак незакрытой кавычки, из-за которой в ячейку попала половина файла
        for field, label, limit in (("personnel_no", "табельный номер", 40), ("full_name", "ФИО", 200),
                                    ("department", "подразделение", 200), ("position", "должность", 200), ("login", "логин", 80)):
            if len(item[field]) > limit or "\n" in item[field]:
                item["problems"].append("%s длиннее %d символов — проверьте кавычки в строке" % (label, limit))
                item[field] = item[field][:60] + "…"
        if item["personnel_no"] in seen:
            item["problems"].append("табельный номер повторяется в файле")
        seen.add(item["personnel_no"])
        if item["department"] and item["department"].casefold() not in depts:
            item["notes"].append("будет создано подразделение «%s»" % item["department"])
        if item["problems"]:
            item["status"] = "error"
        elif item["personnel_no"] in existing:
            item["status"] = "exists"
            item["notes"] = ["уже в системе: %s — строка пропускается" % existing[item["personnel_no"]].full_name]
        else:
            if item["login"] and (item["login"] in taken or db.query(User).filter(User.login == item["login"]).first()):
                item["notes"].append("логин занят — будет подобран другой")
                item["login"] = ""
            if not item["login"]:
                item["login"] = make_login(db, item["full_name"], taken)
            else:
                taken.add(item["login"])
        out.append(item)
    summary = {s: sum(1 for r in out if r["status"] == s) for s in ("new", "exists", "error")}
    return {"encoding": encoding, "delimiter": delimiter, "columns": header, "rows": out, "summary": summary}


@router.post("/users/import/preview", summary="Импорт сотрудников: предпросмотр и проверка строк")
async def import_preview(file: UploadFile = File(...), p: Principal = Depends(require_methodist),
                         db: Session = Depends(get_db)) -> dict:
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, detail={"message": "Файл больше 5 МБ"})
    return _analyze(db, data)


@router.post("/users/import/commit", summary="Импорт сотрудников: создать новых, существующих пропустить")
async def import_commit(request: Request, file: UploadFile = File(...), p: Principal = Depends(require_methodist),
                        db: Session = Depends(get_db)) -> dict:
    data = await file.read()
    result = _analyze(db, data)
    depts = {d.name.casefold(): d for d in db.query(Department).all()}
    created = []
    for item in result["rows"]:
        if item["status"] != "new":
            continue
        dept = None
        if item["department"]:
            dept = depts.get(item["department"].casefold())
            if not dept:
                dept = Department(id=new_id(), name=item["department"])
                db.add(dept)
                db.flush()
                depts[dept.name.casefold()] = dept
        password = secrets.token_urlsafe(6)
        u = User(id=new_id(), personnel_no=item["personnel_no"], full_name=item["full_name"], login=item["login"],
                 password_hash=hash_password(password), role=item["role"] or "employee",
                 department_id=dept.id if dept else None, position=item["position"])
        db.add(u)
        db.flush()
        note = sync_manager(db, u)
        created.append({"personnel_no": u.personnel_no, "full_name": u.full_name, "login": u.login,
                        "password": password, "department": dept.name if dept else "", "note": note})
    audit(db, request, p.user.id, "users_import", "user", None, created=len(created),
          skipped=result["summary"]["exists"], errors=result["summary"]["error"])
    db.commit()
    return {"created": created, "skipped_existing": result["summary"]["exists"], "errors": result["summary"]["error"]}


@router.get("/users/import/sample.csv", summary="Образец файла для импорта")
def import_sample(p: Principal = Depends(require_methodist)):
    data = to_csv_bytes(
        ["Табельный номер", "ФИО", "Подразделение", "Должность", "Роль"],
        [["30101", "Жумабаев Алмас Кайратович", "Участок фасовки", "Оператор фасовочной линии", "Сотрудник"],
         ["30102", "Ким Виктория Сергеевна", "Участок фасовки", "Укладчик-упаковщик", "Сотрудник"],
         ["30103", "Оспанов Данияр Ерланович", "Участок фасовки", "Мастер смены", "Руководитель"]],
    )
    return csv_response(data, "Образец импорта сотрудников.csv")


# ---------- настройки ----------

class SettingsIn(BaseModel):
    heartbeat_seconds: Optional[int] = None
    idle_timeout_seconds: Optional[int] = None
    reading_idle_seconds: Optional[int] = None
    local_retention_days: Optional[int] = None


_LIMITS = {"heartbeat_seconds": (5, 60), "idle_timeout_seconds": (30, 1800),
           "reading_idle_seconds": (30, 900), "local_retention_days": (1, 365)}


@router.get("/settings", summary="Настройки учёта времени")
def get_settings(p: Principal = Depends(require_methodist), db: Session = Depends(get_db)) -> dict:
    from .auth import client_settings
    return client_settings(db)


@router.patch("/settings", summary="Изменить настройки учёта времени")
def patch_settings(body: SettingsIn, request: Request, p: Principal = Depends(require_methodist),
                   db: Session = Depends(get_db)) -> dict:
    from .auth import client_settings
    for key, value in body.model_dump(exclude_none=True).items():
        lo, hi = _LIMITS[key]
        if not (lo <= value <= hi):
            raise HTTPException(400, detail={"message": "Значение «%s» должно быть от %d до %d" % (key, lo, hi)})
        row = db.get(Setting, key)
        if row:
            row.value = value
        else:
            db.add(Setting(key=key, value=value))
    audit(db, request, p.user.id, "settings_update", "settings", None, **body.model_dump(exclude_none=True))
    db.commit()
    return client_settings(db)
