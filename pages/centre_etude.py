"""Centre d'Études unifié : regroupe la Bibliothèque, la Salle d'Étude, les Flash Quiz et la Méthode des J."""

import streamlit as st

# Importer les modules qui ont été migrés
from modules import bibliotheque
from modules import session_etude
from modules import revision_rapide
from modules import revisions_j

# Définition des onglets disponibles
TABS = [
    "📚 Ma Bibliothèque",
    "🧠 Salle d'Étude",
    "⚡ Quiz Flash",
    "📅 Méthode des J"
]

# Mapping vers le module correspondant
MODULES = {
    "📚 Ma Bibliothèque": bibliotheque,
    "🧠 Salle d'Étude": session_etude,
    "⚡ Quiz Flash": revision_rapide,
    "📅 Méthode des J": revisions_j
}

def render():
    st.title("🎓 Centre d'Études")
    st.caption("Ton espace de travail unifié. Navigue entre tes cours, tes sessions d'étude et tes révisions espacées.")

    # Si une autre page (comme le Dashboard) a demandé à ouvrir la salle d'étude
    if "active_etude_tab" not in st.session_state:
        st.session_state.active_etude_tab = TABS[0]
        
    # Determine the index for the radio based on the active tab state
    try:
        default_index = TABS.index(st.session_state.active_etude_tab)
    except ValueError:
        default_index = 0

    # Navigation interne avec style "pilules" via un radio bouton
    active_tab = st.radio(
        "Navigation",
        options=TABS,
        index=default_index,
        horizontal=True,
        label_visibility="collapsed",
    )
    
    # Synchronisation de l'état si modifié par le widget
    if active_tab != st.session_state.active_etude_tab:
        st.session_state.active_etude_tab = active_tab
        st.rerun()
        
    st.divider()
    
    # Appel du module correspondant
    module_to_render = MODULES.get(st.session_state.active_etude_tab, bibliotheque)
    module_to_render.render()

# Si ce fichier est appelé par st.navigation, le code à la racine s'exécute.
render()
