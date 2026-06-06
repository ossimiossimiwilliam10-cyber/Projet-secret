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
from modules import (
    bibliotheque,
    dashboard,
    generation,
    import_externe,
    profil,
    suivi,
    preparer_semaine,
)


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
            st.Page(preparer_semaine.render, title="Préparer ma semaine", icon="📅", url_path="preparer-semaine"),
            st.Page(generation.render, title="Génération IA", icon="✨", url_path="generation"),
        ],
        "Action": [
            st.Page("pages/session_etude.py", title="Salle d'étude", icon="🧠", url_path="session-etude"),
            st.Page("pages/revision_rapide.py", title="Révision Rapide", icon="⚡", url_path="revision-rapide"),
            st.Page("pages/examen_blanc.py", title="Examen Blanc", icon="📝", url_path="examen-blanc"),
            st.Page(bibliotheque.render, title="Bibliothèque", icon="📚", url_path="bibliotheque"),
        ],
        "Progression & Bilan": [
            st.Page(suivi.render, title="Suivi quotidien", icon="📝", url_path="suivi"),
            st.Page(dashboard.render, title="Tableau de bord", icon="📈", url_path="dashboard"),
            st.Page("pages/historique.py", title="Historique (Cadenciers)", icon="🕰️", url_path="historique"),
            st.Page("pages/revisions.py", title="Révisions (Méthode des J)", icon="🔥", url_path="revisions"),
            st.Page("pages/achievements.py",  title="Achievements", icon="🏆", url_path="achievements"),
            st.Page("pages/objectifs.py", title="Objectifs", icon="🎯", url_path="objectifs"),
        ],
        "Configuration": [
            st.Page(profil.render, title="Profil & Réglages", icon="👤", url_path="profil"),
            st.Page(import_externe.render, title="Import Photo (IA)", icon="📸", url_path="import-externe"),
            st.Page("pages/aide.py", title="Aide & Mode d'emploi", icon="❓", url_path="aide"),
        ],
    }
    nav = st.navigation(pages, position="sidebar")
    nav.run()


if __name__ == "__main__":
    main()