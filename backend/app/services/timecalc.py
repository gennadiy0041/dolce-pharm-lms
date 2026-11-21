"""Подсчёт активного времени: пересекающиеся интервалы одного сотрудника объединяются,
поэтому две открытые вкладки или ПК и телефон одновременно не удваивают время."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

Span = tuple[datetime, datetime]


def merge_spans(spans: Iterable[Span]) -> list[Span]:
    ordered = sorted((s, e) for s, e in spans if e > s)
    merged: list[Span] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def union_seconds(spans: Iterable[Span], frm: Optional[datetime] = None, to: Optional[datetime] = None) -> int:
    """Суммарная длительность объединения интервалов, обрезанных периодом [frm, to]."""
    clipped = []
    for start, end in spans:
        if frm and start < frm:
            start = frm
        if to and end > to:
            end = to
        if end > start:
            clipped.append((start, end))
    return int(round(sum((e - s).total_seconds() for s, e in merge_spans(clipped))))
