"""Onglet **Social & Loisirs** de la saisie hebdomadaire."""

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
JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _get_current_saisie(session: Session) -> tuple[Semaine | None, SaisieHebdo | None]:
    today = datetime.date.today()
    iso_year, iso_week, _ = today.isocalendar()
    semaine = session.query(Semaine).filter_by(annee=iso_year, numero_semaine=iso_week).first()
    if semaine:
        saisie = session.query(SaisieHebdo).filter_by(semaine_id=semaine.id).first()
        return semaine, saisie
    return None, None

def _is_valid_time(s: str) -> bool:
    """Valide le format HH:MM."""
    try:
        parts = str(s).split(":")
        if len(parts) != 2: return False
        h, m = int(parts[0]), int(parts[1])
        return 0 <= h <= 23 and 0 <= m <= 59
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Rendu UI
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("🍹 Social & Loisirs")
    st.caption("Protège tes moments de détente. L'IA planifiera tes révisions autour de ces créneaux vitaux.")

    with get_session() as session:
        semaine, saisie = _get_current_saisie(session)
        
        if not saisie or not semaine:
            st.warning("⚠️ Ouvre d'abord l'onglet 'Études' pour initialiser la semaine en cours.")
            return

        config_db: dict[str, Any] = saisie.social_config or {}

        # --- Section Décompression Quotidienne ---
        st.subheader("1. Décompression quotidienne")
        temps_decompression = st.slider(
            "Temps de détente minimum par jour (min)",
            min_value=30, max_value=240, step=15,
            value=config_db.get("temps_decompression_min", 90),
            help="L'IA sanctuarisera ce temps chaque jour pour tes loisirs libres."
        )

        # --- Section Événements Sociaux ---
        st.divider()
        st.subheader("2. Événements sociaux fixes")
        st.caption("Soirée entre amis, appels importants, sessions de jeu prévues, etc.")
        
        evenements_db = config_db.get("evenements", [])
        df_evenements = pd.DataFrame(evenements_db)
        if df_evenements.empty:
            # Lignes d'exemple par défaut
            df_evenements = pd.DataFrame([
                {
                    "libelle": "Appel vidéo", 
                    "jour": "Dimanche", 
                    "heure_debut": "14:00", 
                    "duree_min": 120
                },
                {
                    "libelle": "Session gaming (CK3 / Skyrim)", 
                    "jour": "Vendredi", 
                    "heure_debut": "21:00", 
                    "duree_min": 180
                }
            ])
            
        edited_evenements = st.data_editor(
            df_evenements,
            num_rows="dynamic",
            width='stretch',
            column_config={
                "libelle": st.column_config.TextColumn("Libellé", required=True),
                "jour": st.column_config.SelectboxColumn("Jour", options=JOURS, required=True),
                "heure_debut": st.column_config.TextColumn("Heure de début (HH:MM)", required=True),
                "duree_min": st.column_config.NumberColumn("Durée (min)", min_value=30, step=30, default=120),
            },
            key="editor_social"
        )

        st.divider()
        if st.button("💾 Enregistrer Social & Loisirs", type="primary"):
            evenements_propres = []
            erreurs = []
            
            for i, row in edited_evenements.iterrows():
                if pd.notna(row.get("libelle")) and str(row.get("libelle")).strip() != "":
                    hd = str(row.get("heure_debut", "")).strip()
                    if not _is_valid_time(hd):
                        erreurs.append(f"Format d'heure invalide pour '{row['libelle']}' (attendu: HH:MM).")
                        continue
                        
                    evenements_propres.append({
                        "libelle": str(row["libelle"]).strip(),
                        "jour": str(row.get("jour", "Lundi")),
                        "heure_debut": hd,
                        "duree_min": int(row.get("duree_min", 120))
                    })

            if erreurs:
                for err in erreurs:
                    st.error(err)
            else:
                try:
                    with session_scope() as write_session:
                        saisie_to_update = write_session.query(SaisieHebdo).get(saisie.id)
                        saisie_to_update.social_config = {
                            "temps_decompression_min": int(temps_decompression),
                            "evenements": evenements_propres
                        }
                    st.success("✅ Tes moments sociaux sont protégés et enregistrés !")
                    st.toast("Loisirs sauvegardés", icon="✅")
                except Exception as e:
                    st.error(f"Erreur lors de la sauvegarde : {e}")