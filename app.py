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
    travail,
)
from modules.hebdo import (
    ajustements,
    courses,
    dev_perso,
    etudes,
    intendance,
    projets,
    social,
    sport,
)


def main() -> None:
    st.set_page_config(
        page_title="Planning étudiant IA",
        page_icon="📚",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # 1. Création des tables si pas encore là.
    init_db()
    # 2. Ajout des colonnes manquantes sur les tables existantes (idempotent).
    #    Sans cette étape, si tu as déjà une BD avant les évolutions du modèle,
    #    certaines colonnes (texte_cache, qcm_cache, fiche_ia...) n'existent pas
    #    et toute requête qui les utilise plante.
    migrate_schema(verbose=True)

    pages = {
        "Configuration": [
            st.Page(profil.render, title="Profil", icon="👤", url_path="profil"),
            st.Page(bibliotheque.render, title="Bibliothèque", icon="📚", url_path="bibliotheque"),
            st.Page("pages/aide.py", title="Aide & Mode d'emploi", icon="❓", url_path="aide"),
        ],
        "Ma semaine": [
            st.Page(etudes.render, title="Études", icon="📖", url_path="hebdo-etudes"),
            st.Page(sport.render, title="Sport", icon="🥊", url_path="hebdo-sport"),
            st.Page(courses.render, title="Courses & Repas", icon="🛒", url_path="hebdo-courses"),
            st.Page(projets.render, title="Projets & Tâches", icon="🎯", url_path="hebdo-projets"),
            st.Page(dev_perso.render, title="Développement Personnel", icon="🌱", url_path="hebdo-dev-perso"),
            st.Page(social.render, title="Social & Loisirs", icon="🍹", url_path="hebdo-social"),
            st.Page(intendance.render, title="Intendance & Admin", icon="🧹", url_path="hebdo-intendance"),
            st.Page(ajustements.render, title="Ajustements", icon="⚖️", url_path="hebdo-ajustements"),
            st.Page(travail.render, title="Jobs & Travail", icon="💼", url_path="hebdo-travail"),
            st.Page(import_externe.render, title="Import Photo (IA)", icon="📸", url_path="import-externe"),
        ],
        "Action": [
            st.Page(generation.render, title="Génération IA", icon="✨", url_path="generation"),
            st.Page(suivi.render, title="Suivi quotidien", icon="📊", url_path="suivi"),
            # Salle d'étude : accessible via clic sur une tâche dans generation.py
            # OU directement depuis la sidebar.
            st.Page("pages/session_etude.py", title="Salle d'étude", icon="🧠", url_path="session-etude"),
            st.Page("pages/achievements.py",  title="Achievements", icon="🏆", url_path="achievements"),
            st.Page("pages/objectifs.py", title="Objectifs", icon="🎯", url_path="objectifs")
        ],
        "Bilan": [
            st.Page(dashboard.render, title="Tableau de bord", icon="📈", url_path="dashboard"),
            st.Page("pages/revisions.py", title="Révisions (Méthode des J)", icon="🧠", url_path="revisions"),
        ],
    }
    nav = st.navigation(pages, position="sidebar")
    nav.run()


if __name__ == "__main__":
    main()