"""Onglet **Projets & Tâches** de la saisie hebdomadaire.

Permet de lister les travaux ponctuels, projets personnels ou tâches administratives
à intégrer dans le planning, avec gestion des priorités.
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
PRIORITES = ["Basse", "Moyenne", "Haute"]
TYPES_PROJET = ["Scolaire (Devoir/Projet)", "Personnel", "Administratif", "Autre"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_current_saisie(session: Session) -> SaisieHebdo | None:
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
    st.title("🎯 Projets & Tâches Ponctuelles")
    st.caption("L'IA placera tes tâches prioritaires dans tes meilleurs créneaux de concentration.")

    with get_session() as session:
        saisie = _get_current_saisie(session)
        
        if not saisie:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine en cours.")
            return

        # Récupération de la configuration existante
        config_db = saisie.projets_config or []
        
        # Éditeur de projets
        df_projets = pd.DataFrame(config_db)
        if df_projets.empty:
            df_projets = pd.DataFrame([{
                "titre": "", 
                "type": TYPES_PROJET[0],
                "duree_min": 60, 
                "priorite": "Moyenne",
                "echeance": "Peu importe"
            }])
        else:
            # Migration à la volée
            if "priorite" not in df_projets.columns:
                df_projets["priorite"] = "Moyenne"
            if "type" not in df_projets.columns:
                df_projets["type"] = "Personnel"

        st.subheader("Liste de tes projets de la semaine")
        edited_projets = st.data_editor(
            df_projets,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "titre": st.column_config.TextColumn("Nom de la tâche / Projet", required=True, placeholder="Ex: Rendre rapport de stage"),
                "type": st.column_config.SelectboxColumn("Catégorie", options=TYPES_PROJET, default=TYPES_PROJET[0]),
                "duree_min": st.column_config.NumberColumn("Durée totale (min)", min_value=15, step=15, default=60),
                "priorite": st.column_config.SelectboxColumn("Priorité", options=PRIORITES, default="Moyenne"),
                "echeance": st.column_config.TextColumn("Échéance (ex: Avant Mercredi)", required=False)
            },
            key="projets_editor_v2"
        )

        st.divider()
        
        col_save, col_info = st.columns([1, 2])
        with col_save:
            if st.button("💾 Enregistrer mes projets", type="primary", use_container_width=True):
                projets_propre = []
                for _, row in edited_projets.iterrows():
                    if pd.notna(row.get("titre")) and str(row.get("titre")).strip() != "":
                        projets_propre.append({
                            "titre": str(row["titre"]).strip(),
                            "type": str(row.get("type", TYPES_PROJET[0])),
                            "duree_min": int(row.get("duree_min", 60)),
                            "priorite": str(row.get("priorite", "Moyenne")),
                            "echeance": str(row.get("echeance", "Peu importe"))
                        })

                try:
                    with session_scope() as write_session:
                        saisie_to_update = write_session.get(SaisieHebdo, saisie.id)
                        saisie_to_update.projets_config = projets_propre
                    st.success("✅ Projets enregistrés !")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur lors de la sauvegarde : {e}")
        
        with col_info:
            st.info("💡 **Astuce** : Les tâches 'Haute Priorité' rapportent un bonus d'XP lors de leur validation !")
