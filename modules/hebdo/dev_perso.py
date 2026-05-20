"""Onglet **Développement Personnel** de la saisie hebdomadaire."""

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
TYPES_DEV = ["Langue étrangère", "Lecture", "Apprentissage (Code, etc.)", "Méditation / Journaling", "Autre"]
FREQUENCES = ["Quotidien", "1x / semaine", "3x / semaine", "5x / semaine"]

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _get_current_saisie(session: Session) -> tuple[Semaine | None, SaisieHebdo | None]:
    today = datetime.date.today()
    iso_year, iso_week, _ = today.isocalendar()
    semaine = session.query(Semaine).filter_by(annee=iso_year, numero_semaine=iso_week).first()
    if semaine:
        saisie = session.query(SaisieHebdo).filter_by(semaine_id=semaine.id).first()
        return semaine, saisie
    return None, None

# ---------------------------------------------------------------------------
# Rendu UI
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("🌱 Développement Personnel")
    st.caption("Sanctuarise du temps pour tes habitudes quotidiennes (langues, lecture, etc.). L'IA placera ces petits blocs intelligemment.")

    with get_session() as session:
        semaine, saisie = _get_current_saisie(session)
        
        if not saisie or not semaine:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine en cours.")
            return

        config_db: list[dict[str, Any]] = saisie.dev_perso_config or []

        st.subheader("Mes habitudes de la semaine")
        
        df_dev = pd.DataFrame(config_db)
        if df_dev.empty:
            # Lignes d'exemple par défaut
            df_dev = pd.DataFrame([
                {
                    "type": "Langue étrangère", 
                    "libelle": "Anki - Vocabulaire Anglais (Objectif B2)", 
                    "duree_min": 15, 
                    "frequence": "Quotidien"
                },
                {
                    "type": "Lecture", 
                    "libelle": "Lecture théorique (Feynman / Sciences cognitives)", 
                    "duree_min": 30, 
                    "frequence": "3x / semaine"
                }
            ])
            
        edited_dev = st.data_editor(
            df_dev,
            num_rows="dynamic",
            width='stretch',
            column_config={
                "type": st.column_config.SelectboxColumn("Catégorie", options=TYPES_DEV, required=True),
                "libelle": st.column_config.TextColumn("Description de l'activité", required=True),
                "duree_min": st.column_config.NumberColumn("Durée par session (min)", min_value=5, step=5, default=15),
                "frequence": st.column_config.SelectboxColumn("Fréquence", options=FREQUENCES, default="Quotidien"),
            },
            key="editor_dev_perso"
        )

        st.divider()
        if st.button("💾 Enregistrer mes habitudes", type="primary"):
            dev_propres = []
            for _, row in edited_dev.iterrows():
                if pd.notna(row.get("libelle")) and str(row.get("libelle")).strip() != "":
                    dev_propres.append({
                        "type": str(row.get("type", "Autre")),
                        "libelle": str(row["libelle"]).strip(),
                        "duree_min": int(row.get("duree_min", 15)),
                        "frequence": str(row.get("frequence", "Quotidien"))
                    })

            try:
                with session_scope() as write_session:
                    saisie_to_update = write_session.query(SaisieHebdo).get(saisie.id)
                    saisie_to_update.dev_perso_config = dev_propres
                st.success("✅ Tes habitudes de développement personnel sont enregistrées !")
                st.toast("Habitudes sauvegardées", icon="✅")
            except Exception as e:
                st.error(f"Erreur lors de la sauvegarde : {e}")