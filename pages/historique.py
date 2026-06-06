"""Page **🕰️ Historique (Cadenciers)**.

Permet de consulter les semaines passées (ou générées), de voir le cadencier
complet (le planning jour par jour) tel qu'il a été enregistré dans la base
de données, ainsi que les bilans de chaque semaine.
"""

from __future__ import annotations

import streamlit as st

from database import Semaine, Tache, get_session
from modules.suivi import _render_weekly_stats


# Constantes pour le rendu visuel
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_TYPE_COLORS = {
    "etude": "#4cd137", "sport": "#e84118", "courses": "#00a8ff",
    "projet": "#9c88ff", "dev_perso": "#00bc8c", "social": "#e1b12c",
    "intendance": "#718093", "meal_prep": "#fbc531", "travail": "#d63031",
}


def _render_cadencier(taches: list[Tache]) -> None:
    """Affiche un tableau/cadencier des tâches regroupées par jour."""
    if not taches:
        st.info("Aucune tâche dans ce cadencier.")
        return

    # Grouper par jour
    taches_par_jour: dict[str, list[Tache]] = {j: [] for j in JOURS}
    for t in taches:
        if t.jour in taches_par_jour:
            taches_par_jour[t.jour].append(t)

    st.markdown("### 📅 Le Cadencier")
    
    # On affiche les jours dans des colonnes pour un effet "calendrier"
    # On va faire 7 colonnes, ou bien des onglets si c'est plus propre sur mobile.
    # Les onglets sont très lisibles.
    tabs = st.tabs([j.capitalize() for j in JOURS])
    
    for idx, jour in enumerate(JOURS):
        with tabs[idx]:
            taches_jour = taches_par_jour[jour]
            if not taches_jour:
                st.write("*Journée libre*")
                continue
                
            # Trier par heure de début
            taches_jour.sort(key=lambda t: t.heure_debut)
            
            for t in taches_jour:
                color = _TYPE_COLORS.get((t.type or "").lower(), "#6b7280")
                statut_emoji = "✅" if t.statut == "fait" else "⚠️" if t.statut == "partiellement" else "❌" if t.statut == "non_fait" else "⏳"
                
                # Rendu de la carte de la tâche
                st.markdown(
                    f"<div style='border-left: 4px solid {color}; padding: 10px; margin-bottom: 10px; border-radius: 4px; background: #f8f9fa;'>"
                    f"<div style='font-size: 0.8em; color: #666;'>{t.heure_debut.strftime('%H:%M')} - {t.heure_fin.strftime('%H:%M')}</div>"
                    f"<div style='font-weight: bold; margin-top: 4px;'>{statut_emoji} {t.titre}</div>"
                    f"<div style='font-size: 0.85em; color: #444; margin-top: 4px;'>{t.justification_ia or ''}</div>"
                    f"</div>",
                    unsafe_allow_html=True
                )


def render() -> None:
    st.title("🕰️ Historique des Cadenciers")
    st.caption("Consultez ici vos semaines passées pour voir le chemin parcouru.")

    with get_session() as session:
        # Récupérer toutes les semaines triées de la plus récente à la plus ancienne
        semaines = (
            session.query(Semaine)
            .order_by(Semaine.annee.desc(), Semaine.numero_semaine.desc())
            .all()
        )

        if not semaines:
            st.info("Aucune semaine enregistrée dans la base de données pour le moment.")
            return

        # Créer le dictionnaire pour le sélecteur
        options = {}
        for s in semaines:
            status_emoji = "⏳" if s.statut == "en_cours" else "⚙️" if s.statut == "generee" else "✅"
            options[s.id] = f"{status_emoji} Semaine {s.numero_semaine} ({s.annee}) — du {s.date_debut.strftime('%d/%m')} au {s.date_fin.strftime('%d/%m')}"

        # Par défaut, sélectionner la semaine la plus récente
        selected_id = st.selectbox(
            "Choisissez un cadencier à consulter :",
            options=list(options.keys()),
            format_func=lambda x: options[x]
        )

        semaine_choisie = session.query(Semaine).get(selected_id)
        
        st.divider()

        # Récupérer les tâches de la semaine choisie
        taches = session.query(Tache).filter_by(semaine_id=semaine_choisie.id).all()
        
        # 1. Afficher le bilan (Taux de complétion, stats)
        _render_weekly_stats(session, semaine_choisie)
        
        st.divider()
        
        # 2. Afficher le cadencier complet
        _render_cadencier(taches)

if __name__ == "__main__":
    render()
