"""Отчёт «Время и входы»: один расчёт для экрана методиста и для выгрузки в Excel и CSV.

Экран и файлы строятся из одного и того же результата `compute()` с теми же фильтрами и правами,
поэтому итоги в выгрузке совпадают с экраном по построению.

Правила подсчёта:
- В отчёт попадает каждый сотрудник, у которого за период был хотя бы один вход или интервал
  активности, — в том числе тот, кто только читал материалы и тест не сдавал.
- Время — объединение интервалов активности, обрезанных границами периода: пересекающиеся интервалы
  (две вкладки, ПК и телефон одновременно, повторная доставка) не складываются дважды.
- Сеанс («вход») попадает в период, если пересекается с ним. Выход: явный (кнопка «Выйти»),
  «сеанс идёт» (выхода нет, последняя активность моложе OPEN_WINDOW) или оценка по последней записи
  (приложение закрыли или телефон ещё не передал данные).
- Данные с телефона приходят после появления связи. Интервалы несут время устройства, поэтому
  поздняя доставка попадает в тот день, когда человек на самом деле учился; сеанс продлевается.
"""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import config
from ..models import Block, CourseVersion, LearnSession, LibraryItem, TimeInterval, TrainerResult, User
from .timecalc import union_seconds

SOURCE_LABELS = {"pc": "ПК", "phone": "Телефон"}
OPEN_WINDOW = timedelta(minutes=5)
END_LABELS = {
    "logout": "явный выход",
    "open": "сеанс идёт",
    "estimated": "оценка по последней записи",
}
OUTCOME_LABELS = {"good": "хороший", "bad": "плохой", "mixed": "с потерями"}


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _intervals(db: Session, user_ids: list[str], frm: Optional[datetime], to: Optional[datetime]) -> list[TimeInterval]:
    if not user_ids:
        return []
    q = db.query(TimeInterval).filter(TimeInterval.user_id.in_(user_ids))
    if frm:
        q = q.filter(TimeInterval.ended_at > frm)
    if to:
        q = q.filter(TimeInterval.started_at < to)
    return q.all()


def compute(db: Session, users: dict[str, User], depts: dict, frm: Optional[datetime], to: Optional[datetime],
            now: datetime) -> dict:
    """Сеансы, итоги по сотрудникам и время по материалам за период [frm, to)."""
    empty = {"sessions": [], "materials": [], "totals": [], "trainer": [], "sessions_total": 0}
    if not users:
        return empty
    ids = list(users)

    def dept_of(u: User) -> str:
        return depts[u.department_id].name if u.department_id in depts else ""

    ivs = _intervals(db, ids, frm, to)
    by_session: dict[str, list[TimeInterval]] = defaultdict(list)
    by_user: dict[str, list[TimeInterval]] = defaultdict(list)
    for iv in ivs:
        by_user[iv.user_id].append(iv)
        if iv.session_id:
            by_session[iv.session_id].append(iv)

    # сеансы, пересекающиеся с периодом: начались до его конца и закончились (или ещё длятся) после начала
    q = db.query(LearnSession).filter(LearnSession.user_id.in_(ids))
    if to:
        q = q.filter(LearnSession.started_at < to)
    if frm:
        q = q.filter(func.coalesce(LearnSession.ended_at, LearnSession.last_activity_at, LearnSession.started_at) >= frm)
    sessions = q.order_by(LearnSession.started_at.desc(), LearnSession.id).all()

    def span(items: Iterable[TimeInterval], material: Optional[bool] = None) -> int:
        return union_seconds(((i.started_at, i.ended_at) for i in items
                              if material is None or (i.kind == "material") == material), frm, to)

    out_sessions = []
    per_user_sessions: dict[str, list[dict]] = defaultdict(list)
    for s in sessions:
        u = users[s.user_id]
        items = by_session.get(s.id, [])
        last = s.last_activity_at or s.started_at
        if s.ended_at:
            end_kind, exit_at = "logout", s.ended_at
        elif now - last <= OPEN_WINDOW:
            end_kind, exit_at = "open", None
        else:
            end_kind, exit_at = "estimated", last
        row = {
            "id": s.id, "user_id": u.id, "full_name": u.full_name, "personnel_no": u.personnel_no,
            "department": dept_of(u), "source": s.source, "source_label": SOURCE_LABELS.get(s.source, s.source),
            "login_at": _iso(s.started_at), "logout_at": _iso(s.ended_at), "exit_at": _iso(exit_at),
            "last_activity_at": _iso(s.last_activity_at), "end_kind": end_kind, "end_label": END_LABELS[end_kind],
            "active_seconds": span(items), "material_seconds": span(items, True),
            "estimated_intervals": sum(1 for i in items if i.estimated),
            "_login": s.started_at, "_exit": exit_at or last,
        }
        out_sessions.append(row)
        per_user_sessions[u.id].append(row)

    totals = []
    for uid in sorted(set(by_user) | set(per_user_sessions), key=lambda x: users[x].full_name):
        u = users[uid]
        items = by_user.get(uid, [])
        rows = per_user_sessions.get(uid, [])
        firsts = [r["_login"] for r in rows] + [i.started_at for i in items]
        lasts = [r["_exit"] for r in rows] + [i.ended_at for i in items]
        library = union_seconds(((i.started_at, i.ended_at) for i in items if i.kind == "material" and i.library_item_id), frm, to)
        totals.append({
            "user_id": uid, "full_name": u.full_name, "personnel_no": u.personnel_no, "department": dept_of(u),
            "sessions": len(rows), "open_sessions": sum(1 for r in rows if r["end_kind"] == "open"),
            "first_login_at": _iso(min(firsts)) if firsts else None, "last_exit_at": _iso(max(lasts)) if lasts else None,
            "platform_seconds": span(items), "material_seconds": span(items, True), "library_seconds": library,
            "sources": sorted({SOURCE_LABELS.get(r["source"], r["source"]) for r in rows}
                              | {SOURCE_LABELS.get(i.source, i.source) for i in items}),
        })

    version_ids = {i.version_id for i in ivs if i.version_id}
    block_ids = {i.block_id for i in ivs if i.block_id}
    library_ids = {i.library_item_id for i in ivs if i.library_item_id}
    versions = {v.id: v for v in db.query(CourseVersion).filter(CourseVersion.id.in_(version_ids)).all()} if version_ids else {}
    blocks = {b.id: b for b in db.query(Block).filter(Block.id.in_(block_ids)).all()} if block_ids else {}
    library = {x.id: x for x in db.query(LibraryItem).filter(LibraryItem.id.in_(library_ids)).all()} if library_ids else {}
    materials = []
    for uid, items in by_user.items():
        u = users[uid]
        groups: dict[tuple, list[TimeInterval]] = defaultdict(list)
        for i in items:
            if i.kind == "material" and i.version_id:
                groups[("course", i.version_id, i.block_id)].append(i)
            elif i.kind == "material" and i.library_item_id:
                groups[("library", i.library_item_id, None)].append(i)
        for (where, key, bid), group in groups.items():
            seconds = span(group)
            if not seconds:
                continue
            row = {"user_id": uid, "full_name": u.full_name, "personnel_no": u.personnel_no, "department": dept_of(u),
                   "seconds": seconds, "sources": sorted({SOURCE_LABELS.get(i.source, i.source) for i in group}),
                   "first_at": _iso(min(i.started_at for i in group)), "last_at": _iso(max(i.ended_at for i in group))}
            if where == "course":
                v, b = versions.get(key), blocks.get(bid)
                if not v:
                    continue
                row.update({"course_title": v.title, "sop_code": v.sop_code, "revision": v.revision,
                            "block_title": (b.title or b.type) if b else "Материал"})
            else:                                           # справочник из библиотеки
                item = library.get(key)
                row.update({"course_title": "Библиотека", "sop_code": item.sop_code if item else "", "revision": None,
                            "block_title": item.title if item else "Материал библиотеки"})
            materials.append(row)
    materials.sort(key=lambda m: (m["full_name"], m["course_title"], m["block_title"]))

    tq = db.query(TrainerResult).filter(TrainerResult.user_id.in_(ids))
    if frm:
        tq = tq.filter(TrainerResult.finished_at >= frm)
    if to:
        tq = tq.filter(TrainerResult.finished_at < to)
    trainer_versions = {}
    trainer = []
    for t in tq.order_by(TrainerResult.finished_at.desc()).all():
        u = users[t.user_id]
        if t.version_id not in trainer_versions:
            trainer_versions[t.version_id] = db.get(CourseVersion, t.version_id)
        v = trainer_versions[t.version_id]
        trainer.append({
            "id": t.id, "user_id": u.id, "full_name": u.full_name, "personnel_no": u.personnel_no, "department": dept_of(u),
            "course_title": v.title if v else "", "revision": v.revision if v else None,
            "ending_title": t.ending_title, "outcome": t.outcome, "outcome_label": OUTCOME_LABELS.get(t.outcome, t.outcome),
            "steps": len(t.path) if isinstance(t.path, list) else 0,
            "finished_at": _iso(t.finished_at), "received_at": _iso(t.received_at),
            "source_label": SOURCE_LABELS.get(t.source, t.source),
        })

    for r in out_sessions:
        r.pop("_login", None)
        r.pop("_exit", None)
    return {"sessions": out_sessions, "materials": materials, "totals": totals, "trainer": trainer,
            "sessions_total": len(out_sessions)}


# ---------- выгрузка ----------

def _local(dt_iso: Optional[str]) -> Optional[datetime]:
    """Время для таблицы: местное, без часового пояса (Excel его не хранит)."""
    if not dt_iso:
        return None
    return datetime.fromisoformat(dt_iso).astimezone(config.LOCAL_TZ).replace(tzinfo=None)


def _text_dt(dt_iso: Optional[str]) -> str:
    d = _local(dt_iso)
    return d.strftime("%d.%m.%Y %H:%M:%S") if d else ""


def hms(seconds: Optional[int]) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


# Колонки каждой таблицы: (заголовок, ключ, вид). Вид: text, dt (дата и время), dur (длительность),
# int (число). Один список и для Excel, и для CSV — состав столбцов не расходится.
TABLES: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "totals": ("Итого по сотрудникам", [
        ("ФИО", "full_name", "text"), ("Табельный номер", "personnel_no", "text"), ("Подразделение", "department", "text"),
        ("Входов (сеансов)", "sessions", "int"), ("Из них идут сейчас", "open_sessions", "int"),
        ("Первый вход", "first_login_at", "dt"), ("Последний выход или активность", "last_exit_at", "dt"),
        ("Время на платформе", "platform_seconds", "dur"), ("Из него — изучение материалов", "material_seconds", "dur"),
        ("В том числе справочники библиотеки", "library_seconds", "dur"), ("Источники", "sources", "list"),
    ]),
    "sessions": ("Входы и выходы", [
        ("ФИО", "full_name", "text"), ("Табельный номер", "personnel_no", "text"), ("Подразделение", "department", "text"),
        ("Вход", "login_at", "dt"), ("Выход", "exit_at", "dt"), ("Как определён выход", "end_label", "text"),
        ("Последняя активность", "last_activity_at", "dt"), ("Активное время", "active_seconds", "dur"),
        ("Из него — изучение материалов", "material_seconds", "dur"),
        ("Интервалов восстановлено после сбоя", "estimated_intervals", "int"), ("Источник", "source_label", "text"),
    ]),
    "materials": ("По материалам", [
        ("ФИО", "full_name", "text"), ("Табельный номер", "personnel_no", "text"), ("Подразделение", "department", "text"),
        ("Курс", "course_title", "text"), ("Код СОП", "sop_code", "text"), ("Редакция", "revision", "int"),
        ("Материал", "block_title", "text"), ("Время изучения", "seconds", "dur"),
        ("Первое обращение", "first_at", "dt"), ("Последнее обращение", "last_at", "dt"), ("Источники", "sources", "list"),
    ]),
    "trainer": ("Тренажёр", [
        ("ФИО", "full_name", "text"), ("Табельный номер", "personnel_no", "text"), ("Подразделение", "department", "text"),
        ("Курс", "course_title", "text"), ("Редакция", "revision", "int"), ("Развязка", "ending_title", "text"),
        ("Исход", "outcome_label", "text"), ("Шагов", "steps", "int"), ("Пройден", "finished_at", "dt"),
        ("Получен сервером", "received_at", "dt"), ("Источник", "source_label", "text"),
    ]),
}


def csv_rows(data: dict, table: str) -> tuple[list[str], list[list[object]]]:
    """Таблица для CSV: длительности — в виде «ч:мм:сс» и отдельным столбцом в секундах."""
    _, cols = TABLES[table]
    header: list[str] = []
    for title, _key, kind in cols:
        header.append(title + (", ч:мм:сс" if kind == "dur" else ""))
        if kind == "dur":
            header.append(title + ", секунд")
    rows = []
    for r in data[table]:
        row: list[object] = []
        for _title, key, kind in cols:
            v = r.get(key)
            if kind == "dur":
                row += [hms(v), int(v or 0)]
            elif kind == "dt":
                row.append(_text_dt(v))
            elif kind == "list":
                row.append(", ".join(v or []))
            else:
                row.append(v if v is not None else "")
        rows.append(row)
    return header, rows


def build_xlsx(data: dict, params: list[tuple[str, str]]) -> bytes:
    """Книга Excel: итоги, входы и выходы, материалы, тренажёр и лист с параметрами выгрузки."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="4F007D")
    wrap = Alignment(wrap_text=True, vertical="top")

    def text_cell(ws, row: int, col: int, value: object) -> None:
        cell = ws.cell(row=row, column=col, value=value)
        if isinstance(value, str):
            cell.data_type = "s"            # строка, начинающаяся с «=», не станет формулой

    first = True
    for table in ("totals", "sessions", "materials", "trainer"):
        title, cols = TABLES[table]
        ws = wb.active if first else wb.create_sheet()
        first = False
        ws.title = title
        for c, (name, _key, _kind) in enumerate(cols, 1):
            cell = ws.cell(row=1, column=c, value=name)
            cell.font, cell.fill, cell.alignment = head_font, head_fill, wrap
        for r, item in enumerate(data[table], 2):
            for c, (_name, key, kind) in enumerate(cols, 1):
                v = item.get(key)
                if kind == "dur":
                    cell = ws.cell(row=r, column=c, value=(int(v or 0)) / 86400)
                    cell.number_format = "[h]:mm:ss"
                elif kind == "dt":
                    cell = ws.cell(row=r, column=c, value=_local(v))
                    cell.number_format = "DD.MM.YYYY HH:MM:SS"
                elif kind == "list":
                    text_cell(ws, r, c, ", ".join(v or []))
                else:
                    text_cell(ws, r, c, v)
        last = len(data[table]) + 1
        if table == "totals" and last > 1:
            total_row = last + 1
            ws.cell(row=total_row, column=1, value="Всего").font = Font(bold=True)
            for c, (_name, _key, kind) in enumerate(cols, 1):
                if kind == "dur" or (kind == "int" and c > 3):
                    letter = get_column_letter(c)
                    cell = ws.cell(row=total_row, column=c, value="=SUM(%s2:%s%d)" % (letter, letter, last))
                    cell.font = Font(bold=True)
                    if kind == "dur":
                        cell.number_format = "[h]:mm:ss"
        for c, (name, _key, kind) in enumerate(cols, 1):
            width = {"text": 28, "dt": 20, "dur": 14, "int": 12, "list": 16}[kind]
            ws.column_dimensions[get_column_letter(c)].width = max(width, min(len(name) + 2, 34))
        ws.row_dimensions[1].height = 34
        ws.freeze_panes = "A2"
        if last > 1:
            ws.auto_filter.ref = "A1:%s%d" % (get_column_letter(len(cols)), last)

    ws = wb.create_sheet("Параметры")
    for r, (k, v) in enumerate(params, 1):
        ws.cell(row=r, column=1, value=k).font = Font(bold=True)
        text_cell(ws, r, 2, v)
        ws.cell(row=r, column=2).alignment = wrap
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 100

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
