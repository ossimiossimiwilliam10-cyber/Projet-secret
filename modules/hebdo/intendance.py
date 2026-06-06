"""Onglet **Intendance & Admin** de la saisie hebdomadaire.

Planifie les corvées domestiques, la paperasse et l'entretien personnel.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from database import SaisieHebdo, get_session, session_scope
from utils.helpers import get_or_create_week_for_offset

TYPES_INTENDANCE = [
    "🧹 Ménage / Rangement", "🧺 Lessive / Repassage",
    "📁 Administratif / Factures", "🧴 Soin personnel / Rdv médical",
    "🔧 Réparation / Bricolage", "📦 Autre",
]


def _get_saisie_for_offset(session: Session, offset: int) -> SaisieHebdo | None:
    _, saisie, _ = get_or_create_week_for_offset(session, offset_weeks=offset)
    return saisie


def _get_prev_intendance(session, offset: int) -> list[dict]:
    try:
        _, ps, _ = get_or_create_week_for_offset(session, offset_weeks=offset - 1)
        return ps.intendance_config or []
    except Exception:
        return []


def render() -> None:
    st.subheader("🧹 Intendance & Administratif")
    st.caption("Libère ton esprit des corvées en les planifiant dans tes moments de basse énergie.")

    offset_courant = int(st.session_state.get("semaine_target_offset", 0))

    with get_session() as session:
        saisie = _get_saisie_for_offset(session, offset_courant)
        if not saisie:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine.")
            return

        config_db = saisie.intendance_config or []
        df_int = pd.DataFrame(config_db)
        if df_int.empty:
            df_int = pd.DataFrame([{"activite": TYPES_INTENDANCE[0], "duree_min": 30, "creneau_pref": "Peu importe"}])

    # Reprendre S-1
    col_prev, _ = st.columns([1, 3])
    with col_prev:
        if st.button("📋 Reprendre mes corvées de la semaine dernière", width="stretch"):
            prev = _get_prev_intendance(session, offset_courant)
            if prev:
                with session_scope() as ws:
                    s = ws.get(SaisieHebdo, saisie.id)
                    s.intendance_config = prev
                st.session_state.pop(f"intendance_config_{saisie.id}", None)
                st.toast("Corvées reprises !", icon="📋")
                st.rerun()
            else:
                st.toast("Aucune corvée la semaine dernière.", icon="ℹ️")

    st.subheader("Corvées et tâches administratives")

    state_key = f"intendance_config_{saisie.id}"
    if state_key not in st.session_state:
        st.session_state[state_key] = [dict(c) for c in config_db]

    corvees_actuelles = st.session_state[state_key]

    if corvees_actuelles:
        total_min = sum(int(c.get("duree_min", 0)) for c in corvees_actuelles)
        st.caption(f"⏱️ **{len(corvees_actuelles)} corvée(s) · ~{total_min // 60}h{total_min % 60:02d}** — bien organisé !")
    else:
        st.info("Aucune corvée prévue pour le moment.")

    # Affichage en cartes
    for idx, corvee in enumerate(corvees_actuelles):
        activite_full = corvee.get("activite", "📦 Autre")
        type_icon = activite_full.split(" ")[0] if activite_full else "📦"

        with st.container(border=True):
            col_icon, col_details, col_del = st.columns([1, 8, 1])
            with col_icon:
                st.markdown(f"<h2 style='text-align:center;'>{type_icon}</h2>", unsafe_allow_html=True)
            with col_details:
                st.markdown(f"**{activite_full}**")
                st.caption(f"⏱️ {corvee.get('duree_min', 30)} min | 📅 Moment idéal : {corvee.get('creneau_pref', 'Peu importe')}")
            with col_del:
                if st.button("❌", key=f"del_int_{saisie.id}_{idx}", help="Supprimer la corvée"):
                    corvees_actuelles.pop(idx)
                    st.rerun()

    # Formulaire d'ajout
    with st.expander("➕ **Ajouter une corvée**", expanded=len(corvees_actuelles) == 0):
        with st.form(f"form_add_int_{saisie.id}"):
            c1, c2 = st.columns(2)
            with c1:
                new_activite = st.selectbox("Type de tâche", options=TYPES_INTENDANCE, index=0)
            with c2:
                new_duree = st.number_input("Durée (min)", min_value=15, step=15, value=30)
                new_creneau = st.selectbox("Moment", options=["Peu importe", "Matin", "Midi", "Après-midi", "Soir", "Week-end"], index=0)
                
            if st.form_submit_button("✓ Ajouter", type="primary", use_container_width=True):
                corvees_actuelles.append({
                    "activite": new_activite,
                    "duree_min": int(new_duree),
                    "creneau_pref": new_creneau,
                })
                st.rerun()

    st.divider()
    col_save, col_info = st.columns([1, 2])
    with col_save:
        if st.button("💾 Enregistrer corvées", type="primary", width='stretch'):
            int_propre = corvees_actuelles
            try:
                with session_scope() as ws:
                    s = ws.get(SaisieHebdo, saisie.id)
                    s.intendance_config = int_propre
                st.toast("Intendance sauvegardée", icon="✅")
            except Exception as e:
                st.error(f"Erreur : {e}")
    with col_info:
        st.info("💡 Planifier le ménage le vendredi soir ou samedi matin = environnement propre pour réviser le dimanche.")
