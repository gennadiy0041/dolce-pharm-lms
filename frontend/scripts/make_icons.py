"""Иконки приложения для домашнего экрана (PNG) в фирменном стиле Dolce-Pharm.

Запуск: python frontend/scripts/make_icons.py

Иконка — логотип компании на белом поле и фиолетовая плашка «ОБУЧЕНИЕ». Логотип берётся из
frontend/public/brand/dolce-pharm.png (файл с сайта компании) и почти не масштабируется, поэтому остаётся чётким.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "frontend" / "public" / "icons"
LOGO = ROOT / "frontend" / "public" / "brand" / "dolce-pharm.png"
FONT = ROOT / "backend" / "app" / "assets" / "fonts" / "DejaVuSans-Bold.ttf"
VIOLET, WHITE = (79, 0, 125, 255), (255, 255, 255, 255)


def icon(size: int, maskable: bool = False) -> Image.Image:
    """maskable: фон без скруглений, всё содержимое — внутри центрального круга (80 % стороны)."""
    s = size * 4                                    # рисуем крупно и уменьшаем — края получаются гладкими
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if maskable:
        d.rectangle([0, 0, s, s], fill=WHITE)
    else:
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.2), fill=WHITE, outline=VIOLET, width=max(4, int(s * 0.012)))

    logo = Image.open(LOGO).convert("RGBA")
    logo_w = int(s * (0.64 if maskable else 0.84))
    logo_h = round(logo.height * logo_w / logo.width)
    logo = logo.resize((logo_w, logo_h), Image.LANCZOS)
    band_h = int(s * (0.13 if maskable else 0.17))
    gap = int(s * 0.045)
    top = (s - (logo_h + gap + band_h)) // 2
    img.alpha_composite(logo, ((s - logo_w) // 2, top))

    band_w = int(s * (0.5 if maskable else 0.7))
    x0, y0 = (s - band_w) // 2, top + logo_h + gap
    d.rounded_rectangle([x0, y0, x0 + band_w, y0 + band_h], radius=band_h // 2, fill=VIOLET)
    text = "ОБУЧЕНИЕ"
    font_size = int(band_h * 0.56)
    font = ImageFont.truetype(str(FONT), font_size)
    while d.textlength(text, font=font) > band_w * 0.84 and font_size > 8:
        font_size -= 2
        font = ImageFont.truetype(str(FONT), font_size)
    d.text((s / 2, y0 + band_h / 2), text, font=font, fill=WHITE, anchor="mm")
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    icon(192).save(OUT / "icon-192.png")
    icon(512).save(OUT / "icon-512.png")
    icon(512, maskable=True).save(OUT / "maskable-512.png")
    apple = Image.new("RGBA", (180, 180), WHITE)
    apple.alpha_composite(icon(180, maskable=True))
    apple.convert("RGB").save(OUT / "apple-touch-icon.png")
    print("Иконки записаны в", OUT)


if __name__ == "__main__":
    main()
