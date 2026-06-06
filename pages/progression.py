"""Super onglet : Progression & Bilan."""

from __future__ import annotations

import streamlit as st
from modules import suivi, dashboard, historique, achievements, objectifs

TABS = [
    "📝 Suivi Quotidien",
    "📈 Tableau de bord",
    "🕰️ Historique",
    "🎯 Objectifs",
    "🏆 Achievements"
]

MODULES = {
    "📝 Suivi Quotidien": suivi,
    "📈 Tableau de bord": dashboard,
    "🕰️ Historique": historique,
    "🎯 Objectifs": objectifs,
    "🏆 Achievements": achievements
}

def render() -> None:
    st.title("📈 Progression & Bilan")
    st.caption("Suis ton avancement, analyse tes statistiques et valide tes objectifs.")
    
    if "active_prog_tab" not in st.session_state:
        st.session_state.active_prog_tab = TABS[0]
        
    try:
        default_index = TABS.index(st.session_state.active_prog_tab)
    except ValueError:
        default_index = 0

    active_tab = st.radio(
        "Navigation",
        options=TABS,
        index=default_index,
        horizontal=True,
        label_visibility="collapsed",
    )
    
    if active_tab != st.session_state.active_prog_tab:
        st.session_state.active_prog_tab = active_tab
        st.rerun()
        
    st.divider()
    
    module_to_render = MODULES.get(st.session_state.active_prog_tab, suivi)
    module_to_render.render()

if __name__ == "__main__":
    render()
