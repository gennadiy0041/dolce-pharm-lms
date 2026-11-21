"""Тесты преобразования DOCX / XLSX / PPTX в PDF (app.services.convert).

Файлы-образцы собираются на лету в tmp_path. Встроенный конвертер включается принудительно
(LMS_CONVERTER=builtin), чтобы тесты вели себя одинаково на машинах с LibreOffice и без него;
путь через LibreOffice проверяется отдельно на подставном «soffice».
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import stat
import sys
import time
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfReader

from app.services import convert
from app.services.convert import OFFICE_EXTENSIONS, ConversionResult, convert_office_to_pdf, find_soffice

KAZAKH = "Ұжымның қауіпсіздігі — ортақ жауапкершілік: ӘҒҚҢӨҰҮІ әғқңөұүі"


# ── вспомогательные функции ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _builtin_converter(monkeypatch):
    monkeypatch.setenv("LMS_CONVERTER", "builtin")
    monkeypatch.delenv("LMS_SOFFICE", raising=False)


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def page_texts(pdf: Path) -> list[str]:
    return [squash(page.extract_text() or "") for page in PdfReader(str(pdf)).pages]


def pdf_text(pdf: Path) -> str:
    return "".join(page_texts(pdf))


def has(text: str, needle: str) -> bool:
    return squash(needle) in text


def is_russian(message: str | None) -> bool:
    return bool(message) and re.search("[А-Яа-яЁё]{3,}", message) is not None


def leftovers(folder: Path) -> list[str]:
    """Временные файлы конвертера, забытые рядом с результатом."""
    return [p.name for p in folder.iterdir() if p.name.startswith(".") or p.suffix == ".tmp"]


def png_bytes(width: int = 320, height: int = 180, color=(40, 120, 200)) -> io.BytesIO:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "PNG")
    buf.seek(0)
    return buf


def text_fragments(pdf: Path, page_index: int) -> list[tuple[str, float, float, float]]:
    """Фрагменты текста страницы: (текст, x, y, кегль) в координатах страницы.

    pypdf при переходе на новую строку (оператор T*) сдвигает текст на «интервал × кегль» вместо
    интервала; строки одного абзаца (у них общая матрица cm) возвращаем на место делением на кегль.
    """
    raw: list[tuple[str, tuple, float, float, float]] = []

    def visit(text, cm, tm, _font, size):
        if text and text.strip():
            raw.append((text, tuple(cm), tm[4], tm[5], float(size)))

    PdfReader(str(pdf)).pages[page_index].extract_text(visitor_text=visit)
    found: list[tuple[str, float, float, float]] = []
    first_line: dict[tuple, float] = {}
    for text, cm, tx, ty, size in raw:
        top = first_line.setdefault(cm, ty)
        if size:
            ty = top - (top - ty) / size
        found.append((text, tx * cm[0] + ty * cm[2] + cm[4], tx * cm[1] + ty * cm[3] + cm[5], size))
    return found


# ── образцы файлов ───────────────────────────────────────────────────────────────

def make_docx(path: Path) -> None:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.shared import Cm

    doc = Document()
    doc.add_heading("СОП-ПР-012. Вход в чистые помещения", level=1)
    p = doc.add_paragraph("Абзац перед таблицей. ")
    p.add_run("Полужирный текст").bold = True
    p.add_run(", ")
    run = p.add_run("курсив с подчёркиванием")
    run.italic = True
    run.underline = True
    p.add_run(" и спецсимволы <70 % & аналоги>.")
    centered = doc.add_paragraph(KAZAKH)
    centered.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_heading("Термины", level=2)
    for text in ("Маркер первый", "Маркер второй"):
        doc.add_paragraph(text, style="List Bullet")
    for text in ("Шаг первый", "Шаг второй", "Шаг третий"):
        doc.add_paragraph(text, style="List Number")

    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, ("№", "Контрольная точка", "Критерий приёмки", "Ответственный")):
        cell.text = text
    for row in (("1", "Мытьё рук", "Не менее 30 секунд", "Оператор"),
                ("2", "Комбинезон", "Целый и чистый", "Оператор"),
                ("3", "Перчатки", "Поверх манжет", "Мастер смены")):
        for cell, text in zip(table.add_row().cells, row):
            cell.text = text
    total = table.add_row().cells
    total[0].merge(total[2]).text = "Итого контрольных точек"
    total[3].text = "три"

    doc.add_paragraph("Абзац после таблицы.")
    doc.add_picture(png_bytes(), width=Cm(8))
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    doc.add_heading("Ответственность", level=1)
    doc.add_paragraph("Текст на второй странице.")
    doc.save(str(path))


def make_xlsx(path: Path, rows: int = 300) -> list[str]:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    headers = ["№", "Код документа", "Наименование процедуры", "Подразделение", "Версия", "Дата утверждения",
               "Дата пересмотра", "Ответственный", "Статус", "Обучено, чел.", "Доля обученных", "Средний балл",
               "Обязательна", "Примечание"]
    book = Workbook()
    sheet = book.active
    sheet.title = "Реестр СОП"
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    sheet["A1"] = "Реестр стандартных операционных процедур — Өндіріс бөлімі"
    for col, text in enumerate(headers, 1):
        sheet.cell(row=2, column=col, value=text).font = Font(bold=True)
    for i in range(rows):
        row = i + 3
        sheet.cell(row=row, column=1, value=i + 1)
        sheet.cell(row=row, column=2, value=f"СОП-ПР-{i + 1:03d}")
        sheet.cell(row=row, column=3, value="Переодевание и вход в чистые помещения класса D")
        sheet.cell(row=row, column=4, value="Участок твёрдых лекарственных форм")
        sheet.cell(row=row, column=5, value=1 + i % 4)
        sheet.cell(row=row, column=6, value=dt.date(2026, 2, 1 + i % 28)).number_format = "DD.MM.YYYY"
        sheet.cell(row=row, column=7, value=dt.datetime(2026, 11, 1 + i % 28, 9, 30)).number_format = "DD.MM.YYYY HH:MM"
        sheet.cell(row=row, column=8, value="Нұрғалиев Ғ. Қ.")
        sheet.cell(row=row, column=9, value="Действует")
        sheet.cell(row=row, column=10, value=12 + i % 30)
        sheet.cell(row=row, column=11, value=0.125).number_format = "0.0%"
        sheet.cell(row=row, column=12, value=3.75).number_format = "0.00"
        sheet.cell(row=row, column=13, value=bool(i % 2))
        if i % 10 == 0:
            sheet.cell(row=row, column=14, value="Требуется актуализация ссылок")
    last = rows + 3
    sheet.cell(row=last, column=3, value="Итого обучено")
    sheet.cell(row=last, column=10, value=f"=SUM(J3:J{last - 1})")
    book.save(str(path))
    return headers


def make_pptx(path: Path) -> list[str]:
    """Шесть слайдов: титул, список, рисунок, таблица, группа фигур, длинный текст в фигуре + диаграмма."""
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches, Pt

    prs = Presentation()
    marks = []

    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "Пятиминутка по СОП"
    slide.placeholders[1].text = KAZAKH
    marks.append("Пятиминутка по СОП")

    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Что изменилось"
    body = slide.placeholders[1].text_frame
    body.text = "Мытьё рук не менее 30 секунд"
    for text, level in (("Перчатки поверх манжет", 0), ("проверка целостности", 1), ("Запись в журнале смены", 0)):
        para = body.add_paragraph()
        para.text = text
        para.level = level
    marks.append("Запись в журнале смены")

    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Схема шлюза"
    slide.shapes.add_picture(png_bytes(640, 360), Inches(1), Inches(1.8), width=Inches(5))
    marks.append("Схема шлюза")

    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Контрольные точки"
    table = slide.shapes.add_table(3, 3, Inches(0.5), Inches(1.8), Inches(9), Inches(2)).table
    for r, row in enumerate((("Точка", "Критерий", "Кто проверяет"),
                             ("Мытьё рук", "Не менее 30 с", "Оператор"),
                             ("Перчатки", "Без проколов", "Мастер смены"))):
        for c, text in enumerate(row):
            table.cell(r, c).text = text
    marks.append("Без проколов")

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    group = slide.shapes.add_group_shape()
    box = group.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(1), Inches(1), Inches(3), Inches(1.5))
    box.text_frame.text = "Обучено 96 %"
    oval = group.shapes.add_shape(MSO_SHAPE.OVAL, Inches(5), Inches(1), Inches(3), Inches(1.5))
    oval.fill.solid()
    oval.fill.fore_color.rgb = RGBColor(0x1A, 0x8F, 0x8A)
    oval.text_frame.text = "Два отклонения"
    slide._element.set("show", "0")       # скрытый слайд тоже должен попасть в PDF
    marks.append("Два отклонения")

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(1), Inches(1), Inches(4), Inches(1.6))
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
    box.text_frame.word_wrap = True
    box.text_frame.text = ("Ответственность за соблюдение процедуры несёт каждый сотрудник, входящий в чистую "
                           "зону. Контроль осуществляет мастер смены; выборочную проверку проводит специалист "
                           "отдела контроля качества не реже одного раза в неделю. Конец длинного текста.")
    box.text_frame.paragraphs[0].runs[0].font.size = Pt(18)
    data = CategoryChartData()
    data.categories = ["Август", "Сентябрь"]
    data.add_series("Отклонения", (5, 3))
    slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(5.5), Inches(1), Inches(4), Inches(3), data)
    marks.append("Конец длинного текста")

    prs.save(str(path))
    return marks


# ── интерфейс ───────────────────────────────────────────────────────────────────

def test_public_interface():
    assert OFFICE_EXTENSIONS == {"docx", "xlsx", "pptx"}
    result = ConversionResult(ok=True)
    assert (result.converter, result.pages, result.error, result.warnings) == (None, None, None, [])
    assert ConversionResult(ok=False).warnings is not result.warnings


# ── DOCX ────────────────────────────────────────────────────────────────────────

def test_docx_content_and_order(tmp_path):
    src, dst = tmp_path / "sop.docx", tmp_path / "out" / "sop.pdf"
    make_docx(src)
    result = convert_office_to_pdf(src, dst)
    assert result.ok, result.error
    assert result.converter == "builtin"
    assert result.error is None
    assert dst.is_file() and leftovers(dst.parent) == []
    pages = page_texts(dst)
    assert result.pages == len(pages) == 2          # явный разрыв страницы
    text = "".join(pages)
    for needle in ("СОП-ПР-012. Вход в чистые помещения", "Полужирный текст", "курсив с подчёркиванием",
                   "<70 % & аналоги>", KAZAKH, "Маркер первый", "Контрольная точка", "Критерий приёмки",
                   "Не менее 30 секунд", "Итого контрольных точек", "Текст на второй странице"):
        assert has(text, needle), needle
    # нумерованный список считает сам
    for needle in ("1.Шаг первый", "2.Шаг второй", "3.Шаг третий"):
        assert has(text, needle), needle
    # абзацы и таблица идут в том же порядке, что и в документе
    order = [text.index(squash(n)) for n in ("Абзац перед таблицей", "Мытьё рук", "Абзац после таблицы")]
    assert order == sorted(order)
    assert has(pages[0], "Стр. 1") and has(pages[1], "Стр. 2")
    assert has(pages[1], "Ответственность") and not has(pages[0], "Текст на второй странице")
    # рисунок встроен в PDF
    assert any(len(page.images) for page in PdfReader(str(dst)).pages)


def test_docx_headings_by_russian_style_name_are_larger(tmp_path):
    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE

    doc = Document()
    doc.styles.add_style("Заголовок 1", WD_STYLE_TYPE.PARAGRAPH)
    doc.add_paragraph("Раздел с русским стилем", style="Заголовок 1")
    doc.add_paragraph("Обычный абзац документа.")
    src, dst = tmp_path / "ru.docx", tmp_path / "ru.pdf"
    doc.save(str(src))
    result = convert_office_to_pdf(src, dst)
    assert result.ok, result.error
    sizes = {squash(text): size for text, _x, _y, size in text_fragments(dst, 0)}
    heading = next(size for text, size in sizes.items() if "Раздел" in text)
    body = next(size for text, size in sizes.items() if "Обычный" in text)
    assert heading >= body + 3


def test_docx_landscape_document_stays_landscape(tmp_path):
    from docx import Document
    from docx.enum.section import WD_ORIENT

    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = section.page_height, section.page_width
    doc.add_paragraph("Альбомный журнал смены")
    src, dst = tmp_path / "wide.docx", tmp_path / "wide.pdf"
    doc.save(str(src))
    assert convert_office_to_pdf(src, dst).ok
    box = PdfReader(str(dst)).pages[0].mediabox
    assert float(box.width) > float(box.height)


def test_docx_long_table_repeats_header_and_wide_cells_survive(tmp_path):
    from docx import Document

    doc = Document()
    table = doc.add_table(rows=1, cols=8)
    heads = ("ФИО", "Должность", "Подразделение", "СОП-012", "СОП-015", "СОП-021", "Дата", "Статус")
    for cell, text in zip(table.rows[0].cells, heads):
        cell.text = text
    for i in range(90):
        values = (f"Сотрудник {i + 1}", "Оператор таблетпресса", "Участок твёрдых форм", "да", "да", "нет",
                  "01.10.2026", "Требуется переобучение")
        for cell, text in zip(table.add_row().cells, values):
            cell.text = text
    src, dst = tmp_path / "matrix.docx", tmp_path / "matrix.pdf"
    doc.save(str(src))
    result = convert_office_to_pdf(src, dst)
    assert result.ok, result.error
    pages = page_texts(dst)
    assert len(pages) >= 2
    for page in pages:                       # шапка повторяется на каждой странице
        assert has(page, "Подразделение") and has(page, "Должность")
    text = "".join(pages)
    assert has(text, "Сотрудник 90") and text.count(squash("Требуется переобучение")) == 90


def test_docx_numbered_headings_and_tall_merged_cells(tmp_path):
    from docx import Document
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls

    doc = Document()
    numbering = doc.part.numbering_part.element
    numbering.insert(0, parse_xml(
        f'<w:abstractNum {nsdecls("w")} w:abstractNumId="90"><w:multiLevelType w:val="multilevel"/>'
        '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>'
        '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2."/></w:lvl>'
        '</w:abstractNum>'))
    numbering.append(parse_xml(f'<w:num {nsdecls("w")} w:numId="90"><w:abstractNumId w:val="90"/></w:num>'))
    for text, level in (("Общие положения", 0), ("Область применения", 1), ("Термины", 1), ("Ответственность", 0),
                        ("Мастер смены", 1)):
        heading = doc.add_heading(text, level + 1)
        heading._p.get_or_add_pPr().append(parse_xml(
            f'<w:numPr {nsdecls("w")}><w:ilvl w:val="{level}"/><w:numId w:val="90"/></w:numPr>'))

    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, ("Участок", "Операция", "Комментарий")):
        cell.text = text
    for i in range(70):
        cells = table.add_row().cells
        cells[1].text = f"Операция {i + 1}"
        cells[2].text = "без замечаний" if i != 4 else "Очень длинное пояснение к операции. " * 160
    table.cell(1, 0).merge(table.cell(70, 0)).text = "Участок таблетирования"     # объединение выше страницы
    src, dst = tmp_path / "hard.docx", tmp_path / "hard.pdf"
    doc.save(str(src))

    result = convert_office_to_pdf(str(src), str(dst))          # пути можно передавать и строками
    assert result.ok, result.error
    text = pdf_text(dst)
    for needle in ("1. Общие положения", "1.1. Область применения", "1.2. Термины", "2. Ответственность",
                   "2.1. Мастер смены", "Участок таблетирования", "Операция 70"):
        assert has(text, needle), needle
    # ячейка выше страницы разорвана между страницами, но не обрезана: все слова на месте
    assert text.count("Очень") == 160 and text.count("пояснение") == 160 and text.count("операции.") == 160
    assert result.pages >= 3


def test_docx_without_content_is_refused(tmp_path):
    from docx import Document

    src, dst = tmp_path / "empty.docx", tmp_path / "empty.pdf"
    Document().save(str(src))
    result = convert_office_to_pdf(src, dst)
    assert not result.ok and is_russian(result.error) and not dst.exists()


# ── XLSX ────────────────────────────────────────────────────────────────────────

def test_xlsx_wide_long_sheet(tmp_path):
    src, dst = tmp_path / "reestr.xlsx", tmp_path / "reestr.pdf"
    headers = make_xlsx(src, rows=300)
    result = convert_office_to_pdf(src, dst)
    assert result.ok, result.error
    assert result.converter == "builtin"
    pages = page_texts(dst)
    assert result.pages == len(pages) > 1
    text = "".join(pages)
    for header in headers:                               # все 14 заголовков на месте
        assert has(text, header), header
    assert not any("не найдены заголовки" in w for w in result.warnings)
    for page in pages:                                   # шапка повторяется на каждой странице
        assert has(page, "Код документа") and has(page, "Дата пересмотра")
    assert has(pages[0], "Реестр СОП")                   # имя листа — заголовок
    assert has(pages[0], "Реестр стандартных операционных процедур — Өндіріс бөлімі")
    assert has(text, "СОП-ПР-001") and has(text, "СОП-ПР-300")
    assert has(text, "01.02.2026") and has(text, "28.02.2026")        # даты ДД.ММ.ГГГГ
    assert has(text, "01.11.2026 09:30")                               # дата со временем
    assert not re.search(r"2026-\d\d-\d\d", text)
    assert has(text, "3,75") and "3.75" not in text                    # дробь с запятой
    assert has(text, "12,5 %")                                         # проценты
    assert has(text, "Да") and has(text, "Нет") and "True" not in text and "False" not in text
    assert "300.0" not in text                                         # целые без «.0»
    assert has(text, "Нұрғалиев Ғ. Қ.")
    assert has(text, "=SUM(J3:J302)")                                  # формула без сохранённого значения
    assert any("Формулы без сохранённых значений" in w for w in result.warnings)
    assert has(pages[-1], f"Стр. {len(pages)}")
    box = PdfReader(str(dst)).pages[0].mediabox
    assert float(box.width) > float(box.height)                        # альбомная A4


def test_xlsx_very_wide_sheet_is_split_into_column_groups(tmp_path):
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Матрица допусков"
    headers = ["Сотрудник"] + [f"Процедура номер {i:02d} допуск" for i in range(1, 30)]
    sheet.append(headers)
    for i in range(12):
        sheet.append([f"Работник {i + 1}"] + [("допущен" if (i + j) % 3 else "нет допуска") for j in range(29)])
    src, dst = tmp_path / "wide.xlsx", tmp_path / "wide.pdf"
    book.save(str(src))
    result = convert_office_to_pdf(src, dst)
    assert result.ok, result.error
    pages = page_texts(dst)
    assert len(pages) >= 2
    text = "".join(pages)
    for header in headers:
        assert has(text, header), header
    assert has(pages[0], "Часть 1 из") and has(pages[0], "столбцы B–")
    for page in pages:                                   # первый столбец повторяется в каждой части
        assert has(page, "Сотрудник") and has(page, "Работник 12")


def test_xlsx_formula_with_cached_value_and_merged_cells(tmp_path):
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Итоги"
    sheet.append(["Показатель", "План", "Факт"])
    sheet.append(["Обучено", 40, 38])
    sheet.append(["Экзамен", 40, 35])
    sheet.append(["Всего", "=SUM(B2:B3)", "=SUM(C2:C3)"])
    sheet.merge_cells("A6:C6")
    sheet["A6"] = "Объединённая строка с примечанием"
    src, dst = tmp_path / "totals.xlsx", tmp_path / "totals.pdf"
    book.save(str(src))
    # подставляем сохранённые значения формул так, как это делает Excel
    patched = tmp_path / "totals-cached.xlsx"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(patched, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                xml = data.decode("utf-8")
                xml, count = re.subn(r"<f>SUM\(B2:B3\)</f><v\s*/>|<f>SUM\(B2:B3\)</f><v></v>",
                                     "<f>SUM(B2:B3)</f><v>80</v>", xml)
                assert count == 1
                data = xml.encode("utf-8")
            zout.writestr(item, data)
    result = convert_office_to_pdf(patched, dst)
    assert result.ok, result.error
    text = pdf_text(dst)
    assert has(text, "Всего80")                    # сохранённое значение вместо формулы
    assert has(text, "=SUM(C2:C3)")                # у второй формулы значения нет — показан текст
    assert any("Формулы" in w for w in result.warnings)
    assert has(text, "Объединённая строка с примечанием")


def test_xlsx_two_row_header_repeats(tmp_path):
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Журнал"
    sheet.merge_cells("A1:A2")
    sheet.merge_cells("B1:C1")
    sheet["A1"] = "Сотрудник"
    sheet["B1"] = "Результаты обучения"
    sheet["B2"] = "Теория"
    sheet["C2"] = "Практика"
    for i in range(120):
        sheet.append([f"Работник {i + 1}", 80 + i % 20, "зачёт"])
    src, dst = tmp_path / "journal.xlsx", tmp_path / "journal.pdf"
    book.save(str(src))
    result = convert_office_to_pdf(src, dst)
    assert result.ok, result.error
    pages = page_texts(dst)
    assert len(pages) >= 2
    for page in pages:
        assert has(page, "Результаты обучения") and has(page, "Теория") and has(page, "Практика")
    assert has("".join(pages), "Работник 120")


def test_xlsx_tall_merge_and_huge_cell_do_not_break_layout(tmp_path):
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Смены"
    sheet.append(["Группа", "Сотрудник", "Балл", "Комментарий"])
    for i in range(100):
        sheet.append([None, f"Работник {i + 1}", 70 + i % 30, "ок"])
    sheet.merge_cells("A2:A101")                     # объединение на сто строк — выше страницы
    sheet["A2"] = "Смена А"
    sheet["D12"] = "Очень длинный комментарий. " * 250
    src, dst = tmp_path / "merge.xlsx", tmp_path / "merge.pdf"
    book.save(str(src))
    result = convert_office_to_pdf(src, dst)
    assert result.ok, result.error
    pages = page_texts(dst)
    assert len(pages) >= 3
    text = "".join(pages)
    assert has(text, "Работник 100")
    # ячейка выше страницы разорвана между страницами, но не обрезана: все слова на месте
    assert text.count("Очень") == 250 and text.count("длинный") == 250 and text.count("комментарий.") == 250
    assert text.count("СменаА") >= 5                                         # подпись повторяется по ходу таблицы
    assert has(pages[-1], "Смена А")


def test_xlsx_hidden_rows_and_columns_are_skipped(tmp_path):
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append(["Код", "Служебный столбец", "Название"])
    sheet.append(["А-1", "секрет-1", "Видимая строка"])
    sheet.append(["А-2", "секрет-2", "Скрытая строка"])
    sheet.column_dimensions["B"].hidden = True
    sheet.row_dimensions[3].hidden = True
    src, dst = tmp_path / "hidden.xlsx", tmp_path / "hidden.pdf"
    book.save(str(src))
    result = convert_office_to_pdf(src, dst)
    assert result.ok, result.error
    text = pdf_text(dst)
    assert has(text, "Видимая строка") and not has(text, "секрет") and not has(text, "Скрытая строка")
    assert any("скрытые строки и столбцы" in w for w in result.warnings)


def test_xlsx_empty_and_hidden_only_workbooks(tmp_path):
    from openpyxl import Workbook

    src, dst = tmp_path / "empty.xlsx", tmp_path / "empty.pdf"
    Workbook().save(str(src))
    result = convert_office_to_pdf(src, dst)
    assert not result.ok and result.pages is None and result.converter is None
    assert is_russian(result.error) and "пуст" in result.error
    assert not dst.exists() and leftovers(tmp_path) == []

    book = Workbook()
    book.active.title = "Видимый"
    hidden = book.create_sheet("Скрытый")
    hidden["A1"] = "данные только на скрытом листе"
    hidden.sheet_state = "hidden"
    src2 = tmp_path / "hidden.xlsx"
    book.save(str(src2))
    result = convert_office_to_pdf(src2, dst)
    assert not result.ok and is_russian(result.error) and "скрыт" in result.error


@pytest.mark.parametrize(
    ("value", "number_format", "expected"),
    [
        (5, "General", "5"),
        (5.0, "General", "5"),
        (3.14159, "General", "3,14159"),
        (0.1 + 0.2, "General", "0,3"),
        (1250000.5, "#,##0.00", "1 250 000,50"),
        (-42, "0", "-42"),
        (0.125, "0.0%", "12,5 %"),
        (1, "0%", "100 %"),
        (12500.5, '#,##0.00 "₸"', "12 500,50 ₸"),
        (True, "General", "Да"),
        (False, "General", "Нет"),
        (None, "General", ""),
        (dt.date(2026, 3, 5), "DD.MM.YYYY", "05.03.2026"),
        (dt.datetime(2026, 3, 5), "DD.MM.YYYY", "05.03.2026"),
        (dt.datetime(2026, 3, 5, 14, 7), "DD.MM.YYYY HH:MM", "05.03.2026 14:07"),
        (dt.time(8, 30), "HH:MM", "08:30"),
        ("текст", "General", "текст"),
    ],
)
def test_xlsx_cell_formatting(value, number_format, expected):
    assert convert._xl_text(value, number_format)[0] == expected


# ── PPTX ────────────────────────────────────────────────────────────────────────

def test_pptx_one_page_per_slide(tmp_path):
    from pptx import Presentation

    src, dst = tmp_path / "deck.pptx", tmp_path / "deck.pdf"
    marks = make_pptx(src)
    slides = len(Presentation(str(src)).slides)
    assert slides == 6
    result = convert_office_to_pdf(src, dst)
    assert result.ok, result.error
    assert result.converter == "builtin"
    pages = page_texts(dst)
    assert result.pages == len(pages) == slides          # скрытый слайд тоже на месте
    for index, mark in enumerate(marks):                 # текст каждого слайда — на своей странице
        assert has(pages[index], mark), (index + 1, mark)
        for other, page in enumerate(pages):
            if other != index:
                assert not has(page, mark)
    assert has(pages[0], KAZAKH)
    assert has(pages[1], "проверка целостности") and has(pages[1], "•")
    for needle in ("Точка", "Критерий", "Кто проверяет", "Мытьё рук", "Мастер смены"):
        assert has(pages[3], needle)
    assert has(pages[4], "Обучено 96 %")                 # фигуры внутри группы
    assert any(w == "Слайд 6: диаграмма не перенесена." for w in result.warnings)
    reader = PdfReader(str(dst))
    assert len(reader.pages[2].images) == 1              # рисунок на третьем слайде
    for page in reader.pages:                            # страница = слайд 10 × 7,5 дюйма
        assert float(page.mediabox.width) == pytest.approx(720, abs=0.5)
        assert float(page.mediabox.height) == pytest.approx(540, abs=0.5)


def test_pptx_long_text_is_shrunk_to_stay_inside_its_shape(tmp_path):
    src, dst = tmp_path / "deck.pptx", tmp_path / "deck.pdf"
    make_pptx(src)
    assert convert_office_to_pdf(src, dst).ok
    # прямоугольник: слева 1", сверху 1", 4" × 1,6"; страница 540 пунктов высотой
    left, right, top, bottom = 72.0, 72.0 * 5, 540 - 72.0, 540 - 72.0 * 2.6
    inside = [(text, x, y, size) for text, x, y, size in text_fragments(dst, 5) if x < right]
    joined = squash("".join(fragment[0] for fragment in inside))
    assert squash("Ответственность за соблюдение") in joined and squash("Конец длинного текста.") in joined
    assert len(inside) >= 4                               # текст перенесён по словам на несколько строк
    for _text, x, y, size in inside:
        assert size < 18                                  # шрифт уменьшен, иначе текст не поместился бы
        assert size >= 9                                  # но не больше чем вдвое
        assert left <= x <= right
        assert bottom <= y <= top, "текст вышел за пределы фигуры"


def test_pptx_assorted_shapes_render_without_losses(tmp_path):
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
    from pptx.util import Inches, Pt

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    names = {MSO_SHAPE.RECTANGLE: "Прямоугольник", MSO_SHAPE.ROUNDED_RECTANGLE: "Скруглённый", MSO_SHAPE.OVAL: "Овал",
             MSO_SHAPE.DIAMOND: "Ромб", MSO_SHAPE.CHEVRON: "Шеврон", MSO_SHAPE.RIGHT_ARROW: "Стрелка",
             MSO_SHAPE.HEXAGON: "Шестиугольник", MSO_SHAPE.ISOSCELES_TRIANGLE: "Треугольник"}
    for index, (kind, name) in enumerate(names.items()):
        shape = slide.shapes.add_shape(kind, Inches(0.3 + (index % 4) * 2.4), Inches(0.3 + (index // 4) * 1.9),
                                       Inches(2.2), Inches(1.6))
        shape.text_frame.text = name
        if index == 3:
            shape.rotation = 20
    line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(0.5), Inches(4.5), Inches(9), Inches(5.5))
    line.line.width = Pt(2)
    builder = slide.shapes.build_freeform(100, 480, scale=Inches(1) / 100)
    builder.add_line_segments([(250, 480), (300, 600), (100, 650)], close=True)
    freeform = builder.convert_to_shape()
    freeform.fill.solid()
    freeform.fill.fore_color.rgb = RGBColor(0x8E, 0x44, 0xAD)
    box = slide.shapes.add_textbox(Inches(4), Inches(5.8), Inches(1), Inches(0.4))       # без переноса строк
    box.text_frame.text = "Надпись длиннее своей рамки, но целиком на слайде"
    table = slide.shapes.add_table(3, 3, Inches(4), Inches(6.3), Inches(5.5), Inches(1)).table
    table.cell(0, 0).merge(table.cell(0, 2))
    table.cell(0, 0).text = "Объединённая шапка"
    table.cell(1, 0).merge(table.cell(2, 0))
    table.cell(1, 0).text = "Объединено по вертикали"
    table.cell(2, 2).text = "Последняя ячейка"
    src, dst = tmp_path / "shapes.pptx", tmp_path / "shapes.pdf"
    prs.save(str(src))

    result = convert_office_to_pdf(src, dst)
    assert result.ok, result.error
    assert result.pages == 1 and result.warnings == []
    text = pdf_text(dst)
    for needle in list(names.values()) + ["Надпись длиннее своей рамки, но целиком на слайде", "Объединённая шапка",
                                          "Объединено по вертикали", "Последняя ячейка"]:
        assert has(text, needle), needle
    for fragment, x, y, _size in text_fragments(dst, 0):         # текст не уходит за края страницы
        assert 0 <= x <= 720 and 0 <= y <= 540, fragment


def test_pptx_metafile_picture_becomes_placeholder_with_warning(tmp_path):
    import struct

    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Слайд с векторным рисунком"
    picture = slide.shapes.add_picture(png_bytes(), Inches(1), Inches(2), width=Inches(4))
    # подменяем содержимое рисунка заголовком EMF: такие файлы PowerPoint хранит как есть
    emf = struct.pack("<II", 1, 88) + b"\0" * 32 + b" EMF" + struct.pack("<IIIHH", 0x10000, 88, 1, 1, 0) + b"\0" * 28
    picture.part.related_part(picture._element.blipFill.blip.rEmbed)._blob = emf
    src, dst = tmp_path / "vector.pptx", tmp_path / "vector.pdf"
    prs.save(str(src))

    result = convert_office_to_pdf(src, dst)
    assert result.ok, result.error
    assert result.pages == 1
    assert any(w.startswith("Слайд 1: рисунок в формате EMF/WMF") for w in result.warnings)
    text = pdf_text(dst)
    assert has(text, "Слайд с векторным рисунком") and has(text, "Рисунок в формате, который нельзя показать")


def test_paths_with_cyrillic_and_spaces(tmp_path):
    folder = tmp_path / "Учебные материалы (новые)" / "Смена № 2"
    folder.mkdir(parents=True)
    src = folder / "Журнал приёма-передачи смены — образец.xlsx"
    dst = folder / "PDF копии" / "Журнал приёма-передачи смены — образец.pdf"
    make_xlsx(src, rows=5)
    result = convert_office_to_pdf(src, dst)
    assert result.ok, result.error
    assert dst.is_file() and leftovers(dst.parent) == []
    assert has(pdf_text(dst), "СОП-ПР-005")


def test_pptx_without_slides_is_refused(tmp_path):
    from pptx import Presentation

    src, dst = tmp_path / "empty.pptx", tmp_path / "empty.pdf"
    Presentation().save(str(src))
    result = convert_office_to_pdf(src, dst)
    assert not result.ok and is_russian(result.error) and not dst.exists()


# ── отказы ──────────────────────────────────────────────────────────────────────

def test_garbage_named_docx(tmp_path):
    src, dst = tmp_path / "broken.docx", tmp_path / "broken.pdf"
    src.write_bytes(os.urandom(4096))
    result = convert_office_to_pdf(src, dst)
    assert result.ok is False
    assert result.converter is None and result.pages is None
    assert is_russian(result.error) and "DOCX" in result.error
    assert not dst.exists() and leftovers(tmp_path) == []


@pytest.mark.parametrize("name", ["letter.doc", "table.xls", "slides.ppt", "notes.txt", "scan.pdf", "noext"])
def test_unsupported_extension(tmp_path, name):
    src, dst = tmp_path / name, tmp_path / "out.pdf"
    src.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 512)
    result = convert_office_to_pdf(src, dst)
    assert not result.ok and not dst.exists()
    assert "не поддерживается" in result.error and "DOCX, XLSX или PPTX" in result.error
    if "." in name:
        assert f"Формат .{name.rsplit('.', 1)[1]} " in result.error


def test_explicit_extension_overrides_file_name(tmp_path):
    stored = tmp_path / "upload-3f9a.bin"
    make_docx(tmp_path / "source.docx")
    stored.write_bytes((tmp_path / "source.docx").read_bytes())
    for ext in ("docx", ".DOCX"):
        dst = tmp_path / f"copy-{ext.strip('.')}.pdf"
        result = convert_office_to_pdf(stored, dst, ext=ext)
        assert result.ok, result.error
        assert dst.is_file()
    refused = convert_office_to_pdf(tmp_path / "source.docx", tmp_path / "x.pdf", ext="doc")
    assert not refused.ok and "Формат .doc не поддерживается" in refused.error


def test_old_binary_and_password_protected_files(tmp_path):
    dst = tmp_path / "out.pdf"
    old = tmp_path / "renamed.docx"
    old.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 2048)
    result = convert_office_to_pdf(old, dst)
    assert not result.ok and "старом формате" in result.error and not dst.exists()

    locked = tmp_path / "locked.xlsx"
    locked.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600
                       + "EncryptedPackage".encode("utf-16-le") + b"\0" * 600)
    result = convert_office_to_pdf(locked, dst)
    assert not result.ok and "паролем" in result.error and not dst.exists()


def test_wrong_content_empty_and_missing_files(tmp_path):
    dst = tmp_path / "out.pdf"
    book = tmp_path / "book.xlsx"
    make_xlsx(book, rows=3)
    disguised = tmp_path / "disguised.docx"
    disguised.write_bytes(book.read_bytes())
    result = convert_office_to_pdf(disguised, dst)
    assert not result.ok and "XLSX" in result.error and is_russian(result.error)

    empty = tmp_path / "zero.pptx"
    empty.write_bytes(b"")
    result = convert_office_to_pdf(empty, dst)
    assert not result.ok and "пуст" in result.error

    result = convert_office_to_pdf(tmp_path / "missing.docx", dst)
    assert not result.ok and is_russian(result.error)

    # архив с правильной структурой, но испорченным содержимым
    broken = tmp_path / "broken-inside.docx"
    with zipfile.ZipFile(broken, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document>не xml")
    result = convert_office_to_pdf(broken, dst)
    assert not result.ok and is_russian(result.error)
    assert not dst.exists() and leftovers(tmp_path) == []


def test_failure_removes_stale_copy(tmp_path):
    src, dst = tmp_path / "sop.docx", tmp_path / "sop.pdf"
    make_docx(src)
    assert convert_office_to_pdf(src, dst).ok and dst.is_file()
    src.write_bytes(b"this is not a document any more")
    result = convert_office_to_pdf(src, dst)
    assert not result.ok
    assert not dst.exists(), "устаревшая PDF-копия не должна оставаться после неудачной конвертации"


def test_success_overwrites_previous_copy(tmp_path):
    src, dst = tmp_path / "sop.docx", tmp_path / "sop.pdf"
    make_docx(src)
    dst.write_bytes(b"old")
    result = convert_office_to_pdf(src, dst)
    assert result.ok and dst.read_bytes().startswith(b"%PDF") and leftovers(tmp_path) == []


# ── LibreOffice ─────────────────────────────────────────────────────────────────

def test_find_soffice_ignores_missing_override(tmp_path, monkeypatch):
    bogus = tmp_path / "нет такого" / "soffice.exe"
    monkeypatch.setenv("LMS_SOFFICE", str(bogus))
    found = find_soffice()                       # не падает
    assert found is None or (Path(found).is_file() and Path(found) != bogus)


def test_find_soffice_uses_override(tmp_path, monkeypatch):
    fake = tmp_path / "soffice.exe"
    fake.write_bytes(b"")
    monkeypatch.setenv("LMS_SOFFICE", str(fake))
    assert find_soffice() == fake


FAKE_SOFFICE = r'''
import json, os, sys, time
args = sys.argv[1:]
with open(os.environ["LMS_TEST_ARGV"], "w", encoding="utf-8") as fh:
    json.dump(args, fh)
mode = os.environ.get("LMS_TEST_MODE", "ok")
if mode == "hang":
    time.sleep(60)
if mode == "fail":
    sys.exit(3)
outdir = args[args.index("--outdir") + 1]
name = os.path.splitext(os.path.basename(args[-1]))[0] + ".pdf"
from reportlab.pdfgen import canvas
pdf = canvas.Canvas(os.path.join(outdir, name))
for number in range(int(os.environ.get("LMS_TEST_PAGES", "2"))):
    pdf.drawString(72, 720, "made by fake soffice, page %d" % (number + 1))
    pdf.showPage()
pdf.save()
'''


@pytest.fixture
def fake_soffice(tmp_path, monkeypatch):
    """Подставной LibreOffice: скрипт, который ведёт себя как `soffice --convert-to pdf`."""
    tools = tmp_path / "lo"
    tools.mkdir()
    script = tools / "fake_soffice.py"
    script.write_text(FAKE_SOFFICE, encoding="utf-8")
    if os.name == "nt":
        launcher = tools / "soffice.cmd"
        launcher.write_text('@"%LMS_TEST_PYTHON%" "%LMS_TEST_SCRIPT%" %*\r\n', encoding="ascii")
    else:
        launcher = tools / "soffice"
        launcher.write_text('#!/bin/sh\nexec "$LMS_TEST_PYTHON" "$LMS_TEST_SCRIPT" "$@"\n', encoding="ascii")
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    argv_file = tools / "argv.json"
    monkeypatch.setenv("LMS_TEST_PYTHON", sys.executable)
    monkeypatch.setenv("LMS_TEST_SCRIPT", str(script))
    monkeypatch.setenv("LMS_TEST_ARGV", str(argv_file))
    monkeypatch.setenv("LMS_SOFFICE", str(launcher))
    monkeypatch.delenv("LMS_CONVERTER", raising=False)
    return argv_file


def test_libreoffice_is_preferred_when_available(tmp_path, fake_soffice, monkeypatch):
    monkeypatch.setenv("LMS_TEST_MODE", "ok")
    src, dst = tmp_path / "Мой документ с пробелами.docx", tmp_path / "copy.pdf"
    make_docx(src)
    result = convert_office_to_pdf(src, dst)
    assert result.ok, result.error
    assert result.converter == "libreoffice" and result.pages == 2 and result.warnings == []
    assert has(pdf_text(dst), "made by fake soffice")
    assert leftovers(tmp_path) == []
    args = json.loads(fake_soffice.read_text(encoding="utf-8"))
    assert "--headless" in args
    assert args[args.index("--convert-to") + 1] == "pdf"
    profile = next(a for a in args if a.startswith("-env:UserInstallation="))
    assert profile.startswith("-env:UserInstallation=file:///")
    assert args[-1].endswith(".docx")
    assert not Path(args[args.index("--outdir") + 1]).exists()      # временная папка убрана


def test_builtin_is_forced_by_environment(tmp_path, fake_soffice, monkeypatch):
    monkeypatch.setenv("LMS_CONVERTER", "builtin")
    src, dst = tmp_path / "sop.docx", tmp_path / "copy.pdf"
    make_docx(src)
    result = convert_office_to_pdf(src, dst)
    assert result.ok and result.converter == "builtin"
    assert not fake_soffice.exists()             # LibreOffice даже не запускался


def test_libreoffice_failure_falls_back_to_builtin(tmp_path, fake_soffice, monkeypatch):
    monkeypatch.setenv("LMS_TEST_MODE", "fail")
    src, dst = tmp_path / "sop.docx", tmp_path / "copy.pdf"
    make_docx(src)
    result = convert_office_to_pdf(src, dst)
    assert result.ok, result.error
    assert result.converter == "builtin" and result.pages == 2
    assert any("LibreOffice" in w and "встроенный конвертер" in w for w in result.warnings)
    assert has(pdf_text(dst), "Вход в чистые помещения")


def test_libreoffice_timeout_falls_back_to_builtin(tmp_path, fake_soffice, monkeypatch):
    monkeypatch.setenv("LMS_TEST_MODE", "hang")
    monkeypatch.setattr(convert, "LIBREOFFICE_TIMEOUT", 2)
    src, dst = tmp_path / "sop.docx", tmp_path / "copy.pdf"
    make_docx(src)
    started = time.monotonic()
    result = convert_office_to_pdf(src, dst)
    assert time.monotonic() - started < 40
    assert result.ok, result.error
    assert result.converter == "builtin"
    assert any("LibreOffice" in w and "не ответил" in w for w in result.warnings)


def test_libreoffice_result_with_missing_slides_is_rejected(tmp_path, fake_soffice, monkeypatch):
    monkeypatch.setenv("LMS_TEST_MODE", "ok")
    monkeypatch.setenv("LMS_TEST_PAGES", "4")                      # слайдов шесть, страниц четыре
    src, dst = tmp_path / "deck.pptx", tmp_path / "deck.pdf"
    make_pptx(src)
    result = convert_office_to_pdf(src, dst)
    assert result.ok, result.error
    assert result.converter == "builtin" and result.pages == 6
    assert any("LibreOffice" in w and "Не все слайды" in w for w in result.warnings)


def test_libreoffice_result_for_xlsx_is_checked_for_headers(tmp_path, fake_soffice, monkeypatch):
    monkeypatch.setenv("LMS_TEST_MODE", "ok")
    src, dst = tmp_path / "book.xlsx", tmp_path / "book.pdf"
    make_xlsx(src, rows=5)
    result = convert_office_to_pdf(src, dst)
    assert result.ok and result.converter == "libreoffice"
    warning = next(w for w in result.warnings if "не найдены заголовки" in w)
    assert "«Код документа»" in warning


# ── раскладка столбцов ──────────────────────────────────────────────────────────

def test_column_width_helpers_keep_totals_and_minimums():
    soft = [30.0, 60.0, 45.0, 80.0]
    pref = [30.0, 400.0, 45.0, 300.0]
    typical = [10.0, 380.0, 30.0, 150.0]
    widths, depth = convert._distribute(soft, pref, typical, 7.5, 500.0)
    assert sum(widths) == pytest.approx(500.0, abs=0.01) and depth > 1
    assert all(w >= s - 1e-6 for w, s in zip(widths, soft))
    assert widths[1] > widths[3] > widths[2]                 # длинному тексту — больше места
    roomy, depth = convert._distribute(soft, pref, typical, 7.5, 2000.0)
    assert roomy == pytest.approx(pref) and depth == 1.0     # места хватает — всё в одну строку
    squeezed = convert._squeeze([100.0, 100.0, 40.0], [40.0, 90.0, 40.0], 200.0)
    assert sum(squeezed) == pytest.approx(200.0) and squeezed[1] == 90.0 and squeezed[2] == 40.0
