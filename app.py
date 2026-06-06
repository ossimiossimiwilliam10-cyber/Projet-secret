"""Point d'entrée Streamlit de l'application **Planning étudiant IA**.

Architecture multipage avec ``st.navigation`` (Streamlit ≥ 1.36).

Pour lancer : ``py -m streamlit run app.py`` depuis la racine du projet.

Note : la page ``pages/session_etude.py`` est enregistrée dans la nav mais peut
aussi être atteinte via ``st.switch_page("pages/session_etude.py")`` depuis
``generation.py`` (clic sur une tâche d'étude).
"""

from __future__ import annotations

import streamlit as st

from database.db import init_db, migrate_schema



def main() -> None:
    st.set_page_config(
        page_title="Exocerveau",
        page_icon="⊞",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # 0. Injection du thème graphique global.
    from utils.theme import inject_theme
    inject_theme()

    # 1. Création des tables si pas encore là.
    init_db()
    # 2. Ajout des colonnes manquantes sur les tables existantes (idempotent).
    migrate_schema(verbose=True)

    pages = {
        "Aujourd'hui": [
            st.Page("pages/aujourdhui.py", title="Aujourd'hui", icon="📍", url_path="aujourdhui", default=True),
        ],
        "Planification": [
            st.Page("pages/planification.py", title="Planification", icon="📅", url_path="planification"),
        ],
        "Action": [
            st.Page("pages/centre_etude.py", title="Centre d'Études", icon="🎓", url_path="centre-etude"),
        ],
        "Progression & Bilan": [
            st.Page("pages/progression.py", title="Progression & Bilan", icon="📈", url_path="progression"),
        ],
        "Configuration": [
            st.Page("pages/configuration.py", title="Configuration", icon="⚙️", url_path="configuration"),
        ],
    }
    nav = st.navigation(pages, position="sidebar")
    nav.run()


if __name__ == "__main__":
    main()