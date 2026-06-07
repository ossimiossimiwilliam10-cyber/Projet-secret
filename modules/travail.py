"""Onglet **Gestion des Jobs & Travail**."""

from __future__ import annotations

import datetime
import streamlit as st
from database import Job, Utilisateur, get_session, session_scope
from utils.helpers import get_or_create_week_for_offset

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
    st.subheader("💼 Emplois & Activités Professionnelles")
    st.caption(
        "Renseigne tes contrats à long terme ou tes shifts ponctuels. "
        "L'algorithme verrouillera automatiquement ces créneaux dans ton planning."
    )

    offset_courant = int(st.session_state.get("semaine_target_offset", 0))

    with get_session() as session:
        semaine, _, _ = get_or_create_week_for_offset(
            session, offset_weeks=offset_courant, transfer_reported=False,
        )

        # Liste des trajets habituels pour offrir un selecteur de lieu cohérent.
        # Défensif : profil OU sa sous-config logistique peuvent manquer
        # (DB fraîche, ou données legacy sans sous-config).
        profil = session.query(Utilisateur).first()
        logistique = getattr(profil, "logistique", None) if profil else None
        trajets_habituels = dict(getattr(logistique, "trajets_habituels", None) or {})

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

                # 📍 Lieu du job — pour croiser avec les trajets habituels
                # du Utilisateur. Si un trajet correspond, l'algorithme utilise sa durée
                # exacte au lieu du temps par défaut.
                lieu_options = ["(aucun)"] + list(trajets_habituels.keys()) + ["✏️ Saisir un autre lieu…"]
                lieu_choisi = st.selectbox(
                    "📍 Lieu du job (matche un trajet habituel)",
                    options=lieu_options,
                    index=0,
                    help="Choisis un trajet déclaré dans ton Utilisateur pour que "
                         "l'algorithme calcule exactement le bon temps de déplacement. "
                         "Ex. : « Strasbourg-Luxembourg » → 150 min de trajet.",
                )
                if lieu_choisi == "✏️ Saisir un autre lieu…":
                    lieu_libre = st.text_input(
                        "Lieu (saisie libre)",
                        placeholder="Ex. : Luxembourg-ville, Mulhouse, Télétravail…",
                    )
                else:
                    lieu_libre = ""

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
                elif heure_debut >= heure_fin:
                    st.error("L'heure de fin doit être postérieure à l'heure de début.")
                else:
                    # Résolution du lieu : choix dans la liste OU saisie libre.
                    if lieu_choisi == "(aucun)":
                        lieu_final = ""
                    elif lieu_choisi == "✏️ Saisir un autre lieu…":
                        lieu_final = (lieu_libre or "").strip()
                    else:
                        lieu_final = lieu_choisi

                    with session_scope() as write_session:
                        nouveau_job = Job(
                            titre=titre.strip(),
                            jour=jour,
                            heure_debut=heure_debut,
                            heure_fin=heure_fin,
                            lieu=lieu_final,
                            date_debut=d_debut if type_duree == "long_terme" else None,
                            date_fin=d_fin if type_duree == "long_terme" else None,
                            semaine_id=semaine.id if type_duree == "semaine_unique" else None,
                        )
                        write_session.add(nouveau_job)

                    st.toast(f"✅ Activité '{titre}' enregistrée avec succès !")
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
                    if j.lieu:
                        st.caption(f"📍 Lieu : **{j.lieu}**")
                with c2:
                    st.markdown(
                        f"⏰ {j.jour.capitalize()} de **{j.heure_debut.strftime('%H:%M')}** à **{j.heure_fin.strftime('%H:%M')}**"
                    )
                with c3:
                    col_edit, col_del = st.columns(2)
                    with col_edit:
                        if st.button(
                            "✏️", key=f"edit_job_{j.id}", width="stretch",
                            help="Modifier ce job",
                        ):
                            st.session_state["editing_job_id"] = j.id
                            st.rerun()
                    with col_del:
                        if st.button(
                            "🗑️", key=f"del_job_{j.id}", width="stretch",
                            help="Supprimer ce job",
                        ):
                            with session_scope() as delete_session:
                                job_to_del = delete_session.get(Job, j.id)
                                if job_to_del:
                                    delete_session.delete(job_to_del)
                            st.toast("Activité supprimée", icon="🗑️")
                            st.rerun()

                # === Formulaire d'édition (déplié au clic sur ✏️) ===
                if st.session_state.get("editing_job_id") == j.id:
                    _render_edit_form_job(j, trajets_habituels, semaine)


def _render_edit_form_job(j: Job, trajets_habituels: dict, semaine) -> None:
    """Formulaire d'édition d'un job existant, prérempli avec ses valeurs.

    Apparaît sous la carte du job quand l'utilisateur a cliqué sur ✏️.
    On garde la même logique que la création (long-terme vs semaine
    unique, sélection de lieu via trajets habituels ou saisie libre).
    """
    st.markdown("---")
    st.markdown(f"**✏️ Modifier : {j.titre}**")

    with st.form(f"edit_form_job_{j.id}"):
        col1, col2 = st.columns(2)
        with col1:
            new_titre = st.text_input("Intitulé*", value=j.titre)
            new_jour = st.selectbox(
                "Jour de la semaine*",
                options=JOURS,
                index=JOURS.index(j.jour) if j.jour in JOURS else 0,
                format_func=lambda x: x.capitalize(),
            )
        with col2:
            new_debut = st.time_input("Heure de début*", value=j.heure_debut)
            new_fin = st.time_input("Heure de fin*", value=j.heure_fin)

        # Sélecteur de lieu : si le lieu actuel est dans les trajets,
        # le présélectionner ; sinon proposer la saisie libre.
        lieu_options = ["(aucun)"] + list(trajets_habituels.keys()) + ["✏️ Saisir un autre lieu…"]
        if not j.lieu:
            default_lieu_idx = 0
        elif j.lieu in trajets_habituels:
            default_lieu_idx = 1 + list(trajets_habituels.keys()).index(j.lieu)
        else:
            default_lieu_idx = len(lieu_options) - 1
        new_lieu_choisi = st.selectbox(
            "📍 Lieu du job",
            options=lieu_options,
            index=default_lieu_idx,
        )
        if new_lieu_choisi == "✏️ Saisir un autre lieu…":
            new_lieu_libre = st.text_input(
                "Lieu (saisie libre)",
                value=j.lieu if j.lieu and j.lieu not in trajets_habituels else "",
            )
        else:
            new_lieu_libre = ""

        st.divider()
        # Détermine le type courant depuis la donnée
        if j.semaine_id is not None:
            type_courant = "semaine_unique"
        else:
            type_courant = "long_terme"
        new_type = st.radio(
            "Type de planification",
            options=["long_terme", "semaine_unique"],
            index=0 if type_courant == "long_terme" else 1,
            format_func=lambda x: (
                "🗓️ Long terme (plusieurs mois)" if x == "long_terme"
                else "🎯 Semaine en cours uniquement"
            ),
        )
        col_d1, col_d2 = st.columns(2)
        with col_d1:
            new_d_debut = st.date_input(
                "Date de début",
                value=j.date_debut or datetime.date.today(),
                disabled=(new_type == "semaine_unique"),
            )
        with col_d2:
            new_d_fin = st.date_input(
                "Date de fin",
                value=j.date_fin or (datetime.date.today() + datetime.timedelta(days=90)),
                disabled=(new_type == "semaine_unique"),
            )

        col_save, col_cancel = st.columns(2)
        with col_save:
            save = st.form_submit_button("💾 Enregistrer", type="primary", width="stretch")
        with col_cancel:
            cancel = st.form_submit_button("✕ Annuler", width="stretch")

    if cancel:
        st.session_state.pop("editing_job_id", None)
        st.rerun()

    if save:
        if not new_titre.strip():
            st.error("L'intitulé est obligatoire.")
            return
        if new_debut >= new_fin:
            st.error("L'heure de fin doit être postérieure à l'heure de début.")
            return

        # Résolution du lieu (même logique que la création).
        if new_lieu_choisi == "(aucun)":
            lieu_final = ""
        elif new_lieu_choisi == "✏️ Saisir un autre lieu…":
            lieu_final = (new_lieu_libre or "").strip()
        else:
            lieu_final = new_lieu_choisi

        try:
            with session_scope() as write_session:
                job_to_update = write_session.get(Job, j.id)
                if job_to_update is None:
                    st.error("Job introuvable (peut-être supprimé entre-temps).")
                    return
                job_to_update.titre = new_titre.strip()
                job_to_update.jour = new_jour
                job_to_update.heure_debut = new_debut
                job_to_update.heure_fin = new_fin
                job_to_update.lieu = lieu_final
                if new_type == "long_terme":
                    job_to_update.date_debut = new_d_debut
                    job_to_update.date_fin = new_d_fin
                    job_to_update.semaine_id = None
                else:
                    job_to_update.date_debut = None
                    job_to_update.date_fin = None
                    job_to_update.semaine_id = semaine.id
            st.session_state.pop("editing_job_id", None)
            st.toast("Job mis à jour", icon="✅")
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger("travail").exception("mise à jour job")
            st.error(f"Erreur lors de la mise à jour : {exc}")


__all__ = ["render"]