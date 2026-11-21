"""Адреса сервера в локальной сети, QR-коды и страница подключения телефона."""
from __future__ import annotations

import io
import socket
from html import escape

from .. import config

HOTSPOT_IP = "192.168.137.1"      # постоянный адрес ноутбука при включённом «Мобильном хот-споте» Windows


def lan_ips() -> list[str]:
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")) and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    try:  # адрес интерфейса, через который идёт маршрут по умолчанию (без отправки пакетов)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith(("127.", "169.254.")) and ip not in ips:
            ips.insert(0, ip)
    except OSError:
        pass
    return ips


def lan_urls() -> list[dict]:
    """Адреса для телефона. Первым идёт постоянный — адрес хот-спота ноутбука: он не зависит от того,
    какой IP выдала сеть Wi-Fi, поэтому ярлык на телефоне продолжает работать после перезагрузки."""
    urls = [{"ip": ip, "stable": ip == HOTSPOT_IP, "app_url": "https://%s:%d/" % (ip, config.HTTPS_PORT),
             "setup_url": "http://%s:%d/phone" % (ip, config.HTTP_PORT)} for ip in lan_ips()]
    urls.sort(key=lambda u: not u["stable"])
    return urls


def qr_svg(text: str) -> str:
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def converter_name() -> str:
    try:
        from .convert import find_msoffice, find_soffice
        if all(find_msoffice(k) for k in ("docx", "xlsx", "pptx")):
            return "Microsoft Office (Word, Excel, PowerPoint)"
        return "LibreOffice" if find_soffice() else "встроенный конвертер (LibreOffice не найден)"
    except Exception:
        return "встроенный конвертер"


def phone_page(host: str) -> str:
    app_url = "https://%s:%d/" % (host, config.HTTPS_PORT)
    h = escape(host)
    return """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Подключение телефона — Dolce Pharm LMS</title>
<style>
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;font-size:18px;line-height:1.5;margin:0;background:#F5F3EE;color:#1B2A2A}
main{max-width:640px;margin:0 auto;padding:20px 18px 48px}
h1{font-size:26px;line-height:1.2;margin:8px 0 6px}
h2{font-size:20px;margin:26px 0 8px}
.card{background:#fff;border:1px solid #DDD7C9;border-radius:14px;padding:16px 18px;margin:14px 0}
.btn{display:block;text-align:center;background:#0E5F58;color:#fff;text-decoration:none;font-weight:700;padding:16px;border-radius:12px;margin:12px 0;min-height:24px}
.btn.alt{background:#fff;color:#0E5F58;border:2px solid #0E5F58}
ol{padding-left:22px;margin:8px 0}li{margin:6px 0}
code{background:#EFEBE0;padding:2px 6px;border-radius:6px;font-size:16px;word-break:break-all}
.note{color:#5B6B6A;font-size:16px}
</style></head><body><main>
<p class="note">Dolce Pharm · учебная платформа</p>
<h1>Подключение телефона</h1>
<p>Делается один раз. После этого платформа открывается по защищённому адресу, ставится на домашний экран
и работает без сети.</p>

<div class="card">
<h2 style="margin-top:0">Шаг 1. Установите сертификат</h2>
<a class="btn" href="/ca.crt">Скачать сертификат</a>
<p><b>Android.</b> Настройки → Безопасность → Дополнительные настройки → Шифрование и учётные данные →
Установить сертификат → <b>Сертификат ЦС</b> → «Всё равно установить» → выберите скачанный файл
<code>dolce-lms-root-ca.crt</code>. (Быстрее всего — набрать в поиске настроек «сертификат».)</p>
<p><b>iPhone.</b> Откройте эту страницу в Safari, разрешите загрузку профиля. Настройки → «Профиль загружен» →
Установить. Затем Настройки → Основные → Об этом устройстве → Доверие сертификатам → включите
«Dolce Pharm LMS Root CA».</p>
</div>

<div class="card">
<h2 style="margin-top:0">Шаг 2. Откройте платформу</h2>
<a class="btn" href="%(app)s">Открыть %(host)s</a>
<p class="note">Адрес: <code>%(app)s</code>. Если браузер пишет «Подключение не защищено» — сертификат
из шага 1 ещё не установлен.</p>
</div>

<div class="card">
<h2 style="margin-top:0">Шаг 3. Поставьте на домашний экран</h2>
<ol>
<li><b>Android, Chrome:</b> меню ⋮ → «Добавить на главный экран» → «Установить».</li>
<li><b>iPhone, Safari:</b> кнопка «Поделиться» → «На экран “Домой”».</li>
</ol>
<p class="note">После входа откройте «Профиль» → «Проверка автономной работы»: там видно, что Service Worker
зарегистрирован и хранилище готово.</p>
</div>
</main></body></html>""" % {"app": escape(app_url), "host": h}
