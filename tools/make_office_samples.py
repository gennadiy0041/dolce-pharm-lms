"""Файлы Word и PowerPoint с таблицами и настоящими диаграммами Office — для проверки PDF-копий и
офлайн-просмотра. Книгу Excel делает сам Excel: tools/make_office_samples.ps1.

Диаграммы здесь — штатные объекты Office (DrawingML chart с кэшем значений и встроенной книгой данных),
те же, что создаёт «Вставка → Диаграмма»: Word и PowerPoint открывают и рисуют их как свои.

    python tools/make_office_samples.py [папка]
"""
from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import parse_xml
from docx.shared import Pt, RGBColor
from lxml import etree
from pptx import Presentation
from pptx.chart.chart import Chart
from pptx.chart.data import CategoryChartData
from pptx.chart.xmlwriter import ChartXmlWriter
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.oxml import parse_xml as pptx_parse_xml
from pptx.util import Emu, Inches

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "e2e" / "fixtures" / "office-real"

ROWS = [("Таблетирование", 7, 6), ("Фасовка", 4, 4), ("Упаковка", 9, 7), ("Склад", 2, 2)]
HEAD = ("Участок", "Отклонения, шт.", "Устранено, шт.")


def chart_data() -> CategoryChartData:
    cd = CategoryChartData()
    cd.categories = [r[0] for r in ROWS]
    cd.add_series(HEAD[1], [r[1] for r in ROWS])
    cd.add_series(HEAD[2], [r[2] for r in ROWS])
    return cd


def make_docx(path: Path) -> None:
    doc = Document()
    doc.add_heading("СОП-ОК-021. Контроль отклонений на участках", level=1)
    doc.add_paragraph("Документ проверяет перенос таблиц и диаграмм в PDF-копию для чтения с телефона без сети. "
                      "Ниже — сводная таблица отклонений за ноябрь и диаграмма по участкам.")
    doc.add_heading("1. Сводная таблица", level=2)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, HEAD):
        cell.text = text
        cell.paragraphs[0].runs[0].bold = True
    for row in ROWS:
        cells = table.add_row().cells
        for cell, value in zip(cells, row):
            cell.text = str(value)
    total = table.add_row().cells
    total[0].text, total[1].text, total[2].text = "Итого", str(sum(r[1] for r in ROWS)), str(sum(r[2] for r in ROWS))
    total[0].paragraphs[0].runs[0].bold = True

    doc.add_heading("2. Диаграмма отклонений по участкам", level=2)
    cd = chart_data()
    space = pptx_parse_xml(ChartXmlWriter(XL_CHART_TYPE.COLUMN_CLUSTERED, cd).xml.encode("utf-8"))
    chart = Chart(space, None)
    chart.has_title = True
    chart.chart_title.text_frame.text = "Отклонения и устранение по участкам"
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    chart.legend.include_in_layout = False
    package = doc.part.package
    xlsx = Part(PackURI("/word/embeddings/Microsoft_Excel_Worksheet1.xlsx"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", cd.xlsx_blob, package)
    chart_part = Part(PackURI("/word/charts/chart1.xml"),
                      "application/vnd.openxmlformats-officedocument.drawingml.chart+xml", b"", package)
    xlsx_rid = chart_part.relate_to(xlsx, RT.PACKAGE)
    space.append(pptx_parse_xml(
        '<c:externalData xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="%s">'
        '<c:autoUpdate val="0"/></c:externalData>' % xlsx_rid))
    chart_part._blob = etree.tostring(space, xml_declaration=True, encoding="UTF-8", standalone=True)
    rid = doc.part.relate_to(chart_part, RT.CHART)
    cx, cy = Emu(Inches(6)), Emu(Inches(3.4))
    inline = parse_xml(
        '<wp:inline distT="0" distB="0" distL="0" distR="0" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<wp:extent cx="%d" cy="%d"/><wp:effectExtent l="0" t="0" r="0" b="0"/>'
        '<wp:docPr id="101" name="Диаграмма 1"/><wp:cNvGraphicFramePr/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
        '<c:chart r:id="%s"/></a:graphicData></a:graphic></wp:inline>' % (cx, cy, rid))
    run = doc.add_paragraph().add_run()
    drawing = parse_xml('<w:drawing xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
    drawing.append(inline)
    run._r.append(drawing)

    doc.add_heading("3. Ответственность", level=2)
    p = doc.add_paragraph("Ответственный за контроль — мастер участка. Пересмотр СОП — ежегодно. ")
    note = p.add_run("Отклонения с участка упаковки разбираются на пятиминутке в начале смены.")
    note.font.color.rgb = RGBColor(0x4F, 0x00, 0x7D)
    note.font.size = Pt(11)
    doc.core_properties.title = "СОП-ОК-021. Контроль отклонений"
    doc.save(str(path))


def make_pptx(path: Path) -> None:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "Гигиена рук на производстве"
    s.placeholders[1].text = "Обучение смены · СОП-ГП-004"
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Когда обрабатывать руки"
    body = s.placeholders[1].text_frame
    body.text = "Перед входом в чистую зону"
    for line in ("После перерыва и приёма пищи", "После контакта с отходами", "Перед сменой перчаток"):
        body.add_paragraph().text = line
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Нарушения по участкам"
    table = s.shapes.add_table(len(ROWS) + 1, 3, Inches(0.8), Inches(1.8), Inches(11.5), Inches(3.5)).table
    for c, text in enumerate(HEAD):
        table.cell(0, c).text = text
    for r, row in enumerate(ROWS, start=1):
        for c, value in enumerate(row):
            table.cell(r, c).text = str(value)
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Динамика устранения"
    chart = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.8), Inches(1.6), Inches(11.5), Inches(5.4),
                               chart_data()).chart
    chart.has_title = True
    chart.chart_title.text_frame.text = "Отклонения и устранение"
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    chart.legend.include_in_layout = False
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Скрытый слайд для докладчика"
    s.placeholders[1].text = "Этот слайд скрыт и не показывается сотрудникам"
    s._element.set("show", "0")
    prs.save(str(path))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    make_docx(OUT / "СОП-ОК-021 Контроль отклонений.docx")
    make_pptx(OUT / "Гигиена рук — обучение.pptx")
    for f in sorted(OUT.iterdir()):
        print(f.name, f.stat().st_size)


if __name__ == "__main__":
    main()
