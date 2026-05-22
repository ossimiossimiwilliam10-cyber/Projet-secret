"""Onglet **Intendance & Admin** de la saisie hebdomadaire.

Permet de planifier les corvées domestiques, la paperasse et l'entretien personnel.
"""

from __future__ import annotations

import datetime

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from database.db import get_session, session_scope
from database.models import SaisieHebdo, Semaine

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
TYPES_INTENDANCE = [
    "🧹 Ménage / Rangement",
    "🧺 Lessive / Repassage",
    "📁 Administratif / Factures",
    "🧴 Soin personnel / Rdv médical",
    "🔧 Réparation / Bricolage",
    "📦 Autre"
]

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
    st.title("🧹 Intendance & Administratif")
    st.caption("Libère ton esprit des corvées en les planifiant dans tes moments de basse énergie.")

    with get_session() as session:
        saisie = _get_current_saisie(session)
        
        if not saisie:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine en cours.")
            return

        config_db = saisie.intendance_config or []
        
        df_int = pd.DataFrame(config_db)
        if df_int.empty:
            df_int = pd.DataFrame([{
                "activite": TYPES_INTENDANCE[0],
                "duree_min": 30,
                "creneau_pref": "Soir"
            }])

        st.subheader("Corvées et tâches administratives")
        edited_int = st.data_editor(
            df_int,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "activite": st.column_config.SelectboxColumn("Type de tâche", options=TYPES_INTENDANCE, required=True),
                "duree_min": st.column_config.NumberColumn("Durée (min)", min_value=15, step=15, default=30),
                "creneau_pref": st.column_config.SelectboxColumn("Moment", options=["Peu importe", "Matin", "Midi", "Après-midi", "Soir", "Week-end"], default="Peu importe")
            },
            key="intendance_editor_v2"
        )

        st.divider()
        
        col_save, col_info = st.columns([1, 2])
        with col_save:
            if st.button("💾 Enregistrer corvées", type="primary", use_container_width=True):
                int_propre = []
                for _, row in edited_int.iterrows():
                    if pd.notna(row.get("activite")):
                        int_propre.append({
                            "activite": str(row["activite"]),
                            "duree_min": int(row.get("duree_min", 30)),
                            "creneau_pref": str(row.get("creneau_pref", "Peu importe"))
                        })

                try:
                    with session_scope() as write_session:
                        saisie_to_update = write_session.get(SaisieHebdo, saisie.id)
                        saisie_to_update.intendance_config = int_propre
                    st.success("✅ Intendance enregistrée !")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur lors de la sauvegarde : {e}")
        
        with col_info:
            st.info("💡 **Conseil** : Planifier le ménage le vendredi soir ou samedi matin permet d'avoir un environnement propre pour réviser sereinement le dimanche.")
