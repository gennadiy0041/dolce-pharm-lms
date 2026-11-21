"""Локальные изображения диалогового тренажёра (SVG): фон цеха и собеседник с четырьмя эмоциями.
Оригинальная графика проекта. Запуск: python frontend/scripts/make_trainer_art.py"""
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "public" / "trainer"

BG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 500" preserveAspectRatio="xMidYMid slice">
  <defs>
    <linearGradient id="wall" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#E4EBE8"/><stop offset="1" stop-color="#CFDAD6"/></linearGradient>
    <linearGradient id="floor" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#AEBBB7"/><stop offset="1" stop-color="#8C9A96"/></linearGradient>
    <linearGradient id="steel" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#C9D2D0"/><stop offset=".5" stop-color="#EEF2F1"/><stop offset="1" stop-color="#B4BFBC"/></linearGradient>
  </defs>
  <rect width="800" height="500" fill="url(#wall)"/>
  <!-- потолочные трубы и светильники -->
  <rect x="0" y="26" width="800" height="12" fill="#B7C3BF"/>
  <rect x="0" y="46" width="800" height="6" fill="#C6D0CD"/>
  <g fill="#FFFFFF" opacity=".9"><rect x="90" y="60" width="150" height="12" rx="6"/><rect x="330" y="60" width="150" height="12" rx="6"/><rect x="570" y="60" width="150" height="12" rx="6"/></g>
  <g fill="#FFFFFF" opacity=".18"><path d="M90 72h150l40 250H50z"/><path d="M330 72h150l40 250H290z"/><path d="M570 72h150l40 250H530z"/></g>
  <!-- окно в чистую зону -->
  <rect x="40" y="120" width="190" height="150" rx="6" fill="#DCE9F0" stroke="#9FB0AC" stroke-width="6"/>
  <path d="M135 120v150M40 195h190" stroke="#9FB0AC" stroke-width="4"/>
  <path d="M60 140l50 0-60 60z" fill="#FFFFFF" opacity=".5"/>
  <!-- табличка участка -->
  <rect x="290" y="112" width="220" height="44" rx="6" fill="#0E5F58"/>
  <text x="400" y="141" font-family="Arial, sans-serif" font-size="19" font-weight="700" fill="#FFFFFF" text-anchor="middle">УЧАСТОК ТАБЛЕТИРОВАНИЯ</text>
  <!-- стеллаж с папками СОП -->
  <rect x="566" y="150" width="190" height="180" rx="4" fill="#F4F1EA" stroke="#A9B4B0" stroke-width="4"/>
  <path d="M566 208h190M566 268h190" stroke="#A9B4B0" stroke-width="4"/>
  <g>
    <rect x="580" y="160" width="22" height="46" fill="#0E5F58"/><rect x="606" y="160" width="22" height="46" fill="#2E7D75"/>
    <rect x="632" y="166" width="22" height="40" fill="#E8A33D"/><rect x="658" y="160" width="22" height="46" fill="#0E5F58"/>
    <rect x="684" y="164" width="22" height="42" fill="#6C8A86"/><rect x="712" y="160" width="26" height="46" fill="#2E7D75"/>
    <rect x="580" y="218" width="26" height="48" fill="#6C8A86"/><rect x="610" y="222" width="22" height="44" fill="#0E5F58"/>
    <rect x="636" y="218" width="22" height="48" fill="#E8A33D"/><rect x="662" y="218" width="22" height="48" fill="#2E7D75"/>
    <rect x="690" y="224" width="46" height="42" fill="#FFFFFF" stroke="#A9B4B0" stroke-width="2"/>
    <rect x="580" y="280" width="60" height="46" fill="#FFFFFF" stroke="#A9B4B0" stroke-width="2"/>
    <rect x="648" y="278" width="22" height="48" fill="#0E5F58"/><rect x="674" y="278" width="22" height="48" fill="#6C8A86"/><rect x="700" y="282" width="22" height="44" fill="#E8A33D"/>
  </g>
  <!-- пол -->
  <rect y="330" width="800" height="170" fill="url(#floor)"/>
  <path d="M0 330h800" stroke="#7F8E8A" stroke-width="3"/>
  <g fill="#E8A33D"><path d="M0 470h800v14H0z" opacity=".9"/></g>
  <g fill="#1B2A2A" opacity=".85"><path d="M20 470l14 14H20zM60 470l14 14H46l14-14zM120 470l14 14h-28zM180 470l14 14h-28zM240 470l14 14h-28zM300 470l14 14h-28zM360 470l14 14h-28zM420 470l14 14h-28zM480 470l14 14h-28zM540 470l14 14h-28zM600 470l14 14h-28zM660 470l14 14h-28zM720 470l14 14h-28zM780 470l14 14h-28z"/></g>
  <!-- таблет-пресс -->
  <g>
    <rect x="246" y="250" width="150" height="170" rx="8" fill="url(#steel)" stroke="#8C9A96" stroke-width="4"/>
    <path d="M286 190h70l-14 60h-42z" fill="url(#steel)" stroke="#8C9A96" stroke-width="4"/>
    <rect x="262" y="268" width="118" height="70" rx="6" fill="#DCE9F0" stroke="#8C9A96" stroke-width="3"/>
    <circle cx="321" cy="303" r="24" fill="#F4F7F6" stroke="#8C9A96" stroke-width="3"/>
    <circle cx="321" cy="303" r="6" fill="#8C9A96"/>
    <rect x="262" y="350" width="60" height="38" rx="4" fill="#1B2A2A"/>
    <rect x="268" y="356" width="48" height="16" rx="2" fill="#3FC1A8"/>
    <circle cx="344" cy="362" r="8" fill="#2E9E4F"/><circle cx="366" cy="362" r="8" fill="#C0392B"/>
    <rect x="236" y="420" width="170" height="14" rx="4" fill="#7F8E8A"/>
  </g>
  <rect width="800" height="500" fill="#F4F1EA" opacity=".12"/>
</svg>
"""

HEAD = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 480">
  <!-- плечи и халат -->
  <path d="M40 480c0-96 58-150 160-150s160 54 160 150z" fill="#F7F5EF" stroke="#C9C3B4" stroke-width="4"/>
  <path d="M160 334l40 62 40-62-22-16h-36z" fill="#0E5F58"/>
  <path d="M158 332l42 70-34 46-36-98z" fill="#FFFFFF" stroke="#C9C3B4" stroke-width="3"/>
  <path d="M242 332l-42 70 34 46 36-98z" fill="#FFFFFF" stroke="#C9C3B4" stroke-width="3"/>
  <rect x="244" y="408" width="66" height="42" rx="5" fill="#FFFFFF" stroke="#0E5F58" stroke-width="3"/>
  <rect x="252" y="417" width="50" height="6" rx="3" fill="#0E5F58"/><rect x="252" y="429" width="36" height="5" rx="2.5" fill="#A9B4B0"/><rect x="252" y="438" width="42" height="5" rx="2.5" fill="#A9B4B0"/>
  <!-- шея и голова -->
  <path d="M172 276h56v56c0 18-56 18-56 0z" fill="#DDA77B"/>
  <ellipse cx="124" cy="206" rx="15" ry="22" fill="#E5B187"/><ellipse cx="276" cy="206" rx="15" ry="22" fill="#E5B187"/>
  <path d="M124 190c0-66 34-98 76-98s76 32 76 98c0 62-32 104-76 104s-76-42-76-104z" fill="#EDBE96"/>
  <!-- волосы и шапочка -->
  <path d="M124 176c2-22 8-36 16-46 6 22 24 34 60 34s54-12 60-34c8 10 14 24 16 46 6-70-22-112-76-112s-82 42-76 112z" fill="#2C2A29"/>
  <path d="M118 150c0-58 36-92 82-92s82 34 82 92c-24-18-52-26-82-26s-58 8-82 26z" fill="#FFFFFF" stroke="#C9C3B4" stroke-width="4"/>
  <path d="M120 146c24-17 51-25 80-25s56 8 80 25l-1-16c-24-15-50-22-79-22s-55 7-79 22z" fill="#0E5F58"/>
"""

FACES = {
    "neutral": """
  <path d="M150 192q20-8 38 0M212 192q18-8 38 0" fill="none" stroke="#2C2A29" stroke-width="6" stroke-linecap="round"/>
  <ellipse cx="170" cy="214" rx="8" ry="10" fill="#2C2A29"/><ellipse cx="230" cy="214" rx="8" ry="10" fill="#2C2A29"/>
  <circle cx="173" cy="210" r="2.6" fill="#FFFFFF"/><circle cx="233" cy="210" r="2.6" fill="#FFFFFF"/>
  <path d="M198 222q-8 22 6 24" fill="none" stroke="#C98F62" stroke-width="5" stroke-linecap="round"/>
  <path d="M176 266q24 8 48 0" fill="none" stroke="#8E4B33" stroke-width="6" stroke-linecap="round"/>
""",
    "worried": """
  <path d="M150 196q18-16 38-10M250 196q-18-16-38-10" fill="none" stroke="#2C2A29" stroke-width="6" stroke-linecap="round"/>
  <ellipse cx="170" cy="216" rx="8" ry="11" fill="#2C2A29"/><ellipse cx="230" cy="216" rx="8" ry="11" fill="#2C2A29"/>
  <circle cx="173" cy="212" r="2.6" fill="#FFFFFF"/><circle cx="233" cy="212" r="2.6" fill="#FFFFFF"/>
  <path d="M198 224q-8 22 6 24" fill="none" stroke="#C98F62" stroke-width="5" stroke-linecap="round"/>
  <path d="M178 272q22-12 44 0" fill="none" stroke="#8E4B33" stroke-width="6" stroke-linecap="round"/>
  <path d="M262 150c8 12 12 20 12 26a12 12 0 0 1-24 0c0-6 4-14 12-26z" fill="#8FC9E8" stroke="#5FA6CB" stroke-width="2"/>
""",
    "angry": """
  <path d="M148 186l40 14M252 186l-40 14" fill="none" stroke="#2C2A29" stroke-width="7" stroke-linecap="round"/>
  <ellipse cx="170" cy="216" rx="8" ry="8" fill="#2C2A29"/><ellipse cx="230" cy="216" rx="8" ry="8" fill="#2C2A29"/>
  <circle cx="173" cy="213" r="2.4" fill="#FFFFFF"/><circle cx="233" cy="213" r="2.4" fill="#FFFFFF"/>
  <ellipse cx="150" cy="242" rx="14" ry="8" fill="#E08A7A" opacity=".55"/><ellipse cx="250" cy="242" rx="14" ry="8" fill="#E08A7A" opacity=".55"/>
  <path d="M198 224q-8 22 6 24" fill="none" stroke="#C98F62" stroke-width="5" stroke-linecap="round"/>
  <path d="M174 276q26-18 52 0" fill="none" stroke="#8E4B33" stroke-width="7" stroke-linecap="round"/>
""",
    "pleased": """
  <path d="M150 194q20-12 38-2M212 192q18-10 38 2" fill="none" stroke="#2C2A29" stroke-width="6" stroke-linecap="round"/>
  <path d="M158 218q12-12 24 0M218 218q12-12 24 0" fill="none" stroke="#2C2A29" stroke-width="6" stroke-linecap="round"/>
  <ellipse cx="150" cy="244" rx="13" ry="7" fill="#E8A08A" opacity=".45"/><ellipse cx="250" cy="244" rx="13" ry="7" fill="#E8A08A" opacity=".45"/>
  <path d="M198 222q-8 22 6 24" fill="none" stroke="#C98F62" stroke-width="5" stroke-linecap="round"/>
  <path d="M170 258q30 30 60 0z" fill="#FFFFFF" stroke="#8E4B33" stroke-width="5" stroke-linejoin="round"/>
""",
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "bg-shop.svg").write_text(BG, encoding="utf-8")
    for name, face in FACES.items():
        (OUT / ("master-%s.svg" % name)).write_text(HEAD + face + "</svg>\n", encoding="utf-8")
    print("Графика тренажёра записана в", OUT)


if __name__ == "__main__":
    main()
