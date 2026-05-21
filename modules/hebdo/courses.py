"""Onglet **Courses & Repas** de la saisie hebdomadaire."""

from __future__ import annotations

import datetime

import streamlit as st
from sqlalchemy.orm import Session

from database.db import get_session, session_scope
from database.models import SaisieHebdo, Semaine

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
FREQUENCES_COURSES = ["Aucune (déjà fait)", "1x par semaine", "2x par semaine", "Livraison"]
CRENEAUX = ["Peu importe", "Matin", "Après-midi", "Soir", "Week-end uniquement"]

# ---------------------------------------------------------------------------
# Helper
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
    st.title("🛒 Courses & Repas")
    st.caption("Gère ton intendance alimentaire. Le Meal Prep est un excellent moyen de gagner du temps en semaine.")

    with get_session() as session:
        saisie = _get_current_saisie(session)
        
        if not saisie:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine en cours.")
            return

        config_db = saisie.courses_config or {}

        # --- Section Courses ---
        st.subheader("1. Les Courses")
        col1, col2 = st.columns(2)
        with col1:
            idx_freq = 1
            if config_db.get("frequence") in FREQUENCES_COURSES:
                idx_freq = FREQUENCES_COURSES.index(config_db["frequence"])
            frequence = st.selectbox("Fréquence", options=FREQUENCES_COURSES, index=idx_freq)
            
        with col2:
            duree_courses = st.number_input(
                "Durée estimée par session (min)",
                min_value=15, step=15,
                value=max(15, int(config_db.get("duree_min", 60) or 60)),
                disabled=(frequence == "Aucune (déjà fait)")
            )
        
        idx_creneau = 0
        if config_db.get("creneau_pref") in CRENEAUX:
            idx_creneau = CRENEAUX.index(config_db["creneau_pref"])
        creneau_pref = st.selectbox(
            "Créneau préféré pour les courses", 
            options=CRENEAUX, index=idx_creneau,
            disabled=(frequence == "Aucune (déjà fait)")
        )

        # --- Section Meal Prep ---
        st.divider()
        st.subheader("2. Meal Prep (Préparation en avance)")
        meal_prep = st.checkbox(
            "Je prévois une session de Meal Prep cette semaine", 
            value=config_db.get("meal_prep", False)
        )
        duree_meal_prep = st.number_input(
            "Durée de la session (min)",
            min_value=30, max_value=240, step=30,
            value=max(30, int(config_db.get("duree_meal_prep_min", 120) or 120)),
            disabled=not meal_prep
        )

        st.divider()
        if st.button("💾 Enregistrer Courses & Repas", type="primary"):
            nouvelle_config = {
                "frequence": frequence,
                "duree_min": int(duree_courses),
                "creneau_pref": creneau_pref,
                "meal_prep": meal_prep,
                "duree_meal_prep_min": int(duree_meal_prep) if meal_prep else 0
            }
            try:
                with session_scope() as write_session:
                    saisie_to_update = write_session.get(SaisieHebdo, saisie.id)
                    saisie_to_update.courses_config = nouvelle_config
                st.success("✅ Intendance alimentaire enregistrée !")
                st.toast("Courses sauvegardées", icon="✅")
            except Exception as e:
                st.error(f"Erreur lors de la sauvegarde : {e}")