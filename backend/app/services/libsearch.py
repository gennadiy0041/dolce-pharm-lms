"""Поиск по библиотеке: по словам запроса в любом порядке, с учётом окончаний, по коду СОП в любой записи
и по тексту самих документов.

Тот же алгоритм повторён на телефоне (frontend/src/local/search.ts): без сети поиск идёт по сохранённому
списку, в котором для каждого материала есть и его текст. Правила:

* регистр и «ё/е» не различаются, знаки препинания и дефисы — разделители;
* каждое слово запроса должно найтись хотя бы в одном поле (название, код СОП, категория, описание, текст);
* у слов из букв длиной от 5 символов отбрасываются два последних — «гигиены», «гигиене» и «гигиена»
  находят друг друга (простая замена морфологии, без словарей);
* код СОП сравнивается ещё и слитно: «СОП-ОК-015», «соп ок 015» и «сопок015» — один и тот же код;
* выше в списке — совпадения в названии и коде, ниже — только в тексте документа.
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Optional

from .. import config

log = logging.getLogger(__name__)

_WORD = re.compile(r"[0-9a-zа-я]+")
TEXT_LIMIT = 20000          # столько символов текста документа уходит на телефон для поиска без сети
FIELDS = (("title", 4.0), ("sop_code", 4.0), ("category", 2.0), ("description", 1.5), ("text", 1.0))
_lock = threading.Lock()


def norm(s: Optional[str]) -> str:
    return (s or "").casefold().replace("ё", "е")


def words(s: Optional[str]) -> list[str]:
    return _WORD.findall(norm(s))


def stem(token: str) -> str:
    if token.isalpha() and len(token) >= 5:
        return token[:max(4, len(token) - 2)]
    return token


_CODE = re.compile(r"[0-9a-zа-я]+(?:\s*[-‐–—/.]\s*[0-9a-zа-я]+)+")


class Query:
    """Разобранный запрос: коды через дефис («СОП-ОК-015», «ок-015») и отдельные слова."""

    def __init__(self, q: str) -> None:
        self.codes: list[str] = []

        def take(m: re.Match) -> str:
            group = m.group(0)
            if any(ch.isdigit() for ch in group):
                self.codes.append("".join(words(group)))
                return " "
            return group

        rest = _CODE.sub(take, norm(q))
        self.terms = [stem(t) for t in words(rest)]
        # для отрывка текста: сначала слова, затем первое слово каждого кода
        self.hints = self.terms + [w for c in _CODE.findall(norm(q)) for w in words(c)[:1]]

    def __bool__(self) -> bool:
        return bool(self.codes or self.terms)


def query_terms(q: str) -> Query:
    return Query(q)


def score(fields: dict[str, str], query: Query) -> Optional[float]:
    """Вес совпадения или None, если хотя бы одно слово или код запроса не найден.

    Код через дефис сравнивается слитно с каждым полем («СОП-ОК-015» находит и «сопок015» в коде, и
    «СОП-ОК-015» в тексте). Из отдельных слов короткие (1–2 буквы) ищутся только с начала слова, числа —
    только целиком: иначе «х» или «1» находились бы в любом тексте.
    """
    if not query:
        return 0.0
    terms = query.terms
    hay = {k: " " + " ".join(words(fields.get(k))) + " " for k, _ in FIELDS}
    solid = {k: "".join(words(fields.get(k))) for k, _ in FIELDS}
    total = 0.0
    for code in query.codes:
        best = max((w for k, w in FIELDS if code in solid[k]), default=0.0)
        if not best:
            return None
        total += best * 2                      # точный код весит больше отдельных слов
    joined = "".join(terms)
    whole = 0.0
    if len(terms) > 1 and any(ch.isdigit() for ch in joined):   # «СОП ОК 015» через пробелы — тоже код
        whole = max((w for k, w in FIELDS if joined in solid[k]), default=0.0) * len(terms)
    words_total = 0.0
    for t in terms:
        best = 0.0
        strict = t.isdigit() or len(t) <= 2
        for key, weight in FIELDS:
            h = hay[key]
            if (" " + t + " ") in h if t.isdigit() else (" " + t) in h:     # с начала слова — полный вес
                best = max(best, weight)
            elif not strict and (t in h or (key in ("title", "sop_code") and t in solid[key])):
                best = max(best, weight * 0.6)
        if not best:
            if not whole:
                return None
            words_total = whole
            break
        words_total += best
    return total + max(words_total, whole)


def snippet(text: str, query: Query, radius: int = 70) -> Optional[str]:
    """Отрывок текста документа вокруг первого найденного слова запроса."""
    low = norm(text)
    for t in query.hints:
        i = low.find(t)
        if i >= 0:
            start, end = max(0, i - radius), min(len(text), i + len(t) + radius)
            return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")
    return None


# ---------- текст документов ----------

def _cache_dir() -> Path:
    return config.DATA_ROOT / "text-cache"


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts = []
    size = 0
    for page in reader.pages[:200]:
        try:
            chunk = page.extract_text() or ""
        except Exception:  # noqa: BLE001 — страница без извлекаемого текста
            continue
        parts.append(chunk)
        size += len(chunk)
        if size > TEXT_LIMIT * 3:
            break
    return " ".join(" ".join(parts).split())


def document_text(view_path: Optional[Path], sha256: Optional[str]) -> str:
    """Текст PDF-копии (или исходного PDF). Извлекается один раз и хранится в каталоге данных по SHA-256."""
    if not view_path or not sha256:
        return ""
    cached = _cache_dir() / ("%s.txt" % sha256)
    try:
        if cached.exists():
            return cached.read_text(encoding="utf-8")
    except OSError:
        pass
    with _lock:
        try:
            text = _extract_pdf(view_path) if view_path.exists() else ""
        except Exception:  # noqa: BLE001 — повреждённый файл не должен ломать поиск
            log.warning("Не удалось извлечь текст из %s", view_path, exc_info=True)
            text = ""
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(cached)
        except OSError:
            pass
    return text
