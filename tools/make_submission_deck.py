"""Презентация для сдачи кейса: ровно 6 слайдов по Положению хакатона (п. 7.3).

Порядок: проблема → решение → демо → метрики → импакт → план пилота 30–90 дней.
Оформление и картинки — те же, что в основной презентации (tools/make_deck.py), её код не меняется.

Запуск из корня проекта:  .venv\\Scripts\\python.exe tools\\make_submission_deck.py
Результат: docs/сдача/Dolce-Pharm_LMS_6_слайдов.pptx
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_deck import (AMBER, BRAND, BRAND_MID, ICONS, INK, LILAC, LINE, M, MUTED, SCREENS, SH, SOFT, SW, WHITE,  # noqa: E402
                       Inches, Presentation, icon, logo, new_slide, notes, oval, phone, picture, ratio, rect, text, title)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "сдача" / "Dolce-Pharm_LMS_6_слайдов.pptx"


def kicker(s, label, color=BRAND):
    text(s, M, 0.2, 8.0, 0.3, label, size=12, color=color, bold=True)


def build() -> Path:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(SW), Inches(SH)
    prs.core_properties.title = "Dolce-Pharm LMS: обучение смены без связи в цеху"
    prs.core_properties.author = "Команда проекта"
    cw, gap = 3.83, 0.27

    # ---------- 1. Проблема ----------
    s = new_slide(prs)
    logo(s, 11.2, 0.5, 1.48)
    kicker(s, "1 · ПРОБЛЕМА")
    title(s, "Инструкцию обновили. Кто её изучил?")
    cards = [("users", "≈ 300", "сотрудников на площадке"),
             ("smartphone", "20–70 лет", "многие редко пользуются приложениями"),
             ("wifi-off", "Нет связи", "в цеху: ни Wi-Fi, ни сотовой сети")]
    for i, (ic, big, label) in enumerate(cards):
        x = M + i * (cw + gap)
        rect(s, x, 1.8, cw, 2.7, fill=SOFT, radius=0.22, name="Карточка")
        icon(s, ic, x + 0.35, 2.1, 0.8)
        text(s, x + 0.35, 3.05, cw - 0.7, 0.8, big, size=40, color=BRAND, bold=True, anchor="m")
        text(s, x + 0.35, 3.85, cw - 0.7, 0.6, label, size=16, color=INK)
    rect(s, M, 4.8, SW - 2 * M, 1.75, fill=BRAND, radius=0.22, name="Почему готовые системы не подходят")
    text(s, M + 0.4, 4.98, 11.0, 0.3, "ПОЧЕМУ ГОТОВЫЕ LMS НЕ ПОДХОДЯТ", size=12, color=LILAC, bold=True)
    text(s, M + 0.4, 5.38, SW - 2 * M - 0.8, 1.1,
         ["Плохо работают без сети на личном телефоне. Курс нужно собирать в SCORM у подрядчика.",
          "Результаты переносят в бумажный протокол вручную."],
         size=17, color=WHITE, spacing=1.05)
    text(s, M, 6.8, SW - 2 * M, 0.35,
         "Dolce-Pharm LMS · кейс Dolce Pharm · AI in Engineering Hackathon 2026. Прототип: все ФИО, участки и документы вымышлены.",
         size=12, color=MUTED)
    notes(s, "На площадке около трёхсот сотрудников, от двадцати до семидесяти лет, и в цеху нет связи. "
             "Инструкцию обновили — и отдел обучения должен знать, кто её изучил. Готовые системы плохо работают "
             "без сети, требуют SCORM, а результаты переносят в бумажный протокол вручную.")

    # ---------- 2. Решение ----------
    s = new_slide(prs)
    logo(s, 11.2, 0.5, 1.48)
    kicker(s, "2 · РЕШЕНИЕ")
    title(s, "Три шага — и протокол готов")
    steps = ["Назначить курс", "Сдать без сети", "Получить протокол"]
    caps = ["Методист собирает курс из обычных файлов прямо в браузере и назначает его смене",
            "Курс скачан на телефон заранее (PWA) — в цеху связь не нужна",
            "Результат приходит сам. Сертификат и протокол — PDF, отчёт — Excel/CSV"]
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
    picture(s, SCREENS / "certificate.png", M + 2 * (cw + gap), top + (area - h3) / 2, w=cw, radius=0.05, shadow=True,
            border=LINE, alt="Именной сертификат с логотипом Dolce-Pharm")
    notes(s, "Решение — три шага. Методист собирает курс из обычных файлов и назначает его смене. Оператор заранее "
             "скачивает курс на телефон и сдаёт тест в цеху без сети. Когда телефон снова видит сеть предприятия, "
             "результат приходит сам — с сертификатом и протоколом.")

    # ---------- 3. Демо ----------
    s = new_slide(prs)
    logo(s, 11.2, 0.5, 1.48)
    kicker(s, "3 · ДЕМО")
    title(s, "Оператор: одно задание — одна кнопка")
    shots = [("04-phone-home-before-download.png", "Скачал заранее"), ("06-phone-offline-reopened.png", "Открыл без сети"),
             ("13-phone-test-order.png", "Прошёл тест"), ("14-phone-answers-saved-offline.png", "Ответы сохранены")]
    ph = 4.15
    pw = ph * ratio(SCREENS / shots[0][0])
    pg = 0.95
    x0 = (SW - 4 * pw - 3 * pg) / 2
    for i, (shot, cap) in enumerate(shots):
        x = x0 + i * (pw + pg)
        phone(s, shot, x, 1.8, ph, alt=f"Экран оператора: {cap.lower()}")
        oval(s, x, 6.2, 0.46, BRAND, name="Номер")
        text(s, x, 6.2, 0.46, 0.46, str(i + 1), size=14, color=WHITE, bold=True, align="c", anchor="m")
        text(s, x + 0.58, 6.2, pw + 0.3, 0.46, cap, size=15, color=INK, bold=True, anchor="m")
        if i < 3:
            picture(s, ICONS / "arrow-right-violet.png", x + pw + (pg - 0.4) / 2, 1.8 + ph / 2 - 0.2, w=0.4)
    text(s, M, 6.88, SW - 2 * M, 0.35,
         "На защите — живой показ на телефоне в авиарежиме. Видео-демо: docs/сдача/Dolce-Pharm_LMS_демо.mp4",
         size=12, color=MUTED, align="c")
    notes(s, "Демо. Здесь мы переходим к живому показу на телефоне: курс скачан заранее, телефон в авиарежиме, "
             "приложение открывается с иконки, оператор проходит тест, ответы хранятся на телефоне. Затем включаем "
             "сеть — и строка появляется у методиста на ноутбуке. Если телефон недоступен — показываем этот слайд "
             "и видео-демо.")

    # ---------- 4. Метрики ----------
    s = new_slide(prs, dark=True)
    logo(s, 11.05, 0.55, 1.45, plaque=True)
    kicker(s, "4 · МЕТРИКИ: ТРЕБОВАНИЕ КЕЙСА → ИЗМЕРЕНО", color=AMBER)
    title(s, "Измерено, а не обещано", color=WHITE)
    stats = [("1,6 с", "синхронизация после возврата сети", "требование кейса: ≤ 30 мин. Телефон, приложение было закрыто"),
             ("9 мин 44 с", "в авиарежиме без сбоев", "запуск с иконки, материалы, видео и тест — без сети. Телефон"),
             ("61 с", "записано за 60 с изучения", "свёрнутая минута не засчитана. Автотест в Chromium"),
             ("0", "дублей и потерь", "повторная отправка ничего не дублирует. Автотесты и телефон")]
    sw_, sg = 2.83, 0.237
    for i, (big, label, sub) in enumerate(stats):
        x = M + i * (sw_ + sg)
        rect(s, x, 1.75, sw_, 2.8, fill=WHITE, radius=0.22, name="Показатель")
        text(s, x + 0.3, 1.95, sw_ - 0.6, 0.95, big, size=28 if len(big) > 6 else 38, color=BRAND, bold=True, anchor="m")
        text(s, x + 0.3, 2.95, sw_ - 0.6, 0.75, label, size=16, color=INK, bold=True)
        text(s, x + 0.3, 3.7, sw_ - 0.6, 0.8, sub, size=12, color=MUTED)
    bw = (SW - 2 * M - 0.25) / 2
    rect(s, M, 4.8, bw, 1.4, fill=BRAND_MID, radius=0.2, name="До")
    text(s, M + 0.35, 4.95, bw - 0.7, 0.3, "ДО", size=12, color=AMBER, bold=True)
    text(s, M + 0.35, 5.28, bw - 0.7, 0.85,
         "Курс — через SCORM у подрядчика. Результаты переносят в бумажный протокол вручную.",
         size=15, color=WHITE)
    rect(s, M + bw + 0.25, 4.8, bw, 1.4, fill=WHITE, radius=0.2, name="После")
    text(s, M + bw + 0.6, 4.95, bw - 0.7, 0.3, "ПОСЛЕ", size=12, color=BRAND, bold=True)
    text(s, M + bw + 0.6, 5.28, bw - 0.7, 0.85,
         "Методист собирает курс сам из обычных файлов. Протокол PDF и выгрузка CSV — одной кнопкой.",
         size=15, color=INK)
    text(s, M, 6.45, SW - 2 * M, 0.75,
         ["Телефон: POCO X7 Pro · Android · Chrome, 20.11.2026; один телефон, замеры после 2–10 мин без сети.",
          "Автотесты: pytest 87 из 87, Playwright 18 пройдено и 1 пропущен (Background Sync в тестовом браузере выключен)."],
         size=12, color=LILAC)
    notes(s, "Метрики. Кейс требует, чтобы результат попадал в отчёт не позже чем через 30 минут после появления сети. "
             "На настоящем телефоне — 1,6 секунды, даже когда приложение было закрыто. Почти десять минут в авиарежиме "
             "без сбоев. Время считается честно: свёрнутое приложение не засчитывается. Ни одного дубля.")

    # ---------- 5. Импакт ----------
    s = new_slide(prs)
    logo(s, 11.2, 0.5, 1.48)
    kicker(s, "5 · ИМПАКТ")
    title(s, "Что получает отдел обучения")
    effects = [("printer", "Протокол сам", "Сертификат и протокол аттестации — PDF, без переноса в бумажный журнал"),
               ("graduation-cap", "Без SCORM", "Курс из PDF, Word, Excel, PowerPoint, видео и ссылок — прямо в браузере"),
               ("server", "0 ₸", "Открытые компоненты. Данные — на сервере предприятия, без облаков")]
    for i, (ic, big, label) in enumerate(effects):
        x = M + i * (cw + gap)
        rect(s, x, 1.8, cw, 2.85, fill=SOFT, radius=0.22, name="Эффект")
        icon(s, ic, x + 0.35, 2.1, 0.8)
        text(s, x + 0.35, 3.0, cw - 0.7, 0.75, big, size=30, color=BRAND, bold=True, anchor="m")
        text(s, x + 0.35, 3.8, cw - 0.7, 0.8, label, size=14, color=INK)
    rect(s, M, 4.95, SW - 2 * M, 1.6, fill=BRAND, radius=0.22, name="Требования кейса")
    text(s, M + 0.4, 5.13, 11.0, 0.3, "ВСЕ ТРЕБОВАНИЯ КЕЙСА", size=12, color=LILAC, bold=True)
    cx = M + 0.4
    for label, w in (("6 из 6 обязательных", 2.7), ("3 из 3 доп.: 360°, роли и сертификат, тренажёр", 4.8),
                     ("REST API для 1С и Битрикс24", 3.4)):
        rect(s, cx, 5.58, w, 0.66, fill=WHITE, radius=0.33, name="Требование")
        text(s, cx, 5.58, w, 0.66, label, size=14, color=BRAND, bold=True, align="c", anchor="m")
        cx += w + 0.12
    text(s, M, 6.8, SW - 2 * M, 0.35,
         "Соответствие закону РК «О персональных данных и их защите»: сервер и база — в сети предприятия.",
         size=12, color=MUTED)
    notes(s, "Импакт для отдела обучения. Протокол аттестации формируется сам — без бумажного журнала. Курс собирает "
             "методист из обычных файлов, без SCORM и подрядчика. Ноль тенге на лицензии и облака, данные остаются "
             "на сервере предприятия. Закрыты все шесть обязательных требований и все три дополнительных.")

    # ---------- 6. План пилота ----------
    s = new_slide(prs, dark=True)
    logo(s, 11.05, 0.55, 1.45, plaque=True)
    kicker(s, "6 · ПЛАН ПИЛОТА", color=AMBER)
    title(s, "Пилот на 30–90 дней", color=WHITE)
    phases = [("ДНИ 1–30", "Запуск", ["Сервер в сети предприятия", "Сертификат предприятия для телефонов",
                                      "2–3 действующих СОП вместо учебных", "Одна смена одного участка"]),
              ("ДНИ 31–60", "Замеры", ["Доля сдавших в срок", "Синхронизация на телефонах сотрудников, в том числе iPhone",
                                       "Время изучения против секундомера", "Отзывы операторов и мастеров"]),
              ("ДНИ 61–90", "Решение", ["Остальные участки", "Импорт сотрудников из 1С через CSV",
                                        "Порядок обработки персональных данных", "Решение о внедрении"])]
    pw3 = (SW - 2 * M - 2 * 0.3) / 3
    for i, (days, head, items) in enumerate(phases):
        x = M + i * (pw3 + 0.3)
        rect(s, x, 1.75, pw3, 3.85, fill=WHITE, radius=0.22, name="Этап пилота")
        text(s, x + 0.35, 1.97, pw3 - 0.7, 0.3, days, size=12, color=BRAND, bold=True)
        text(s, x + 0.35, 2.3, pw3 - 0.7, 0.55, head, size=24, color=INK, bold=True)
        text(s, x + 0.35, 3.0, pw3 - 0.7, 2.5, ["• " + it for it in items], size=14, color=INK, spacing=1.1)
        if i < 2:
            picture(s, ICONS / "arrow-right-white.png", x + pw3 + 0.02, 3.5, w=0.26)
    text(s, M, 5.85, SW - 2 * M, 0.75,
         ["Ограничения: на iPhone ответы уходят при открытии приложения — фоновой отправки в iOS нет. "
          "Проверено на одном телефоне Android; долгий простой и режим экономии заряда не измерялись."],
         size=13, color=LILAC)
    text(s, M, 6.7, 6.0, 0.45, "СПАСИБО! Готовы к пробному запуску.", size=18, color=AMBER, bold=True)
    notes(s, "План пилота. Первый месяц — запуск: сервер в сети предприятия, сертификат для телефонов, ваши СОП, "
             "одна смена. Второй — замеры: доля сдавших в срок, синхронизация на телефонах сотрудников, включая "
             "iPhone, время против секундомера. Третий — остальные участки, импорт из 1С и решение о внедрении. "
             "Честно об ограничениях: на iPhone фоновой отправки нет, проверяли на одном телефоне. Спасибо!")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(OUT))
    return OUT


if __name__ == "__main__":
    print("Сохранено:", build())
