"""Видео-демо для сдачи кейса (Положение хакатона, п. 7.2: 1–2 минуты).

Собирает ролик из снимков экранов последнего прогона автотестов (e2e/results/shots) с подписями:
путь «методист назначает → оператор проходит без сети → результат и протокол у методиста».
Это пошаговый показ по снимкам, а не запись экрана: на защите тот же путь показывается вживую.

Запуск из корня проекта:  .venv\\Scripts\\python.exe tools\\make_demo_video.py
Нужен ffmpeg в PATH. Результат: docs/сдача/Dolce-Pharm_LMS_демо.mp4
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "e2e" / "results" / "shots"
LOGO = ROOT / "docs" / "защита" / "assets" / "dolce-pharm.png"
OUT = ROOT / "docs" / "сдача" / "Dolce-Pharm_LMS_демо.mp4"

W, H = 1920, 1080
BRAND, SOFT, INK, MUTED, WHITE, LILAC, AMBER = "#4F007D", "#F1E9F8", "#1C1326", "#5F566B", "#FFFFFF", "#E6D7F3", "#F2B544"
FONTS = Path("C:/Windows/Fonts")
REG, BOLD = FONTS / "segoeui.ttf", FONTS / "segoeuib.ttf"
STEP_SEC, CARD_SEC = 5.5, 4.5

STEPS = [
    ("Отдел обучения", "36-overview.png", "«Обучение смены»: кто сдал, кто на проверке, кого ещё ждём"),
    ("Отдел обучения", "29-editor-new-course.png", "Курс собирается прямо в браузере: PDF, видео, Word, Excel, PowerPoint, ссылки, текст"),
    ("Отдел обучения", "01-editor-open-question.png", "Конструктор теста: одиночный и множественный выбор, открытый ответ, последовательность"),
    ("Отдел обучения", "03-assign-dialog.png", "Назначение курса подразделению или сотруднику со сроком"),
    ("Оператор, телефон", "04-phone-home-before-download.png", "Одно задание и одна кнопка: «Скачать для работы без сети»"),
    ("Оператор, телефон", "05-phone-ready-offline.png", "Пакет скачан и сверен по контрольным суммам: «Готово без сети»"),
    ("Оператор, без сети", "06-phone-offline-reopened.png", "Авиарежим: приложение открывается с иконки"),
    ("Оператор, без сети", "07-phone-pdf-offline.png", "Инструкция в PDF — без сети"),
    ("Оператор, без сети", "08-phone-video-offline.png", "Видео — без сети"),
    ("Оператор, без сети", "13-phone-test-order.png", "Тест без сети: вопрос на последовательность"),
    ("Оператор, без сети", "14-phone-answers-saved-offline.png", "Ответы сохранены на телефоне и уйдут сами, когда появится сеть"),
    ("Сеть вернулась", "16-pc-attempt-arrived.png", "Попытка сама появилась у методиста — без действий оператора"),
    ("Отдел обучения", "18-pc-reviewed-passed.png", "Методист проверил открытый ответ: «Сдал»"),
    ("Оператор, телефон", "20-phone-passed-certificate.png", "Результат и именной сертификат — у оператора"),
    ("Отдел обучения", "19-pc-protocol.png", "Протокол аттестации — PDF; отчёты — Excel/CSV"),
    ("Оператор, без сети", "22-library-offline.png", "Библиотека с категориями и поиском — тоже без сети"),
    ("Оператор, без сети", "11-phone-trainer-good-ending.png", "Диалоговый тренажёр: реплики, эмоции собеседника, развилки"),
    ("Оценка 360°", "32-assessment-360-summary.png", "Самооценка, руководитель, коллеги — сводный профиль"),
]


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def wrap(draw: ImageDraw.ImageDraw, text: str, f, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        probe = (cur + " " + word).strip()
        if draw.textlength(probe, font=f) <= width:
            cur = probe
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def paste_logo(img: Image.Image, x: int, y: int, w: int) -> None:
    logo = Image.open(LOGO).convert("RGBA")
    logo = logo.resize((w, round(w * logo.height / logo.width)), Image.LANCZOS)
    img.paste(logo, (x, y), logo)


def card(lines: list[tuple[str, Path, int, str]], plaque_logo: bool = True) -> Image.Image:
    img = Image.new("RGB", (W, H), BRAND)
    d = ImageDraw.Draw(img)
    if plaque_logo:
        d.rounded_rectangle((120, 110, 470, 250), radius=22, fill=WHITE)
        paste_logo(img, 150, 130, 290)
    y = 360
    for txt, fpath, size, color in lines:
        f = font(fpath, size)
        for ln in wrap(d, txt, f, W - 240):
            d.text((120, y), ln, font=f, fill=color)
            y += round(size * 1.3)
        y += round(size * 0.45)
    return img


def step_frame(i: int, n: int, who: str, shot: Path, caption: str) -> Image.Image:
    img = Image.new("RGB", (W, H), SOFT)
    d = ImageDraw.Draw(img)
    bar_h = 190
    area = (60, 40, W - 60, H - bar_h - 30)
    sc = Image.open(shot).convert("RGB")
    aw, ah = area[2] - area[0], area[3] - area[1]
    k = min(aw / sc.width, ah / sc.height)
    sc = sc.resize((round(sc.width * k), round(sc.height * k)), Image.LANCZOS)
    x = area[0] + (aw - sc.width) // 2
    y = area[1] + (ah - sc.height) // 2
    d.rounded_rectangle((x - 8, y - 8, x + sc.width + 8, y + sc.height + 8), radius=18,
                        fill=INK if sc.height > sc.width else "#D3BFE6")
    img.paste(sc, (x, y))
    d.rectangle((0, H - bar_h, W, H), fill=BRAND)
    d.ellipse((60, H - bar_h + 45, 160, H - bar_h + 145), fill=WHITE)
    num = str(i)
    fn = font(BOLD, 44)
    d.text((110 - d.textlength(num, font=fn) / 2, H - bar_h + 66), num, font=fn, fill=BRAND)
    d.text((200, H - bar_h + 30), who.upper(), font=font(BOLD, 28), fill=AMBER)
    fc = font(BOLD, 44)
    lines = wrap(d, caption, fc, W - 260 - 260)
    for j, ln in enumerate(lines[:2]):
        d.text((200, H - bar_h + 72 + j * 52), ln, font=fc, fill=WHITE)
    fs = font(REG, 24)
    d.text((W - 60 - d.textlength(f"{i} / {n}", font=fs), H - bar_h + 34), f"{i} / {n}", font=fs, fill=LILAC)
    return img


def main() -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("Нужен ffmpeg в PATH")
    missing = [s for _, s, _ in STEPS if not (SHOTS / s).exists()]
    if missing:
        raise SystemExit(f"Нет снимков (запустите run-tests.bat): {missing}")
    frames: list[tuple[Image.Image, float]] = [(card([
        ("Видео-демо", BOLD, 40, AMBER),
        ("Обучение смены — даже там, где нет связи", BOLD, 76, WHITE),
        ("Dolce-Pharm LMS · кейс Dolce Pharm · AI in Engineering Hackathon 2026", REG, 36, LILAC),
        ("Снимки экранов из автотестов (Playwright, сборка 2). Все ФИО и документы вымышлены.", REG, 28, LILAC),
    ]), CARD_SEC)]
    for i, (who, shot, cap) in enumerate(STEPS, 1):
        frames.append((step_frame(i, len(STEPS), who, SHOTS / shot, cap), STEP_SEC))
    frames.append((card([
        ("Измерено на телефоне POCO X7 Pro, 20.11.2026", BOLD, 36, AMBER),
        ("Результат в отчёте через 1,6 с после возврата сети", BOLD, 64, WHITE),
        ("Требование кейса — не более 30 минут. Приложение на телефоне было закрыто.", REG, 36, LILAC),
        ("0 ₸ на лицензии и облака · данные на сервере предприятия · REST API для 1С и Битрикс24", REG, 36, LILAC),
    ]), CARD_SEC + 1))

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        lst = []
        for k, (im, sec) in enumerate(frames):
            p = tmp / f"f{k:02d}.png"
            im.save(p)
            seg = tmp / f"s{k:02d}.mp4"
            fade_out = sec - 0.35
            subprocess.run([ffmpeg, "-v", "error", "-y", "-loop", "1", "-t", f"{sec}", "-i", str(p),
                            "-vf", f"fade=t=in:st=0:d=0.35,fade=t=out:st={fade_out}:d=0.35,format=yuv420p",
                            "-r", "25", "-c:v", "libx264", "-preset", "medium", "-crf", "20", str(seg)], check=True)
            lst.append(f"file '{seg.as_posix()}'")
        (tmp / "list.txt").write_text("\n".join(lst), encoding="utf-8")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(OUT)], check=True)
    return OUT


if __name__ == "__main__":
    print("Сохранено:", main())
