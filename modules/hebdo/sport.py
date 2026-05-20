"""Onglet **Sport** de la saisie hebdomadaire.

Permet de planifier les séances d'entraînement de la semaine (durée, intensité, créneau).
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
INTENSITES = ["Légère (Récupération active)", "Modérée (Entraînement classique)", "Intense (Sparring / Max PR)"]
CRENEAUX = ["Peu importe", "Matin", "Midi", "Après-midi", "Soir"]

# ---------------------------------------------------------------------------
# Helper (similaire à etudes.py)
# ---------------------------------------------------------------------------
def _get_current_saisie(session: Session) -> SaisieHebdo | None:
    """Récupère la saisie de la semaine courante (déjà créée par l'onglet Études)."""
    today = datetime.date.today()
    iso_year, iso_week, _ = today.isocalendar()
    semaine = session.query(Semaine).filter_by(annee=iso_year, numero_semaine=iso_week).first()
    if semaine:
        return session.query(SaisieHebdo).filter_by(semaine_id=semaine.id).first()
    return None

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
        
        st.subheader("Séances prévues cette semaine")
        
        # Préparation du dataframe pour le data_editor
        df_sport = pd.DataFrame(sport_config_db)
        if df_sport.empty:
            # Ligne par défaut pour inviter à la saisie
            df_sport = pd.DataFrame([{"nom": "", "duree_min": 90, "intensite": "Modérée (Entraînement classique)", "creneau_pref": "Soir"}])
            
        edited_sport = st.data_editor(
            df_sport,
            num_rows="dynamic",
            width='stretch',
            column_config={
                "nom": st.column_config.TextColumn("Type de séance (ex: Muscu, Boxe)", required=True),
                "duree_min": st.column_config.NumberColumn("Durée (min)", min_value=15, step=15, default=60),
                "intensite": st.column_config.SelectboxColumn("Intensité", options=INTENSITES, default=INTENSITES[1]),
                "creneau_pref": st.column_config.SelectboxColumn("Créneau préféré", options=CRENEAUX, default="Peu importe")
            },
            key="editor_sport"
        )

        st.divider()
        if st.button("💾 Enregistrer mes séances de sport", type="primary"):
            # Nettoyage des données (ignorer les lignes vides)
            sport_propre = []
            for _, row in edited_sport.iterrows():
                if pd.notna(row.get("nom")) and str(row.get("nom")).strip() != "":
                    sport_propre.append({
                        "nom": str(row["nom"]).strip(),
                        "duree_min": int(row.get("duree_min", 60)),
                        "intensite": str(row.get("intensite", INTENSITES[1])),
                        "creneau_pref": str(row.get("creneau_pref", "Peu importe"))
                    })

            # Sauvegarde
            try:
                with session_scope() as write_session:
                    saisie_to_update = write_session.query(SaisieHebdo).get(saisie.id)
                    saisie_to_update.sport_config = sport_propre
                st.success("✅ Tes séances de sport sont enregistrées !")
                st.toast("Sport sauvegardé", icon="✅")
            except Exception as e:
                st.error(f"Erreur lors de la sauvegarde : {e}")