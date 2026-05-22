"""Tests du helper ``get_or_create_week_for_offset``.

Sécurise la fonctionnalité « planifier la semaine prochaine le dimanche
soir » : on doit pouvoir créer une SaisieHebdo cible à offset +1 sans
toucher à la semaine en cours.
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import SaisieHebdo, Semaine
from utils.helpers import get_or_create_week_for_offset


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def test_offset_zero_renvoie_la_semaine_courante(session):
    """offset=0 correspond exactement à get_or_create_current_week."""
    today = datetime.date.today()
    iso_year, iso_week, _ = today.isocalendar()

    semaine, saisie, _ = get_or_create_week_for_offset(session, offset_weeks=0)

    assert semaine.annee == iso_year
    assert semaine.numero_semaine == iso_week
    assert semaine.date_debut <= today <= semaine.date_fin
    assert saisie.semaine_id == semaine.id


def test_offset_un_cree_la_semaine_prochaine(session):
    """offset=1 crée la semaine qui commence lundi prochain."""
    today = datetime.date.today()
    semaine_prochaine_date = today + datetime.timedelta(weeks=1)
    iso_year, iso_week, _ = semaine_prochaine_date.isocalendar()

    semaine, saisie, _ = get_or_create_week_for_offset(session, offset_weeks=1)

    assert semaine.annee == iso_year
    assert semaine.numero_semaine == iso_week
    # date_debut doit être un lundi futur (strictement > today sauf si on est lundi).
    assert semaine.date_debut > today or semaine.date_debut.weekday() == 0
    assert saisie.semaine_id == semaine.id


def test_idempotent_meme_offset_ne_crée_pas_de_doublon(session):
    """Appeler 2 fois avec le même offset doit retourner la même Semaine."""
    s1, _, _ = get_or_create_week_for_offset(session, offset_weeks=1)
    s2, _, _ = get_or_create_week_for_offset(session, offset_weeks=1)
    assert s1.id == s2.id

    nb_semaines = session.query(Semaine).count()
    assert nb_semaines == 1  # une seule, pas deux.


def test_offsets_distincts_creent_des_semaines_distinctes(session):
    """offset=0 et offset=1 doivent créer 2 semaines indépendantes."""
    s_now, _, _ = get_or_create_week_for_offset(session, offset_weeks=0)
    s_next, _, _ = get_or_create_week_for_offset(session, offset_weeks=1)
    assert s_now.id != s_next.id
    # La semaine prochaine commence après la semaine courante.
    assert s_next.date_debut > s_now.date_debut

    nb_saisies = session.query(SaisieHebdo).count()
    assert nb_saisies == 2  # une saisie par semaine.
