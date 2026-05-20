"""Onglet **Gestion des Jobs & Travail**."""

from __future__ import annotations

import datetime
import streamlit as st
from database.db import get_session, session_scope
from database.models import Job
from utils.helpers import get_or_create_current_week

JOURS = [
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
]


def render() -> None:
    st.title("💼 Emplois & Activités Professionnelles")
    st.caption(
        "Renseigne tes contrats à long terme ou tes shifts ponctuels. "
        "L'IA verrouillera automatiquement ces créneaux dans ton planning."
    )

    with get_session() as session:
        semaine, _, _ = get_or_create_current_week(session, transfer_reported=False)

        # 1. Formulaire d'ajout
        with st.expander("➕ Enregistrer une activité professionnelle", expanded=True):
            with st.form("form_add_job", clear_on_submit=True):
                col1, col2 = st.columns(2)
                with col1:
                    titre = st.text_input(
                        "Intitulé du poste / Mission*",
                        placeholder="Ex: Magasinier, Assistant, Livreur...",
                    )
                    jour = st.selectbox(
                        "Jour de la semaine*",
                        options=JOURS,
                        format_func=lambda x: x.capitalize(),
                    )
                with col2:
                    heure_debut = st.time_input(
                        "Heure de début*", value=datetime.time(8, 0)
                    )
                    heure_fin = st.time_input(
                        "Heure de fin*", value=datetime.time(16, 0)
                    )

                st.divider()
                st.markdown("**Disponibilité et récurrence de l'horaire :**")
                type_duree = st.radio(
                    "Type de planification",
                    options=["long_terme", "semaine_unique"],
                    format_func=lambda x: (
                        "🗓️ Contrat long terme (Actif sur une période de plusieurs mois)"
                        if x == "long_terme"
                        else "🎯 Temporaire (Uniquement pour la semaine en cours)"
                    ),
                )

                # Sélecteurs de dates pour le long terme
                col_d1, col_d2 = st.columns(2)
                with col_d1:
                    d_debut = st.date_input(
                        "Date de début du contrat",
                        value=datetime.date.today(),
                        disabled=(type_duree == "semaine_unique"),
                    )
                with col_d2:
                    d_fin = st.date_input(
                        "Date de fin du contrat",
                        value=datetime.date.today() + datetime.timedelta(days=90),
                        disabled=(type_duree == "semaine_unique"),
                    )

                submit = st.form_submit_button(
                    "Enregistrer l'horaire", type="primary", width='stretch'
                )

            if submit:
                if not titre.strip():
                    st.error("L'intitulé du poste est obligatoire.")
                    return
                if heure_debut >= heure_fin:
                    st.error("L'heure de fin doit être postérieure à l'heure de début.")
                    return

                with session_scope() as write_session:
                    nouveau_job = Job(
                        titre=titre.strip(),
                        jour=jour,
                        heure_debut=heure_debut,
                        heure_fin=heure_fin,
                        date_debut=d_debut if type_duree == "long_terme" else None,
                        date_fin=d_fin if type_duree == "long_terme" else None,
                        semaine_id=semaine.id if type_duree == "semaine_unique" else None,
                    )
                    write_session.add(nouveau_job)

                st.success(f"✅ Activité '{titre}' enregistrée avec succès !")
                st.rerun()

        # 2. Liste des activités enregistrées
        st.divider()
        st.subheader("Mes engagements professionnels enregistrés")

        tous_les_jobs = session.query(Job).order_by(Job.jour).all()

        if not tous_les_jobs:
            st.info("Aucun horaire de travail enregistré pour le moment.")
            return

        for j in tous_les_jobs:
            # Construction du label de validité temporelle
            if j.semaine_id:
                validite = f"🎯 Uniquement la Semaine {semaine.numero_semaine}"
            elif j.date_debut and j.date_fin:
                validite = f"🗓️ Du {j.date_debut.strftime('%d/%m/%Y')} au {j.date_fin.strftime('%d/%m/%Y')}"
            else:
                validite = "♾️ Permanent"

            with st.container(border=True):
                c1, c2, c3 = st.columns([2, 2, 1])
                with c1:
                    st.markdown(f"**💼 {j.titre}**")
                    st.caption(f"Validité : {validite}")
                with c2:
                    st.markdown(
                        f"⏰ {j.jour.capitalize()} de **{j.heure_debut.strftime('%H:%M')}** à **{j.heure_fin.strftime('%H:%M')}**"
                    )
                with c3:
                    if st.button(
                        "🗑️ Supprimer", key=f"del_job_{j.id}", width='stretch'
                    ):
                        with session_scope() as delete_session:
                            job_to_del = delete_session.query(Job).get(j.id)
                            if job_to_del:
                                delete_session.delete(job_to_del)
                        st.toast("Activité supprimée", icon="🗑️")
                        st.rerun()


__all__ = ["render"]