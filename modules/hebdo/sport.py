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
    st.subheader("🥊 Sport & Entraînement")
    st.caption(
        "Planifie tes séances physiques. "
        "L'IA évitera de placer des révisions denses après une séance intense."
    )

    offset_courant = int(st.session_state.get("semaine_target_offset", 0))

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
                st.session_state.pop(f"sport_config_{saisie_id}", None)
                st.toast("Séances reprises !", icon="📋")
                st.rerun()
            else:
                st.toast("Aucune séance la semaine dernière.", icon="ℹ️")

    st.divider()

    # --- Éditeur de séances ---
    st.subheader("Séances prévues")

    state_key = f"sport_config_{saisie_id}"
    if state_key not in st.session_state:
        st.session_state[state_key] = [dict(s) for s in sport_config_db]

    seances_actuelles = st.session_state[state_key]

    # Affichage en cartes
    if not seances_actuelles:
        st.info("Aucune séance de sport prévue pour le moment.")
    else:
        for idx, seance in enumerate(seances_actuelles):
            # Extraction des icônes pour le design
            type_icon = seance.get("type", "🎯").split(" ")[0] if seance.get("type") else "🎯"
            intensite_icon = (
                "🔴" if "🔴" in seance.get("intensite", "")
                else "🟡" if "🟡" in seance.get("intensite", "")
                else "🟢" if "🟢" in seance.get("intensite", "")
                else "🔵"
            )

            with st.container(border=True):
                col_icon, col_details, col_del = st.columns([1, 8, 1])
                with col_icon:
                    st.markdown(f"<h2 style='text-align:center;'>{type_icon}</h2>", unsafe_allow_html=True)
                with col_details:
                    titre = seance.get("nom") or seance.get("type", "Séance")
                    st.markdown(f"**{titre}**")
                    st.caption(f"⏱️ {seance.get('duree_min', 60)} min | {intensite_icon} {seance.get('intensite', '')} | 📅 Créneau : {seance.get('creneau_pref', 'Peu importe')}")
                with col_del:
                    if st.button("❌", key=f"del_sp_{saisie_id}_{idx}", help="Supprimer la séance"):
                        seances_actuelles.pop(idx)
                        st.rerun()

    # Formulaire d'ajout
    with st.expander("➕ **Ajouter une séance**", expanded=len(seances_actuelles) == 0):
        with st.form(f"form_add_sp_{saisie_id}"):
            c1, c2 = st.columns(2)
            with c1:
                new_type = st.selectbox("Discipline", options=list(TYPES_SPORT.keys()))
                new_nom = st.text_input("Détails (ex: Séance Jambes)")
                new_duree = st.number_input("Durée (min)", min_value=15, step=15, value=60)
            with c2:
                new_intensite = st.selectbox("Intensité", options=INTENSITES, index=1)
                new_creneau = st.selectbox("Créneau préféré", options=CRENEAUX, index=0)
                
            if st.form_submit_button("✓ Ajouter", type="primary", use_container_width=True):
                seances_actuelles.append({
                    "type": new_type,
                    "nom": new_nom.strip(),
                    "duree_min": int(new_duree),
                    "intensite": new_intensite,
                    "creneau_pref": new_creneau,
                })
                st.rerun()

    st.divider()

    col_save, col_info = st.columns([1, 2])
    with col_save:
        if st.button("💾 Enregistrer mes séances", type="primary", width='stretch'):
            sport_propre = seances_actuelles
            try:
                with session_scope() as write_session:
                    s = write_session.get(SaisieHebdo, saisie_id)
                    s.sport_config = sport_propre
                st.toast("Sport sauvegardé", icon="💾")
            except Exception as e:
                st.error(f"Erreur : {e}")

    with col_info:
        st.caption(
            "💡 L'intensité influence la récupération : "
            "une séance 🔴 bloque les révisions théoriques intenses pendant 2h après le sport."
        )
