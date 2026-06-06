"""Super onglet : Planification (Préparer la semaine + Génération IA)."""

from __future__ import annotations

import streamlit as st
from modules import preparer_semaine, generation

TABS = [
    "📅 Préparer ma semaine",
    "✨ Génération IA"
]

MODULES = {
    "📅 Préparer ma semaine": preparer_semaine,
    "✨ Génération IA": generation
}

def render() -> None:
    st.title("📅 Planification")
    st.caption("Organise tes semaines et laisse l'IA générer le planning optimal.")
    
    if "active_planif_tab" not in st.session_state:
        st.session_state.active_planif_tab = TABS[0]
        
    try:
        default_index = TABS.index(st.session_state.active_planif_tab)
    except ValueError:
        default_index = 0

    active_tab = st.radio(
        "Navigation",
        options=TABS,
        index=default_index,
        horizontal=True,
        label_visibility="collapsed",
    )
    
    if active_tab != st.session_state.active_planif_tab:
        st.session_state.active_planif_tab = active_tab
        st.rerun()
        
    st.divider()
    
    module_to_render = MODULES.get(st.session_state.active_planif_tab, preparer_semaine)
    module_to_render.render()

if __name__ == "__main__":
    render()
