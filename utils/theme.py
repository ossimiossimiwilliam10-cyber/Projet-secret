"""Système de thème graphique « Exocerveau ».

Injecte le CSS global et fournit des composants stylisés réutilisables :
- Cartes glassmorphism
- Métriques enrichies
- Badges de statut
- Barres de progression custom
- Icônes cohérentes
"""

from __future__ import annotations

import streamlit as st

# ===========================================================================
# Palette Exocerveau
# ===========================================================================
PALETTE = {
    "primary":      "#6C5CE7",  # Violet profond
    "primary_soft": "#A29BFE",  # Violet clair
    "accent":       "#00CEC9",  # Cyan/menthe
    "success":      "#00B894",  # Vert
    "warning":      "#FDCB6E",  # Jaune doré
    "danger":       "#E17055",  # Corail
    "info":         "#74B9FF",  # Bleu clair

    "bg_light":     "#F8F9FE",  # Fond clair
    "card_light":    "#FFFFFF",  # Carte claire
    "text_light":    "#2D3436",  # Texte clair
    "subtext_light": "#6B7280",  # Sous-texte clair

    "bg_dark":      "#0F0F23",  # Fond sombre
    "card_dark":    "rgba(255,255,255,0.04)",  # Carte sombre
    "text_dark":    "#E2E8F0",  # Texte sombre
    "subtext_dark": "#94A3B8",  # Sous-texte sombre
}

# ===========================================================================
# Icônes modernisées (emojis cohérents, pas de mélange styles)
# ===========================================================================
ICONS = {
    # Navigation
    "dashboard":       "⊞",
    "bibliotheque":    "▤",
    "profil":          "◎",
    "aide":            "?",
    "generation":      "✦",
    "suivi":           "✓",
    "revisions":       "↻",
    "objectifs":       "◆",

    # Hiérarchie
    "semestre":        "◈",
    "ue":              "◆",
    "matiere":         "▸",
    "chapitre":        "·",

    # Actions
    "search":          "⌕",
    "add":             "+",
    "edit":            "✎",
    "delete":          "✕",
    "save":            "↧",
    "expand":          "▾",
    "collapse":        "▴",
    "study":           "▶",
    "urgent":          "!",

    # Statuts
    "fait":            "✓",
    "a_faire":         "○",
    "partiel":         "◐",
    "non_fait":        "✕",
    "alerte":          "△",
    "ok":              "✓",
    "locked":          "🔒",

    # Catégories
    "sport":           "⌘",
    "courses":         "⌂",
    "projet":          "◆",
    "dev_perso":       "♣",
    "social":          "♡",
    "intendance":      "⚙",
    "transport":       "↗",
    "travail":         "⌛",
    "repas":           "♨",
    "sommeil":         "☾",
    "etude":           "☉",
}

# ===========================================================================
# CSS Global
# ===========================================================================
GLOBAL_CSS = """
/* ===== GOOGLE FONT (Inter) ===== */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

/* ===== RESET ===== */
* { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important; }

/* ===== SCROLLBAR ===== */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #6C5CE733; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #6C5CE755; }

/* ===== CARDS / CONTAINERS ===== */
div[data-testid="stExpander"] {
    border: 1px solid rgba(108, 92, 231, 0.12) !important;
    border-radius: 12px !important;
    background: rgba(255,255,255,0.03) !important;
    backdrop-filter: blur(10px) !important;
    -webkit-backdrop-filter: blur(10px) !important;
    box-shadow: 0 2px 12px rgba(0,0,0,0.04) !important;
    margin-bottom: 8px !important;
    transition: all 0.2s ease !important;
}
div[data-testid="stExpander"]:hover {
    border-color: rgba(108, 92, 231, 0.25) !important;
    box-shadow: 0 4px 20px rgba(108, 92, 231, 0.08) !important;
}

/* ===== METRICS ===== */
div[data-testid="stMetric"] {
    background: rgba(255,255,255,0.03) !important;
    border: 1px solid rgba(108, 92, 231, 0.08) !important;
    border-radius: 12px !important;
    padding: 14px 16px !important;
    transition: all 0.2s ease !important;
}
div[data-testid="stMetric"]:hover {
    border-color: rgba(108, 92, 231, 0.2) !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 16px rgba(108, 92, 231, 0.06) !important;
}
div[data-testid="stMetric"] label {
    font-size: 0.75rem !important;
    font-weight: 500 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.06em !important;
    color: #6B7280 !important;
}
div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
    font-size: 1.5rem !important;
    font-weight: 700 !important;
    color: #2D3436 !important;
}

/* ===== BUTTONS ===== */
button[kind="primary"] {
    background: linear-gradient(135deg, #6C5CE7 0%, #A29BFE 100%) !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    letter-spacing: 0.02em !important;
    transition: all 0.2s ease !important;
    box-shadow: 0 2px 8px rgba(108, 92, 231, 0.25) !important;
}
button[kind="primary"]:hover {
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 16px rgba(108, 92, 231, 0.35) !important;
}
button[kind="secondary"] {
    border-radius: 10px !important;
    font-weight: 500 !important;
    transition: all 0.2s ease !important;
}
button[kind="secondary"]:hover {
    border-color: #6C5CE7 !important;
    color: #6C5CE7 !important;
}

/* ===== PROGRESS BARS ===== */
div[data-testid="stProgress"] > div {
    background: rgba(108, 92, 231, 0.1) !important;
    border-radius: 6px !important;
    height: 8px !important;
}
div[data-testid="stProgress"] > div > div {
    background: linear-gradient(90deg, #6C5CE7 0%, #00CEC9 100%) !important;
    border-radius: 6px !important;
}

/* ===== SIDEBAR ===== */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0F0F23 0%, #1A1A3E 100%) !important;
}
section[data-testid="stSidebar"] * {
    color: #E2E8F0 !important;
}
section[data-testid="stSidebar"] button {
    background: rgba(255,255,255,0.06) !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    border-radius: 10px !important;
    color: #E2E8F0 !important;
}
section[data-testid="stSidebar"] button:hover {
    background: rgba(108, 92, 231, 0.15) !important;
    border-color: rgba(108, 92, 231, 0.3) !important;
}

/* ===== TABS ===== */
button[data-baseweb="tab"] {
    border-radius: 10px 10px 0 0 !important;
    font-weight: 500 !important;
    letter-spacing: 0.02em !important;
    padding: 10px 20px !important;
    transition: all 0.2s ease !important;
}
button[data-baseweb="tab"][aria-selected="true"] {
    border-bottom: 3px solid #6C5CE7 !important;
    color: #6C5CE7 !important;
}

/* ===== INPUTS ===== */
input, textarea, div[data-baseweb="select"] {
    border-radius: 10px !important;
    border-color: rgba(108, 92, 231, 0.15) !important;
    transition: all 0.2s ease !important;
}
input:focus, textarea:focus {
    border-color: #6C5CE7 !important;
    box-shadow: 0 0 0 3px rgba(108, 92, 231, 0.1) !important;
}

/* ===== TOASTS ===== */
div[data-testid="stToast"] {
    background: rgba(15, 15, 35, 0.95) !important;
    backdrop-filter: blur(12px) !important;
    border-radius: 12px !important;
    border: 1px solid rgba(108, 92, 231, 0.2) !important;
    box-shadow: 0 8px 32px rgba(0,0,0,0.2) !important;
}

/* ===== TITLES ===== */
h1 { font-weight: 800 !important; letter-spacing: -0.02em !important; }
h2 { font-weight: 700 !important; letter-spacing: -0.01em !important; }
h3 { font-weight: 600 !important; }

/* ===== RADIO / CHECKBOX ===== */
div[data-testid="stRadio"] label, div[data-testid="stCheckbox"] label {
    font-weight: 500 !important;
}

/* ===== MARKDOWN CODE ===== */
code {
    background: rgba(108, 92, 231, 0.08) !important;
    color: #6C5CE7 !important;
    border-radius: 4px !important;
    padding: 2px 6px !important;
    font-size: 0.9em !important;
}

/* ===== DIVIDERS ===== */
hr {
    border-color: rgba(108, 92, 231, 0.08) !important;
    margin: 1.5rem 0 !important;
}
"""


# ===========================================================================
# Injection
# ===========================================================================
def inject_theme() -> None:
    """Injection unique du CSS global — à appeler dans app.py avant tout rendu."""
    st.markdown(f"<style>{GLOBAL_CSS}</style>", unsafe_allow_html=True)
    # Configuration du favicon + titre via set_page_config est déjà dans app.py


# ===========================================================================
# Composants stylisés
# ===========================================================================
def glass_card(content: str, padding: str = "16px", margin: str = "8px 0") -> None:
    """Carte effet verre (glassmorphism)."""
    st.markdown(
        f"<div style='"
        f"background: rgba(255,255,255,0.03); "
        f"border: 1px solid rgba(108,92,231,0.12); "
        f"border-radius: 14px; padding:{padding}; margin:{margin}; "
        f"backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); "
        f"box-shadow: 0 2px 12px rgba(0,0,0,0.03); "
        f"transition: all 0.2s ease;' "
        f"onmouseover=\"this.style.borderColor='rgba(108,92,231,0.25)';this.style.boxShadow='0 4px 20px rgba(108,92,231,0.06)'\" "
        f"onmouseout=\"this.style.borderColor='rgba(108,92,231,0.12)';this.style.boxShadow='0 2px 12px rgba(0,0,0,0.03)'\">"
        f"{content}"
        f"</div>",
        unsafe_allow_html=True,
    )


def badge(text: str, color: str = "primary", size: str = "sm") -> str:
    """Badge coloré inline. Retourne du HTML."""
    colors = {
        "primary":  ("#6C5CE7", "#FFFFFF"),
        "success":  ("#00B894", "#FFFFFF"),
        "warning":  ("#FDCB6E", "#2D3436"),
        "danger":   ("#E17055", "#FFFFFF"),
        "info":     ("#74B9FF", "#2D3436"),
        "neutral":  ("#E2E8F0", "#2D3436"),
    }
    bg, fg = colors.get(color, colors["primary"])
    sizes = {"sm": "0.7rem", "md": "0.8rem", "lg": "0.9rem"}
    return (
        f"<span style='display:inline-block; background:{bg}; color:{fg}; "
        f"padding:2px 10px; border-radius:20px; font-size:{sizes.get(size, '0.75rem')}; "
        f"font-weight:600; letter-spacing:0.02em;'>{text}</span>"
    )


def section_header(icon: str, title: str, subtitle: str = "") -> None:
    """En-tête de section stylisé avec ligne décorative."""
    st.markdown(
        f"<div style='display:flex; align-items:center; gap:10px; margin:1.5rem 0 0.5rem 0;'>"
        f"<div style='width:4px; height:24px; background:linear-gradient(180deg, #6C5CE7, #00CEC9); "
        f"border-radius:2px;'></div>"
        f"<div style='font-size:1.1rem; font-weight:700; color:#2D3436;'>{icon} {title}</div>"
        f"</div>"
        + (f"<div style='color:#6B7280; font-size:0.85rem; margin-bottom:0.8rem;'>{subtitle}</div>" if subtitle else ""),
        unsafe_allow_html=True,
    )


def stat_card(label: str, value: str, delta: str = "", icon: str = "", color: str = "primary") -> None:
    """Carte statistique compacte."""
    colors = {
        "primary":  "#6C5CE7",
        "success":  "#00B894",
        "warning":  "#FDCB6E",
        "danger":   "#E17055",
        "accent":   "#00CEC9",
    }
    accent = colors.get(color, colors["primary"])
    delta_html = f"<div style='font-size:0.75rem; color:#6B7280;'>{delta}</div>" if delta else ""
    st.markdown(
        f"<div style='background:rgba(255,255,255,0.03); border:1px solid rgba(108,92,231,0.08); "
        f"border-radius:12px; padding:14px 16px; text-align:center;'>"
        f"<div style='font-size:0.7rem; font-weight:600; text-transform:uppercase; "
        f"letter-spacing:0.06em; color:#6B7280; margin-bottom:4px;'>{icon} {label}</div>"
        f"<div style='font-size:1.6rem; font-weight:800; color:{accent};'>{value}</div>"
        f"{delta_html}"
        f"</div>",
        unsafe_allow_html=True,
    )


def progress_bar_pct(pct: float, text: str = "", height: int = 8) -> None:
    """Barre de progression avec pourcentage intégré."""
    pct_int = max(0, min(100, int(pct)))
    gradient = f"linear-gradient(90deg, #6C5CE7 0%, #00CEC9 100%)"
    st.markdown(
        f"<div style='margin:4px 0;'>"
        f"<div style='display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:2px;'>"
        f"<span style='color:#2D3436; font-weight:500;'>{text}</span>"
        f"<span style='color:#6C5CE7; font-weight:700;'>{pct_int}%</span></div>"
        f"<div style='background:rgba(108,92,231,0.1); border-radius:{height}px; height:{height}px; overflow:hidden;'>"
        f"<div style='background:{gradient}; width:{pct_int}%; height:100%; border-radius:{height}px; "
        f"transition:width 0.5s ease;'></div></div></div>",
        unsafe_allow_html=True,
    )


def chip_list(items: list[tuple[str, str]]) -> str:
    """Liste de chips colorées. Chaque item = (label, color_name). Retourne du HTML."""
    chips = []
    for label, color in items:
        chips.append(badge(label, color))
    return " ".join(chips)
