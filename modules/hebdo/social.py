"""Onglet **Social & Loisirs** de la saisie hebdomadaire.

Planifie tes moments de détente, sorties entre amis ou temps de repos.
L'IA adapte le planning du lendemain selon l'intensité des sorties.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from database import SaisieHebdo, get_session, session_scope
from utils.helpers import get_or_create_week_for_offset

TYPES_SOCIAL = [
    "🍹 Sortie / Fête (Grosse fatigue)", "☕ Café / Rencontre courte",
    "🎮 Jeu Vidéo / Ciné", "🛋️ Repos / Ressourcement", "📞 Appel / Famille",
]
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche", "peu importe"]


def _get_saisie_for_offset(session: Session, offset: int) -> SaisieHebdo | None:
    _, saisie, _ = get_or_create_week_for_offset(session, offset_weeks=offset)
    return saisie


def _get_prev_social(session, offset: int) -> list[dict]:
    try:
        _, ps, _ = get_or_create_week_for_offset(session, offset_weeks=offset - 1)
        return ps.social_config or []
    except Exception:
        return []


def render() -> None:
    st.subheader("🍹 Social & Loisirs")
    st.caption("Équilibre ta vie d'étudiant. L'IA protègera tes temps de récupération.")

    offset_courant = int(st.session_state.get("semaine_target_offset", 0))

    with get_session() as session:
        saisie = _get_saisie_for_offset(session, offset_courant)
        if not saisie:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine.")
            return

        config_db = saisie.social_config or []
        df_social = pd.DataFrame(config_db)
        if df_social.empty:
            df_social = pd.DataFrame([{"activite": "", "type": TYPES_SOCIAL[1], "duree_min": 60, "jour_pref": "vendredi", "creneau_pref": "Soir"}])

    # Reprendre S-1
    col_prev, _ = st.columns([1, 3])
    with col_prev:
        if st.button("📋 Reprendre mes loisirs de la semaine dernière", width="stretch"):
            prev = _get_prev_social(session, offset_courant)
            if prev:
                with session_scope() as ws:
                    s = ws.get(SaisieHebdo, saisie.id)
                    s.social_config = prev
                st.session_state.pop(f"social_config_{saisie.id}", None)
                st.toast("Loisirs repris !", icon="📋")
                st.rerun()
            else:
                st.toast("Aucun loisir la semaine dernière.", icon="ℹ️")

    st.subheader("Tes rendez-vous et moments de détente")

    state_key = f"social_config_{saisie.id}"
    if state_key not in st.session_state:
        st.session_state[state_key] = [dict(d) for d in config_db]

    loisirs_actuels = st.session_state[state_key]

    # Mini-grille quotidienne
    if loisirs_actuels:
        jours_abbr = ["Lu", "Ma", "Me", "Je", "Ve", "Sa", "Di"]
        by_jour = {j: [] for j in JOURS[:7]}
        for s in loisirs_actuels:
            jp = s.get("jour_pref", "peu importe")
            if jp in by_jour:
                by_jour[jp].append(s)
            elif jp == "peu importe":
                pass  # ne peut pas être positionné
        cols = st.columns(7)
        for i, (col, jour) in enumerate(zip(cols, jours_abbr)):
            with col:
                st.markdown(f"**{jour}**")
                sessions = by_jour.get(JOURS[i], [])
                for s in sessions[:2]:
                    type_icon = s.get("type", "").split(" ")[0] if s.get("type") else "🍹"
                    st.caption(f"{type_icon} {s.get('duree_min', 0)}m")
                if len(sessions) > 2:
                    st.caption(f"└ +{len(sessions) - 2}")

    st.divider()

    if not loisirs_actuels:
        st.info("Aucun loisir prévu pour le moment.")
    else:
        for idx, loisir in enumerate(loisirs_actuels):
            type_full = loisir.get("type", TYPES_SOCIAL[1])
            type_icon = type_full.split(" ")[0] if type_full else "🍹"

            with st.container(border=True):
                col_icon, col_details, col_del = st.columns([1, 8, 1])
                with col_icon:
                    st.markdown(f"<h2 style='text-align:center;'>{type_icon}</h2>", unsafe_allow_html=True)
                with col_details:
                    activite = loisir.get("activite") or type_full.replace(type_icon, "").strip() or "Détente"
                    st.markdown(f"**{activite}**")
                    jour = str(loisir.get('jour_pref', 'peu importe')).capitalize()
                    st.caption(f"⏱️ {loisir.get('duree_min', 60)} min | 📅 {jour} ({loisir.get('creneau_pref', 'Soir')})")
                with col_del:
                    if st.button("❌", key=f"del_soc_{saisie.id}_{idx}", help="Supprimer cette activité"):
                        loisirs_actuels.pop(idx)
                        st.rerun()

    # Formulaire d'ajout
    with st.expander("➕ **Ajouter une sortie / un loisir**", expanded=len(loisirs_actuels) == 0):
        with st.form(f"form_add_soc_{saisie.id}"):
            c1, c2 = st.columns(2)
            with c1:
                new_activite = st.text_input("Événement / Détail (ex: Soirée Alice)")
                new_type = st.selectbox("Type d'activité", options=TYPES_SOCIAL, index=1)
                new_duree = st.number_input("Durée (min)", min_value=15, step=15, value=120)
            with c2:
                new_jour = st.selectbox("Jour", options=JOURS, index=4) # vendredi par defaut
                new_creneau = st.selectbox("Moment", options=["Midi", "Après-midi", "Soir", "Nuit"], index=2) # Soir par defaut
                
            if st.form_submit_button("✓ Ajouter", type="primary", use_container_width=True):
                loisirs_actuels.append({
                    "activite": new_activite.strip() if new_activite.strip() else new_type.split(" ", 1)[-1].strip(),
                    "type": new_type,
                    "duree_min": int(new_duree),
                    "jour_pref": new_jour,
                    "creneau_pref": new_creneau,
                })
                st.rerun()

    st.divider()
    col_save, col_info = st.columns([1, 2])
    with col_save:
        if st.button("💾 Enregistrer mes loisirs", type="primary", width='stretch'):
            social_propre = loisirs_actuels
            try:
                with session_scope() as ws:
                    s = ws.get(SaisieHebdo, saisie.id)
                    s.social_config = social_propre
                st.toast("Social sauvegardé", icon="✅")
            except Exception as e:
                st.error(f"Erreur : {e}")
    with col_info:
        st.caption("💡 Si tu prévois une 'Sortie / Fête', l'IA placera ton lever plus tard le lendemain et évitera les révisions denses le matin.")
