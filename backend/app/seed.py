"""Демоданные: вымышленные подразделения, пользователи трёх ролей, два курса, библиотека.

Попыток, оценок и сертификатов в демоданных нет: на защите они появляются только из действий участников.
Материалы берутся из backend/demo/materials и проходят через обычное хранилище (контрольная сумма,
PDF-копии для Office), как при загрузке методистом.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from . import config
from .db import SessionLocal, utcnow
from .models import (
    Assessment, AssessmentRater, Assignment, Block, Competency, Course, CourseVersion, Department, LibraryItem,
    Question, Setting, StoredFile, User, new_id,
)
from .security import hash_password
from .services.storage import store_path

MATERIALS = config.DEMO_DIR / "materials"

DEMO_ACCOUNTS = [
    # логин, пароль, роль — для README и экрана входа в демо-режиме
    ("metodist", "metod2026", "Методист (отдел обучения)"),
    ("operator", "smena2026", "Сотрудник (оператор)"),
    ("master", "master2026", "Руководитель (мастер смены)"),
]


# Учётная запись для защиты. За неё никто не проходит курсы до выхода на сцену, поэтому на показе
# курс назначается и проходится впервые, а результаты репетиций под другими учётными записями остаются.
STAGE_LOGIN = "zashita"
STAGE_PASSWORD = "scena2026"
STAGE_NAME = "Сейтказина Асель Маратовна"
STAGE_POSITION = "Оператор таблет-пресса (стажёр)"
STAGE_DEPARTMENT = "Участок таблетирования"


def is_empty() -> bool:
    with SessionLocal() as db:
        return db.query(User).count() == 0


def ensure_stage_account() -> dict:
    """Возвращает учётную запись для защиты, за которую ещё ничего не назначали и не сдавали.

    Если прежняя уже использована (например, на репетиции), создаётся следующая: zashita2, zashita3…
    Ничего не удаляет — результаты репетиций остаются в отчётах.
    """
    from .models import Attempt

    with SessionLocal() as db:
        rows = db.query(User).filter(User.login.like(STAGE_LOGIN + "%"), User.is_active.is_(True)).order_by(User.created_at).all()
        for u in rows:
            used = db.query(Assignment).filter(Assignment.user_id == u.id).first() is not None \
                or db.query(Attempt).filter(Attempt.user_id == u.id).first() is not None
            if not used:
                return {"login": u.login, "full_name": u.full_name, "created": False}
        dept = db.query(Department).filter(Department.name == STAGE_DEPARTMENT).first() or db.query(Department).order_by(Department.name).first()
        taken_logins = {x for (x,) in db.query(User.login).all()}
        taken_numbers = {x for (x,) in db.query(User.personnel_no).all()}
        n = 1
        while (STAGE_LOGIN if n == 1 else "%s%d" % (STAGE_LOGIN, n)) in taken_logins or "1029%d" % n in taken_numbers:
            n += 1
        login = STAGE_LOGIN if n == 1 else "%s%d" % (STAGE_LOGIN, n)
        name = STAGE_NAME if n == 1 else "%s (%d)" % (STAGE_NAME, n)
        db.add(User(id=new_id(), personnel_no="1029%d" % n, full_name=name, login=login,
                    password_hash=hash_password(STAGE_PASSWORD), role="employee",
                    department_id=dept.id if dept else None, position=STAGE_POSITION))
        db.commit()
        return {"login": login, "full_name": name, "created": True}


def _local(y: int, m: int, d: int, hh: int = 9, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=config.LOCAL_TZ)


def _opts(*texts: str) -> list[dict]:
    return [{"id": new_id()[:8], "text": t} for t in texts]


def run() -> None:
    config.ensure_dirs()
    missing = [n for n in ("sop-ok-015-red1.pdf", "sop-ok-015-red2.pdf", "checklist-sop.docx", "reestr-sop.xlsx",
                           "pyatiminutka-sop.pptx", "video-sop-check.mp4", "gigiena-chistaya-zona.pdf",
                           "pamyatka-nesootvetstvie.pdf", "glossary-gmp.pdf", "zhurnal-smeny.docx",
                           "grafik-obucheniya.xlsx") if not (MATERIALS / n).exists()]
    if missing:
        raise RuntimeError("Нет учебных материалов: %s. Выполните: python backend/demo/make_materials.py" % ", ".join(missing))

    with SessionLocal() as db:
        # ---------- подразделения и люди ----------
        d_tab = Department(id=new_id(), name="Участок таблетирования")
        d_pack = Department(id=new_id(), name="Участок фасовки и упаковки")
        d_qa = Department(id=new_id(), name="Отдел обеспечения качества")
        d_edu = Department(id=new_id(), name="Отдел обучения")
        db.add_all([d_tab, d_pack, d_qa, d_edu])
        db.flush()

        def user(pn, name, login, password, role, dept, position) -> User:
            u = User(id=new_id(), personnel_no=pn, full_name=name, login=login, password_hash=hash_password(password),
                     role=role, department_id=dept.id, position=position)
            db.add(u)
            return u

        metodist = user("10001", "Жакупова Динара Ермековна", "metodist", "metod2026", "methodist", d_edu, "Методист отдела обучения")
        master = user("10210", "Сапаров Ержан Маратович", "master", "master2026", "manager", d_tab, "Мастер смены")
        operator = user("10234", "Ибраев Тимур Асхатович", "operator", "smena2026", "employee", d_tab, "Оператор таблет-пресса")
        kim = user("10235", "Ким Алина Викторовна", "kim.a", "demo2026", "employee", d_tab, "Оператор таблет-пресса")
        abdr = user("10236", "Абдрахманов Руслан Нурланович", "abdrakhmanov.r", "demo2026", "employee", d_tab, "Оператор таблет-пресса")
        smag = user("10237", "Смагулова Жанна Талгатовна", "smagulova.zh", "demo2026", "employee", d_tab, "Аппаратчик смешивания")
        petr = user("10238", "Петренко Олег Иванович", "petrenko.o", "demo2026", "employee", d_tab, "Наладчик оборудования")
        nurl = user("10310", "Нурланова Айгуль Болатовна", "nurlanova.a", "demo2026", "manager", d_pack, "Мастер участка фасовки")
        kas = user("10331", "Касымов Данияр Серикович", "kasymov.d", "demo2026", "employee", d_pack, "Оператор фасовочной линии")
        ivan = user("10332", "Иванова Марина Петровна", "ivanova.m", "demo2026", "employee", d_pack, "Укладчик-упаковщик")
        tul = user("10333", "Тулегенов Арман Кайратович", "tulegenov.a", "demo2026", "employee", d_pack, "Оператор фасовочной линии")
        akhm = user("10410", "Ахметова Гульнара Сериковна", "akhmetova.g", "demo2026", "manager", d_qa, "Начальник отдела обеспечения качества")
        li = user("10421", "Ли Светлана Олеговна", "li.s", "demo2026", "employee", d_qa, "Специалист по документации")
        user("10291", STAGE_NAME, STAGE_LOGIN, STAGE_PASSWORD, "employee", d_tab, STAGE_POSITION)
        db.flush()
        d_tab.manager_id, d_pack.manager_id, d_qa.manager_id = master.id, nurl.id, akhm.id

        for key, value in config.DEFAULT_SETTINGS.items():
            db.add(Setting(key=key, value=value))

        # ---------- файлы ----------
        def f(name: str) -> StoredFile:
            row = store_path(db, MATERIALS / name, uploaded_by=metodist.id)
            if row.conversion_status == "error":
                raise RuntimeError("Не удалось построить PDF-копию %s: %s" % (name, row.conversion_error))
            return row

        pdf1, pdf2 = f("sop-ok-015-red1.pdf"), f("sop-ok-015-red2.pdf")
        docx, xlsx, pptx, video = f("checklist-sop.docx"), f("reestr-sop.xlsx"), f("pyatiminutka-sop.pptx"), f("video-sop-check.mp4")
        hygiene, memo, glossary = f("gigiena-chistaya-zona.pdf"), f("pamyatka-nesootvetstvie.pdf"), f("glossary-gmp.pdf")
        journal, plan = f("zhurnal-smeny.docx"), f("grafik-obucheniya.xlsx")

        scenario = json.loads((config.ASSETS_DIR / "trainer" / "sop-revision.json").read_text(encoding="utf-8"))

        def add_blocks(v: CourseVersion, items: list[tuple]) -> None:
            for i, (btype, title, data, required) in enumerate(items):
                db.add(Block(id=new_id(), version_id=v.id, position=i, type=btype, title=title, data=data, required=required))

        def add_questions(v: CourseVersion, items: list[dict]) -> None:
            for i, q in enumerate(items):
                db.add(Question(id=new_id(), version_id=v.id, position=i, **q))

        def q_single(text: str, options: list[str], right: int, points: int = 1) -> dict:
            o = _opts(*options)
            return {"type": "single", "text": text, "options": o, "correct": o[right]["id"], "points": points}

        def q_multi(text: str, options: list[str], right: list[int], points: int = 2) -> dict:
            o = _opts(*options)
            return {"type": "multiple", "text": text, "options": o, "correct": [o[i]["id"] for i in right], "points": points}

        def q_order(text: str, options: list[str], points: int = 2) -> dict:
            o = _opts(*options)
            return {"type": "order", "text": text, "options": o, "correct": [x["id"] for x in o], "points": points}

        # ---------- курс 1: проверка актуальности СОП ----------
        title1 = "Как проверить актуальность СОП перед началом смены"
        c1 = Course(id=new_id(), title=title1, sop_code="СОП-ОК-015", created_by=metodist.id,
                    description="Четыре шага, которые оператор выполняет до начала работы, чтобы убедиться: "
                                "инструкция на рабочем месте — действующей редакции.")
        db.add(c1)
        v1 = CourseVersion(id=new_id(), course_id=c1.id, revision=1, status="published", title=title1,
                           sop_code="СОП-ОК-015", description=c1.description, pass_percent=70, max_attempts=3,
                           created_at=_local(2026, 1, 28), published_at=_local(2026, 2, 3), published_by=metodist.id)
        db.add(v1)
        intro1 = (
            "# Зачем проверять редакцию\n\n"
            "СОП меняются: уточняют параметры, добавляют шаги контроля. Если на рабочем месте остался старый "
            "экземпляр, вы можете выполнить работу правильно «по бумаге» и всё равно получить отклонение.\n\n"
            "Проверка занимает около двух минут и делается **до начала смены**.\n\n"
            "## Что нужно запомнить\n\n"
            "1. Найдите на титульном листе код документа и номер редакции.\n"
            "2. Откройте реестр действующих редакций на посту мастера смены.\n"
            "3. Сверьте код, номер редакции и дату введения. Проверьте штамп «Контролируемая копия».\n"
            "4. Совпало — распишитесь в журнале и работайте. Не совпало — не начинайте работу и сообщите мастеру смены.\n\n"
            "> Учебный образец. Данные вымышлены."
        )
        add_blocks(v1, [
            ("text", "Зачем проверять редакцию", {"text": intro1}, True),
            ("pdf", "Инструкция СОП-ОК-015, редакция 1", {"file_id": pdf1.id}, True),
            ("docx", "Чек-лист перед сменой", {"file_id": docx.id}, False),
        ])
        common_q = [
            q_single("Где записано, какая редакция СОП сейчас действует?",
                     ["В реестре действующих редакций на посту мастера смены", "На обложке папки, в которой лежит СОП",
                      "В памяти самого опытного оператора смены", "В графике отпусков участка"], 0, 1),
            q_multi("Какие признаки нужно сверить с реестром перед началом смены? Отметьте все верные.",
                    ["Код документа", "Номер редакции", "Дату введения в действие", "Цвет папки", "Фамилию предыдущего оператора"],
                    [0, 1, 2], 2),
        ]
        add_questions(v1, common_q + [
            q_order("Расставьте шаги проверки по порядку.",
                    ["Найти на титульном листе код и номер редакции", "Открыть реестр действующих редакций",
                     "Сверить код, редакцию и дату, проверить штамп «Контролируемая копия»",
                     "Расписаться в журнале начала смены и приступить к работе"], 2),
        ])

        v2 = CourseVersion(id=new_id(), course_id=c1.id, revision=2, status="draft", title=title1,
                           sop_code="СОП-ОК-015", description=c1.description, pass_percent=70, max_attempts=3,
                           change_note="Добавлена сверка по QR-коду электронного реестра. Уточнено: устаревший "
                                       "экземпляр изымает мастер смены под запись в журнале.",
                           created_at=_local(2026, 9, 24))
        db.add(v2)
        intro2 = (
            "# Зачем проверять редакцию\n\n"
            "СОП меняются: уточняют параметры, добавляют шаги контроля. Если на рабочем месте остался старый "
            "экземпляр, вы можете выполнить работу правильно «по бумаге» и всё равно получить отклонение. "
            "Серию при этом задержат до конца расследования.\n\n"
            "Проверка занимает около двух минут и делается **до начала смены**.\n\n"
            "## Пять шагов\n\n"
            "1. Найдите на титульном листе **код документа** и **номер редакции**.\n"
            "2. Откройте **реестр действующих редакций** на посту мастера смены и найдите строку с этим кодом.\n"
            "3. Сверьте **код, номер редакции и дату введения**. Проверьте синий штамп «Контролируемая копия».\n"
            "4. Отсканируйте **QR-код** на титульном листе: электронный реестр покажет действующую редакцию.\n"
            "5. Совпало — распишитесь в журнале начала смены и работайте.\n\n"
            "## Если не совпало\n\n"
            "- Не начинайте работу по этому документу.\n"
            "- Сообщите мастеру смены.\n"
            "- Экземпляр не выбрасывайте и не исправляйте: его изымает мастер под запись в журнале.\n\n"
            "> Учебный образец. Данные вымышлены."
        )
        add_blocks(v2, [
            ("text", "Зачем проверять редакцию", {"text": intro2}, True),
            ("pdf", "Инструкция СОП-ОК-015, редакция 2", {"file_id": pdf2.id}, True),
            ("video", "Видео: шаги проверки за 45 секунд", {"file_id": video.id}, False),
            ("docx", "Чек-лист перед сменой (Word)", {"file_id": docx.id}, False),
            ("xlsx", "Реестр действующих редакций (Excel)", {"file_id": xlsx.id}, False),
            ("pptx", "Пятиминутка: та ли редакция у вас в руках? (PowerPoint)", {"file_id": pptx.id}, False),
            ("link", "Правила надлежащей производственной практики ЕАЭС",
             {"url": "https://docs.eaeunion.org/", "mandatory": False, "alt_file_id": None,
              "note": "Справочно: портал правовых актов ЕАЭС. Для аттестации не требуется."}, False),
            ("trainer", "Тренажёр: в смене оказалась инструкция другой редакции", {"scenario": scenario}, False),
        ])
        add_questions(v2, common_q + [
            q_order("Расставьте шаги проверки по порядку.",
                    ["Найти на титульном листе код и номер редакции", "Открыть реестр действующих редакций",
                     "Сверить код, редакцию и дату, проверить штамп «Контролируемая копия»",
                     "Отсканировать QR-код и сверить с электронным реестром",
                     "Расписаться в журнале начала смены и приступить к работе"], 2),
        ])

        # ---------- курс 2: гигиена ----------
        title2 = "Гигиена и переодевание при входе в чистую зону"
        c2 = Course(id=new_id(), title=title2, sop_code="СОП-ПР-007", created_by=metodist.id,
                    description="Порядок подготовки к входу в чистую зону: руки, одежда, самоконтроль.")
        db.add(c2)
        v_h = CourseVersion(id=new_id(), course_id=c2.id, revision=3, status="published", title=title2,
                            sop_code="СОП-ПР-007", description=c2.description, pass_percent=75, max_attempts=2,
                            created_at=_local(2026, 5, 10), published_at=_local(2026, 5, 15), published_by=metodist.id)
        db.add(v_h)
        add_blocks(v_h, [
            ("text", "Коротко о главном", {"text": (
                "# Чистая зона начинается с вас\n\n"
                "Человек — главный источник загрязнений в чистом помещении. Правила переодевания защищают продукт, "
                "а значит и пациента.\n\n"
                "- Украшения и часы остаются в шкафчике.\n"
                "- Руки моем не меньше **30 секунд**.\n"
                "- Одежду надеваем строго по порядку: шапочка, маска, комбинезон, бахилы.\n"
                "- Повреждённую одежду заменяем сразу.\n\n"
                "> Учебный образец. Данные вымышлены.")}, True),
            ("pdf", "Инструкция СОП-ПР-007, редакция 3", {"file_id": hygiene.id}, True),
        ])
        add_questions(v_h, [
            q_single("Сколько времени нужно мыть руки перед входом в чистую зону?",
                     ["Не меньше 30 секунд", "5 секунд достаточно", "Ровно 2 минуты", "Мыть не нужно, если есть перчатки"], 0, 1),
            q_multi("Что нужно снять и оставить в шкафчике? Отметьте все верные.",
                    ["Часы", "Кольца и браслеты", "Пропуск на шнурке поверх одежды", "Рабочий комбинезон"], [0, 1, 2], 2),
            q_order("Расставьте действия по порядку.",
                    ["Снять украшения и часы", "Вымыть руки с мылом", "Надеть технологическую одежду",
                     "Обработать руки антисептиком", "Проверить себя у зеркала"], 2),
            {"type": "open", "text": "Вы заметили, что у комбинезона разошёлся шов. Что вы сделаете? Ответьте одним-двумя предложениями.",
             "options": [], "correct": None, "points": 2,
             "criteria": "2 балла: не входит в зону, заменяет одежду и сообщает мастеру. 1 балл: только заменяет одежду. "
                         "0 баллов: продолжает работу или чинит сам."},
        ])
        db.flush()

        # ---------- назначения ----------
        now = utcnow()

        def assign(v: CourseVersion, u: User, days: int, via: Department | None = None) -> None:
            db.add(Assignment(id=new_id(), course_id=v.course_id, version_id=v.id, user_id=u.id,
                              due_at=now + timedelta(days=days), assigned_by=metodist.id,
                              via_department_id=via.id if via else None))

        for u in (kim, abdr):
            assign(v1, u, 10)
        # Срок курса по гигиене намеренно дальний: задание, назначенное на защите (срок — неделя),
        # должно оказаться «ближайшим» на первом экране оператора, даже если демоданные загружены давно.
        for u in (operator, kim, abdr, smag, petr, master):
            assign(v_h, u, 60, d_tab)
        for u in (kas, ivan, tul, nurl):
            assign(v_h, u, 60, d_pack)

        # ---------- библиотека ----------
        def lib(title: str, code: str, category: str, description: str, stored: StoredFile) -> None:
            db.add(LibraryItem(id=new_id(), title=title, sop_code=code, category=category, description=description,
                               file_id=stored.id, created_by=metodist.id))

        lib("Памятка: в смене оказалась инструкция другой редакции", "СОП-ОК-015", "Памятки",
            "Пять правил на одной странице и внутренние телефоны.", memo)
        lib("Краткий глоссарий для оператора", "", "Справочники",
            "СОП, редакция, контролируемая копия, отклонение — простыми словами.", glossary)
        lib("Реестр действующих редакций СОП — участок таблетирования", "", "Реестры",
            "Таблица: код, название, действующая редакция, дата введения.", xlsx)
        lib("Гигиена и переодевание при входе в чистую зону", "СОП-ПР-007", "Инструкции",
            "Действующая редакция 3.", hygiene)
        lib("Образец заполнения журнала приёма-передачи смены", "", "Образцы документов",
            "Заполненный пример с отметкой о проверке СОП.", journal)
        lib("График обучения на IV квартал 2026", "", "Планы",
            "Темы, сроки и ответственные по подразделениям.", plan)
        lib("Пятиминутка: та ли редакция у вас в руках?", "СОП-ОК-015", "Презентации",
            "Шесть слайдов для разбора в начале смены.", pptx)

        # ---------- оценка 180/360 ----------
        comps = [
            Competency(id=new_id(), name="Соблюдение СОП и ведение записей",
                       description="Работает по действующим редакциям, записи делает сразу и разборчиво."),
            Competency(id=new_id(), name="Внимательность к качеству",
                       description="Замечает отклонения и сообщает о них, не дожидаясь проверки."),
            Competency(id=new_id(), name="Работа в команде смены",
                       description="Передаёт смену без недомолвок, помогает коллегам."),
            Competency(id=new_id(), name="Наставничество",
                       description="Объясняет новичкам правила и показывает на примере."),
            Competency(id=new_id(), name="Безопасность труда",
                       description="Использует средства защиты, не обходит блокировки."),
        ]
        db.add_all(comps)
        db.flush()
        a = Assessment(id=new_id(), title="Оценка 360°: Ибраев Т. А., IV квартал 2026", subject_id=operator.id,
                       mode="360", created_by=metodist.id, competency_ids=[c.id for c in comps[:3]])
        db.add(a)
        db.add(AssessmentRater(id=new_id(), assessment_id=a.id, rater_id=operator.id, role="self", scores={}))
        db.add(AssessmentRater(id=new_id(), assessment_id=a.id, rater_id=master.id, role="manager", scores={}))
        for peer in (kim, abdr):
            db.add(AssessmentRater(id=new_id(), assessment_id=a.id, rater_id=peer.id, role="peer", scores={}))

        db.commit()

    sample = config.PROJECT_DIR / "samples" / "employees_import.csv"
    if not sample.exists():
        from .services.csvio import to_csv_bytes
        sample.parent.mkdir(exist_ok=True)
        sample.write_bytes(to_csv_bytes(
            ["Табельный номер", "ФИО", "Подразделение", "Должность", "Роль"],
            [["30101", "Жумабаев Алмас Кайратович", "Участок фасовки и упаковки", "Оператор фасовочной линии", "Сотрудник"],
             ["30102", "Ким Виктория Сергеевна", "Участок фасовки и упаковки", "Укладчик-упаковщик", "Сотрудник"],
             ["30103", "Оспанов Данияр Ерланович", "Склад готовой продукции", "Кладовщик", "Сотрудник"],
             ["30104", "Сергеева Наталья Юрьевна", "Склад готовой продукции", "Начальник склада", "Руководитель"],
             ["10234", "Ибраев Тимур Асхатович", "Участок таблетирования", "Оператор таблет-пресса", "Сотрудник"],
             ["", "Безтабельный Пример Строки", "Склад готовой продукции", "Грузчик", "Сотрудник"]]))
