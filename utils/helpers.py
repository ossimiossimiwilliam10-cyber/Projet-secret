"""Helpers partagés entre modules.

Pour l'instant :

- :func:`get_or_create_current_week` : crée la ``Semaine`` + la ``SaisieHebdo``
  de la semaine ISO en cours si elles n'existent pas. **Si une nouvelle
  ``SaisieHebdo`` est créée**, déclenche automatiquement le transfert des
  tâches non finies depuis la semaine précédente.
"""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from database.models import SaisieHebdo, Semaine


def get_or_create_current_week(
    session: Session,
    transfer_reported: bool = True,
) -> tuple[Semaine, SaisieHebdo, int]:
    """Récupère (ou crée) la semaine en cours et sa SaisieHebdo.

    Args:
        session: session SQLAlchemy active.
        transfer_reported: si ``True``, déclenche le transfert des tâches
            reportées depuis la semaine précédente lors de la première création.

    Returns:
        ``(semaine, saisie, nb_reportees)`` — ``nb_reportees`` est 0 si la
        SaisieHebdo existait déjà ou s'il n'y avait rien à reporter.
    """
    today = datetime.date.today()
    iso_year, iso_week, _ = today.isocalendar()

    semaine = session.query(Semaine).filter_by(
        annee=iso_year, numero_semaine=iso_week,
    ).first()

    if semaine is None:
        lundi = today - datetime.timedelta(days=today.weekday())
        dimanche = lundi + datetime.timedelta(days=6)
        semaine = Semaine(
            numero_semaine=iso_week,
            annee=iso_year,
            date_debut=lundi,
            date_fin=dimanche,
            statut="en_cours",
        )
        session.add(semaine)
        session.flush()

    saisie = session.query(SaisieHebdo).filter_by(semaine_id=semaine.id).first()
    nb_reportees = 0

    if saisie is None:
        saisie = SaisieHebdo(semaine_id=semaine.id)
        session.add(saisie)
        session.flush()

        # Transfert depuis la semaine précédente — UNIQUEMENT à la première
        # création de la saisie. Si on revient sur l'onglet plus tard, on ne
        # re-pollue pas avec les mêmes tâches.
        if transfer_reported:
            previous_week = _find_previous_week(session, semaine)
            if previous_week is not None:
                # Import local pour éviter un cycle utils <-> services
                from services.report_service import transfer_reported_items_to_new_week
                nb_reportees = transfer_reported_items_to_new_week(
                    session, previous_week.id, saisie,
                )

        session.commit()

    return semaine, saisie, nb_reportees


def _find_previous_week(session: Session, current: Semaine) -> Semaine | None:
    """Trouve la semaine la plus récente strictement antérieure à ``current``."""
    return (
        session.query(Semaine)
        .filter(
            (Semaine.annee < current.annee)
            | (
                (Semaine.annee == current.annee)
                & (Semaine.numero_semaine < current.numero_semaine)
            )
        )
        .order_by(Semaine.annee.desc(), Semaine.numero_semaine.desc())
        .first()
    )


__all__ = ["get_or_create_current_week"]
