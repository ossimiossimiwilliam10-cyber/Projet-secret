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
    st.title("🌱 Développement Personnel")
    st.caption("Investis en toi-même. L'IA utilisera ces blocs comme des respirations productives.")

    offset_courant = int(st.session_state.get("semaine_target_offset", 0))
    options = {0: "📅 Cette semaine", 1: "📆 Semaine prochaine"}
    nouveau_offset = st.radio(
        "Semaine à préparer", options=list(options.keys()),
        format_func=lambda k: options[k],
        index=list(options.keys()).index(offset_courant) if offset_courant in options else 0,
        horizontal=True, key="dev_semaine_target",
    )
    if nouveau_offset != offset_courant:
        st.session_state["semaine_target_offset"] = nouveau_offset
        st.rerun()

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
                st.toast("Habitudes reprises !", icon="📋")
                st.rerun()
            else:
                st.toast("Aucune habitude la semaine dernière.", icon="ℹ️")

    st.subheader("Tes habitudes de croissance")
    # KPI rapide — la fréquence étant un champ texte libre (ex: \"3x/semaine\"),
    # on ne peut pas calculer un volume total exact. On affiche le nombre
    # d'habitudes plutôt qu'un total trompeur.
    if config_db:
        st.caption(
            f"⏱️ **{len(config_db)} habitude(s) planifiée(s)** — "
            f"l'IA intégrera ces sessions de croissance à ton planning. 👏"
        )

    edited_dev = st.data_editor(
        df_dev, num_rows="dynamic", width='stretch',
        column_config={
            "activite": st.column_config.SelectboxColumn("Activité", options=CATEGORIES, required=True),
            "frequence": st.column_config.TextColumn("Objectif (ex: Tous les jours, 2x...)"),
            "duree_min": st.column_config.NumberColumn("Durée / session (min)", min_value=5, step=5, default=20),
            "creneau_pref": st.column_config.SelectboxColumn("Moment idéal", options=CRENEAUX, default="Matin"),
        },
        key="dev_perso_editor_v3",
    )

    st.divider()
    col_save, col_info = st.columns([1, 2])
    with col_save:
        if st.button("💾 Enregistrer mes habitudes", type="primary", width='stretch'):
            dev_propre = []
            for _, row in edited_dev.iterrows():
                if pd.notna(row.get("activite")):
                    dev_propre.append({
                        "activite": str(row["activite"]),
                        "frequence": str(row.get("frequence", "Toutes les fois")).strip(),
                        "duree_min": int(row.get("duree_min", 20)),
                        "creneau_pref": str(row.get("creneau_pref", "Peu importe")),
                    })
            try:
                with session_scope() as ws:
                    s = ws.get(SaisieHebdo, saisie.id)
                    s.dev_perso_config = dev_propre
                st.success("✅ Habitudes enregistrées !")
                st.toast("Dev perso sauvegardé", icon="✅")
                st.rerun()
            except Exception as e:
                st.error(f"Erreur : {e}")
    with col_info:
        st.info("💡 La lecture et la méditation améliorent ta concentration pour les blocs d'études suivants.")
