"""Onglet **Courses & Repas** de la saisie hebdomadaire.

Permet de gérer l'intendance alimentaire, de planifier un menu et de prévoir 
des sessions de Meal Prep pour gagner du temps en semaine.
"""

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

        # --- Section Menu de la semaine ---
        st.subheader("📝 Menu de la semaine (Optionnel)")
        st.caption("Planifier tes repas évite la fatigue décisionnelle et les achats impulsifs.")
        
        menu_hebdo = st.text_area(
            "Qu'as-tu prévu de manger cette semaine ?",
            value=config_db.get("menu_hebdo", ""),
            placeholder="Ex : \n- Lundi/Mardi : Curry de pois chiches\n- Mercredi : Pâtes au pesto\n- Jeudi/Vendredi : Salade de quinoa...",
            height=120,
            help="Note ici tes idées de plats pour ne pas avoir à y réfléchir chaque soir."
        )

        st.divider()

        # --- Section Courses ---
        st.subheader("🛒 Organisation des Courses")
        col1, col2 = st.columns(2)
        with col1:
            idx_freq = 1
            if config_db.get("frequence") in FREQUENCES_COURSES:
                idx_freq = FREQUENCES_COURSES.index(config_db["frequence"])
            frequence = st.selectbox("Fréquence des sorties", options=FREQUENCES_COURSES, index=idx_freq)
            
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
        st.subheader("🍱 Meal Prep (Préparation en avance)")
        
        col_mp1, col_mp2 = st.columns([2, 1])
        with col_mp1:
            meal_prep = st.checkbox(
                "Je prévois une session de Meal Prep cette semaine", 
                value=config_db.get("meal_prep", False),
                help="Préparer tes repas en une seule fois (ex: le dimanche) te fait gagner du temps chaque jour."
            )
            if meal_prep:
                st.info("💡 **Impact IA** : En activant le Meal Prep, l'IA réduira automatiquement ton temps de préparation de repas quotidien dans le planning.")
        
        with col_mp2:
            duree_meal_prep = st.number_input(
                "Durée session (min)",
                min_value=30, max_value=240, step=30,
                value=max(30, int(config_db.get("duree_meal_prep_min", 120) or 120)),
                disabled=not meal_prep
            )

        # Calculateur de gain de temps théorique
        if meal_prep:
            gain_estime = 20 * 5 # 20 min gagnées par jour sur 5 jours
            st.success(f"📈 **Gain de temps estimé** : Environ **{gain_estime} minutes** économisées sur ta semaine de travail !")

        st.divider()
        
        col_btn, col_xp = st.columns([1, 2])
        with col_btn:
            if st.button("💾 Enregistrer l'intendance", type="primary", use_container_width=True):
                nouvelle_config = {
                    "menu_hebdo": menu_hebdo.strip(),
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
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur lors de la sauvegarde : {e}")
        
        with col_xp:
            st.caption("🏆 **Récompenses** : \n- Courses terminées : +20 XP\n- Meal Prep terminé : +50 XP")
