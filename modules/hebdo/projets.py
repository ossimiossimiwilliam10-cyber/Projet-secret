"""Onglet **Projets & Tâches** de la saisie hebdomadaire.

Liste les travaux ponctuels, projets personnels ou tâches administratives
à intégrer dans le planning, avec gestion des priorités.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from database import SaisieHebdo, get_session, session_scope
from utils.helpers import get_or_create_week_for_offset

PRIORITES = ["Basse", "Moyenne", "Haute"]
TYPES_PROJET = ["Scolaire (Devoir/Projet)", "Personnel", "Administratif", "Autre"]

_TYPE_ORIGINAL_TO_PROJET: dict[str, str] = {
    "etude": "Scolaire (Devoir/Projet)", "projet": "Scolaire (Devoir/Projet)",
    "intendance": "Administratif", "dev_perso": "Personnel",
    "social": "Personnel", "sport": "Personnel",
}


def _normaliser_item(item: dict) -> dict:
    titre = (item.get("titre") or item.get("libelle") or "").strip()
    if "type_original" in item and "type" not in item:
        type_label = _TYPE_ORIGINAL_TO_PROJET.get(str(item.get("type_original") or "").lower(), TYPES_PROJET[0])
    else:
        type_label = item.get("type") or TYPES_PROJET[0]
        if type_label not in TYPES_PROJET:
            type_label = TYPES_PROJET[0]
    priorite = item.get("priorite") or "Moyenne"
    if priorite not in PRIORITES:
        priorite = "Moyenne"
    echeance = item.get("echeance") or ("🔁 Reportée" if item.get("reportee_depuis_semaine_id") else "Peu importe")
    return {"titre": titre, "type": type_label, "duree_min": int(item.get("duree_min") or 60), "priorite": priorite, "echeance": str(echeance)}


def _get_saisie_for_offset(session: Session, offset: int) -> SaisieHebdo | None:
    _, saisie, _ = get_or_create_week_for_offset(session, offset_weeks=offset)
    return saisie


def _get_prev_projets(session, offset: int) -> list[dict]:
    try:
        _, ps, _ = get_or_create_week_for_offset(session, offset_weeks=offset - 1)
        return ps.projets_config or []
    except Exception:
        return []


def render() -> None:
    st.subheader("🎯 Projets & Tâches Ponctuelles")
    st.caption("L'IA placera tes tâches prioritaires dans tes meilleurs créneaux de concentration.")

    offset_courant = int(st.session_state.get("semaine_target_offset", 0))

    with get_session() as session:
        saisie = _get_saisie_for_offset(session, offset_courant)
        if not saisie:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine.")
            return

        config_db = saisie.projets_config or []
        items_normalises = [_normaliser_item(it) for it in config_db if isinstance(it, dict)]

    # Reprendre S-1
    col_prev, _ = st.columns([1, 3])
    with col_prev:
        if st.button("📋 Reprendre mes projets de la semaine dernière", width="stretch"):
            prev = _get_prev_projets(session, offset_courant)
            if prev:
                # Ne conserver que les projets « récurrents » : ceux déjà
                # reportés d'une semaine antérieure (reportee_depuis_semaine_id).
                # Les projets ponctuels (ex: "Rendre le dossier de Droit")
                # sont probablement terminés et ne doivent pas être recopiés.
                prev_filtre = [
                    p for p in prev
                    if isinstance(p, dict) and p.get("reportee_depuis_semaine_id")
                ]
                if prev_filtre:
                    with session_scope() as ws:
                        s = ws.get(SaisieHebdo, saisie.id)
                        s.projets_config = prev_filtre
                    nb_ignores = len(prev) - len(prev_filtre)
                    st.toast(
                        f"{len(prev_filtre)} projet(s) repris"
                        + (f", {nb_ignores} ponctuel(s) ignoré(s)" if nb_ignores else ""),
                        icon="📋",
                    )
                else:
                    st.toast(
                        f"Aucun projet récurrent la semaine dernière "
                        f"({len(prev)} projet(s) ponctuel(s) ignoré(s)).",
                        icon="ℹ️",
                    )
                st.session_state.pop(f"projets_config_{saisie.id}", None)
                st.rerun()
            else:
                st.toast("Aucun projet la semaine dernière.", icon="ℹ️")

    st.subheader("Liste de tes projets de la semaine")

    state_key = f"projets_config_{saisie.id}"
    if state_key not in st.session_state:
        st.session_state[state_key] = [dict(p) for p in items_normalises]

    projets_actuels = st.session_state[state_key]

    if not projets_actuels:
        st.info("Aucun projet ou tâche prévu cette semaine.")
    else:
        for idx, p in enumerate(projets_actuels):
            # Icônes selon priorité et type
            prio = p.get("priorite", "Moyenne")
            prio_icon = "🚨" if prio == "Haute" else "🔵" if prio == "Moyenne" else "🟢"
            
            type_p = p.get("type", TYPES_PROJET[0])
            type_icon = "🎓" if "Scolaire" in type_p else "👤" if "Personnel" in type_p else "📁" if "Administratif" in type_p else "🎯"

            with st.container(border=True):
                col_icon, col_details, col_del = st.columns([1, 8, 1])
                with col_icon:
                    st.markdown(f"<h2 style='text-align:center;'>{type_icon}</h2>", unsafe_allow_html=True)
                with col_details:
                    titre = p.get("titre", "Tâche")
                    st.markdown(f"**{titre}**")
                    st.caption(f"⏱️ {p.get('duree_min', 60)} min | {prio_icon} Priorité {prio} | 📅 Échéance : {p.get('echeance', 'Peu importe')}")
                with col_del:
                    if st.button("❌", key=f"del_pj_{saisie.id}_{idx}", help="Supprimer la tâche"):
                        projets_actuels.pop(idx)
                        st.rerun()

    with st.expander("➕ **Ajouter un projet / tâche**", expanded=len(projets_actuels) == 0):
        with st.form(f"form_add_pj_{saisie.id}"):
            c1, c2 = st.columns(2)
            with c1:
                new_titre = st.text_input("Nom de la tâche / Projet")
                new_type = st.selectbox("Catégorie", options=TYPES_PROJET, index=0)
                new_duree = st.number_input("Durée totale (min)", min_value=15, step=15, value=60)
            with c2:
                new_priorite = st.selectbox("Priorité", options=PRIORITES, index=1)
                new_echeance = st.text_input("Échéance", placeholder="ex: Avant Mercredi")
                
            if st.form_submit_button("✓ Ajouter", type="primary", use_container_width=True):
                if not new_titre.strip():
                    st.error("Le nom de la tâche est requis.")
                else:
                    projets_actuels.append({
                        "titre": new_titre.strip(),
                        "type": new_type,
                        "duree_min": int(new_duree),
                        "priorite": new_priorite,
                        "echeance": new_echeance.strip() if new_echeance.strip() else "Peu importe",
                    })
                    st.rerun()

    st.divider()
    col_save, col_info = st.columns([1, 2])
    with col_save:
        if st.button("💾 Enregistrer mes projets", type="primary", width='stretch'):
            projets_propre = projets_actuels
            try:
                with session_scope() as ws:
                    s = ws.get(SaisieHebdo, saisie.id)
                    s.projets_config = projets_propre
                st.toast("Projets sauvegardés", icon="✅")
            except Exception as e:  # noqa: BLE001
                import logging
                logging.getLogger("hebdo").exception("sauvegarde projets")
                st.error(f"Erreur lors de la sauvegarde : {e}")
    with col_info:
        st.info("💡 Les tâches 'Haute Priorité' rapportent un bonus d'XP lors de leur validation !")
