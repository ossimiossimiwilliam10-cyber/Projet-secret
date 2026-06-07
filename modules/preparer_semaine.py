"""Nouveau Stepper unifié pour préparer la semaine.

Remplace les 10 pages individuelles de la sidebar par un flux guidé.
"""

from __future__ import annotations

import streamlit as st

from modules import travail
from modules.hebdo import (
    ajustements,
    courses,
    dev_perso,
    etudes,
    intendance,
    projets,
    social,
    sport,
)

# Ordre logique des étapes de préparation
STEPS = [
    ("📖 Études", etudes.render),
    ("💼 Travail", travail.render),
    ("🥊 Sport", sport.render),
    ("🛒 Courses", courses.render),
    ("🎯 Projets", projets.render),
    ("🌱 Dev Perso", dev_perso.render),
    ("🍹 Social", social.render),
    ("🧹 Intendance", intendance.render),
    ("⚖️ Ajustements", ajustements.render),
]


def render() -> None:
    st.title("📅 Préparer ma semaine")
    st.caption(
        "Ce guide étape par étape t'aide à construire un emploi du temps "
        "équilibré en prenant en compte tes cours, ton bien-être et tes corvées."
    )

    # --- Sélecteur de semaine global ---
    offset_courant = int(st.session_state.get("semaine_target_offset", 0))
    options = {0: "📅 Cette semaine (en cours)", 1: "📆 Semaine prochaine"}
    nouveau_offset = st.radio(
        "Quelle semaine souhaites-tu préparer ?",
        options=list(options.keys()),
        format_func=lambda k: options[k],
        index=list(options.keys()).index(offset_courant) if offset_courant in options else 0,
        horizontal=True,
    )
    if nouveau_offset != offset_courant:
        st.session_state["semaine_target_offset"] = nouveau_offset
        st.rerun()

    st.divider()

    # --- Gestion de l'état du stepper ---
    if "preparer_step" not in st.session_state:
        st.session_state.preparer_step = 0

    current_step = st.session_state.preparer_step
    step_name, render_func = STEPS[current_step]

    # --- Barre de progression ---
    progress = (current_step + 1) / len(STEPS)
    st.progress(progress, text=f"Étape {current_step + 1} sur {len(STEPS)} : {step_name}")

    # --- Rendu de l'étape courante ---
    with st.container(border=True):
        render_func()

    st.divider()

    # --- Navigation bas de page ---
    col_prev, col_next = st.columns([1, 1])
    
    with col_prev:
        if current_step > 0:
            if st.button("⬅️ Étape précédente", use_container_width=True):
                st.session_state.preparer_step -= 1
                st.rerun()
                
    with col_next:
        if current_step < len(STEPS) - 1:
            if st.button(f"Étape suivante : {STEPS[current_step+1][0]} ➡️", type="primary", use_container_width=True):
                st.session_state.preparer_step += 1
                st.rerun()
        else:
            if st.button("🚀 Tout est prêt ! Générer le planning", type="primary", use_container_width=True):
                st.success("Parfait ! Rends-toi maintenant dans l'onglet **✨ Génération du planning** dans le menu à gauche.")
                st.balloons()
