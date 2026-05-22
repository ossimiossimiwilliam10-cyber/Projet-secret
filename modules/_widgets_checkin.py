"""Widget Streamlit partagé : Check-in biomécanique du jour.

Extrait du dashboard pour pouvoir être réutilisé là où il est utile —
notamment dans la page **Génération IA** où le check-in influence
directement le plafond d'étude calculé par le scheduler.

Le widget affiche :
- 3 sliders (fatigue physique, charge mentale, qualité du sommeil) sur
  une échelle 1-10.
- Un bouton « Enregistrer mon état » qui fait un upsert sur la ligne
  ``CheckInQuotidien`` du jour.

Effet métier : si fatigue > 7 ou charge_mentale > 7, le plafond
journalier d'étude est réduit de 30 % par
``services.scheduler_engine.calculer_quota_etude_minutes``.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from database.db import session_scope
from database.models import CheckInQuotidien


def render_checkin_quotidien_widget(
    session,
    *,
    expanded_by_default: bool | None = None,
    titre: str = "📊 Check-in Biomécanique du jour",
    key_prefix: str = "checkin",
) -> None:
    """Affiche l'expander du check-in du jour.

    Args:
        session: session SQLAlchemy ouverte (lecture).
        expanded_by_default: force l'état initial de l'expander. Si
            ``None``, l'expander est ouvert quand aucun check-in n'a
            encore été saisi aujourd'hui (auto-incitation).
        titre: libellé de l'expander.
        key_prefix: préfixe pour les keys Streamlit, à customiser si le
            widget apparaît plusieurs fois sur la même page.
    """
    aujourdhui = date.today()
    existing = (
        session.query(CheckInQuotidien)
        .filter(CheckInQuotidien.date == aujourdhui)
        .first()
    )

    if expanded_by_default is None:
        expanded_by_default = existing is None

    val_fatigue = int(existing.fatigue_physique) if existing else 5
    val_mental = int(existing.charge_mentale) if existing else 5
    val_sommeil = int(existing.qualite_sommeil) if existing else 5

    with st.expander(titre, expanded=expanded_by_default):
        st.caption(
            "Évalue ton état actuel sur une échelle de 1 à 10. "
            "L'IA s'en servira pour adapter la difficulté des sessions d'étude. "
            "(1 = forme olympique, 10 = épuisé)"
        )
        col1, col2, col3 = st.columns(3)
        with col1:
            fatigue_physique = st.slider(
                "💪 Fatigue physique", 1, 10, val_fatigue,
                key=f"{key_prefix}_fatigue",
                help="Après boxe, entrepôt, longue marche…",
            )
        with col2:
            charge_mentale = st.slider(
                "🧠 Charge mentale", 1, 10, val_mental,
                key=f"{key_prefix}_mental",
                help="Stress, examens à venir, semaine intense…",
            )
        with col3:
            qualite_sommeil = st.slider(
                "😴 Qualité du sommeil", 1, 10, val_sommeil,
                key=f"{key_prefix}_sommeil",
                help="1 = très mauvais, 10 = parfaitement reposé",
            )

        col_btn, col_msg = st.columns([1, 3])
        with col_btn:
            if st.button(
                "💾 Enregistrer mon état",
                type="primary", width="stretch",
                key=f"{key_prefix}_save_btn",
            ):
                with session_scope() as write_session:
                    row = (
                        write_session.query(CheckInQuotidien)
                        .filter(CheckInQuotidien.date == aujourdhui)
                        .first()
                    )
                    if row is None:
                        row = CheckInQuotidien(date=aujourdhui)
                        write_session.add(row)
                    row.fatigue_physique = int(fatigue_physique)
                    row.charge_mentale = int(charge_mentale)
                    row.qualite_sommeil = int(qualite_sommeil)
                with col_msg:
                    st.success("✅ Check-in enregistré.")
                st.toast("Check-in enregistré", icon="✅")
                st.rerun()

        if existing is not None:
            with col_msg:
                st.caption(
                    f"Dernier check-in : fatigue {existing.fatigue_physique}/10, "
                    f"mental {existing.charge_mentale}/10, "
                    f"sommeil {existing.qualite_sommeil}/10"
                )


__all__ = ["render_checkin_quotidien_widget"]
