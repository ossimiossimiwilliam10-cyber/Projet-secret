"""Onglet **Sport** de la saisie hebdomadaire.

Permet de planifier les séances d'entraînement de la semaine (durée, intensité, créneau).
Optimisé avec des icônes, des types de sport et un calculateur de charge.
"""

from __future__ import annotations

import datetime
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from database.db import get_session, session_scope
from database.models import SaisieHebdo, Semaine

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
INTENSITES = [
    "🟢 Légère (Récupération active)", 
    "🟡 Modérée (Entraînement classique)", 
    "🔴 Intense (Sparring / Max PR)"
]

TYPES_SPORT = {
    "🏃‍♂️ Course / Cardio": "Cardio",
    "🏋️‍♀️ Musculation / Force": "Force",
    "🥊 Boxe / Combat": "Combat",
    "🧘‍♂️ Yoga / Mobilité": "Récupération",
    "🏊‍♂️ Natation": "Complet",
    "🚴‍♂️ Cyclisme": "Cardio",
    "🎾 Sport de raquette": "Mixte",
    "⚽ Sport collectif": "Mixte",
    "🤸‍♂️ Gymnastique": "Force/Souplesse",
    "🎯 Autre": "Autre"
}

CRENEAUX = ["Peu importe", "Matin", "Midi", "Après-midi", "Soir"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_current_saisie(session: Session) -> SaisieHebdo | None:
    """Récupère la saisie de la semaine courante."""
    today = datetime.date.today()
    iso_year, iso_week, _ = today.isocalendar()
    semaine = session.query(Semaine).filter_by(annee=iso_year, numero_semaine=iso_week).first()
    if semaine:
        return session.query(SaisieHebdo).filter_by(semaine_id=semaine.id).first()
    return None

def _afficher_resume_charge(sport_config: list[dict[str, Any]]) -> None:
    """Affiche un résumé visuel de la charge sportive prévue."""
    if not sport_config:
        st.info("Aucune séance prévue pour le moment.")
        return

    total_min = sum(int(s.get("duree_min", 0)) for s in sport_config)
    nb_intense = sum(1 for s in sport_config if "🔴" in s.get("intensite", ""))
    nb_moderee = sum(1 for s in sport_config if "🟡" in s.get("intensite", ""))
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Temps total", f"{total_min // 60}h{total_min % 60:02d}")
    col2.metric("Séances intenses", nb_intense)
    col3.metric("Séances modérées", nb_moderee)

# ---------------------------------------------------------------------------
# Rendu UI
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("🥊 Sport & Entraînement")
    st.caption("Planifie tes séances physiques. L'IA évitera de placer des révisions denses juste après une séance intense.")

    with get_session() as session:
        saisie = _get_current_saisie(session)
        
        if not saisie:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine en cours.")
            return

        # Récupération de la configuration existante
        sport_config_db: list[dict[str, Any]] = saisie.sport_config or []
        
        # 1. Résumé de la charge
        st.subheader("Résumé de la semaine")
        _afficher_resume_charge(sport_config_db)
        
        st.divider()
        
        # 2. Éditeur de séances
        st.subheader("Séances prévues")
        
        # Préparation du dataframe pour le data_editor
        df_sport = pd.DataFrame(sport_config_db)
        if df_sport.empty:
            df_sport = pd.DataFrame([{
                "type": list(TYPES_SPORT.keys())[0],
                "nom": "", 
                "duree_min": 60, 
                "intensite": INTENSITES[1], 
                "creneau_pref": "Soir"
            }])
            
        edited_sport = st.data_editor(
            df_sport,
            num_rows="dynamic",
            width='stretch',
            column_config={
                "type": st.column_config.SelectboxColumn("Discipline", options=list(TYPES_SPORT.keys()), required=True),
                "nom": st.column_config.TextColumn("Détails (ex: Séance Jambes, Sparring)", required=False),
                "duree_min": st.column_config.NumberColumn("Durée (min)", min_value=15, step=15, default=60),
                "intensite": st.column_config.SelectboxColumn("Intensité", options=INTENSITES, default=INTENSITES[1]),
                "creneau_pref": st.column_config.SelectboxColumn("Créneau préféré", options=CRENEAUX, default="Peu importe")
            },
            key="editor_sport_v2"
        )

        st.divider()
        
        # 3. Sauvegarde
        col_save, col_info = st.columns([1, 2])
        with col_save:
            if st.button("💾 Enregistrer mes séances", type="primary", use_container_width=True):
                # Nettoyage des données
                sport_propre = []
                for _, row in edited_sport.iterrows():
                    # On accepte la ligne si elle a un type
                    if pd.notna(row.get("type")):
                        sport_propre.append({
                            "type": str(row["type"]),
                            "nom": str(row.get("nom", "")).strip(),
                            "duree_min": int(row.get("duree_min", 60)),
                            "intensite": str(row.get("intensite", INTENSITES[1])),
                            "creneau_pref": str(row.get("creneau_pref", "Peu importe"))
                        })

                # Sauvegarde
                try:
                    with session_scope() as write_session:
                        saisie_to_update = write_session.get(SaisieHebdo, saisie.id)
                        saisie_to_update.sport_config = sport_propre
                    st.success("✅ Tes séances de sport sont enregistrées !")
                    st.toast("Sport sauvegardé", icon="✅")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur lors de la sauvegarde : {e}")
        
        with col_info:
            st.caption("💡 L'intensité influence la récupération : une séance 🔴 bloque les révisions théoriques intenses pendant 2h après le sport.")
