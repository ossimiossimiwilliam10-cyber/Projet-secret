"""Onglet **Projets & Tâches perso** de la saisie hebdomadaire.

Cette version distingue **deux blocs** :

- 🔁 **Tâches reportées de la semaine précédente** (badge orange) — pré-remplies
  automatiquement par ``services/report_service.py`` lors de la création de
  la SaisieHebdo. L'étudiant peut les modifier, les supprimer ou les laisser.
- ✏️ **Nouvelles tâches & projets** — saisie libre.

À la sauvegarde, les deux listes sont fusionnées dans ``projets_config``
en préservant les métadonnées de report (``reportee_depuis_semaine_id``,
``reportee_label``, ``type_original``).
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
PRIORITES = ["Basse", "Normale", "Haute"]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _get_current_saisie(session: Session) -> tuple[Semaine | None, SaisieHebdo | None]:
    today = datetime.date.today()
    iso_year, iso_week, _ = today.isocalendar()
    semaine = session.query(Semaine).filter_by(
        annee=iso_year, numero_semaine=iso_week,
    ).first()
    if semaine:
        saisie = session.query(SaisieHebdo).filter_by(semaine_id=semaine.id).first()
        return semaine, saisie
    return None, None


def _is_reportee(item: dict) -> bool:
    """Un item est considéré reporté s'il porte une référence
    ``reportee_depuis_semaine_id``."""
    return bool(item.get("reportee_depuis_semaine_id"))


# ---------------------------------------------------------------------------
# Rendu UI
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("🎯 Projets & Tâches Perso")
    st.caption(
        "Planifie tes projets de développement, tes tâches administratives ou tes objectifs personnels. "
        "Les tâches non terminées la semaine précédente apparaissent ici automatiquement."
    )

    with get_session() as session:
        semaine, saisie = _get_current_saisie(session)
        if not saisie or not semaine:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine en cours.")
            return

        config_db: list[dict[str, Any]] = saisie.projets_config or []
        reported_items = [c for c in config_db if _is_reportee(c)]
        regular_items = [c for c in config_db if not _is_reportee(c)]

        # === SECTION 1 — Tâches reportées =================================
        edited_reported = None
        if reported_items:
            st.subheader("🔁 Tâches reportées de la semaine précédente")
            st.warning(
                f"**{len(reported_items)} tâche(s) reportée(s) automatiquement.** "
                "Tu peux les modifier (durée, priorité…), les supprimer si elles "
                "ne sont plus pertinentes, ou les laisser telles quelles. L'IA "
                "leur donnera une priorité plus élevée dans le prochain planning."
            )

            df_reported = pd.DataFrame([{
                "libelle": c["libelle"],
                "duree_min": c.get("duree_min", 60),
                "priorite": c.get("priorite", "Normale"),
                "reportee_de": c.get("reportee_label", "?"),
                # Métadonnées masquées, préservées pour la sauvegarde
                "_meta_semaine_id": c.get("reportee_depuis_semaine_id"),
                "_meta_type_original": c.get("type_original", ""),
                "_meta_commentaire": c.get("commentaire_etudiant", ""),
            } for c in reported_items])

            edited_reported = st.data_editor(
                df_reported,
                num_rows="fixed",  # pas d'ajout possible dans cette section
                width='stretch',
                column_config={
                    "libelle": st.column_config.TextColumn("Libellé", required=True),
                    "duree_min": st.column_config.NumberColumn("Durée (min)", min_value=15, step=15),
                    "priorite": st.column_config.SelectboxColumn("Priorité", options=PRIORITES),
                    "reportee_de": st.column_config.TextColumn("🔁 Reportée de", disabled=True),
                    "_meta_semaine_id": None,
                    "_meta_type_original": None,
                    "_meta_commentaire": None,
                },
                key="editor_reportees",
            )
            st.divider()

        # === SECTION 2 — Nouvelles tâches =================================
        st.subheader("✏️ Nouvelles tâches & projets")
        st.caption("Coche **Projet fil rouge** pour les activités régulières (ex: App Python).")

        df_regular = pd.DataFrame(regular_items)
        if df_regular.empty:
            df_regular = pd.DataFrame([{
                "libelle": "",
                "duree_min": 60,
                "deadline": None,
                "priorite": "Normale",
                "projet_long_terme": False,
            }])
        # On s'assure que toutes les colonnes attendues sont présentes
        for col, default in [
            ("libelle", ""), ("duree_min", 60), ("deadline", None),
            ("priorite", "Normale"), ("projet_long_terme", False),
        ]:
            if col not in df_regular.columns:
                df_regular[col] = default
        df_regular = df_regular[["libelle", "duree_min", "deadline", "priorite", "projet_long_terme"]]
        # Conversion deadline string → datetime (compat avec DatetimeColumn)
        df_regular["deadline"] = pd.to_datetime(df_regular["deadline"], errors="coerce")

        edited_regular = st.data_editor(
            df_regular,
            num_rows="dynamic",
            width='stretch',
            column_config={
                "libelle": st.column_config.TextColumn("Nom de la tâche / du projet", required=True),
                "duree_min": st.column_config.NumberColumn("Durée (min) à y consacrer", min_value=15, step=15, default=60),
                "deadline": st.column_config.DatetimeColumn("Deadline (Optionnelle)", format="DD/MM/YYYY HH:mm"),
                "priorite": st.column_config.SelectboxColumn("Priorité", options=PRIORITES, default="Normale"),
                "projet_long_terme": st.column_config.CheckboxColumn("Projet fil rouge", default=False),
            },
            key="editor_projets",
        )

        # === SAUVEGARDE ===================================================
        st.divider()
        if st.button("💾 Enregistrer mes projets", type="primary"):
            nouvelle_config: list[dict[str, Any]] = []

            # 1. Items reportés (gardent leurs métadonnées)
            if edited_reported is not None:
                for _, row in edited_reported.iterrows():
                    if pd.notna(row.get("libelle")) and str(row["libelle"]).strip():
                        nouvelle_config.append({
                            "libelle": str(row["libelle"]).strip(),
                            "duree_min": int(row.get("duree_min", 60) or 60),
                            "deadline": "",
                            "priorite": str(row.get("priorite", "Normale")),
                            "projet_long_terme": False,
                            "reportee_depuis_semaine_id": row.get("_meta_semaine_id"),
                            "reportee_label": str(row.get("reportee_de", "")),
                            "type_original": str(row.get("_meta_type_original", "")),
                            "commentaire_etudiant": str(row.get("_meta_commentaire", "")),
                        })

            # 2. Items réguliers
            for _, row in edited_regular.iterrows():
                if pd.notna(row.get("libelle")) and str(row.get("libelle")).strip():
                    deadline_str = ""
                    if pd.notna(row.get("deadline")):
                        try:
                            deadline_str = row["deadline"].strftime("%Y-%m-%d %H:%M")
                        except Exception:  # noqa: BLE001
                            pass
                    nouvelle_config.append({
                        "libelle": str(row["libelle"]).strip(),
                        "duree_min": int(row.get("duree_min", 60) or 60),
                        "deadline": deadline_str,
                        "priorite": str(row.get("priorite", "Normale")),
                        "projet_long_terme": bool(row.get("projet_long_terme", False)),
                    })

            try:
                with session_scope() as write_session:
                    saisie_to_update = write_session.get(SaisieHebdo, saisie.id)
                    saisie_to_update.projets_config = nouvelle_config
                st.success("✅ Tes projets sont enregistrés !")
                st.toast("Projets sauvegardés", icon="✅")
            except Exception as e:  # noqa: BLE001
                st.error(f"Erreur lors de la sauvegarde : {e}")