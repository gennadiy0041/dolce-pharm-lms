# -*- coding: utf-8 -*-
"""Генератор учебных материалов для демо-LMS Dolce Pharm.

Запуск (из корня проекта):
    .venv\\Scripts\\python.exe backend\\demo\\make_materials.py
    .venv\\Scripts\\python.exe backend\\demo\\make_materials.py --only sop-ok-015-red1.pdf,checklist-sop.docx
    .venv\\Scripts\\python.exe backend\\demo\\make_materials.py --skip-video

Скрипт детерминирован: в текстах только фиксированные даты, метаданные файлов закреплены,
поэтому повторный запуск даёт те же документы. Всё пишется в backend/demo/materials/.

Все тексты, таблицы, слайды, рисунки и видео — оригинальные, созданы для прототипа.
Предприятие, люди, коды документов и номера телефонов вымышлены.

Для видео нужен ffmpeg (по умолчанию C:\\ffmpeg\\bin\\ffmpeg.exe, можно задать FFMPEG_PATH).
Кадры видео рисуются во временную папку (LMS_MATERIALS_TMP или системный TEMP), не в проект.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True      # не оставлять __pycache__ в backend/app при импорте помощников
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from PIL import Image, ImageDraw, ImageFont

from reportlab.graphics.shapes import Circle, Drawing, Group, Line, Rect, String, mmult, rotate, translate
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Flowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer
from reportlab.platypus import Table as RLTable
from reportlab.platypus import TableStyle

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm
from docx.shared import Pt as DPt
from docx.shared import RGBColor as DRGB

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from pptx import Presentation
from pptx.dml.color import RGBColor as PRGB
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Inches
from pptx.util import Pt as PPt

try:  # штатный помощник проекта: регистрация DejaVu в reportlab
    from app.config import FONTS_DIR
    from app.services.pdf_fonts import CONDENSED, CONDENSED_BOLD, SANS, SANS_BOLD, register_fonts
except Exception:  # запасной путь, если backend/app в этот момент кто-то правит
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    FONTS_DIR = BACKEND_DIR / "app" / "assets" / "fonts"
    SANS, SANS_BOLD = "DejaVu", "DejaVu-Bold"
    CONDENSED, CONDENSED_BOLD = "DejaVuCondensed", "DejaVuCondensed-Bold"

    def register_fonts() -> None:
        files = {SANS: "DejaVuSans.ttf", SANS_BOLD: "DejaVuSans-Bold.ttf",
                 CONDENSED: "DejaVuSansCondensed.ttf", CONDENSED_BOLD: "DejaVuSansCondensed-Bold.ttf"}
        for name, filename in files.items():
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(FONTS_DIR / filename)))
        for family, bold in ((SANS, SANS_BOLD), (CONDENSED, CONDENSED_BOLD)):   # <b> внутри Paragraph
            addMapping(family, 0, 0, family)
            addMapping(family, 1, 0, bold)
            addMapping(family, 0, 1, family)
            addMapping(family, 1, 1, bold)


OUT_DIR = Path(__file__).resolve().parent / "materials"
FFMPEG = os.environ.get("FFMPEG_PATH") or r"C:\ffmpeg\bin\ffmpeg.exe"

# ════════════════════════════════════════════════════════════════════════════════════════
# 1. Палитра и единое содержание (на него опираются тест и диалоговый тренажёр)
# ════════════════════════════════════════════════════════════════════════════════════════

HEX_TEAL = "0E5F58"        # основной акцент
HEX_AMBER = "E8A33D"       # тёплый акцент, подсветка
HEX_INK = "1B2A2A"         # текст
HEX_PAPER = "F5F3EE"       # фон
HEX_TEAL_TINT = "E3EFEC"
HEX_AMBER_TINT = "FBEFD8"
HEX_LINE = "C9D1CE"
HEX_MUTED = "5B6A68"
HEX_STAMP = "1F4FA3"       # синий штамп «Контролируемая копия»
HEX_STOP = "B8432F"
HEX_WHITE = "FFFFFF"

NOTICE = "Учебный образец. Данные вымышлены."
ORG = "Dolce Pharm"
AREA = "Участок таблетирования"
AUTHOR = "Dolce Pharm — отдел обучения (учебный образец)"
FIXED_DT = dt.datetime(2026, 11, 2, 9, 0, 0)      # закреплённая дата в метаданных файлов

MASTER = "Ержан Сапаров"
QA_HEAD = "Гульнара Ахметова"
TEL_MASTER, TEL_QA, TEL_TRAINING = "2-14", "3-05", "4-20"

SOP_CODE = "СОП-ОК-015"
SOP_TITLE = "Проверка актуальности СОП перед началом смены"

# Шаги оператора. Формулировки — эталон для теста, менять только вместе с ним.
STEPS_RED1 = [
    "Возьмите рабочий экземпляр СОП на своём рабочем месте и найдите на титульном листе "
    "<b>код документа</b> и <b>номер редакции</b>.",
    "Откройте <b>«Реестр действующих редакций СОП»</b> участка (папка на посту мастера смены) "
    "и найдите строку с тем же кодом.",
    "Сверьте три признака: <b>код</b>, <b>номер редакции</b>, <b>дату введения в действие</b>. "
    "Проверьте синий штамп <b>«Контролируемая копия»</b> с номером экземпляра.",
    "Всё совпало — распишитесь в журнале начала смены и приступайте к работе. "
    "Не совпал хотя бы один признак — <b>не начинайте работу по этому документу</b>, "
    "<b>сообщите мастеру смены</b>, экземпляр не выбрасывайте и не исправляйте сами.",
]
STEPS_RED2 = STEPS_RED1[:3] + [
    "Отсканируйте <b>QR-код</b> на титульном листе — электронный реестр покажет действующую редакцию.",
    "Всё совпало — распишитесь в журнале начала смены и приступайте к работе. "
    "Не совпал хотя бы один признак — <b>не начинайте работу по этому документу</b> и "
    "<b>сообщите мастеру смены</b>. <b>Передайте устаревший экземпляр мастеру смены</b>: мастер изымает его "
    "и делает запись в журнале изъятия. Оператор получает актуальный экземпляр под подпись. "
    "Экземпляр не выбрасывайте и не исправляйте сами.",
]

RED1 = {
    "code": SOP_CODE, "title": SOP_TITLE, "rev": "1", "date": "03.02.2026", "copy_no": "07",
    "approvals": [("Разработал", "Отдел обучения", "20.01.2026"),
                  ("Согласовал", f"{MASTER}, мастер смены", "27.01.2026"),
                  ("Утвердил", f"{QA_HEAD}, начальник отдела обеспечения качества", "30.01.2026")],
}
RED2 = {
    "code": SOP_CODE, "title": SOP_TITLE, "rev": "2", "date": "01.10.2026", "copy_no": "07",
    "approvals": [("Разработал", "Отдел обучения", "14.09.2026"),
                  ("Согласовал", f"{MASTER}, мастер смены", "21.09.2026"),
                  ("Утвердил", f"{QA_HEAD}, начальник отдела обеспечения качества", "25.09.2026")],
}
# Редакция 3 выпущена перед защитой: порядок проверки тот же, уточнён раздел «Если данные не совпали».
RED3 = {
    "code": SOP_CODE, "title": SOP_TITLE, "rev": "3", "date": "20.11.2026", "copy_no": "07",
    "approvals": [("Разработал", "Отдел обучения", "09.11.2026"),
                  ("Согласовал", f"{MASTER}, мастер смены", "13.11.2026"),
                  ("Утвердил", f"{QA_HEAD}, начальник отдела обеспечения качества", "18.11.2026")],
}
HYGIENE = {
    "code": "СОП-ПР-007", "title": "Гигиена и переодевание при входе в чистую зону", "rev": "3",
    "date": "15.05.2026", "copy_no": "03",
    "approvals": [("Разработал", "Отдел обучения", "28.04.2026"),
                  ("Согласовал", "Айгерим Нурланова, технолог участка", "05.05.2026"),
                  ("Утвердил", f"{QA_HEAD}, начальник отдела обеспечения качества", "12.05.2026")],
}


def _d(text: str) -> dt.date:
    """«ДД.ММ.ГГГГ» → date (для настоящих ячеек-дат в Excel)."""
    return dt.datetime.strptime(text, "%d.%m.%Y").date()


# Реестр действующих редакций: код, название, редакция, дата введения, место хранения, ответственный
REGISTRY = [
    ("СОП-ОК-003", "Управление документацией участка", 4, "12.01.2026", "Пост мастера смены, папка № 1", "Ахметова Г."),
    ("СОП-ОК-009", "Действия при выявлении отклонения", 2, "20.03.2026", "Пост мастера смены, папка № 1", "Ахметова Г."),
    ("СОП-ОК-015", SOP_TITLE, 2, "01.10.2026", "Зал таблетирования, рабочие места операторов", "Сапаров Е."),
    ("СОП-ПР-007", "Гигиена и переодевание при входе в чистую зону", 3, "15.05.2026", "Шлюз чистой зоны, стенд у входа", "Нурланова А."),
    ("СОП-ПР-011", "Взвешивание сырья", 2, "02.03.2026", "Весовая, рабочее место № 1", "Жумабеков Д."),
    ("СОП-ПР-014", "Ведение журнала приёма-передачи смены", 1, "10.02.2026", "Пост мастера смены, папка № 2", "Сапаров Е."),
    ("СОП-ТБ-021", "Эксплуатация таблет-пресса", 5, "07.09.2026", "Зал таблетирования, у пресса № 1", "Ким В."),
    ("СОП-ТБ-022", "Переналадка таблет-пресса при смене продукта", 2, "18.06.2026", "Зал таблетирования, у пресса № 2", "Ким В."),
    ("СОП-ТБ-025", "Контроль массы и твёрдости таблеток в процессе производства", 3, "14.04.2026", "Зал таблетирования, пост контроля", "Оспанова М."),
    ("СОП-ОЧ-004", "Очистка таблет-пресса после серии", 4, "25.08.2026", "Моечная, стенд у входа", "Байжанов Р."),
    ("СОП-ОЧ-006", "Очистка помещений участка", 2, "30.01.2026", "Комната уборочного инвентаря", "Байжанов Р."),
    ("СОП-ТБ-030", "Действия при аварийной остановке оборудования", 1, "17.11.2025", "Зал таблетирования, стенд у пульта", "Сапаров Е."),
]
# Изъятые редакции: код, редакция, дата изъятия, причина, кто изъял
WITHDRAWN = [
    ("СОП-ОК-015", 1, "01.10.2026", "Введена редакция 2: добавлен шаг с QR-кодом, уточнён порядок изъятия", "Сапаров Е."),
    ("СОП-ТБ-021", 4, "07.09.2026", "Введена редакция 5: уточнены параметры пуска пресса", "Сапаров Е."),
    ("СОП-ОЧ-004", 3, "25.08.2026", "Введена редакция 4: изменён порядок разборки узлов", "Тулегенова С."),
    ("СОП-ТБ-022", 1, "18.06.2026", "Введена редакция 2: добавлен контрольный лист переналадки", "Сапаров Е."),
    ("СОП-ПР-007", 2, "15.05.2026", "Введена редакция 3: уточнён порядок надевания одежды", "Тулегенова С."),
]
# График обучения: подразделение, тема, код СОП, срок, ответственный, форма
TRAINING_PLAN = [
    ("Участок таблетирования", "Проверка актуальности СОП перед началом смены (редакция 2)", "СОП-ОК-015", "09.10.2026", "Сапаров Е.", "Электронный курс + тест"),
    ("Участок таблетирования", "Гигиена и переодевание при входе в чистую зону", "СОП-ПР-007", "23.10.2026", "Нурланова А.", "Электронный курс + практика в шлюзе"),
    ("Участок таблетирования", "Эксплуатация таблет-пресса (редакция 5)", "СОП-ТБ-021", "30.10.2026", "Ким В.", "Инструктаж на рабочем месте"),
    ("Весовая", "Взвешивание сырья", "СОП-ПР-011", "13.11.2026", "Жумабеков Д.", "Электронный курс + тест"),
    ("Участок таблетирования", "Очистка таблет-пресса после серии", "СОП-ОЧ-004", "20.11.2026", "Байжанов Р.", "Инструктаж на рабочем месте"),
    ("Отдел обеспечения качества", "Действия при выявлении отклонения", "СОП-ОК-009", "27.11.2026", "Ахметова Г.", "Семинар"),
    ("Все смены участка", "Ведение журнала приёма-передачи смены", "СОП-ПР-014", "04.12.2026", "Сапаров Е.", "Пятиминутка на смене"),
    ("Новые сотрудники", "Вводный курс: документы участка и основы GMP", "—", "18.12.2026", "Отдел обучения", "Электронный курс + тест"),
]

GLOSSARY = [
    ("СОП", "Стандартная операционная процедура — утверждённая пошаговая инструкция: как выполнять операцию, "
            "чтобы результат каждый раз был одинаковым."),
    ("Редакция", "Версия документа со своим номером. Работать можно только по одной редакции — той, что указана в реестре."),
    ("Контролируемая копия", "Учтённый экземпляр документа с синим штампом и номером. Известно, где он лежит, "
                             "и при смене редакции его заменяют."),
    ("Реестр действующих документов", "Список, где для каждого документа указаны код, действующая редакция и дата "
                                      "её введения. На участке это «Реестр действующих редакций СОП»."),
    ("Дата введения в действие", "День, с которого нужно работать по новой редакции. Прежняя редакция с этого дня не действует."),
    ("Изъятие документа", "Устаревший экземпляр забирают с рабочего места, чтобы по нему никто не работал. "
                          "Запись об этом делают в журнале изъятия."),
    ("Отклонение", "Любое отступление от утверждённой инструкции или технологии. О нём сообщают, его записывают и расследуют."),
    ("Серия", "Количество продукции, изготовленное за один производственный цикл в одинаковых условиях. У серии есть свой номер."),
    ("GMP", "Надлежащая производственная практика — правила, по которым выпускают лекарства стабильного качества: "
            "чистота, обученные люди, точные записи."),
    ("Мастер смены", "Руководитель смены на участке. Ведёт реестр, выдаёт и изымает экземпляры СОП; к нему обращаются "
                     "при любом несоответствии."),
    ("Отдел обеспечения качества", "Подразделение, которое утверждает документы, следит за соблюдением правил "
                                   "и расследует отклонения."),
    ("Лист ознакомления", "Список, в котором работник подписью подтверждает, что прочитал и понял документ."),
]

# ════════════════════════════════════════════════════════════════════════════════════════
# 2. Общие помощники PDF (reportlab)
# ════════════════════════════════════════════════════════════════════════════════════════


def _c(hex_: str):
    return colors.HexColor("#" + hex_)


C_TEAL, C_AMBER, C_INK, C_PAPER = _c(HEX_TEAL), _c(HEX_AMBER), _c(HEX_INK), _c(HEX_PAPER)
C_TEAL_TINT, C_AMBER_TINT, C_LINE, C_MUTED = _c(HEX_TEAL_TINT), _c(HEX_AMBER_TINT), _c(HEX_LINE), _c(HEX_MUTED)
C_STAMP, C_STOP, C_NOTICE = _c(HEX_STAMP), _c(HEX_STOP), _c("7A4E08")

PAGE_W, PAGE_H = A4
MX = 18 * mm                      # боковое поле
BODY_W = PAGE_W - 2 * MX          # ширина текстовой колонки
FRAME_PAD = 6                     # внутренний отступ рамки SimpleDocTemplate


def _styles() -> dict:
    """Стили абзацев PDF. Основной текст 11,5 pt с увеличенным интерлиньяжем — читается с телефона."""
    register_fonts()
    body = ParagraphStyle("body", fontName=SANS, fontSize=11.5, leading=17, textColor=C_INK, spaceAfter=6)
    return {
        "body": body,
        "kicker": ParagraphStyle("kicker", fontName=CONDENSED_BOLD, fontSize=9.5, leading=12, textColor=C_TEAL, spaceAfter=5),
        "title": ParagraphStyle("title", fontName=SANS_BOLD, fontSize=21, leading=26.5, textColor=C_INK, spaceAfter=7),
        "meta": ParagraphStyle("meta", fontName=SANS, fontSize=10, leading=14.5, textColor=C_MUTED),
        "h2": ParagraphStyle("h2", fontName=SANS_BOLD, fontSize=14, leading=18, textColor=C_TEAL,
                             spaceBefore=12, spaceAfter=6, keepWithNext=1),
        "h3": ParagraphStyle("h3", fontName=SANS_BOLD, fontSize=12, leading=16, textColor=C_INK, spaceAfter=5),
        "step": ParagraphStyle("step", parent=body, spaceAfter=0),
        "bullet": ParagraphStyle("bullet", parent=body, leftIndent=17, bulletIndent=3, spaceAfter=4,
                                 bulletFontName=SANS_BOLD, bulletFontSize=11.5, bulletColor=C_TEAL),
        "small": ParagraphStyle("small", fontName=SANS, fontSize=9, leading=13, textColor=C_MUTED),
        "cell": ParagraphStyle("cell", fontName=SANS, fontSize=10.5, leading=15, textColor=C_INK),
        "appr": ParagraphStyle("appr", fontName=SANS, fontSize=9, leading=12.5, textColor=C_INK),
        "legend": ParagraphStyle("legend", fontName=SANS, fontSize=10, leading=14, textColor=C_INK),
        "notice": ParagraphStyle("notice", fontName=SANS_BOLD, fontSize=9, leading=12, textColor=C_NOTICE),
        "end": ParagraphStyle("end", fontName=SANS, fontSize=9, leading=13, textColor=C_MUTED, alignment=1, spaceBefore=12),
    }


def _numbered_canvas(draw_chrome):
    """Canvas, который дорисовывает оформление страницы, когда уже известно общее число страниц («Стр. X из Y»)."""

    class _Canvas(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_pages = []

        def showPage(self):
            self._saved_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_pages)
            for number, state in enumerate(self._saved_pages, 1):
                self.__dict__.update(state)
                draw_chrome(self, number, total)
                super().showPage()
            super().save()

    return _Canvas


def _draw_footer(c, right_text: str) -> None:
    """Нижний колонтитул: линия, пометка об учебном образце и подпись справа."""
    c.setStrokeColor(C_LINE)
    c.setLineWidth(0.6)
    c.line(MX, 15 * mm, PAGE_W - MX, 15 * mm)
    c.setFillColor(C_NOTICE)
    c.setFont(SANS_BOLD, 8)
    c.drawString(MX, 10.3 * mm, NOTICE)
    c.setFillColor(C_MUTED)
    c.setFont(SANS, 8)
    c.drawRightString(PAGE_W - MX, 10.3 * mm, right_text)


def _sop_chrome(c, page: int, total: int, meta: dict) -> None:
    """Шапка контролируемого документа на каждой странице: предприятие | код | редакция | дата | страница."""
    c.saveState()
    h = 15 * mm
    y0 = PAGE_H - 12 * mm - h
    shares = [0.30, 0.22, 0.13, 0.21, 0.14]
    labels = ["", "КОД ДОКУМЕНТА", "РЕДАКЦИЯ", "ВВЕДЕНА В ДЕЙСТВИЕ", "СТРАНИЦА"]
    values = ["", meta["code"], meta["rev"], meta["date"], f"{page} из {total}"]
    x = MX
    for i, share in enumerate(shares):
        w = BODY_W * share
        if i == 0:
            c.setFillColor(C_TEAL)
            c.rect(x, y0, w, h, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.setFont(SANS_BOLD, 13.5)
            c.drawString(x + 9, y0 + h - 19, ORG)
            c.setFillColor(_c("CFE3E0"))
            c.setFont(CONDENSED, 8)
            c.drawString(x + 9, y0 + 8.5, AREA)
        else:
            c.setFillColor(C_MUTED)
            c.setFont(CONDENSED, 7.5)
            c.drawString(x + 7, y0 + h - 13.5, labels[i])
            c.setFillColor(C_INK)
            c.setFont(SANS_BOLD, 11.5)
            c.drawString(x + 7, y0 + 9.5, values[i])
            c.setStrokeColor(C_TEAL)
            c.setLineWidth(0.7)
            c.line(x, y0, x, y0 + h)
        x += w
    c.setStrokeColor(C_TEAL)
    c.setLineWidth(0.9)
    c.rect(MX, y0, BODY_W, h, stroke=1, fill=0)
    _draw_footer(c, f"{ORG} · отдел обучения")
    c.restoreState()


def _library_chrome(c, page: int, total: int, doc_title: str) -> None:
    """Облегчённое оформление справочных материалов: строка с названием и колонтитул с номером страницы."""
    c.saveState()
    y = PAGE_H - 15 * mm
    c.setFillColor(C_TEAL)
    c.setFont(SANS_BOLD, 11)
    c.drawString(MX, y, ORG)
    c.setFillColor(C_MUTED)
    c.setFont(SANS, 8.5)
    c.drawRightString(PAGE_W - MX, y, doc_title)
    c.setStrokeColor(C_TEAL)
    c.setLineWidth(1.6)
    c.line(MX, y - 6, PAGE_W - MX, y - 6)
    _draw_footer(c, f"Стр. {page} из {total}")
    c.restoreState()


class _Badge(Flowable):
    """Кружок с номером шага."""

    def __init__(self, text: str, d: float = 24, fill=None, color=None, size: float = 12):
        super().__init__()
        self.text, self.d, self.size = text, d, size
        self.fill, self.color = fill or C_TEAL, color or colors.white

    def wrap(self, aw, ah):
        return self.d, self.d

    def draw(self):
        c, r = self.canv, self.d / 2
        c.setFillColor(self.fill)
        c.circle(r, r, r, stroke=0, fill=1)
        c.setFillColor(self.color)
        c.setFont(SANS_BOLD, self.size)
        c.drawCentredString(r, r - self.size * 0.35, self.text)


def _qr_modules(n: int = 21, seed: int = 15) -> list:
    """Условный рисунок QR-кода (не считывается): три угловых метки и детерминированный узор."""
    rnd = random.Random(seed)
    grid = [[rnd.random() < 0.48 for _ in range(n)] for _ in range(n)]

    def finder(r0: int, c0: int) -> None:
        for r in range(-1, 8):
            for col in range(-1, 8):
                rr, cc = r0 + r, c0 + col
                if 0 <= rr < n and 0 <= cc < n:
                    inside = 0 <= r <= 6 and 0 <= col <= 6
                    ring = inside and (r in (0, 6) or col in (0, 6))
                    core = 2 <= r <= 4 and 2 <= col <= 4
                    grid[rr][cc] = ring or core

    finder(0, 0)
    finder(0, n - 7)
    finder(n - 7, 0)
    return grid


class _TitleMarks(Flowable):
    """Отметки титульного листа: синий штамп «Контролируемая копия» и (с редакции 2) QR-код."""

    W = 150

    def __init__(self, copy_no: str, with_qr: bool = False):
        super().__init__()
        self.copy_no, self.with_qr = copy_no, with_qr
        self.H = 128 if with_qr else 70

    def wrap(self, aw, ah):
        return self.W, self.H

    def draw(self):
        c = self.canv
        sw, sh = 132, 50
        c.saveState()
        c.translate(self.W - sw / 2 - 6, self.H - sh / 2 - 10)
        c.rotate(5)
        c.setStrokeColor(C_STAMP)
        c.setLineWidth(1.7)
        c.roundRect(-sw / 2, -sh / 2, sw, sh, 5, stroke=1, fill=0)
        c.setLineWidth(0.6)
        c.roundRect(-sw / 2 + 3.2, -sh / 2 + 3.2, sw - 6.4, sh - 6.4, 3, stroke=1, fill=0)
        c.setFillColor(C_STAMP)
        c.setFont(SANS_BOLD, 10)
        c.drawCentredString(0, 9, "КОНТРОЛИРУЕМАЯ")
        c.drawCentredString(0, -2.5, "КОПИЯ")
        c.setFont(SANS, 8)
        c.drawCentredString(0, -15, f"экземпляр № {self.copy_no}")
        c.restoreState()
        if self.with_qr:
            size, n = 44, 21
            m = size / n
            x0, y0 = self.W - size - 4, 6
            c.setFillColor(colors.white)
            c.rect(x0 - 3, y0 - 3, size + 6, size + 6, stroke=0, fill=1)
            c.setFillColor(C_INK)
            for r, row in enumerate(_qr_modules(n)):
                for col, on in enumerate(row):
                    if on:
                        c.rect(x0 + col * m, y0 + (n - 1 - r) * m, m + 0.05, m + 0.05, stroke=0, fill=1)
            c.setFillColor(C_MUTED)
            c.setFont(CONDENSED, 7.5)
            c.drawRightString(x0 - 8, y0 + 26, "QR-код:")
            c.drawRightString(x0 - 8, y0 + 16, "электронный реестр")


def _notice_pill(st: dict) -> RLTable:
    """Плашка «Учебный образец. Данные вымышлены.» рядом с заголовком документа."""
    pill = RLTable([[Paragraph(NOTICE, st["notice"])]], colWidths=[stringWidth(NOTICE, SANS_BOLD, 9) + 24], hAlign="LEFT")
    pill.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_AMBER_TINT), ("ROUNDEDCORNERS", [9, 9, 9, 9]),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return pill


def _sop_title_block(st: dict, meta: dict, with_qr: bool = False) -> list:
    """Титульный блок: вид документа, название, реквизиты, штамп, пометка об образце, согласование."""
    left = [
        Paragraph("СТАНДАРТНАЯ ОПЕРАЦИОННАЯ ПРОЦЕДУРА", st["kicker"]),
        Paragraph(meta["title"], st["title"]),
        Paragraph(f"{meta['code']} · редакция {meta['rev']} · введена в действие {meta['date']}", st["meta"]),
        Spacer(1, 8),
        _notice_pill(st),
    ]
    head = RLTable([[left, _TitleMarks(meta["copy_no"], with_qr)]], colWidths=[BODY_W - _TitleMarks.W, _TitleMarks.W])
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    cells = [Paragraph(f'<font name="{CONDENSED_BOLD}" size="8" color="#{HEX_MUTED}">{label.upper()} · {date}</font>'
                       f'<br/>{who}', st["appr"])
             for label, who, date in meta["approvals"]]
    appr = RLTable([cells], colWidths=[BODY_W * 0.27, BODY_W * 0.33, BODY_W * 0.40])
    appr.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, C_LINE), ("INNERGRID", (0, 0), (-1, -1), 0.6, C_LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return [head, Spacer(1, 10), appr]


def _steps_table(st: dict, steps: list, heading: str | None = None) -> RLTable:
    """Нумерованные шаги: кружок с номером слева, короткий абзац справа.

    Если передан heading, заголовок раздела становится первой строкой таблицы: тогда длинный список
    может перейти на следующую страницу, а заголовок не оторвётся от первого шага."""
    rows = [[_Badge(str(i)), Paragraph(text, st["step"])] for i, text in enumerate(steps, 1)]
    first = 0
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]
    if heading:
        head_style = ParagraphStyle("h2_in_table", parent=st["h2"], spaceBefore=0, spaceAfter=0, keepWithNext=0)
        rows.insert(0, [Paragraph(heading, head_style), ""])
        first = 1
        style += [("SPAN", (0, 0), (1, 0)), ("TOPPADDING", (0, 0), (-1, 0), 12), ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
                  ("NOSPLIT", (0, 0), (-1, 1))]
    style += [
        ("TOPPADDING", (0, first), (0, -1), 6), ("TOPPADDING", (1, first), (1, -1), 8),
        ("BOTTOMPADDING", (0, first), (-1, -1), 8),
        ("LINEBELOW", (0, first), (-1, -2), 0.6, C_LINE),
    ]
    t = RLTable(rows, colWidths=[36, BODY_W - 36])
    t.setStyle(TableStyle(style))
    return t


def _callout(flowables: list, bar=None, bg=None) -> RLTable:
    """Выделенный блок с цветной полосой слева."""
    t = RLTable([[flowables]], colWidths=[BODY_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg or C_AMBER_TINT),
        ("LINEBEFORE", (0, 0), (0, -1), 3.5, bar or C_AMBER),
        ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _roles_table(st: dict, rows: list) -> RLTable:
    """Таблица «роль — за что отвечает»."""
    data = [[Paragraph(f"<b>{role}</b>", st["cell"]), Paragraph(text, st["cell"])] for role, text in rows]
    t = RLTable(data, colWidths=[150, BODY_W - 150])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEABOVE", (0, 0), (-1, 0), 0.8, C_TEAL), ("LINEBELOW", (0, 0), (-1, -1), 0.6, C_LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def _bullets(st: dict, items: list) -> list:
    return [Paragraph(text, st["bullet"], bulletText="•") for text in items]


def _build_pdf(path: Path, story: list, title: str, chrome, on_page=None, top=35 * mm, bottom=20 * mm) -> None:
    """Собирает PDF: invariant=1 закрепляет служебные даты, чтобы файл не менялся от запуска к запуску."""
    doc = SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=MX - FRAME_PAD, rightMargin=MX - FRAME_PAD,
        topMargin=top - FRAME_PAD, bottomMargin=bottom - FRAME_PAD,
        title=title, author=AUTHOR, subject=NOTICE, creator="backend/demo/make_materials.py", invariant=1)
    kwargs = {"canvasmaker": _numbered_canvas(chrome)}
    if on_page:
        kwargs.update(onFirstPage=on_page, onLaterPages=on_page)
    doc.build(story, **kwargs)


# ════════════════════════════════════════════════════════════════════════════════════════
# 3. PDF: СОП-ОК-015, редакции 1 и 2
# ════════════════════════════════════════════════════════════════════════════════════════


def _title_sheet_drawing(meta: dict, with_qr: bool) -> Drawing:
    """Схема титульного листа: контур листа и подсвеченные зоны с номерами."""
    W, H = 206, 208
    d = Drawing(W, H)
    sx, sy, sw, sh = 8, 8, 186, 192
    top = sy + sh
    d.add(Rect(sx + 3, sy - 3, sw, sh, fillColor=_c("DDD8CE"), strokeColor=None))           # тень
    d.add(Rect(sx, sy, sw, sh, fillColor=colors.white, strokeColor=C_LINE, strokeWidth=0.8))  # лист
    ix, iw = sx + 11, sw - 22
    d.add(Rect(ix, top - 27, iw, 17, fillColor=C_TEAL, strokeColor=None))
    d.add(String(ix + 6, top - 21.5, ORG, fontName=SANS_BOLD, fontSize=8.5, fillColor=colors.white))
    d.add(String(ix + iw - 6, top - 21, "СОП", fontName=SANS_BOLD, fontSize=7, fillColor=_c("CFE3E0"), textAnchor="end"))

    def badge(n: str, cx: float, cy: float) -> None:
        d.add(Circle(cx, cy, 7.5, fillColor=C_AMBER, strokeColor=colors.white, strokeWidth=1))
        d.add(String(cx, cy - 2.9, n, fontName=SANS_BOLD, fontSize=8, fillColor=C_INK, textAnchor="middle"))

    def zone(n: str, x: float, y: float, w: float, h: float, label: str, value: str, vsize: float) -> None:
        d.add(Rect(x, y, w, h, rx=3, ry=3, fillColor=C_AMBER_TINT, strokeColor=C_AMBER, strokeWidth=1.3))
        d.add(String(x + 5, y + h - 9.5, label, fontName=SANS, fontSize=5.8, fillColor=C_MUTED))
        d.add(String(x + 5, y + 5.5, value, fontName=SANS_BOLD, fontSize=vsize, fillColor=C_INK))
        badge(n, x + w - 2, y + h - 2)

    zone("1", ix, top - 65, iw, 30, "Код документа", meta["code"], 12)
    zone("2", ix, top - 104, 62, 30, "Редакция", meta["rev"], 12)
    zone("3", ix + 72, top - 104, iw - 72, 30, "Введена в действие", meta["date"], 10.5)

    # строки-заглушки вместо текста документа
    for dy, share in ((117, 1.0), (127, 0.68)):
        d.add(Rect(ix, top - dy, iw * share, 5, rx=1.2, ry=1.2, fillColor=_c("E3E7E5"), strokeColor=None))

    # зона 4: синий штамп
    d.add(Rect(ix - 3, sy + 9, 107, 46, rx=4, ry=4, fillColor=C_AMBER_TINT, strokeColor=C_AMBER, strokeWidth=1.3))
    stamp = Group(
        Rect(-47, -17, 94, 34, rx=3, ry=3, fillColor=None, strokeColor=C_STAMP, strokeWidth=1.4),
        String(0, 5.5, "КОНТРОЛИРУЕМАЯ", fontName=SANS_BOLD, fontSize=6.6, fillColor=C_STAMP, textAnchor="middle"),
        String(0, -2.5, "КОПИЯ", fontName=SANS_BOLD, fontSize=6.6, fillColor=C_STAMP, textAnchor="middle"),
        String(0, -11.5, f"экз. № {meta['copy_no']}", fontName=SANS, fontSize=5.8, fillColor=C_STAMP, textAnchor="middle"),
    )
    stamp.transform = mmult(translate(ix + 50.5, sy + 32), rotate(5))
    d.add(stamp)
    badge("4", ix + 102, sy + 53)

    if with_qr:  # зона 5: QR-код электронного реестра
        size, n = 36, 21
        m = size / n
        qx, qy = ix + iw - size - 4, sy + 14
        d.add(Rect(qx - 5, sy + 9, size + 10, 46, rx=4, ry=4, fillColor=C_AMBER_TINT, strokeColor=C_AMBER, strokeWidth=1.3))
        d.add(Rect(qx - 1.5, qy - 1.5, size + 3, size + 3, fillColor=colors.white, strokeColor=None))
        for r, row in enumerate(_qr_modules(n)):
            for col, on in enumerate(row):
                if on:
                    d.add(Rect(qx + col * m, qy + (n - 1 - r) * m, m + 0.05, m + 0.05, fillColor=C_INK, strokeColor=None))
        badge("5", qx + size + 3, sy + 53)
    else:  # место подписей на листе без QR
        for dy in (22, 36):
            d.add(Line(ix + 118, sy + dy, ix + iw, sy + dy, strokeColor=_c("D5DAD8"), strokeWidth=0.8))
    return d


def _where_to_look(st: dict, meta: dict, with_qr: bool) -> RLTable:
    """Блок «Где искать на титульном листе»: схема слева, расшифровка зон справа."""
    drawing = _title_sheet_drawing(meta, with_qr)
    items = [
        ("1", "Код документа", meta["code"]),
        ("2", "Номер редакции", f"Редакция {meta['rev']}"),
        ("3", "Дата введения в действие", meta["date"]),
        ("4", "Штамп «Контролируемая копия»", "синий, с номером экземпляра"),
    ]
    if with_qr:
        items.append(("5", "QR-код", "открывает электронный реестр"))
    left_w = drawing.width + 22
    legend_w = BODY_W - left_w - 14
    rows = [[_Badge(n, d=20, fill=C_AMBER, color=C_INK, size=10), Paragraph(f"<b>{t}</b><br/>{v}", st["legend"])]
            for n, t, v in items]
    legend = RLTable(rows, colWidths=[29, legend_w - 29])
    legend.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (0, -1), 1), ("TOPPADDING", (1, 0), (1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8 if with_qr else 11),
    ]))
    block = RLTable([[drawing, legend]], colWidths=[left_w, BODY_W - left_w])
    block.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_PAPER), ("ROUNDEDCORNERS", [8, 8, 8, 8]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 11), ("RIGHTPADDING", (0, 0), (0, 0), 11),
        ("LEFTPADDING", (1, 0), (1, 0), 0), ("RIGHTPADDING", (1, 0), (1, 0), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return block


def _make_sop_ok015(path: Path, meta: dict, steps: list, red2: bool, red3: bool = False) -> None:
    """red3 — редакция 3: всё, что есть в редакции 2 (red2=True), плюс уточнение раздела 4."""
    st = _styles()
    s = _sop_title_block(st, meta, with_qr=red2)

    if red3:
        s.append(Spacer(1, 11))
        s.append(_callout([
            Paragraph("Что изменилось в редакции 3", st["h3"]),
            *_bullets(st, [
                "<b>Уточнён раздел 4:</b> пока данные не совпали, оборудование не запускают — до получения "
                "актуального экземпляра под подпись.",
                "Порядок проверки не изменился: те же пять шагов.",
                "Редакция 2 (введена 01.10.2026) изъята 20.11.2026.",
            ]),
        ], bar=C_TEAL, bg=C_TEAL_TINT))
    elif red2:  # что изменилось — сразу после титульного блока, чтобы увидеть первым
        s.append(Spacer(1, 11))
        s.append(_callout([
            Paragraph("Что изменилось в редакции 2", st["h3"]),
            *_bullets(st, [
                "<b>Новый шаг 4:</b> отсканируйте QR-код на титульном листе — электронный реестр покажет "
                "действующую редакцию.",
                "<b>Уточнён последний шаг:</b> устаревший экземпляр передают мастеру смены; мастер изымает его "
                "и делает запись в журнале изъятия; оператор получает актуальный экземпляр под подпись.",
                "Шагов стало пять вместо четырёх. Редакция 1 (введена 03.02.2026) изъята 01.10.2026.",
            ]),
        ], bar=C_TEAL, bg=C_TEAL_TINT))

    s.append(Paragraph("1. Назначение", st["h2"]))
    s.append(Paragraph(
        "Процедура описывает, как оператор перед началом смены убеждается, что работает по действующей "
        "редакции СОП. Работа по устаревшей редакции — это отклонение от утверждённой технологии: серия "
        "может быть задержана до окончания расследования. Проверка занимает около двух минут.", st["body"]))

    s.append(Paragraph("2. Область применения", st["h2"]))
    s.append(Paragraph(
        "Процедура обязательна для операторов участка таблетирования. Её выполняют в начале каждой смены "
        "для каждого рабочего экземпляра СОП, по которому предстоит работать.", st["body"]))

    s.append(Paragraph("3. Порядок действий", st["h2"]))
    s.append(_steps_table(st, steps))

    s.append(KeepTogether([
        Paragraph("Где искать на титульном листе", st["h2"]),
        _where_to_look(st, meta, with_qr=red2),
    ]))

    mismatch = [
        "<b>Остановитесь.</b> Не начинайте работу по этому документу.",
        "<b>Сообщите мастеру смены:</b> назовите код и что не совпало.",
        "<b>Экземпляр не выбрасывайте и не исправляйте сами.</b>",
    ]
    if red3:
        mismatch.insert(1, "<b>Не запускайте оборудование</b>, пока не получите актуальный экземпляр.")
    if red2:
        mismatch += [
            "<b>Передайте устаревший экземпляр мастеру смены.</b> Мастер изымает его и делает запись "
            "в журнале изъятия.",
            "<b>Получите актуальный экземпляр под подпись</b> и повторите проверку.",
        ]
    else:
        mismatch.append("Дождитесь от мастера смены экземпляра действующей редакции.")
    mismatch.append("Нет синего штампа «Контролируемая копия» — действуйте так же.")
    s.append(KeepTogether([
        Paragraph("4. Если данные не совпали", st["h2"]),
        _callout(_bullets(st, mismatch)),
    ]))

    master_duty = ("ведёт «Реестр действующих редакций СОП», изымает устаревшие экземпляры с записью в журнале "
                   "изъятия и выдаёт актуальные экземпляры под подпись." if red2 else
                   "ведёт «Реестр действующих редакций СОП» участка и выдаёт экземпляры действующей редакции.")
    s.append(KeepTogether([
        Paragraph("5. Ответственность", st["h2"]),
        _roles_table(st, [
            ("Оператор", "выполняет проверку перед началом каждой смены и расписывается в журнале начала смены."),
            ("Мастер смены", master_duty),
            ("Начальник отдела обеспечения качества", "утверждает редакции СОП и контролирует соблюдение процедуры."),
            ("Отдел обучения", "разрабатывает учебные материалы и проводит ознакомление с процедурой."),
        ]),
    ]))

    if red2:
        s.append(KeepTogether([
            Paragraph("6. Связанные документы и записи", st["h2"]),
            *_bullets(st, [
                "«Реестр действующих редакций СОП» участка — папка на посту мастера смены и электронный реестр по QR-коду.",
                "Журнал начала смены — подпись оператора после проверки.",
                "Журнал изъятия — запись мастера смены об изъятом экземпляре.",
                f"Лист ознакомления с редакцией {meta['rev']} — подпись каждого оператора участка.",
            ]),
        ]))
    s.append(Paragraph("— конец документа —", st["end"]))

    _build_pdf(path, s, f"{meta['code']} «{meta['title']}». Редакция {meta['rev']}",
               lambda c, n, t: _sop_chrome(c, n, t, meta))


def make_sop_red1(path: Path) -> None:
    _make_sop_ok015(path, RED1, STEPS_RED1, red2=False)


def make_sop_red2(path: Path) -> None:
    _make_sop_ok015(path, RED2, STEPS_RED2, red2=True)


def make_sop_red3(path: Path) -> None:
    _make_sop_ok015(path, RED3, STEPS_RED2, red2=True, red3=True)


# ════════════════════════════════════════════════════════════════════════════════════════
# 4. PDF: СОП-ПР-007 «Гигиена и переодевание при входе в чистую зону»
# ════════════════════════════════════════════════════════════════════════════════════════

HYGIENE_STEPS = [
    "<b>Снимите украшения и часы.</b> Кольца, браслеты, серьги, часы и телефон оставьте в личном шкафчике.",
    "<b>Вымойте руки с мылом — не менее 30 секунд</b> по схеме над раковиной: ладони, тыльная сторона, "
    "между пальцами, большие пальцы, кончики пальцев, запястья. Вытрите руки одноразовым полотенцем.",
    "<b>Наденьте технологическую одежду в установленном порядке:</b> шапочка, маска, комбинезон, бахилы. "
    "Волосы полностью убраны под шапочку, маска закрывает нос и рот.",
    "<b>Обработайте руки антисептиком</b> из дозатора и дайте ему полностью высохнуть.",
    "<b>Проверьте себя у зеркала:</b> комбинезон застёгнут, волосы и шея закрыты, маска прилегает, бахилы целые.",
    "<b>Одежда повреждена или загрязнена — замените её</b> на новый комплект до входа в чистую зону.",
    "<b>Сделайте запись в журнале входа в чистую зону:</b> дата, время, фамилия, подпись.",
]


def _dressing_order_drawing() -> Drawing:
    """Полоса «порядок надевания одежды»: четыре плашки со стрелками."""
    items = ["Шапочка", "Маска", "Комбинезон", "Бахилы"]
    W, H = BODY_W, 44
    d = Drawing(W, H)
    gap = 20
    bw = (W - gap * (len(items) - 1)) / len(items)
    for i, name in enumerate(items):
        x = i * (bw + gap)
        d.add(Rect(x, 4, bw, 36, rx=6, ry=6, fillColor=C_TEAL_TINT, strokeColor=C_TEAL, strokeWidth=0.9))
        d.add(Circle(x + 16, 22, 9, fillColor=C_TEAL, strokeColor=None))
        d.add(String(x + 16, 18.4, str(i + 1), fontName=SANS_BOLD, fontSize=10, fillColor=colors.white, textAnchor="middle"))
        d.add(String(x + 30, 18.5, name, fontName=SANS_BOLD, fontSize=10, fillColor=C_INK))
        if i < len(items) - 1:
            ax = x + bw + 4
            d.add(Line(ax, 22, ax + gap - 9, 22, strokeColor=C_AMBER, strokeWidth=2))
            d.add(Line(ax + gap - 14, 27, ax + gap - 8, 22, strokeColor=C_AMBER, strokeWidth=2))
            d.add(Line(ax + gap - 14, 17, ax + gap - 8, 22, strokeColor=C_AMBER, strokeWidth=2))
    return d


def make_hygiene(path: Path) -> None:
    st = _styles()
    meta = HYGIENE
    s = _sop_title_block(st, meta)

    s.append(Paragraph("1. Назначение", st["h2"]))
    s.append(Paragraph(
        "Процедура описывает подготовку ко входу в чистую зону: личную гигиену, порядок переодевания "
        "и самопроверку. Цель — не занести в чистую зону загрязнения с кожи, волос, одежды и личных вещей.",
        st["body"]))

    s.append(Paragraph("2. Область применения", st["h2"]))
    s.append(Paragraph(
        "Процедура обязательна для всех, кто входит в чистую зону участка таблетирования: операторов, "
        "мастеров смены, наладчиков, сотрудников отдела обеспечения качества и посетителей с сопровождающим.",
        st["body"]))

    s.append(_steps_table(st, HYGIENE_STEPS, heading="3. Порядок действий"))

    s.append(KeepTogether([
        Paragraph("Порядок надевания одежды (шаг 3)", st["h2"]),
        _dressing_order_drawing(),
        Spacer(1, 2),
        Paragraph("Одеваемся сверху вниз: сначала закрываем волосы и лицо, затем тело и обувь.", st["small"]),
    ]))

    s.append(KeepTogether([
        Paragraph("4. Чего делать нельзя", st["h2"]),
        _callout([
            *_bullets(st, [
                "Входить в чистую зону с украшениями, часами, телефоном, едой и напитками.",
                "Входить с признаками простуды или с повреждениями кожи на руках — сообщите мастеру смены.",
                "Касаться лица и поправлять маску руками внутри чистой зоны.",
                "Выходить в технологической одежде за пределы зоны переодевания.",
                "Надевать повреждённую, загрязнённую или уже использованную одноразовую одежду.",
                "Пропускать мытьё и обработку рук, даже если вы выходили ненадолго.",
            ]),
        ], bar=C_STOP, bg=_c("F8E9E5")),
    ]))

    s.append(KeepTogether([
        Paragraph("5. Ответственность", st["h2"]),
        _roles_table(st, [
            ("Каждый входящий", "выполняет процедуру при каждом входе в чистую зону и делает запись в журнале."),
            ("Мастер смены", "следит за соблюдением процедуры и за тем, чтобы в шлюзе были одежда, мыло и антисептик."),
            ("Отдел обеспечения качества", "проверяет записи в журнале входа и проводит выборочный контроль."),
        ]),
    ]))
    s.append(Paragraph("— конец документа —", st["end"]))

    _build_pdf(path, s, f"{meta['code']} «{meta['title']}». Редакция {meta['rev']}",
               lambda c, n, t: _sop_chrome(c, n, t, meta))


# ════════════════════════════════════════════════════════════════════════════════════════
# 5. PDF: памятка и глоссарий
# ════════════════════════════════════════════════════════════════════════════════════════

MEMO_TITLE = "Памятка: в смене оказалась инструкция другой редакции"
MEMO_RULES = [
    ("Остановитесь.", "Не начинайте операцию, пока не выяснится, какая редакция действует."),
    ("Не работайте по сомнительному документу.", "Действует только та редакция, что указана в реестре, — "
                                                  "даже если «всегда так делали»."),
    ("Сообщите мастеру смены.", "Назовите код документа и что не совпало: редакция, дата или штамп."),
    ("Не выбрасывайте и не исправляйте экземпляр.", "Передайте его мастеру смены: он изымет экземпляр "
                                                     "и сделает запись в журнале изъятия."),
    ("Дождитесь актуального экземпляра и распишитесь.", "Мастер смены выдаёт действующую редакцию под подпись. "
                                                         "После этого повторите проверку."),
]


def make_memo(path: Path) -> None:
    st = _styles()
    band_h = 60 * mm
    title_style = ParagraphStyle("memo_title", fontName=SANS_BOLD, fontSize=24, leading=30.5, textColor=colors.white)
    rule_style = ParagraphStyle("memo_rule", fontName=SANS_BOLD, fontSize=16, leading=21, textColor=C_INK, spaceAfter=2)
    why_style = ParagraphStyle("memo_why", fontName=SANS, fontSize=11.5, leading=16.5, textColor=_c("3D4C4A"))

    def background(c, doc) -> None:
        """Цветная шапка памятки (рисуется под текстом)."""
        c.saveState()
        c.setFillColor(C_TEAL)
        c.rect(0, PAGE_H - band_h, PAGE_W, band_h, stroke=0, fill=1)
        c.setFillColor(C_AMBER)
        c.rect(0, PAGE_H - band_h - 2.2 * mm, PAGE_W, 2.2 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont(SANS_BOLD, 12)
        c.drawString(MX, PAGE_H - 15 * mm, ORG)
        c.setFillColor(_c("BFD8D4"))
        c.setFont(SANS, 9.5)
        c.drawRightString(PAGE_W - MX, PAGE_H - 15 * mm, "Участок таблетирования · отдел обучения")
        p = Paragraph(MEMO_TITLE, title_style)
        _, h = p.wrap(BODY_W, band_h)
        p.drawOn(c, MX, PAGE_H - 22 * mm - h)
        c.setFillColor(C_AMBER)
        c.setFont(SANS_BOLD, 11)
        c.drawString(MX, PAGE_H - band_h + 7.5 * mm, f"Пять правил оператора · к {SOP_CODE}")
        c.restoreState()

    rows = [[_Badge(str(i), d=36, size=18), [Paragraph(rule, rule_style), Paragraph(why, why_style)]]
            for i, (rule, why) in enumerate(MEMO_RULES, 1)]
    rules = RLTable(rows, colWidths=[52, BODY_W - 52])
    rules.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (0, -1), 11), ("TOPPADDING", (1, 0), (1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
        ("LINEBELOW", (0, 0), (-1, -2), 0.6, C_LINE),
    ]))

    tel_style = ParagraphStyle("memo_tel", fontName=SANS_BOLD, fontSize=14, leading=18, textColor=C_TEAL, alignment=2)
    contacts = [
        ("Мастер смены", MASTER, TEL_MASTER),
        ("Отдел обеспечения качества", f"{QA_HEAD}, начальник отдела", TEL_QA),
        ("Отдел обучения", "вопросы по учебным материалам", TEL_TRAINING),
    ]
    tel = RLTable([[Paragraph(f"<b>{role}</b>", st["cell"]), Paragraph(who, st["cell"]),
                    Paragraph(f"внутр. {num}", tel_style)] for role, who, num in contacts],
                  colWidths=[BODY_W * 0.36, BODY_W * 0.38, BODY_W * 0.26])
    tel.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_TEAL_TINT), ("ROUNDEDCORNERS", [8, 8, 8, 8]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.6, colors.white),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))

    story = [
        rules,
        Paragraph("Кому звонить", st["h2"]),
        tel,
        Spacer(1, 9),
        Paragraph(f"Памятка не заменяет {SOP_CODE}. Действующая редакция любого СОП указана в «Реестре "
                  f"действующих редакций СОП» на посту мастера смены.", st["small"]),
    ]

    def chrome(c, page, total) -> None:
        c.saveState()
        _draw_footer(c, f"Памятка к {SOP_CODE}")
        c.restoreState()

    _build_pdf(path, story, MEMO_TITLE, chrome, on_page=background, top=band_h + 9 * mm)


def make_glossary(path: Path) -> None:
    st = _styles()
    title = "Краткий глоссарий для оператора"
    term_style = ParagraphStyle("term", fontName=SANS_BOLD, fontSize=11.5, leading=16, textColor=C_TEAL)
    story = [
        Paragraph("СПРАВОЧНЫЙ МАТЕРИАЛ", st["kicker"]),
        Paragraph(title, st["title"]),
        Paragraph("Двенадцать слов, которые встречаются в инструкциях участка. Простыми словами, по одному "
                  "предложению на каждое.", st["meta"]),
        Spacer(1, 8),
    ]
    story += [_notice_pill(st), Spacer(1, 12)]

    rows = [[Paragraph(term, term_style), Paragraph(text, st["body"])] for term, text in GLOSSARY]
    table = RLTable(rows, colWidths=[152, BODY_W - 152], repeatRows=0)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEABOVE", (0, 0), (-1, 0), 0.8, C_TEAL), ("LINEBELOW", (0, 0), (-1, -1), 0.6, C_LINE),
    ]
    for i in range(0, len(rows), 2):
        style.append(("BACKGROUND", (0, i), (-1, i), C_PAPER))
    table.setStyle(TableStyle(style))
    story.append(table)
    story.append(KeepTogether([
        Paragraph("Как эти слова связаны", st["h2"]),
        _callout(_bullets(st, [
            "У каждого <b>СОП</b> есть номер <b>редакции</b>. Действует только одна.",
            "Какая редакция действует и с какой <b>даты</b> — записано в <b>реестре</b>.",
            "На рабочем месте лежит <b>контролируемая копия</b> — экземпляр с синим штампом и номером.",
            "Вышла новая редакция — старые экземпляры <b>изымают</b>, с новой знакомят под подпись "
            "в <b>листе ознакомления</b>.",
            "Работа по старой редакции — это <b>отклонение</b>: <b>серию</b> могут задержать до конца расследования.",
        ]), bar=C_TEAL, bg=C_TEAL_TINT),
    ]))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Не нашли нужное слово? Спросите мастера смены или отдел обучения "
                           f"(внутр. {TEL_TRAINING}).", st["small"]))

    _build_pdf(path, story, title, lambda c, n, t: _library_chrome(c, n, t, title), top=27 * mm)


# ════════════════════════════════════════════════════════════════════════════════════════
# 6. DOCX: чек-лист и образец журнала
# ════════════════════════════════════════════════════════════════════════════════════════

DOCX_FONT = "Arial"


def _docx_style_font(style, name: str) -> None:
    """Задаёт шрифт стиля для всех письменностей и убирает ссылку на шрифт темы (иначе Word берёт Calibri)."""
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        rfonts.attrib.pop(qn(attr), None)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), name)


def _docx_new(title: str, landscape: bool = False):
    """Пустой документ A4 с настроенными стилями, свойствами и колонтитулом-пометкой."""
    doc = Document()
    sec = doc.sections[0]
    if landscape:
        sec.orientation = WD_ORIENT.LANDSCAPE
        sec.page_width, sec.page_height = Cm(29.7), Cm(21.0)
    else:
        sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.0)
    sec.top_margin, sec.bottom_margin = Cm(2.0), Cm(1.8)

    normal = doc.styles["Normal"]
    _docx_style_font(normal, DOCX_FONT)
    normal.font.size = DPt(11)
    normal.font.color.rgb = DRGB.from_string(HEX_INK)
    normal.paragraph_format.space_after = DPt(6)
    normal.paragraph_format.line_spacing = 1.2
    for name, size, before, after in (("Heading 1", 20, 0, 6), ("Heading 2", 14, 14, 6)):
        hs = doc.styles[name]
        _docx_style_font(hs, DOCX_FONT)
        hs.font.size = DPt(size)
        hs.font.bold = True
        hs.font.color.rgb = DRGB.from_string(HEX_TEAL)
        hs.paragraph_format.space_before = DPt(before)
        hs.paragraph_format.space_after = DPt(after)
    for name in ("List Number", "List Bullet"):
        _docx_style_font(doc.styles[name], DOCX_FONT)

    cp = doc.core_properties
    cp.title, cp.author, cp.last_modified_by = title, AUTHOR, AUTHOR
    cp.subject, cp.comments, cp.language = NOTICE, NOTICE, "ru-RU"
    cp.created = cp.modified = FIXED_DT
    cp.revision = 1

    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = fp.add_run(f"{NOTICE}  ·  {ORG}, отдел обучения")
    run.font.size = DPt(8.5)
    run.font.color.rgb = DRGB.from_string(HEX_MUTED)
    return doc


def _docx_notice(doc) -> None:
    """Строка-пометка под заголовком (видна и там, где колонтитулы не показываются)."""
    p = doc.add_paragraph()
    run = p.add_run(NOTICE)
    run.italic = True
    run.font.size = DPt(9.5)
    run.font.color.rgb = DRGB.from_string("7A4E08")


def _docx_cell(cell, text: str, bold: bool = False, size: float = 10.5, color: str | None = None,
               align=None, fill: str | None = None) -> None:
    """Заполняет ячейку таблицы Word: текст, начертание, заливка."""
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = DPt(3)
    p.paragraph_format.space_after = DPt(3)
    p.paragraph_format.line_spacing = 1.1
    if align is not None:
        p.alignment = align
    run = p.add_run(text)
    run.bold = bold
    run.font.size = DPt(size)
    if color:
        run.font.color.rgb = DRGB.from_string(color)
    if fill:
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fill)
        cell._tc.get_or_add_tcPr().append(shd)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def _docx_table(doc, headers: list, rows: list, widths_cm: list, center_cols: tuple = (), size: float = 10.5):
    """Таблица Word со сплошной сеткой и закрашенной жирной строкой заголовков."""
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    for r, values in enumerate([headers] + rows):
        for col, value in enumerate(values):
            cell = table.cell(r, col)
            cell.width = Cm(widths_cm[col])
            if r == 0:
                _docx_cell(cell, value, bold=True, size=size, color=HEX_WHITE,
                           align=WD_ALIGN_PARAGRAPH.CENTER, fill=HEX_TEAL)
            else:
                _docx_cell(cell, value, size=size,
                           align=WD_ALIGN_PARAGRAPH.CENTER if col in center_cols else None,
                           fill=HEX_PAPER if r % 2 == 0 else None)
    header_flag = OxmlElement("w:tblHeader")       # повторять заголовок на каждой странице
    header_flag.set(qn("w:val"), "true")
    table.rows[0]._tr.get_or_add_trPr().append(header_flag)
    return table


def make_checklist(path: Path) -> None:
    title = "Чек-лист: проверка СОП перед сменой"
    doc = _docx_new(title)
    doc.add_heading(title, level=1)
    _docx_notice(doc)
    doc.add_paragraph(
        f"Пройдите чек-лист в начале смены для каждого рабочего экземпляра СОП, по которому будете работать. "
        f"Проверка занимает около двух минут. Основание — {SOP_CODE} «{SOP_TITLE}».")
    p = doc.add_paragraph()
    p.add_run("Дата: ___.___.2026     Смена: _____     Оператор: __________________     "
              "Код СОП: ___________").font.size = DPt(10.5)

    rows = [
        ("1", "Код документа на рабочем экземпляре", "Титульный лист СОП", "☐"),
        ("2", "Номер редакции на рабочем экземпляре", "Титульный лист СОП", "☐"),
        ("3", "Строка с тем же кодом в «Реестре действующих редакций СОП»", "Папка на посту мастера смены", "☐"),
        ("4", "Код, номер редакции и дата введения в действие совпадают с реестром", "Титульный лист и строка реестра", "☐"),
        ("5", "Синий штамп «Контролируемая копия» с номером экземпляра", "Титульный лист СОП", "☐"),
        ("6", "Подпись оператора о проверке", "Журнал начала смены", "☐"),
    ]
    _docx_table(doc, ["№", "Что проверить", "Где смотреть", "Отметка"], rows, [1.1, 8.4, 5.3, 2.2], center_cols=(0, 3))

    note = doc.add_paragraph()
    note.paragraph_format.space_before = DPt(8)
    r = note.add_run("Есть QR-код на титульном листе (с редакции 2)? ")
    r.bold = True
    note.add_run("Отсканируйте его — электронный реестр покажет действующую редакцию.")

    doc.add_heading("Если не совпало", level=2)
    for text in ("Не начинайте работу по этому документу.",
                 "Сообщите мастеру смены: назовите код документа и что именно не совпало.",
                 "Экземпляр не выбрасывайте и не исправляйте сами — передайте его мастеру смены."):
        doc.add_paragraph(text, style="List Number")

    tail = doc.add_paragraph()
    tail.paragraph_format.space_before = DPt(8)
    tr = tail.add_run(f"Мастер смены — {MASTER}, внутр. {TEL_MASTER}.")
    tr.font.color.rgb = DRGB.from_string(HEX_MUTED)
    doc.save(str(path))
    _normalize_office_zip(path)


def make_shift_log(path: Path) -> None:
    title = "Образец заполнения журнала приёма-передачи смены"
    doc = _docx_new(title, landscape=True)
    doc.add_heading(title, level=1)
    _docx_notice(doc)
    doc.add_paragraph(
        "Журнал заполняют при каждой передаче смены на участке таблетирования. В графе «Проверка СОП» "
        "принимающий указывает код и редакцию каждого проверенного документа и результат сверки с «Реестром "
        "действующих редакций СОП». Пишите разборчиво, без сокращений названий; исправления — одной чертой "
        "с подписью и датой.")

    rows = [
        ("30.09.2026", "2-я\n(20:00–08:00)", "Иманов Б.", "Касымова Д.",
         "СОП-ТБ-021 / ред. 5 — совпало\nСОП-ОК-015 / ред. 1 — совпало",
         "Замечаний нет."),
        ("01.10.2026", "1-я\n(08:00–20:00)", "Касымова Д.", "Абенов Т.",
         "СОП-ОК-015 / ред. 1 — не совпало: в реестре действует ред. 2\nСОП-ТБ-021 / ред. 5 — совпало",
         "Работа по СОП-ОК-015 не начата. Экземпляр ред. 1 передан мастеру смены Сапарову Е. и изъят, "
         "получен экземпляр ред. 2 под подпись."),
        ("01.10.2026", "2-я\n(20:00–08:00)", "Абенов Т.", "Иманов Б.",
         "СОП-ОК-015 / ред. 2 — совпало\nСОП-ТБ-021 / ред. 5 — совпало",
         "Замечаний нет."),
        ("02.10.2026", "1-я\n(08:00–20:00)", "Иманов Б.", "Касымова Д.",
         "СОП-ТБ-021 / ред. 5 — совпало\nСОП-ОЧ-004 / ред. 4 — совпало",
         "На экземпляре № 3 СОП-ОЧ-004 плохо читается штамп — сообщено мастеру смены."),
    ]
    _docx_table(doc, ["Дата", "Смена", "Сдал", "Принял", "Проверка СОП (код/редакция)", "Замечания"],
                rows, [2.4, 2.7, 2.7, 2.7, 7.6, 7.6], center_cols=(0, 1), size=10)

    doc.add_heading("На что обратить внимание", level=2)
    for text in ("Запись о проверке СОП делают до начала работы, а не в конце смены.",
                 "Если редакция не совпала, в замечаниях пишут, что работа не начата и кому передан экземпляр.",
                 "Пустых граф не оставляют: нет замечаний — так и пишут «Замечаний нет»."):
        doc.add_paragraph(text, style="List Bullet")
    doc.save(str(path))
    _normalize_office_zip(path)


def _normalize_office_zip(path: Path) -> None:
    """Перепаковывает docx/xlsx/pptx с закреплёнными датами — файл получается одинаковым при каждом запуске."""
    stamp = FIXED_DT.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
    with zipfile.ZipFile(path) as zin:
        items = [(info.filename, zin.read(info.filename)) for info in zin.infolist()]
    tmp = path.with_name(path.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in items:
            if name == "docProps/core.xml":
                data = re.sub(rb"(<dcterms:(created|modified)[^>]*>)[^<]*(</dcterms:\2>)",
                              lambda m: m.group(1) + stamp + m.group(3), data)
            info = zipfile.ZipInfo(name, date_time=FIXED_DT.timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0
            zout.writestr(info, data)
    os.replace(tmp, path)


# ════════════════════════════════════════════════════════════════════════════════════════
# 7. XLSX: реестр и график обучения
# ════════════════════════════════════════════════════════════════════════════════════════

XL_DATE_FMT = "DD.MM.YYYY"


def _xlsx_sheet(ws, title: str, note: str, headers: list, rows: list, widths: list,
                date_cols: tuple = (), center_cols: tuple = (), wrap_cols: tuple = ()) -> None:
    """Лист Excel: строка названия, строка-пометка, закрашенный жирный заголовок, данные, закреплённая шапка."""
    thin = Side(style="thin", color=HEX_LINE)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    last_col = get_column_letter(len(headers))

    ws.merge_cells(f"A1:{last_col}1")
    ws["A1"] = title
    ws["A1"].font = Font(name="Calibri", size=14, bold=True, color=HEX_TEAL)
    ws["A1"].alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 26
    ws.merge_cells(f"A2:{last_col}2")
    ws["A2"] = note
    ws["A2"].font = Font(name="Calibri", size=10, italic=True, color="7A4E08")
    ws.row_dimensions[2].height = 18

    header_row = 3
    for col, name in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col, value=name)
        cell.font = Font(name="Calibri", size=11, bold=True, color=HEX_WHITE)
        cell.fill = PatternFill("solid", fgColor=HEX_TEAL)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border
    ws.row_dimensions[header_row].height = 34

    for r, values in enumerate(rows, header_row + 1):
        lines = 1
        for col, value in enumerate(values, 1):
            idx = col - 1
            if idx in date_cols:
                value = _d(value)
            cell = ws.cell(row=r, column=col, value=value)
            cell.font = Font(name="Calibri", size=11)
            cell.border = border
            if idx in date_cols:
                cell.number_format = XL_DATE_FMT
            wrap = idx in wrap_cols
            cell.alignment = Alignment(horizontal="center" if idx in center_cols or idx in date_cols else "left",
                                       vertical="center", wrap_text=wrap)
            if wrap and isinstance(value, str):
                lines = max(lines, math.ceil(len(value) * 0.95 / widths[idx]))
            if (r - header_row) % 2 == 0:
                cell.fill = PatternFill("solid", fgColor=HEX_PAPER)
        ws.row_dimensions[r].height = 15 * lines + 7     # высота под число строк текста с переносом

    for col, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)         # шапка остаётся на экране при прокрутке
    ws.auto_filter.ref = f"A{header_row}:{last_col}{header_row + len(rows)}"
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"{header_row}:{header_row}"
    ws.oddFooter.center.text = NOTICE


def _xlsx_save(wb, path: Path, title: str) -> None:
    wb.properties.title = title
    wb.properties.creator = AUTHOR
    wb.properties.lastModifiedBy = AUTHOR
    wb.properties.description = NOTICE
    wb.properties.created = FIXED_DT
    wb.properties.modified = FIXED_DT
    wb.save(str(path))
    _normalize_office_zip(path)


def make_registry(path: Path) -> None:
    title = "Реестр действующих редакций СОП — участок таблетирования"
    wb = Workbook()
    ws = wb.active
    ws.title = "Действующие"
    _xlsx_sheet(ws, title, f"{NOTICE} Состояние на 02.11.2026. Реестр ведёт мастер смены.",
                ["Код СОП", "Название", "Действующая редакция", "Дата введения", "Место хранения экземпляра", "Ответственный"],
                REGISTRY, [14, 46, 15, 15, 40, 18], date_cols=(3,), center_cols=(2,), wrap_cols=(1, 4))
    ws2 = wb.create_sheet("Изъятые редакции")
    _xlsx_sheet(ws2, "Изъятые редакции СОП — участок таблетирования",
                f"{NOTICE} Изъятые экземпляры по этим редакциям в работе не используются.",
                ["Код", "Редакция", "Дата изъятия", "Причина", "Кто изъял"],
                WITHDRAWN, [14, 12, 15, 62, 18], date_cols=(2,), center_cols=(1,), wrap_cols=(3,))
    _xlsx_save(wb, path, title)


def make_training_plan(path: Path) -> None:
    title = "График обучения на IV квартал 2026"
    wb = Workbook()
    ws = wb.active
    ws.title = "IV квартал 2026"
    _xlsx_sheet(ws, f"{title} — производственная площадка {ORG}",
                f"{NOTICE} Составил отдел обучения, утверждён 28.09.2026.",
                ["Подразделение", "Тема", "Код СОП", "Срок", "Ответственный", "Форма"],
                TRAINING_PLAN, [27, 50, 14, 14, 18, 36], date_cols=(3,), center_cols=(2,), wrap_cols=(0, 1, 5))
    _xlsx_save(wb, path, title)


# ════════════════════════════════════════════════════════════════════════════════════════
# 8. Иллюстрации Pillow (общие для презентации и видео)
# ════════════════════════════════════════════════════════════════════════════════════════

_PIL_FONTS: dict = {}


def _pil_font(bold: bool, px: float) -> ImageFont.FreeTypeFont:
    key = ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", int(round(px)))
    if key not in _PIL_FONTS:
        _PIL_FONTS[key] = ImageFont.truetype(str(FONTS_DIR / key[0]), key[1])
    return _PIL_FONTS[key]


def _rgba(hex_: str | None):
    if hex_ is None:
        return None
    return tuple(int(hex_[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


class Art:
    """Холст Pillow. Координаты задаются в «проектных» пикселях, рисование идёт с запасом (scale),
    итог уменьшается — так получаются сглаженные края фигур и текста."""

    def __init__(self, w: int, h: int, bg: str | None, scale: int = 2):
        self.w, self.h, self.s = w, h, scale
        self.img = Image.new("RGBA", (w * scale, h * scale), _rgba(bg) or (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    def rect(self, x, y, w, h, fill=None, outline=None, width=0, r=0) -> None:
        s = self.s
        box = [round(x * s), round(y * s), round((x + w) * s), round((y + h) * s)]
        lw = max(1, round(width * s)) if outline else 0
        if r:
            self.d.rounded_rectangle(box, radius=round(r * s), fill=_rgba(fill), outline=_rgba(outline), width=lw)
        else:
            self.d.rectangle(box, fill=_rgba(fill), outline=_rgba(outline), width=lw)

    def circle(self, cx, cy, r, fill=None, outline=None, width=0) -> None:
        s = self.s
        box = [round((cx - r) * s), round((cy - r) * s), round((cx + r) * s), round((cy + r) * s)]
        self.d.ellipse(box, fill=_rgba(fill), outline=_rgba(outline), width=max(1, round(width * s)) if outline else 0)

    def line(self, pts, fill, width) -> None:
        s = self.s
        scaled = [(x * s, y * s) for x, y in pts]
        lw = max(1, round(width * s))
        self.d.line(scaled, fill=_rgba(fill), width=lw, joint="curve")
        for x, y in (scaled[0], scaled[-1]):                     # скруглённые концы
            self.d.ellipse([x - lw / 2, y - lw / 2, x + lw / 2, y + lw / 2], fill=_rgba(fill))

    def poly(self, pts, fill) -> None:
        self.d.polygon([(x * self.s, y * self.s) for x, y in pts], fill=_rgba(fill))

    def text(self, x, y, txt, size, fill, bold=False, anchor="la") -> None:
        self.d.text((x * self.s, y * self.s), txt, font=_pil_font(bold, size * self.s), fill=_rgba(fill), anchor=anchor)

    def tw(self, txt, size, bold=False) -> float:
        return _pil_font(bold, size * self.s).getlength(txt) / self.s

    def check(self, cx, cy, r, color=HEX_WHITE) -> None:
        """Галочка внутри кружка радиуса r."""
        self.line([(cx - 0.45 * r, cy + 0.02 * r), (cx - 0.12 * r, cy + 0.36 * r), (cx + 0.48 * r, cy - 0.32 * r)],
                  color, 0.2 * r)

    def paste(self, other: "Art", cx, cy, angle: float = 0) -> None:
        """Вклеивает другой холст по центру (cx, cy), при необходимости повернув."""
        im = other.img.rotate(angle, resample=Image.BICUBIC, expand=True) if angle else other.img
        self.img.alpha_composite(im, (round(cx * self.s - im.width / 2), round(cy * self.s - im.height / 2)))

    def done(self, full_res: bool = False) -> Image.Image:
        rgb = self.img.convert("RGB")
        return rgb if full_res else rgb.resize((self.w, self.h), Image.LANCZOS)


SHEET_W, SHEET_H = 440, 560          # размер «листа» на иллюстрациях


def _pil_sheet(a: Art, ox, oy) -> None:
    """Белый лист с тенью — основа всех иллюстраций."""
    a.rect(ox + 8, oy + 10, SHEET_W, SHEET_H, fill="DAD5CA", r=12)
    a.rect(ox, oy, SHEET_W, SHEET_H, fill=HEX_WHITE, outline=HEX_LINE, width=2, r=12)


def _pil_stamp(scale: int, active: bool = True) -> Art:
    """Синий штамп «Контролируемая копия» (в неактивном виде — бледный)."""
    color = HEX_STAMP if active else "B9C5DD"
    w, h = 286, 104
    a = Art(w, h, None, scale)
    a.rect(3, 3, w - 6, h - 6, outline=color, width=5, r=12)
    a.rect(12, 12, w - 24, h - 24, outline=color, width=1.5, r=7)
    a.text(w / 2, 37, "КОНТРОЛИРУЕМАЯ", 23, color, bold=True, anchor="mm")
    a.text(w / 2, 67, "КОПИЯ · экз. № 07", 21, color, bold=True, anchor="mm")
    return a


def _illus_title_page(a: Art, ox, oy, hl: tuple = ()) -> None:
    """Титульный лист СОП; hl — какие зоны подсветить: code, rev, date, stamp."""
    _pil_sheet(a, ox, oy)
    a.rect(ox + 26, oy + 26, SHEET_W - 52, 46, fill=HEX_TEAL, r=7)
    a.text(ox + 42, oy + 49, ORG, 24, HEX_WHITE, bold=True, anchor="lm")
    a.text(ox + SHEET_W - 42, oy + 49, "СОП", 20, "CFE3E0", bold=True, anchor="rm")

    def field(x, y, w, label, value, size, on) -> None:
        a.text(x, y, label, 17, HEX_MUTED)
        a.rect(x, y + 26, w, 64, fill=HEX_AMBER_TINT if on else "F0F2F1", outline=HEX_AMBER if on else None,
               width=5, r=9)
        a.text(x + 16, y + 58, value, size, HEX_INK, bold=True, anchor="lm")

    field(ox + 26, oy + 88, SHEET_W - 52, "Код документа", SOP_CODE, 38, "code" in hl)
    field(ox + 26, oy + 196, 150, "Редакция", RED1["rev"], 38, "rev" in hl)
    field(ox + 196, oy + 196, SHEET_W - 26 - 196, "Введена в действие", RED1["date"], 29, "date" in hl)
    for i, wd in enumerate((388, 290)):                           # строки-заглушки названия
        a.rect(ox + 26, oy + 310 + i * 26, wd, 14, fill="E2E6E4", r=7)
    for i, wd in enumerate((388, 388, 330)):                      # строки-заглушки текста
        a.rect(ox + 26, oy + 372 + i * 18, wd, 7, fill="EBEEEC", r=3)
    if "stamp" in hl:
        a.rect(ox + 20, oy + 430, 330, 118, fill=HEX_AMBER_TINT, outline=HEX_AMBER, width=5, r=12)
    a.paste(_pil_stamp(a.s), ox + 185, oy + 489, angle=5)


def _illus_registry(a: Art, ox, oy, hl: bool = False) -> None:
    """Страница «Реестра действующих редакций СОП»; hl — подсветить строку нужного кода."""
    _pil_sheet(a, ox, oy)
    a.rect(ox + 26, oy + 26, 70, 8, fill=HEX_AMBER, r=4)
    a.text(ox + 26, oy + 48, "Реестр действующих", 29, HEX_TEAL, bold=True)
    a.text(ox + 26, oy + 86, "редакций СОП", 29, HEX_TEAL, bold=True)
    y = oy + 146
    a.rect(ox + 20, y, SHEET_W - 40, 42, fill=HEX_TEAL_TINT, r=7)
    a.text(ox + 36, y + 21, "Код", 18, HEX_MUTED, bold=True, anchor="lm")
    a.text(ox + 238, y + 21, "Ред.", 18, HEX_MUTED, bold=True, anchor="mm")
    a.text(ox + SHEET_W - 36, y + 21, "Введена", 18, HEX_MUTED, bold=True, anchor="rm")
    rows = [("СОП-ТБ-021", "4", "12.01.2026"), (SOP_CODE, RED1["rev"], RED1["date"]),
            ("СОП-ПР-007", "2", "10.11.2025"), ("СОП-ОЧ-004", "3", "16.12.2025")]
    for i, (code, rev, date) in enumerate(rows):
        ry = oy + 198 + i * 76
        mine = code == SOP_CODE
        if mine and hl:
            a.rect(ox + 12, ry + 2, SHEET_W - 24, 68, fill=HEX_AMBER_TINT, outline=HEX_AMBER, width=5, r=10)
        elif i < len(rows) - 1:
            a.rect(ox + 26, ry + 73, SHEET_W - 52, 2, fill="E2E6E4")
        ink = HEX_INK if mine else "7D8A88"
        a.text(ox + 36, ry + 36, code, 23, ink, bold=True, anchor="lm")
        a.text(ox + 238, ry + 36, rev, 23, ink, bold=True, anchor="mm")
        a.text(ox + SHEET_W - 36, ry + 36, date, 21, ink, bold=mine, anchor="rm")
    a.text(ox + 26, oy + 522, "Папка на посту мастера смены", 18, HEX_MUTED)


def _illus_compare(a: Art, ox, oy, ticks: int = 0, stamp: bool = False) -> None:
    """Сверка трёх признаков: ticks — сколько уже совпало; stamp — штамп проверен."""
    _pil_sheet(a, ox, oy)
    a.text(ox + 26, oy + 26, "Экземпляр = реестр?", 27, HEX_TEAL, bold=True)
    for i, (label, value) in enumerate((("Код", SOP_CODE), ("Редакция", RED1["rev"]), ("Дата введения", RED1["date"]))):
        y = oy + 76 + i * 104
        on = i < ticks
        a.rect(ox + 26, y, SHEET_W - 52, 90, fill=HEX_TEAL_TINT if on else "F0F2F1", r=11)
        a.text(ox + 44, y + 13, label, 18, HEX_MUTED)
        a.text(ox + 44, y + 59, value, 31, HEX_INK, bold=True, anchor="lm")
        cx, cy = ox + SHEET_W - 76, y + 45
        if on:
            a.circle(cx, cy, 27, fill=HEX_TEAL)
            a.check(cx, cy, 27)
        else:
            a.circle(cx, cy, 27, fill=HEX_WHITE, outline="C5CDCA", width=3)
    if stamp:
        a.rect(ox + 20, oy + 396, SHEET_W - 40, 146, fill=HEX_AMBER_TINT, outline=HEX_AMBER, width=5, r=12)
    a.paste(_pil_stamp(a.s, active=stamp), ox + 182, oy + 469, angle=5)
    if stamp:
        a.circle(ox + SHEET_W - 60, oy + 469, 24, fill=HEX_STAMP)
        a.check(ox + SHEET_W - 60, oy + 469, 24)


def _scribble(x0, y0, length, phase: float) -> list:
    """Точки условной подписи — детерминированная «закорючка»."""
    pts = []
    for k in range(61):
        t = k / 60
        pts.append((x0 + length * t + 9 * math.sin(15 * t + phase),
                    y0 - 15 * math.sin(10.5 * t + phase * 0.7) * (1 - 0.55 * t)))
    return pts


def _illus_journal(a: Art, ox, oy, signed: bool = False, go: bool = False) -> None:
    """Журнал начала смены: подпись оператора и разрешение работать."""
    _pil_sheet(a, ox, oy)
    a.rect(ox + 26, oy + 26, 70, 8, fill=HEX_AMBER, r=4)
    a.text(ox + 26, oy + 48, "Журнал начала", 29, HEX_TEAL, bold=True)
    a.text(ox + 26, oy + 86, "смены", 29, HEX_TEAL, bold=True)
    y = oy + 146
    a.rect(ox + 20, y, SHEET_W - 40, 42, fill=HEX_TEAL_TINT, r=7)
    a.text(ox + 36, y + 21, "Оператор", 18, HEX_MUTED, bold=True, anchor="lm")
    a.text(ox + 250, y + 21, "Подпись", 18, HEX_MUTED, bold=True, anchor="lm")
    for i, (name, phase) in enumerate((("Иманов Б.", 0.4), ("Касымова Д.", 2.1), ("Абенов Т.", 4.0))):
        ry = oy + 198 + i * 76
        last = i == 2
        if last and signed:
            a.rect(ox + 12, ry + 2, SHEET_W - 24, 68, fill=HEX_AMBER_TINT, outline=HEX_AMBER, width=5, r=10)
        elif not last:
            a.rect(ox + 26, ry + 73, SHEET_W - 52, 2, fill="E2E6E4")
        a.text(ox + 36, ry + 36, name, 23, HEX_INK if last else "7D8A88", bold=last, anchor="lm")
        if not last:
            a.line(_scribble(ox + 256, ry + 40, 120, phase), "9AA8C4", 3)
        elif signed:
            a.line(_scribble(ox + 256, ry + 40, 120, phase), HEX_STAMP, 4.5)
        else:
            a.rect(ox + 250, ry + 52, 140, 2, fill="B9C2BF")
    by = oy + 448
    if go:
        a.rect(ox + 26, by, SHEET_W - 52, 84, fill=HEX_TEAL, r=42)
        a.circle(ox + 78, by + 42, 26, fill=HEX_WHITE)
        a.check(ox + 78, by + 42, 26, color=HEX_TEAL)
        a.text(ox + 122, by + 42, "Можно работать", 28, HEX_WHITE, bold=True, anchor="lm")
    else:
        a.rect(ox + 26, by, SHEET_W - 52, 84, fill="F0F2F1", r=42)
        a.text(ox + SHEET_W / 2, by + 42, "ждёт подписи", 24, "9AA5A2", anchor="mm")


def _title_page_png() -> bytes:
    """PNG-схема титульного листа для слайда: подсвечены код и номер редакции."""
    a = Art(SHEET_W + 48, SHEET_H + 48, HEX_PAPER, scale=2)
    _illus_title_page(a, 20, 18, hl=("code", "rev"))
    buf = io.BytesIO()
    a.done(full_res=True).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ════════════════════════════════════════════════════════════════════════════════════════
# 9. PPTX: пятиминутка
# ════════════════════════════════════════════════════════════════════════════════════════

PP_FONT = "Arial"
SLIDE_W, SLIDE_H = 13.333, 7.5       # дюймы, 16:9


def _pp_fill_text(tf, paras, size, color=HEX_INK, bold=False, align="l", anchor="t", line=1.08) -> None:
    """Заполняет текстовую рамку: paras — строка, список строк или список абзацев из пар (текст, жирный)."""
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE, "b": MSO_ANCHOR.BOTTOM}[anchor]
    if isinstance(paras, str):
        paras = [paras]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER, "r": PP_ALIGN.RIGHT}[align]
        p.line_spacing = line
        for txt, is_bold in ([(para, bold)] if isinstance(para, str) else para):
            run = p.add_run()
            run.text = txt
            run.font.name = PP_FONT
            run.font.size = PPt(size)
            run.font.bold = is_bold
            run.font.color.rgb = PRGB.from_string(color)


def _pp_text(slide, x, y, w, h, paras, size, **kw):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    _pp_fill_text(box.text_frame, paras, size, **kw)
    return box


def _pp_shape(slide, kind, x, y, w, h, fill: str, radius: float | None = None):
    """Фигура со сплошной заливкой, без контура и тени."""
    shape = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = PRGB.from_string(fill)
    shape.line.fill.background()
    shape.shadow.inherit = False
    if radius is not None and kind == MSO_SHAPE.ROUNDED_RECTANGLE:
        shape.adjustments[0] = radius
    return shape


def _pp_number(slide, x, y, d, text, size, fill=HEX_TEAL, color=HEX_WHITE):
    """Кружок с номером."""
    circle = _pp_shape(slide, MSO_SHAPE.OVAL, x, y, d, d, fill)
    _pp_fill_text(circle.text_frame, text, size, color=color, bold=True, align="c", anchor="m")
    return circle


def _pp_content_slide(prs, title: str, number: int, total: int, notes: str):
    """Слайд с содержимым: фон «бумага», цветная полоса заголовка, пометка и номер слайда."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _pp_shape(slide, MSO_SHAPE.RECTANGLE, 0, 0, SLIDE_W, SLIDE_H, HEX_PAPER)
    _pp_shape(slide, MSO_SHAPE.RECTANGLE, 0, 0, SLIDE_W, 1.45, HEX_TEAL)
    _pp_shape(slide, MSO_SHAPE.RECTANGLE, 0, 1.45, SLIDE_W, 0.1, HEX_AMBER)
    _pp_text(slide, 0.7, 0.2, 11.9, 1.05, title, 40, color=HEX_WHITE, bold=True, anchor="m")
    _pp_text(slide, 0.7, 7.02, 9.0, 0.3, f"{NOTICE}  ·  {ORG}, отдел обучения", 12, color=HEX_MUTED)
    _pp_text(slide, 11.63, 7.02, 1.0, 0.3, f"{number} / {total}", 12, color=HEX_MUTED, align="r")
    slide.notes_slide.notes_text_frame.text = notes
    return slide


def _pp_step_card(slide, x, y, w, h, number: str, paras) -> None:
    """Карточка шага: белая плашка, янтарная планка, кружок с номером и текст."""
    _pp_shape(slide, MSO_SHAPE.RECTANGLE, x, y, w, h, HEX_WHITE)
    _pp_shape(slide, MSO_SHAPE.RECTANGLE, x, y, 0.14, h, HEX_AMBER)
    _pp_number(slide, x + 0.4, y + (h - 1.0) / 2, 1.0, number, 40)
    _pp_text(slide, x + 1.7, y + 0.15, w - 1.95, h - 0.3, paras, 24, anchor="m", line=1.1)


def make_deck(path: Path) -> None:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(SLIDE_W), Inches(SLIDE_H)
    total = 6
    deck_title = "Пятиминутка: та ли редакция у вас в руках?"

    # ── 1. Титульный слайд ──
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _pp_shape(s, MSO_SHAPE.RECTANGLE, 0, 0, SLIDE_W, SLIDE_H, HEX_TEAL)
    _pp_shape(s, MSO_SHAPE.RECTANGLE, 0, 0, 0.45, SLIDE_H, HEX_AMBER)
    _pp_text(s, 1.2, 1.75, 11.2, 2.7, deck_title, 54, color=HEX_WHITE, bold=True, anchor="m", line=1.02)
    _pp_text(s, 1.2, 0.75, 11.0, 0.5, f"{ORG}  ·  участок таблетирования", 24, color="BFD8D4")
    _pp_shape(s, MSO_SHAPE.RECTANGLE, 1.2, 4.7, 1.6, 0.09, HEX_AMBER)
    _pp_text(s, 1.2, 4.95, 11.2, 1.0, f"{SOP_CODE} «{SOP_TITLE}»", 26, color=HEX_PAPER)
    _pp_text(s, 1.2, 6.75, 9.0, 0.35, f"{NOTICE}  ·  Подготовил отдел обучения", 14, color="BFD8D4")
    s.notes_slide.notes_text_frame.text = (
        "Пятиминутка перед сменой. Цель: каждый оператор умеет за две минуты проверить, что работает "
        "по действующей редакции СОП. Ведёт мастер смены.")

    # ── 2. Зачем проверять ──
    s = _pp_content_slide(prs, "Зачем проверять", 2, total,
                          "Три причины. Главная мысль: устаревшая редакция — это отклонение, а проверка занимает две минуты.")
    reasons = [
        [("Устаревшая редакция", True), (" — это отклонение от утверждённой технологии.", False)],
        [("Серия может быть задержана", True), (" до окончания расследования.", False)],
        [("Проверка занимает", False), (" около двух минут.", True)],
    ]
    for i, para in enumerate(reasons):
        y = 1.95 + i * 1.62
        _pp_shape(s, MSO_SHAPE.RECTANGLE, 0.7, y, 11.93, 1.36, HEX_WHITE)
        _pp_shape(s, MSO_SHAPE.RECTANGLE, 0.7, y, 0.14, 1.36, HEX_AMBER)
        _pp_shape(s, MSO_SHAPE.OVAL, 1.2, y + 0.48, 0.4, 0.4, HEX_TEAL)
        _pp_text(s, 1.95, y + 0.1, 10.4, 1.16, [para], 28, anchor="m")

    # ── 3. Шаги 1–2 (со схемой титульного листа) ──
    s = _pp_content_slide(prs, "Шаги 1–2", 3, total,
                          "Шаг 1: взять свой рабочий экземпляр, найти код и номер редакции на титульном листе. "
                          "Шаг 2: открыть реестр в папке на посту мастера смены и найти строку с тем же кодом.")
    pic_h = 4.8
    pic_w = pic_h * (SHEET_W + 48) / (SHEET_H + 48)
    pic_x = SLIDE_W - 0.6 - pic_w
    card_w = pic_x - 0.7 - 0.3
    _pp_step_card(s, 0.7, 1.9, card_w, 2.28, "1",
                  [[("Возьмите рабочий экземпляр СОП. Найдите на титульном листе ", False),
                    ("код документа", True), (" и ", False), ("номер редакции", True), (".", False)]])
    _pp_step_card(s, 0.7, 4.42, card_w, 2.28, "2",
                  [[("Откройте ", False), ("«Реестр действующих редакций СОП»", True),
                    (" — папка на посту мастера смены. Найдите строку с тем же кодом.", False)]])
    s.shapes.add_picture(io.BytesIO(_title_page_png()), Inches(pic_x), Inches(1.9), height=Inches(pic_h))

    # ── 4. Шаги 3–4 ──
    s = _pp_content_slide(prs, "Шаги 3–4", 4, total,
                          "Шаг 3: сверить код, номер редакции и дату введения в действие, проверить синий штамп. "
                          "Шаг 4: всё совпало — подпись в журнале начала смены и работа.")
    _pp_step_card(s, 0.7, 1.9, 11.93, 2.28, "3",
                  [[("Сверьте три признака: ", False), ("код, номер редакции, дату введения в действие", True),
                    (". Проверьте синий штамп ", False), ("«Контролируемая копия»", True),
                    (" с номером экземпляра.", False)]])
    _pp_step_card(s, 0.7, 4.42, 11.93, 2.28, "4",
                  [[("Всё совпало", True), (" — распишитесь в журнале начала смены и приступайте к работе.", False)],
                   [("Не совпал хотя бы один признак", True), (" — не начинайте работу.", False)]])

    # ── 5. Не совпало — что делать ──
    s = _pp_content_slide(prs, "Не совпало — что делать", 5, total,
                          "Три действия по порядку: остановиться, сообщить мастеру смены, ничего не исправлять самому. "
                          "Экземпляр не выбрасывать.")
    blocks = [
        (HEX_STOP, HEX_WHITE, ["СТОП"], 60, "Не начинайте работу по этому документу"),
        (HEX_TEAL, HEX_WHITE, ["Сообщите", "мастеру смены"], 28, "Назовите код и что не совпало"),
        (HEX_AMBER, HEX_INK, ["Не исправляйте", "сами"], 28, "Экземпляр не выбрасывайте"),
    ]
    bw, bh, gap = 3.6, 3.75, 0.565
    for i, (fill, ink, head, head_size, sub) in enumerate(blocks):
        x = 0.7 + i * (bw + gap)
        _pp_shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, 1.95, bw, bh, fill, radius=0.07)
        _pp_text(s, x + 0.1, 2.2, bw - 0.2, 1.45, head, head_size, color=ink, bold=True, align="c", anchor="m", line=1.05)
        _pp_text(s, x + 0.2, 3.85, bw - 0.4, 1.7, sub, 24, color=ink, align="c", anchor="t", line=1.1)
        if i < len(blocks) - 1:
            _pp_shape(s, MSO_SHAPE.RIGHT_ARROW, x + bw + 0.08, 3.52, gap - 0.16, 0.6, HEX_INK)
    _pp_text(s, 0.7, 6.0, 11.93, 0.8, [[("Работаем только по действующей редакции из реестра.", True)]], 26,
             color=HEX_TEAL, align="c", anchor="m")

    # ── 6. Проверь себя ──
    s = _pp_content_slide(prs, "Проверь себя", 6, total,
                          "Ответы. 1: код, номер редакции, дата введения в действие. 2: в папке на посту мастера смены. "
                          "3: не начинать работу по этому документу, сообщить мастеру смены, экземпляр не выбрасывать "
                          "и не исправлять.")
    questions = [
        "Какие три признака вы сверяете с реестром?",
        "Где лежит «Реестр действующих редакций СОП»?",
        "Что вы делаете, если не совпал хотя бы один признак?",
    ]
    for i, q in enumerate(questions):
        y = 1.95 + i * 1.62
        _pp_shape(s, MSO_SHAPE.RECTANGLE, 0.7, y, 11.93, 1.36, HEX_WHITE)
        _pp_number(s, 1.0, y + 0.23, 0.9, str(i + 1), 34, fill=HEX_AMBER, color=HEX_INK)
        _pp_text(s, 2.25, y + 0.1, 10.1, 1.16, q, 28, anchor="m")

    cp = prs.core_properties
    cp.title, cp.author, cp.last_modified_by = deck_title, AUTHOR, AUTHOR
    cp.subject, cp.comments, cp.language = NOTICE, NOTICE, "ru-RU"
    cp.created = cp.modified = FIXED_DT
    cp.revision = 1
    prs.save(str(path))
    _normalize_office_zip(path)


# ════════════════════════════════════════════════════════════════════════════════════════
# 10. MP4: короткое видео «Проверка СОП за четыре шага»
# ════════════════════════════════════════════════════════════════════════════════════════

VW, VH, FPS = 1280, 720, 30
V_SCALE = 2                     # запас разрешения при рисовании карточек
V_FADE = 0.6                    # проявление подсветки внутри карточки, с
V_XFADE = 0.4                   # переход между карточками, с
V_TEXT = 54                     # кегль главной фразы, px (не меньше 52)
V_TEXT_W = 700                  # ширина колонки текста

# Карточки шагов: строки фразы подобраны вручную, чтобы текст оставался крупным и редким.
VIDEO_STEPS = [
    ["Найдите на титульном", "листе код и номер", "редакции"],
    ["Откройте реестр", "и найдите строку", "с тем же кодом"],
    ["Сверьте код,", "редакцию и дату.", "Проверьте", "синий штамп"],
    ["Всё совпало —", "распишитесь", "и приступайте", "к работе"],
]


def _v_watermark(a: Art, color: str) -> None:
    a.text(VW - 60, 42, "Учебный образец", 22, color, anchor="rm")


def _v_title_card(stage: int) -> Image.Image:
    a = Art(VW, VH, HEX_TEAL, V_SCALE)
    a.rect(64, 156, 16, 222, fill=HEX_AMBER, r=8)
    a.text(112, 140, "Та ли редакция", 96, HEX_WHITE, bold=True)
    a.text(112, 258, "у вас в руках?", 96, HEX_WHITE, bold=True)
    if stage >= 1:
        a.text(112, 430, "Проверка СОП перед сменой", 46, HEX_PAPER)
        a.text(112, 494, "4 шага · 2 минуты", 46, HEX_AMBER, bold=True)
    a.text(112, 628, f"{ORG} · участок таблетирования", 28, "BFD8D4")
    _v_watermark(a, "BFD8D4")
    return a.done()


def _v_step_card(n: int, lines: list, illus) -> Image.Image:
    a = Art(VW, VH, HEX_PAPER, V_SCALE)
    label = f"Шаг {n} из {len(VIDEO_STEPS)}"
    a.rect(64, 40, a.tw(label, 36, True) + 60, 68, fill=HEX_TEAL, r=34)
    a.text(94, 74, label, 36, HEX_WHITE, bold=True, anchor="lm")
    a.text(58, 300, str(n), 190, HEX_TEAL, bold=True, anchor="ls")
    a.rect(64, 320, 120, 10, fill=HEX_AMBER, r=5)
    size = V_TEXT
    while size > 52 and max(a.tw(ln, size, True) for ln in lines) > V_TEXT_W:
        size -= 1
    widest = max(a.tw(ln, size, True) for ln in lines)
    if widest > V_TEXT_W:
        raise ValueError(f"Фраза шага {n} не помещается в колонку: {widest:.0f}px > {V_TEXT_W}px")
    for i, ln in enumerate(lines):
        a.text(64, 356 + i * round(size * 1.3), ln, size, HEX_INK, bold=True)
    _v_watermark(a, "8C9896")
    illus(a, 790, 84)
    return a.done()


def _v_final_card(stage: int) -> Image.Image:
    a = Art(VW, VH, HEX_INK, V_SCALE)
    cx, cy = 262, 348

    def octagon(r: float) -> list:
        return [(cx + r * math.cos(math.radians(22.5 + 45 * k)), cy + r * math.sin(math.radians(22.5 + 45 * k)))
                for k in range(8)]

    a.poly(octagon(190), HEX_WHITE)
    a.poly(octagon(174), HEX_STOP)
    a.text(cx, cy, "СТОП", 88, HEX_WHITE, bold=True, anchor="mm")
    x = 500
    a.text(x, 150, "Не совпало?", 66, HEX_AMBER, bold=True)
    if stage >= 1:
        a.text(x, 262, "Стоп.", 66, HEX_WHITE, bold=True)
    if stage >= 2:
        a.text(x, 374, "Сообщите мастеру", 66, HEX_WHITE, bold=True)
        a.text(x, 460, "смены.", 66, HEX_WHITE, bold=True)
    _v_watermark(a, "8C9896")
    return a.done()


def _video_cards() -> list:
    """Сценарий: (длительность, [(момент появления, кадр-состояние), ...]) для каждой карточки."""
    cards = [(4.0, [(0.0, _v_title_card(0)), (1.0, _v_title_card(1))])]
    cards.append((8.0, [
        (0.0, _v_step_card(1, VIDEO_STEPS[0], lambda a, x, y: _illus_title_page(a, x, y))),
        (0.9, _v_step_card(1, VIDEO_STEPS[0], lambda a, x, y: _illus_title_page(a, x, y, hl=("code",)))),
        (2.1, _v_step_card(1, VIDEO_STEPS[0], lambda a, x, y: _illus_title_page(a, x, y, hl=("code", "rev")))),
    ]))
    cards.append((8.0, [
        (0.0, _v_step_card(2, VIDEO_STEPS[1], lambda a, x, y: _illus_registry(a, x, y))),
        (1.2, _v_step_card(2, VIDEO_STEPS[1], lambda a, x, y: _illus_registry(a, x, y, hl=True))),
    ]))
    cards.append((8.0, [
        (0.0, _v_step_card(3, VIDEO_STEPS[2], lambda a, x, y: _illus_compare(a, x, y))),
        (0.9, _v_step_card(3, VIDEO_STEPS[2], lambda a, x, y: _illus_compare(a, x, y, ticks=1))),
        (1.8, _v_step_card(3, VIDEO_STEPS[2], lambda a, x, y: _illus_compare(a, x, y, ticks=2))),
        (2.7, _v_step_card(3, VIDEO_STEPS[2], lambda a, x, y: _illus_compare(a, x, y, ticks=3))),
        (4.0, _v_step_card(3, VIDEO_STEPS[2], lambda a, x, y: _illus_compare(a, x, y, ticks=3, stamp=True))),
    ]))
    cards.append((8.0, [
        (0.0, _v_step_card(4, VIDEO_STEPS[3], lambda a, x, y: _illus_journal(a, x, y))),
        (1.2, _v_step_card(4, VIDEO_STEPS[3], lambda a, x, y: _illus_journal(a, x, y, signed=True))),
        (2.4, _v_step_card(4, VIDEO_STEPS[3], lambda a, x, y: _illus_journal(a, x, y, signed=True, go=True))),
    ]))
    cards.append((6.0, [(0.0, _v_final_card(0)), (1.1, _v_final_card(1)), (2.2, _v_final_card(2))]))
    return cards


def _ease(k: float) -> float:
    k = min(1.0, max(0.0, k))
    return k * k * (3 - 2 * k)


def _card_frame(stages: list, t: float) -> Image.Image:
    """Кадр карточки в момент t: состояния плавно сменяют друг друга."""
    idx = 0
    for i, (start, _) in enumerate(stages):
        if t >= start:
            idx = i
    start, img = stages[idx]
    if idx > 0 and t < start + V_FADE:
        return Image.blend(stages[idx - 1][1], img, _ease((t - start) / V_FADE))
    return img


def _render_video_frames(frames_dir: Path) -> int:
    """Рисует все кадры в PNG. Внизу — полоса прогресса с отметками границ карточек."""
    cards = _video_cards()
    total = sum(round(dur * FPS) for dur, _ in cards)
    bounds, acc = [], 0
    for dur, _ in cards[:-1]:
        acc += round(dur * FPS)
        bounds.append(round(VW * acc / total))
    bar_y, n, prev_last = VH - 14, 0, None
    for dur, stages in cards:
        for i in range(round(dur * FPS)):
            t = i / FPS
            img = _card_frame(stages, t)
            if prev_last is not None and t < V_XFADE:
                img = Image.blend(prev_last, img, _ease(t / V_XFADE))
            frame = img.copy()
            d = ImageDraw.Draw(frame)
            d.rectangle([0, bar_y, VW, VH], fill=(16, 30, 30))
            d.rectangle([0, bar_y, round(VW * (n + 1) / total), VH], fill=_rgba(HEX_AMBER)[:3])
            for bx in bounds:
                d.rectangle([bx - 1, bar_y, bx + 1, VH], fill=_rgba(HEX_PAPER)[:3])
            frame.save(frames_dir / f"f_{n:05d}.png", compress_level=1)
            n += 1
        prev_last = stages[-1][1]
    return n


def make_video(path: Path) -> None:
    ffmpeg = FFMPEG if Path(FFMPEG).exists() else shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден: укажите путь в переменной окружения FFMPEG_PATH")
    work = Path(os.environ.get("LMS_MATERIALS_TMP") or tempfile.gettempdir()) / "dolce_materials_build"
    frames_dir = work / "video_frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)
    count = _render_video_frames(frames_dir)
    tmp_out = work / "video-sop-check.mp4"          # кодируем во временный ASCII-путь, затем копируем в проект
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-framerate", str(FPS), "-i", "f_%05d.png",
        "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        "-c:v", "libx264", "-preset", "slow", "-crf", "22", "-profile:v", "high", "-level:v", "4.0",
        "-pix_fmt", "yuv420p", "-r", str(FPS), "-g", str(FPS), "-keyint_min", str(FPS), "-sc_threshold", "0",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
        "-an", "-movflags", "+faststart", "-map_metadata", "-1",
        "-fflags", "+bitexact", "-flags:v", "+bitexact", "-threads", "4",
        str(tmp_out),
    ]
    subprocess.run(cmd, check=True, cwd=str(frames_dir))
    shutil.copyfile(tmp_out, path)
    if not os.environ.get("LMS_MATERIALS_KEEP_FRAMES"):
        shutil.rmtree(frames_dir, ignore_errors=True)
        tmp_out.unlink(missing_ok=True)
    print(f"      кадров: {count}, длительность: {count / FPS:.1f} с")


# ════════════════════════════════════════════════════════════════════════════════════════
# 11. SOURCES.md и запуск
# ════════════════════════════════════════════════════════════════════════════════════════

MATERIALS = [
    ("sop-ok-015-red1.pdf", make_sop_red1,
     "СОП-ОК-015 «Проверка актуальности СОП перед началом смены», редакция 1 (введена 03.02.2026): 4 шага оператора, схема титульного листа."),
    ("sop-ok-015-red2.pdf", make_sop_red2,
     "Тот же СОП-ОК-015, редакция 2 (введена 01.10.2026): 5 шагов, шаг с QR-кодом, блок «Что изменилось в редакции 2»."),
    ("sop-ok-015-red3.pdf", make_sop_red3,
     "Тот же СОП-ОК-015, редакция 3 (введена 20.11.2026): те же 5 шагов, уточнён раздел «Если данные не совпали». "
     "В демоданные не входит: редакцию 3 методист выпускает через интерфейс."),
    ("checklist-sop.docx", make_checklist,
     "Чек-лист «Проверка СОП перед сменой»: таблица из 6 пунктов и порядок действий, если данные не совпали."),
    ("reestr-sop.xlsx", make_registry,
     "Реестр действующих редакций СОП участка таблетирования: листы «Действующие» (12 документов) и «Изъятые редакции» (5 записей)."),
    ("pyatiminutka-sop.pptx", make_deck,
     "Презентация-пятиминутка «Та ли редакция у вас в руках?», 6 слайдов 16:9 с заметками докладчика."),
    ("video-sop-check.mp4", make_video,
     "Видео 42 с без звука, 1280×720: четыре шага проверки СОП и карточка «Не совпало? Стоп. Сообщите мастеру смены.»."),
    ("gigiena-chistaya-zona.pdf", make_hygiene,
     "СОП-ПР-007 «Гигиена и переодевание при входе в чистую зону», редакция 3 (введена 15.05.2026): 7 шагов и список запретов."),
    ("pamyatka-nesootvetstvie.pdf", make_memo,
     "Памятка на одну страницу «В смене оказалась инструкция другой редакции»: 5 правил и внутренние телефоны."),
    ("glossary-gmp.pdf", make_glossary,
     "Краткий глоссарий для оператора: 12 терминов простыми словами."),
    ("zhurnal-smeny.docx", make_shift_log,
     "Образец заполнения журнала приёма-передачи смены: пояснение и таблица с четырьмя записями."),
    ("grafik-obucheniya.xlsx", make_training_plan,
     "График обучения на IV квартал 2026: 8 строк с темами, сроками и ответственными."),
]


def make_sources(path: Path) -> None:
    lines = [
        "# Источники учебных материалов",
        "",
        "Все тексты, таблицы, слайды, рисунки и видео в этой папке — **оригинальные работы команды проекта**,",
        "созданные специально для прототипа LMS Dolce Pharm. Сторонние тексты, изображения, шаблоны и видеозаписи",
        "не использовались.",
        "",
        f"- **Данные вымышлены.** Предприятие, фамилии, коды документов, даты и внутренние телефоны придуманы; "
        f"каждый документ помечен «{NOTICE}»",
        "- **Использование.** Материалы можно свободно использовать, изменять и распространять внутри проекта.",
        "- **Шрифты.** В PDF и видео — DejaVu (свободная лицензия, см. `backend/app/assets/fonts/LICENSE-DejaVu.txt`). "
        "В DOCX, XLSX и PPTX шрифты не встроены: названы Arial и Calibri, которые есть на рабочих компьютерах.",
        "- **Как получены.** Все файлы создаёт скрипт `backend/demo/make_materials.py` "
        "(reportlab, python-docx, openpyxl, python-pptx, Pillow; видео кодирует ffmpeg). Повторный запуск даёт те же документы.",
        "",
        "## Файлы",
        "",
        "| Файл | Что это |",
        "|---|---|",
    ]
    lines += [f"| `{name}` | {desc} |" for name, _, desc in MATERIALS]
    lines += [
        "",
        "## Как пересоздать",
        "",
        "```",
        r".venv\Scripts\python.exe backend\demo\make_materials.py",
        "```",
        "",
        "Формулировки шагов СОП-ОК-015 заданы в начале скрипта (`STEPS_RED1`, `STEPS_RED2`) — на них опираются тест "
        "и диалоговый тренажёр, поэтому менять их нужно одновременно.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Генератор учебных материалов Dolce Pharm")
    parser.add_argument("--only", help="имена файлов через запятую (по умолчанию — все)")
    parser.add_argument("--skip-video", action="store_true", help="не пересобирать видео")
    args = parser.parse_args()
    only = {x.strip() for x in args.only.split(",")} if args.only else None

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    register_fonts()
    failed = 0
    for name, func, _ in MATERIALS:
        if only and name not in only:
            continue
        if args.skip_video and name.endswith(".mp4"):
            continue
        target = OUT_DIR / name
        try:
            func(target)
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            if not name.endswith(".mp4"):
                raise
            failed += 1                      # нет ffmpeg или он упал: остальные материалы всё равно создаём
            print(f"  !!  {name:32s} не создан: {exc}")
            continue
        print(f"  ok  {name:32s} {target.stat().st_size:>9,d} байт".replace(",", " "))
    if not only or "SOURCES.md" in only:
        make_sources(OUT_DIR / "SOURCES.md")
        print(f"  ok  {'SOURCES.md':32s} {(OUT_DIR / 'SOURCES.md').stat().st_size:>9,d} байт".replace(",", " "))
    print(f"Готово: {OUT_DIR}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
