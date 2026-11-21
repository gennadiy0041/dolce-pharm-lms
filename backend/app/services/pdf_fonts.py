"""Регистрация шрифтов DejaVu в reportlab: кириллица и казахские буквы в любых PDF."""
from __future__ import annotations

from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from ..config import FONTS_DIR

SANS = "DejaVu"
SANS_BOLD = "DejaVu-Bold"
SANS_ITALIC = "DejaVu-Oblique"
SANS_BOLD_ITALIC = "DejaVu-BoldOblique"
SERIF = "DejaVuSerif"
SERIF_BOLD = "DejaVuSerif-Bold"
CONDENSED = "DejaVuCondensed"
CONDENSED_BOLD = "DejaVuCondensed-Bold"

_FILES = {
    SANS: "DejaVuSans.ttf",
    SANS_BOLD: "DejaVuSans-Bold.ttf",
    SANS_ITALIC: "DejaVuSans-Oblique.ttf",
    SANS_BOLD_ITALIC: "DejaVuSans-BoldOblique.ttf",
    SERIF: "DejaVuSerif.ttf",
    SERIF_BOLD: "DejaVuSerif-Bold.ttf",
    CONDENSED: "DejaVuSansCondensed.ttf",
    CONDENSED_BOLD: "DejaVuSansCondensed-Bold.ttf",
}
_registered = False


def register_fonts() -> None:
    """Идемпотентно регистрирует шрифты. Вызывать перед построением любого PDF."""
    global _registered
    if _registered:
        return
    for name, filename in _FILES.items():
        pdfmetrics.registerFont(TTFont(name, str(FONTS_DIR / filename)))
    # <b>/<i> внутри Paragraph
    addMapping(SANS, 0, 0, SANS)
    addMapping(SANS, 1, 0, SANS_BOLD)
    addMapping(SANS, 0, 1, SANS_ITALIC)
    addMapping(SANS, 1, 1, SANS_BOLD_ITALIC)
    addMapping(SERIF, 0, 0, SERIF)
    addMapping(SERIF, 1, 0, SERIF_BOLD)
    addMapping(SERIF, 0, 1, SERIF)
    addMapping(SERIF, 1, 1, SERIF_BOLD)
    addMapping(CONDENSED, 0, 0, CONDENSED)
    addMapping(CONDENSED, 1, 0, CONDENSED_BOLD)
    addMapping(CONDENSED, 0, 1, CONDENSED)
    addMapping(CONDENSED, 1, 1, CONDENSED_BOLD)
    _registered = True
