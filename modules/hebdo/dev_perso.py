"""Onglet **Développement Personnel** de la saisie hebdomadaire.

Permet de planifier des activités de croissance (lecture, méditation, langues)
qui enrichissent le planning au-delà des études pures.
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
CATEGORIES = [
    "🧠 Méditation / Mindfulness",
    "📚 Lecture (non-scolaire)",
    "🗣️ Langues / Duolingo",
    "💻 Veille Techno / Side-project",
    "🎨 Créativité (Dessin, Musique)",
    "🌱 Autre / Réflexion"
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
    st.title("🌱 Développement Personnel")
    st.caption("Investis en toi-même. L'IA utilisera ces blocs comme des respirations productives.")

    with get_session() as session:
        saisie = _get_current_saisie(session)
        
        if not saisie:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine en cours.")
            return

        config_db = saisie.dev_perso_config or []
        
        df_dev = pd.DataFrame(config_db)
        if df_dev.empty:
            df_dev = pd.DataFrame([{
                "activite": CATEGORIES[0], 
                "frequence": "3x par semaine",
                "duree_min": 20,
                "creneau_pref": "Matin"
            }])

        st.subheader("Tes habitudes de croissance")
        edited_dev = st.data_editor(
            df_dev,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "activite": st.column_config.SelectboxColumn("Activité", options=CATEGORIES, required=True),
                "frequence": st.column_config.TextColumn("Objectif (ex: Tous les jours, 2x...)"),
                "duree_min": st.column_config.NumberColumn("Durée / session (min)", min_value=5, step=5, default=20),
                "creneau_pref": st.column_config.SelectboxColumn("Moment idéal", options=["Matin", "Après-midi", "Soir", "Peu importe"], default="Matin")
            },
            key="dev_perso_editor_v2"
        )

        st.divider()
        
        col_save, col_info = st.columns([1, 2])
        with col_save:
            if st.button("💾 Enregistrer mes habitudes", type="primary", use_container_width=True):
                dev_propre = []
                for _, row in edited_dev.iterrows():
                    if pd.notna(row.get("activite")):
                        dev_propre.append({
                            "activite": str(row["activite"]),
                            "frequence": str(row.get("frequence", "Toutes les fois")).strip(),
                            "duree_min": int(row.get("duree_min", 20)),
                            "creneau_pref": str(row.get("creneau_pref", "Peu importe"))
                        })

                try:
                    with session_scope() as write_session:
                        saisie_to_update = write_session.get(SaisieHebdo, saisie.id)
                        saisie_to_update.dev_perso_config = dev_propre
                    st.success("✅ Habitudes enregistrées !")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur lors de la sauvegarde : {e}")
        
        with col_info:
            st.info("💡 **Le savais-tu ?** La lecture et la méditation améliorent ta concentration pour tes blocs d'études suivants.")
