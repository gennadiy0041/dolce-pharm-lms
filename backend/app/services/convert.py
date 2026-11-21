"""Преобразование офисных файлов (DOCX, XLSX, PPTX) в PDF-копию для чтения с телефона без сети.

Порядок работы:

1. Если на Windows установлен Microsoft Office (Word, Excel, PowerPoint — см. find_msoffice),
   файл сохраняет в PDF сама программа Office: это точная копия со всеми таблицами, диаграммами,
   SmartArt и шрифтами. Office запускается без окна, файл открывается только для чтения.
2. Иначе, если найден LibreOffice (см. find_soffice), файл конвертирует он.
3. Иначе, а также если программа не справилась или не уложилась во время, работает
   встроенный конвертер на reportlab. Он переносит содержание и основную разметку
   (заголовки, списки, таблицы, рисунки, слайды), а не воспроизводит документ пиксель в пиксель.

LMS_CONVERTER=builtin отключает оба внешних конвертера, LMS_MSOFFICE=off — только Microsoft Office.

convert_office_to_pdf никогда не бросает исключений: причина отказа возвращается текстом
на русском языке, который показывается автору курса. После вызова файл dst существует
тогда и только тогда, когда result.ok == True (устаревшая копия от прошлой версии файла
при неудаче удаляется, чтобы сотрудник не читал PDF, не соответствующий исходнику).

Переменные окружения:
    LMS_SOFFICE    — путь к soffice, если LibreOffice установлен в нестандартное место;
    LMS_CONVERTER  — значение builtin запрещает Microsoft Office и LibreOffice (так запускаются тесты);
    LMS_MSOFFICE   — значение off запрещает только Microsoft Office.

Что встроенный конвертер сознательно не переносит (автор получает предупреждение в
result.warnings): диаграммы, SmartArt (показывается списком), видео и звук, рисунки EMF/WMF,
сноски, формулы Excel без сохранённых значений (показываются текстом), скрытые листы,
строки и столбцы. Шрифты заменяются на DejaVu, поэтому переносы строк не совпадают с исходными.
"""
from __future__ import annotations

import colorsys
import datetime as _dt
import functools
import io
import logging
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import uuid
import warnings as _warnings
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as _rl_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    Image as RLImage,
    LongTable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
)
from reportlab.platypus.doctemplate import LayoutError

from .pdf_fonts import (
    CONDENSED,
    CONDENSED_BOLD,
    SANS,
    SANS_BOLD,
    SANS_BOLD_ITALIC,
    SANS_ITALIC,
    SERIF,
    SERIF_BOLD,
    register_fonts,
)

log = logging.getLogger(__name__)

OFFICE_EXTENSIONS = {"docx", "xlsx", "pptx"}

#: Сколько секунд ждём LibreOffice, прежде чем перейти на встроенный конвертер.
LIBREOFFICE_TIMEOUT = 120
#: Сколько секунд ждём Microsoft Office (включая запуск программы).
MSOFFICE_TIMEOUT = 150


@dataclass
class ConversionResult:
    ok: bool
    converter: str | None = None      # "libreoffice" | "builtin"
    pages: int | None = None          # страниц в получившемся PDF
    error: str | None = None          # причина отказа по-русски, показывается автору курса
    warnings: list[str] = field(default_factory=list)   # по-русски, напр. «Диаграммы не перенесены»


class _Fail(Exception):
    """Ожидаемый отказ: текст исключения — готовое сообщение для автора курса."""


class _Ctx:
    """Собирает предупреждения по ходу конвертации (без повторов)."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warn(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)


# ════════════════════════════════════════════════════════════════════════════
#  Публичный интерфейс
# ════════════════════════════════════════════════════════════════════════════

_SOFFICE_LOCATIONS = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/usr/local/bin/soffice",
    "/opt/libreoffice/program/soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
)


def find_soffice() -> Path | None:
    """Ищет исполняемый файл LibreOffice: LMS_SOFFICE → PATH → стандартные каталоги.

    Если LMS_SOFFICE указывает на несуществующий файл, значение игнорируется и поиск
    продолжается обычным порядком.
    """
    override = (os.environ.get("LMS_SOFFICE") or "").strip().strip('"')
    if override:
        try:
            candidate = Path(override)
            if candidate.is_file():
                return candidate
        except OSError:
            pass
        log.warning("LMS_SOFFICE=%r: файл не найден, ищу LibreOffice в стандартных местах", override)
    for name in ("soffice", "soffice.com", "libreoffice"):
        found = shutil.which(name)
        if found:
            return Path(found)
    candidates = list(_SOFFICE_LOCATIONS)
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432"):
        base = os.environ.get(var)
        if base:
            candidates.append(os.path.join(base, "LibreOffice", "program", "soffice.exe"))
    for raw in candidates:
        try:
            candidate = Path(raw)
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def convert_office_to_pdf(src: Path, dst: Path, ext: str | None = None) -> ConversionResult:
    """Делает из офисного файла src PDF-копию dst. Не бросает исключений (см. описание модуля)."""
    ctx = _Ctx()
    result = ConversionResult(ok=False)
    dst_path: Path | None = None
    try:
        src_path = Path(src)
        dst_path = Path(dst)
        converter, pages = _convert(src_path, dst_path, ext, ctx)
        result.ok = True
        result.converter = converter
        result.pages = pages
    except _Fail as exc:
        result.error = str(exc)
    except OSError as exc:
        log.exception("Конвертация %s в PDF: ошибка чтения или записи", src)
        result.error = (
            "Не удалось прочитать исходный файл или записать PDF-копию на диск сервера "
            f"({_short(exc.strerror or str(exc), 120)}). Повторите попытку позже или обратитесь к администратору."
        )
    except Exception as exc:  # noqa: BLE001 — наружу не должно выйти ничего
        log.exception("Конвертация %s в PDF завершилась ошибкой", src)
        result.error = (
            "Не удалось преобразовать файл в PDF: он повреждён или содержит элементы, "
            "которые конвертер не понимает. Откройте файл в офисном редакторе, сохраните "
            f"его заново и загрузите ещё раз. (Техническая причина: {type(exc).__name__}: {_short(str(exc), 160)})"
        )
    result.warnings = list(ctx.warnings)
    if not result.ok and dst_path is not None:
        _remove_quietly(dst_path)
    return result


def _convert(src: Path, dst: Path, ext: str | None, ctx: _Ctx) -> tuple[str, int]:
    kind = (ext if ext is not None else src.suffix).strip().lower().lstrip(".")
    if kind not in OFFICE_EXTENSIONS:
        shown = f".{kind}" if kind else "без расширения"
        raise _Fail(f"Формат {shown} не поддерживается. Сохраните файл как DOCX, XLSX или PPTX.")
    _sniff(src, kind)

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.stem}.{uuid.uuid4().hex[:10]}.tmp")
    try:
        converter: str | None = None
        pages = 0
        forced_builtin = (os.environ.get("LMS_CONVERTER") or "").strip().lower() == "builtin"
        if not forced_builtin and find_msoffice(kind):
            try:
                _run_msoffice(src, kind, tmp, MSOFFICE_TIMEOUT)
                ms_ctx = _Ctx()
                pages = _validate(tmp, kind, _facts_from_source(src, kind), ms_ctx)
                for text in ms_ctx.warnings:
                    ctx.warn(text)
                converter = "msoffice"
            except _Fail as exc:
                _remove_quietly(tmp)
                reason = _short(str(exc).rstrip("."), 200)
                ctx.warn(f"Microsoft {_KIND_APP[kind]} не справился ({reason}) — копия сделана другим конвертером.")
            except Exception as exc:  # noqa: BLE001
                log.exception("Microsoft Office: сбой при конвертации %s", src)
                _remove_quietly(tmp)
                ctx.warn(f"Microsoft {_KIND_APP[kind]} не справился ({type(exc).__name__}) — копия сделана другим конвертером.")
        soffice = None if forced_builtin or converter else find_soffice()
        if soffice is not None:
            try:
                _run_libreoffice(soffice, src, kind, tmp, LIBREOFFICE_TIMEOUT)
                lo_ctx = _Ctx()
                pages = _validate(tmp, kind, _facts_from_source(src, kind), lo_ctx)
                for text in lo_ctx.warnings:
                    ctx.warn(text)
                converter = "libreoffice"
            except _Fail as exc:
                _remove_quietly(tmp)
                reason = _short(str(exc).rstrip("."), 200)
                ctx.warn(f"LibreOffice не справился ({reason}) — использован встроенный конвертер.")
            except Exception as exc:  # noqa: BLE001
                log.exception("LibreOffice: сбой при конвертации %s", src)
                _remove_quietly(tmp)
                ctx.warn(f"LibreOffice не справился ({type(exc).__name__}) — использован встроенный конвертер.")
        if converter is None:
            facts = _BUILDERS[kind](src, tmp, ctx)
            pages = _validate(tmp, kind, facts, ctx)
            converter = "builtin"
        os.replace(tmp, dst)
        return converter, pages
    finally:
        _remove_quietly(tmp)


# ════════════════════════════════════════════════════════════════════════════
#  Проверка входного файла и результата
# ════════════════════════════════════════════════════════════════════════════

_KIND_TITLE = {"docx": "DOCX", "xlsx": "XLSX", "pptx": "PPTX"}
_KIND_APP = {"docx": "Word", "xlsx": "Excel", "pptx": "PowerPoint"}
_KIND_DIR = {"docx": "word/", "xlsx": "xl/", "pptx": "ppt/"}
_KIND_MAIN = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml", "pptx": "ppt/presentation.xml"}
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _sniff(src: Path, kind: str) -> None:
    """Отсеивает то, что заведомо не откроется, с понятным объяснением."""
    title = _KIND_TITLE[kind]
    try:
        if not src.is_file():
            raise _Fail("Файл не найден на сервере. Загрузите его ещё раз.")
        size = src.stat().st_size
        with open(src, "rb") as fh:
            head = fh.read(8)
    except OSError as exc:
        raise _Fail("Файл не удалось прочитать с диска. Загрузите его ещё раз.") from exc
    if size == 0:
        raise _Fail("Файл пустой (0 байт). Загрузите его ещё раз.")
    if head.startswith(_OLE_MAGIC):
        if _ole_is_encrypted(src):
            raise _Fail(
                f"Файл защищён паролем. Снимите пароль в {_KIND_APP[kind]} "
                "(Файл → Сведения → Защита) и загрузите файл заново."
            )
        old = title[:-1]
        raise _Fail(
            f"Файл сохранён в старом формате ({old}), хотя назван .{kind}. "
            f"Откройте его в {_KIND_APP[kind]} и сохраните как {title}."
        )
    try:
        if not zipfile.is_zipfile(src):
            raise _Fail(f"Файл повреждён или не является документом {title}: его не удалось открыть.")
        with zipfile.ZipFile(src) as archive:
            names = archive.namelist()
            main = _KIND_MAIN[kind]
            sample = archive.read(main)[:4096] if main in names else b""
    except _Fail:
        raise
    except Exception as exc:  # zipfile.BadZipFile, OSError, RuntimeError (шифрованный zip)…
        raise _Fail(f"Файл повреждён или не является документом {title}: его не удалось открыть.") from exc
    if not any(name.startswith(_KIND_DIR[kind]) for name in names):
        actual = next((k for k, d in _KIND_DIR.items() if any(n.startswith(d) for n in names)), None)
        if actual:
            raise _Fail(
                f"Содержимое файла не соответствует расширению .{kind}: внутри {_KIND_TITLE[actual]}. "
                f"Переименуйте файл в .{actual} или сохраните его заново."
            )
        raise _Fail(f"Файл не является документом {title}: внутри нет нужных данных.")
    if b"purl.oclc.org/ooxml" in sample:
        raise _Fail(
            f"Файл сохранён в формате Strict Open XML, который не поддерживается. "
            f"Сохраните его в {_KIND_APP[kind]} как обычный {title}."
        )


def _ole_is_encrypted(src: Path) -> bool:
    needle = "EncryptedPackage".encode("utf-16-le")
    try:
        with open(src, "rb") as fh:
            tail = b""
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    return False
                if needle in tail + chunk:
                    return True
                tail = chunk[-len(needle):]
    except OSError:
        return False


def _validate(pdf: Path, kind: str, facts: dict, ctx: _Ctx) -> int:
    """Проверяет получившийся PDF и возвращает число страниц."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(pdf))
        pages = len(reader.pages)
    except Exception as exc:  # noqa: BLE001
        raise _Fail("Получившийся PDF не открывается — конвертация не удалась.") from exc
    if pages < 1:
        raise _Fail("Получившийся PDF пустой — конвертация не удалась.")
    if kind == "pptx":
        slides = facts.get("slides")
        # PowerPoint не выводит в PDF скрытые слайды — тогда страниц столько, сколько видимых
        if slides is not None and pages != slides and pages != facts.get("visible_slides"):
            raise _Fail(f"Не все слайды попали в PDF: в презентации слайдов — {slides}, в PDF страниц — {pages}.")
    if kind == "xlsx":
        missing = _missing_headers(reader, facts.get("headers") or [], facts.get("header_pages"))
        if missing:
            shown = ", ".join(f"«{_short(name, 40)}»" for name in missing[:8])
            more = f" и ещё {len(missing) - 8}" if len(missing) > 8 else ""
            ctx.warn(f"В PDF не найдены заголовки столбцов: {shown}{more}. Проверьте копию перед публикацией.")
    return pages


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


_HEADER_SCAN_LIMIT = 60     # столько страниц просматриваем в поисках шапки, не зная, где она


def _missing_headers(reader, headers: list[str], hinted_pages: list[int] | None = None) -> list[str]:
    """Заголовки столбцов, которых нет в тексте PDF.

    hinted_pages — номера страниц (с единицы), где встроенный конвертер начал таблицы: шапку ищем
    сначала там. Извлечение текста стоит ~0,2 с на страницу, поэтому остальные страницы
    просматриваются с ограничением; если просмотрено не всё и подсказок не было, вывод не делаем.
    """
    remaining: dict[str, str] = {}
    for text in headers:
        key = _squash(text)
        if key:
            remaining.setdefault(key, text.strip())
    if not remaining:
        return []
    total = len(reader.pages)
    hinted = [n - 1 for n in dict.fromkeys(hinted_pages or []) if 1 <= n <= total]
    skip = set(hinted)
    others = [index for index in range(total) if index not in skip][:_HEADER_SCAN_LIMIT]
    for index in hinted + others:
        try:
            chunk = _squash(reader.pages[index].extract_text() or "")
        except Exception:  # noqa: BLE001 — страница без извлекаемого текста
            continue
        for key in [k for k in remaining if k in chunk]:
            del remaining[key]
        if not remaining:
            return []
    conclusive = bool(hinted) or len(hinted) + len(others) == total
    return list(remaining.values()) if conclusive else []


def _facts_from_source(src: Path, kind: str) -> dict:
    """Что должно быть в PDF, сделанном LibreOffice (для встроенного конвертера это знает сам сборщик)."""
    try:
        if kind == "pptx":
            from pptx import Presentation

            slides = list(Presentation(str(src)).slides)
            visible = sum(1 for s in slides if s._element.get("show") not in ("0", "false"))
            return {"slides": len(slides), "visible_slides": visible}
        if kind == "xlsx":
            import openpyxl

            headers: list[str] = []
            with _warnings.catch_warnings():
                _warnings.simplefilter("ignore")
                book = openpyxl.load_workbook(str(src), data_only=True)
            for ws in book.worksheets:
                if ws.sheet_state != "visible":
                    continue
                sheet = _xl_analyse(ws, ws, _Ctx())
                if sheet is not None:
                    headers.extend(text for text in sheet.header_texts if not _looks_numeric(text))
            return {"headers": headers}
    except Exception:  # noqa: BLE001 — проверка необязательная
        log.debug("Не удалось получить контрольные данные из %s", src, exc_info=True)
    return {}


def _looks_numeric(text: str) -> bool:
    return bool(re.fullmatch(r"[\d\s.,:%\-/]+", text or ""))


# ════════════════════════════════════════════════════════════════════════════
#  Microsoft Office (Windows)
# ════════════════════════════════════════════════════════════════════════════

_MSOFFICE_PROGID = {"docx": "Word.Application", "xlsx": "Excel.Application", "pptx": "PowerPoint.Application"}
_MSOFFICE_EXE = {"docx": "winword.exe", "xlsx": "excel.exe", "pptx": "powerpnt.exe"}
_msoffice_lock = threading.Lock()      # одна программа Office за раз: так надёжнее и меньше памяти

# Скрипт выполняется отдельным процессом PowerShell: зависшую программу Office можно снять по тайм-ауту,
# не трогая сервер. Файл открывается только для чтения, без окна, без запросов и без пароля
# (пароль-заглушка: защищённый файл не откроется, вместо того чтобы ждать ввода).
_MSOFFICE_SCRIPT = r"""
param([string]$Kind, [string]$Src, [string]$Out)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$app = $null; $doc = $null
try {
  if ($Kind -eq 'docx') {
    $app = New-Object -ComObject Word.Application
    $app.Visible = $false; $app.DisplayAlerts = 0
    $doc = $app.Documents.Open($Src, $false, $true, $false, 'lms-no-password')
    $doc.ExportAsFixedFormat($Out, 17)
    $doc.Close(0); $doc = $null
    # Quit у Word принимает аргументы только по ссылке — без аргументов выходит без сохранения
    if ($app.Documents.Count -eq 0) { $app.Quit() }
  } elseif ($Kind -eq 'xlsx') {
    $app = New-Object -ComObject Excel.Application
    $app.Visible = $false; $app.DisplayAlerts = $false; $app.AskToUpdateLinks = $false; $app.ScreenUpdating = $false
    $doc = $app.Workbooks.Open($Src, 0, $true, [Type]::Missing, 'lms-no-password')
    foreach ($ws in $doc.Worksheets) {
      if ($ws.Visible -ne -1) { continue }
      # для экрана телефона: лист по ширине страницы, широкие таблицы — альбомом
      try { $app.PrintCommunication = $false } catch {}
      $ps = $ws.PageSetup
      $ps.Zoom = $false; $ps.FitToPagesWide = 1; $ps.FitToPagesTall = $false
      if ($ws.UsedRange.Columns.Count -gt 6) { $ps.Orientation = 2 }
      try { $app.PrintCommunication = $true } catch {}
    }
    $doc.ExportAsFixedFormat(0, $Out)
    $doc.Close($false); $doc = $null
    if ($app.Workbooks.Count -eq 0) { $app.Quit() }
  } else {
    $app = New-Object -ComObject PowerPoint.Application
    $doc = $app.Presentations.Open($Src, -1, 0, 0)
    $doc.SaveAs($Out, 32)
    $doc.Close(); $doc = $null
    if ($app.Presentations.Count -eq 0) { $app.Quit() }
  }
  'ok'
} catch {
  'error: ' + $_.Exception.Message
  if ($doc) { try { if ($Kind -eq 'xlsx') { $doc.Close($false) } elseif ($Kind -eq 'docx') { $doc.Close(0) } else { $doc.Close() } } catch {} }
  if ($app) {
    try {
      $left = if ($Kind -eq 'docx') { $app.Documents.Count } elseif ($Kind -eq 'xlsx') { $app.Workbooks.Count } else { $app.Presentations.Count }
      if ($left -eq 0) { $app.Quit() }
    } catch {}
  }
}
"""


def find_msoffice(kind: str) -> bool:
    """Установлена ли на Windows программа Microsoft Office для этого формата (Word, Excel, PowerPoint)."""
    if os.name != "nt" or (os.environ.get("LMS_MSOFFICE") or "").strip().lower() in {"off", "0", "no", "false"}:
        return False
    progid = _MSOFFICE_PROGID.get(kind)
    if not progid:
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + "\\CLSID"):
            return True
    except OSError:
        return False


def _office_pids(exe: str) -> set[int]:
    try:
        out = subprocess.run(  # noqa: S603, S607
            ["tasklist", "/FI", f"IMAGENAME eq {exe}", "/FO", "CSV", "/NH"], capture_output=True, text=True,
            errors="replace", timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except Exception:  # noqa: BLE001
        return set()
    pids = set()
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def _run_msoffice(src: Path, kind: str, out_pdf: Path, timeout: float) -> None:
    """Сохраняет файл в PDF средствами Microsoft Office. При неудаче — _Fail."""
    work = Path(tempfile.mkdtemp(prefix="lms-ms-"))
    try:
        source = work / f"source.{kind}"   # короткое имя: исходник может называться как угодно
        produced = work / "copy.pdf"
        script = work / "convert.ps1"
        shutil.copyfile(src, source)
        script.write_text(_MSOFFICE_SCRIPT, encoding="utf-8-sig")
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
               kind, str(source), str(produced)]
        with _msoffice_lock:
            before = _office_pids(_MSOFFICE_EXE[kind])
            try:
                proc = subprocess.Popen(  # noqa: S603
                    cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(work),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                raise _Fail(f"не удалось запустить PowerShell: {exc.strerror or exc}") from exc
            try:
                stdout, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                _kill_tree(proc)
                # программа Office запускается системой, а не PowerShell: снимаем только ту копию,
                # которая появилась во время этой конвертации
                for pid in _office_pids(_MSOFFICE_EXE[kind]) - before:
                    subprocess.run(  # noqa: S603, S607
                        ["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=20,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                raise _Fail(f"не ответил за {int(timeout)} с") from exc
        lines = [x.strip() for x in stdout.decode("utf-8", "replace").splitlines() if x.strip()]
        status = lines[-1] if lines else ""
        if status != "ok":
            raise _Fail(_short(status.removeprefix("error: ") or f"код завершения {proc.returncode}", 200))
        if not produced.is_file() or produced.stat().st_size == 0:
            raise _Fail("не создал PDF")
        shutil.copyfile(produced, out_pdf)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════════════
#  LibreOffice
# ════════════════════════════════════════════════════════════════════════════

def _run_libreoffice(soffice: Path, src: Path, kind: str, out_pdf: Path, timeout: float) -> None:
    """Запускает LibreOffice без окна, с отдельным временным профилем. При неудаче — _Fail."""
    work = Path(tempfile.mkdtemp(prefix="lms-lo-"))
    try:
        profile = work / "profile"
        outdir = work / "out"
        outdir.mkdir()
        source = work / f"source.{kind}"   # короткое ASCII-имя: исходник может называться как угодно
        shutil.copyfile(src, source)
        cmd = [
            str(soffice),
            "--headless", "--invisible", "--norestore", "--nologo", "--nodefault",
            "--nolockcheck", "--nofirststartwizard",
            f"-env:UserInstallation={profile.as_uri()}",
            "--convert-to", "pdf",
            "--outdir", str(outdir),
            str(source),
        ]
        popen_kwargs: dict = dict(
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(work)
        )
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            popen_kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)  # noqa: S603
        except OSError as exc:
            raise _Fail(f"не удалось запустить {soffice.name}: {exc.strerror or exc}") from exc
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _kill_tree(proc)
            raise _Fail(f"не ответил за {int(timeout)} с") from exc
        produced = outdir / "source.pdf"
        if not produced.is_file():
            others = sorted(outdir.glob("*.pdf"))
            produced = others[0] if others else produced
        if not produced.is_file() or produced.stat().st_size == 0:
            raise _Fail(f"не создал PDF (код завершения {code})")
        shutil.copyfile(produced, out_pdf)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _kill_tree(proc: subprocess.Popen) -> None:
    """Завершает процесс вместе с потомками (soffice.com → soffice.bin)."""
    try:
        if os.name == "nt":
            subprocess.run(  # noqa: S603, S607
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        pass


# ════════════════════════════════════════════════════════════════════════════
#  Общие вспомогательные функции встроенного конвертера
# ════════════════════════════════════════════════════════════════════════════

_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "v": "urn:schemas-microsoft-com:vml",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
}


@functools.lru_cache(maxsize=None)
def _qn(tag: str) -> str:
    """'w:p' → '{…wordprocessingml…}p'."""
    prefix, _, local = tag.partition(":")
    return "{%s}%s" % (_NS[prefix], local)


@functools.lru_cache(maxsize=None)
def _xpath(expr: str) -> str:
    return "/".join(_qn(part) for part in expr.split("/"))


def _find(el, expr: str):
    return el.find(_xpath(expr)) if el is not None else None


def _findall(el, expr: str) -> list:
    return el.findall(_xpath(expr)) if el is not None else []


def _int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


# ── цвет ────────────────────────────────────────────────────────────────────

_INK = (0.11, 0.12, 0.14)       # тёмные «чернила» по умолчанию
_WHITE = (1.0, 1.0, 1.0)


def _hex_rgb(value) -> tuple | None:
    """'RRGGBB' или 'AARRGGBB' → (r, g, b) в долях единицы."""
    if not isinstance(value, str):
        return None
    value = value.strip().lstrip("#")
    if len(value) == 8:
        value = value[2:]
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", value):
        return None
    return (int(value[0:2], 16) / 255.0, int(value[2:4], 16) / 255.0, int(value[4:6], 16) / 255.0)


def _rgb_hex(rgb: tuple) -> str:
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(c * 255)))) for c in rgb[:3])


def _lum(rgb: tuple) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _mix(rgb: tuple, other: tuple, share: float) -> tuple:
    """share долей rgb + (1 − share) долей other."""
    return tuple(a * share + b * (1.0 - share) for a, b in zip(rgb, other))


def _readable_on(rgb: tuple | None, background: tuple) -> tuple:
    """Цвет текста, который видно на данном фоне: исходный, если контраста хватает."""
    if rgb is not None and abs(_lum(rgb) - _lum(background)) >= 0.28:
        return rgb
    return _WHITE if _lum(background) < 0.5 else _INK


def _rl_color(rgb: tuple):
    return colors.Color(rgb[0], rgb[1], rgb[2])


# ── текст и шрифты ───────────────────────────────────────────────────────────

_GLYPHS: dict[str, frozenset] = {}

# Символы из «символьных» шрифтов Word (Symbol, Wingdings) лежат в области частного использования.
_PUA = {
    0xF0B7: "•", 0xF0A7: "▪", 0xF0A8: "□", 0xF06F: "□", 0xF071: "□", 0xF0FC: "✓", 0xF0FB: "✗",
    0xF0FE: "☑", 0xF0FD: "☒", 0xF0D8: "➢", 0xF0E0: "→", 0xF0DF: "←", 0xF0E8: "➔", 0xF06C: "●",
    0xF06E: "■", 0xF0A1: "○", 0xF076: "❖", 0xF02D: "–", 0xF0B0: "°", 0xF0B1: "±", 0xF0B4: "×",
    0xF0A3: "≤", 0xF0B3: "≥", 0xF0AE: "→", 0xF0AC: "←", 0xF0AD: "↑", 0xF0AF: "↓",
}
_INVISIBLE = frozenset([0xAD, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0xFEFF])
_MISSING_GLYPH = "□"
_MISSING_WARNING = "Некоторые символы (например, эмодзи или иероглифы) отсутствуют в шрифте и заменены на □."

_SERIF_PREFIXES = (
    "times", "georgia", "cambria", "garamond", "palatino", "book antiqua", "bookman", "century",
    "constantia", "pt serif", "liberation serif", "noto serif", "dejavu serif", "droid serif",
    "baskerville", "minion", "charter", "linux libertine", "pt astra serif", "tinos",
)


def _glyphs(font_name: str) -> frozenset:
    cached = _GLYPHS.get(font_name)
    if cached is None:
        register_fonts()
        face = pdfmetrics.getFont(font_name).face
        table = getattr(face, "charToGlyph", None) or getattr(face, "charWidths", {})
        cached = frozenset(table)
        _GLYPHS[font_name] = cached
    return cached


def _is_serif(font_name: str | None) -> bool:
    name = (font_name or "").strip().lower()
    return bool(name) and name.startswith(_SERIF_PREFIXES)


def _clean(text: str, ctx: _Ctx) -> str:
    """Убирает управляющие символы и заменяет то, чего нет в шрифте DejaVu."""
    if not text:
        return ""
    known = _glyphs(SANS)
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if code < 32:
            if ch in "\n\t":
                out.append(ch)
            elif ch == "\v":
                out.append("\n")
            continue
        if code in _INVISIBLE or 0xFE00 <= code <= 0xFE0F:
            continue
        if 0xF000 <= code <= 0xF0FF:
            out.append(_PUA.get(code, "•"))
            continue
        if code in known:
            out.append(ch)
        elif ch.isspace():
            out.append(" ")
        else:
            out.append(_MISSING_GLYPH)
            ctx.warn(_MISSING_WARNING)
    return "".join(out)


def _markup(text: str, ctx: _Ctx, serif: bool = False) -> str:
    """Текст → безопасная разметка reportlab Paragraph (экранирование, переносы строк, пробелы)."""
    text = _clean(text, ctx)
    if not text:
        return ""
    text = re.sub(r" {2,}", lambda m: " " + "\u00a0" * (len(m.group(0)) - 1), text)
    if not serif:
        return _escape(text)
    serif_glyphs = _glyphs(SERIF)
    parts: list[str] = []
    buf: list[str] = []
    buf_ok = True
    for ch in text:
        ok = ch in "\n\t " or ord(ch) in serif_glyphs
        if ok != buf_ok and buf:
            parts.append(_serif_part("".join(buf), buf_ok))
            buf = []
        buf_ok = ok
        buf.append(ch)
    if buf:
        parts.append(_serif_part("".join(buf), buf_ok))
    return "".join(parts)


def _serif_part(text: str, in_serif: bool) -> str:
    escaped = _escape(text)
    return escaped if in_serif else f'<font name="{SANS}">{escaped}</font>'


def _escape(text: str) -> str:
    return _xml_escape(text).replace("\t", "\u00a0" * 4).replace("\n", "<br/>")


def _plain(markup: str) -> str:
    """Разметка → видимый текст (для проверки «есть ли что печатать»)."""
    return re.sub(r"<[^>]+>", "", markup).replace("\u00a0", " ").strip()


def _text_width(text: str, font: str, size: float) -> float:
    return pdfmetrics.stringWidth(text, font, size)


def _ellipsize(text: str, font: str, size: float, max_width: float) -> str:
    if _text_width(text, font, size) <= max_width:
        return text
    while text and _text_width(text + "…", font, size) > max_width:
        text = text[:-1]
    return text.rstrip() + "…"


# ── рисунки ─────────────────────────────────────────────────────────────────

_IMAGE_DPI = 200   # до такого разрешения уменьшаем крупные фото: файл читают с телефона


def _load_image(blob: bytes, display_width_pt: float, crop: tuple | None = None):
    """Готовит рисунок к вставке в PDF → (BytesIO, (ширина_px, высота_px)) или None, если формат не читается."""
    from PIL import Image as PILImage

    try:
        image = PILImage.open(io.BytesIO(blob))
        fmt = (image.format or "").upper()
        if fmt in ("WMF", "EMF"):
            return None
        image.load()
    except Exception:  # noqa: BLE001 — неизвестный или битый формат
        return None
    width, height = image.size
    if width < 1 or height < 1:
        return None
    changed = False
    if crop and any(abs(v) > 1e-4 for v in crop):
        left, top, right, bottom = crop
        box = (
            int(round(width * max(0.0, left))),
            int(round(height * max(0.0, top))),
            int(round(width * (1.0 - max(0.0, right)))),
            int(round(height * (1.0 - max(0.0, bottom)))),
        )
        if box[2] - box[0] >= 1 and box[3] - box[1] >= 1:
            image = image.crop(box)
            width, height = image.size
            changed = True
    limit = max(64, int(display_width_pt / 72.0 * _IMAGE_DPI))
    if width > limit * 1.3:
        image = image.resize((limit, max(1, int(round(height * limit / width)))), PILImage.LANCZOS)
        changed = True
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)
    if fmt == "JPEG" and not changed and image.mode in ("RGB", "L"):
        data = io.BytesIO(blob)
    else:
        data = io.BytesIO()
        if has_alpha:
            image.convert("RGBA").save(data, "PNG")
        elif fmt == "JPEG":
            image.convert("RGB").save(data, "JPEG", quality=85)
        else:
            (image if image.mode in ("RGB", "L") else image.convert("RGB")).save(data, "PNG")
        data.seek(0)
    return data, image.size


# ── постраничный PDF из потока абзацев и таблиц (DOCX, XLSX) ────────────────────

@dataclass
class _Page:
    size: tuple
    margin: float
    footer_room: float = 6 * mm
    header_room: float = 0.0

    @property
    def width(self) -> float:
        return self.size[0] - 2 * self.margin

    @property
    def height(self) -> float:
        return self.size[1] - 2 * self.margin - self.footer_room - self.header_room


def _build_flow_pdf(out: Path, story: list, page: _Page, title: str, footer: str = "", header: str = "") -> None:
    register_fonts()
    page_w, page_h = page.size
    bottom = page.margin + page.footer_room
    top = page.margin + page.header_room
    doc = BaseDocTemplate(
        str(out), pagesize=page.size, leftMargin=page.margin, rightMargin=page.margin,
        topMargin=top, bottomMargin=bottom, title=title or "", author="", creator="LMS",
    )
    frame = Frame(
        page.margin, bottom, page.width, page.height,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id="body",
    )

    def on_page_end(canv, _doc) -> None:
        canv.saveState()
        canv.setFont(SANS, 8)
        canv.setFillGray(0.42)
        baseline = page.margin + 0.5 * mm
        number = f"Стр. {canv.getPageNumber()}"
        canv.drawRightString(page_w - page.margin, baseline, number)
        room = page.width - _text_width(number, SANS, 8) - 6 * mm
        left = getattr(canv, "_lms_footer", None) or footer
        if left:
            canv.drawString(page.margin, baseline, _ellipsize(left, SANS, 8, room))
        if header:
            canv.drawString(page.margin, page_h - page.margin - 2.5 * mm, _ellipsize(header, SANS, 8, page.width))
        canv.restoreState()

    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPageEnd=on_page_end)])
    doc.build(story)


class _FlowTable(LongTable):
    """Таблица, которая помнит выравнивание и после разрыва строки между страницами.

    reportlab при разрыве внутри строки создаёт промежуточную таблицу без hAlign, и обе половины
    уезжают в центр; здесь выравнивание задаётся классом и потому переживает любое деление.
    """

    _lms_align = "LEFT"

    def __init__(self, *args, **kwargs) -> None:
        kwargs["hAlign"] = self._lms_align
        kwargs.setdefault("splitByRow", 1)
        kwargs.setdefault("splitInRow", 1)      # ячейку выше страницы можно разорвать
        super().__init__(*args, **kwargs)


class _FlowTableCenter(_FlowTable):
    _lms_align = "CENTER"


class _FlowTableRight(_FlowTable):
    _lms_align = "RIGHT"


class _FooterMark(Flowable):
    """Невидимая метка: начиная с этой страницы слева в нижнем колонтитуле пишется другой текст.

    Заодно записывает номер своей страницы в список pages (если он передан).
    """

    def __init__(self, text: str, pages: list | None = None) -> None:
        super().__init__()
        self.text = text
        self.pages = pages

    def wrap(self, _avail_width, _avail_height):
        return 0, 0

    def draw(self) -> None:
        self.canv._lms_footer = self.text
        if self.pages is not None:
            self.pages.append(self.canv.getPageNumber())


# ════════════════════════════════════════════════════════════════════════════
#  DOCX
# ════════════════════════════════════════════════════════════════════════════

_EMPTY = object()        # пустой абзац
_PAGE_BREAK = object()   # явный разрыв страницы

_HEADING_RE = re.compile(r"^(?:heading|заголовок)\s*([1-9])$", re.IGNORECASE)
_TITLE_NAMES = {"title", "название", "заголовок"}
_SUBTITLE_NAMES = {"subtitle", "подзаголовок"}
_BULLET_STYLE_RE = re.compile(r"^(?:list bullet|маркированный список)\s*(\d)?$", re.IGNORECASE)
_NUMBER_STYLE_RE = re.compile(r"^(?:list number|нумерованный список)\s*(\d)?$", re.IGNORECASE)
_RU_LOWER = "абвгдежзиклмнопрстуфхцчшщэюя"
_OFF = ("0", "false", "off")


def _w_on(el) -> bool:
    """Переключатель WordprocessingML (<w:b/>, <w:b w:val="0"/>)."""
    return el is not None and (el.get(_qn("w:val")) or "1").lower() not in _OFF


def _roman(number: int) -> str:
    if number <= 0 or number >= 4000:
        return str(number)
    out = []
    for value, sym in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                       (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while number >= value:
            out.append(sym)
            number -= value
    return "".join(out)


def _letters(number: int, alphabet: str) -> str:
    if number <= 0:
        return str(number)
    base = len(alphabet)
    letter = alphabet[(number - 1) % base]
    return letter * ((number - 1) // base + 1)


def _format_counter(number: int, fmt: str) -> str:
    if fmt == "decimalZero":
        return f"{number:02d}"
    if fmt == "upperRoman":
        return _roman(number)
    if fmt == "lowerRoman":
        return _roman(number).lower()
    if fmt == "upperLetter":
        return _letters(number, "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    if fmt == "lowerLetter":
        return _letters(number, "abcdefghijklmnopqrstuvwxyz")
    if fmt == "russianLower":
        return _letters(number, _RU_LOWER)
    if fmt == "russianUpper":
        return _letters(number, _RU_LOWER.upper())
    return str(number)


def _bullet_char(raw: str | None) -> str:
    raw = (raw or "").strip()
    if not raw:
        return "•"
    ch = raw[0]
    code = ord(ch)
    if 0xF000 <= code <= 0xF0FF:
        return _PUA.get(code, "•")
    if ch == "o":
        return "◦"
    if ch in "-–—":
        return "–"
    return ch if code in _glyphs(SANS) else "•"


class _DocxStyles:
    """Стили документа: поиск свойств по цепочке «стиль → базовый стиль → значения по умолчанию»."""

    def __init__(self, styles_el, theme_fonts: dict) -> None:
        self.by_id: dict[str, object] = {}
        self.default_para: str | None = None
        self.theme_fonts = theme_fonts
        self._run_cache: dict[str | None, dict] = {}
        self._name_cache: dict[str | None, list[str]] = {}
        self.doc_rpr = None
        if styles_el is None:
            return
        for st in styles_el.findall(_qn("w:style")):
            sid = st.get(_qn("w:styleId"))
            if sid is None:
                continue
            self.by_id[sid] = st
            if st.get(_qn("w:type")) == "paragraph" and (st.get(_qn("w:default")) or "").lower() in ("1", "true"):
                self.default_para = sid
        self.doc_rpr = _find(styles_el, "w:docDefaults/w:rPrDefault/w:rPr")

    def chain(self, sid: str | None) -> list:
        out = []
        seen = set()
        while sid is not None and sid not in seen and len(out) < 12:
            seen.add(sid)
            st = self.by_id.get(sid)
            if st is None:
                break
            out.append(st)
            based = st.find(_qn("w:basedOn"))
            sid = based.get(_qn("w:val")) if based is not None else None
        return out

    def names(self, sid: str | None) -> list[str]:
        cached = self._name_cache.get(sid)
        if cached is None:
            cached = []
            for st in self.chain(sid):
                name = st.find(_qn("w:name"))
                cached.append(((name.get(_qn("w:val")) if name is not None else "") or "").strip().lower())
            self._name_cache[sid] = cached
        return cached

    def ppr(self, sid: str | None, tag: str):
        """Первый найденный в цепочке элемент w:pPr/<tag>."""
        path = _xpath("w:pPr/" + tag)
        for st in self.chain(sid):
            found = st.find(path)
            if found is not None:
                return found
        return None

    def run_defaults(self, sid: str | None) -> dict:
        """Свойства шрифта, которые абзац получает от своего стиля."""
        cached = self._run_cache.get(sid)
        if cached is None:
            cached = self.parse_rpr(self.doc_rpr)
            for st in reversed(self.chain(sid)):
                cached.update(self.parse_rpr(st.find(_qn("w:rPr"))))
            self._run_cache[sid] = cached
        return cached

    def char_style(self, sid: str | None) -> dict:
        out: dict = {}
        for st in reversed(self.chain(sid)):
            out.update(self.parse_rpr(st.find(_qn("w:rPr"))))
        return out

    def parse_rpr(self, rpr) -> dict:
        out: dict = {}
        if rpr is None:
            return out
        val = _qn("w:val")
        for child in rpr:
            tag = child.tag
            if tag == _qn("w:b"):
                out["bold"] = _w_on(child)
            elif tag == _qn("w:i"):
                out["italic"] = _w_on(child)
            elif tag == _qn("w:u"):
                out["underline"] = (child.get(val) or "single").lower() not in ("none",) + _OFF
            elif tag in (_qn("w:strike"), _qn("w:dstrike")):
                out["strike"] = _w_on(child)
            elif tag == _qn("w:vertAlign"):
                out["vert"] = {"superscript": "super", "subscript": "sub"}.get(child.get(val) or "")
            elif tag == _qn("w:color"):
                out["color"] = _hex_rgb(child.get(val))
            elif tag == _qn("w:sz"):
                size = _int(child.get(val), 0)
                if size > 0:
                    out["size"] = size / 2.0
            elif tag == _qn("w:rFonts"):
                name = child.get(_qn("w:ascii")) or child.get(_qn("w:hAnsi")) or child.get(_qn("w:cs"))
                if not name:
                    theme = child.get(_qn("w:asciiTheme")) or child.get(_qn("w:hAnsiTheme")) or ""
                    name = self.theme_fonts.get("major" if theme.startswith("major") else "minor") if theme else None
                if name:
                    out["serif"] = _is_serif(name)
            elif tag == _qn("w:caps"):
                out["caps"] = _w_on(child)
            elif tag == _qn("w:vanish"):
                out["hidden"] = _w_on(child)
            elif tag == _qn("w:rStyle"):
                out["rstyle"] = child.get(val)
        return out


class _DocxNumbering:
    """Нумерация списков Word: считает номера так же, как их показывает редактор."""

    def __init__(self, document) -> None:
        self.levels: dict[str, dict[int, dict]] = {}
        self.nums: dict[str, tuple] = {}
        self.counters: dict[str, dict[int, int]] = {}
        self.started: set[str] = set()
        root = None
        try:
            from docx.opc.constants import RELATIONSHIP_TYPE as RT

            part = document.part.part_related_by(RT.NUMBERING)
            root = getattr(part, "element", None)
            if root is None:
                from lxml import etree

                root = etree.fromstring(part.blob)
        except Exception:  # noqa: BLE001 — в документе нет списков
            root = None
        if root is None:
            return
        val = _qn("w:val")
        for abstract in root.findall(_qn("w:abstractNum")):
            levels = {}
            for lvl in abstract.findall(_qn("w:lvl")):
                levels[_int(lvl.get(_qn("w:ilvl")), 0)] = self._parse_level(lvl)
            self.levels[abstract.get(_qn("w:abstractNumId"))] = levels
        for num in root.findall(_qn("w:num")):
            link = num.find(_qn("w:abstractNumId"))
            if link is None:
                continue
            starts: dict[int, int] = {}
            overrides: dict[int, dict] = {}
            for override in num.findall(_qn("w:lvlOverride")):
                ilvl = _int(override.get(_qn("w:ilvl")), 0)
                start = override.find(_qn("w:startOverride"))
                if start is not None:
                    starts[ilvl] = _int(start.get(val), 1)
                lvl = override.find(_qn("w:lvl"))
                if lvl is not None:
                    overrides[ilvl] = self._parse_level(lvl)
            self.nums[num.get(_qn("w:numId"))] = (link.get(val), starts, overrides)

    @staticmethod
    def _parse_level(lvl) -> dict:
        val = _qn("w:val")
        fmt = lvl.find(_qn("w:numFmt"))
        text = lvl.find(_qn("w:lvlText"))
        start = lvl.find(_qn("w:start"))
        return {
            "fmt": (fmt.get(val) if fmt is not None else None) or "decimal",
            "text": text.get(val) if text is not None else None,
            "start": _int(start.get(val), 1) if start is not None else 1,
        }

    def label(self, num_id: str, ilvl: int) -> tuple[str, bool] | None:
        """→ (текст метки, это_маркер) или None, если нумерацию не удалось разобрать."""
        entry = self.nums.get(num_id)
        if entry is None:
            return None
        abstract_id, starts, overrides = entry
        levels = dict(self.levels.get(abstract_id) or {})
        levels.update(overrides)
        if not levels:
            return None
        level = levels.get(ilvl) or levels[min(levels)]
        counters = self.counters.setdefault(abstract_id, {})
        if num_id not in self.started:
            self.started.add(num_id)
            for lvl_index, start in starts.items():
                counters[lvl_index] = start - 1
                for deeper in [k for k in counters if k > lvl_index]:
                    del counters[deeper]
        if level["fmt"] == "bullet":
            return _bullet_char(level["text"]), True
        current = counters.get(ilvl)
        current = level["start"] if current is None else current + 1
        counters[ilvl] = current
        for deeper in [k for k in counters if k > ilvl]:
            del counters[deeper]
        if level["fmt"] == "none":
            return re.sub(r"%\d", "", level["text"] or "").strip(), False

        def substitute(match) -> str:
            index = int(match.group(1)) - 1
            definition = levels.get(index) or level
            value = counters.get(index)
            if value is None:
                value = definition["start"]
            return _format_counter(value, definition["fmt"])

        template = level["text"] if level["text"] is not None else f"%{ilvl + 1}."
        return re.sub(r"%(\d)", substitute, template).strip(), False


@dataclass
class _Env:
    """Где сейчас рисуем: ширина, доступная рисункам, базовый кегль, внутри ли ячейки."""
    max_w: float
    max_h: float
    size: float
    in_cell: bool = False
    bold: bool = False
    dark: bool = False      # тёмный фон (заливка ячейки) — светлый текст оставляем светлым


class _Docx:
    BASE = 11.5
    LEADING = 1.4
    HEADING_SIZES = {0: 20.0, 1: 17.0, 2: 14.5, 3: 12.5}

    def __init__(self, document, ctx: _Ctx, page: _Page, table_mode: int = 0) -> None:
        self.doc = document
        self.ctx = ctx
        self.page = page
        self.table_mode = table_mode      # 0 — как в документе, 1 — без объединений, 2 — текстом
        self.styles = _DocxStyles(document.styles.element, self._theme_fonts())
        self.numbering = _DocxNumbering(document)
        self.src_base = self._source_base_size()
        self._style_cache: dict[tuple, ParagraphStyle] = {}
        self._fallback_counters: dict[str, int] = {}
        self._last_was_list = False
        self.title = ""
        self.has_content = False

    # ── подготовка ──────────────────────────────────────────────────────────
    def _theme_fonts(self) -> dict:
        fonts: dict = {}
        try:
            from docx.opc.constants import RELATIONSHIP_TYPE as RT
            from lxml import etree

            root = etree.fromstring(self.doc.part.part_related_by(RT.THEME).blob)
            for key, tag in (("major", "a:majorFont"), ("minor", "a:minorFont")):
                latin = _find(root, f"a:themeElements/a:fontScheme/{tag}/a:latin")
                if latin is not None and latin.get("typeface"):
                    fonts[key] = latin.get("typeface")
        except Exception:  # noqa: BLE001 — темы может не быть
            pass
        return fonts

    def _source_base_size(self) -> float:
        size = self.styles.run_defaults(self.styles.default_para).get("size")
        return float(size) if size else 10.0

    def story(self) -> list:
        raw: list = []
        env = _Env(max_w=self.page.width, max_h=self.page.height * 0.92, size=self.BASE)
        self._blocks(self.doc.element.body, raw, env)
        return self._finish(raw, top=True)

    def header_footer(self, kind: str) -> str:
        """Текст колонтитула первого раздела одной строкой (без полей с номерами страниц)."""
        try:
            section = self.doc.sections[0]
            part = section.footer if kind == "footer" else section.header
            if part.is_linked_to_previous:
                return ""
            texts = []
            for p in part._element.iter(_qn("w:p")):
                instr = "".join(t.text or "" for t in p.iter(_qn("w:instrText")))
                instr += "".join(f.get(_qn("w:instr")) or "" for f in p.iter(_qn("w:fldSimple")))
                if "PAGE" in instr.upper():
                    continue
                text = " ".join("".join(t.text or "" for t in p.iter(_qn("w:t"))).split())
                if text and text not in texts:
                    texts.append(text)
            return _clean(_short(" · ".join(texts), 220), self.ctx)
        except Exception:  # noqa: BLE001 — колонтитул необязателен
            return ""

    # ── блоки ───────────────────────────────────────────────────────────────
    def _blocks(self, container, out: list, env: _Env) -> None:
        for child in container.iterchildren():
            tag = child.tag
            if tag == _qn("w:p"):
                self._paragraph(child, out, env)
            elif tag == _qn("w:tbl"):
                if env.in_cell or self.table_mode == 2:
                    self._table_as_text(child, out, env)
                else:
                    self._table(child, out, env)
            elif tag == _qn("w:sdt"):
                content = child.find(_qn("w:sdtContent"))
                if content is not None:
                    self._blocks(content, out, env)
            elif tag in (_qn("w:ins"), _qn("w:smartTag"), _qn("w:customXml"), _qn("w:moveTo")):
                self._blocks(child, out, env)

    def _finish(self, raw: list, top: bool) -> list:
        """Заменяет служебные метки на отступы и разрывы страниц, убирая лишние."""
        out: list = []
        blanks = 0
        for item in raw:
            if item is _EMPTY:
                blanks += 1
                if out and blanks <= (2 if top else 1) and not isinstance(out[-1], PageBreak):
                    out.append(Spacer(1, self.BASE * (0.7 if top else 0.35)))
                continue
            blanks = 0
            if item is _PAGE_BREAK:
                if not top:
                    continue
                while out and isinstance(out[-1], Spacer):
                    out.pop()
                if out and not isinstance(out[-1], PageBreak):
                    out.append(PageBreak())
                continue
            out.append(item)
        while out and isinstance(out[-1], (Spacer, PageBreak)):
            out.pop()
        return out

    # ── абзац ───────────────────────────────────────────────────────────────
    def _para_style_id(self, ppr) -> str | None:
        style = ppr.find(_qn("w:pStyle")) if ppr is not None else None
        sid = style.get(_qn("w:val")) if style is not None else None
        return sid if sid in self.styles.by_id else self.styles.default_para

    def _pprop(self, ppr, sid: str | None, tag: str):
        direct = ppr.find(_qn(tag)) if ppr is not None else None
        return direct if direct is not None else self.styles.ppr(sid, tag)

    def _heading_level(self, ppr, sid: str | None) -> int | None:
        """0 — название документа, −1 — подзаголовок, 1…9 — уровень заголовка, None — обычный текст."""
        for name in self.styles.names(sid):
            match = _HEADING_RE.match(name)
            if match:
                return int(match.group(1))
            if name in _TITLE_NAMES:
                return 0
            if name in _SUBTITLE_NAMES:
                return -1
        outline = self._pprop(ppr, sid, "w:outlineLvl")
        if outline is not None:
            level = _int(outline.get(_qn("w:val")), 9)
            if 0 <= level <= 8:
                return level + 1
        return None

    def _list_label(self, ppr, sid: str | None) -> tuple[str, bool, int] | None:
        """→ (метка, это_маркер, уровень) для абзаца-элемента списка."""
        val = _qn("w:val")
        num_pr = ppr.find(_qn("w:numPr")) if ppr is not None else None
        style_num_pr = self.styles.ppr(sid, "w:numPr")
        num_id = ilvl = None
        for source in (num_pr, style_num_pr):
            if source is None:
                continue
            if num_id is None:
                el = source.find(_qn("w:numId"))
                if el is not None:
                    num_id = el.get(val)
            if ilvl is None:
                el = source.find(_qn("w:ilvl"))
                if el is not None:
                    ilvl = _int(el.get(val), 0)
        level = ilvl or 0
        if num_id is not None:
            if num_id == "0":      # numId=0 — нумерация у абзаца явно снята
                return None
            label = self.numbering.label(num_id, level)
            if label is not None:
                depth = level
                for name in self.styles.names(sid):      # «List Bullet 2» — второй уровень при ilvl = 0
                    match = _BULLET_STYLE_RE.match(name) or _NUMBER_STYLE_RE.match(name)
                    if match:
                        if match.group(1):
                            depth = max(depth, int(match.group(1)) - 1)
                        break
                return label[0], label[1], depth
        # нумерацию разобрать не удалось — определяем по названию стиля
        for name in self.styles.names(sid):
            match = _BULLET_STYLE_RE.match(name)
            if match:
                depth = int(match.group(1)) - 1 if match.group(1) else level
                return ("•" if depth == 0 else "–"), True, depth
            match = _NUMBER_STYLE_RE.match(name)
            if match:
                depth = int(match.group(1)) - 1 if match.group(1) else level
                key = f"{sid}:{depth}"
                if not self._last_was_list:
                    self._fallback_counters.pop(key, None)
                self._fallback_counters[key] = self._fallback_counters.get(key, 0) + 1
                return f"{self._fallback_counters[key]}.", False, depth
        if num_id is not None:
            return "•", True, level
        return None

    def _paragraph(self, p, out: list, env: _Env) -> None:
        ppr = p.find(_qn("w:pPr"))
        sid = self._para_style_id(ppr)
        heading = self._heading_level(ppr, sid)
        label = self._list_label(ppr, sid)
        defaults = self.styles.run_defaults(sid)
        val = _qn("w:val")

        if _w_on(ppr.find(_qn("w:pageBreakBefore")) if ppr is not None else None) and not env.in_cell:
            out.append(_PAGE_BREAK)

        # кегль и оформление абзаца
        if heading is not None and not env.in_cell:
            size = self.HEADING_SIZES.get(heading, 13.0 if heading == -1 else self.BASE)
            bold = heading != -1
        else:
            ratio = (defaults.get("size") or self.src_base) / self.src_base
            size = env.size * max(0.75, min(1.8, ratio))
            bold = env.bold or (heading is not None)

        tokens: list = []
        base = dict(defaults)
        if bold:
            base["bold"] = True
        self._inline(p, tokens, base, size, heading is not None and not env.in_cell, env)

        jc = self._pprop(ppr, sid, "w:jc")
        align = {"center": TA_CENTER, "right": TA_RIGHT, "end": TA_RIGHT, "both": TA_JUSTIFY,
                 "distribute": TA_JUSTIFY}.get((jc.get(val) if jc is not None else "") or "", TA_LEFT)
        left = first = 0.0
        ind = self._pprop(ppr, sid, "w:ind")
        if ind is not None and label is None:
            left = _int(ind.get(_qn("w:left")) or ind.get(_qn("w:start")), 0) / 20.0
            first = _int(ind.get(_qn("w:firstLine")), 0) / 20.0 - _int(ind.get(_qn("w:hanging")), 0) / 20.0
            left = max(0.0, min(left, env.max_w * 0.4))
            first = max(-left, min(first, 42.0))
        shade = None
        shd = ppr.find(_qn("w:shd")) if ppr is not None else None
        if shd is not None:
            shade = _hex_rgb(shd.get(_qn("w:fill")))

        text_seen = False
        label_pending = label
        buffer: list[str] = []
        sizes: list[float] = []

        def flush() -> None:
            nonlocal text_seen, label_pending, buffer, sizes
            markup = "".join(buffer)
            biggest = max(sizes) if sizes else size
            buffer, sizes = [], []
            if not _plain(markup) and not (label_pending and not text_seen):
                return
            if not _plain(markup) and label_pending and label_pending[1]:
                return      # пустой элемент маркированного списка
            bullet = None
            bullet_indent = None
            indent_left, indent_first = left, first
            step = 14.0 if env.in_cell else 20.0
            if label_pending is not None:
                text, is_bullet, depth = label_pending
                label_pending = None
                if heading is not None and not env.in_cell:
                    if not is_bullet and text:      # номер раздела — тем же шрифтом, что и заголовок
                        markup = self._wrap(text + "\u00a0", base, size, size, env) + markup
                else:
                    bullet = _clean(text, self.ctx)
                    label_w = _text_width(bullet, SANS_BOLD if bold else SANS, size) if bullet else 0.0
                    bullet_indent = step * depth + (4.0 if is_bullet else 0.0)
                    # reportlab ставит текст не ближе 0,6 кегля от метки — продолжение строк выравниваем так же
                    indent_left = max(step * (depth + 1), bullet_indent + label_w + size * 0.62)
                    indent_first = 0.0
            elif label is not None and heading is None:
                # продолжение элемента списка после рисунка или разрыва — тот же отступ, без метки
                indent_left = step * (label[2] + 1)
                indent_first = 0.0
            style = self._style(heading if not env.in_cell else None, size, biggest, align,
                                indent_left, indent_first, bullet_indent, bold, shade, env)
            out.append(Paragraph(markup, style, bulletText=bullet))
            text_seen = True
            self.has_content = True
            if not self.title and not env.in_cell:
                self.title = _short(_plain(markup), 90)

        for token in tokens:
            if token[0] == "m":
                buffer.append(token[1])
                sizes.append(token[2])
            elif token[0] == "flow":
                flush()
                out.extend(token[1])
                text_seen = True
                self.has_content = True
            elif token[0] == "break":
                flush()
                if not env.in_cell:
                    out.append(_PAGE_BREAK)
        flush()
        if not text_seen:
            out.append(_EMPTY)
        self._last_was_list = label is not None

        # разрыв раздела «со следующей страницы» в конце абзаца
        sect = ppr.find(_qn("w:sectPr")) if ppr is not None else None
        if sect is not None and not env.in_cell:
            kind = sect.find(_qn("w:type"))
            if kind is None or (kind.get(val) or "nextPage") != "continuous":
                out.append(_PAGE_BREAK)

    def _style(self, heading, size: float, biggest: float, align: int, left: float, first: float,
               bullet_indent: float | None, bold: bool, shade, env: _Env) -> ParagraphStyle:
        lead_size = max(size, biggest)
        is_list = bullet_indent is not None
        key = (heading, round(size, 1), round(lead_size, 1), align, round(left, 1), round(first, 1),
               None if bullet_indent is None else round(bullet_indent, 1), bold, shade, env.in_cell)
        style = self._style_cache.get(key)
        if style is not None:
            return style
        before, after, keep = 0.0, size * 0.5, 0
        if env.in_cell:
            after = 2.0
        elif heading is not None:
            before = {0: 4.0, 1: 16.0, 2: 13.0, 3: 10.0}.get(heading, 8.0)
            after = {0: 10.0, 1: 8.0, 2: 6.0}.get(heading, 5.0)
            keep = 1
        elif is_list:
            after = size * 0.28
        leading = lead_size * (1.22 if heading is not None and not env.in_cell else
                               (1.28 if env.in_cell else self.LEADING))
        style = ParagraphStyle(
            f"docx-{len(self._style_cache)}",
            fontName=SANS, fontSize=size, leading=leading, alignment=align,
            leftIndent=left, firstLineIndent=first, spaceBefore=before, spaceAfter=after,
            bulletFontName=SANS_BOLD if bold else SANS, bulletFontSize=size, bulletIndent=bullet_indent or 0.0,
            textColor=_rl_color(_INK), keepWithNext=keep, splitLongWords=1,
            backColor=_rl_color(shade) if shade else None,
            borderPadding=(2, 3, 2, 3) if shade else 0,
        )
        self._style_cache[key] = style
        return style

    # ── текст внутри абзаца ───────────────────────────────────────────────────
    def _inline(self, container, tokens: list, base: dict, para_size: float, fixed_size: bool, env: _Env) -> None:
        for child in container.iterchildren():
            tag = child.tag
            if tag == _qn("w:r"):
                self._run(child, tokens, base, para_size, fixed_size, env)
            elif tag in (_qn("w:hyperlink"), _qn("w:ins"), _qn("w:smartTag"), _qn("w:fldSimple"),
                         _qn("w:sdt"), _qn("w:sdtContent"), _qn("w:customXml"), _qn("w:moveTo")):
                self._inline(child, tokens, base, para_size, fixed_size, env)
            elif tag == _qn("mc:AlternateContent"):
                # берём основной вариант, а запасной — только если основного нет (иначе текст задвоится)
                holder = child.find(_qn("mc:Choice"))
                if holder is None:
                    holder = child.find(_qn("mc:Fallback"))
                if holder is not None:
                    self._inline(holder, tokens, base, para_size, fixed_size, env)
            elif tag in (_qn("m:oMath"), _qn("m:oMathPara")):
                formula = "".join(t.text or "" for t in child.iter(_qn("m:t")))
                if formula.strip():
                    tokens.append(("m", _markup(formula, self.ctx), para_size))

    def _run(self, r, tokens: list, base: dict, para_size: float, fixed_size: bool, env: _Env) -> None:
        direct = self.styles.parse_rpr(r.find(_qn("w:rPr")))
        fmt = dict(base)
        if direct.get("rstyle"):
            fmt.update(self.styles.char_style(direct["rstyle"]))
        if base.get("bold") and env.bold:
            direct.pop("bold", None)
        fmt.update(direct)
        if fmt.get("hidden"):
            return
        size = para_size
        explicit = direct.get("size")
        if not explicit and direct.get("rstyle"):
            explicit = self.styles.char_style(direct["rstyle"]).get("size")
        if explicit and not fixed_size:
            size = env.size * max(0.72, min(2.2, explicit / self.src_base))
        buffer: list[str] = []

        def flush() -> None:
            if buffer:
                text = "".join(buffer)
                buffer.clear()
                piece = self._wrap(text, fmt, size, para_size, env)
                if piece:
                    tokens.append(("m", piece, size))

        for child in r.iterchildren():
            tag = child.tag
            if tag == _qn("w:t"):
                buffer.append(child.text or "")
            elif tag == _qn("w:tab"):
                buffer.append("\t")
            elif tag == _qn("w:br"):
                if child.get(_qn("w:type")) == "page":
                    flush()
                    tokens.append(("break",))
                else:
                    buffer.append("\n")
            elif tag == _qn("w:cr"):
                buffer.append("\n")
            elif tag == _qn("w:noBreakHyphen"):
                buffer.append("\u2011")
            elif tag == _qn("w:sym"):
                try:
                    code = int(child.get(_qn("w:char")) or "F0B7", 16)
                except ValueError:
                    code = 0xF0B7
                buffer.append(chr(code if code >= 0xF000 else 0xF000 + (code & 0xFF)))
            elif tag in (_qn("w:drawing"), _qn("w:pict"), _qn("w:object"), _qn("mc:AlternateContent")):
                flush()
                flow = self._drawing(child, env)
                if flow:
                    tokens.append(("flow", flow))
            elif tag in (_qn("w:footnoteReference"), _qn("w:endnoteReference")):
                self.ctx.warn("Сноски не перенесены в PDF-копию.")
        flush()

    def _wrap(self, text: str, fmt: dict, size: float, para_size: float, env: _Env) -> str:
        if fmt.get("caps"):
            text = text.upper()
        italic = bool(fmt.get("italic"))
        serif = bool(fmt.get("serif")) and not italic      # у DejaVu Serif нет курсива — берём наклонный Sans
        markup = _markup(text, self.ctx, serif=serif)
        if not markup:
            return ""
        if fmt.get("vert"):
            markup = f"<{fmt['vert']}>{markup}</{fmt['vert']}>"
        if fmt.get("strike"):
            markup = f"<strike>{markup}</strike>"
        if fmt.get("underline"):
            markup = f"<u>{markup}</u>"
        if italic:
            markup = f"<i>{markup}</i>"
        if fmt.get("bold"):
            markup = f"<b>{markup}</b>"
        attrs = []
        if serif:
            attrs.append(f'name="{SERIF}"')
        if abs(size - para_size) > 0.25:
            attrs.append(f'size="{size:.1f}"')
        color = fmt.get("color")
        if color is not None:
            if env.dark:
                color = _readable_on(color, (0.2, 0.2, 0.2))
            elif _lum(color) > 0.8:
                color = None          # светлый текст на белом листе не виден
            if color is not None and color != (0.0, 0.0, 0.0):
                attrs.append(f'color="{_rgb_hex(color)}"')
        elif env.dark:
            attrs.append('color="#FFFFFF"')
        if attrs:
            markup = f"<font {' '.join(attrs)}>{markup}</font>"
        return markup

    # ── рисунки, надписи, диаграммы ────────────────────────────────────────────
    @staticmethod
    def _iter_real(el, tags: tuple):
        """Обходит потомков, пропуская запасные копии (mc:Fallback), чтобы не получить дубль."""
        for child in el.iterchildren():
            if child.tag == _qn("mc:Fallback"):
                if child.getparent() is not None and child.getparent().find(_qn("mc:Choice")) is not None:
                    continue
            if child.tag in tags:
                yield child
                continue
            yield from _Docx._iter_real(child, tags)

    def _drawing(self, el, env: _Env) -> list:
        flow: list = []
        tags = (_qn("a:blip"), _qn("v:imagedata"), _qn("w:txbxContent"), _qn("a:graphicData"))
        extents = [e for e in el.iter(_qn("wp:extent"))]
        width = height = 0.0
        if extents:
            width = _int(extents[0].get("cx"), 0) / 12700.0
            height = _int(extents[0].get("cy"), 0) / 12700.0
        pending_blocks: list = []
        for node in self._iter_real(el, tags):
            if node.tag == _qn("a:graphicData"):
                uri = node.get("uri") or ""
                if uri.endswith("/chart"):
                    self.ctx.warn("Диаграммы не перенесены.")
                    flow.append(self._note("[Диаграмма — см. исходный файл]", env))
                elif uri.endswith("/diagram"):
                    self.ctx.warn("Схемы SmartArt не перенесены.")
                    flow.append(self._note("[Схема SmartArt — см. исходный файл]", env))
                else:
                    for inner in self._iter_real(node, tags[:3]):
                        self._drawing_node(inner, width, height, env, flow, pending_blocks)
            else:
                self._drawing_node(node, width, height, env, flow, pending_blocks)
        flow.extend(pending_blocks)
        return flow

    def _drawing_node(self, node, width: float, height: float, env: _Env, flow: list, blocks: list) -> None:
        if node.tag == _qn("w:txbxContent"):
            raw: list = []
            self._blocks(node, raw, env)
            blocks.extend(self._finish(raw, top=False))
            return
        if node.tag == _qn("a:blip"):
            rid = node.get(_qn("r:embed")) or node.get(_qn("r:link"))
        else:
            rid = node.get(_qn("r:id"))
            width = height = 0.0
        if not rid:
            return
        image = self._image(rid, width, height, env)
        if image is not None:
            flow.append(image)

    def _note(self, text: str, env: _Env) -> Paragraph:
        style = self._style_cache.get(("note", env.in_cell))
        if style is None:
            style = ParagraphStyle("docx-note", fontName=SANS_ITALIC, fontSize=9, leading=12,
                                   textColor=colors.Color(0.4, 0.4, 0.4), spaceAfter=4)
            self._style_cache[("note", env.in_cell)] = style
        return Paragraph(_markup(text, self.ctx), style)

    def _image(self, rid: str, width: float, height: float, env: _Env):
        try:
            blob = self.doc.part.related_parts[rid].blob
        except Exception:  # noqa: BLE001 — внешняя ссылка или битая связь
            self.ctx.warn("Рисунки, вставленные ссылкой на внешний файл, не перенесены.")
            return None
        shown_w = min(width, env.max_w) if width > 0 else env.max_w
        loaded = _load_image(blob, shown_w)
        if loaded is None:
            self.ctx.warn("Рисунки в формате EMF/WMF (или повреждённые) не перенесены.")
            return self._note("[Рисунок в формате, который нельзя показать в PDF, — см. исходный файл]", env)
        data, (px_w, px_h) = loaded
        if width <= 0 or height <= 0:
            width, height = px_w * 0.75, px_h * 0.75
        scale = min(1.0, env.max_w / width, env.max_h / height)
        image = RLImage(data, width=width * scale, height=height * scale)
        image.hAlign = "CENTER" if width * scale > env.max_w * 0.5 else "LEFT"
        image.spaceBefore = 3
        image.spaceAfter = 5
        return image

    # ── таблицы ─────────────────────────────────────────────────────────────
    def _rows(self, tbl) -> list:
        rows = []
        for child in tbl.iterchildren():
            if child.tag == _qn("w:tr"):
                rows.append(child)
            elif child.tag in (_qn("w:sdt"), _qn("w:ins")):
                rows.extend(child.iter(_qn("w:tr")))
        return rows

    @staticmethod
    def _cells(tr) -> list:
        cells = []
        for child in tr.iterchildren():
            if child.tag == _qn("w:tc"):
                cells.append(child)
            elif child.tag == _qn("w:sdt"):
                content = child.find(_qn("w:sdtContent"))
                if content is not None:
                    cells.extend(c for c in content.iterchildren() if c.tag == _qn("w:tc"))
        return cells

    def _table_as_text(self, tbl, out: list, env: _Env) -> None:
        """Вложенная (или слишком сложная) таблица — строками текста «ячейка | ячейка»."""
        for tr in self._rows(tbl):
            parts = []
            for tc in self._cells(tr):
                text = " ".join(" ".join(t.text or "" for t in tc.iter(_qn("w:t"))).split())
                if text:
                    parts.append(_markup(text, self.ctx))
            if parts:
                style = self._style(None, env.size, env.size, TA_LEFT, 0.0, 0.0, None, False, None, env)
                out.append(Paragraph(" &nbsp;|&nbsp; ".join(parts), style))
                self.has_content = True

    def _has_borders(self, tbl, rows: list) -> bool:
        val = _qn("w:val")

        def visible(borders) -> bool | None:
            if borders is None:
                return None
            return any((b.get(val) or "none").lower() not in ("none", "nil") for b in borders)

        tbl_pr = tbl.find(_qn("w:tblPr"))
        state = visible(_find(tbl_pr, "w:tblBorders"))
        if state is None and tbl_pr is not None:
            style = tbl_pr.find(_qn("w:tblStyle"))
            for st in self.styles.chain(style.get(val) if style is not None else None):
                state = visible(_find(st, "w:tblPr/w:tblBorders"))
                if state is not None:
                    break
        if state:
            return True
        for tr in rows[:6]:
            for tc in self._cells(tr):
                if visible(_find(tc, "w:tcPr/w:tcBorders")):
                    return True
        return False

    def _table(self, tbl, out: list, env: _Env) -> None:
        rows = self._rows(tbl)
        if not rows:
            return
        val = _qn("w:val")
        # 1. раскладка ячеек по сетке
        layout: list[dict[int, dict]] = []
        ncols = 0
        for tr in rows:
            tr_pr = tr.find(_qn("w:trPr"))
            col = _int(_find(tr_pr, "w:gridBefore").get(val), 0) if _find(tr_pr, "w:gridBefore") is not None else 0
            row: dict[int, dict] = {}
            for tc in self._cells(tr):
                tc_pr = tc.find(_qn("w:tcPr"))
                span_el = _find(tc_pr, "w:gridSpan")
                span = max(1, _int(span_el.get(val), 1)) if span_el is not None else 1
                merge_el = _find(tc_pr, "w:vMerge")
                merge = None if merge_el is None else (merge_el.get(val) or "continue")
                row[col] = {"tc": tc, "pr": tc_pr, "span": span, "merge": merge, "rows": 1}
                col += span
            ncols = max(ncols, col)
            layout.append(row)
        if ncols == 0:
            return
        # 2. объединения по вертикали
        anchors: list[dict[int, dict]] = []
        active: dict[int, dict] = {}
        for row in layout:
            kept: dict[int, dict] = {}
            for col, cell in row.items():
                above = active.get(col)
                if cell["merge"] == "continue" and above is not None and above["span"] == cell["span"]:
                    if self.table_mode == 0:
                        above["rows"] += 1
                    continue
                kept[col] = cell
                if cell["merge"] == "restart":
                    active[col] = cell
                else:
                    active.pop(col, None)
            for col in [c for c in active if c not in row]:
                del active[col]
            anchors.append(kept)

        bordered = self._has_borders(tbl, rows)
        data_table = bordered or (ncols >= 3 and len(rows) >= 2)

        # 3. строка заголовка
        header_rows = 0
        for tr in rows:
            if _w_on(_find(tr, "w:trPr/w:tblHeader")):
                header_rows += 1
            else:
                break
        flagged = header_rows > 0
        first_bold = self._row_is_bold(rows[0])
        first_shaded = False
        for cell in anchors[0].values():
            shd = _find(cell["pr"], "w:shd")
            if shd is not None and _hex_rgb(shd.get(_qn("w:fill"))) not in (None, _WHITE):
                first_shaded = True
        if not flagged and data_table and len(rows) >= 2 and (ncols >= 3 or first_bold or first_shaded):
            header_rows = 1
        if header_rows:
            reach = max((c["rows"] for r in anchors[:header_rows] for c in r.values()), default=1)
            header_rows = max(header_rows, reach)
        if header_rows >= len(rows):
            header_rows = 0

        # 4. ширины столбцов и кегль
        cell_size = 10.0 if ncols <= 4 else 9.5 if ncols <= 6 else 9.0 if ncols <= 8 else 8.0
        pad_h, pad_v = 4.0, 3.0
        widths, cell_size = self._column_widths(tbl, rows, anchors, ncols, env, data_table, header_rows,
                                                cell_size, 2 * pad_h + 1.0)

        # 5. содержимое
        data = [["" for _ in range(ncols)] for _ in rows]
        commands: list = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), pad_h), ("RIGHTPADDING", (0, 0), (-1, -1), pad_h),
            ("TOPPADDING", (0, 0), (-1, -1), pad_v), ("BOTTOMPADDING", (0, 0), (-1, -1), pad_v),
        ]
        if data_table:
            commands.append(("GRID", (0, 0), (-1, -1), 0.5, colors.Color(0.55, 0.57, 0.6)))
        row_chunk = 12      # reportlab не разрывает объединение строк между страницами: длинное режем на куски
        for r_index, row in enumerate(anchors):
            for col, cell in row.items():
                last_col = min(ncols - 1, col + cell["span"] - 1)
                last_row = min(len(rows) - 1, r_index + cell["rows"] - 1)
                shade = None
                shd = _find(cell["pr"], "w:shd")
                if shd is not None:
                    shade = _hex_rgb(shd.get(_qn("w:fill")))
                is_header = r_index < header_rows
                if shade is None and is_header and not (flagged and not data_table):
                    shade = (0.89, 0.91, 0.94)
                v_align = _find(cell["pr"], "w:vAlign")
                v_align = (v_align.get(val) or "") if v_align is not None else ""
                inner_w = sum(widths[col:last_col + 1]) - 2 * pad_h
                cell_env = _Env(max_w=max(20.0, inner_w), max_h=self.page.height * 0.5, size=cell_size,
                                in_cell=True, bold=is_header, dark=shade is not None and _lum(shade) < 0.45)
                if last_row - r_index + 1 <= row_chunk or is_header:
                    pieces = [(r_index, last_row)]
                else:
                    pieces = [(top, min(last_row, top + row_chunk - 1))
                              for top in range(r_index, last_row + 1, row_chunk)]
                for top, bottom in pieces:
                    if last_col > col or bottom > top:
                        commands.append(("SPAN", (col, top), (last_col, bottom)))
                    if shade is not None and shade != _WHITE:
                        commands.append(("BACKGROUND", (col, top), (last_col, bottom), _rl_color(shade)))
                    if v_align in ("center", "bottom"):
                        commands.append(("VALIGN", (col, top), (last_col, bottom),
                                         "MIDDLE" if v_align == "center" else "BOTTOM"))
                    raw: list = []
                    self._blocks(cell["tc"], raw, cell_env)     # для каждого куска содержимое повторяется
                    content = self._finish(raw, top=False)
                    if content:
                        data[top][col] = content if len(content) > 1 else content[0]
        align_el = _find(tbl, "w:tblPr/w:jc")
        maker = {"center": _FlowTableCenter, "right": _FlowTableRight, "end": _FlowTableRight}.get(
            (align_el.get(val) if align_el is not None else "") or "", _FlowTable)
        table = maker(data, colWidths=widths, repeatRows=header_rows)
        table.setStyle(commands)
        table.spaceBefore = 4
        table.spaceAfter = 8
        out.append(table)
        self.has_content = True

    def _column_widths(self, tbl, rows: list, anchors: list, ncols: int, env: _Env, data_table: bool,
                       header_rows: int, cell_size: float, pad: float) -> tuple[list[float], float]:
        """Ширины столбцов и кегль ячеек: как в документе, но так, чтобы слова не рвались посреди строки."""
        natural: list[float] | None = None
        for tr in rows[:6]:                    # ширины ячеек первой строки без объединений
            cells = self._cells(tr)
            if len(cells) != ncols:
                continue
            found = []
            for tc in cells:
                tc_w = _find(tc, "w:tcPr/w:tcW")
                value = _int(tc_w.get(_qn("w:w")), 0) if tc_w is not None else 0
                if value <= 0 or (tc_w.get(_qn("w:type")) or "dxa") != "dxa":
                    found = []
                    break
                found.append(value / 20.0)
            if found:
                natural = found
            break
        if natural is None:
            grid = [_int(g.get(_qn("w:w")), 0) / 20.0 for g in _findall(tbl, "w:tblGrid/w:gridCol")]
            if len(grid) == ncols and all(w > 0 for w in grid):
                natural = grid
        total = sum(natural) if natural else 0.0
        full = env.max_w
        if not data_table:                     # таблица без рамок — обычно блок подписей: как в документе
            if not natural:
                return [full / ncols] * ncols, cell_size
            factor = min(1.0, full / total)
            return [w * factor for w in natural], cell_size

        # замеры текста при кегле 1 (ширина строки пропорциональна кеглю)
        words = [0.0] * ncols
        lines = [0.0] * ncols
        body: list[list[float]] = [[] for _ in range(ncols)]
        serif = bool(self.styles.run_defaults(self.styles.default_para).get("serif"))
        regular, heavy = (SERIF, SERIF_BOLD) if serif else (SANS, SANS_BOLD)
        for r_index, row in enumerate(anchors):
            bold_row = r_index < header_rows or self._row_is_bold(rows[r_index])
            face = heavy if bold_row else regular
            for col, cell in row.items():
                if cell["span"] != 1:
                    continue
                longest = 0.0
                for p in cell["tc"].iter(_qn("w:p")):
                    text = _clean("".join(t.text or "" for t in p.iter(_qn("w:t"))), self.ctx)
                    if not text.strip():
                        continue
                    longest = max(longest, _text_width(text, face, 1.0))
                    for word in text.split():
                        words[col] = max(words[col], _text_width(word, face, 1.0))
                lines[col] = max(lines[col], longest)
                body[col].append(longest)
        # если самые длинные слова не помещаются по ширине — уменьшаем кегль таблицы (не мельче 7,5)
        size = cell_size
        soft: list[float] = []
        while True:
            soft = [min(full * 0.5, max(20.0, w * size * 1.03 + pad)) for w in words]
            if sum(soft) <= full or size <= 7.5:
                break
            size -= 0.5
        pref = [max(s, min(full, l * size + pad)) for s, l in zip(soft, lines)]
        tight = [min(s, 24.0) for s in soft]
        typical = [_percentile(b, 0.85) * size for b in body]
        uniform = not natural or max(natural) <= min(natural) * 1.05
        if uniform:
            # ширины не настраивали (так создаёт таблицы python-docx) — подбираем по содержимому
            if sum(soft) > full:
                return _squeeze(soft, tight, full), size
            widths, _depth = _distribute(soft, pref, typical, pad, full)
            if sum(widths) < full and (not natural or total >= full * 0.72):
                factor = full / sum(widths)
                widths = [w * factor for w in widths]
            return widths, size
        target = full if total > full * 0.72 else total
        widths = [w * target / total for w in natural]
        if sum(soft) <= target:
            # ширины автора рассчитаны на шрифт Word, у нас буквы шире: берём середину между
            # авторскими и подобранными по содержимому, чтобы узкие столбцы не вытягивались в «колбасу»
            fitted, _depth = _distribute(soft, pref, typical, pad, target)
            factor = target / sum(fitted)
            widths = [(w + f * factor) / 2.0 for w, f in zip(widths, fitted)]
        wanted = [max(w, s) for w, s in zip(widths, soft)]
        if sum(wanted) <= full:
            return wanted, size
        return _squeeze(wanted, soft if sum(soft) <= full else tight, full), size

    def _row_is_bold(self, tr) -> bool:
        """Весь текст строки набран полужирным (так обычно оформляют шапку таблицы)."""
        seen = False
        for p in tr.iter(_qn("w:p")):
            ppr = p.find(_qn("w:pPr"))
            defaults = self.styles.run_defaults(self._para_style_id(ppr))
            for r in p.iter(_qn("w:r")):
                if not "".join(t.text or "" for t in r.iter(_qn("w:t"))).strip():
                    continue
                direct = self.styles.parse_rpr(r.find(_qn("w:rPr")))
                fmt = dict(defaults)
                if direct.get("rstyle"):
                    fmt.update(self.styles.char_style(direct["rstyle"]))
                fmt.update(direct)
                if not fmt.get("bold"):
                    return False
                seen = True
        return seen


def _build_docx(src: Path, out: Path, ctx: _Ctx) -> dict:
    import docx

    try:
        document = docx.Document(str(src))
    except Exception as exc:  # noqa: BLE001
        raise _Fail("Не удалось открыть документ DOCX: файл повреждён. Сохраните его заново в Word.") from exc
    wide = False
    try:        # альбомный документ (обычно из-за широких таблиц) остаётся альбомным
        first_section = document.sections[0]
        wide = bool(first_section.page_width and first_section.page_height
                    and first_section.page_width > first_section.page_height)
    except Exception:  # noqa: BLE001
        wide = False
    page = _Page(size=landscape(A4) if wide else A4, margin=18 * mm)
    last_error: Exception | None = None
    for mode in (0, 1, 2):
        attempt = _Ctx()
        converter = _Docx(document, attempt, page, table_mode=mode)
        story = converter.story()
        if not converter.has_content:
            raise _Fail("Документ пустой: в нём нет ни текста, ни таблиц, ни рисунков.")
        header = converter.header_footer("header")
        footer = converter.header_footer("footer")
        page.header_room = 5 * mm if header else 0.0
        try:
            _build_flow_pdf(out, story, page, converter.title or src.stem, footer=footer, header=header)
        except Exception as exc:  # noqa: BLE001 — LayoutError или сбой внутри reportlab на слишком высокой ячейке
            if mode == 0 and not isinstance(exc, LayoutError):
                log.warning("DOCX %s: reportlab не смог разместить содержимое: %r", src, exc)
            last_error = exc
            log.info("DOCX %s: таблица не поместилась на страницу, упрощаю (режим %s)", src, mode + 1)
            continue
        for text in attempt.warnings:
            ctx.warn(text)
        if mode:
            ctx.warn("Сложные таблицы показаны упрощённо: объединённые ячейки не поместились на страницу.")
        return {}
    raise _Fail(
        "Не удалось разместить содержимое документа на страницах PDF: слишком крупная таблица или рисунок."
    ) from last_error


# ════════════════════════════════════════════════════════════════════════════
#  XLSX
# ════════════════════════════════════════════════════════════════════════════

_XL_MAX_ROWS = 2000            # строк одного листа в PDF-копии (дальше — предупреждение)
_XL_FLOOR = 14 * mm            # уже этого столбец делаем, только если его содержимому хватает меньшего
_XL_NARROW = 8 * mm
_XL_SOFT_CAP = 36 * mm         # слова длиннее переносятся по буквам
_XL_PREF_CAP = 80 * mm
_XL_PAD = 3.0
_XL_FONTS = (
    (SANS, SANS_BOLD, 9.0),
    (SANS, SANS_BOLD, 8.0),
    (CONDENSED, CONDENSED_BOLD, 8.0),
    (CONDENSED, CONDENSED_BOLD, 7.5),
    (CONDENSED, CONDENSED_BOLD, 7.0),
)
_XL_GROUP_FONT = 2             # кегль для листов, которые приходится делить на группы столбцов
_XL_FORMULA_WARNING = (
    "Формулы без сохранённых значений показаны как текст: откройте книгу в Excel и сохраните её заново."
)


def _squeeze(wanted: list[float], floors: list[float], avail: float) -> list[float]:
    """Сжимает ширины до суммы avail, не опуская ни одну ниже её минимума."""
    count = len(wanted)
    widths = list(wanted)
    locked = [False] * count
    for _ in range(count + 1):
        free = [i for i in range(count) if not locked[i]]
        free_sum = sum(wanted[i] for i in free)
        if not free or free_sum <= 0:
            break
        ratio = (avail - sum(widths[i] for i in range(count) if locked[i])) / free_sum
        changed = False
        for i in free:
            width = wanted[i] * ratio
            if width < floors[i]:
                widths[i] = floors[i]
                locked[i] = True
                changed = True
            else:
                widths[i] = width
        if not changed:
            break
    return widths


def _distribute(soft: list[float], pref: list[float], typical: list[float], pad: float,
                avail: float, wish: list[float] | None = None) -> tuple[list[float], float]:
    """Ширины столбцов между soft (самое длинное слово) и pref (весь текст в одну строку).

    Место делится так, чтобы «типичная» ячейка (typical — ширина её текста) в каждом столбце
    занимала одинаковое число строк: так таблица получается самой низкой. Сумма soft ≤ avail.
    Если место ещё остаётся, столбцы расширяются до wish (ширины, заданные автором файла).
    → (ширины, число строк типичной ячейки).
    """
    def at(lines: float) -> list[float]:
        return [min(p, max(s, t / lines + pad)) for s, p, t in zip(soft, pref, typical)]

    lines = 1.0
    widths = at(lines)
    if sum(widths) > avail:
        low, high = 1.0, 400.0
        for _ in range(48):
            middle = (low + high) / 2.0
            if sum(at(middle)) > avail:
                low = middle
            else:
                high = middle
        lines = high
        widths = at(lines)
    for target in (pref, wish):
        spare = avail - sum(widths)
        if target is None or spare <= 0.01:
            continue
        want = [max(0.0, t - w) for t, w in zip(target, widths)]
        need = sum(want)
        if need > 0:
            share = min(1.0, spare / need)
            widths = [w + x * share for w, x in zip(widths, want)]
    return widths, lines


def _percentile(values: list[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))]


# ── значения ячеек так, как их ждёт русскоязычный читатель ────────────────────────

_XL_TOKEN = re.compile(r'"([^"]*)"|\[([^\]]*)\]|\\(.)|_.|\*.')
_XL_DIGITS = re.compile(r"[0#?][0#?,.]*")


def _xl_first_section(fmt: str) -> str:
    quoted = False
    for index, ch in enumerate(fmt):
        if ch == '"':
            quoted = not quoted
        elif ch == ";" and not quoted:
            return fmt[:index]
    return fmt


def _xl_general(value) -> str:
    if isinstance(value, int):
        return str(value)
    if value != value or value in (float("inf"), float("-inf")):
        return str(value)
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    text = f"{value:.10g}"
    if "e" in text:
        mantissa, _, exponent = text.partition("e")
        return mantissa.replace(".", ",") + "E" + exponent
    return text.replace(".", ",")


def _xl_number(value, fmt: str) -> str:
    literals: list[str] = []

    def keep(match) -> str:
        if match.group(1) is not None:
            literals.append(match.group(1))
        elif match.group(2) is not None:
            inner = match.group(2)
            if inner.startswith("$"):
                literals.append(inner[1:].split("-")[0])
        elif match.group(3) is not None:
            literals.append(match.group(3))
        return ""

    core = _XL_TOKEN.sub(keep, _xl_first_section(fmt or "General"))
    pattern = _XL_DIGITS.search(core)
    if pattern is None:
        return _xl_general(value)
    digits = pattern.group(0)
    literals.insert(0, core[: pattern.start()])
    literals.append(core[pattern.end():])
    percent = "%" in core
    scale = len(digits) - len(digits.rstrip(","))
    digits = digits.rstrip(",")
    whole, _, frac = digits.partition(".")
    max_dec = sum(1 for ch in frac if ch in "0#?")
    min_dec = frac.count("0")
    grouping = "," in whole
    number = float(value) * (100.0 if percent else 1.0) / (1000.0 ** scale)
    if "E+" in core.upper() or "E-" in core.upper():
        return f"{number:.{max_dec}E}".replace(".", ",")
    text = f"{abs(number):.{max_dec}f}"
    int_part, _, dec_part = text.partition(".")
    while len(dec_part) > min_dec and dec_part.endswith("0"):
        dec_part = dec_part[:-1]
    if grouping and len(int_part) > 3:
        int_part = " ".join(_chunks_from_right(int_part, 3))
    text = int_part + ("," + dec_part if dec_part else "")
    if number < 0 and any(ch in "123456789" for ch in text):
        text = "-" + text
    if percent:
        return text + " %"
    unit = "".join(literals).replace("%", "").strip(" -+()/")
    return f"{text} {unit}" if unit else text


def _chunks_from_right(text: str, size: int) -> list[str]:
    head = len(text) % size
    parts = [text[:head]] if head else []
    parts.extend(text[i:i + size] for i in range(head, len(text), size))
    return parts


def _xl_datetime(value) -> str:
    if isinstance(value, _dt.datetime):
        text = f"{value.day:02d}.{value.month:02d}.{value.year:04d}"
        if value.hour or value.minute or value.second:
            text += f" {value.hour:02d}:{value.minute:02d}"
            if value.second:
                text += f":{value.second:02d}"
        return text
    if isinstance(value, _dt.date):
        return f"{value.day:02d}.{value.month:02d}.{value.year:04d}"
    if isinstance(value, _dt.time):
        return f"{value.hour:02d}:{value.minute:02d}" + (f":{value.second:02d}" if value.second else "")
    total = int(round(value.total_seconds()))
    hours, rest = divmod(abs(total), 3600)
    minutes, seconds = divmod(rest, 60)
    return ("-" if total < 0 else "") + f"{hours}:{minutes:02d}" + (f":{seconds:02d}" if seconds else "")


def _xl_text(value, fmt: str | None) -> tuple[str, str]:
    """Значение ячейки → (текст, вид: text / num / date / bool)."""
    if value is None:
        return "", "text"
    if isinstance(value, bool):
        return ("Да" if value else "Нет"), "bool"
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time, _dt.timedelta)):
        return _xl_datetime(value), "date"
    if isinstance(value, (int, float)):
        try:
            return _xl_number(value, fmt or "General"), "num"
        except Exception:  # noqa: BLE001 — экзотический формат числа
            return _xl_general(value), "num"
    return str(value), "text"


def _xl_theme_colors(book) -> list:
    """Цвета темы книги в порядке индексов Excel: lt1, dk1, lt2, dk2, accent1…6."""
    try:
        from lxml import etree

        root = etree.fromstring(book.loaded_theme)
        scheme = _find(root, "a:themeElements/a:clrScheme")
        found = {}
        for child in scheme:
            name = child.tag.rpartition("}")[2]
            inner = child[0] if len(child) else None
            if inner is not None:
                found[name] = _hex_rgb(inner.get("lastClr") or inner.get("val"))
        order = ("lt1", "dk1", "lt2", "dk2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6",
                 "hlink", "folHlink")
        return [found.get(name) for name in order]
    except Exception:  # noqa: BLE001 — темы нет или она не читается
        return []


def _xl_color(color, theme: list) -> tuple | None:
    """Цвет openpyxl (RGB, индекс палитры или цвет темы с оттенком) → (r, g, b)."""
    if color is None:
        return None
    try:
        kind = color.type
        if kind == "rgb":
            return _hex_rgb(color.rgb)
        if kind == "indexed":
            from openpyxl.styles.colors import COLOR_INDEX

            index = color.indexed
            return _hex_rgb(COLOR_INDEX[index]) if 0 <= index < len(COLOR_INDEX) else None
        if kind == "theme":
            index = color.theme
            rgb = theme[index] if 0 <= index < len(theme) else None
            if rgb is None:
                return None
            tint = float(color.tint or 0.0)
            if tint:
                hue, light, sat = colorsys.rgb_to_hls(*rgb)
                light = light * (1.0 + tint) if tint < 0 else light * (1.0 - tint) + tint
                rgb = colorsys.hls_to_rgb(hue, max(0.0, min(1.0, light)), sat)
            return rgb
    except Exception:  # noqa: BLE001
        return None
    return None


@dataclass
class _XlCell:
    text: str
    kind: str = "text"
    bold: bool = False
    align: str | None = None          # выравнивание, явно заданное в файле: l / c / r
    fill: tuple | None = None
    color: tuple | None = None


@dataclass
class _XlSheet:
    title: str
    letters: list[str]                 # буквы столбцов Excel по позициям сетки
    excel_widths: list[float | None]   # ширины из файла в «символах» Excel
    nrows: int
    cells: dict[tuple[int, int], _XlCell]
    spans: list[tuple[int, int, int, int]]   # r0, c0, r1, c1 в координатах сетки
    titles: list[str]
    header_rows: int
    header_texts: list[str]
    blank_rows: set[int]


def _xl_analyse(ws_f, ws_v, ctx: _Ctx, theme: list | None = None) -> _XlSheet | None:
    """Разбирает лист: рабочая область, заголовок, шапка, объединения. None — лист пустой.

    ws_f — лист книги с формулами, ws_v — тот же лист с сохранёнными значениями формул.
    """
    from openpyxl.utils import column_index_from_string, get_column_letter

    theme = theme or []
    same = ws_f is ws_v
    hidden_rows = {index for index, dim in ws_f.row_dimensions.items() if dim.hidden}
    hidden_cols: set[int] = set()
    file_widths: dict[int, float] = {}
    for dim in ws_f.column_dimensions.values():
        try:
            low = dim.min or column_index_from_string(dim.index)
            high = min(dim.max or low, low + 512)
        except Exception:  # noqa: BLE001
            continue
        for index in range(low, high + 1):
            if dim.hidden:
                hidden_cols.add(index)
            if dim.width:
                file_widths[index] = float(dim.width)

    values: dict[tuple[int, int], object] = {}
    hidden_data = False
    # Берём только существующие ячейки: iter_rows() обходит весь прямоугольник листа, а одна
    # случайно отформатированная ячейка в миллионной строке сделала бы его необъятным.
    stored = getattr(ws_f, "_cells", None)
    existing = stored.values() if isinstance(stored, dict) else (cell for row in ws_f.iter_rows() for cell in row)
    for cell in list(existing):
        value = cell.value
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if cell.row in hidden_rows or cell.column in hidden_cols:
            hidden_data = True
            continue
        values[(cell.row, cell.column)] = cell
    if not values:
        return None
    if hidden_data:
        ctx.warn(f"Лист «{ws_f.title}»: скрытые строки и столбцы в PDF не показаны.")

    by_row: dict[int, list[int]] = {}
    for row_no, col_no in values:
        by_row.setdefault(row_no, []).append(col_no)
    value_rows = sorted(by_row)
    all_cols = sorted({col_no for _, col_no in values})
    merged = {(rng.min_row, rng.min_col): rng for rng in ws_f.merged_cells.ranges}

    # строки-заголовки листа над таблицей: одна текстовая ячейка на всю ширину
    titles: list[str] = []
    start = 0
    while start < len(value_rows) - 1 and len(titles) < 3:
        row_no = value_rows[start]
        present = by_row[row_no]
        if len(present) != 1:
            break
        cell = values[(row_no, present[0])]
        if not isinstance(cell.value, str):
            break
        rng = merged.get((row_no, present[0]))
        span = sum(1 for c in all_cols if rng.min_col <= c <= rng.max_col) if rng is not None else 1
        wide = span >= 2 and span >= 0.5 * len(all_cols)
        lone = False
        if len(all_cols) >= 2:
            for ahead in range(start + 1, min(start + 4, len(value_rows))):
                size = len(by_row[value_rows[ahead]])
                if size >= 2:
                    lone = True
                    break
                if size != 1:
                    break
        if not (wide or lone):
            break
        titles.append(_clean(" ".join(cell.value.split()), ctx))
        start += 1

    table_rows = value_rows[start:]
    total_rows = len(table_rows)
    if total_rows > _XL_MAX_ROWS:
        table_rows = table_rows[:_XL_MAX_ROWS]
        ctx.warn(f"Лист «{ws_f.title}»: в PDF показаны первые {_XL_MAX_ROWS} строк из {total_rows}.")
    cols = sorted({c for r in table_rows for c in by_row[r]})
    col_index = {col_no: i for i, col_no in enumerate(cols)}

    # сетка: строки со значениями и не более одной пустой строки-разделителя между ними
    row_index: dict[int, int] = {}
    blank_rows: set[int] = set()
    position = 0
    previous = None
    for row_no in table_rows:
        if previous is not None and row_no - previous > 1:
            if any(r not in hidden_rows for r in range(previous + 1, min(row_no, previous + 50))):
                blank_rows.add(position)
                position += 1
        row_index[row_no] = position
        position += 1
        previous = row_no
    nrows = position

    spans: list[tuple[int, int, int, int]] = []
    for rng in ws_f.merged_cells.ranges:
        if (rng.min_row, rng.min_col) not in values or rng.min_row not in row_index:
            continue
        rows_in = [i for r, i in row_index.items() if rng.min_row <= r <= rng.max_row]
        cols_in = [i for c, i in col_index.items() if rng.min_col <= c <= rng.max_col]
        if rows_in and cols_in and (len(rows_in) > 1 or len(cols_in) > 1):
            spans.append((min(rows_in), min(cols_in), max(rows_in), max(cols_in)))

    header_rows = 1
    for r0, _c0, r1, _c1 in spans:
        if r0 == 0 and r1 > 0:
            header_rows = max(header_rows, r1 + 1)
    header_rows = min(header_rows, 4, nrows)

    cells: dict[tuple[int, int], _XlCell] = {}
    for (row_no, col_no), cell in values.items():
        r = row_index.get(row_no)
        c = col_index.get(col_no)
        if r is None or c is None:
            continue
        value = cell.value
        if not same and cell.data_type == "f":
            cached = ws_v.cell(row=row_no, column=col_no).value
            if cached is None or cached == "":
                formula = value if isinstance(value, str) else str(getattr(value, "text", value) or "")
                text, kind = (formula if formula.startswith("=") else "=" + formula), "text"
                ctx.warn(_XL_FORMULA_WARNING)
            else:
                text, kind = _xl_text(cached, cell.number_format)
        else:
            text, kind = _xl_text(value, cell.number_format)
        text = _clean(text.rstrip(), ctx)
        if not text:
            continue
        item = _XlCell(text=text, kind=kind)
        try:
            item.bold = bool(cell.font is not None and cell.font.b)
            horizontal = cell.alignment.horizontal if cell.alignment is not None else None
            item.align = {"left": "l", "center": "c", "centerContinuous": "c", "right": "r",
                          "justify": "l", "distributed": "c"}.get(horizontal or "")
            fill = cell.fill
            if fill is not None and fill.fill_type == "solid":
                item.fill = _xl_color(fill.fgColor, theme)
            color = _xl_color(cell.font.color, theme) if cell.font is not None else None
            if color is not None and _lum(color) > 0.03:
                item.color = color
        except Exception:  # noqa: BLE001 — оформление необязательно
            pass
        cells[(r, c)] = item

    header_texts = [cells[(r, c)].text for r in range(header_rows) for c in range(len(cols)) if (r, c) in cells]
    return _XlSheet(
        title=_clean(ws_f.title, ctx),
        letters=[get_column_letter(col_no) for col_no in cols],
        excel_widths=[file_widths.get(col_no) for col_no in cols],
        nrows=nrows, cells=cells, spans=spans, titles=titles,
        header_rows=header_rows, header_texts=header_texts, blank_rows=blank_rows,
    )


class _XlLayout:
    """Раскладывает лист по ширине страницы: кегль, ширины столбцов, группы столбцов."""

    def __init__(self, sheet: _XlSheet, avail: float) -> None:
        self.sheet = sheet
        self.avail = avail
        ncols = len(sheet.letters)
        covered: set[tuple[int, int]] = set()
        for r0, c0, r1, c1 in sheet.spans:
            if c1 > c0:
                covered.add((r0, c0))
        # одинокая длинная ячейка в строке «переливается» вправо, как в Excel
        row_counts: dict[int, int] = {}
        for (r, _c) in sheet.cells:
            row_counts[r] = row_counts.get(r, 0) + 1
        self.overflow: set[tuple[int, int]] = set()
        for (r, c), cell in sheet.cells.items():
            if (r >= sheet.header_rows and row_counts[r] == 1 and cell.kind == "text" and c < ncols - 1
                    and (r, c) not in covered and not any(s[0] <= r <= s[2] and s[1] <= c <= s[3] for s in sheet.spans)
                    and len(cell.text) > 24):
                self.overflow.add((r, c))
        skip = covered | self.overflow
        body_rows = max(1, sheet.nrows - sheet.header_rows - len(sheet.blank_rows))
        self._unit: dict[str, tuple[list[float], list[float], list[float], list[float]]] = {}
        for font, bold_font, _size in _XL_FONTS:
            if font in self._unit:
                continue
            words = [0.0] * ncols
            lines = [0.0] * ncols
            body: list[list[float]] = [[] for _ in range(ncols)]
            for (r, c), cell in sheet.cells.items():
                if (r, c) in skip:
                    continue
                face = bold_font if (cell.bold or r < sheet.header_rows) else font
                longest = 0.0
                for line in cell.text.split("\n"):
                    longest = max(longest, _text_width(line, face, 1.0))
                    for word in line.split():
                        words[c] = max(words[c], _text_width(word, face, 1.0))
                lines[c] = max(lines[c], longest)
                if r >= sheet.header_rows:
                    body[c].append(longest)
            # «типичная» ячейка столбца: редко заполненный столбец не должен отбирать ширину у остальных
            typical = [_percentile(body[c], 0.85) * min(1.0, len(body[c]) / body_rows) ** 0.5 for c in range(ncols)]
            self._unit[font] = (words, [max(b, default=0.0) for b in body], typical, lines)

        self.font_index = len(_XL_FONTS) - 1
        self.groups: list[list[int]] = [list(range(ncols))]
        self.widths: list[list[float]] = []
        pad = 2 * _XL_PAD + 1.5
        fitted = None
        for index in range(len(_XL_FONTS)):
            soft, pref, typical, wish = self._needs(index)
            if sum(soft) > avail:
                continue
            widths, depth = _distribute(soft, pref, typical, pad, avail, wish)
            fitted = (index, widths)
            if depth <= 3.0:        # типичная ячейка не длиннее трёх строк — кегль подходит
                break
        if fitted is not None:
            self.font_index, widths = fitted
            self.widths = [widths]
            return
        soft = self._needs(self.font_index)[0]
        floors = [min(s, _XL_FLOOR) for s in soft]
        if sum(floors) <= avail and sum(soft) <= avail * 1.18:
            self.widths = [_squeeze(soft, floors, avail)]     # чуть тесно: часть длинных слов перенесётся
            return
        self._split()

    def _needs(self, index: int) -> tuple[list[float], list[float], list[float], list[float]]:
        """Потребности столбцов при данном кегле.

        → (минимум: самое длинное слово; данные в одну строку; ширина типичного текста;
           пожелание: шапка в одну строку и ширина столбца, заданная в файле).
        """
        font, _bold, size = _XL_FONTS[index]
        words, body_lines, typical, all_lines = self._unit[font]
        pad = 2 * _XL_PAD + 1.5
        soft: list[float] = []
        pref: list[float] = []
        wish: list[float] = []
        for c in range(len(words)):
            minimum = max(_XL_NARROW, min(_XL_SOFT_CAP, words[c] * size + pad))
            single = max(minimum, min(_XL_PREF_CAP, body_lines[c] * size + pad))
            wanted = max(single, min(_XL_PREF_CAP, all_lines[c] * size + pad))
            file_width = self.sheet.excel_widths[c]
            if file_width:
                wanted = max(wanted, min(_XL_PREF_CAP, file_width * 5.25 * size / 11.0))
            soft.append(minimum)
            pref.append(single)
            wish.append(wanted)
        return soft, pref, [t * size for t in typical], wish

    def _split(self) -> None:
        """Столбцы не помещаются на страницу: делим на группы, повторяя первый столбец."""
        self.font_index = _XL_GROUP_FONT
        soft, pref, typical, wish = self._needs(self.font_index)
        pad = 2 * _XL_PAD + 1.5
        ncols = len(soft)
        first = min(max(soft[0], min(pref[0], 45 * mm)), self.avail * 0.3)
        budget = self.avail - first

        def pack(capacity: float) -> list[list[int]]:
            groups: list[list[int]] = []
            current: list[int] = []
            used = 0.0
            for c in range(1, ncols):
                need = min(soft[c], capacity)
                if current and used + need > capacity:
                    groups.append(current)
                    current, used = [], 0.0
                current.append(c)
                used += need
            if current:
                groups.append(current)
            return groups

        groups = pack(budget)
        # выравниваем группы по ширине, не увеличивая их число
        total = sum(min(s, budget) for s in soft[1:])
        for step in range(0, 21):
            capacity = total / len(groups) + (budget - total / len(groups)) * step / 20.0
            candidate = pack(capacity)
            if len(candidate) == len(groups):
                groups = candidate
                break
        self.groups = []
        self.widths = []
        for group in groups:
            g_soft = [min(soft[c], budget) for c in group]
            g_pref = [max(s, min(pref[c], budget)) for s, c in zip(g_soft, group)]
            if sum(g_soft) > budget:
                widths = _squeeze(g_soft, [min(s, _XL_FLOOR) for s in g_soft], budget)
            else:
                g_wish = [max(p, min(wish[c], budget)) for p, c in zip(g_pref, group)]
                widths, _depth = _distribute(g_soft, g_pref, [typical[c] for c in group], pad, budget, g_wish)
            self.groups.append([0] + group)
            self.widths.append([first] + widths)


def _xl_flowables(sheet: _XlSheet, page: _Page, row_chunk: int, header_pages: list) -> list:
    """Лист → заголовок и таблицы (по одной на группу столбцов).

    row_chunk — наибольшая высота объединения строк; header_pages — сюда по ходу сборки PDF
    записываются номера страниц, с которых начинаются таблицы (там искать шапку при проверке).
    """
    layout = _XlLayout(sheet, page.width)
    font, _bold_font, size = _XL_FONTS[layout.font_index]
    styles: dict[tuple, ParagraphStyle] = {}

    def style(align: str, header: bool) -> ParagraphStyle:
        key = (align, header)
        found = styles.get(key)
        if found is None:
            found = ParagraphStyle(
                f"xl-{align}-{int(header)}", fontName=font, fontSize=size, leading=size * 1.25,
                alignment={"l": TA_LEFT, "c": TA_CENTER, "r": TA_RIGHT}[align],
                textColor=_rl_color(_INK), splitLongWords=1,
            )
            styles[key] = found
        return found

    heading = ParagraphStyle("xl-sheet", fontName=SANS_BOLD, fontSize=13, leading=16.5,
                             textColor=_rl_color((0.09, 0.2, 0.36)), spaceAfter=3)
    note = ParagraphStyle("xl-note", fontName=SANS, fontSize=8.5, leading=11,
                          textColor=colors.Color(0.38, 0.4, 0.44), spaceAfter=4)
    title_style = ParagraphStyle("xl-title", fontName=SANS_BOLD, fontSize=10.5, leading=14,
                                 textColor=_rl_color(_INK), spaceAfter=2)
    subtitle_style = ParagraphStyle("xl-subtitle", parent=title_style, fontName=SANS, fontSize=9.5, leading=12.5)

    header_bg = _rl_color((0.88, 0.905, 0.94))
    grid_color = colors.Color(0.6, 0.62, 0.66)
    flow: list = []
    parts = len(layout.groups)
    for part, (group, widths) in enumerate(zip(layout.groups, layout.widths), 1):
        if part > 1:
            flow.append(PageBreak())
        flow.append(_FooterMark(sheet.title, header_pages))
        flow.append(Paragraph(_escape(sheet.title), heading))
        if parts > 1:
            own = group[1:]
            first_letter, last_letter = sheet.letters[own[0]], sheet.letters[own[-1]]
            span_text = first_letter if first_letter == last_letter else f"{first_letter}–{last_letter}"
            flow.append(Paragraph(
                f"Часть {part} из {parts}: столбцы {span_text}. "
                f"Столбец {sheet.letters[0]} повторяется в каждой части.", note))
        if part == 1:
            for index, text in enumerate(sheet.titles):
                flow.append(Paragraph(_escape(text), title_style if index == 0 else subtitle_style))
            if sheet.titles:
                flow.append(Spacer(1, 3))

        positions = {c: i for i, c in enumerate(group)}
        ncols = len(group)
        data = [["" for _ in range(ncols)] for _ in range(sheet.nrows)]
        commands: list = [
            ("GRID", (0, 0), (-1, -1), 0.4, grid_color),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONT", (0, 0), (-1, -1), font, size, size * 1.25),
            ("TEXTCOLOR", (0, 0), (-1, -1), _rl_color(_INK)),
            ("LEFTPADDING", (0, 0), (-1, -1), _XL_PAD), ("RIGHTPADDING", (0, 0), (-1, -1), _XL_PAD),
            ("TOPPADDING", (0, 0), (-1, -1), 2.0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
        ]
        if sheet.header_rows:
            commands.append(("BACKGROUND", (0, 0), (-1, sheet.header_rows - 1), header_bg))
            commands.append(("VALIGN", (0, 0), (-1, sheet.header_rows - 1), "MIDDLE"))

        def natural_align(r: int, cell: _XlCell) -> str:
            return cell.align or ("c" if r < sheet.header_rows or cell.kind in ("date", "bool") else
                                  "r" if cell.kind == "num" else "l")

        # выравнивание столбца «по большинству» задаётся одной командой, остальное — по ячейкам
        column_align: dict[int, str] = {}
        tally: dict[int, dict[str, int]] = {}
        for (r, c), cell in sheet.cells.items():
            if r >= sheet.header_rows and c in positions:
                counts = tally.setdefault(c, {})
                key = natural_align(r, cell)
                counts[key] = counts.get(key, 0) + 1
        for c, counts in tally.items():
            column_align[c] = max(counts, key=counts.get)
            if column_align[c] != "l":
                commands.append(("ALIGN", (positions[c], sheet.header_rows), (positions[c], -1),
                                 "RIGHT" if column_align[c] == "r" else "CENTER"))
        wide_cells = {(r0, c0) for r0, c0, _r1, _c1 in sheet.spans} | layout.overflow

        def put(r: int, pos: int, cell: _XlCell, c: int | None = None) -> None:
            is_header = r < sheet.header_rows
            align = natural_align(r, cell)
            color = cell.color
            if cell.fill is not None:
                commands.append(("BACKGROUND", (pos, r), (pos, r), _rl_color(cell.fill)))
                color = _readable_on(color, cell.fill)
            elif color is not None and _lum(color) > 0.72:
                color = None
            # короткий текст без оформления рисуется строкой: это в несколько раз быстрее абзаца
            if (c is not None and not is_header and not cell.bold and "\n" not in cell.text
                    and (r, c) not in wide_cells
                    and _text_width(cell.text, font, size) <= widths[pos] - 2 * _XL_PAD - 0.6):
                data[r][pos] = cell.text
                if align != column_align.get(c, "l"):
                    commands.append(("ALIGN", (pos, r), (pos, r), {"l": "LEFT", "c": "CENTER", "r": "RIGHT"}[align]))
                if color is not None:
                    commands.append(("TEXTCOLOR", (pos, r), (pos, r), _rl_color(color)))
                return
            markup = _escape(cell.text)
            if cell.bold or is_header:
                markup = f"<b>{markup}</b>"
            if color is not None:
                markup = f'<font color="{_rgb_hex(color)}">{markup}</font>'
            data[r][pos] = Paragraph(markup, style(align, is_header))

        for (r, c), cell in sheet.cells.items():
            pos = positions.get(c)
            if pos is not None:
                put(r, pos, cell, c)
        for r0, c0, r1, c1 in sheet.spans:
            inside = sorted(positions[c] for c in range(c0, c1 + 1) if c in positions)
            if not inside:
                continue
            run = [inside[0]]
            for pos in inside[1:]:
                if pos != run[-1] + 1:
                    break
                run.append(pos)
            anchor = sheet.cells.get((r0, c0))
            # reportlab не умеет разрывать объединение строк между страницами, поэтому длинное
            # объединение режем на куски по row_chunk строк и повторяем подпись в начале каждого
            if r1 - r0 + 1 <= row_chunk or r0 < sheet.header_rows:
                pieces = [(r0, r1)]
            else:
                pieces = [(start, min(r1, start + row_chunk - 1)) for start in range(r0, r1 + 1, row_chunk)]
            for index, (top, bottom) in enumerate(pieces):
                if anchor is not None and (index > 0 or c0 not in positions):
                    put(top, run[0], anchor)        # подпись для куска или для другой части столбцов
                if len(run) > 1 or bottom > top:
                    commands.append(("SPAN", (run[0], top), (run[-1], bottom)))
                    if anchor is not None and anchor.fill is not None:
                        commands.append(("BACKGROUND", (run[0], top), (run[-1], bottom), _rl_color(anchor.fill)))
        for r, c in layout.overflow:
            pos = positions.get(c)
            if pos is not None and pos < ncols - 1:
                commands.append(("SPAN", (pos, r), (ncols - 1, r)))

        heights = [5.0 if r in sheet.blank_rows else None for r in range(sheet.nrows)]
        repeat = sheet.header_rows if sheet.nrows > sheet.header_rows else 0
        table = _FlowTable(data, colWidths=widths, rowHeights=heights, repeatRows=repeat)
        table.setStyle(commands)
        flow.append(table)
    return flow


def _build_xlsx(src: Path, out: Path, ctx: _Ctx) -> dict:
    import openpyxl

    try:
        with _warnings.catch_warnings():
            _warnings.simplefilter("ignore")
            book_f = openpyxl.load_workbook(str(src), data_only=False)
            book_v = openpyxl.load_workbook(str(src), data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise _Fail("Не удалось открыть книгу XLSX: файл повреждён. Сохраните его заново в Excel.") from exc
    theme = _xl_theme_colors(book_f)
    sheets: list[_XlSheet] = []
    hidden_with_data = False
    for ws_f in book_f.worksheets:
        ws_v = book_v[ws_f.title] if ws_f.title in book_v.sheetnames else ws_f
        if ws_f.sheet_state != "visible":
            hidden_with_data = hidden_with_data or _xl_analyse(ws_f, ws_v, _Ctx(), theme) is not None
            continue
        sheet = _xl_analyse(ws_f, ws_v, ctx, theme)
        if sheet is None:
            continue
        if getattr(ws_f, "_charts", None):
            ctx.warn(f"Лист «{ws_f.title}»: диаграммы не перенесены.")
        if getattr(ws_f, "_images", None):
            ctx.warn(f"Лист «{ws_f.title}»: рисунки не перенесены.")
        sheets.append(sheet)
    if getattr(book_f, "chartsheets", None):
        ctx.warn("Листы-диаграммы не перенесены.")
    if not sheets:
        if hidden_with_data:
            raise _Fail("В книге нет данных на видимых листах: заполнены только скрытые листы.")
        raise _Fail("В книге нет данных: все листы пустые.")
    if hidden_with_data:
        ctx.warn("Скрытые листы в PDF не показаны.")

    page = _Page(size=landscape(A4), margin=12 * mm)
    title = sheets[0].titles[0] if sheets[0].titles else sheets[0].title
    header_pages: list[int] = []
    last_error: Exception | None = None
    # Объединение строк выше страницы reportlab разместить не может (и сам падает на сообщении об
    # ошибке), поэтому при неудаче пробуем ещё раз с более короткими кусками объединений.
    for row_chunk in (12, 4, 1):
        story: list = []
        header_pages = []
        for index, sheet in enumerate(sheets):
            if index:
                story.append(PageBreak())
            story.extend(_xl_flowables(sheet, page, row_chunk, header_pages))
        try:
            _build_flow_pdf(out, story, page, _short(title, 90) or src.stem)
            break
        except Exception as exc:  # noqa: BLE001 — LayoutError или сбой внутри reportlab
            last_error = exc
            log.info("XLSX %s: таблица не разместилась (объединения по %s строк): %r", src, row_chunk, exc)
    else:
        raise _Fail("Не удалось разместить таблицу на страницах PDF: в ячейке слишком много текста.") from last_error
    headers: list[str] = []
    for sheet in sheets:
        headers.extend(sheet.header_texts)
    return {"headers": headers, "header_pages": header_pages}


# ════════════════════════════════════════════════════════════════════════════
#  PPTX
# ════════════════════════════════════════════════════════════════════════════

_EMU = 12700.0     # EMU в одном пункте
_PP_SHAPE_TAGS = frozenset(_qn(t) for t in ("p:sp", "p:pic", "p:grpSp", "p:graphicFrame", "p:cxnSp"))
_PP_DEFAULT_SIZE = {"title": 40.0, "body": 24.0, "other": 18.0}
_PP_SCALES = (1.0, 0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.55, 0.5)
_PP_RECTS = frozenset((
    "rect", "flowChartProcess", "snip1Rect", "snip2SameRect", "snip2DiagRect", "snipRoundRect",
    "wedgeRectCallout", "actionButtonBlank", "foldedCorner", "bevel", "frame", "plaque",
))
_PP_ROUNDS = frozenset((
    "roundRect", "round1Rect", "round2SameRect", "round2DiagRect", "flowChartAlternateProcess",
    "flowChartTerminator", "wedgeRoundRectCallout",
))
_PP_ELLIPSES = frozenset(("ellipse", "flowChartConnector", "wedgeEllipseCallout", "donut"))
_PP_LINES = frozenset((
    "line", "straightConnector1", "bentConnector2", "bentConnector3", "bentConnector4", "bentConnector5",
    "curvedConnector2", "curvedConnector3", "curvedConnector4", "curvedConnector5",
))
_PP_PRESET_COLORS = {
    "black": (0.0, 0.0, 0.0), "white": (1.0, 1.0, 1.0), "red": (1.0, 0.0, 0.0), "green": (0.0, 0.5, 0.0),
    "blue": (0.0, 0.0, 1.0), "yellow": (1.0, 1.0, 0.0), "gray": (0.5, 0.5, 0.5), "grey": (0.5, 0.5, 0.5),
    "orange": (1.0, 0.65, 0.0), "darkGray": (0.66, 0.66, 0.66), "lightGray": (0.83, 0.83, 0.83),
}
_PP_ALIGN = {"l": TA_LEFT, "ctr": TA_CENTER, "r": TA_RIGHT, "just": TA_JUSTIFY, "dist": TA_JUSTIFY}
_PP_TABLE_PLAIN_GRID = "{5940675A-B579-460E-94D1-54222C63F5DA}"
_PP_TABLE_NO_GRID = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"
_PP_TABLE_ACCENTS = {
    "{21E4AEA4-8DFA-4A89-87EB-49C32662AFE0}": "accent2", "{F5AB1C69-6EDB-4FF4-983F-18BD219EF322}": "accent3",
    "{00A15C55-8517-42AA-B614-E9B94910E393}": "accent4", "{7DF18680-E054-41AD-8BC1-D1AEF772440D}": "accent5",
    "{93296810-A885-4BE3-A3E7-6D5BEEA58F35}": "accent6",
}
_ON = ("1", "true")


def _local(el) -> str:
    return el.tag.rpartition("}")[2] if isinstance(el.tag, str) else ""


def _pp_points(prst: str, w: float, h: float, adj: dict) -> list | None:
    """Вершины простых фигур PowerPoint в координатах «от левого верхнего угла, y вниз»."""
    m = min(w, h)

    def a(name: str, default: int) -> float:
        return max(0.0, adj.get(name, default)) / 100000.0

    if prst == "triangle":
        return [(w * a("adj", 50000), 0), (w, h), (0, h)]
    if prst == "rtTriangle":
        return [(0, 0), (w, h), (0, h)]
    if prst in ("diamond", "flowChartDecision"):
        return [(w / 2, 0), (w, h / 2), (w / 2, h), (0, h / 2)]
    if prst in ("parallelogram", "flowChartInputOutput"):
        d = min(w, m * a("adj", 25000))
        return [(d, 0), (w, 0), (w - d, h), (0, h)]
    if prst == "trapezoid":
        d = min(w / 2, m * a("adj", 25000))
        return [(d, 0), (w - d, 0), (w, h), (0, h)]
    if prst in ("hexagon", "flowChartPreparation"):
        d = min(w / 2, m * a("adj", 25000))
        return [(d, 0), (w - d, 0), (w, h / 2), (w - d, h), (d, h), (0, h / 2)]
    if prst == "octagon":
        d = min(w / 2, h / 2, m * a("adj", 29289))
        return [(d, 0), (w - d, 0), (w, d), (w, h - d), (w - d, h), (d, h), (0, h - d), (0, d)]
    if prst == "pentagon":
        return [(w / 2, 0), (w, h * 0.382), (w * 0.809, h), (w * 0.191, h), (0, h * 0.382)]
    if prst in ("rightArrow", "notchedRightArrow", "stripedRightArrow", "leftArrow", "upArrow", "downArrow"):
        along, across = (w, h) if prst not in ("upArrow", "downArrow") else (h, w)
        head = min(along, m * a("adj2", 50000))
        half = across * a("adj1", 50000) / 2
        base = [(0, across / 2 - half), (along - head, across / 2 - half), (along - head, 0), (along, across / 2),
                (along - head, across), (along - head, across / 2 + half), (0, across / 2 + half)]
        if prst == "leftArrow":
            return [(along - x, y) for x, y in base]
        if prst == "downArrow":
            return [(y, x) for x, y in base]
        if prst == "upArrow":
            return [(y, along - x) for x, y in base]
        return base
    if prst in ("leftRightArrow", "upDownArrow"):
        along, across = (w, h) if prst == "leftRightArrow" else (h, w)
        head = min(along / 2, m * a("adj2", 50000))
        half = across * a("adj1", 50000) / 2
        base = [(0, across / 2), (head, 0), (head, across / 2 - half), (along - head, across / 2 - half),
                (along - head, 0), (along, across / 2), (along - head, across), (along - head, across / 2 + half),
                (head, across / 2 + half), (head, across)]
        return base if prst == "leftRightArrow" else [(y, x) for x, y in base]
    if prst == "chevron":
        d = min(w, m * a("adj", 50000))
        return [(0, 0), (w - d, 0), (w, h / 2), (w - d, h), (0, h), (d, h / 2)]
    if prst == "homePlate":
        d = min(w, m * a("adj", 50000))
        return [(0, 0), (w - d, 0), (w, h / 2), (w - d, h), (0, h)]
    if prst == "plus":
        d = min(w / 2, h / 2, m * a("adj", 25000))
        return [(d, 0), (w - d, 0), (w - d, d), (w, d), (w, h - d), (w - d, h - d), (w - d, h), (d, h),
                (d, h - d), (0, h - d), (0, d), (d, d)]
    if prst == "star5":
        inner = min(0.5, a("adj", 19098))
        points = []
        for k in range(10):
            radius = 0.5 if k % 2 == 0 else inner
            angle = math.radians(-90 + 36 * k)
            points.append((w * (0.5 + radius * math.cos(angle) * 1.05), h * (0.55 + radius * math.sin(angle) * 1.1)))
        return points
    return None


def _pp_text_inset(prst: str, w: float, h: float, adj: dict) -> tuple:
    """На сколько область текста фигуры меньше её рамки → (слева, сверху, справа, снизу), приближённо."""
    m = min(w, h)

    def a(name: str, default: int) -> float:
        return max(0.0, adj.get(name, default)) / 100000.0

    if prst in _PP_ELLIPSES:
        return w * 0.1464, h * 0.1464, w * 0.1464, h * 0.1464
    if prst in _PP_ROUNDS:
        share = 0.5 if prst == "flowChartTerminator" else a("adj", 16667)
        k = min(m * share, m / 2.0) * 0.2929
        return k, k, k, k
    if prst in ("diamond", "flowChartDecision"):
        return w / 4, h / 4, w / 4, h / 4
    if prst == "triangle":
        return w / 4, h / 2, w / 4, 0.0
    if prst == "rtTriangle":
        return w / 12, h * 0.58, w * 0.42, h / 12
    if prst == "chevron":
        d = min(w * 0.45, m * a("adj", 50000))
        return d, 0.0, d, 0.0
    if prst == "homePlate":
        return 0.0, 0.0, min(w * 0.45, m * a("adj", 50000)) / 2, 0.0
    if prst in ("hexagon", "flowChartPreparation", "parallelogram", "flowChartInputOutput", "trapezoid"):
        d = min(w * 0.45, m * a("adj", 25000))
        return d * 0.6, h * 0.08, d * 0.6, h * 0.08
    if prst == "octagon":
        d = min(w / 2, h / 2, m * a("adj", 29289)) * 0.2929
        return d, d, d, d
    if prst == "pentagon":
        return w * 0.2, h * 0.25, w * 0.2, h * 0.05
    if prst == "star5":
        return w * 0.3, h * 0.38, w * 0.3, h * 0.26
    if prst in ("rightArrow", "notchedRightArrow", "stripedRightArrow", "leftArrow", "leftRightArrow"):
        band = h * (1.0 - a("adj1", 50000)) / 2
        head = min(w * 0.45, m * a("adj2", 50000)) / 2
        return (head if prst != "rightArrow" else 0.0), band, (head if prst != "leftArrow" else 0.0), band
    if prst in ("upArrow", "downArrow", "upDownArrow"):
        band = w * (1.0 - a("adj1", 50000)) / 2
        head = min(h * 0.45, m * a("adj2", 50000)) / 2
        return band, (head if prst != "downArrow" else 0.0), band, (head if prst != "upArrow" else 0.0)
    return 0.0, 0.0, 0.0, 0.0


def _pp_auto_number(number: int, scheme: str) -> str:
    if scheme.startswith("alphaLc"):
        text = _letters(number, "abcdefghijklmnopqrstuvwxyz")
    elif scheme.startswith("alphaUc"):
        text = _letters(number, "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    elif scheme.startswith("romanLc"):
        text = _roman(number).lower()
    elif scheme.startswith("romanUc"):
        text = _roman(number)
    else:
        text = str(number)
    if scheme.endswith("ParenBoth"):
        return f"({text})"
    if scheme.endswith("ParenR"):
        return f"{text})"
    if scheme.endswith("Plain"):
        return text
    return f"{text}."


@dataclass
class _Fill:
    rgb: tuple | None = None
    alpha: float = 1.0
    image: bytes | None = None


@dataclass
class _Stroke:
    rgb: tuple
    width: float
    dash: str | None = None
    alpha: float = 1.0
    head: bool = False      # стрелка в начале линии
    tail: bool = False      # стрелка в конце линии


@dataclass
class _Item:
    """Фигура слайда с рамкой в пунктах; начало координат — левый верхний угол слайда."""
    shape: object
    el: object
    x: float
    y: float
    w: float
    h: float
    rot: float = 0.0
    flip_h: bool = False
    flip_v: bool = False
    cache: dict = field(default_factory=dict)


@dataclass
class _TextRun:
    text: str
    size: float
    bold: bool
    italic: bool
    underline: bool
    strike: bool
    vert: str | None
    color: tuple
    serif: bool


@dataclass
class _TextPara:
    runs: list
    align: int
    left: float
    indent: float
    bullet: str | None
    size: float
    before: float
    after: float
    spacing: float
    color: tuple


class _Pptx:
    """Рисует слайды на холсте reportlab: одна страница PDF на слайд, размер страницы = размер слайда."""

    def __init__(self, prs, canv, width: float, height: float, ctx: _Ctx) -> None:
        self.prs = prs
        self.c = canv
        self.W = width
        self.H = height
        self.ctx = ctx
        self.number = 0
        self.theme: dict = {}
        self._themes: dict = {}
        self.bg_rgb = _WHITE
        self.layout = None
        self.master = None
        try:
            self.default_style = prs.part._element.find(_qn("p:defaultTextStyle"))
        except Exception:  # noqa: BLE001
            self.default_style = None

    # ── слайд ───────────────────────────────────────────────────────────────
    def render(self, slide, number: int) -> None:
        self.number = number
        self.layout = slide.slide_layout
        self.master = self.layout.slide_master
        self.theme = self._theme(self.master)
        self.bg_rgb = self._background(slide)
        if (slide._element.get("showMasterSp") or "1") in _ON:
            if (self.layout._element.get("showMasterSp") or "1") in _ON:
                self._draw_items(self._collect(self.master.shapes), decorative=True)
            self._draw_items(self._collect(self.layout.shapes), decorative=True)
        self._draw_items(self._collect(slide.shapes), decorative=False)

    def warn(self, text: str) -> None:
        self.ctx.warn(f"Слайд {self.number}: {text}")

    def _theme(self, master) -> dict:
        key = str(master.part.partname)
        cached = self._themes.get(key)
        if cached is not None:
            return cached
        scheme = {name: _hex_rgb(value) for name, value in (
            ("dk1", "000000"), ("lt1", "FFFFFF"), ("dk2", "1F497D"), ("lt2", "EEECE1"), ("accent1", "4F81BD"),
            ("accent2", "C0504D"), ("accent3", "9BBB59"), ("accent4", "8064A2"), ("accent5", "4BACC6"),
            ("accent6", "F79646"), ("hlink", "0000FF"), ("folHlink", "800080"))}
        fonts: dict = {}
        try:
            from lxml import etree
            from pptx.opc.constants import RELATIONSHIP_TYPE as RT

            root = etree.fromstring(master.part.part_related_by(RT.THEME).blob)
            colors_el = _find(root, "a:themeElements/a:clrScheme")
            for child in (colors_el if colors_el is not None else []):
                inner = child[0] if len(child) else None
                rgb = _hex_rgb(inner.get("lastClr") or inner.get("val")) if inner is not None else None
                if rgb is not None:
                    scheme[_local(child)] = rgb
            for key_name, tag in (("+mj", "a:majorFont"), ("+mn", "a:minorFont")):
                latin = _find(root, f"a:themeElements/a:fontScheme/{tag}/a:latin")
                if latin is not None:
                    fonts[key_name] = latin.get("typeface") or ""
        except Exception:  # noqa: BLE001 — тема не читается: остаются стандартные цвета
            pass
        mapping = {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2"}
        clr_map = master._element.find(_qn("p:clrMap"))
        if clr_map is not None:
            mapping.update({k: v for k, v in clr_map.attrib.items()})
        cached = {"scheme": scheme, "map": mapping, "fonts": fonts}
        self._themes[key] = cached
        return cached

    def _scheme(self, name: str) -> tuple | None:
        return self.theme["scheme"].get(self.theme["map"].get(name, name))

    def _color(self, holder) -> tuple:
        """Цвет DrawingML внутри holder → ((r, g, b) или None, непрозрачность)."""
        if holder is None:
            return None, 1.0
        for child in holder:
            tag = _local(child)
            if tag == "srgbClr":
                rgb = _hex_rgb(child.get("val"))
            elif tag == "schemeClr":
                rgb = self._scheme(child.get("val") or "")
            elif tag == "sysClr":
                rgb = _hex_rgb(child.get("lastClr")) or (_WHITE if child.get("val") == "window" else (0.0, 0.0, 0.0))
            elif tag == "prstClr":
                rgb = _PP_PRESET_COLORS.get(child.get("val") or "")
            elif tag == "scrgbClr":
                rgb = tuple(max(0.0, min(1.0, _int(child.get(k), 0) / 100000.0)) for k in "rgb")
            else:
                continue
            if rgb is None:
                return None, 1.0
            alpha = 1.0
            for mod in child:
                name = _local(mod)
                value = _int(mod.get("val"), 100000) / 100000.0
                if name in ("lumMod", "lumOff"):
                    hue, light, sat = colorsys.rgb_to_hls(*rgb)
                    light = light * value if name == "lumMod" else light + value
                    rgb = colorsys.hls_to_rgb(hue, max(0.0, min(1.0, light)), sat)
                elif name == "tint":
                    rgb = _mix(rgb, _WHITE, value)
                elif name == "shade":
                    rgb = _mix(rgb, (0.0, 0.0, 0.0), value)
                elif name == "alpha":
                    alpha = max(0.0, min(1.0, value))
            return tuple(rgb), alpha
        return None, 1.0

    def _fill(self, sp_pr, style, part) -> tuple:
        """→ (задана ли заливка явно, _Fill или None)."""
        if sp_pr is not None:
            for child in sp_pr:
                tag = _local(child)
                if tag == "noFill" or tag == "grpFill":
                    return True, None
                if tag == "solidFill":
                    rgb, alpha = self._color(child)
                    return True, (_Fill(rgb, alpha) if rgb is not None else None)
                if tag == "gradFill":
                    stops = [self._color(gs) for gs in child.iter(_qn("a:gs"))]
                    stops = [(rgb, alpha) for rgb, alpha in stops if rgb is not None]
                    if not stops:
                        return True, None
                    rgb = tuple(sum(s[0][i] for s in stops) / len(stops) for i in range(3))
                    return True, _Fill(rgb, sum(s[1] for s in stops) / len(stops))
                if tag == "pattFill":
                    rgb, alpha = self._color(child.find(_qn("a:fgClr")))
                    return True, (_Fill(rgb, alpha) if rgb is not None else None)
                if tag == "blipFill":
                    blip = child.find(_qn("a:blip"))
                    rid = blip.get(_qn("r:embed")) if blip is not None else None
                    try:
                        return True, _Fill(image=part.related_part(rid).blob)
                    except Exception:  # noqa: BLE001 — рисунок не найден
                        return True, None
        if style is not None:
            ref = style.find(_qn("a:fillRef"))
            if ref is not None and _int(ref.get("idx"), 0) > 0:
                rgb, alpha = self._color(ref)
                if rgb is not None:
                    return True, _Fill(rgb, alpha)
        return False, None

    def _stroke(self, sp_pr, style) -> _Stroke | None:
        ln = sp_pr.find(_qn("a:ln")) if sp_pr is not None else None
        rgb = None
        alpha, width, dash, head, tail = 1.0, None, None, False, False
        if ln is not None:
            if ln.get("w"):
                width = _int(ln.get("w"), 9525) / _EMU
            if ln.find(_qn("a:noFill")) is not None:
                return None
            solid = ln.find(_qn("a:solidFill"))
            if solid is not None:
                rgb, alpha = self._color(solid)
            else:
                stop = next(ln.iter(_qn("a:gs")), None)
                if stop is not None:
                    rgb, alpha = self._color(stop)
            dash_el = ln.find(_qn("a:prstDash"))
            if dash_el is not None and (dash_el.get("val") or "solid") != "solid":
                dash = dash_el.get("val")
            head_el, tail_el = ln.find(_qn("a:headEnd")), ln.find(_qn("a:tailEnd"))
            head = head_el is not None and (head_el.get("type") or "none") != "none"
            tail = tail_el is not None and (tail_el.get("type") or "none") != "none"
        if rgb is None and style is not None:
            ref = style.find(_qn("a:lnRef"))
            index = _int(ref.get("idx"), 0) if ref is not None else 0
            if index > 0:
                rgb, alpha = self._color(ref)
                if width is None:
                    width = {1: 0.75, 2: 2.0, 3: 3.0}.get(index, 0.75)
        if rgb is None:
            return None
        return _Stroke(rgb, max(0.25, width if width is not None else 0.75), dash, alpha, head, tail)

    def _background(self, slide) -> tuple:
        c = self.c
        rgb = _WHITE
        image = None
        for owner in (slide, self.layout, self.master):
            bg = _find(owner._element, "p:cSld/p:bg")
            if bg is None:
                continue
            bg_pr = bg.find(_qn("p:bgPr"))
            if bg_pr is not None:
                explicit, fill = self._fill(bg_pr, None, owner.part)
                if fill is not None:
                    if fill.image is not None:
                        image = fill.image
                    elif fill.rgb is not None:
                        rgb = _mix(fill.rgb, _WHITE, fill.alpha)
                if explicit:
                    break
                continue
            ref = bg.find(_qn("p:bgRef"))
            if ref is not None:
                found, _alpha = self._color(ref)
                if found is not None:
                    rgb = found
                    break
        c.setFillColorRGB(*rgb)
        c.rect(0, 0, self.W, self.H, stroke=0, fill=1)
        if image is not None:
            loaded = _load_image(image, self.W)
            if loaded is not None:
                c.drawImage(ImageReader(loaded[0]), 0, 0, self.W, self.H, mask="auto")
                try:
                    from PIL import Image as PILImage

                    loaded[0].seek(0)
                    pixel = PILImage.open(loaded[0]).convert("RGB").resize((1, 1)).getpixel((0, 0))
                    rgb = tuple(v / 255.0 for v in pixel)
                except Exception:  # noqa: BLE001
                    pass
        return rgb

    # ── сбор фигур ───────────────────────────────────────────────────────────
    @staticmethod
    def _iter_shapes(shapes):
        try:
            tree = shapes._spTree
            factory = shapes._shape_factory
        except AttributeError:
            yield from shapes
            return
        for child in tree.iterchildren():
            if child.tag in _PP_SHAPE_TAGS:
                yield factory(child)
            elif child.tag == _qn("mc:AlternateContent"):
                # сначала запасной вариант: обычно это картинка, которую точно можно нарисовать
                for holder in (child.find(_qn("mc:Fallback")), child.find(_qn("mc:Choice"))):
                    found = [e for e in holder.iterchildren() if e.tag in _PP_SHAPE_TAGS] if holder is not None else []
                    if found:
                        for element in found:
                            yield factory(element)
                        break

    def _collect(self, shapes, transform: tuple = (0.0, 1.0, 0.0, 1.0), out: list | None = None) -> list:
        """Плоский список фигур (группы раскрыты) в порядке отрисовки."""
        out = [] if out is None else out
        ax, bx, ay, by = transform
        try:
            iterator = list(self._iter_shapes(shapes))
        except Exception:  # noqa: BLE001
            iterator = list(shapes)
        for shape in iterator:
            el = shape._element
            if el.tag == _qn("p:grpSp"):
                child_tf = transform
                xfrm = _find(el, "p:grpSpPr/a:xfrm")
                parts = []
                if xfrm is not None:
                    parts = [xfrm.find(_qn(t)) for t in ("a:off", "a:ext", "a:chOff", "a:chExt")]
                if parts and all(part is not None for part in parts):
                    off, ext, ch_off, ch_ext = parts
                    sx = _int(ext.get("cx"), 0) / (_int(ch_ext.get("cx"), 0) or 1)
                    sy = _int(ext.get("cy"), 0) / (_int(ch_ext.get("cy"), 0) or 1)
                    sx, sy = sx or 1.0, sy or 1.0
                    child_tf = (ax + bx * (_int(off.get("x"), 0) - _int(ch_off.get("x"), 0) * sx), bx * sx,
                                ay + by * (_int(off.get("y"), 0) - _int(ch_off.get("y"), 0) * sy), by * sy)
                try:
                    self._collect(shape.shapes, child_tf, out)
                except Exception:  # noqa: BLE001
                    log.debug("PPTX: группа не разобрана", exc_info=True)
                continue
            try:
                left, top, width, height = shape.left, shape.top, shape.width, shape.height
            except Exception:  # noqa: BLE001
                continue
            if left is None or top is None or width is None or height is None:
                continue
            xfrm = _find(el, "p:spPr/a:xfrm")
            if xfrm is None:
                xfrm = el.find(_qn("p:xfrm"))
            rot = _int(xfrm.get("rot"), 0) / 60000.0 if xfrm is not None else 0.0
            out.append(_Item(
                shape=shape, el=el,
                x=(ax + bx * left) / _EMU, y=(ay + by * top) / _EMU, w=bx * width / _EMU, h=by * height / _EMU,
                rot=rot,
                flip_h=xfrm is not None and xfrm.get("flipH") in _ON,
                flip_v=xfrm is not None and xfrm.get("flipV") in _ON,
            ))
        return out

    def _draw_items(self, items: list, decorative: bool) -> None:
        c = self.c
        for index, item in enumerate(items):
            depth = len(c.state_stack)
            c.saveState()
            try:
                tag = item.el.tag
                if tag == _qn("p:sp"):
                    self._draw_sp(item, items, index, decorative)
                elif tag == _qn("p:pic"):
                    self._draw_pic(item, decorative)
                elif tag == _qn("p:cxnSp"):
                    self._draw_line(item, item.el.find(_qn("p:spPr")), item.el.find(_qn("p:style")))
                elif tag == _qn("p:graphicFrame") and not decorative:
                    self._draw_frame(item, items, index)
            except Exception:  # noqa: BLE001 — одна неудачная фигура не должна ломать слайд
                log.debug("PPTX: фигура на слайде %s не нарисована", self.number, exc_info=True)
                if not decorative:
                    self.warn("часть фигур не удалось перенести.")
            finally:
                while len(c.state_stack) > depth:
                    c.restoreState()

    def _enter(self, item: _Item) -> None:
        """Переходит в систему координат фигуры: (0, 0) — её левый нижний угол, ось y вверх."""
        c = self.c
        c.translate(item.x + item.w / 2.0, self.H - (item.y + item.h / 2.0))
        if item.rot:
            c.rotate(-item.rot)
        c.translate(-item.w / 2.0, -item.h / 2.0)

    # ── автофигуры ──────────────────────────────────────────────────────────
    def _placeholder_bases(self, ph) -> list:
        """Прототипы местозаполнителя на макете и образце слайдов: [(элемент, часть)]."""
        ptype = ph.get("type") or "obj"
        index = ph.get("idx") or "0"
        out = []
        layout_ph = self._find_placeholder(self.layout._element, index, ptype)
        if layout_ph is not None:
            out.append((layout_ph, self.layout.part))
        if ptype in ("title", "ctrTitle"):
            master_type = "title"
        else:
            master_type = ptype if ptype in ("dt", "ftr", "sldNum") else "body"
        master_ph = self._find_placeholder(self.master._element, None, master_type)
        if master_ph is not None:
            out.append((master_ph, self.master.part))
        return out

    @staticmethod
    def _find_placeholder(root, index: str | None, ptype: str):
        same = {"title": ("title", "ctrTitle"), "ctrTitle": ("title", "ctrTitle"),
                "body": ("body", "subTitle", "obj"), "subTitle": ("subTitle", "body"), "obj": ("obj", "body")}
        wanted = same.get(ptype, (ptype,))
        by_type = None
        for sp in root.iter(_qn("p:sp")):
            ph = _find(sp, "p:nvSpPr/p:nvPr/p:ph")
            if ph is None:
                continue
            if index is not None and (ph.get("idx") or "0") == index:
                return sp
            if by_type is None and (ph.get("type") or "obj") in wanted:
                by_type = sp
        return by_type

    def _geometry(self, item: _Item, sp_pr) -> tuple:
        """Контур фигуры в её локальных координатах → (путь, замкнут ли) или (None, False)."""
        c = self.c
        w, h = item.w, item.h

        def local(x: float, y: float) -> tuple:
            return (w - x if item.flip_h else x), (y if item.flip_v else h - y)

        custom = sp_pr.find(_qn("a:custGeom")) if sp_pr is not None else None
        if custom is not None:
            return self._custom_path(custom, item, local)
        geom = sp_pr.find(_qn("a:prstGeom")) if sp_pr is not None else None
        prst = (geom.get("prst") if geom is not None else None) or "rect"
        adj: dict = {}
        if geom is not None:
            for guide in geom.iter(_qn("a:gd")):
                match = re.fullmatch(r"val\s+(-?\d+)", (guide.get("fmla") or "").strip())
                if match:
                    adj[guide.get("name") or ""] = int(match.group(1))
        inset = _pp_text_inset(prst, w, h, adj)
        if item.flip_h:
            inset = (inset[2], inset[1], inset[0], inset[3])
        if item.flip_v:
            inset = (inset[0], inset[3], inset[2], inset[1])
        item.cache["text_inset"] = inset
        path = c.beginPath()
        if prst in _PP_RECTS:
            path.rect(0, 0, w, h)
            return path, True
        if prst in _PP_ROUNDS:
            share = 0.5
            if prst != "flowChartTerminator":
                share = max(0.0, adj.get("adj", adj.get("adj1", 16667))) / 100000.0
            path.roundRect(0, 0, w, h, min(min(w, h) * share, min(w, h) / 2.0))
            return path, True
        if prst in _PP_ELLIPSES:
            path.ellipse(0, 0, w, h)
            return path, True
        points = _pp_points(prst, w, h, adj)
        if points:
            path.moveTo(*local(*points[0]))
            for point in points[1:]:
                path.lineTo(*local(*point))
            path.close()
            return path, True
        return None, False

    def _custom_path(self, custom, item: _Item, local) -> tuple:
        paths = _findall(custom, "a:pathLst/a:path")
        if not paths:
            return None, False
        path = self.c.beginPath()
        closed = False
        drawn = False
        try:
            for path_el in paths:
                base_w = _int(path_el.get("w"), 0) or (getattr(item.shape, "width", 0) or 1)
                base_h = _int(path_el.get("h"), 0) or (getattr(item.shape, "height", 0) or 1)
                sx, sy = item.w / base_w, item.h / base_h
                current = None
                for segment in path_el:
                    tag = _local(segment)
                    points = [(float(pt.get("x")), float(pt.get("y"))) for pt in segment.findall(_qn("a:pt"))]
                    if tag == "moveTo" and points:
                        current = points[0]
                        path.moveTo(*local(current[0] * sx, current[1] * sy))
                    elif current is None:
                        continue
                    elif tag == "lnTo" and points:
                        current = points[0]
                        path.lineTo(*local(current[0] * sx, current[1] * sy))
                        drawn = True
                    elif tag == "cubicBezTo" and len(points) == 3:
                        flat = [v for px, py in points for v in local(px * sx, py * sy)]
                        path.curveTo(*flat)
                        current = points[2]
                        drawn = True
                    elif tag == "quadBezTo" and len(points) == 2:
                        (qx, qy), (ex, ey) = points
                        c1 = (current[0] + 2.0 / 3.0 * (qx - current[0]), current[1] + 2.0 / 3.0 * (qy - current[1]))
                        c2 = (ex + 2.0 / 3.0 * (qx - ex), ey + 2.0 / 3.0 * (qy - ey))
                        flat = [v for px, py in (c1, c2, (ex, ey)) for v in local(px * sx, py * sy)]
                        path.curveTo(*flat)
                        current = (ex, ey)
                        drawn = True
                    elif tag == "arcTo":
                        rx, ry = float(segment.get("wR")), float(segment.get("hR"))
                        start = math.radians(float(segment.get("stAng")) / 60000.0)
                        sweep = math.radians(float(segment.get("swAng")) / 60000.0)
                        cx, cy = current[0] - rx * math.cos(start), current[1] - ry * math.sin(start)
                        steps = max(2, int(abs(math.degrees(sweep)) / 8.0) + 1)
                        for step in range(1, steps + 1):
                            angle = start + sweep * step / steps
                            current = (cx + rx * math.cos(angle), cy + ry * math.sin(angle))
                            path.lineTo(*local(current[0] * sx, current[1] * sy))
                        drawn = True
                    elif tag == "close":
                        path.close()
                        closed = True
        except (TypeError, ValueError):      # координаты заданы формулами — такой контур не разбираем
            return None, False
        return (path, closed) if drawn else (None, False)

    def _apply_stroke(self, stroke: _Stroke) -> None:
        c = self.c
        c.setStrokeColorRGB(*stroke.rgb)
        c.setLineWidth(stroke.width)
        c.setLineJoin(1)
        if stroke.alpha < 0.999:
            c.setStrokeAlpha(stroke.alpha)
        if stroke.dash:
            w = max(stroke.width, 0.5)
            patterns = {"dot": (w, 2 * w), "sysDot": (w, w), "sysDash": (3 * w, w), "lgDash": (8 * w, 3 * w),
                        "dashDot": (4 * w, 3 * w, w, 3 * w), "lgDashDot": (8 * w, 3 * w, w, 3 * w)}
            c.setDash(list(patterns.get(stroke.dash, (4 * w, 3 * w))))

    def _paint(self, path, fill: _Fill | None, stroke: _Stroke | None, item: _Item) -> None:
        c = self.c
        c.saveState()
        do_fill = False
        if fill is not None:
            if fill.image is not None:
                loaded = _load_image(fill.image, item.w)
                if loaded is not None:
                    c.saveState()
                    c.clipPath(path, stroke=0, fill=0)
                    c.drawImage(ImageReader(loaded[0]), 0, 0, item.w, item.h, mask="auto")
                    c.restoreState()
            elif fill.rgb is not None:
                c.setFillColorRGB(*fill.rgb)
                if fill.alpha < 0.999:
                    c.setFillAlpha(fill.alpha)
                do_fill = True
        if stroke is not None:
            self._apply_stroke(stroke)
        if do_fill or stroke is not None:
            c.drawPath(path, stroke=1 if stroke is not None else 0, fill=1 if do_fill else 0)
        c.restoreState()

    def _shape_look(self, item: _Item) -> tuple:
        """Заливка, обводка и наличие текста автофигуры (с кэшем: нужны и соседним фигурам)."""
        cached = item.cache.get("look")
        if cached is not None:
            return cached
        el = item.el
        fill = stroke = None
        has_text = False
        if el.tag == _qn("p:sp"):
            ph = _find(el, "p:nvSpPr/p:nvPr/p:ph")
            sp_pr, style = el.find(_qn("p:spPr")), el.find(_qn("p:style"))
            explicit, fill = self._fill(sp_pr, style, item.shape.part)
            stroke = self._stroke(sp_pr, style)
            if ph is not None and not explicit:
                for base, part in self._placeholder_bases(ph):
                    explicit, fill = self._fill(base.find(_qn("p:spPr")), None, part)
                    if explicit:
                        break
            body = el.find(_qn("p:txBody"))
            has_text = body is not None and any((t.text or "").strip() for t in body.iter(_qn("a:t")))
        cached = (fill, stroke, has_text)
        item.cache["look"] = cached
        return cached

    def _draw_sp(self, item: _Item, items: list, index: int, decorative: bool) -> None:
        el = item.el
        ph = _find(el, "p:nvSpPr/p:nvPr/p:ph")
        if ph is not None and decorative:
            return
        fill, stroke, has_text = self._shape_look(item)
        if not has_text and (ph is not None or (fill is None and stroke is None)):
            return          # пустой местозаполнитель или невидимая фигура
        sp_pr = el.find(_qn("p:spPr"))
        geom = sp_pr.find(_qn("a:prstGeom")) if sp_pr is not None else None
        if geom is not None and geom.get("prst") in _PP_LINES:
            self._draw_line(item, sp_pr, el.find(_qn("p:style")))
            return
        self._enter(item)
        path, closed = self._geometry(item, sp_pr)
        if path is None:
            if fill is None and not has_text:
                return      # сложный контур без заливки (скобка, дуга) — пропускаем
            path = self.c.beginPath()
            path.rect(0, 0, item.w, item.h)
            closed = True
            if fill is not None and not decorative:
                self.warn("фигура сложной формы показана прямоугольником.")
        if fill is not None or stroke is not None:
            self._paint(path, fill if closed else None, stroke, item)
        if has_text:
            bases = self._placeholder_bases(ph) if ph is not None else []
            self._draw_text(item, items, index, el.find(_qn("p:txBody")), ph, bases, fill, stroke)

    def _draw_line(self, item: _Item, sp_pr, style) -> None:
        stroke = self._stroke(sp_pr, style)
        if stroke is None:
            return
        c = self.c
        geom = sp_pr.find(_qn("a:prstGeom")) if sp_pr is not None else None
        prst = (geom.get("prst") if geom is not None else None) or "line"
        w, h = item.w, item.h
        self._enter(item)
        if prst == "bentConnector2":
            raw = [(0, 0), (w, 0), (w, h)]
        elif prst.startswith("bentConnector"):
            raw = [(0, 0), (w / 2, 0), (w / 2, h), (w, h)]
        else:
            raw = [(0, 0), (w, h)]
        points = [((w - x if item.flip_h else x), (y if item.flip_v else h - y)) for x, y in raw]
        path = c.beginPath()
        path.moveTo(*points[0])
        for point in points[1:]:
            path.lineTo(*point)
        self._apply_stroke(stroke)
        c.drawPath(path, stroke=1, fill=0)
        if stroke.tail:
            self._arrow_head(points[-2], points[-1], stroke)
        if stroke.head:
            self._arrow_head(points[1], points[0], stroke)

    def _arrow_head(self, start: tuple, tip: tuple, stroke: _Stroke) -> None:
        dx, dy = tip[0] - start[0], tip[1] - start[1]
        length = math.hypot(dx, dy)
        if length < 0.01:
            return
        ux, uy = dx / length, dy / length
        size = max(5.0, stroke.width * 3.5)
        bx, by = tip[0] - ux * size, tip[1] - uy * size
        c = self.c
        path = c.beginPath()
        path.moveTo(*tip)
        path.lineTo(bx - uy * size * 0.42, by + ux * size * 0.42)
        path.lineTo(bx + uy * size * 0.42, by - ux * size * 0.42)
        path.close()
        c.setDash([])
        c.setFillColorRGB(*stroke.rgb)
        c.drawPath(path, stroke=0, fill=1)

    # ── текст ───────────────────────────────────────────────────────────────
    def _sources(self, tx_body, ph, bases: list) -> tuple:
        """Цепочка стилей текста от ближнего к дальнему и вид текста: title / body / other."""
        sources = []
        own = tx_body.find(_qn("a:lstStyle"))
        if own is not None:
            sources.append(own)
        kind = "other"
        if ph is not None:
            ptype = ph.get("type") or "obj"
            if ptype in ("title", "ctrTitle"):
                kind = "title"
            elif ptype in ("body", "subTitle", "obj"):
                kind = "body"
            for base, _part in bases:
                style = _find(base, "p:txBody/a:lstStyle")
                if style is not None:
                    sources.append(style)
            tx_styles = self.master._element.find(_qn("p:txStyles"))
            if tx_styles is not None:
                style = tx_styles.find(_qn({"title": "p:titleStyle", "body": "p:bodyStyle"}.get(kind, "p:otherStyle")))
                if style is not None:
                    sources.append(style)
        return sources, kind

    def _paragraphs(self, tx_body, sources: list, kind: str, base_color: tuple | None,
                    background: tuple | None, force_bold: bool = False) -> list:
        """Абзацы текста со всеми унаследованными свойствами.

        sources — стили самой фигуры и её местозаполнителя (от ближнего к дальнему); после них
        идёт цвет фигуры base_color (a:fontRef, стиль таблицы) и стиль текста презентации по умолчанию.
        """
        paras: list[_TextPara] = []
        counters: dict[int, int] = {}
        for p in tx_body.findall(_qn("a:p")):
            p_pr = p.find(_qn("a:pPr"))
            level = max(0, min(8, _int(p_pr.get("lvl"), 0))) if p_pr is not None else 0
            tag = _qn(f"a:lvl{level + 1}pPr")
            near = [p_pr] if p_pr is not None else []
            styled = False
            for source in sources:
                found = source.find(tag)
                if found is not None:
                    near.append(found)
                    styled = True
            far = []
            if self.default_style is not None:
                found = self.default_style.find(tag)
                if found is not None:
                    far.append(found)
                    styled = True
            levels = near + far

            def p_attr(name: str, levels=levels):
                for element in levels:
                    value = element.get(name)
                    if value is not None:
                        return value
                return None

            def p_child(names: tuple, levels=levels):
                for element in levels:
                    for child in element:
                        if _local(child) in names:
                            return child
                return None

            def r_attr(r_pr, name: str, levels=levels):
                if r_pr is not None and r_pr.get(name) is not None:
                    return r_pr.get(name)
                for element in levels:
                    default = element.find(_qn("a:defRPr"))
                    if default is not None and default.get(name) is not None:
                        return default.get(name)
                return None

            def r_child(r_pr, name: str, where):
                """→ (элемент, задан ли прямо у фрагмента)."""
                if r_pr is not None and r_pr.find(_qn(name)) is not None:
                    return r_pr.find(_qn(name)), True
                for element in where:
                    default = element.find(_qn("a:defRPr"))
                    if default is not None and default.find(_qn(name)) is not None:
                        return default.find(_qn(name)), False
                return None, False

            def run_props(r_pr, near=near, far=far, levels=levels) -> dict:
                size = _int(r_attr(r_pr, "sz"), 0) / 100.0 or _PP_DEFAULT_SIZE[kind]
                fill_el, direct = r_child(r_pr, "a:solidFill", near)
                color = self._color(fill_el)[0] if fill_el is not None else None
                if color is None:
                    color, direct = base_color, False
                if color is None:
                    fill_el, _direct = r_child(None, "a:solidFill", far)
                    color = self._color(fill_el)[0] if fill_el is not None else None
                if color is None:
                    color = self._scheme("tx1") or _INK
                if not direct and background is not None:
                    color = _readable_on(color, background)
                latin, _direct = r_child(r_pr, "a:latin", levels)
                face = latin.get("typeface") if latin is not None else ""
                if face.startswith("+"):
                    face = self.theme["fonts"].get(face[:3], "")
                bold_attr = r_pr.get("b") if r_pr is not None else None
                bold = (bold_attr in _ON) if bold_attr is not None else (force_bold or r_attr(r_pr, "b") in _ON)
                baseline = _int(r_attr(r_pr, "baseline"), 0)
                return {
                    "size": size, "bold": bold, "italic": r_attr(r_pr, "i") in _ON,
                    "underline": (r_attr(r_pr, "u") or "none") != "none",
                    "strike": (r_attr(r_pr, "strike") or "noStrike") != "noStrike",
                    "vert": "super" if baseline > 0 else "sub" if baseline < 0 else None,
                    "color": color, "serif": _is_serif(face), "caps": r_attr(r_pr, "cap") == "all",
                }

            runs: list[_TextRun] = []
            for child in p:
                name = _local(child)
                if name not in ("r", "br", "fld"):
                    continue
                props = run_props(child.find(_qn("a:rPr")))
                if name == "br":
                    text = "\n"
                elif name == "fld" and (child.get("type") or "") == "slidenum":
                    text = str(self.number)
                else:
                    t = child.find(_qn("a:t"))
                    text = (t.text or "") if t is not None else ""
                if props.pop("caps"):
                    text = text.upper()
                if text:
                    runs.append(_TextRun(text=text, **props))
            end_props = run_props(p.find(_qn("a:endParaRPr")))
            visible = any(run.text.strip() for run in runs)
            if not visible:
                runs = []
            size = max((run.size for run in runs), default=end_props["size"])
            color = runs[0].color if runs else end_props["color"]

            # маркер
            bullet = None
            bullet_el = p_child(("buNone", "buChar", "buAutoNum", "buBlip"))
            bullet_kind = _local(bullet_el) if bullet_el is not None else None
            if bullet_kind == "buAutoNum":
                start = _int(bullet_el.get("startAt"), 1)
                counters[level] = counters.get(level, start - 1) + (1 if visible else 0)
                for deeper in [k for k in counters if k > level]:
                    del counters[deeper]
                bullet = _pp_auto_number(counters[level], bullet_el.get("type") or "arabicPeriod")
            else:
                for deeper in [k for k in counters if k >= level]:
                    del counters[deeper]
                if bullet_kind == "buChar":
                    raw = bullet_el.get("char") or "•"
                    font_el = p_child(("buFont",))
                    face = (font_el.get("typeface") if font_el is not None else "") or ""
                    if face.lower().startswith(("wingdings", "symbol", "webdings")) and ord(raw[0]) < 0x100:
                        raw = chr(0xF000 + ord(raw[0]))
                    bullet = _bullet_char(raw) if ord(raw[0]) >= 0xF000 or raw[0] in "-–—" else _clean(raw[0], self.ctx)
                    if bullet == _MISSING_GLYPH:
                        bullet = "•"
                elif bullet_kind == "buBlip":
                    bullet = "•"
            left = _int(p_attr("marL"), 0) / _EMU
            indent = _int(p_attr("indent"), 0) / _EMU
            if bullet_el is None and not styled and (level > 0 or kind == "body"):
                bullet = "•" if level == 0 else "–"       # стили уровня нигде не описаны: маркер по умолчанию
                left, indent = 27.0 * (level + 1), -22.0
            if not visible:
                bullet = None

            spacing = 1.0
            line_el = p_child(("lnSpc",))
            if line_el is not None:
                pct = line_el.find(_qn("a:spcPct"))
                pts = line_el.find(_qn("a:spcPts"))
                if pct is not None:
                    spacing = max(0.6, min(3.0, _int(pct.get("val"), 100000) / 100000.0))
                elif pts is not None and size > 0:
                    spacing = max(0.6, min(3.0, _int(pts.get("val"), 0) / 100.0 / (size * 1.2)))

            def gap(name: str) -> float:
                element = p_child((name,))
                if element is None:
                    return 0.0
                pct = element.find(_qn("a:spcPct"))
                pts = element.find(_qn("a:spcPts"))
                if pct is not None:
                    return size * 1.2 * _int(pct.get("val"), 0) / 100000.0
                if pts is not None:
                    return _int(pts.get("val"), 0) / 100.0
                return 0.0

            paras.append(_TextPara(
                runs=runs, align=_PP_ALIGN.get(p_attr("algn") or "l", TA_LEFT), left=max(0.0, left), indent=indent,
                bullet=bullet, size=size, before=gap("spcBef"), after=gap("spcAft"), spacing=spacing, color=color,
            ))
        while paras and not paras[-1].runs:
            paras.pop()
        return paras

    def _layout(self, paras: list, width: float, scale: float) -> tuple:
        """→ ([(Paragraph или None, высота, отступ перед)], общая высота)."""
        laid = []
        total = 0.0
        width = max(width, 12.0)
        for index, para in enumerate(paras):
            size = para.size * scale
            before = para.before * scale if index else 0.0
            if not para.runs:
                height = size * 1.2 * para.spacing
                laid.append((None, height, before))
                total += height + before
                continue
            pieces = []
            for run in para.runs:
                if run.italic:
                    face = SANS_BOLD_ITALIC if run.bold else SANS_ITALIC
                elif run.serif:
                    face = SERIF_BOLD if run.bold else SERIF
                else:
                    face = CONDENSED_BOLD if run.bold else CONDENSED
                markup = _markup(run.text, self.ctx, serif=run.serif and not run.italic)
                if not markup:
                    continue
                if run.vert:
                    markup = f"<{run.vert}>{markup}</{run.vert}>"
                if run.strike:
                    markup = f"<strike>{markup}</strike>"
                if run.underline:
                    markup = f"<u>{markup}</u>"
                pieces.append(f'<font name="{face}" size="{run.size * scale:.2f}" '
                              f'color="{_rgb_hex(run.color)}">{markup}</font>')
            bullet_indent = 0.0
            left = min(para.left, width * 0.5)
            first = 0.0
            if para.bullet:
                bullet_indent = max(0.0, left + para.indent)
                bullet_width = _text_width(para.bullet, CONDENSED, size)
                # reportlab ставит текст первой строки не ближе 0,6 кегля от маркера — выравниваем по нему и остальные
                left = max(left, bullet_indent + bullet_width + size * 0.6 + 0.5)
            else:
                first = max(-left, min(para.indent, width * 0.3))
            style = ParagraphStyle(
                "pp", fontName=CONDENSED, fontSize=size, leading=size * 1.2 * para.spacing, alignment=para.align,
                leftIndent=left, firstLineIndent=first, bulletIndent=bullet_indent,
                bulletFontName=CONDENSED, bulletFontSize=size, bulletColor=_rl_color(para.color),
                textColor=_rl_color(para.color), splitLongWords=1,
            )
            flow = Paragraph("".join(pieces), style, bulletText=para.bullet)
            _used, height = flow.wrap(width, 100000.0)
            after = para.after * scale
            laid.append((flow, height + after, before))
            total += height + after + before
        return laid, total

    def _fit(self, paras: list, width: float, height: float, first_scale: float = 1.0) -> tuple:
        """Подбирает масштаб шрифта (не меньше 50 %), при котором текст входит по высоте."""
        laid, total, used = [], 0.0, first_scale
        for scale in _PP_SCALES:
            used = scale * first_scale
            laid, total = self._layout(paras, width, used)
            if total <= height + 0.5:
                break
        return laid, total, used

    def _draw_laid(self, laid: list, x: float, top: float) -> None:
        y = top
        for flow, height, before in laid:
            y -= before
            if flow is not None:
                flow.drawOn(self.c, x, y - flow.height)
            y -= height

    def _is_obstacle(self, other: _Item) -> bool:
        tag = other.el.tag
        if tag == _qn("p:sp"):
            fill, stroke, has_text = self._shape_look(other)
            return has_text or fill is not None or stroke is not None
        return tag in (_qn("p:pic"), _qn("p:graphicFrame"))

    def _surroundings(self, item: _Item, items: list, index: int) -> tuple:
        """Свободное место вокруг фигуры → (слева, справа, снизу, цвет подложки или None)."""
        edge = min(self.W, self.H) * 0.03
        left_limit, right_limit, bottom_limit = edge, self.W - edge, self.H - edge
        x0, y0, x1, y1 = item.x, item.y, item.x + item.w, item.y + item.h
        backdrop = None
        container = None
        def covers(other: _Item) -> bool:
            return (other.x <= x0 + 1.5 and other.y <= y0 + 1.5
                    and other.x + other.w >= x1 - 1.5 and other.y + other.h >= y1 - 1.5)

        for other in reversed(items[:index]):
            if other.rot or not self._is_obstacle(other):
                continue
            if covers(other):
                container = other
                break
        if container is not None:
            inset = 4.0
            left_limit = max(left_limit, container.x + inset)
            right_limit = min(right_limit, container.x + container.w - inset)
            bottom_limit = min(bottom_limit, container.y + container.h - inset)
            if container.el.tag == _qn("p:sp"):
                fill = self._shape_look(container)[0]
                backdrop = fill.rgb if fill is not None and fill.rgb is not None and fill.alpha > 0.5 else None
            else:
                backdrop = False        # под текстом рисунок: цвет фона неизвестен
        for other in items:
            if other is item or other is container or not self._is_obstacle(other):
                continue
            ox0, oy0, ox1, oy1 = other.x, other.y, other.x + other.w, other.y + other.h
            if covers(other):
                continue        # ещё одна подложка под фигурой
            overlap_x = ox0 < x1 - 1.0 and ox1 > x0 + 1.0
            overlap_y = oy0 < y1 - 1.0 and oy1 > y0 + 1.0
            if overlap_x and oy0 >= y1 - 1.5:
                bottom_limit = min(bottom_limit, oy0 - 3.0)
            if overlap_y and ox0 >= x1 - 1.5:
                right_limit = min(right_limit, ox0 - 3.0)
            if overlap_y and ox1 <= x0 + 1.5:
                left_limit = max(left_limit, ox1 + 3.0)
        return max(0.0, x0 - left_limit), max(0.0, right_limit - x1), max(0.0, bottom_limit - y1), backdrop

    def _draw_text(self, item: _Item, items: list, index: int, tx_body, ph, bases: list,
                   fill: _Fill | None, stroke: _Stroke | None) -> None:
        body_chain = [tx_body.find(_qn("a:bodyPr"))] + [_find(base, "p:txBody/a:bodyPr") for base, _part in bases]
        body_chain = [b for b in body_chain if b is not None]

        def body_attr(name: str, default=None):
            for element in body_chain:
                if element.get(name) is not None:
                    return element.get(name)
            return default

        inset_l = _int(body_attr("lIns"), 91440) / _EMU
        inset_r = _int(body_attr("rIns"), 91440) / _EMU
        inset_t = _int(body_attr("tIns"), 45720) / _EMU
        inset_b = _int(body_attr("bIns"), 45720) / _EMU
        anchor = body_attr("anchor", "t")
        wrap = body_attr("wrap", "square")
        vertical = body_attr("vert", "horz")
        first_scale = 1.0
        autofit = None
        for element in body_chain:
            kinds = [_local(ch) for ch in element if _local(ch) in ("noAutofit", "normAutofit", "spAutoFit")]
            if kinds:
                autofit = kinds[0]
                break
        if body_chain:
            auto = body_chain[0].find(_qn("a:normAutofit"))
            if auto is not None and auto.get("fontScale"):
                first_scale = max(0.3, min(1.0, _int(auto.get("fontScale"), 100000) / 100000.0))

        visible_box = (fill is not None and (fill.image is not None or fill.alpha > 0.15)) or stroke is not None
        if visible_box:        # текст держим внутри видимого контура (круг, ромб, стрелка…)
            extra = item.cache.get("text_inset", (0.0, 0.0, 0.0, 0.0))
            inset_l, inset_t, inset_r, inset_b = (inset_l + extra[0], inset_t + extra[1],
                                                  inset_r + extra[2], inset_b + extra[3])
        left_room, right_room, down_room, backdrop = (0.0, 0.0, 0.0, None)
        if not item.rot:
            left_room, right_room, down_room, backdrop = self._surroundings(item, items, index)
        if fill is not None and fill.rgb is not None and fill.alpha > 0.5:
            background = fill.rgb
        elif fill is not None and fill.image is not None:
            background = None
        elif backdrop is False:
            background = None
        else:
            background = backdrop if backdrop is not None else self.bg_rgb

        style = item.el.find(_qn("p:style"))
        base_color = None
        if style is not None:
            base_color = self._color(style.find(_qn("a:fontRef")))[0]
        sources, kind = self._sources(tx_body, ph, bases)
        paras = self._paragraphs(tx_body, sources, kind, base_color, background)
        if not paras:
            return

        c = self.c
        w, h = item.w, item.h
        if vertical in ("vert", "eaVert", "wordArtVert", "vert270") and not item.flip_h:
            # вертикальный текст: поворачиваем рамку текста на 90° и меняем местами её стороны
            c.translate(w / 2.0, h / 2.0)
            c.rotate(90 if vertical == "vert270" else -90)
            w, h = h, w
            c.translate(-w / 2.0, -h / 2.0)
            left_room = right_room = down_room = 0.0
        shift = 0.0
        extra_down = 0.0
        if not visible_box and not item.rot:
            if wrap == "none":      # надпись без переноса: в PowerPoint текст выходит за рамку
                align = paras[0].align
                if align == TA_RIGHT:
                    w, shift = w + left_room, -left_room
                elif align == TA_CENTER:
                    grow = min(left_room, right_room)
                    w, shift = w + 2 * grow, -grow
                else:
                    w += right_room
            if anchor == "t" and autofit != "normAutofit":
                extra_down = down_room      # рамка без заливки: текст может продолжаться ниже, как в PowerPoint
        inner_w = max(w - inset_l - inset_r, 12.0)
        inner_h = max(h - inset_t - inset_b, 4.0)
        laid, total, _scale = self._fit(paras, inner_w, inner_h + extra_down, first_scale)
        if total > inner_h + extra_down + 0.5:
            self.warn("текст не помещается в фигуру даже уменьшенным шрифтом — проверьте слайд.")
            top = h - inset_t
        elif anchor == "ctr" and total <= inner_h:
            top = h - inset_t - (inner_h - total) / 2.0
        elif anchor == "b" and total <= inner_h:
            top = inset_b + total
        else:
            top = h - inset_t
        self._draw_laid(laid, inset_l + shift, top)

    # ── рисунки ─────────────────────────────────────────────────────────────
    def _note_box(self, item: _Item, caption: str) -> None:
        """Серая рамка с подписью на месте объекта, который нельзя перенести."""
        c = self.c
        c.saveState()
        c.setFillColorRGB(0.93, 0.94, 0.96)
        c.setStrokeColorRGB(0.62, 0.65, 0.7)
        c.setLineWidth(0.75)
        c.setDash([4, 3])
        c.rect(0, 0, item.w, item.h, stroke=1, fill=1)
        c.restoreState()
        size = max(7.0, min(14.0, min(item.w, item.h) * 0.11))
        style = ParagraphStyle("pp-note", fontName=CONDENSED, fontSize=size, leading=size * 1.25,
                               alignment=TA_CENTER, textColor=colors.Color(0.36, 0.39, 0.44))
        flow = Paragraph(_markup(caption, self.ctx), style)
        _used, height = flow.wrap(max(item.w - 8.0, 12.0), 100000.0)
        if height <= item.h:
            flow.drawOn(c, 4.0, (item.h - height) / 2.0)

    def _draw_pic(self, item: _Item, decorative: bool) -> None:
        el = item.el
        c = self.c
        nv_pr = _find(el, "p:nvPicPr/p:nvPr")
        media = nv_pr is not None and any(_local(ch) in ("videoFile", "audioFile", "wavAudioFile", "quickTimeFile")
                                          for ch in nv_pr)
        if media and not decorative:
            self.warn("видео или звук не перенесены (показан только кадр-заставка).")
        blip = _find(el, "p:blipFill/a:blip")
        rid = blip.get(_qn("r:embed")) if blip is not None else None
        self._enter(item)
        blob = None
        if rid:
            try:
                blob = item.shape.part.related_part(rid).blob
            except Exception:  # noqa: BLE001
                blob = None
        if blob is None:
            if not decorative:
                self.warn("рисунок, вставленный ссылкой на внешний файл, не перенесён.")
                self._note_box(item, "Рисунок недоступен")
            return
        crop = None
        rect = _find(el, "p:blipFill/a:srcRect")
        if rect is not None:
            crop = tuple(_int(rect.get(k), 0) / 100000.0 for k in ("l", "t", "r", "b"))
        loaded = _load_image(blob, item.w, crop)
        if loaded is None:
            if not decorative:
                self.warn("рисунок в формате EMF/WMF (или повреждённый) не перенесён.")
                self._note_box(item, "Рисунок в формате, который нельзя показать в PDF")
            return
        sp_pr = el.find(_qn("p:spPr"))
        geom = sp_pr.find(_qn("a:prstGeom")) if sp_pr is not None else None
        prst = geom.get("prst") if geom is not None else "rect"
        c.saveState()
        if prst in _PP_ROUNDS or prst in _PP_ELLIPSES:
            path, _closed = self._geometry(item, sp_pr)
            if path is not None:
                c.clipPath(path, stroke=0, fill=0)
        if item.flip_h or item.flip_v:
            c.translate(item.w if item.flip_h else 0, item.h if item.flip_v else 0)
            c.scale(-1 if item.flip_h else 1, -1 if item.flip_v else 1)
        c.drawImage(ImageReader(loaded[0]), 0, 0, item.w, item.h, mask="auto")
        c.restoreState()
        stroke = self._stroke(sp_pr, el.find(_qn("p:style")))
        if stroke is not None:
            path, _closed = self._geometry(item, sp_pr)
            if path is not None:
                self._paint(path, None, stroke, item)

    # ── таблицы, диаграммы, SmartArt ──────────────────────────────────────────
    def _draw_frame(self, item: _Item, items: list, index: int) -> None:
        data = _find(item.el, "a:graphic/a:graphicData")
        uri = (data.get("uri") if data is not None else "") or ""
        if uri.endswith("/table"):
            self._draw_table(item, items, index, data.find(_qn("a:tbl")))
            return
        self._enter(item)
        if uri.endswith("/chart"):
            self.warn("диаграмма не перенесена.")
            self._note_box(item, "Диаграмма — см. исходную презентацию")
        elif uri.endswith("/diagram"):
            self._draw_smartart(item, data)
        else:
            self.warn("встроенный объект не перенесён.")
            self._note_box(item, "Встроенный объект — см. исходную презентацию")

    def _draw_smartart(self, item: _Item, data) -> None:
        texts: list[str] = []
        try:
            from lxml import etree

            rel = data.find(_qn("dgm:relIds"))
            root = etree.fromstring(item.shape.part.related_part(rel.get(_qn("r:dm"))).blob)
            for point in root.iter(_qn("dgm:pt")):
                if (point.get("type") or "node") not in ("node", "asst"):
                    continue
                text = " ".join("".join(t.text or "" for t in point.iter(_qn("a:t"))).split())
                if text:
                    texts.append(text)
        except Exception:  # noqa: BLE001 — данные схемы не читаются
            texts = []
        if not texts:
            self.warn("схема SmartArt не перенесена.")
            self._note_box(item, "Схема SmartArt — см. исходную презентацию")
            return
        self.warn("схема SmartArt показана списком.")
        c = self.c
        c.saveState()
        c.setStrokeColorRGB(0.62, 0.65, 0.7)
        c.setLineWidth(0.75)
        c.setDash([4, 3])
        c.rect(0, 0, item.w, item.h, stroke=1, fill=0)
        c.restoreState()
        color = _readable_on(self._scheme("tx1"), self.bg_rgb)
        paras = [_TextPara(runs=[_TextRun(text, 18.0, False, False, False, False, None, color, False)],
                           align=TA_LEFT, left=20.0, indent=-16.0, bullet="•", size=18.0, before=4.0, after=0.0,
                           spacing=1.0, color=color)
                 for text in texts]
        laid, total, _scale = self._fit(paras, item.w - 16.0, item.h - 12.0)
        self._draw_laid(laid, 8.0, item.h - 6.0 - max(0.0, (item.h - 12.0 - total) / 2.0))

    def _draw_table(self, item: _Item, items: list, index: int, tbl) -> None:
        if tbl is None:
            return
        grid = [max(1, _int(g.get("w"), 0)) for g in _findall(tbl, "a:tblGrid/a:gridCol")]
        rows = tbl.findall(_qn("a:tr"))
        if not grid or not rows:
            return
        c = self.c
        ncols, nrows = len(grid), len(rows)
        col_w = [g * item.w / sum(grid) for g in grid]
        declared = getattr(item.shape, "height", None) or 0
        ky = item.h / (declared / _EMU) if declared else 1.0
        row_min = [_int(tr.get("h"), 0) / _EMU * ky for tr in rows]
        pr = tbl.find(_qn("a:tblPr"))

        def flag(name: str) -> bool:
            return pr is not None and pr.get(name) in _ON

        style_el = pr.find(_qn("a:tableStyleId")) if pr is not None else None
        style_id = ((style_el.text if style_el is not None else "") or "").strip().upper()
        plain = style_id in ("", _PP_TABLE_PLAIN_GRID)
        bare = style_id == _PP_TABLE_NO_GRID
        accent = self._scheme(_PP_TABLE_ACCENTS.get(style_id, "accent1")) or _hex_rgb("4F81BD")
        ink = self._scheme("tx1") or _INK
        part = item.shape.part

        cells = []
        for r, tr in enumerate(rows):
            for col, tc in enumerate(tr.findall(_qn("a:tc"))):
                if col >= ncols or tc.get("hMerge") in _ON or tc.get("vMerge") in _ON:
                    continue
                span_c = max(1, min(ncols - col, _int(tc.get("gridSpan"), 1)))
                span_r = max(1, min(nrows - r, _int(tc.get("rowSpan"), 1)))
                tc_pr = tc.find(_qn("a:tcPr"))
                explicit, fill = self._fill(tc_pr, None, part)
                rgb = fill.rgb if fill is not None else None
                bold = False
                text_color = ink
                if not explicit and not plain and not bare:
                    data_row = r - (1 if flag("firstRow") else 0)
                    if flag("firstRow") and r == 0:
                        rgb, bold, text_color = accent, True, _WHITE
                    elif flag("firstCol") and col == 0:
                        rgb, bold, text_color = accent, True, _WHITE
                    else:
                        rgb = _mix(accent, _WHITE, 0.27 if (flag("bandRow") and data_row % 2 == 0) else 0.125)
                        bold = flag("lastRow") and r == nrows - 1
                background = rgb if rgb is not None else self.bg_rgb
                body = tc.find(_qn("a:txBody"))
                paras = []
                if body is not None:
                    own = body.find(_qn("a:lstStyle"))
                    paras = self._paragraphs(body, [own] if own is not None else [], "other",
                                             _readable_on(text_color, background), background, bold)

                def margin(name: str, default: int, tc_pr=tc_pr) -> float:
                    return _int(tc_pr.get(name) if tc_pr is not None else None, default) / _EMU

                cells.append({
                    "r": r, "c": col, "rs": span_r, "cs": span_c, "rgb": rgb, "paras": paras, "pr": tc_pr,
                    "ml": margin("marL", 91440), "mr": margin("marR", 91440),
                    "mt": margin("marT", 45720), "mb": margin("marB", 45720),
                    "anchor": (tc_pr.get("anchor") if tc_pr is not None else None) or "t",
                })

        limit = item.h + (self._surroundings(item, items, index)[2] if not item.rot else 0.0)
        heights = list(row_min)
        for scale in _PP_SCALES:
            heights = [m if scale == 1.0 else m * scale for m in row_min]
            for cell in cells:
                width = sum(col_w[cell["c"]:cell["c"] + cell["cs"]]) - cell["ml"] - cell["mr"]
                cell["laid"], cell["total"] = self._layout(cell["paras"], width, scale)
                cell["need"] = cell["total"] + cell["mt"] + cell["mb"]
                if cell["rs"] == 1:
                    heights[cell["r"]] = max(heights[cell["r"]], cell["need"])
            for cell in cells:
                if cell["rs"] > 1:
                    have = sum(heights[cell["r"]:cell["r"] + cell["rs"]])
                    if cell["need"] > have:
                        heights[cell["r"] + cell["rs"] - 1] += cell["need"] - have
            if sum(heights) <= limit + 0.5:
                break
        else:
            self.warn("таблица не помещается на слайд даже уменьшенным шрифтом — проверьте слайд.")

        self._enter(item)
        xs = [0.0]
        for width in col_w:
            xs.append(xs[-1] + width)
        ys = [item.h]
        for height in heights:
            ys.append(ys[-1] - height)
        for cell in cells:
            x0, x1 = xs[cell["c"]], xs[cell["c"] + cell["cs"]]
            y_top, y_bottom = ys[cell["r"]], ys[cell["r"] + cell["rs"]]
            if cell["rgb"] is not None:
                c.setFillColorRGB(*cell["rgb"])
                c.rect(x0, y_bottom, x1 - x0, y_top - y_bottom, stroke=0, fill=1)
        default_border = None if bare else _Stroke(ink, 0.75) if plain else _Stroke(_WHITE, 1.0)
        for cell in cells:
            x0, x1 = xs[cell["c"]], xs[cell["c"] + cell["cs"]]
            y_top, y_bottom = ys[cell["r"]], ys[cell["r"] + cell["rs"]]
            for tag, line in (("a:lnL", (x0, y_bottom, x0, y_top)), ("a:lnR", (x1, y_bottom, x1, y_top)),
                              ("a:lnT", (x0, y_top, x1, y_top)), ("a:lnB", (x0, y_bottom, x1, y_bottom))):
                border = default_border
                ln = cell["pr"].find(_qn(tag)) if cell["pr"] is not None else None
                if ln is not None:
                    if ln.find(_qn("a:noFill")) is not None:
                        border = None
                    elif ln.find(_qn("a:solidFill")) is not None:
                        rgb, alpha = self._color(ln.find(_qn("a:solidFill")))
                        if rgb is not None:
                            border = _Stroke(rgb, max(0.25, _int(ln.get("w"), 12700) / _EMU), None, alpha)
                if border is not None:
                    c.saveState()
                    self._apply_stroke(border)
                    c.line(*line)
                    c.restoreState()
            space = (y_top - y_bottom) - cell["mt"] - cell["mb"]
            if cell["anchor"] == "ctr":
                top = y_top - cell["mt"] - max(0.0, (space - cell["total"]) / 2.0)
            elif cell["anchor"] == "b":
                top = y_top - cell["mt"] - max(0.0, space - cell["total"])
            else:
                top = y_top - cell["mt"]
            self._draw_laid(cell["laid"], x0 + cell["ml"], top)


def _pptx_title(slides: list) -> str:
    for slide in slides[:3]:
        try:
            for shape in slide.placeholders:
                kind = shape._element.find(_xpath("p:nvSpPr/p:nvPr/p:ph"))
                if kind is not None and (kind.get("type") or "") in ("title", "ctrTitle") and shape.has_text_frame:
                    text = " ".join(shape.text_frame.text.split())
                    if text:
                        return text
        except Exception:  # noqa: BLE001
            continue
    return ""


def _build_pptx(src: Path, out: Path, ctx: _Ctx) -> dict:
    from pptx import Presentation

    try:
        prs = Presentation(str(src))
        slides = list(prs.slides)
    except Exception as exc:  # noqa: BLE001
        raise _Fail("Не удалось открыть презентацию PPTX: файл повреждён. Сохраните его заново в PowerPoint.") from exc
    if not slides:
        raise _Fail("В презентации нет ни одного слайда.")
    register_fonts()
    width = (prs.slide_width or 9144000) / _EMU
    height = (prs.slide_height or 6858000) / _EMU
    canv = _rl_canvas.Canvas(str(out), pagesize=(width, height), pageCompression=1)
    canv.setTitle(_short(_clean(_pptx_title(slides), _Ctx()), 90) or src.stem)
    canv.setAuthor("")
    canv.setCreator("LMS")
    renderer = _Pptx(prs, canv, width, height, ctx)
    for number, slide in enumerate(slides, 1):
        canv.setPageSize((width, height))
        depth = len(canv.state_stack)
        try:
            renderer.render(slide, number)
        except Exception:  # noqa: BLE001 — слайд всё равно должен занять свою страницу
            log.exception("PPTX %s: слайд %s перенесён не полностью", src, number)
            ctx.warn(f"Слайд {number}: перенесён не полностью из-за ошибки разбора.")
        while len(canv.state_stack) > depth:
            canv.restoreState()
        canv.showPage()
    canv.save()
    return {"slides": len(slides)}


_BUILDERS = {"docx": _build_docx, "xlsx": _build_xlsx, "pptx": _build_pptx}
