"""Проверка PDF-копий настоящих файлов Word, Excel и PowerPoint: что из таблиц и диаграмм попало в копию.

Каждый файл из e2e/fixtures/office-real конвертируется двумя способами — через установленный Microsoft
Office (как на ноутбуке методиста) и встроенным конвертером (запасной путь без Office). В тексте PDF ищутся
заголовки таблиц и подписи диаграмм (название, категории): у диаграммы, нарисованной в копии, они есть.

    python tools/check_office_copies.py      → e2e/results/office-copies.json и PDF-копии рядом
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services.convert import convert_office_to_pdf, find_msoffice  # noqa: E402

SRC = ROOT / "e2e" / "fixtures" / "office-real"
OUT = ROOT / "e2e" / "results" / "office-copies"

# что должно читаться в копии: заголовки таблиц и подписи диаграмм
EXPECT = {
    "docx": {"table": ["Участок", "Отклонения, шт.", "Устранено, шт.", "Таблетирование", "Итого"],
             "chart": ["Отклонения и устранение по участкам"]},
    "xlsx": {"table": ["Склад №1, °C", "Влажность, %", "Ответственный", "Проверка кондиционера", "Итого"],
             "chart": ["Температура на складах, ноябрь", "Доля отклонений по участкам"]},
    "pptx": {"table": ["Участок", "Отклонения, шт.", "Таблетирование"],
             "chart": ["Отклонения и устранение"]},
}


def squash(s: str) -> str:
    return "".join(s.split()).lower()


def pdf_text(path: Path) -> list[str]:
    from pypdf import PdfReader
    return [p.extract_text() or "" for p in PdfReader(str(path)).pages]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    report = []
    for src in sorted(SRC.iterdir()):
        kind = src.suffix[1:].lower()
        if kind not in EXPECT:
            continue
        for mode in ("msoffice", "builtin"):
            if mode == "msoffice" and not find_msoffice(kind):
                report.append({"file": src.name, "converter": "msoffice", "skipped": "Office не установлен"})
                continue
            os.environ.pop("LMS_CONVERTER", None)
            if mode == "builtin":
                os.environ["LMS_CONVERTER"] = "builtin"
            dst = OUT / ("%s — %s.pdf" % (src.stem, mode))
            t = time.time()
            res = convert_office_to_pdf(src, dst)
            row = {"file": src.name, "asked": mode, "converter": res.converter, "ok": res.ok, "pages": res.pages,
                   "seconds": round(time.time() - t, 1), "warnings": res.warnings, "error": res.error}
            if res.ok:
                pages = pdf_text(dst)
                text = squash(" ".join(pages))
                for part, needles in EXPECT[kind].items():
                    row[part + "_found"] = [n for n in needles if squash(n) in text]
                    row[part + "_missing"] = [n for n in needles if squash(n) not in text]
                if kind == "pptx":
                    row["hidden_slide_in_pdf"] = "скрытыйслайд" in text
            report.append(row)
            print(json.dumps(row, ensure_ascii=False))
    os.environ.pop("LMS_CONVERTER", None)
    (ROOT / "e2e" / "results" / "office-copies.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                                                 encoding="utf-8")


if __name__ == "__main__":
    main()
