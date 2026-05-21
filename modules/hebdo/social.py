"""Onglet **Social & Loisirs** de la saisie hebdomadaire.

Permet de planifier les moments de détente, sorties entre amis ou temps de repos.
L'IA adapte le planning du lendemain en fonction de l'intensité des sorties.
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
TYPES_SOCIAL = [
    "🍹 Sortie / Fête (Grosse fatigue)",
    "☕ Café / Rencontre courte",
    "🎮 Jeu Vidéo / Ciné",
    "🛋️ Repos / Ressourcement",
    "📞 Appel / Famille"
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
    st.title("🍹 Social & Loisirs")
    st.caption("Équilibre ta vie d'étudiant. L'IA protègera tes temps de récupération.")

    with get_session() as session:
        saisie = _get_current_saisie(session)
        
        if not saisie:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine en cours.")
            return

        config_db = saisie.social_config or []
        
        df_social = pd.DataFrame(config_db)
        if df_social.empty:
            df_social = pd.DataFrame([{
                "activite": "",
                "type": TYPES_SOCIAL[1],
                "duree_min": 60,
                "creneau_pref": "Soir",
                "jour_pref": "Vendredi"
            }])

        st.subheader("Tes rendez-vous et moments de détente")
        edited_social = st.data_editor(
            df_social,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "activite": st.column_config.TextColumn("Événement / Détail", placeholder="Ex: Soirée chez Tom"),
                "type": st.column_config.SelectboxColumn("Type d'activité", options=TYPES_SOCIAL, default=TYPES_SOCIAL[1]),
                "duree_min": st.column_config.NumberColumn("Durée (min)", min_value=15, step=15, default=120),
                "jour_pref": st.column_config.SelectboxColumn("Jour", options=["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche", "Peu importe"], default="Vendredi"),
                "creneau_pref": st.column_config.SelectboxColumn("Moment", options=["Midi", "Après-midi", "Soir", "Nuit"], default="Soir")
            },
            key="social_editor_v2"
        )

        st.divider()
        
        col_save, col_info = st.columns([1, 2])
        with col_save:
            if st.button("💾 Enregistrer mes loisirs", type="primary", use_container_width=True):
                social_propre = []
                for _, row in edited_social.iterrows():
                    if pd.notna(row.get("type")):
                        social_propre.append({
                            "activite": str(row.get("activite", "Détente")).strip(),
                            "type": str(row["type"]),
                            "duree_min": int(row.get("duree_min", 60)),
                            "jour_pref": str(row.get("jour_pref", "Peu importe")),
                            "creneau_pref": str(row.get("creneau_pref", "Soir"))
                        })

                try:
                    with session_scope() as write_session:
                        saisie_to_update = write_session.get(SaisieHebdo, saisie.id)
                        saisie_to_update.social_config = social_propre
                    st.success("✅ Loisirs enregistrés !")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur lors de la sauvegarde : {e}")
        
        with col_info:
            st.caption("💡 **Stratégie IA** : Si tu prévois une 'Sortie / Fête', l'IA placera ton lever plus tard le lendemain et évitera les révisions denses le matin.")
