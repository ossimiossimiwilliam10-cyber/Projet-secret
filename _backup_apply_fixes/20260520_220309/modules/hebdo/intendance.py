"""Onglet **Intendance & Admin** de la saisie hebdomadaire."""

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
TYPES_INTENDANCE = ["Ménage / Lessive", "Administratif", "Rendez-vous", "Autre"]
JOURS = ["Peu importe", "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

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
    st.title("🧹 Intendance & Admin")
    st.caption("Planifie tes tâches ménagères, tes démarches administratives ou tes rendez-vous.")

    with get_session() as session:
        semaine, saisie = _get_current_saisie(session)
        
        if not saisie or not semaine:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine en cours.")
            return

        config_db: list[dict[str, Any]] = saisie.intendance_config or []

        st.subheader("Tâches de la semaine")
        
        df_intendance = pd.DataFrame(config_db)
        if df_intendance.empty:
            # Ligne d'exemple par défaut
            df_intendance = pd.DataFrame([{
                "type": "Ménage / Lessive",
                "libelle": "Gros rangement et ménage",
                "duree_min": 60,
                "jour_prefere": "Peu importe"
            }])
            
        edited_intendance = st.data_editor(
            df_intendance,
            num_rows="dynamic",
            width='stretch',
            column_config={
                "type": st.column_config.SelectboxColumn("Type", options=TYPES_INTENDANCE, required=True),
                "libelle": st.column_config.TextColumn("Description de la tâche", required=True),
                "duree_min": st.column_config.NumberColumn("Durée (min)", min_value=15, step=15, default=30),
                "jour_prefere": st.column_config.SelectboxColumn("Jour souhaité", options=JOURS, default="Peu importe"),
            },
            key="editor_intendance"
        )

        st.divider()
        if st.button("💾 Enregistrer Intendance", type="primary"):
            intendance_propres = []
            for _, row in edited_intendance.iterrows():
                if pd.notna(row.get("libelle")) and str(row.get("libelle")).strip() != "":
                    intendance_propres.append({
                        "type": str(row.get("type", "Autre")),
                        "libelle": str(row["libelle"]).strip(),
                        "duree_min": int(row.get("duree_min", 30)),
                        "jour_prefere": str(row.get("jour_prefere", "Peu importe"))
                    })

            try:
                with session_scope() as write_session:
                    saisie_to_update = write_session.query(SaisieHebdo).get(saisie.id)
                    saisie_to_update.intendance_config = intendance_propres
                st.success("✅ Tâches d'intendance enregistrées !")
                st.toast("Intendance sauvegardée", icon="✅")
            except Exception as e:
                st.error(f"Erreur lors de la sauvegarde : {e}")