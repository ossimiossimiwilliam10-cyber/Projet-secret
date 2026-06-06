"""Super onglet : Configuration."""

from __future__ import annotations

import streamlit as st
from modules import profil, import_externe, aide

TABS = [
    "👤 Profil & Réglages",
    "📸 Import Photo (IA)",
    "❓ Aide"
]

MODULES = {
    "👤 Profil & Réglages": profil,
    "📸 Import Photo (IA)": import_externe,
    "❓ Aide": aide
}

def render() -> None:
    st.title("⚙️ Configuration")
    st.caption("Configure ton profil, importe tes données et trouve de l'aide.")
    
    if "active_config_tab" not in st.session_state:
        st.session_state.active_config_tab = TABS[0]
        
    try:
        default_index = TABS.index(st.session_state.active_config_tab)
    except ValueError:
        default_index = 0

    active_tab = st.radio(
        "Navigation Configuration",
        options=TABS,
        index=default_index,
        horizontal=True,
        label_visibility="collapsed",
        key="config_tab_radio",
        help="Choisis une section : Profil, Import ou Aide",
    )
    
    if active_tab != st.session_state.active_config_tab:
        st.session_state.active_config_tab = active_tab
        st.rerun()
        
    st.divider()
    
    module_to_render = MODULES.get(st.session_state.active_config_tab, profil)
    module_to_render.render()

if __name__ == "__main__":
    render()
