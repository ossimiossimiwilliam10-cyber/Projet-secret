"""Onglet **Génération du planning IA**.

Déclenche l'appel à l'API Gemini, sauvegarde le résultat en base et affiche le planning.
"""

from __future__ import annotations

import datetime
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from database.db import get_session, session_scope
from database.models import Semaine, Tache
from services.ai_planner import generate_schedule_from_ai

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_current_week(session: Session) -> Semaine | None:
    today = datetime.date.today()
    iso_year, iso_week, _ = today.isocalendar()
    return session.query(Semaine).filter_by(annee=iso_year, numero_semaine=iso_week).first()

def _str_to_time(time_str: str) -> datetime.time:
    """Convertit 'HH:MM' en objet datetime.time."""
    h, m = map(int, time_str.split(':'))
    return datetime.time(h, m)

def _get_color_for_task(task_type: str) -> str:
    """Retourne une couleur hexadécimale selon le type de tâche."""
    colors = {
        "repas": "#fbc531",
        "sommeil": "#273c75",
        "etude": "#4cd137",
        "sport": "#e84118",
        "courses": "#00a8ff",
        "projet": "#9c88ff",
        "dev_perso": "#00bc8c",
        "social": "#e1b12c",
        "intendance": "#718093",
        "transport": "#bdc3c7",
    }
    return colors.get(task_type.lower(), "#353b48")

# ---------------------------------------------------------------------------
# Traitement & Sauvegarde du JSON
# ---------------------------------------------------------------------------
def _save_planning_to_db(semaine_id: int, planning_json: dict[str, Any]) -> None:
    """Transforme le JSON de Gemini en objets Tache et les sauvegarde."""
    jours_semaine = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    
    with session_scope() as session:
        semaine = session.get(Semaine, semaine_id)
        if not semaine: return
        
        # Mise à jour des métadonnées de la semaine
        semaine.score_realisme = planning_json.get("score_realisme", 0)
        semaine.bilan_ia = planning_json.get("justification_globale", "")
        semaine.statut = "generee"

        # Supprimer les anciennes tâches si on regénère
        session.query(Tache).filter_by(semaine_id=semaine_id).delete()
        
        # Création des nouvelles tâches
        planning_jours = planning_json.get("planning", {})
        for jour in jours_semaine:
            taches_jour = planning_jours.get(jour, [])
            for t_data in taches_jour:
                try:
                    nouvelle_tache = Tache(
                        semaine_id=semaine_id,
                        type=t_data.get("type", "autre").lower(),
                        titre=t_data.get("titre", "Tâche sans nom"),
                        jour=jour,
                        heure_debut=_str_to_time(t_data["heure_debut"]),
                        heure_fin=_str_to_time(t_data["heure_fin"]),
                        obligatoire=t_data.get("obligatoire", False),
                        justification_ia=t_data.get("justification", ""),
                        statut="a_faire",
                        chapitre_ids=t_data.get("chapitre_ids", [])
                    )
                    session.add(nouvelle_tache)
                except Exception as e:
                    print(f"Erreur d'import pour la tâche {t_data}: {e}")

# ---------------------------------------------------------------------------
# Rendu UI
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("✨ Génération du planning")
    st.caption("Laisse l'IA calculer le meilleur emploi du temps selon tes contraintes.")

    with get_session() as session:
        semaine = _get_current_week(session)
        
        if not semaine:
            st.warning("⚠️ Ouvre l'onglet 'Études' pour initialiser la semaine.")
            return

        # --- BOUTON DE GÉNÉRATION ---
        st.subheader("1. Lancer la planification")

        consignes_manuelles = st.text_area(
            "💬 Consignes exceptionnelles pour cette semaine",
            value="",
            placeholder="Ex : Jeudi je dois absolument finir avant 18h. Ne mets pas de sport mardi car j'ai mal au genou. Priorise les maths cette semaine.",
            help="Ajoute ici toute consigne libre que l'IA devra impérativement respecter en plus des règles habituelles.",
        )

        if st.button("🚀 Générer le planning avec Gemini", type="primary", width='stretch'):
            with st.spinner("🧠 Gemini réfléchit à ton planning... (ça prend environ 15 à 30 secondes)"):
                try:
                    resultat_json = generate_schedule_from_ai(session, semaine.id, consignes_manuelles=consignes_manuelles)
                    _save_planning_to_db(semaine.id, resultat_json)
                    st.success("✅ Planning généré avec succès !")
                    st.toast("Planning prêt !", icon="🎉")
                    
                    # Affichage immédiat des insights de l'IA (avant le rechargement)
                    if "alertes" in resultat_json and resultat_json["alertes"]:
                        st.error(f"⚠️ **Alertes de l'IA :** {', '.join(resultat_json['alertes'])}")
                    if "suggestions" in resultat_json and resultat_json["suggestions"]:
                        st.info(f"💡 **Suggestions :** {', '.join(resultat_json['suggestions'])}")
                        
                except Exception as e:
                    st.error(f"❌ Erreur lors de la génération : {e}")

        st.divider()

        # --- AFFICHAGE DU PLANNING GÉNÉRÉ ---
        if semaine.statut == "generee":
            st.subheader(f"📊 Planning de la Semaine {semaine.numero_semaine}")
            
            col1, col2 = st.columns([3, 1])
            with col1:
                st.info(f"**Stratégie de l'IA :** {semaine.bilan_ia}")
            with col2:
                score = semaine.score_realisme or 0
                color = "green" if score >= 80 else "orange" if score >= 50 else "red"
                st.markdown(f"<h3 style='text-align: center; color: {color};'>Score : {score}/100</h3>", unsafe_allow_html=True)

            taches = session.query(Tache).filter_by(semaine_id=semaine.id).order_by(Tache.jour, Tache.heure_debut).all()
            
            jours_fr = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
            onglets_jours = st.tabs([j.capitalize() for j in jours_fr])

            for idx_jour, onglet in enumerate(onglets_jours):
                jour_cible = jours_fr[idx_jour]
                taches_du_jour = [t for t in taches if t.jour == jour_cible]
                
                with onglet:
                    if not taches_du_jour:
                        st.write("Aucune tâche planifiée pour ce jour.")
                    else:
                        for t in taches_du_jour:
                            # On prépare le texte du bouton
                            color = _get_color_for_task(t.type)
                            oblig_icon = "🔒 " if t.obligatoire else ""
                            label = f"{t.heure_debut.strftime('%H:%M')} : {oblig_icon}{t.titre}"

                            # On crée le bouton cliquable
                            if st.button(label, key=f"btn_{t.id}", width='stretch'):

                                # On vérifie si la tâche a bien des chapitres associés
                                if getattr(t, 'chapitre_ids', None) and len(t.chapitre_ids) > 0:
                                    # On stocke le premier ID de la liste dans la session
                                    st.session_state.target_chapitre_id = t.chapitre_ids[0]

                                    # On bascule vers la page de la salle d'étude
                                    try:
                                        st.switch_page("pages/session_etude.py")
                                    except Exception:
                                        # Fallback: set a flag if switch_page not available
                                        st.session_state.navigate_to_session = True
                                else:
                                    st.toast("Cette tâche n'est liée à aucun chapitre spécifique.")