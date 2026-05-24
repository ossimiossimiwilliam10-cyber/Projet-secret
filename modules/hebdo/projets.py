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
    st.title("🎯 Projets & Tâches Ponctuelles")
    st.caption("L'IA placera tes tâches prioritaires dans tes meilleurs créneaux de concentration.")

    offset_courant = int(st.session_state.get("semaine_target_offset", 0))
    options = {0: "📅 Cette semaine", 1: "📆 Semaine prochaine"}
    nouveau_offset = st.radio(
        "Semaine à préparer", options=list(options.keys()),
        format_func=lambda k: options[k],
        index=list(options.keys()).index(offset_courant) if offset_courant in options else 0,
        horizontal=True, key="projets_semaine_target",
    )
    if nouveau_offset != offset_courant:
        st.session_state["semaine_target_offset"] = nouveau_offset
        st.rerun()

    with get_session() as session:
        saisie = _get_saisie_for_offset(session, offset_courant)
        if not saisie:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine.")
            return

        config_db = saisie.projets_config or []
        items_normalises = [_normaliser_item(it) for it in config_db if isinstance(it, dict)]

        if items_normalises:
            df_projets = pd.DataFrame(items_normalises)
        else:
            df_projets = pd.DataFrame([{"titre": "", "type": TYPES_PROJET[0], "duree_min": 60, "priorite": "Moyenne", "echeance": "Peu importe"}])

        df_projets = df_projets.reindex(columns=["titre", "type", "duree_min", "priorite", "echeance"])

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
                st.rerun()
            else:
                st.toast("Aucun projet la semaine dernière.", icon="ℹ️")

    st.subheader("Liste de tes projets de la semaine")
    edited_projets = st.data_editor(
        df_projets, num_rows="dynamic", width='stretch',
        column_config={
            "titre": st.column_config.TextColumn("Nom de la tâche / Projet", required=True),
            "type": st.column_config.SelectboxColumn("Catégorie", options=TYPES_PROJET, default=TYPES_PROJET[0]),
            "duree_min": st.column_config.NumberColumn("Durée totale (min)", min_value=15, step=15, default=60),
            "priorite": st.column_config.SelectboxColumn("Priorité", options=PRIORITES, default="Moyenne"),
            "echeance": st.column_config.TextColumn("Échéance (ex: Avant Mercredi)"),
        },
        key="projets_editor_v3",
    )

    st.divider()
    col_save, col_info = st.columns([1, 2])
    with col_save:
        if st.button("💾 Enregistrer mes projets", type="primary", width='stretch'):
            projets_propre = []
            for _, row in edited_projets.iterrows():
                if pd.notna(row.get("titre")) and str(row.get("titre")).strip():
                    projets_propre.append({
                        "titre": str(row["titre"]).strip(),
                        "type": str(row.get("type", TYPES_PROJET[0])),
                        "duree_min": int(row.get("duree_min", 60)),
                        "priorite": str(row.get("priorite", "Moyenne")),
                        "echeance": str(row.get("echeance", "Peu importe")),
                    })
            try:
                with session_scope() as ws:
                    s = ws.get(SaisieHebdo, saisie.id)
                    s.projets_config = projets_propre
                st.success("✅ Projets enregistrés !")
                st.toast("Projets sauvegardés", icon="✅")
                st.rerun()
            except Exception as e:
                st.error(f"Erreur : {e}")
    with col_info:
        st.info("💡 Les tâches 'Haute Priorité' rapportent un bonus d'XP lors de leur validation !")
