"""CSV для Excel: UTF-8 с BOM и разделителем «;» — русский Excel открывает такой файл двойным щелчком
без искажения кириллицы. При чтении понимаем UTF-8, UTF-8 с BOM и Windows-1251, разделители «;», «,» и табуляцию."""
from __future__ import annotations

import csv
import io
from typing import Iterable, Sequence
from urllib.parse import quote

from fastapi.responses import Response


def to_csv_bytes(header: Sequence[str], rows: Iterable[Sequence[object]]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if v is None else _safe(v) for v in row])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def _safe(v: object) -> object:
    """Защита от формул: значение, начинающееся с =, +, -, @, Excel выполнил бы как формулу."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") and not _is_number(v):
        return "'" + v
    return v


def _is_number(s: str) -> bool:
    try:
        float(s.replace(",", "."))
        return True
    except ValueError:
        return False


def csv_response(data: bytes, filename: str) -> Response:
    ascii_name = filename.encode("ascii", "ignore").decode() or "export.csv"
    return Response(content=data, media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, quote(filename)),
    })


def decode_csv(data: bytes) -> tuple[str, str]:
    """Возвращает (текст, название кодировки)."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "UTF-8"
    # «Текст Юникод» из Excel и вывод PowerShell — это UTF-16 с меткой порядка байтов
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "UTF-16"
    try:
        text, name = data.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        text, name = data.decode("cp1251", errors="replace"), "Windows-1251"
    if "\x00" in text:
        raise UnicodeError("в файле есть нулевые байты — это не текстовый CSV")
    return text, name


def parse_csv(text: str) -> tuple[list[str], list[list[str]], str]:
    """Возвращает (заголовок, строки, разделитель). Пустые строки пропускаются."""
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = max((";", ",", "\t"), key=lambda d: first.count(d))
    if first.count(delimiter) == 0:
        delimiter = ";"
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [[c.strip() for c in row] for row in reader if any(c.strip() for c in row)]
    if not rows:
        return [], [], delimiter
    return rows[0], rows[1:], delimiter
