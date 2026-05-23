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
    st.title("🧹 Intendance & Administratif")
    st.caption("Libère ton esprit des corvées en les planifiant dans tes moments de basse énergie.")

    offset_courant = int(st.session_state.get("semaine_target_offset", 0))
    options = {0: "📅 Cette semaine", 1: "📆 Semaine prochaine"}
    nouveau_offset = st.radio(
        "Semaine à préparer", options=list(options.keys()),
        format_func=lambda k: options[k],
        index=list(options.keys()).index(offset_courant) if offset_courant in options else 0,
        horizontal=True, key="intendance_semaine_target",
    )
    if nouveau_offset != offset_courant:
        st.session_state["semaine_target_offset"] = nouveau_offset
        st.rerun()

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
                st.toast("Corvées reprises !", icon="📋")
                st.rerun()
            else:
                st.toast("Aucune corvée la semaine dernière.", icon="ℹ️")

    st.subheader("Corvées et tâches administratives")

    if config_db:
        total_min = sum(int(c.get("duree_min", 0)) for c in config_db)
        st.caption(f"⏱️ **{len(config_db)} corvée(s) · ~{total_min // 60}h{total_min % 60:02d}** — bien organisé !")

    edited_int = st.data_editor(
        df_int, num_rows="dynamic", width='stretch',
        column_config={
            "activite": st.column_config.SelectboxColumn("Type de tâche", options=TYPES_INTENDANCE, required=True),
            "duree_min": st.column_config.NumberColumn("Durée (min)", min_value=15, step=15, default=30),
            "creneau_pref": st.column_config.SelectboxColumn("Moment", options=["Peu importe", "Matin", "Midi", "Après-midi", "Soir", "Week-end"], default="Peu importe"),
        },
        key="intendance_editor_v3",
    )

    st.divider()
    col_save, col_info = st.columns([1, 2])
    with col_save:
        if st.button("💾 Enregistrer corvées", type="primary", width='stretch'):
            int_propre = []
            for _, row in edited_int.iterrows():
                if pd.notna(row.get("activite")):
                    int_propre.append({
                        "activite": str(row["activite"]),
                        "duree_min": int(row.get("duree_min", 30)),
                        "creneau_pref": str(row.get("creneau_pref", "Peu importe")),
                    })
            try:
                with session_scope() as ws:
                    s = ws.get(SaisieHebdo, saisie.id)
                    s.intendance_config = int_propre
                st.success("✅ Intendance enregistrée !")
                st.toast("Intendance sauvegardée", icon="✅")
                st.rerun()
            except Exception as e:
                st.error(f"Erreur : {e}")
    with col_info:
        st.info("💡 Planifier le ménage le vendredi soir ou samedi matin = environnement propre pour réviser le dimanche.")
