"""Onglet **Projets & Tâches** de la saisie hebdomadaire.

Permet de lister les travaux ponctuels, projets personnels ou tâches administratives
à intégrer dans le planning, avec gestion des priorités.
"""

from __future__ import annotations

import datetime
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from database.db import get_session, session_scope
from database.models import SaisieHebdo, Semaine

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
PRIORITES = ["Basse", "Moyenne", "Haute"]
TYPES_PROJET = ["Scolaire (Devoir/Projet)", "Personnel", "Administratif", "Autre"]

# Mapping des types techniques venant du report_service (tâches reportées
# de la semaine précédente) vers les libellés affichables de TYPES_PROJET.
_TYPE_ORIGINAL_TO_PROJET: dict[str, str] = {
    "etude":      "Scolaire (Devoir/Projet)",
    "projet":     "Scolaire (Devoir/Projet)",
    "intendance": "Administratif",
    "dev_perso":  "Personnel",
    "social":     "Personnel",
    "sport":      "Personnel",
}


def _normaliser_item(item: dict) -> dict:
    """Normalise un item de ``projets_config`` au schéma attendu par le
    data_editor : ``{titre, type, duree_min, priorite, echeance}``.

    Tolère les items reportés par ``services.report_service`` qui ont une
    structure différente (clés ``type_original``,
    ``reportee_depuis_semaine_id``, etc.). Sans cette normalisation, le
    DataFrame mélange des colonnes incompatibles avec la column_config et
    le SelectboxColumn affiche du vide pour les valeurs hors enum.
    """
    titre = (item.get("titre") or item.get("libelle") or "").strip()

    # Si c'est un item reporté, on dérive le type depuis type_original.
    if "type_original" in item and "type" not in item:
        type_label = _TYPE_ORIGINAL_TO_PROJET.get(
            str(item.get("type_original") or "").lower(),
            TYPES_PROJET[0],
        )
    else:
        type_label = item.get("type") or TYPES_PROJET[0]
        if type_label not in TYPES_PROJET:
            type_label = TYPES_PROJET[0]

    priorite = item.get("priorite") or "Moyenne"
    if priorite not in PRIORITES:
        priorite = "Moyenne"

    # Si l'item provient d'un report, on l'indique dans l'échéance.
    if item.get("reportee_depuis_semaine_id"):
        echeance = item.get("echeance") or "🔁 Reportée de la semaine précédente"
    else:
        echeance = item.get("echeance") or "Peu importe"

    return {
        "titre": titre,
        "type": type_label,
        "duree_min": int(item.get("duree_min") or 60),
        "priorite": priorite,
        "echeance": str(echeance),
    }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_current_saisie(session: Session) -> SaisieHebdo | None:
    today = datetime.date.today()
    iso_year, iso_week, _ = today.isocalendar()
    semaine = session.query(Semaine).filter_by(annee=iso_year, numero_semaine=iso_week).first()
    if semaine:
        return session.query(SaisieHebdo).filter_by(semaine_id=semaine.id).first()
    return None

# ---------------------------------------------------------------------------
# Rendu UI
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("🎯 Projets & Tâches Ponctuelles")
    st.caption("L'IA placera tes tâches prioritaires dans tes meilleurs créneaux de concentration.")

    with get_session() as session:
        saisie = _get_current_saisie(session)
        
        if not saisie:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine en cours.")
            return

        # Récupération de la configuration existante + normalisation
        # tolérante (items hétérogènes : natifs vs reportés du report_service).
        config_db = saisie.projets_config or []
        items_normalises = [_normaliser_item(it) for it in config_db if isinstance(it, dict)]

        if items_normalises:
            df_projets = pd.DataFrame(items_normalises)
        else:
            df_projets = pd.DataFrame([{
                "titre": "",
                "type": TYPES_PROJET[0],
                "duree_min": 60,
                "priorite": "Moyenne",
                "echeance": "Peu importe",
            }])

        # Ne garder que les colonnes attendues, dans l'ordre attendu.
        # (Sécurité contre les clés résiduelles si _normaliser_item a un trou.)
        df_projets = df_projets.reindex(
            columns=["titre", "type", "duree_min", "priorite", "echeance"]
        )

        st.subheader("Liste de tes projets de la semaine")
        edited_projets = st.data_editor(
            df_projets,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "titre": st.column_config.TextColumn("Nom de la tâche / Projet", required=True),
                "type": st.column_config.SelectboxColumn("Catégorie", options=TYPES_PROJET, default=TYPES_PROJET[0]),
                "duree_min": st.column_config.NumberColumn("Durée totale (min)", min_value=15, step=15, default=60),
                "priorite": st.column_config.SelectboxColumn("Priorité", options=PRIORITES, default="Moyenne"),
                "echeance": st.column_config.TextColumn("Échéance (ex: Avant Mercredi)", required=False)
            },
            key="projets_editor_v2"
        )

        st.divider()
        
        col_save, col_info = st.columns([1, 2])
        with col_save:
            if st.button("💾 Enregistrer mes projets", type="primary", use_container_width=True):
                projets_propre = []
                for _, row in edited_projets.iterrows():
                    if pd.notna(row.get("titre")) and str(row.get("titre")).strip() != "":
                        projets_propre.append({
                            "titre": str(row["titre"]).strip(),
                            "type": str(row.get("type", TYPES_PROJET[0])),
                            "duree_min": int(row.get("duree_min", 60)),
                            "priorite": str(row.get("priorite", "Moyenne")),
                            "echeance": str(row.get("echeance", "Peu importe"))
                        })

                try:
                    with session_scope() as write_session:
                        saisie_to_update = write_session.get(SaisieHebdo, saisie.id)
                        saisie_to_update.projets_config = projets_propre
                    st.success("✅ Projets enregistrés !")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur lors de la sauvegarde : {e}")
        
        with col_info:
            st.info("💡 **Astuce** : Les tâches 'Haute Priorité' rapportent un bonus d'XP lors de leur validation !")
