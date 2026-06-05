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
                st.toast("Loisirs repris !", icon="📋")
                st.rerun()
            else:
                st.toast("Aucun loisir la semaine dernière.", icon="ℹ️")

    st.subheader("Tes rendez-vous et moments de détente")

    # Mini-grille quotidienne
    if config_db:
        jours_abbr = ["Lu", "Ma", "Me", "Je", "Ve", "Sa", "Di"]
        by_jour = {j: [] for j in JOURS[:7]}
        for s in config_db:
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
                    st.caption(f"{type_icon} {s.get('duree_min', 0)}min")
                if len(sessions) > 2:
                    st.caption(f"└ +{len(sessions) - 2} autre(s)")

    edited_social = st.data_editor(
        df_social, num_rows="dynamic", width='stretch',
        column_config={
            "activite": st.column_config.TextColumn("Événement / Détail"),
            "type": st.column_config.SelectboxColumn("Type d'activité", options=TYPES_SOCIAL, default=TYPES_SOCIAL[1]),
            "duree_min": st.column_config.NumberColumn("Durée (min)", min_value=15, step=15, default=120),
            "jour_pref": st.column_config.SelectboxColumn("Jour", options=JOURS, default="vendredi"),
            "creneau_pref": st.column_config.SelectboxColumn("Moment", options=["Midi", "Après-midi", "Soir", "Nuit"], default="Soir"),
        },
        key="social_editor_v3",
    )

    st.divider()
    col_save, col_info = st.columns([1, 2])
    with col_save:
        if st.button("💾 Enregistrer mes loisirs", type="primary", width='stretch'):
            social_propre = []
            for _, row in edited_social.iterrows():
                if pd.notna(row.get("type")):
                    social_propre.append({
                        "activite": str(row.get("activite", "Détente")).strip(),
                        "type": str(row["type"]),
                        "duree_min": int(row.get("duree_min", 60)),
                        "jour_pref": str(row.get("jour_pref", "peu importe")),
                        "creneau_pref": str(row.get("creneau_pref", "Soir")),
                    })
            try:
                with session_scope() as ws:
                    s = ws.get(SaisieHebdo, saisie.id)
                    s.social_config = social_propre
                st.success("✅ Loisirs enregistrés !")
                st.toast("Social sauvegardé", icon="✅")
                st.rerun()
            except Exception as e:
                st.error(f"Erreur : {e}")
    with col_info:
        st.caption("💡 Si tu prévois une 'Sortie / Fête', l'IA placera ton lever plus tard le lendemain et évitera les révisions denses le matin.")
