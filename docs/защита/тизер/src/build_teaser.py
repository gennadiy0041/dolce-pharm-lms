# -*- coding: utf-8 -*-
"""Тизер Dolce Pharm LMS, версия 3: цех → что сделала команда → вопрос перед живым показом.

Запуск:
    python build_teaser.py <work_dir> <out.mp4> [--prompter] [--still <кадр> <png>]

Что нужно в <work_dir>:
    clips/cNN/%04d.jpg   — кадры выпуска Qonaev Media (cut_clips.ps1)
    rec/                 — запись настоящего интерфейса (record_ui.mjs)
    music.mp3            — «Minimal Emotion», Alejandro Magaña (A. M.), Mixkit
Музыка режется по тактам и сводится этим же скриптом (score.wav в <work_dir>).

Сетка: 25 кадр/с; трек 127,97 уд/мин, такт = 1,8754 с. Все склейки стоят на долях.
"""
import site
site.addsitedir(site.getusersitepackages())
import bisect
import io
import json
import math
import re
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FPS, W, H = 25, 1920, 1080
ROOT = Path(__file__).resolve().parents[4]
ASSETS = ROOT / "docs" / "защита" / "assets"
ICONS = ASSETS / "icons"
POCO = ROOT / "releases" / "v1-run2-verified" / "phone"
MONO = "C:/Windows/Fonts/consola.ttf"
MONO_B = "C:/Windows/Fonts/consolab.ttf"

LILAC, AMBER, WHITE = (0xE6, 0xD7, 0xF3), (0xF2, 0xB5, 0x44), (255, 255, 255)
GREEN = (0x4E, 0xC9, 0x6B)

# ---------- музыкальная сетка ----------
TEMPO = 127.97
BEAT = 60.0 / TEMPO
BAR = 4 * BEAT
TRACK_DOWNBEAT = 1.426          # первая сильная доля трека, с


def T(k, beat=0.0):
    """Начало такта k ролика (с 1) плюс beat долей, в секундах."""
    return (k - 1) * BAR + beat * BEAT


def track_bar(n):
    return TRACK_DOWNBEAT + (n - 1) * BAR


MUSIC_END = T(18) + 2.0          # удар на 18-м такте и затухание
TOTAL = int(round((T(18) + 2.9) * FPS))

CREDIT = "Кадры производства: Qonaev Media — выпуск «Made in Алматинская область | Dolce Pharm», YouTube"

# строки диктора: (начало, конец окна, текст) — в секундах
VOICE = [
    (0.35, 3.6, "Сто миллионов изделий в год."),
    (3.85, 5.55, "Каждое — по инструкции."),
    (5.75, 7.45, "Инструкцию обновили."),
    (7.6, 9.3, "Смена уже в цеху."),
    (9.5, 11.15, "А связи там нет."),
    (11.35, 13.55, "Мы сделали платформу, которой связь не нужна."),
    (13.65, 15.0, "Методист назначает курс."),
    (15.1, 16.8, "Ответ сначала сохраняется в телефоне."),
    (16.95, 20.5, "Оператор скачивает курс заранее — все материалы у него в руках."),
    (20.65, 22.45, "Связь вернулась — ответы уйдут сами."),
    (22.6, 24.3, "Каждый шаг проверяют автотесты."),
    (24.5, 28.0, "На репетиции ответы дошли за полторы секунды — при закрытом приложении."),
    (28.3, 29.95, "Телефон — в цеху. Связи нет."),
    (30.1, 31.8, "Откроется ли инструкция?"),
    (32.0, 33.6, "Проверим при вас."),
]


# ---------- текст ----------
_fonts = {}


def _font(size, weight, cyr):
    key = (size, weight, cyr)
    if key not in _fonts:
        name = "golos-text-cyrillic-wght-normal.woff2" if cyr else "golos-text-latin-wght-normal.woff2"
        f = ImageFont.truetype(str(ASSETS / name), size)
        f.set_variation_by_axes([weight])
        _fonts[key] = f
    return _fonts[key]


def _is_cyr(ch):
    return "\u0400" <= ch <= "\u04ff" or ch == "\u2116"


def _runs(s):
    out, cur, cc = [], "", None
    for ch in s:
        c = _is_cyr(ch)
        if cc is None or c == cc:
            cur += ch
        else:
            out.append((cur, cc))
            cur = ch
        cc = c
    if cur:
        out.append((cur, cc))
    return out


def text_w(s, size, weight, tracking=0.0):
    if tracking:
        return sum(_font(size, weight, _is_cyr(ch)).getlength(ch) + tracking for ch in s) - tracking
    return sum(_font(size, weight, c).getlength(r) for r, c in _runs(s))


def draw_text(d, x, y, s, size, weight, fill, tracking=0.0):
    if tracking:
        for ch in s:
            f = _font(size, weight, _is_cyr(ch))
            d.text((x, y), ch, font=f, fill=fill, anchor="ls")
            x += f.getlength(ch) + tracking
        return x - tracking
    for r, c in _runs(s):
        f = _font(size, weight, c)
        d.text((x, y), r, font=f, fill=fill, anchor="ls")
        x += f.getlength(r)
    return x


def text_layer(lines, size, weight=700, fill=WHITE, line_h=1.14, shadow=0.55, tracking=0.0, align="l"):
    norm = [[(ln, fill)] if isinstance(ln, str) else ln for ln in lines]
    pad = int(size * 0.6)
    widths = [sum(text_w(t, size, weight, tracking) for t, _ in ln) for ln in norm]
    w = int(max(widths)) + 2 * pad
    h = int(size * line_h * len(norm)) + 2 * pad
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(norm):
        x = pad if align == "l" else (w - widths[i]) / 2
        y = pad + int(size * 0.84) + int(i * size * line_h)
        for t, col in ln:
            x = draw_text(d, x, y, t, size, weight, col, tracking)
    if shadow:
        a = img.getchannel("A").filter(ImageFilter.GaussianBlur(max(2, size * 0.10))).point(lambda v: int(v * shadow))
        sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sh.putalpha(a)
        img = Image.alpha_composite(sh, img)
    img.pad = pad
    img.text_w = max(widths)
    return img


def paste(base, layer, x, y, alpha=1.0):
    if alpha <= 0.003:
        return
    if alpha < 0.997:
        layer = layer.copy()
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
    base.paste(layer, (int(round(x)), int(round(y))), layer)


def paste_c(base, layer, cx, cy, alpha=1.0):
    paste(base, layer, cx - layer.width / 2, cy - layer.height / 2, alpha)


def paste_t(base, layer, x, y, alpha=1.0):
    paste(base, layer, x - layer.pad, y - layer.pad, alpha)


def text_h(layer):
    return layer.height - 2 * layer.pad


# ---------- движение ----------
def clamp(v, a=0.0, b=1.0):
    return max(a, min(b, v))


def ramp(t, t0, t1):
    return clamp((t - t0) / float(t1 - t0)) if t1 != t0 else float(t >= t1)


def ease_out(t):
    return 1 - (1 - t) ** 3


def ease_io(t):
    return 3 * t * t - 2 * t * t * t


def lerp(a, b, t):
    return a + (b - a) * t


# ---------- фигуры ----------
def rounded_mask(w, h, r, ss=3):
    m = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], r * ss, fill=255)
    return m.resize((w, h), Image.LANCZOS)


def shadow_layer(w, h, r, blur=36, alpha=150):
    pad = blur * 3
    m = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
    ImageDraw.Draw(m).rounded_rectangle([pad, pad, pad + w, pad + h], r, fill=alpha)
    m = m.filter(ImageFilter.GaussianBlur(blur))
    s = Image.new("RGBA", m.size, (6, 0, 14, 0))
    s.putalpha(m)
    return s, pad


def ring(d_out, width, color, alpha):
    ss = 3
    m = Image.new("L", (d_out * ss, d_out * ss), 0)
    ImageDraw.Draw(m).ellipse([width * ss / 2, width * ss / 2, d_out * ss - width * ss / 2, d_out * ss - width * ss / 2],
                              outline=int(255 * alpha), width=int(width * ss))
    m = m.resize((d_out, d_out), Image.LANCZOS)
    im = Image.new("RGBA", (d_out, d_out), color + (0,))
    im.putalpha(m)
    return im


def cursor_img(scale=2.0):
    ss = 4
    pts = [(0, 0), (0, 17.5), (4.6, 13.4), (7.8, 20.6), (10.6, 19.4), (7.4, 12.4), (13.2, 12.4)]
    k = scale * ss
    size = int(24 * scale)
    im = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    p = [(3 * ss + x * k, 3 * ss + y * k) for x, y in pts]
    d.polygon(p, fill=(255, 255, 255, 255))
    d.line(p + [p[0]], fill=(20, 12, 30, 255), width=int(1.5 * ss), joint="curve")
    im = im.resize((size, size), Image.LANCZOS)
    a = im.getchannel("A").filter(ImageFilter.GaussianBlur(3)).point(lambda v: int(v * 0.5))
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    sh.putalpha(a)
    out = Image.new("RGBA", (size + 6, size + 8), (0, 0, 0, 0))
    out.alpha_composite(sh, (4, 6))
    out.alpha_composite(im, (0, 0))
    return out


def icon(name, size, color=None):
    im = Image.open(ICONS / (name + "-white.png")).convert("RGBA").resize((size, size), Image.LANCZOS)
    if color:
        solid = Image.new("RGBA", im.size, color + (0,))
        solid.putalpha(im.getchannel("A"))
        im = solid
    return im


# ---------- цвет кадров выпуска ----------
def make_vignette():
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((x - W / 2) / (W / 2)) ** 2 + ((y - H / 2) / (H / 2)) ** 2)
    vig = 1 - 0.16 * np.clip(r - 0.55, 0, 1) ** 1.5
    bottom = 1 - 0.62 * np.clip((y - 560) / 520, 0, 1) ** 1.35
    return (vig * bottom)[..., None].astype(np.float32)


VIG = None


def grade(im, mood):
    a = np.asarray(im, dtype=np.float32) / 255.0
    a = a + 0.22 * (a - 0.5) * (1 - np.abs(2 * a - 1))
    lum = (a * np.array([0.2126, 0.7152, 0.0722], np.float32)).sum(axis=2, keepdims=True)
    a = lum + (a - lum) * (1.06 - 0.45 * mood)
    a = a * (1.0 - 0.16 * mood)
    a = a + (1 - lum) * np.array([0.020, 0.0, 0.045], np.float32) * (0.6 + 0.9 * mood)
    a = a * VIG
    return Image.fromarray(np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8))


def push(im, k, cx=0.5, cy=0.5):
    """Медленный наезд камеры: k — увеличение; (cx, cy) — куда наезжаем, доли кадра."""
    if k <= 1.0005:
        return im
    w, h = W / k, H / k
    x0, y0 = (W - w) * cx, (H - h) * cy
    return im.resize((W, H), Image.BICUBIC, box=(x0, y0, x0 + w, y0 + h))


# ---------- источники ----------
class Clips:
    def __init__(self, root):
        self.root = root
        self.cache = {}

    def frame(self, name, i):
        files = self.cache.get(name)
        if files is None:
            files = self.cache[name] = sorted((self.root / name).glob("*.jpg"))
        return Image.open(files[max(0, min(len(files) - 1, i))]).convert("RGB")


class Rec:
    """Запись интерфейса: кадры по времени, мышь, клики и касания (время — от первого кадра)."""

    def __init__(self, root):
        self.root = root
        L = json.load(io.open(root / "log.json", encoding="utf-8"))
        self.dpr = {w: L[w]["dpr"] for w in ("pc", "phone")}
        self.frames, self.t0 = {}, {}
        for w in ("pc", "phone"):
            fr = L[w]["frames"]
            self.t0[w] = fr[0]["t"]
            self.frames[w] = ([x["t"] - fr[0]["t"] for x in fr], [x["f"] for x in fr])
        ev = L["events"]
        self.mouse = [(e["t"] - self.t0["pc"], e["x"], e["y"]) for e in ev if e["who"] == "pc" and e["kind"] == "mouse"]
        self.mouse_t = [m[0] for m in self.mouse]
        self.clicks = [(e["t"] - self.t0["pc"], e["x"], e["y"]) for e in ev if e["who"] == "pc" and e["kind"] == "click"]
        self.taps = [(e["t"] - self.t0["phone"], e["x"], e["y"]) for e in ev if e["who"] == "phone" and e["kind"] == "tap"]
        self._last = {}

    def image(self, who, r):
        ts, fs = self.frames[who]
        i = max(0, bisect.bisect_right(ts, r) - 1)
        if self._last.get(who, (None,))[0] != i:
            self._last[who] = (i, Image.open(self.root / who / fs[i]).convert("RGB"))
        return self._last[who][1]

    def cursor(self, r):
        i = bisect.bisect_right(self.mouse_t, r) - 1
        if i < 0:
            return None
        return self.mouse[i][1] * self.dpr["pc"], self.mouse[i][2] * self.dpr["pc"]


# ---------- код ----------
KW = {"const", "let", "var", "async", "await", "function", "export", "import", "from", "new", "of", "in", "def",
      "class", "None", "True", "False", "and", "or", "not", "is", "as", "self", "this", "null", "undefined"}
CTRL = {"if", "else", "elif", "return", "for", "while", "continue", "break", "throw", "try", "catch"}
TOKEN = re.compile(r"(?P<com>//.*$|#.*$)|(?P<str>'[^']*'|\"[^\"]*\"|`[^`]*`)|(?P<num>\b\d+(\.\d+)?\b)|(?P<id>[A-Za-z_][A-Za-z_0-9]*)|(?P<p>.)")
C_DEF, C_KW, C_CTRL, C_STR, C_COM, C_FN, C_NUM, C_TYPE, C_VAR = (
    (212, 212, 212), (86, 156, 214), (197, 134, 192), (206, 145, 120), (106, 153, 85), (220, 220, 170),
    (181, 206, 168), (78, 201, 176), (156, 220, 254))


def code_tokens(line):
    out = []
    for m in TOKEN.finditer(line):
        k, s = m.lastgroup, m.group(0)
        if k == "com":
            out.append((s, C_COM))
        elif k == "str":
            out.append((s, C_STR))
        elif k == "num":
            out.append((s, C_NUM))
        elif k == "id":
            nxt = line[m.end():m.end() + 1]
            if s in CTRL:
                out.append((s, C_CTRL))
            elif s in KW:
                out.append((s, C_KW))
            elif nxt == "(":
                out.append((s, C_FN))
            elif s[:1].isupper():
                out.append((s, C_TYPE))
            else:
                out.append((s, C_VAR))
        else:
            out.append((s, C_DEF))
    return out


def code_panel(path, first, last, hl):
    """Тёмный редактор с настоящим фрагментом файла; hl — номера строк для подсветки."""
    lines = io.open(ROOT / path, encoding="utf-8").read().splitlines()[first - 1:last]
    img = Image.new("RGB", (W, H), (30, 30, 30))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 64], fill=(37, 37, 38))
    fname = Path(path).name
    ft = ImageFont.truetype(MONO, 24)
    tab_w = int(ft.getlength(fname)) + 70
    d.rectangle([0, 0, tab_w, 64], fill=(30, 30, 30))
    d.line([0, 1, tab_w, 1], fill=AMBER, width=3)
    d.text((32, 41), fname, font=ft, fill=(230, 230, 230), anchor="ls")
    d.text((32, 104), " › ".join(Path(path).parts), font=ImageFont.truetype(MONO, 22), fill=(150, 150, 150), anchor="ls")
    fs = 32
    f = ImageFont.truetype(MONO, fs)
    lh = int(fs * 1.5)
    y0 = 150
    hl_y = []
    for i, ln in enumerate(lines):
        no = first + i
        y = y0 + i * lh
        if no in hl:
            hl_y.append(y)
        d.text((150, y + fs), str(no), font=f, fill=(200, 200, 200) if no in hl else (110, 110, 110), anchor="rs")
        x = 200
        for s, col in code_tokens(ln.replace("\t", "    ")):
            d.text((x, y + fs), s, font=f, fill=col, anchor="ls")
            x += f.getlength(s)
    return img.convert("RGBA"), hl_y, lh


def load_e2e_report():
    """Названия, итоги и длительности сквозных тестов из последнего отчёта Playwright (e2e/results/report.json)."""
    from datetime import datetime, timedelta
    d = json.load(io.open(ROOT / "e2e" / "results" / "report.json", encoding="utf-8"))
    tests = []

    def walk(suite):
        for sp in suite.get("specs", []):
            for t in sp.get("tests", []):
                res = (t.get("results") or [{}])[-1]
                tests.append({"file": suite.get("file", ""), "line": sp.get("line"), "title": sp.get("title"),
                              "status": t.get("status"), "ms": res.get("duration") or 0})
        for c in suite.get("suites", []):
            walk(c)

    for s in d.get("suites", []):
        walk(s)
    start = datetime.fromisoformat(d["stats"]["startTime"].replace("Z", "+00:00")) + timedelta(hours=5)  # Алматы
    return {"stats": d["stats"], "tests": tests, "when": start.strftime("%d.%m.%Y, %H:%M")}


def ellipsize(s, f, maxw):
    if f.getlength(s) <= maxw:
        return s
    while s and f.getlength(s + "…") > maxw:
        s = s[:-1]
    return s + "…"


# ---------- кадр ----------
class Teaser:
    def __init__(self, work):
        self.work = Path(work)
        self.clips = Clips(self.work / "clips")
        self.rec = Rec(self.work / "rec")
        self.cursor = cursor_img(1.7)
        self.report = load_e2e_report()
        self.codes = {
            "learner": code_panel("frontend/src/local/learner.ts", 191, 205, set(range(195, 204))),
            "sw": code_panel("frontend/src/sw.ts", 34, 45, {40, 43, 44}),
            "server": code_panel("backend/app/services/syncsvc.py", 213, 227, {215, 216, 224, 225, 226}),
        }
        self.run2 = [json.loads(l) for l in io.open(POCO / "run2.jsonl", encoding="utf-8")]
        self.poco = Image.open(POCO / "23-results-certificate.png").convert("RGB")   # настоящий снимок POCO, 21:55
        t = {}
        t["hundred"] = text_layer(["100 000 000"], 190, 800)
        t["hundred_sub"] = text_layer(["изделий в год"], 64, 700)
        t["hundred_src"] = text_layer(["Dolce Pharm, 2025 · данные выпуска Qonaev Media"], 24, 500, (230, 230, 230), shadow=0.8)
        t["each"] = text_layer(["Каждое — по инструкции."], 96, 800)
        t["upd"] = text_layer(["Инструкцию обновили."], 96, 800)
        t["shift"] = text_layer(["Смена уже в цеху."], 96, 800)
        t["nosig"] = text_layer([[("В цеху ", WHITE), ("нет связи.", AMBER)]], 96, 800)
        t["head"] = text_layer(["Мы сделали LMS,", [("которой ", WHITE), ("не нужна связь.", AMBER)]], 100, 800, shadow=0.7)
        t["floor"] = text_layer(["Телефон — в цеху.", [("Связи ", WHITE), ("нет.", AMBER)]], 96, 800)
        t["q"] = text_layer(["Откроется ли инструкция?"], 104, 800)
        t["check"] = text_layer(["Проверим при вас."], 92, 800, shadow=0, align="c")
        t["credit"] = text_layer([CREDIT], 22, 500, WHITE, shadow=0.7)
        t["rec_pc"] = text_layer(["запись экрана · тестовый стенд"], 22, 500, (235, 225, 245), shadow=0.8)
        t["rec_ph"] = text_layer(["запись экрана · телефон эмулирован в Chromium"], 22, 500, (235, 225, 245), shadow=0.8)
        t["term_when"] = text_layer(["полный прогон " + self.report["when"]], 22, 500, (200, 200, 200), shadow=0)
        t["rh_hdr"] = text_layer(["РЕПЕТИЦИЯ · POCO X7 PRO · ANDROID 15 · 20.11.2026"], 24, 700, AMBER, shadow=0.8, tracking=2.0)
        t["rh_cap"] = text_layer(["Экран POCO X7 Pro после репетиции · в строке состояния — авиарежим"], 22, 500, (210, 200, 220), shadow=0)
        t["rh_src"] = text_layer(["журнал прогона: releases/v1-run2-verified/phone/run2.jsonl"], 20, 500, (150, 140, 160), shadow=0)
        self.t = t
        self.i_wifioff = icon("wifi-off", 96, AMBER)
        self.cache = {}
        self.rh_bg = self._rehearsal_bg()
        self.poco_body = self._poco_body(860)

    # --- общие детали ---
    def chip(self, label, text):
        key = ("chip", label, text)
        if key not in self.cache:
            lab = text_layer([label], 22, 700, AMBER, shadow=0, tracking=2.2)
            txt = text_layer([text], 44, 700, WHITE, shadow=0)
            w = int(max(lab.text_w, txt.text_w)) + 64
            h = 136
            box = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            box.paste(Image.new("RGBA", (w, h), (18, 8, 28, 228)), (0, 0), rounded_mask(w, h, 18))
            paste_t(box, lab, 32, 26)
            paste_t(box, txt, 32, 64)
            self.cache[key] = box
        return self.cache[key]

    def put_chip(self, im, label, text, t, t_in, t_out, top=False):
        a = min(ease_out(ramp(t, t_in, t_in + 0.18)), 1 - ramp(t, t_out - 0.08, t_out))
        if a > 0:
            c = self.chip(label, text)
            y = 72 if top else H - 72 - c.height
            paste(im, c, 72, y + (1 - a) * (-18 if top else 18), a)

    def title(self, im, key, t, t_in, t_out, x=96, yb=972):
        lay = self.t[key]
        a = min(ramp(t, t_in, t_in + 0.2), 1 - ramp(t, t_out - 0.12, t_out))
        if a > 0:
            slide = (1 - ease_out(ramp(t, t_in, t_in + 0.3))) * 26
            paste_t(im, lay, x, yb - text_h(lay) + slide, a)

    def text(self, s, size, weight, fill, shadow=0.0):
        key = ("text", s, size, weight, fill, shadow)
        if key not in self.cache:
            self.cache[key] = text_layer([s], size, weight, fill, shadow=shadow)
        return self.cache[key]

    def corner(self, im, key, left=False):
        lay = self.t[key]
        paste_t(im, lay, 72 if left else W - 60 - lay.text_w, 34, 0.9)

    # --- сцены ---
    def ep(self, name, t, s0, f0=0, mood=0.0, k0=1.0, k1=1.05):
        i = f0 + int(round((t - s0) * FPS))
        im = grade(self.clips.frame(name, i), mood)
        return push(im, lerp(k0, k1, ramp(t, s0, s0 + 2.0)))

    def blurred_bg(self, name, t, s0, f0=0):
        i = f0 + int(round((t - s0) * FPS * 0.6))
        im = self.clips.frame(name, i).resize((384, 216), Image.BILINEAR).filter(ImageFilter.GaussianBlur(5))
        im = im.resize((W, H), Image.BICUBIC)
        a = np.asarray(im, np.float32) * 0.42 + np.array([10, 2, 20], np.float32)
        return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))

    def pc(self, t, s0, s1, r0, r1, c0, c1, k0, k1):
        """Запись ноутбука: время t ролика → время r записи; c — центр кадра, k — увеличение."""
        u = ramp(t, s0, s1)
        r = lerp(r0, r1, u)
        src = self.rec.image("pc", r)
        k = lerp(k0, k1, ease_io(u))
        cx, cy = lerp(c0[0], c1[0], ease_io(u)), lerp(c0[1], c1[1], ease_io(u))
        w, h = W / k, H / k
        x0, y0 = clamp(cx - w / 2, 0, W - w), clamp(cy - h / 2, 0, H - h)
        im = src.resize((W, H), Image.LANCZOS, box=(x0, y0, x0 + w, y0 + h))
        sp = (r1 - r0) / (s1 - s0)
        dpr = self.rec.dpr["pc"]
        for (tc, x, y) in self.rec.clicks:
            tt = s0 + (tc - r0) / sp
            q = ramp(t, tt, tt + 0.32)
            if 0 < q < 1:
                paste_c(im, ring(int(26 + 90 * ease_out(q)), 6, AMBER, 1 - q), (x * dpr - x0) * k, (y * dpr - y0) * k)
        cur = self.rec.cursor(r)
        if cur:
            px, py = (cur[0] - x0) * k, (cur[1] - y0) * k
            if -40 < px < W and -40 < py < H:
                paste(im, self.cursor, px - 6, py - 6)
        return im

    def phone(self, im, t, s0, s1, r0, r1, sh, x, y_top, scroll=(0, 0)):
        """Экран телефона высотой sh пикселей; scroll — насколько экран сдвинут вверх в начале и в конце сцены."""
        u = ramp(t, s0, s1)
        r = lerp(r0, r1, u)
        src = self.rec.image("phone", r)
        sc = sh / src.height
        sw = int(round(src.width * sc))
        rad = int(sw * 0.07)
        bez = max(10, int(sw * 0.035))
        yy = y_top - lerp(scroll[0], scroll[1], ease_io(u))
        key = ("body", sw, sh)
        if key not in self.cache:
            body = Image.new("RGBA", (sw + 2 * bez, sh + 2 * bez), (0, 0, 0, 0))
            body.paste(Image.new("RGBA", body.size, (16, 10, 24, 255)), (0, 0), rounded_mask(body.width, body.height, rad + bez))
            self.cache[key] = (body, rounded_mask(sw, sh, rad), shadow_layer(sw + 2 * bez, sh + 2 * bez, rad + bez, blur=40, alpha=170))
        body0, smask, (shd, pad) = self.cache[key]
        paste(im, shd, x - bez - pad, yy - bez - pad + 24)
        body = body0.copy()
        s = src.resize((sw, sh), Image.LANCZOS).convert("RGBA")
        s.putalpha(smask)
        body.alpha_composite(s, (bez, bez))
        paste(im, body, x - bez, yy - bez)
        sp = (r1 - r0) / (s1 - s0)
        dpr = self.rec.dpr["phone"]
        for (tc, tx, ty) in self.rec.taps:
            tt = s0 + (tc - r0) / sp
            q = ramp(t, tt, tt + 0.36)
            if 0 < q < 1:
                paste_c(im, ring(int(30 + 120 * ease_out(q)), 7, AMBER, 1 - q), x + tx * dpr * sc, yy + ty * dpr * sc)
        return sc, yy

    def code(self, t, s0, s1, key, label, text):
        panel, hl, lh = self.codes[key]
        im = panel.copy()
        if hl:
            u = ease_out(ramp(t, s0 + 0.05, s0 + 0.45))
            band = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(band)
            for y in hl:
                d.rectangle([182, y + 3, 182 + int((W - 262) * u), y + lh - 3], fill=AMBER + (38,))
                d.rectangle([176, y + 3, 181, y + lh - 3], fill=AMBER + (255,))
            im.alpha_composite(band)
        im = im.convert("RGB")
        cy = (hl[0] + hl[-1] + lh) / 2 if hl else H / 2
        im = push(im, lerp(1.0, 1.07, ramp(t, s0, s1)), 0.15, clamp(cy / H))
        self.put_chip(im, label, text, t, s0 + 0.1, s1 + 0.2)
        return im

    def terminal(self, t, s0, s1):
        st, rows = self.report["stats"], self.report["tests"]
        im = Image.new("RGB", (W, H), (12, 12, 12))
        d = ImageDraw.Draw(im)
        f, fb = ImageFont.truetype(MONO, 24), ImageFont.truetype(MONO_B, 24)
        lh = 34
        y = 76
        d.text((60, y), "PS e2e> ", font=fb, fill=(110, 200, 255), anchor="ls")
        d.text((60 + fb.getlength("PS e2e> "), y), "npx playwright test", font=f, fill=(230, 230, 230), anchor="ls")
        y += int(lh * 1.5)
        d.text((60, y), "Running %d tests using 1 worker" % len(rows), font=f, fill=(200, 200, 200), anchor="ls")
        y += int(lh * 1.3)
        shown = int(round(ramp(t, s0 + 0.05, s1 - 0.55) * len(rows)))
        for i, row in enumerate(rows[:shown]):
            ok = row["status"] == "expected"
            d.text((70, y), "✓" if ok else "-", font=fb, fill=GREEN if ok else (150, 150, 150), anchor="ls")
            name = "%2d %s:%s › " % (i + 1, row["file"], row["line"])
            d.text((110, y), name, font=f, fill=(140, 140, 140), anchor="ls")
            x = 110 + f.getlength(name)
            tail = " (%.1fs)" % (row["ms"] / 1000.0) if ok else "  пропущен: в Chromium нет Background Sync"
            title = ellipsize(row["title"], f, W - 80 - x - f.getlength(tail))
            d.text((x, y), title, font=f, fill=(225, 225, 225) if ok else (150, 150, 150), anchor="ls")
            d.text((x + f.getlength(title), y), tail, font=f, fill=(140, 140, 140), anchor="ls")
            y += lh
        im = push(im, lerp(1.0, 1.05, ramp(t, s0, s1)), 0.1, 0.3)
        a = ease_out(ramp(t, s1 - 0.6, s1 - 0.4))
        if a > 0:
            skipped = sum(1 for r in rows if r["status"] == "skipped")
            passed = sum(1 for r in rows if r["status"] == "expected")
            im = Image.blend(im, Image.new("RGB", (W, H), (8, 8, 8)), 0.78 * a)
            big = self.text("%d passed" % passed, 120, 800, GREEN, 0.0)
            sub = self.text("%d skipped · %.1f мин · + 87 проверок API (pytest)" % (skipped, st["duration"] / 60000.0), 38, 600, (220, 220, 220))
            paste_t(im, big, 96, 430 + (1 - a) * 20, a)
            paste_t(im, sub, 100, 430 + text_h(big) + 34 + (1 - a) * 20, a)
        paste_t(im, self.t["term_when"], W - 60 - self.t["term_when"].text_w, 40)
        self.put_chip(im, "АВТОТЕСТЫ", "Две сессии браузера, обрыв сети, повторы", t, s0 + 0.1, s1 + 0.2)
        return im

    def _rehearsal_bg(self):
        im = Image.new("RGB", (W, H), (11, 8, 16))
        d = ImageDraw.Draw(im)
        f = ImageFont.truetype(MONO, 21)
        hot_keys = {"offline_seconds", "trigger", "app_state", "duplicate", "seconds_after_phone_online"}
        picks = [e for e in self.run2 if e["kind"] in ("phone-offline", "phone-online")
                 or (e["kind"] == "server-batch" and e.get("trigger") == "background-sync")]
        y, lh = 92, 31
        for e in picks:
            x = 60
            items = list(e.items())
            for j, (k, v) in enumerate(items):
                ks = '"%s": ' % k
                vs = json.dumps(v, ensure_ascii=False) + (", " if j < len(items) - 1 else "")
                if x + f.getlength(ks + vs) > W - 60:
                    y += lh
                    x = 90
                hot = k in hot_keys
                d.text((x, y), ks, font=f, fill=(160, 200, 255) if hot else (110, 130, 170), anchor="ls")
                x += f.getlength(ks)
                if hot:
                    d.rectangle([x - 3, y - 22, x + f.getlength(vs.rstrip(", ")) + 3, y + 7], fill=(70, 52, 20))
                d.text((x, y), vs, font=f, fill=AMBER if hot else (140, 130, 150), anchor="ls")
                x += f.getlength(vs)
            y += int(lh * 1.6)
        return Image.blend(im, Image.new("RGB", (W, H), (11, 8, 16)), 0.3)

    def _poco_body(self, ph_h):
        sc = ph_h / self.poco.height
        pw = int(self.poco.width * sc)
        rad = int(pw * 0.07)
        scr = self.poco.resize((pw, ph_h), Image.LANCZOS).convert("RGBA")
        scr.putalpha(rounded_mask(pw, ph_h, rad))
        bez = 14
        body = Image.new("RGBA", (pw + 2 * bez, ph_h + 2 * bez), (0, 0, 0, 0))
        body.paste(Image.new("RGBA", body.size, (16, 10, 24, 255)), (0, 0), rounded_mask(body.width, body.height, rad + bez))
        body.alpha_composite(scr, (bez, bez))
        return body, shadow_layer(body.width, body.height, rad + bez, blur=40, alpha=180)

    def rehearsal(self, t, s0, s1):
        im = push(self.rh_bg, lerp(1.0, 1.04, ramp(t, s0, s1)), 0.0, 0.0)
        body, (shd, pad) = self.poco_body
        a = ease_out(ramp(t, s0, s0 + 0.3))
        px, py = W - 130 - body.width, 100
        paste(im, shd, px - pad, py - pad + 24, a)
        paste(im, body, px + (1 - a) * 40, py, a)
        cap = self.t["rh_cap"]
        paste_t(im, cap, min(px, W - 60 - cap.text_w), py + body.height + 22, a)
        # значок авиарежима в строке состояния снимка (x≈935, y≈60 в пикселях экрана 1220×2712)
        sc = (body.height - 28) / float(self.poco.height)
        ax, ay = px + 14 + 935 * sc, py + 14 + 60 * sc
        for tb in (s0 + 3 * BEAT, s0 + 5 * BEAT):
            q = ramp(t, tb, tb + 0.5)
            if 0 < q < 1:
                paste_c(im, ring(int(30 + 70 * ease_out(q)), 5, AMBER, 1 - q), ax, ay)
        if t >= s0 + 3 * BEAT:
            paste_c(im, ring(34, 4, AMBER, 0.9), ax, ay)
        paste_t(im, self.t["rh_hdr"], 96, 430, ramp(t, s0 + 0.05, s0 + 0.25))
        stats = [(s0 + 1 * BEAT, "1 мин 53 с", "телефон без сети, тест сдан"),
                 (s0 + 3 * BEAT, "1,6 с", "от возврата Wi-Fi до сервера — приложение закрыто"),
                 (s0 + 5 * BEAT, "0", "дублей: две сдачи — две строки в отчёте")]
        y = 500
        for ts, big, small in stats:
            a = ease_out(ramp(t, ts, ts + 0.22))
            if a > 0:
                b = self.text(big, 112, 800, WHITE, 0.4)
                s = self.text(small, 32, 600, LILAC)
                paste_t(im, b, 96, y + (1 - a) * 20, a)
                paste_t(im, s, 96 + b.text_w + 34, y + text_h(b) - text_h(s) - 16 + (1 - a) * 20, a)
            y += 150
        paste_t(im, self.t["rh_src"], 96, H - 60)
        return im

    def glitch(self, im, t):
        a = np.asarray(im).copy()
        rng = np.random.default_rng(int(t * 1000) + 7)
        for _ in range(9):
            y0 = int(rng.integers(0, H - 60))
            hh = int(rng.integers(8, 60))
            a[y0:y0 + hh] = np.roll(a[y0:y0 + hh], int(rng.integers(-80, 80)), axis=1)
        a[..., 0] = np.roll(a[..., 0], 10, axis=1)
        a[..., 2] = np.roll(a[..., 2], -10, axis=1)
        return Image.fromarray(a)

    # --- монтаж ---
    def frame(self, n):
        t = n / float(FPS)
        # ===== цех =====
        if t < T(7):
            if t < T(1, 2):
                im = self.ep("c15", t, T(1), 0, k0=1.0, k1=1.06)
            elif t < T(2):
                im = self.ep("c00", t, T(1, 2), 6)
            elif t < T(2, 2):
                im = self.ep("c16", t, T(2), 0)
            elif t < T(3):
                im = self.ep("c01", t, T(2, 2), 4)
            elif t < T(3, 2):
                im = self.ep("c03", t, T(3), 8)
            elif t < T(4):
                im = self.ep("c08", t, T(3, 2), 26)
            elif t < T(5):
                im = self.ep("c09", t, T(4), 0)
            elif t < T(6):
                im = self.ep("c10", t, T(5), 4)
            else:
                im = self.ep("c11", t, T(6), 0, mood=ease_io(ramp(t, T(6), T(6) + 0.6)))
                if t < T(6) + 0.16:
                    im = self.glitch(im, t)
            if t < 0.5:
                im = Image.blend(Image.new("RGB", (W, H), (0, 0, 0)), im, ease_io(ramp(t, 0, 0.5)))
            a = min(ramp(t, T(1, 1), T(1, 1) + 0.2), 1 - ramp(t, T(3) - 0.15, T(3) - 0.02))
            if a > 0:
                h1, h2, h3 = self.t["hundred"], self.t["hundred_sub"], self.t["hundred_src"]
                slide = (1 - ease_out(ramp(t, T(1, 1), T(1, 1) + 0.35))) * 30
                top = 972 - text_h(h1) - 6 - text_h(h2) + slide
                a2 = min(a, ramp(t, T(1, 2), T(1, 2) + 0.2))
                paste_t(im, h3, 100, top - 46, a2)
                paste_t(im, h1, 96, top, a)
                paste_t(im, h2, 100, top + text_h(h1) + 6, a2)
            self.title(im, "each", t, T(3) + 0.05, T(4) - 0.02)
            self.title(im, "upd", t, T(4) + 0.05, T(5) - 0.02)
            self.title(im, "shift", t, T(5) + 0.05, T(6) - 0.02)
            a = min(ramp(t, T(6) + 0.1, T(6) + 0.3), 1 - ramp(t, T(7) - 0.06, T(7)))
            if a > 0:
                lay = self.t["nosig"]
                slide = (1 - ease_out(ramp(t, T(6) + 0.1, T(6) + 0.4))) * 26
                top = 972 - text_h(lay) + slide
                paste(im, self.i_wifioff, 96, top + (text_h(lay) - 96) / 2 + 4, a)
                paste_t(im, lay, 96 + 96 + 28, top, a)
            paste_t(im, self.t["credit"], 96, 1018, 0.85 * ramp(t, 0.2, 0.6))
            return im

        # ===== разработка =====
        if t < T(8):
            im = self.pc(t, T(7), T(8), 10.6, 14.7, (900, 760), (900, 800), 1.42, 1.55)
            ha = 1 - ramp(t, T(7, 2) - 0.1, T(7, 2) + 0.05)
            if ha > 0:
                im = Image.blend(im, Image.new("RGB", (W, H), (14, 4, 24)), 0.62 * ha)
                lay = self.t["head"]
                paste_t(im, lay, 96, (H - text_h(lay)) / 2 - 20 + (1 - ease_out(ramp(t, T(7), T(7) + 0.3))) * 20, ha)
            fl = 1 - ramp(t, T(7), T(7) + 0.12)
            if fl > 0:
                im = Image.blend(im, Image.new("RGB", (W, H), (255, 255, 255)), 0.55 * fl)
            self.put_chip(im, "НОУТБУК МЕТОДИСТА", "Курс собирается прямо в браузере", t, T(7, 2), T(8) + 0.2, top=True)
            self.corner(im, "rec_pc")
            return im
        if t < T(9):
            if t < T(8, 2):
                im = self.pc(t, T(8), T(8, 2), 22.55, 23.9, (960, 540), (960, 560), 1.04, 1.12)
            else:
                im = self.pc(t, T(8, 2), T(9), 30.25, 33.45, (956, 560), (965, 540), 1.22, 1.42)
            self.corner(im, "rec_pc")
            return im
        if t < T(10):
            return self.code(t, T(9), T(10), "learner", "КОД · ТЕЛЕФОН", "Ответ и очередь отправки — одна транзакция")
        if t < T(12):
            im = self.blurred_bg("c11", t, T(10), 10)
            if t < T(11):
                self.phone(im, t, T(10), T(11), 1.45, 6.45, 1760, 1060, 40, scroll=(260, 520))
                self.put_chip(im, "ТЕЛЕФОН ОПЕРАТОРА", "Скачивает курс до смены — 2,6 МБ", t, T(10), T(11) + 0.2)
            elif t < T(11, 2):
                self.phone(im, t, T(11), T(11, 2), 6.6, 8.6, 1760, 1060, 40, scroll=(560, 860))
                self.put_chip(im, "ТЕЛЕФОН ОПЕРАТОРА", "Курс скачан и проверен", t, T(11) - 0.2, T(12) + 0.2)
            else:
                self.phone(im, t, T(11, 2), T(12), 9.5, 13.6, 1760, 1060, 40, scroll=(120, 200))
                self.put_chip(im, "ТЕЛЕФОН ОПЕРАТОРА", "Все 8 материалов — на телефоне", t, T(11, 2), T(12) + 0.2)
            self.corner(im, "rec_ph", left=True)
            return im
        if t < T(13):
            if t < T(12, 2):
                return self.code(t, T(12), T(12, 2), "sw", "КОД · SERVICE WORKER", "Сеть вернулась — ответы уйдут сами")
            return self.code(t, T(12, 2), T(13), "server", "КОД · СЕРВЕР", "Повторная отправка не создаёт дубль")
        if t < T(14):
            return self.terminal(t, T(13), T(14))
        if t < T(16):
            return self.rehearsal(t, T(14), T(16))

        # ===== снова цех =====
        if t < T(17):
            im = self.ep("c12", t, T(16), 0, mood=0.25)
            self.phone(im, t, T(16), T(17), 15.4, 17.6, 900, 200, 72)
            u = ramp(t, T(16, 1.6), T(16, 3.2))
            if 0 < u < 1:
                sc = 900 / 2402.0
                paste_c(im, ring(int(70 + 120 * ease_out(u)), 7, AMBER, 1 - u), 200 + 965 * sc, 72 + 92 * sc)
            self.title(im, "floor", t, T(16) + 0.05, T(17) - 0.02, x=720)
            paste_t(im, self.t["credit"], 96, 1018, 0.85)
            return im
        if t < T(18):
            im = self.ep("c14", t, T(17), 0, mood=0.35, k0=1.0, k1=1.04)
            self.title(im, "q", t, T(17) + 0.05, T(18) + 0.5)
            paste_t(im, self.t["credit"], 96, 1018, 0.85)
            return im
        im = Image.new("RGB", (W, H), (0, 0, 0))
        a = min(ramp(t, T(18), T(18) + 0.12), 1 - ramp(t, T(18) + 1.35, T(18) + 1.75))
        paste_c(im, self.t["check"], W / 2, H / 2, a)
        return im

    def prompter(self, im, n):
        t = n / float(FPS)
        d = ImageDraw.Draw(im, "RGBA")
        d.rectangle([0, 0, W, 132], fill=(0, 0, 0, 205))
        cur = nxt = None
        for a, b, txt in VOICE:
            if a <= t < b:
                cur = (a, b, txt)
            elif t < a and nxt is None:
                nxt = (a, b, txt)
        draw_text(d, 40, 50, "%d:%04.1f" % (int(t // 60), t % 60), 34, 700, (255, 255, 255, 255))
        if cur:
            a, b, txt = cur
            draw_text(d, 230, 62, "ГОВОРИТЕ:  " + txt, 40, 800, AMBER + (255,))
            d.rectangle([230, 96, 230 + int(1500 * (t - a) / (b - a)), 110], fill=AMBER + (255,))
            d.rectangle([230, 96, 1730, 110], outline=(255, 255, 255, 120))
        elif nxt:
            a, b, txt = nxt
            draw_text(d, 230, 62, "через %.1f с:  %s" % (a - t, txt), 36, 500, (255, 255, 255, 190))
        return im


# ---------- звук ----------
def build_audio(mp3, out_wav):
    SR = 48000
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp3), "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32).reshape(-1, 2).astype(np.float64)

    def seg(a, b):
        return x[int(round(a * SR)):int(round(b * SR))].copy()

    def fade(s, fi=0.0, fo=0.0, curve=1.0):
        g = np.ones(len(s))
        if fi:
            k = int(fi * SR)
            g[:k] = np.linspace(0, 1, k)
        if fo:
            k = int(fo * SR)
            g[len(s) - k:] *= np.linspace(1, 0, k) ** curve
        return s * g[:, None]

    N = int(TOTAL / FPS * SR) + 1
    out = np.zeros((N, 2))

    def put(t, s, xf=0.012):
        i = int(round(t * SR))
        k = int(xf * SR)
        s = s.copy()
        if k and i > 0:
            s[:k] *= np.linspace(0, 1, k)[:, None]
            out[i:i + k] *= np.linspace(1, 0, k)[:, None]
        n = min(len(s), N - i)
        out[i:i + n] += s[:n]

    # такты 10–25 трека: вступление, обрыв (15-й), дроп (16-й) и основная часть
    put(0.0, fade(seg(track_bar(10), track_bar(10) + 16 * BAR + 0.012), fi=0.03), xf=0)
    # 31-й такт трека — второй обрыв: под вопрос
    put(T(17), seg(track_bar(31), track_bar(31) + BAR + 0.012))
    # первая доля 32-го такта — удар, затем затухание
    put(T(18), fade(seg(track_bar(32), track_bar(32) + 2.0), fo=1.7, curve=2.0))
    out[int(MUSIC_END * SR):] = 0
    out = out / np.abs(out).max() * 10 ** (-1.0 / 20)
    pcm = (np.clip(out, -1, 1) * 32767).astype("<i2")
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main():
    args = sys.argv[1:]
    work, out = Path(args[0]), Path(args[1])
    with_prompter = "--prompter" in args
    global VIG
    VIG = make_vignette()
    tz = Teaser(work)
    if "--still" in args:
        i = args.index("--still")
        n = int(args[i + 1])
        im = tz.frame(n)
        if with_prompter:
            im = tz.prompter(im, n)
        im.save(args[i + 2])
        return
    wav = work / "score.wav"
    build_audio(work / "music.mp3", wav)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H), "-r", str(FPS), "-i", "-",
           "-i", str(wav),
           "-vf", "scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p",
           "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-profile:v", "high",
           "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
           "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", "-t", "%.3f" % (TOTAL / float(FPS)), str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for n in range(TOTAL):
        im = tz.frame(n)
        if with_prompter:
            im = tz.prompter(im, n)
        p.stdin.write(im.tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise SystemExit("ffmpeg завершился с ошибкой")


if __name__ == "__main__":
    main()
