"""Onglet **Courses & Repas** de la saisie hebdomadaire.

Gère l'intendance alimentaire, planifie un menu et prévoit des sessions
de Meal Prep pour gagner du temps en semaine.
"""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from database import SaisieHebdo, get_session, session_scope
from utils.helpers import get_or_create_week_for_offset

FREQUENCES_COURSES = ["Aucune (déjà fait)", "1x par semaine", "2x par semaine", "Livraison"]
CRENEAUX = ["Peu importe", "Matin", "Après-midi", "Soir", "Week-end uniquement"]


def _get_saisie_for_offset(session: Session, offset: int) -> SaisieHebdo | None:
    _, saisie, _ = get_or_create_week_for_offset(session, offset_weeks=offset)
    return saisie


def _get_previous_week_config(session, current_offset: int) -> dict:
    try:
        _, prev_saisie, _ = get_or_create_week_for_offset(session, offset_weeks=current_offset - 1)
        return prev_saisie.courses_config or {}
    except Exception:
        return {}


def render() -> None:
    st.subheader("🛒 Courses & Repas")
    st.caption("Gère ton intendance alimentaire. Le Meal Prep est un excellent moyen de gagner du temps en semaine.")

    offset_courant = int(st.session_state.get("semaine_target_offset", 0))

    with get_session() as session:
        saisie = _get_saisie_for_offset(session, offset_courant)
        if not saisie:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine.")
            return

        config_db = saisie.courses_config or {}

    # --- Reprendre S-1 ---
    col_prev, _ = st.columns([1, 3])
    with col_prev:
        if st.button("📋 Reprendre ma config de la semaine dernière", width="stretch"):
            prev = _get_previous_week_config(session, offset_courant)
            if prev:
                # On vide le menu de la semaine précédente (les plats
                # changent d'une semaine sur l'autre), mais on conserve
                # les habitudes (fréquence, durée, créneau, meal prep).
                prev.pop("menu_hebdo", None)
                with session_scope() as ws:
                    s = ws.get(SaisieHebdo, saisie.id)
                    s.courses_config = prev
                st.toast("Config reprise (menu réinitialisé) !", icon="📋")
                st.rerun()
            else:
                st.toast("Aucune config la semaine dernière.", icon="ℹ️")

    # --- Menu de la semaine ---
    st.subheader("📝 Menu de la semaine (Optionnel)")
    st.caption("Planifier tes repas évite la fatigue décisionnelle et les achats impulsifs.")
    menu_hebdo = st.text_area(
        "Qu'as-tu prévu de manger cette semaine ?",
        value=config_db.get("menu_hebdo", ""),
        placeholder="Ex :\n- Lun/Mar : Curry de pois chiches\n- Mercredi : Pâtes au pesto\n- Jeu/Ven : Salade de quinoa...",
        height=120,
    )

    st.divider()

    # --- Organisation des Courses ---
    st.subheader("🛒 Organisation des Courses")
    col1, col2 = st.columns(2)
    with col1:
        idx_freq = FREQUENCES_COURSES.index(config_db.get("frequence", "1x par semaine"))
        frequence = st.selectbox("Fréquence des sorties", options=FREQUENCES_COURSES, index=idx_freq)
    with col2:
        duree_courses = st.number_input(
            "Durée estimée par session (min)", min_value=15, step=15,
            value=max(15, int(config_db.get("duree_min", 60) or 60)),
            disabled=(frequence in ["Aucune (déjà fait)", "Livraison"]),
        )

    idx_creneau = CRENEAUX.index(config_db.get("creneau_pref", "Peu importe")) if config_db.get("creneau_pref") in CRENEAUX else 0
    creneau_pref = st.selectbox(
        "Créneau préféré pour les courses", options=CRENEAUX, index=idx_creneau,
        disabled=(frequence in ["Aucune (déjà fait)", "Livraison"]),
    )

    # --- Meal Prep ---
    st.divider()
    st.subheader("🍱 Meal Prep (Préparation en avance)")
    col_mp1, col_mp2 = st.columns([2, 1])
    with col_mp1:
        meal_prep = st.checkbox(
            "Je prévois une session de Meal Prep cette semaine",
            value=config_db.get("meal_prep", False),
        )
        if meal_prep:
            st.info("💡 L'algorithme réduira automatiquement ton temps de préparation quotidien dans le planning.")
    with col_mp2:
        duree_meal_prep = st.number_input(
            "Durée session (min)", min_value=30, max_value=240, step=30,
            value=max(30, int(config_db.get("duree_meal_prep_min", 120) or 120)),
            disabled=not meal_prep,
        )

    if meal_prep:
        gain_estime = 20 * 5
        st.success(f"📈 **Gain estimé** : ~**{gain_estime} min** économisées sur ta semaine !")

    st.divider()

    col_btn, col_xp = st.columns([1, 2])
    with col_btn:
        if st.button("💾 Enregistrer l'intendance", type="primary", width='stretch'):
            nouvelle_config = {
                "menu_hebdo": menu_hebdo.strip(),
                "frequence": frequence,
                "duree_min": 0 if frequence in ["Aucune (déjà fait)", "Livraison"] else int(duree_courses),
                "creneau_pref": creneau_pref,
                "meal_prep": meal_prep,
                "duree_meal_prep_min": int(duree_meal_prep) if meal_prep else 0,
            }
            try:
                with session_scope() as ws:
                    s = ws.get(SaisieHebdo, saisie.id)
                    s.courses_config = nouvelle_config
                st.toast("Courses sauvegardées", icon="✅")
                st.rerun()
            except Exception as e:
                st.error(f"Erreur : {e}")

    with col_xp:
        st.caption("🏆 Courses terminées : +20 XP · Meal Prep terminé : +50 XP")
