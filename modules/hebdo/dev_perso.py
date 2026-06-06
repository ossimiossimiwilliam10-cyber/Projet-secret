"""Onglet **Développement Personnel** de la saisie hebdomadaire.

Planifie tes activités de croissance (lecture, méditation, langues)
qui enrichissent le planning au-delà des études pures.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from database import SaisieHebdo, get_session, session_scope
from utils.helpers import get_or_create_week_for_offset

CATEGORIES = [
    "🧠 Méditation / Mindfulness", "📚 Lecture (non-scolaire)",
    "🗣️ Langues / Duolingo", "💻 Veille Techno / Side-project",
    "🎨 Créativité (Dessin, Musique)", "🌱 Autre / Réflexion",
]
CRENEAUX = ["Matin", "Après-midi", "Soir", "Peu importe"]


def _get_saisie_for_offset(session: Session, offset: int) -> SaisieHebdo | None:
    _, saisie, _ = get_or_create_week_for_offset(session, offset_weeks=offset)
    return saisie


def _get_prev_dev(session, offset: int) -> list[dict]:
    try:
        _, ps, _ = get_or_create_week_for_offset(session, offset_weeks=offset - 1)
        return ps.dev_perso_config or []
    except Exception:
        return []


def render() -> None:
    st.subheader("🌱 Développement Personnel")
    st.caption("Investis en toi-même. L'IA utilisera ces blocs comme des respirations productives.")

    offset_courant = int(st.session_state.get("semaine_target_offset", 0))

    with get_session() as session:
        saisie = _get_saisie_for_offset(session, offset_courant)
        if not saisie:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine.")
            return

        config_db = saisie.dev_perso_config or []
        df_dev = pd.DataFrame(config_db)
        if df_dev.empty:
            df_dev = pd.DataFrame([{"activite": CATEGORIES[0], "frequence": "3x par semaine", "duree_min": 20, "creneau_pref": "Matin"}])

    # Reprendre S-1
    col_prev, _ = st.columns([1, 3])
    with col_prev:
        if st.button("📋 Reprendre mes habitudes de la semaine dernière", width="stretch"):
            prev = _get_prev_dev(session, offset_courant)
            if prev:
                with session_scope() as ws:
                    s = ws.get(SaisieHebdo, saisie.id)
                    s.dev_perso_config = prev
                st.session_state.pop(f"dev_perso_config_{saisie.id}", None)
                st.toast("Habitudes reprises !", icon="📋")
                st.rerun()
            else:
                st.toast("Aucune habitude la semaine dernière.", icon="ℹ️")

    st.subheader("Tes habitudes de croissance")
    state_key = f"dev_perso_config_{saisie.id}"
    if state_key not in st.session_state:
        st.session_state[state_key] = [dict(d) for d in config_db]

    habitudes_actuelles = st.session_state[state_key]

    if habitudes_actuelles:
        st.caption(
            f"⏱️ **{len(habitudes_actuelles)} habitude(s) planifiée(s)** — "
            f"l'IA intégrera ces sessions de croissance à ton planning. 👏"
        )
    else:
        st.info("Aucune habitude de développement personnel prévue pour le moment.")

    # Affichage en cartes
    for idx, habitude in enumerate(habitudes_actuelles):
        # Extraction de l'icône de l'activité
        activite_full = habitude.get("activite", "🌱 Autre")
        type_icon = activite_full.split(" ")[0] if activite_full else "🌱"

        with st.container(border=True):
            col_icon, col_details, col_del = st.columns([1, 8, 1])
            with col_icon:
                st.markdown(f"<h2 style='text-align:center;'>{type_icon}</h2>", unsafe_allow_html=True)
            with col_details:
                st.markdown(f"**{activite_full}**")
                st.caption(f"⏱️ {habitude.get('duree_min', 20)} min | 🔁 {habitude.get('frequence', '1x')} | 📅 Créneau : {habitude.get('creneau_pref', 'Peu importe')}")
            with col_del:
                if st.button("❌", key=f"del_dv_{saisie.id}_{idx}", help="Supprimer cette habitude"):
                    habitudes_actuelles.pop(idx)
                    st.rerun()

    # Formulaire d'ajout
    with st.expander("➕ **Ajouter une habitude**", expanded=len(habitudes_actuelles) == 0):
        with st.form(f"form_add_dv_{saisie.id}"):
            c1, c2 = st.columns(2)
            with c1:
                new_activite = st.selectbox("Activité", options=CATEGORIES, index=0)
                new_frequence = st.text_input("Objectif (ex: Tous les jours, 2x...)", value="3x par semaine")
            with c2:
                new_duree = st.number_input("Durée / session (min)", min_value=5, step=5, value=20)
                new_creneau = st.selectbox("Moment idéal", options=CRENEAUX, index=0)
                
            if st.form_submit_button("✓ Ajouter", type="primary", use_container_width=True):
                habitudes_actuelles.append({
                    "activite": new_activite,
                    "frequence": new_frequence.strip() if new_frequence.strip() else "1x",
                    "duree_min": int(new_duree),
                    "creneau_pref": new_creneau,
                })
                st.rerun()

    st.divider()
    col_save, col_info = st.columns([1, 2])
    with col_save:
        if st.button("💾 Enregistrer mes habitudes", type="primary", width='stretch'):
            dev_propre = habitudes_actuelles
            try:
                with session_scope() as ws:
                    s = ws.get(SaisieHebdo, saisie.id)
                    s.dev_perso_config = dev_propre
                st.toast("Dev perso sauvegardé", icon="✅")
            except Exception as e:
                st.error(f"Erreur : {e}")
    with col_info:
        st.info("💡 La lecture et la méditation améliorent ta concentration pour les blocs d'études suivants.")
