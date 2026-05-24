"""Onglet **Sport** de la saisie hebdomadaire.

Planifie tes séances d'entraînement (discipline, durée, intensité, créneau).
Désormais synchronisé avec le sélecteur de semaine partagé.
"""

from __future__ import annotations

import datetime
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from database import SaisieHebdo, Semaine, get_session, session_scope
from utils.helpers import get_or_create_week_for_offset

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
INTENSITES = [
    "🟢 Légère (Récupération active)",
    "🟡 Modérée (Entraînement classique)",
    "🔴 Intense (Sparring / Max PR)",
]

TYPES_SPORT = {
    "🏃‍♂️ Course / Cardio": "Cardio",
    "🏋️‍♀️ Musculation / Force": "Force",
    "🥊 Boxe / Combat": "Combat",
    "🧘‍♂️ Yoga / Mobilité": "Récupération",
    "🏊‍♂️ Natation": "Complet",
    "🚴‍♂️ Cyclisme": "Cardio",
    "🎾 Sport de raquette": "Mixte",
    "⚽ Sport collectif": "Mixte",
    "🤸‍♂️ Gymnastique": "Force/Souplesse",
    "🎯 Autre": "Autre",
}

CRENEAUX = ["Peu importe", "Matin", "Midi", "Après-midi", "Soir"]
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_saisie_for_offset(session: Session, offset: int) -> SaisieHebdo | None:
    """Récupère la saisie pour l'offset de semaine donné (partagé avec Études)."""
    _, saisie, _ = get_or_create_week_for_offset(session, offset_weeks=offset)
    return saisie


def _get_previous_week_sport(session, current_offset: int) -> list[dict[str, Any]]:
    """Récupère le sport_config de la semaine précédente."""
    try:
        _, prev_saisie, _ = get_or_create_week_for_offset(session, offset_weeks=current_offset - 1)
        return prev_saisie.sport_config or []
    except Exception:
        return []


def _compute_stats(sport_config: list[dict[str, Any]]) -> dict:
    """Calcule les stats de la semaine sportive."""
    if not sport_config:
        return {"total_min": 0, "nb_intense": 0, "nb_moderee": 0, "nb_legere": 0, "nb_seances": 0}

    total_min = sum(int(s.get("duree_min", 0)) for s in sport_config)
    nb_intense = sum(1 for s in sport_config if "🔴" in s.get("intensite", ""))
    nb_moderee = sum(1 for s in sport_config if "🟡" in s.get("intensite", ""))
    nb_legere = sum(1 for s in sport_config if "🟢" in s.get("intensite", ""))
    return {
        "total_min": total_min,
        "nb_intense": nb_intense,
        "nb_moderee": nb_moderee,
        "nb_legere": nb_legere,
        "nb_seances": len(sport_config),
    }


def _render_grille_hebdo(sport_config: list[dict[str, Any]]) -> None:
    """Affiche une mini-grille par créneau (Matin / Midi / Après-midi / Soir).

    L'utilisateur choisit un créneau préféré, pas un jour précis.
    L'IA positionnera les séances lors de la génération du planning.
    """
    if not sport_config:
        st.caption("Aucune séance prévue cette semaine.")
        return

    # Regroupement par créneau préféré
    by_creneau: dict[str, list[dict]] = {}
    for s in sport_config:
        creneau = s.get("creneau_pref", "Peu importe")
        by_creneau.setdefault(creneau, []).append(s)

    creneaux_ordre = ["Matin", "Midi", "Après-midi", "Soir"]
    cols = st.columns(len(creneaux_ordre))

    for col, creneau in zip(cols, creneaux_ordre):
        with col:
            st.markdown(f"**{creneau}**")
            sessions = by_creneau.get(creneau, [])
            # Inclure aussi les "Peu importe" dans chaque créneau (car non spécifique)
            sessions += by_creneau.get("Peu importe", [])

            if sessions:
                seen = set()
                for s in sessions[:3]:  # max 3 par créneau
                    key = s.get("type", "")
                    if key not in seen:
                        seen.add(key)
                        duree = s.get("duree_min", 60)
                        intensite_icon = (
                            "🔴" if "🔴" in s.get("intensite", "")
                            else "🟡" if "🟡" in s.get("intensite", "")
                            else "🟢"
                        )
                        type_icon = s.get("type", "🎯").split(" ")[0] if s.get("type") else "🎯"
                        st.caption(f"{type_icon} {intensite_icon} {duree//60}h{duree%60:02d}")
            else:
                st.caption("—")


# ---------------------------------------------------------------------------
# Rendu UI
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("🥊 Sport & Entraînement")
    st.caption(
        "Planifie tes séances physiques. "
        "L'IA évitera de placer des révisions denses après une séance intense."
    )

    # --- Sélecteur de semaine (synchronisé avec Études) ---
    offset_courant = int(st.session_state.get("semaine_target_offset", 0))
    options = {0: "📅 Cette semaine", 1: "📆 Semaine prochaine"}
    nouveau_offset = st.radio(
        "Semaine à préparer",
        options=list(options.keys()),
        format_func=lambda k: options[k],
        index=list(options.keys()).index(offset_courant) if offset_courant in options else 0,
        horizontal=True,
        key="sport_semaine_target",
    )
    if nouveau_offset != offset_courant:
        st.session_state["semaine_target_offset"] = nouveau_offset
        st.rerun()

    with get_session() as session:
        saisie = _get_saisie_for_offset(session, offset_courant)
        if not saisie:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine.")
            return

        sport_config_db: list[dict[str, Any]] = saisie.sport_config or []
        saisie_id = saisie.id

        stats = _compute_stats(sport_config_db)

    # --- Résumé + Grille ---
    st.subheader("📊 Résumé de la semaine")

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("⏱️ Volume", f"{stats['total_min'] // 60}h{stats['total_min'] % 60:02d}")
    col2.metric("🔴 Intenses", stats["nb_intense"])
    col3.metric("🟡 Modérées", stats["nb_moderee"])
    col4.metric("🟢 Légères", stats["nb_legere"])
    col5.metric("📅 Séances", stats["nb_seances"])

    # Alerte récupération
    if stats["nb_intense"] >= 3:
        st.warning(
            f"⚠️ **{stats['nb_intense']} séances intenses** cette semaine. "
            "L'IA évitera les révisions denses dans les 2h suivant ces créneaux. "
            "Pense à bien dormir (≥ 8h) pour la récupération."
        )
    elif stats["nb_seances"] == 0:
        st.info("💡 Aucune séance prévue. Même une séance légère aide à la concentration.")
    elif stats["nb_seances"] >= 1:
        st.success(
            f"✅ {stats['nb_seances']} séance(s) prévue(s) — "
            f"{'bon équilibre avec ' + str(stats['nb_legere']) + ' récup(s)' if stats['nb_legere'] > 0 else 'pense à inclure une récupération active.'}"
        )

    # Grille par créneau
    st.caption("**📅 Aperçu par créneau** (indicatif — l'IA positionnera précisément) :")
    _render_grille_hebdo(sport_config_db)

    # --- Bouton reprendre semaine précédente ---
    col_prev, _ = st.columns([1, 3])
    with col_prev:
        if st.button("📋 Reprendre mes séances de la semaine dernière", width="stretch"):
            prev_config = _get_previous_week_sport(session, offset_courant)
            if prev_config:
                with session_scope() as ws:
                    s = ws.get(SaisieHebdo, saisie_id)
                    s.sport_config = prev_config
                st.toast("Séances reprises !", icon="📋")
                st.rerun()
            else:
                st.toast("Aucune séance la semaine dernière.", icon="ℹ️")

    st.divider()

    # --- Éditeur de séances ---
    st.subheader("Séances prévues")

    df_sport = pd.DataFrame(sport_config_db)
    if df_sport.empty:
        df_sport = pd.DataFrame([{
            "type": "🏃‍♂️ Course / Cardio",
            "nom": "",
            "duree_min": 60,
            "intensite": INTENSITES[1],
            "creneau_pref": "Soir",
        }])
    else:
        for col_name in ["type", "nom", "duree_min", "intensite", "creneau_pref"]:
            if col_name not in df_sport.columns:
                df_sport[col_name] = "" if col_name in ("type", "nom", "intensite", "creneau_pref") else 60

    cols = ["type", "nom", "duree_min", "intensite", "creneau_pref"]
    df_sport = df_sport[cols]

    edited_sport = st.data_editor(
        df_sport,
        num_rows="dynamic",
        width='stretch',
        column_config={
            "type": st.column_config.SelectboxColumn(
                "Discipline", options=list(TYPES_SPORT.keys()), required=True,
            ),
            "nom": st.column_config.TextColumn("Détails (ex: Séance Jambes)"),
            "duree_min": st.column_config.NumberColumn(
                "Durée (min)", min_value=15, step=15, default=60,
            ),
            "intensite": st.column_config.SelectboxColumn(
                "Intensité", options=INTENSITES, default=INTENSITES[1],
            ),
            "creneau_pref": st.column_config.SelectboxColumn(
                "Créneau préféré", options=CRENEAUX, default="Peu importe",
            ),
        },
        key=f"sport_editor_{saisie_id}",
    )

    st.divider()

    col_save, col_info = st.columns([1, 2])
    with col_save:
        if st.button("💾 Enregistrer mes séances", type="primary", width='stretch'):
            sport_propre = []
            for _, row in edited_sport.iterrows():
                if pd.notna(row.get("type")):
                    sport_propre.append({
                        "type": str(row["type"]),
                        "nom": str(row.get("nom", "")).strip(),
                        "duree_min": int(row.get("duree_min", 60)),
                        "intensite": str(row.get("intensite", INTENSITES[1])),
                        "creneau_pref": str(row.get("creneau_pref", "Peu importe")),
                    })
            try:
                with session_scope() as write_session:
                    s = write_session.get(SaisieHebdo, saisie_id)
                    s.sport_config = sport_propre
                st.success("✅ Séances enregistrées !")
                st.toast("Sport sauvegardé", icon="💾")
                st.rerun()
            except Exception as e:
                st.error(f"Erreur : {e}")

    with col_info:
        st.caption(
            "💡 L'intensité influence la récupération : "
            "une séance 🔴 bloque les révisions théoriques intenses pendant 2h après le sport."
        )
