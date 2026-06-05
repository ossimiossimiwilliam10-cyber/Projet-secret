"""Onglet **Ajustements** de la saisie hebdomadaire.

Adapte le planning à ton état de forme et aux imprévus.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from database import Utilisateur, SaisieHebdo, get_session, session_scope
from utils.helpers import get_or_create_week_for_offset

NIVEAUX_ENERGIE = ["Fatigué / Besoin de récupération", "Normal", "En grande forme"]
TYPES_SEMAINE = ["Light / Chill", "Normale", "Chargée", "Partielle (ex: jours off)"]


def _get_saisie_for_offset(session: Session, offset: int) -> SaisieHebdo | None:
    _, saisie, _ = get_or_create_week_for_offset(session, offset_weeks=offset)
    return saisie


def _get_prev_ajustements(session, offset: int) -> dict:
    try:
        _, ps, _ = get_or_create_week_for_offset(session, offset_weeks=offset - 1)
        return ps.ajustements or {}
    except Exception:
        return {}


def render() -> None:
    st.subheader("⚖️ Ajustements de la semaine")
    st.caption("Adapte le planning à ton état de forme et aux imprévus de dernière minute.")

    offset_courant = int(st.session_state.get("semaine_target_offset", 0))

    with get_session() as session:
        saisie = _get_saisie_for_offset(session, offset_courant)
        if not saisie:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine.")
            return

        config_db: dict[str, Any] = saisie.ajustements or {}

    # Reprendre S-1
    col_prev, _ = st.columns([1, 3])
    with col_prev:
        if st.button("📋 Reprendre mes ajustements de la semaine dernière", width="stretch"):
            prev = _get_prev_ajustements(session, offset_courant)
            if prev:
                with session_scope() as ws:
                    s = ws.get(SaisieHebdo, saisie.id)
                    s.ajustements = prev
                st.toast("Ajustements repris !", icon="📋")
                st.rerun()
            else:
                st.toast("Aucun ajustement la semaine dernière.", icon="ℹ️")

    # --- 1. Paramètres globaux ---
    st.subheader("1. Paramètres globaux")
    col1, col2 = st.columns(2)
    with col1:
        idx_energie = NIVEAUX_ENERGIE.index(config_db.get("niveau_energie", "Normal"))
        niveau_energie = st.radio("Niveau d'énergie prévu", options=NIVEAUX_ENERGIE, index=idx_energie)
    with col2:
        idx_type = TYPES_SEMAINE.index(config_db.get("type_semaine", "Normale"))
        type_semaine = st.radio("Rythme de la semaine", options=TYPES_SEMAINE, index=idx_type)

    # --- 2. Événements exceptionnels ---
    st.divider()
    st.subheader("2. Événements exceptionnels")
    st.caption("Ex: 'Trajet imprévu vendredi 18h', 'Visite médicale mercredi matin'. L'IA en tiendra compte.")
    evenements_db = config_db.get("evenements_exceptionnels", [])
    df_evenements = pd.DataFrame([{"evenement": ev} for ev in evenements_db] if evenements_db else [{"evenement": ""}])
    edited_evenements = st.data_editor(
        df_evenements, num_rows="dynamic", width='stretch',
        column_config={"evenement": st.column_config.TextColumn("Description", required=True)},
        key="editor_ajustements_v2",
    )

    # --- 3. Contraintes à ignorer ---
    st.divider()
    st.subheader("3. Contraintes à ignorer cette semaine")
    profil = session.query(Utilisateur).first()
    contraintes_fixes_dispo = list(profil.logistique.contraintes_fixes or []) if profil else []
    contraintes_ignorees_db = config_db.get("contraintes_ignorees", [])

    if not contraintes_fixes_dispo:
        st.caption("_Aucune contrainte fixe dans ton profil._")
        contraintes_ignorees = []
    else:
        labels_dispo = [c.get("libelle", "(sans libellé)") for c in contraintes_fixes_dispo]
        defaults = [lbl for lbl in contraintes_ignorees_db if lbl in labels_dispo]
        contraintes_ignorees = st.multiselect(
            "Contraintes fixes à IGNORER cette semaine",
            options=labels_dispo, default=defaults,
        )

    # --- 4. Consigne libre ---
    st.divider()
    st.subheader("4. Consigne libre pour l'IA")
    commentaire_libre = st.text_area(
        "Un message pour Gemini / DeepSeek ?",
        value=config_db.get("commentaire_libre", ""),
        placeholder="Ex: 'Je veux garder mes soirées libres cette semaine.', 'Concentre la physique sur le début de semaine.'",
        height=100,
    )

    st.divider()
    if st.button("💾 Enregistrer les ajustements", type="primary"):
        evenements_propres = []
        for _, row in edited_evenements.iterrows():
            if pd.notna(row.get("evenement")) and str(row.get("evenement")).strip():
                evenements_propres.append(str(row["evenement"]).strip())
        try:
            with session_scope() as ws:
                s = ws.get(SaisieHebdo, saisie.id)
                s.ajustements = {
                    "niveau_energie": niveau_energie,
                    "type_semaine": type_semaine,
                    "evenements_exceptionnels": evenements_propres,
                    "contraintes_ignorees": contraintes_ignorees,
                    "commentaire_libre": commentaire_libre.strip(),
                }
            st.success("✅ Ajustements enregistrés !")
            st.toast("Ajustements sauvegardés", icon="✅")
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger("hebdo").exception("sauvegarde ajustements")
            st.error(f"Erreur : {e}")
