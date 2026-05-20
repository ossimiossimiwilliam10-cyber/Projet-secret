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
from database.models import Chapitre, Cours, SaisieHebdo
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
        cours_selectionnes_db: list[dict[str, Any]] = saisie.cours_selectionnes or []
        travaux_ponctuels_db: list[dict[str, Any]] = saisie.travaux_ponctuels or []

        # Récupération de tous les cours actifs
        tous_les_cours = session.query(Cours).filter_by(actif=True).all()
        if not tous_les_cours:
            st.warning("Ta bibliothèque est vide. Ajoute des cours avant de planifier ta semaine.")
            return

        # 1. SÉLECTION MULTIPLE DES COURS
        cours_ids_deja_selectionnes = [c["cours_id"] for c in cours_selectionnes_db]
        cours_pre_selectionnes = [c for c in tous_les_cours if c.id in cours_ids_deja_selectionnes]

        st.subheader("1. Cours à travailler")
        cours_choisis = st.multiselect(
            "Quels cours souhaites-tu aborder cette semaine ?",
            options=tous_les_cours,
            default=cours_pre_selectionnes,
            format_func=lambda c: f"{c.nom} ({c.matiere})" if c.matiere else c.nom,
            help="Choisis les matières. L'IA se chargera de répartir le volume horaire."
        )

        nouveaux_cours_selectionnes = []

        # 2. DÉTAILS POUR CHAQUE COURS CHOISI
        if cours_choisis:
            for cours in cours_choisis:
                config_existante = next(
                    (c for c in cours_selectionnes_db if c["cours_id"] == cours.id), {}
                )

                with st.expander(f"⚙️ Configurer : {cours.nom}", expanded=True):
                    chapitres_cours = (
                        session.query(Chapitre)
                        .filter_by(cours_id=cours.id)
                        .order_by(Chapitre.numero)
                        .all()
                    )
                    chapitres_options = {ch.id: f"Chap. {ch.numero} - {ch.titre}" for ch in chapitres_cours}

                    ch_ids_def = [
                        ch_id for ch_id in config_existante.get("chapitre_ids", [])
                        if ch_id in chapitres_options
                    ]

                    chapitres_choisis = st.multiselect(
                        "Chapitres spécifiques (laisse vide pour une révision globale)",
                        options=list(chapitres_options.keys()),
                        default=ch_ids_def,
                        format_func=lambda x: chapitres_options[x],
                        key=f"ch_{cours.id}",
                    )

                    col1, col2 = st.columns(2)
                    with col1:
                        idx_type = 0
                        if config_existante.get("type_travail") in TYPES_TRAVAIL:
                            idx_type = TYPES_TRAVAIL.index(config_existante["type_travail"])
                        type_travail = st.selectbox(
                            "Type de travail", options=TYPES_TRAVAIL,
                            index=idx_type, key=f"type_{cours.id}",
                        )

                    with col2:
                        idx_urg = 0
                        if config_existante.get("urgence") in URGENCES:
                            idx_urg = URGENCES.index(config_existante["urgence"])
                        urgence = st.select_slider(
                            "Urgence", options=URGENCES, value=URGENCES[idx_urg],
                            key=f"urg_{cours.id}",
                        )

                    nouveaux_cours_selectionnes.append({
                        "cours_id": cours.id,
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
                    saisie_to_update = write_session.query(SaisieHebdo).get(saisie.id)
                    saisie_to_update.cours_selectionnes = nouveaux_cours_selectionnes
                    saisie_to_update.travaux_ponctuels = travaux_propres
                st.success("✅ Tes objectifs d'études pour la semaine sont enregistrés !")
                st.toast("Objectifs sauvegardés", icon="✅")
            except Exception as e:  # noqa: BLE001
                st.error(f"Erreur lors de la sauvegarde : {e}")