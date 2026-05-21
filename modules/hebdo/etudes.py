"""Onglet **Études** de la saisie hebdomadaire.

Permet de sélectionner les cours et chapitres à travailler cette semaine,
ainsi que d'ajouter des travaux ponctuels (devoirs, projets notés).

**Nouveauté :** la création de la semaine + le transfert des tâches reportées
de la semaine précédente sont délégués à ``utils.helpers.get_or_create_current_week``.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from database.db import get_session, session_scope
from database.models import Chapitre, Matiere, SaisieHebdo
from utils.helpers import get_or_create_current_week

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
URGENCES = ["Normale", "Prioritaire", "Exam dans moins de 7 jours"]
PRIORITES = ["Basse", "Normale", "Haute"]
TYPES_TRAVAIL = [
    "Première lecture",
    "Révision / Compréhension",
    "Fiches de synthèse",
    "Exercices / Pratique",
    "Prépa Examen",
]


# ---------------------------------------------------------------------------
# Rendu UI
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("📚 Études de la semaine")
    st.caption("Sélectionne tes cours, et définis tes objectifs de la semaine.")

    with get_session() as session:
        # Helper partagé : crée la semaine + déclenche le transfert des tâches
        # reportées depuis la semaine précédente si on est sur une nouvelle
        # SaisieHebdo. Retourne aussi le nombre de tâches reportées.
        semaine, saisie, nb_reportees = get_or_create_current_week(session)

        st.info(
            f"📅 **Semaine {semaine.numero_semaine}** : "
            f"du {semaine.date_debut.strftime('%d/%m')} au {semaine.date_fin.strftime('%d/%m/%Y')}"
        )

        if nb_reportees > 0:
            st.warning(
                f"🔁 **{nb_reportees} tâche(s) ont été reportées** de la semaine précédente. "
                "Va sur l'onglet **Projets & Tâches** pour les voir et les ajuster."
            )

        # Récupération des données existantes
        matieres_selectionnees_db: list[dict[str, Any]] = saisie.matieres_selectionnees or []
        travaux_ponctuels_db: list[dict[str, Any]] = saisie.travaux_ponctuels or []

        # Récupération de toutes les matières actives
        toutes_matieres = (
            session.query(Matiere).filter_by(actif=True).order_by(Matiere.nom).all()
        )
        if not toutes_matieres:
            st.warning(
                "Ta bibliothèque est vide. Crée des matières et importe des PDFs "
                "avant de planifier ta semaine."
            )
            return

        # 1. SÉLECTION MULTIPLE DES MATIÈRES
        matiere_ids_deja_selectionnees = [
            m["matiere_id"] for m in matieres_selectionnees_db
        ]
        matieres_pre_selectionnees = [
            m for m in toutes_matieres if m.id in matiere_ids_deja_selectionnees
        ]

        st.subheader("1. Matières à travailler")
        matieres_choisies = st.multiselect(
            "Quelles matières souhaites-tu aborder cette semaine ?",
            options=toutes_matieres,
            default=matieres_pre_selectionnees,
            format_func=lambda m: f"{m.nom} ({m.ue.nom})" if m.ue else m.nom,
            help="Choisis les matières. L'IA répartira ensuite les chapitres "
                 "selon la règle « max 1 nouveau chapitre par matière par jour ».",
        )

        nouvelles_matieres_selectionnees = []

        # 2. DÉTAILS POUR CHAQUE MATIÈRE CHOISIE
        if matieres_choisies:
            for matiere in matieres_choisies:
                config_existante = next(
                    (m for m in matieres_selectionnees_db
                     if m.get("matiere_id") == matiere.id),
                    {},
                )

                with st.expander(f"⚙️ Configurer : {matiere.nom}", expanded=True):
                    chapitres_matiere = (
                        session.query(Chapitre)
                        .filter_by(matiere_id=matiere.id)
                        .order_by(Chapitre.numero)
                        .all()
                    )

                    if not chapitres_matiere:
                        st.caption(
                            "⚠️ Cette matière n'a pas encore de chapitre. "
                            "Importe un PDF depuis la Bibliothèque."
                        )
                        continue

                    chapitres_options = {
                        ch.id: f"Chap. {ch.numero} - {ch.titre}"
                        for ch in chapitres_matiere
                    }

                    ch_ids_def = [
                        ch_id for ch_id in config_existante.get("chapitre_ids", [])
                        if ch_id in chapitres_options
                    ]

                    chapitres_choisis = st.multiselect(
                        "Chapitres spécifiques (laisse vide pour une révision globale)",
                        options=list(chapitres_options.keys()),
                        default=ch_ids_def,
                        format_func=lambda x: chapitres_options[x],
                        key=f"ch_{matiere.id}",
                    )

                    col1, col2 = st.columns(2)
                    with col1:
                        idx_type = 0
                        if config_existante.get("type_travail") in TYPES_TRAVAIL:
                            idx_type = TYPES_TRAVAIL.index(config_existante["type_travail"])
                        type_travail = st.selectbox(
                            "Type de travail", options=TYPES_TRAVAIL,
                            index=idx_type, key=f"type_{matiere.id}",
                        )

                    with col2:
                        idx_urg = 0
                        if config_existante.get("urgence") in URGENCES:
                            idx_urg = URGENCES.index(config_existante["urgence"])
                        urgence = st.select_slider(
                            "Urgence", options=URGENCES, value=URGENCES[idx_urg],
                            key=f"urg_{matiere.id}",
                        )

                    nouvelles_matieres_selectionnees.append({
                        "matiere_id": matiere.id,
                        "chapitre_ids": chapitres_choisis,
                        "type_travail": type_travail,
                        "urgence": urgence,
                    })

        # 3. TRAVAUX PONCTUELS (Devoirs, exposés)
        st.divider()
        st.subheader("2. Travaux ponctuels")
        st.caption("Devoir à rendre, compte-rendu de TP, projet noté...")

        df_travaux = pd.DataFrame(travaux_ponctuels_db)
        if df_travaux.empty:
            df_travaux = pd.DataFrame(columns=["libelle", "deadline", "duree_min", "priorite"])
        # Conversion deadline string → datetime (compat avec DatetimeColumn)
        if "deadline" in df_travaux.columns:
            df_travaux["deadline"] = pd.to_datetime(df_travaux["deadline"], errors="coerce")
        edited_travaux = st.data_editor(
            df_travaux,
            num_rows="dynamic",
            width='stretch',
            column_config={
                "libelle": st.column_config.TextColumn("Libellé du devoir", required=True),
                "deadline": st.column_config.DatetimeColumn("Deadline (Date & Heure)", format="DD/MM/YYYY HH:mm"),
                "duree_min": st.column_config.NumberColumn("Durée estimée (min)", min_value=15, step=15, default=60),
                "priorite": st.column_config.SelectboxColumn("Priorité", options=PRIORITES, default="Normale"),
            },
            key="editor_travaux_ponctuels",
        )

        # 4. SAUVEGARDE
        st.divider()
        if st.button("💾 Enregistrer mes objectifs d'études", type="primary"):
            travaux_propres = []
            for _, row in edited_travaux.iterrows():
                if pd.notna(row.get("libelle")) and str(row.get("libelle")).strip() != "":
                    deadline_str = ""
                    if pd.notna(row.get("deadline")):
                        try:
                            deadline_str = row["deadline"].strftime("%Y-%m-%d %H:%M")
                        except Exception:  # noqa: BLE001
                            pass

                    travaux_propres.append({
                        "libelle": str(row["libelle"]).strip(),
                        "deadline": deadline_str,
                        "duree_min": int(row.get("duree_min", 60)),
                        "priorite": str(row.get("priorite", "Normale")),
                    })

            try:
                with session_scope() as write_session:
                    saisie_to_update = write_session.get(SaisieHebdo, saisie.id)
                    saisie_to_update.matieres_selectionnees = nouvelles_matieres_selectionnees
                    saisie_to_update.travaux_ponctuels = travaux_propres
                st.success("✅ Tes objectifs d'études pour la semaine sont enregistrés !")
                st.toast("Objectifs sauvegardés", icon="✅")
            except Exception as e:  # noqa: BLE001
                st.error(f"Erreur lors de la sauvegarde : {e}")