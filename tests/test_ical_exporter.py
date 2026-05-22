"""Tests de l'export iCalendar du planning hebdomadaire."""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import Semaine, Tache
from services.ical_exporter import build_ics_for_semaine, make_ics_filename


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


@pytest.fixture
def semaine(session):
    today = date.today()
    s = Semaine(
        annee=today.year,
        numero_semaine=today.isocalendar().week,
        date_debut=today - timedelta(days=today.weekday()),  # lundi
        date_fin=(today - timedelta(days=today.weekday())) + timedelta(days=6),
    )
    session.add(s)
    session.commit()
    return s


def _make_tache(session, semaine, **kwargs) -> Tache:
    defaults = dict(
        semaine_id=semaine.id,
        type="etude",
        titre="Session",
        jour="lundi",
        heure_debut=time(9, 0),
        heure_fin=time(10, 0),
        statut="a_faire",
        obligatoire=False,
        chapitre_ids=[],
        justification_ia="",
    )
    defaults.update(kwargs)
    t = Tache(**defaults)
    session.add(t)
    session.commit()
    return t


def test_export_contient_les_taches_de_la_semaine(session, semaine):
    """Chaque tâche devient un VEVENT dans le .ics avec son titre."""
    _make_tache(session, semaine, titre="Algèbre - Chap 3", jour="mardi",
                heure_debut=time(14, 0), heure_fin=time(15, 30))
    _make_tache(session, semaine, type="sport", titre="Boxe", jour="mercredi",
                heure_debut=time(18, 0), heure_fin=time(19, 30),
                obligatoire=True)

    ics_bytes = build_ics_for_semaine(session, semaine.id)
    contenu = ics_bytes.decode("utf-8")

    assert "BEGIN:VCALENDAR" in contenu
    assert "END:VCALENDAR" in contenu
    assert "Algèbre - Chap 3" in contenu
    assert "Boxe" in contenu
    assert "VEVENT" in contenu
    # Catégories par type.
    assert "Études" in contenu or "etude" in contenu.lower()


def test_export_uid_stable_permet_la_mise_a_jour(session, semaine):
    """L'UID d'un VEVENT est `tache-{id}@exocerveau` — stable et unique
    par tâche. Permet la mise à jour quand on ré-importe."""
    t = _make_tache(session, semaine, titre="Test UID")
    ics_bytes = build_ics_for_semaine(session, semaine.id)
    assert f"tache-{t.id}@exocerveau" in ics_bytes.decode("utf-8")


def test_export_light_exclut_les_obligatoires(session, semaine):
    """Avec ``inclure_obligatoires=False``, les tâches obligatoires
    (sommeil, repas, sport bloqué, etc.) ne sont pas dans le .ics."""
    _make_tache(session, semaine, titre="Étude libre", obligatoire=False)
    _make_tache(session, semaine, titre="Sommeil", type="sommeil",
                obligatoire=True, jour="lundi",
                heure_debut=time(23, 0), heure_fin=time(23, 59))

    ics_complet = build_ics_for_semaine(
        session, semaine.id, inclure_obligatoires=True,
    ).decode("utf-8")
    ics_light = build_ics_for_semaine(
        session, semaine.id, inclure_obligatoires=False,
    ).decode("utf-8")

    assert "Sommeil" in ics_complet
    assert "Sommeil" not in ics_light
    assert "Étude libre" in ics_light


def test_export_inclut_rappel_pour_sessions_etude(session, semaine):
    """Les tâches d'étude non obligatoires reçoivent un VALARM 10 min
    avant pour avoir une notification native dans le calendrier."""
    _make_tache(session, semaine, type="etude", titre="Révision Maths")
    ics = build_ics_for_semaine(session, semaine.id).decode("utf-8")
    assert "BEGIN:VALARM" in ics
    assert "TRIGGER" in ics
    # -10 minutes au format iCal.
    assert "-PT10M" in ics or "PT-10M" in ics


def test_filename_horodate(session, semaine):
    """Le nom de fichier suggéré inclut la semaine et l'année."""
    nom = make_ics_filename(semaine)
    assert nom.endswith(".ics")
    assert f"S{semaine.numero_semaine:02d}" in nom
    assert str(semaine.annee) in nom


def test_export_ignore_les_taches_sans_heure_valide(session, semaine):
    """Une Tache avec heure_fin <= heure_debut est silencieusement
    ignorée (pas d'event invalide dans le calendrier)."""
    _make_tache(session, semaine, titre="OK", heure_debut=time(9, 0), heure_fin=time(10, 0))
    # Tâche invalide construite manuellement (heure_fin < heure_debut).
    bad = Tache(
        semaine_id=semaine.id, type="etude", titre="Inversée", jour="mardi",
        heure_debut=time(15, 0), heure_fin=time(14, 0),
        statut="a_faire", obligatoire=False,
    )
    session.add(bad)
    session.commit()

    ics = build_ics_for_semaine(session, semaine.id).decode("utf-8")
    assert "OK" in ics
    assert "Inversée" not in ics
