"""Аттестационные документы: именной сертификат и сводный протокол (PDF с кириллицей)."""
from __future__ import annotations

import io
from datetime import datetime
from typing import Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .. import config
from .pdf_fonts import SANS, SANS_BOLD, SERIF, SERIF_BOLD, register_fonts

# Фирменные цвета Dolce-Pharm: фиолетовый с логотипа и сайта компании.
BRAND = colors.HexColor("#4F007D")
BRAND_DARK = colors.HexColor("#36005A")
ACCENT = colors.HexColor("#B79AD6")
INK = colors.HexColor("#1C1326")
MUTED = colors.HexColor("#5F566B")
PAPER = colors.HexColor("#FDFCFE")
LINE = colors.HexColor("#DDD5E8")
ROW_ALT = colors.HexColor("#F7F3FB")

LOGO_PATH = config.ASSETS_DIR / "brand" / "dolce-pharm.png"
LOGO_RATIO = 134 / 398          # высота к ширине файла логотипа


def _logo_available() -> bool:
    """Документ строится и без файла логотипа: тогда остаётся только текстовая шапка."""
    return LOGO_PATH.is_file()


def fmt_dt(dt: Optional[datetime], with_time: bool = True) -> str:
    if not dt:
        return "—"
    local = dt.astimezone(config.LOCAL_TZ)
    return local.strftime("%d.%m.%Y %H:%M") if with_time else local.strftime("%d.%m.%Y")


def fmt_num(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return ("%.1f" % x).rstrip("0").rstrip(".").replace(".", ",")


def _fit_text(c: canvas.Canvas, text: str, font: str, size: float, max_width: float, min_size: float = 12) -> float:
    while size > min_size and c.stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def _wrap(c: canvas.Canvas, text: str, font: str, size: float, max_width: float) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if c.stringWidth(trial, font, size) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def build_certificate_pdf(*, number: str, full_name: str, personnel_no: str, department: str, course_title: str,
                          sop_code: str, revision: int, attempt_date: datetime, issued_at: datetime,
                          score: float, max_score: float, percent: float, pass_percent: int) -> bytes:
    register_fonts()
    buf = io.BytesIO()
    w, h = landscape(A4)
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setTitle("Сертификат %s" % number)
    c.setAuthor(config.ORG_NAME)

    c.setFillColor(PAPER)
    c.rect(0, 0, w, h, stroke=0, fill=1)
    # рамка
    c.setStrokeColor(BRAND)
    c.setLineWidth(2.2)
    c.rect(12 * mm, 12 * mm, w - 24 * mm, h - 24 * mm, stroke=1, fill=0)
    c.setStrokeColor(ACCENT)
    c.setLineWidth(0.8)
    c.rect(15 * mm, 15 * mm, w - 30 * mm, h - 30 * mm, stroke=1, fill=0)
    # цветная полоса слева
    c.setFillColor(BRAND)
    c.rect(15 * mm, 15 * mm, 9 * mm, h - 30 * mm, stroke=0, fill=1)

    cx = w / 2 + 5 * mm
    width = w - 80 * mm
    logo = _logo_available()
    if logo:
        # логотип компании над шапкой; строки ниже сдвинуты и немного сжаты, чтобы название курса
        # в три строки по-прежнему помещалось над нижним блоком реквизитов
        logo_w = 50 * mm
        logo_h = logo_w * LOGO_RATIO
        c.drawImage(ImageReader(str(LOGO_PATH)), cx - logo_w / 2, h - 21 * mm - logo_h, width=logo_w, height=logo_h, mask="auto")
        y = h - 44 * mm
    else:
        y = h - 34 * mm
    c.setFillColor(BRAND_DARK)
    c.setFont(SANS_BOLD, 12 if logo else 13)
    c.drawCentredString(cx, y, config.ORG_NAME.upper() + " · ОТДЕЛ ОБУЧЕНИЯ")
    y -= (16 if logo else 19) * mm
    c.setFillColor(INK)
    c.setFont(SERIF_BOLD, 38)
    c.drawCentredString(cx, y, "СЕРТИФИКАТ")
    y -= 9 * mm
    c.setFont(SANS, 12.5)
    c.setFillColor(MUTED)
    c.drawCentredString(cx, y, "об успешной аттестации № %s" % number)

    y -= (14 if logo else 17) * mm
    c.setFont(SANS, 13)
    c.drawCentredString(cx, y, "Настоящим подтверждается, что")
    y -= (12 if logo else 13) * mm
    c.setFillColor(BRAND_DARK)
    size = _fit_text(c, full_name, SERIF_BOLD, 30, width, 18)
    c.setFont(SERIF_BOLD, size)
    c.drawCentredString(cx, y, full_name)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.8)
    c.line(cx - width / 2 + 20 * mm, y - 4 * mm, cx + width / 2 - 20 * mm, y - 4 * mm)
    y -= 10 * mm
    c.setFillColor(MUTED)
    c.setFont(SANS, 11.5)
    sub = "табельный № %s" % personnel_no + (" · %s" % department if department else "")
    c.drawCentredString(cx, y, sub)

    y -= 12 * mm
    c.setFillColor(INK)
    c.setFont(SANS, 13)
    c.drawCentredString(cx, y, "изучил(а) материал и прошёл(а) проверку знаний по программе")
    y -= 10 * mm
    title = "«%s»" % course_title
    lines = _wrap(c, title, SANS_BOLD, 17, width)[:3]
    c.setFont(SANS_BOLD, 17)
    for line in lines:
        c.drawCentredString(cx, y, line)
        y -= 8 * mm
    c.setFont(SANS, 12.5)
    c.setFillColor(BRAND_DARK)
    code = ("%s, " % sop_code if sop_code else "") + "редакция %d" % revision
    c.drawCentredString(cx, y, code)

    # нижний блок реквизитов
    base = 37 * mm
    cols = [
        ("Дата аттестации", fmt_dt(attempt_date, with_time=False)),
        ("Результат", "%s из %s баллов (%s %%)" % (fmt_num(score), fmt_num(max_score), fmt_num(percent))),
        ("Проходной порог", "%d %%" % pass_percent),
        ("Итог", "Аттестован"),
    ]
    left = 36 * mm
    # Колонки разной ширины: «5 из 7 баллов (71,4 %)» длиннее даты и не должно наезжать на соседний столбец.
    offsets = [0, 50 * mm, 125 * mm, 180 * mm]
    for (label, value), dx in zip(cols, offsets):
        x = left + dx
        c.setFont(SANS, 9.5)
        c.setFillColor(MUTED)
        c.drawString(x, base + 8 * mm, label.upper())
        c.setFont(SANS_BOLD, 13)
        c.setFillColor(INK)
        c.drawString(x, base + 1.5 * mm, value)
    c.setStrokeColor(LINE)
    c.line(left, base + 14 * mm, w - 26 * mm, base + 14 * mm)

    # подпись — отдельной строкой над служебной пометкой: раньше они стояли на одной линии и пересекались
    c.setFont(SANS, 10)
    c.setFillColor(INK)
    c.drawRightString(w - 26 * mm, 28 * mm, "Начальник отдела обучения ____________________")
    c.setFont(SANS, 8.5)
    c.setFillColor(MUTED)
    c.drawString(left, 20.5 * mm, "Документ сформирован автоматически %s (%s). %s" % (
        fmt_dt(issued_at), config.TZ_LABEL, config.SAMPLE_NOTICE))
    c.showPage()
    c.save()
    return buf.getvalue()


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text or ""), style)


def build_protocol_pdf(*, course_title: str, sop_code: str, revisions: list[int], date_from: Optional[datetime],
                       date_to: Optional[datetime], department: Optional[str], rows: list[dict], pending: list[dict],
                       generated_at: datetime, generated_by: str, number: str) -> bytes:
    """rows / pending — те же словари, из которых строится CSV и экранная таблица (одна выборка)."""
    register_fonts()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=16 * mm, title="Протокол аттестации %s" % number,
                            author=config.ORG_NAME)
    base = ParagraphStyle("base", fontName=SANS, fontSize=9.5, leading=12.5, textColor=INK)
    cell = ParagraphStyle("cell", parent=base, fontSize=9, leading=11.5)
    cell_b = ParagraphStyle("cellb", parent=cell, fontName=SANS_BOLD)
    head = ParagraphStyle("head", parent=cell, fontName=SANS_BOLD, textColor=colors.white, alignment=TA_CENTER)
    h1 = ParagraphStyle("h1", parent=base, fontName=SANS_BOLD, fontSize=16, leading=20, alignment=TA_CENTER, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=base, fontName=SANS_BOLD, fontSize=11.5, leading=15, spaceBefore=10, spaceAfter=5)
    small = ParagraphStyle("small", parent=base, fontSize=8.5, leading=11, textColor=MUTED)
    center = ParagraphStyle("center", parent=base, alignment=TA_CENTER)
    meta = ParagraphStyle("meta", parent=base, fontSize=10, leading=14, alignment=TA_LEFT)

    story = []
    if _logo_available():
        logo_w = 40 * mm
        mark = Image(str(LOGO_PATH), width=logo_w, height=logo_w * LOGO_RATIO, mask="auto")
        mark.hAlign = "CENTER"
        story.append(mark)
        story.append(Spacer(1, 1.5 * mm))
    story.append(_p("%s · Отдел обучения" % config.ORG_NAME, ParagraphStyle("org", parent=center, fontName=SANS_BOLD, textColor=BRAND_DARK)))
    story.append(_p("СВОДНЫЙ ПРОТОКОЛ АТТЕСТАЦИИ № %s" % number, h1))
    story.append(_p(config.SAMPLE_NOTICE, ParagraphStyle("sn", parent=small, alignment=TA_CENTER)))
    story.append(Spacer(1, 4 * mm))

    period = "за всё время"
    if date_from and date_to:
        a, b = fmt_dt(date_from, False), fmt_dt(date_to, False)
        period = a if a == b else "%s — %s" % (a, b)
    elif date_from:
        period = "с %s" % fmt_dt(date_from, False)
    elif date_to:
        period = "по %s" % fmt_dt(date_to, False)
    rev = ", ".join(str(r) for r in sorted(set(revisions))) or "—"
    meta_rows = [
        ["Программа:", "«%s»" % course_title + (" (%s)" % sop_code if sop_code else "")],
        ["Редакции в протоколе:", rev],
        ["Дата проверки знаний:", period + " (%s)" % config.TZ_LABEL],
        ["Подразделение:", department or "все подразделения"],
    ]
    t = Table([[_p(a, ParagraphStyle("ml", parent=meta, textColor=MUTED)), _p(b, ParagraphStyle("mv", parent=meta, fontName=SANS_BOLD))]
               for a, b in meta_rows], colWidths=[48 * mm, 215 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                           ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(t)

    widths = [9 * mm, 62 * mm, 20 * mm, 52 * mm, 30 * mm, 15 * mm, 24 * mm, 17 * mm, 38 * mm]
    header = ["№", "ФИО", "Таб. №", "Подразделение", "Дата проверки", "Ред.", "Баллы", "%", "Итог"]

    def table_for(items: list[dict], result_text) -> Table:
        data = [[_p(x, head) for x in header]]
        for i, r in enumerate(items, 1):
            data.append([
                _p(str(i), ParagraphStyle("n", parent=cell, alignment=TA_CENTER)),
                _p(r["full_name"], cell_b), _p(r["personnel_no"], cell), _p(r["department"], cell),
                _p(fmt_dt(r["submitted_at"]), cell),
                _p(str(r["revision"]), ParagraphStyle("r", parent=cell, alignment=TA_CENTER)),
                _p("%s из %s" % (fmt_num(r["score"]), fmt_num(r["max_score"])), cell),
                _p(fmt_num(r["percent"]) if r["percent"] is not None else "—", cell),
                _p(result_text(r), cell_b),
            ])
        tbl = Table(data, colWidths=widths, repeatRows=1)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), BRAND),
            ("GRID", (0, 0), (-1, -1), 0.5, LINE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
        ]
        tbl.setStyle(TableStyle(style))
        return tbl

    passed = [r for r in rows if r["status"] == "passed"]
    failed = [r for r in rows if r["status"] == "failed"]
    story.append(_p("Результаты аттестации", h2))
    if rows:
        story.append(table_for(rows, lambda r: "Аттестован" if r["status"] == "passed" else "Не аттестован"))
    else:
        story.append(_p("За выбранный период завершённых проверок нет.", base))
    story.append(Spacer(1, 3 * mm))
    story.append(_p("Всего проверено: %d. Аттестовано: %d. Не аттестовано: %d." % (len(rows), len(passed), len(failed)),
                    ParagraphStyle("sum", parent=base, fontName=SANS_BOLD)))

    story.append(_p("Незавершённые проверки (ответы ожидают проверки методистом)", h2))
    if pending:
        story.append(table_for(pending, lambda r: "На проверке"))
        story.append(_p("Баллы по этим строкам предварительные: открытые ответы ещё не оценены. "
                        "В итог аттестации строки не входят.", small))
    else:
        story.append(_p("Нет.", base))

    story.append(Spacer(1, 9 * mm))
    sign = Table([
        [_p("Начальник отдела обучения", base), _p("_____________________ / _____________________", base)],
        [_p("Члены комиссии", base), _p("_____________________ / _____________________", base)],
    ], colWidths=[70 * mm, 120 * mm])
    sign.setStyle(TableStyle([("BOTTOMPADDING", (0, 0), (-1, -1), 7), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(sign)

    footer = "Протокол № %s сформирован %s (%s), %s. Источник — попытки сотрудников в LMS %s." % (
        number, fmt_dt(generated_at), config.TZ_LABEL, generated_by, config.ORG_NAME)

    def on_page(cnv: canvas.Canvas, _doc) -> None:
        cnv.saveState()
        cnv.setFont(SANS, 8)
        cnv.setFillColor(MUTED)
        cnv.drawString(15 * mm, 9 * mm, footer[:170])
        cnv.drawRightString(landscape(A4)[0] - 15 * mm, 9 * mm, "Стр. %d" % cnv.getPageNumber())
        cnv.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()
