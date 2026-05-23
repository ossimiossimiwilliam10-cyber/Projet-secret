"""Système de thème graphique « Exocerveau » — version light & safe.

Injecte uniquement le CSS qui ne casse pas le layout Streamlit :
- Palette de couleurs (config.toml)
- Police Inter
- Sidebar dark
- Scrollbar custom
- Progress bars
- Accents subtils
"""

from __future__ import annotations

import streamlit as st

# ===========================================================================
# Palette Exocerveau (référence)
# ===========================================================================
PALETTE = {
    "primary": "#6C5CE7",
    "accent":  "#00CEC9",
    "success": "#00B894",
    "warning": "#FDCB6E",
    "danger":  "#E17055",
}

# ===========================================================================
# CSS Global — safe only
# ===========================================================================
GLOBAL_CSS = """
/* ===== FONT ===== */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

/* ===== SCROLLBAR ===== */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #6C5CE744; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #6C5CE766; }

/* ===== SIDEBAR ===== */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0F0F23 0%, #1A1A3E 100%);
}
[data-testid="stSidebar"] * { color: #E2E8F0 !important; }
[data-testid="stSidebar"] button {
    background: rgba(255,255,255,0.05) !important;
    border: 1px solid rgba(255,255,255,0.06) !important;
    border-radius: 8px !important;
}
[data-testid="stSidebar"] button:hover {
    background: rgba(108,92,231,0.12) !important;
    border-color: rgba(108,92,231,0.25) !important;
}

/* ===== PROGRESS BARS ===== */
[data-testid="stProgress"] > div {
    background: rgba(108,92,231,0.08) !important;
    border-radius: 6px !important;
}
[data-testid="stProgress"] > div > div {
    background: linear-gradient(90deg, #6C5CE7, #00CEC9) !important;
    border-radius: 6px !important;
}

/* ===== TITLES ===== */
h1 { font-weight: 800 !important; letter-spacing: -0.02em !important; }
h2 { font-weight: 700 !important; letter-spacing: -0.01em !important; }
h3 { font-weight: 600 !important; }

/* ===== BUTTONS (primary) ===== */
button[kind="primary"] {
    border-radius: 10px !important;
    font-weight: 600 !important;
    transition: all 0.2s ease !important;
}

/* ===== TABS ===== */
button[data-baseweb="tab"] {
    border-radius: 8px 8px 0 0 !important;
    font-weight: 500 !important;
}
button[data-baseweb="tab"][aria-selected="true"] {
    border-bottom: 2px solid #6C5CE7 !important;
}

/* ===== INPUTS ===== */
input, textarea, [data-baseweb="select"] { border-radius: 8px !important; }

/* ===== CODE ===== */
code {
    background: rgba(108,92,231,0.08) !important;
    color: #6C5CE7 !important;
    border-radius: 4px !important;
    padding: 2px 6px !important;
}

/* ===== DIVIDERS ===== */
hr { border-color: rgba(108,92,231,0.06) !important; }
"""


def inject_theme() -> None:
    """Injection unique du CSS global."""
    st.markdown(f"<style>{GLOBAL_CSS}</style>", unsafe_allow_html=True)


# ===========================================================================
# Composants utilitaires légers
# ===========================================================================
def badge(text: str, color: str = "primary") -> str:
    """Badge coloré inline. Retourne du HTML."""
    colors = {
        "primary": ("#6C5CE7", "#fff"),
        "success": ("#00B894", "#fff"),
        "warning": ("#FDCB6E", "#2D3436"),
        "danger":  ("#E17055", "#fff"),
        "info":    ("#74B9FF", "#2D3436"),
    }
    bg, fg = colors.get(color, colors["primary"])
    return (
        f"<span style='display:inline-block;background:{bg};color:{fg};"
        f"padding:2px 10px;border-radius:20px;font-size:0.75rem;"
        f"font-weight:600;'>{text}</span>"
    )


def section_header(icon: str, title: str, subtitle: str = "") -> None:
    """En-tête de section avec accent violet."""
    sub = f"<div style='color:#6B7280;font-size:0.85rem;margin-bottom:0.8rem;'>{subtitle}</div>" if subtitle else ""
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:8px;margin:1.5rem 0 0.3rem 0;'>"
        f"<div style='width:4px;height:20px;background:linear-gradient(180deg,#6C5CE7,#00CEC9);"
        f"border-radius:2px;'></div>"
        f"<div style='font-size:1.1rem;font-weight:700;color:#2D3436;'>{icon} {title}</div>"
        f"</div>{sub}",
        unsafe_allow_html=True,
    )


def progress_bar_pct(pct: float, text: str = "") -> None:
    """Barre de progression avec pourcentage."""
    pct_int = max(0, min(100, int(pct)))
    st.markdown(
        f"<div style='margin:4px 0;'>"
        f"<div style='display:flex;justify-content:space-between;font-size:0.8rem;margin-bottom:2px;'>"
        f"<span style='color:#2D3436;font-weight:500;'>{text}</span>"
        f"<span style='color:#6C5CE7;font-weight:700;'>{pct_int}%</span></div>"
        f"<div style='background:rgba(108,92,231,0.1);border-radius:6px;height:8px;overflow:hidden;'>"
        f"<div style='background:linear-gradient(90deg,#6C5CE7,#00CEC9);width:{pct_int}%;"
        f"height:100%;border-radius:6px;transition:width 0.5s ease;'></div></div></div>",
        unsafe_allow_html=True,
    )
