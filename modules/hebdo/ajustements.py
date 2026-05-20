"""Onglet **Ajustements** de la saisie hebdomadaire."""

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
NIVEAUX_ENERGIE = ["Fatigué / Besoin de récupération", "Normal", "En grande forme"]
TYPES_SEMAINE = ["Light / Chill", "Normale", "Chargée", "Partielle (ex: jours off)"]

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
    st.title("⚖️ Ajustements de la semaine")
    st.caption("Adapte le planning à ton état de forme et aux imprévus de dernière minute.")

    with get_session() as session:
        semaine, saisie = _get_current_saisie(session)
        
        if not saisie or not semaine:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine en cours.")
            return

        config_db: dict[str, Any] = saisie.ajustements or {}

        # --- Énergie et Type de semaine ---
        st.subheader("1. Paramètres globaux")
        col1, col2 = st.columns(2)
        
        with col1:
            idx_energie = 1
            if config_db.get("niveau_energie") in NIVEAUX_ENERGIE:
                idx_energie = NIVEAUX_ENERGIE.index(config_db["niveau_energie"])
            niveau_energie = st.radio("Niveau d'énergie prévu", options=NIVEAUX_ENERGIE, index=idx_energie)
            
        with col2:
            idx_type = 1
            if config_db.get("type_semaine") in TYPES_SEMAINE:
                idx_type = TYPES_SEMAINE.index(config_db["type_semaine"])
            type_semaine = st.radio("Rythme de la semaine", options=TYPES_SEMAINE, index=idx_type)

        # --- Événements Exceptionnels ---
        st.divider()
        st.subheader("2. Événements exceptionnels")
        st.caption("Ex: 'Trajet imprévu vendredi soir à 18h', 'Visite médicale mercredi matin'. L'IA en tiendra compte.")
        
        evenements_db = config_db.get("evenements_exceptionnels", [])
        df_evenements = pd.DataFrame([{"evenement": ev} for ev in evenements_db] if evenements_db else [{"evenement": ""}])
            
        edited_evenements = st.data_editor(
            df_evenements,
            num_rows="dynamic",
            width='stretch',
            column_config={
                "evenement": st.column_config.TextColumn("Description de l'événement", required=True),
            },
            key="editor_ajustements"
        )

        # --- Commentaire Libre ---
        st.divider()
        st.subheader("3. Consigne libre pour l'IA")
        commentaire_libre = st.text_area(
            "Un message pour Gemini ?",
            value=config_db.get("commentaire_libre", ""),
            placeholder="Ex: 'Je veux vraiment garder mes soirées libres cette semaine pour souffler.', 'Concentre les révisions de physique sur le début de semaine.'",
            height=100
        )

        st.divider()
        if st.button("💾 Enregistrer les ajustements", type="primary"):
            evenements_propres = []
            for _, row in edited_evenements.iterrows():
                if pd.notna(row.get("evenement")) and str(row.get("evenement")).strip() != "":
                    evenements_propres.append(str(row["evenement"]).strip())

            try:
                with session_scope() as write_session:
                    saisie_to_update = write_session.query(SaisieHebdo).get(saisie.id)
                    saisie_to_update.ajustements = {
                        "niveau_energie": niveau_energie,
                        "type_semaine": type_semaine,
                        "evenements_exceptionnels": evenements_propres,
                        "commentaire_libre": commentaire_libre.strip()
                    }
                st.success("✅ Tes ajustements sont enregistrés !")
                st.toast("Ajustements sauvegardés", icon="✅")
            except Exception as e:
                st.error(f"Erreur lors de la sauvegarde : {e}") 