"""Презентация для защиты (PowerPoint, 16:9).

Запуск из корня проекта:  .venv\\Scripts\\python.exe tools\\make_deck.py
Результат: docs/защита/Dolce-Pharm_LMS_защита.pptx

Шесть основных слайдов и два запасных с экранами продукта. Текст выступления лежит в заметках
к каждому слайду. Картинки берутся из docs/защита/assets: логотип компании, снимки экранов из
автотестов (e2e/results/shots) и образец сертификата.
"""
from __future__ import annotations

from pathlib import Path

from lxml import etree
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "защита" / "assets"
SCREENS = ASSETS / "screens"
ICONS = ASSETS / "icons"
LOGO = ASSETS / "dolce-pharm.png"
OUT = ROOT / "docs" / "защита" / "Dolce-Pharm_LMS_защита.pptx"

# фирменные цвета Dolce-Pharm
BRAND, BRAND_MID, SOFT, LINE = "4F007D", "6B2D96", "F1E9F8", "D3BFE6"
INK, MUTED, WHITE, LILAC, AMBER = "1C1326", "5F566B", "FFFFFF", "E6D7F3", "F2B544"
FONT = "Segoe UI"
SW, SH, M = 13.333, 7.5, 0.65


def rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h)


def soft_shadow(shape, blur: float = 0.22, dist: float = 0.06, alpha: int = 22) -> None:
    sp = shape._element.spPr
    for old in sp.findall(qn("a:effectLst")):
        sp.remove(old)
    eff = etree.SubElement(sp, qn("a:effectLst"))
    sh = etree.SubElement(eff, qn("a:outerShdw"), blurRad=str(int(blur * 914400)), dist=str(int(dist * 914400)),
                          dir="5400000", algn="t", rotWithShape="0")
    clr = etree.SubElement(sh, qn("a:srgbClr"), val=INK)
    etree.SubElement(clr, qn("a:alpha"), val=str(alpha * 1000))


def rect(slide, x, y, w, h, fill=None, line=None, radius=0.0, shadow=False, name=None):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    s = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    if radius:
        s.adjustments[0] = min(0.5, radius / min(w, h))
    if fill:
        s.fill.solid()
        s.fill.fore_color.rgb = rgb(fill)
    else:
        s.fill.background()
    if line:
        s.line.color.rgb = rgb(line)
        s.line.width = Pt(1)
    else:
        s.line.fill.background()
    if shadow:
        soft_shadow(s)
    else:
        s.shadow.inherit = False
    if name:
        s.name = name
    return s


def oval(slide, x, y, d, fill, name=None):
    s = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    s.fill.solid()
    s.fill.fore_color.rgb = rgb(fill)
    s.line.fill.background()
    s.shadow.inherit = False
    if name:
        s.name = name
    return s


def _fill_frame(tf, paragraphs, size, color, bold, align, spacing):
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    if isinstance(paragraphs, str):
        paragraphs = [paragraphs]
    for i, para in enumerate(paragraphs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER, "r": PP_ALIGN.RIGHT}[align]
        if spacing:
            p.line_spacing = spacing
        runs = [(para, {})] if isinstance(para, str) else para
        for txt, opt in runs:
            r = p.add_run()
            r.text = txt
            r.font.name = FONT
            r.font.size = Pt(opt.get("size", size))
            r.font.bold = opt.get("bold", bold)
            r.font.color.rgb = rgb(opt.get("color", color))


def text(slide, x, y, w, h, paragraphs, size=16, color=INK, bold=False, align="l", anchor="t", spacing=None, name=None):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tb.text_frame.vertical_anchor = {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE, "b": MSO_ANCHOR.BOTTOM}[anchor]
    _fill_frame(tb.text_frame, paragraphs, size, color, bold, align, spacing)
    if name:
        tb.name = name
    return tb


def title(slide, paragraphs, x=M, y=0.5, w=10.2, h=1.15, size=32, color=INK, spacing=0.95):
    t = slide.shapes.title
    t.left, t.top, t.width, t.height = Inches(x), Inches(y), Inches(w), Inches(h)
    t.text_frame.vertical_anchor = MSO_ANCHOR.TOP
    _fill_frame(t.text_frame, paragraphs, size, color, True, "l", spacing)
    return t


def picture(slide, path, x, y, w=None, h=None, radius=0.0, shadow=False, border=None, alt=""):
    pic = slide.shapes.add_picture(str(path), Inches(x), Inches(y), Inches(w) if w else None, Inches(h) if h else None)
    if radius:
        geom = pic._element.spPr.find(qn("a:prstGeom"))
        geom.set("prst", "roundRect")
        av = geom.find(qn("a:avLst"))
        if av is None:
            av = etree.SubElement(geom, qn("a:avLst"))
        short = min(pic.width, pic.height) / 914400
        etree.SubElement(av, qn("a:gd"), name="adj", fmla="val %d" % int(min(0.5, radius / short) * 100000))
    if border:
        pic.line.color.rgb = rgb(border)
        pic.line.width = Pt(1)
    if shadow:
        soft_shadow(pic)
    pic._element.xpath("./p:nvPicPr/p:cNvPr")[0].set("descr", alt)
    return pic


def ratio(path) -> float:
    with Image.open(path) as im:
        return im.width / im.height


def phone(slide, shot, x, y, h, alt=""):
    """Снимок экрана телефона в тёмной рамке."""
    w = h * ratio(SCREENS / shot)
    pad = h * 0.022
    rect(slide, x - pad, y - pad, w + 2 * pad, h + 2 * pad, fill=INK, radius=h * 0.07, shadow=True, name="Рамка телефона")
    picture(slide, SCREENS / shot, x, y, h=h, radius=h * 0.05, alt=alt)
    return w


def icon(slide, name, x, y, d, bg=WHITE, tone="violet"):
    oval(slide, x, y, d, bg, name="Значок")
    k = 0.52
    picture(slide, ICONS / f"{name}-{tone}.png", x + d * (1 - k) / 2, y + d * (1 - k) / 2, w=d * k)


def logo(slide, x, y, w, plaque=False):
    h = w * 134 / 398
    if plaque:
        px, py = w * 0.1, h * 0.22
        rect(slide, x - px, y - py, w + 2 * px, h + 2 * py, fill=WHITE, radius=0.14, name="Плашка логотипа")
    picture(slide, LOGO, x, y, w=w, alt="Логотип Dolce-Pharm")


def new_slide(prs, dark=False):
    s = prs.slides.add_slide(prs.slide_layouts[5])          # «Только заголовок»
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = rgb(BRAND if dark else WHITE)
    return s


def notes(slide, speech: str) -> None:
    slide.notes_slide.notes_text_frame.text = speech


def build() -> Path:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(SW), Inches(SH)
    prs.core_properties.title = "Dolce-Pharm: обучение смены без связи в цеху"
    prs.core_properties.author = "Команда проекта"

    # ---------- 1. Титул ----------
    s = new_slide(prs, dark=True)
    oval(s, 7.9, 0.55, 6.4, BRAND_MID, name="Фон телефона")
    logo(s, 0.9, 0.78, 2.15, plaque=True)
    title(s, ["Обучение смены —", "даже там, где нет связи"], y=2.2, w=7.6, h=1.9, size=44, color=WHITE, spacing=1.0)
    text(s, M, 4.45, 6.9, 0.95, "Инструкция — на телефоне оператора. Результат и протокол — у отдела обучения.", size=20, color=LILAC)
    px = M
    for label, w in (("Работает в авиарежиме", 2.75), ("Без облаков · 0 ₸", 2.1), ("Проверено на телефоне", 2.75)):
        rect(s, px, 5.8, w, 0.52, fill=WHITE, radius=0.26, name="Метка")
        text(s, px, 5.8, w, 0.52, label, size=14, color=BRAND, bold=True, align="c", anchor="m")
        px += w + 0.2
    text(s, M, 6.75, 7.0, 0.35, "Прототип. Все ФИО, участки и документы в демонстрации вымышлены.", size=12, color=LILAC)
    phone(s, "06-phone-offline-reopened.png", 9.66, 0.8, 5.9, alt="Главный экран оператора: связи нет, курс готов к работе без сети")
    notes(s, "Здравствуйте. Мы сделали для Dolce-Pharm учебную платформу, которая работает там, где нет связи: "
             "оператор проходит инструкцию на телефоне в цеху, а отдел обучения получает результат и протокол.")

    # ---------- 2. Задача ----------
    s = new_slide(prs)
    logo(s, 11.2, 0.5, 1.48)
    title(s, "Инструкцию обновили. Кто её изучил?")
    cards = [("users", "≈ 300", "сотрудников на площадке"),
             ("smartphone", "20–70 лет", "многие редко пользуются приложениями"),
             ("wifi-off", "Нет связи", "в цеху: ни Wi-Fi, ни сотовой сети")]
    cw, gap = 3.83, 0.27
    for i, (ic, big, label) in enumerate(cards):
        x = M + i * (cw + gap)
        rect(s, x, 1.95, cw, 2.8, fill=SOFT, radius=0.22, name="Карточка")
        icon(s, ic, x + 0.35, 2.25, 0.8)
        text(s, x + 0.35, 3.2, cw - 0.7, 0.8, big, size=40, color=BRAND, bold=True, anchor="m")
        text(s, x + 0.35, 4.02, cw - 0.7, 0.6, label, size=16, color=INK)
    rect(s, M, 5.15, SW - 2 * M, 1.6, fill=BRAND, radius=0.22, name="Задача")
    text(s, M + 0.4, 5.33, 6.0, 0.3, "ЗАДАЧА ОТДЕЛА ОБУЧЕНИЯ", size=12, color=LILAC, bold=True)
    cx = M + 0.4
    chips = [("Обычная инструкция", 3.0), ("Назначить смене", 2.6), ("Протокол: фамилии, баллы, даты", 4.6)]
    for i, (label, w) in enumerate(chips):
        rect(s, cx, 5.78, w, 0.66, fill=WHITE, radius=0.33, name="Шаг задачи")
        text(s, cx, 5.78, w, 0.66, label, size=16, color=BRAND, bold=True, align="c", anchor="m")
        cx += w
        if i < len(chips) - 1:
            picture(s, ICONS / "arrow-right-white.png", cx + 0.1, 5.93, w=0.36)
            cx += 0.56
    notes(s, "На площадке около трёхсот сотрудников, от двадцати до семидесяти лет, и в цеху нет связи. "
             "Задача отдела обучения: взять обычную инструкцию, назначить её смене и получить протокол — "
             "с фамилиями, баллами и датами.")

    # ---------- 3. Решение ----------
    s = new_slide(prs)
    logo(s, 11.2, 0.5, 1.48)
    title(s, "Три шага — и протокол готов")
    steps = ["Назначить курс", "Сдать без сети", "Получить протокол"]
    caps = ["Методист собирает курс из обычных файлов и назначает его смене",
            "Курс скачан заранее — в цеху связь не нужна",
            "Результат приходит сам. Сертификат и протокол — PDF"]
    top, area = 2.6, 3.25
    for i in range(3):
        x = M + i * (cw + gap)
        oval(s, x, 1.78, 0.58, BRAND, name="Номер шага")
        text(s, x, 1.78, 0.58, 0.58, str(i + 1), size=18, color=WHITE, bold=True, align="c", anchor="m")
        text(s, x + 0.75, 1.78, cw - 0.75, 0.58, steps[i], size=20, color=INK, bold=True, anchor="m")
        text(s, x, 6.05, cw, 0.8, caps[i], size=14, color=MUTED)
    h1 = cw / ratio(SCREENS / "assign-dialog-2x.png")
    picture(s, SCREENS / "assign-dialog-2x.png", M, top + (area - h1) / 2, w=cw, radius=0.1, shadow=True, border=LINE,
            alt="Окно «Назначить курс»: срок, подразделение или отдельный сотрудник")
    ph = area
    pw = ph * ratio(SCREENS / "06-phone-offline-reopened.png")
    x2 = M + (cw + gap) + (cw - 2 * pw - 0.35) / 2
    phone(s, "06-phone-offline-reopened.png", x2, top, ph, alt="Телефон без связи: курс готов, кнопка «Начать изучение»")
    phone(s, "14-phone-answers-saved-offline.png", x2 + pw + 0.35, top, ph, alt="Телефон без связи: «Ответы сохранены на телефоне»")
    h3 = cw / ratio(SCREENS / "certificate.png")
    picture(s, SCREENS / "certificate.png", M + 2 * (cw + gap), top + (area - h3) / 2, w=cw, radius=0.05, shadow=True, border=LINE,
            alt="Именной сертификат с логотипом Dolce-Pharm")
    notes(s, "Решение — три шага. Методист назначает курс с ноутбука. Оператор заранее скачивает его на телефон "
             "и сдаёт тест в цеху без сети. Когда телефон снова видит сеть предприятия, результат приходит сам — "
             "с сертификатом и протоколом. Сейчас покажем это вживую.")

    # ---------- 4. Измерения ----------
    s = new_slide(prs, dark=True)
    logo(s, 11.05, 0.55, 1.45, plaque=True)
    title(s, "Измерено на настоящем телефоне", color=WHITE)
    stats = [("1,6 с", "от возврата сети до приёма результата", "приложение на телефоне было закрыто"),
             ("0", "дублей и потерь", "две сдачи — две строки в отчёте"),
             ("2,6 МБ", "весь курс на телефоне", "PDF, Word, Excel, PowerPoint, видео и тест"),
             ("0 ₸", "лицензий и облаков", "сервер и данные — на вашем оборудовании")]
    sw_, sg = 2.83, 0.237
    for i, (big, label, sub) in enumerate(stats):
        x = M + i * (sw_ + sg)
        rect(s, x, 2.15, sw_, 3.45, fill=WHITE, radius=0.22, name="Показатель")
        text(s, x + 0.3, 2.4, sw_ - 0.6, 1.0, big, size=44, color=BRAND, bold=True, anchor="m")
        text(s, x + 0.3, 3.5, sw_ - 0.6, 0.95, label, size=17, color=INK, bold=True)
        text(s, x + 0.3, 4.5, sw_ - 0.6, 0.9, sub, size=13, color=MUTED)
    text(s, M, 6.15, SW - 2 * M, 0.7, "POCO X7 Pro · Android 15 · Chrome · 20.11.2026. Один телефон; замеры после 2–10 минут без сети.",
         size=14, color=LILAC)
    notes(s, "То, что вы видели, мы измерили заранее на настоящем телефоне. Результат приходит через 1,6 секунды "
             "после возврата сети, даже если приложение закрыто. Ни одного дубля. Весь курс — 2,6 мегабайта. "
             "Бюджет — ноль тенге: ни облаков, ни подписок.")

    # ---------- 5. Критерии ----------
    s = new_slide(prs)
    logo(s, 11.2, 0.5, 1.48)
    title(s, "Все требования кейса — в продукте")
    # шесть обязательных требований, три дополнительных и задел на интеграцию; последняя плитка выделена
    reqs = [("graduation-cap", "обязательно", "Курсы без SCORM", "PDF, видео, Word, Excel, PowerPoint, ссылки, текст"),
            ("monitor", "обязательно", "Кабинет сотрудника", "На ПК и телефоне: курсы, тесты, результаты"),
            ("book-open", "обязательно", "Библиотека", "Категории и поиск, в том числе без сети"),
            ("list-checks", "обязательно", "Тесты", "4 типа вопросов, проверка методистом"),
            ("wifi-off", "обязательно", "Офлайн и синхронизация", "Авиарежим → результат приходит сам"),
            ("timer", "обязательно", "Время и отчёты", "Вход, выход, время изучения; выгрузка в Excel"),
            ("target", "дополнительно", "Оценка 180/360", "Самооценка, руководитель, коллеги"),
            ("award", "дополнительно", "Роли и документы", "3 роли, сертификат и протокол — PDF"),
            ("messages-square", "дополнительно", "Тренажёр", "Диалог с мастером, две развязки"),
            ("workflow", "интеграция", "1С и Битрикс24", "REST API с описанием — основа для подключения")]
    tw, tg, th = 2.31, 0.12, 2.3
    for i, (ic, tag, name, proof) in enumerate(reqs):
        x = M + (i % 5) * (tw + tg)
        y = 1.72 + (i // 5) * (th + 0.18)
        accent = tag == "интеграция"
        rect(s, x, y, tw, th, fill=BRAND if accent else SOFT, radius=0.18, name="Требование")
        icon(s, ic, x + 0.22, y + 0.22, 0.62)
        text(s, x + 0.9, y + 0.22, tw - 1.12, 0.62, tag, size=11, color=LILAC if accent else BRAND, bold=True, align="r", anchor="m")
        text(s, x + 0.22, y + 0.98, tw - 0.44, 0.56, name, size=15, color=WHITE if accent else INK, bold=True, spacing=0.95)
        text(s, x + 0.22, y + 1.56, tw - 0.44, 0.66, proof, size=12, color=LILAC if accent else MUTED, spacing=0.95)
    text(s, M, 6.7, SW - 2 * M, 0.35,
         "Только бесплатные открытые компоненты. Данные — на сервере предприятия, без зарубежных облаков. 105 автоматических проверок.",
         size=13, color=MUTED)
    notes(s, "Все требования кейса закрыты: шесть обязательных и три дополнительных — оценка 360, роли с сертификатом "
             "и протоколом, тренажёр. Сервер — это REST API с описанием: готовая основа для интеграции с 1С и "
             "Битрикс24. Сотрудники и отчёты уже выгружаются в Excel.")

    # ---------- 6. Финал ----------
    s = new_slide(prs, dark=True)
    oval(s, 7.9, 0.55, 6.4, BRAND_MID, name="Фон телефона")
    text(s, M, 0.85, 6.0, 0.5, "СПАСИБО!", size=18, color=AMBER, bold=True)
    title(s, ["Готовы к пробному", "запуску у вас"], y=1.4, w=7.2, h=1.75, size=40, color=WHITE, spacing=1.0)
    needs = [("server", "Сервер — в сети предприятия"), ("file-text", "Ваши СОП вместо учебных"),
             ("key-round", "Сертификат предприятия для телефонов")]
    for i, (ic, label) in enumerate(needs):
        y = 3.45 + i * 0.86
        icon(s, ic, M, y, 0.62, bg=BRAND_MID, tone="white")
        text(s, M + 0.85, y, 6.3, 0.62, label, size=20, color=WHITE, anchor="m")
    text(s, M, 6.1, 7.1, 0.8, "Ограничения: на iPhone ответы уходят при открытии приложения — фоновой отправки в iOS нет. "
                              "Проверено на одном телефоне Android.", size=14, color=LILAC)
    phone(s, "19a-phone-passed-on-test-screen.png", 9.66, 0.8, 5.9, alt="Экран оператора после проверки: «Сдал»")
    notes(s, "Прототип готов к пробному запуску. Для этого нужны три вещи: сервер в сети предприятия, ваши СОП "
             "вместо учебных и сертификат предприятия для телефонов. Честно об ограничениях: на iPhone ответы "
             "уходят, когда приложение откроют, и проверяли мы на одном телефоне. Спасибо! Готовы ответить на вопросы.")

    # ---------- 7. Запасной: экраны оператора ----------
    s = new_slide(prs)
    logo(s, 11.2, 0.5, 1.48)
    title(s, "Оператор: одно задание — одна кнопка")
    shots = [("04-phone-home-before-download.png", "Скачал заранее"), ("06-phone-offline-reopened.png", "Открыл без сети"),
             ("13-phone-test-order.png", "Прошёл тест"), ("14-phone-answers-saved-offline.png", "Ответы сохранены")]
    ph = 4.15
    pw = ph * ratio(SCREENS / shots[0][0])
    pg = 0.95
    x0 = (SW - 4 * pw - 3 * pg) / 2
    for i, (shot, cap) in enumerate(shots):
        x = x0 + i * (pw + pg)
        phone(s, shot, x, 1.85, ph, alt=f"Экран оператора: {cap.lower()}")
        oval(s, x, 6.3, 0.46, BRAND, name="Номер")
        text(s, x, 6.3, 0.46, 0.46, str(i + 1), size=14, color=WHITE, bold=True, align="c", anchor="m")
        text(s, x + 0.58, 6.3, pw + 0.3, 0.46, cap, size=15, color=INK, bold=True, anchor="m")
        if i < 3:
            picture(s, ICONS / "arrow-right-violet.png", x + pw + (pg - 0.4) / 2, 1.85 + ph / 2 - 0.2, w=0.4)
    notes(s, "Запасной слайд — если телефон недоступен или спросят про экраны оператора. На первом экране одно "
             "задание и одна кнопка. Курс скачан заранее, открывается в авиарежиме, тест сдаётся без сети, "
             "ответы хранятся на телефоне, пока не вернётся связь.")

    # ---------- 8. Запасной: экран отдела обучения ----------
    s = new_slide(prs)
    logo(s, 11.2, 0.5, 1.48)
    title(s, "Отдел обучения: вся смена на одном экране")
    bw = 7.9
    picture(s, SCREENS / "overview-2x.png", M, 1.8, w=bw, radius=0.1, shadow=True, border=LINE,
            alt="Экран «Обучение смены»: инструкция, обучение и протокол по каждому курсу")
    rx = M + bw + 0.5
    rw = SW - M - rx
    points = [("users", "Кто сдал и кого ждём", "поимённо, по действующей редакции"),
              ("clipboard-check", "Проверка ответа", "открытый ответ оценивает методист"),
              ("printer", "Протокол и сертификат", "PDF для печати, таблица для Excel")]
    for i, (ic, head, sub) in enumerate(points):
        y = 1.85 + i * 1.02
        icon(s, ic, rx, y, 0.62, bg=SOFT)
        text(s, rx + 0.8, y - 0.02, rw - 0.8, 0.36, head, size=16, color=INK, bold=True)
        text(s, rx + 0.8, y + 0.33, rw - 0.8, 0.36, sub, size=13, color=MUTED)
    ch = 1.95
    picture(s, SCREENS / "certificate.png", rx, 4.95, h=ch, radius=0.04, shadow=True, border=LINE, alt="Именной сертификат")
    notes(s, "Запасной слайд — экран начальника отдела обучения. По каждой действующей редакции видно, сколько "
             "человек сдали, кто на проверке и кого ещё ждём — поимённо. Отсюда же назначение, проверка ответа и "
             "протокол одной кнопкой.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(OUT))
    return OUT


if __name__ == "__main__":
    print("Сохранено:", build())
