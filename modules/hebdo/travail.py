"""Onglet **Jobs & Travail** de la saisie hebdomadaire.

Permet de planifier les heures de travail salarié ou d'alternance.
L'IA bloque strictement ces créneaux pour ne jamais y placer d'études.
"""

from __future__ import annotations

import datetime
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from database.db import get_session, session_scope
from database.models import Job, Semaine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_current_week(session: Session) -> Semaine | None:
    today = datetime.date.today()
    iso_year, iso_week, _ = today.isocalendar()
    return session.query(Semaine).filter_by(annee=iso_year, numero_semaine=iso_week).first()

# ---------------------------------------------------------------------------
# Rendu UI
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("💼 Jobs & Travail Salarié")
    st.caption("Planifie tes heures de travail. Ces créneaux seront sanctuarisés par l'IA.")

    with get_session() as session:
        semaine = _get_current_week(session)
        if not semaine:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine.")
            return

        # Récupération des jobs déjà en base pour cette semaine
        jobs_db = session.query(Job).filter_by(semaine_id=semaine.id).all()
        
        # Préparation du DataFrame
        data = []
        for j in jobs_db:
            data.append({
                "id": j.id,
                "titre": j.titre,
                "jour": j.jour.capitalize(),
                "debut": j.heure_debut.strftime("%H:%M"),
                "fin": j.heure_fin.strftime("%H:%M"),
                "salaire_h": 11.65 # SMIC par défaut si non stocké
            })
            
        df_jobs = pd.DataFrame(data)
        if df_jobs.empty:
            df_jobs = pd.DataFrame([{
                "titre": "Job",
                "jour": "Samedi",
                "debut": "09:00",
                "fin": "17:00",
                "salaire_h": 11.65
            }])

        st.subheader("Tes créneaux de travail")
        edited_jobs = st.data_editor(
            df_jobs,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "titre": st.column_config.TextColumn("Lieu / Poste", required=True),
                "jour": st.column_config.SelectboxColumn("Jour", options=["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"], required=True),
                "debut": st.column_config.TextColumn("Heure début (HH:MM)", required=True),
                "fin": st.column_config.TextColumn("Heure fin (HH:MM)", required=True),
                "salaire_h": st.column_config.NumberColumn("Salaire horaire (€)", min_value=0.0, format="%.2f €")
            },
            key="jobs_editor_v2"
        )

        # Calcul du revenu estimé
        revenu_total = 0
        heures_totales = 0
        for _, row in edited_jobs.iterrows():
            try:
                h_deb, m_deb = map(int, str(row["debut"]).split(":"))
                h_fin, m_fin = map(int, str(row["fin"]).split(":"))
                duree_h = (h_fin + m_fin/60) - (h_deb + m_deb/60)
                if duree_h > 0:
                    heures_totales += duree_h
                    revenu_total += duree_h * float(row.get("salaire_h", 0))
            except: continue

        if heures_totales > 0:
            st.info(f"💰 **Revenu estimé** : {revenu_total:.2f} € pour {heures_totales:.1f}h de travail.")

        st.divider()
        
        if st.button("💾 Enregistrer mes horaires de travail", type="primary", use_container_width=True):
            try:
                with session_scope() as write_session:
                    # On nettoie les anciens pour cette semaine
                    write_session.query(Job).filter_by(semaine_id=semaine.id).delete()
                    
                    for _, row in edited_jobs.iterrows():
                        if pd.notna(row.get("titre")):
                            h_deb, m_deb = map(int, str(row["debut"]).split(":"))
                            h_fin, m_fin = map(int, str(row["fin"]).split(":"))
                            
                            new_job = Job(
                                semaine_id=semaine.id,
                                titre=str(row["titre"]).strip(),
                                jour=str(row["jour"]).lower(),
                                heure_debut=datetime.time(h_deb, m_deb),
                                heure_fin=datetime.time(h_fin, m_fin)
                            )
                            write_session.add(new_job)
                            
                st.success("✅ Horaires enregistrés !")
                st.rerun()
            except Exception as e:
                st.error(f"Erreur lors de la sauvegarde : {e}. Vérifie le format HH:MM.")
