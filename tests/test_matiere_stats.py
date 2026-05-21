"""Tests du module ``services.matiere_stats``.

Sécurise les trois usages clés de l'onglet Études :

1. ``stats_matiere`` compte correctement nouveaux / révisions / retard /
   maîtrise moyenne pour une matière.
2. ``matieres_avec_revisions_dues`` ne retourne que les matières
   pertinentes pour la pré-sélection automatique.
3. ``estimer_charge_minutes`` somme bien les ``temps_estime_h`` des
   chapitres cochés dans la sélection hebdo.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import Chapitre, Matiere
from services.matiere_stats import (
    estimer_charge_minutes,
    matieres_avec_revisions_dues,
    stats_matiere,
)


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


def _make_matiere(session, nom: str) -> Matiere:
    m = Matiere(nom=nom)
    session.add(m)
    session.commit()
    return m


def _make_chap(
    session,
    matiere: Matiere,
    numero: int,
    *,
    niveau: int = 0,
    date_prochaine: date | None = None,
    maitrise_pct: float = 0.0,
    temps_estime_h: float = 1.0,
) -> Chapitre:
    ch = Chapitre(
        matiere_id=matiere.id,
        numero=numero,
        titre=f"{matiere.nom} - Chap {numero}",
        niveau_actuel=niveau,
        date_prochaine=date_prochaine,
        maitrise_pct=maitrise_pct,
        temps_estime_h=temps_estime_h,
    )
    session.add(ch)
    session.commit()
    return ch


# ---------------------------------------------------------------------------
# 1. stats_matiere
# ---------------------------------------------------------------------------
def test_stats_matiere_distingue_nouveaux_dus_et_retards(session):
    """Sur une matière typique : un nouveau, une révision due aujourd'hui,
    une en retard de 2 jours, et une déjà programmée plus tard."""
    m = _make_matiere(session, "Algèbre")
    today = date.today()

    _make_chap(session, m, 1)  # nouveau (niveau 0, date_prochaine None)
    _make_chap(session, m, 2, niveau=1, date_prochaine=today, maitrise_pct=20)
    _make_chap(session, m, 3, niveau=1, date_prochaine=today - timedelta(days=2), maitrise_pct=10)
    _make_chap(session, m, 4, niveau=3, date_prochaine=today + timedelta(days=5), maitrise_pct=60)

    stats = stats_matiere(session, m)

    assert stats["nb_chapitres"] == 4
    assert stats["nb_nouveaux"] == 1
    assert stats["nb_revisions_dues"] == 2  # today + retard
    assert stats["nb_revisions_en_retard"] == 1
    # (0 + 20 + 10 + 60) / 4 = 22.5
    assert stats["maitrise_moyenne_pct"] == 22.5


def test_stats_matiere_vide_renvoie_zeros(session):
    """Une matière sans chapitre renvoie tous les compteurs à 0."""
    m = _make_matiere(session, "Vide")
    stats = stats_matiere(session, m)
    assert stats == {
        "nb_chapitres": 0,
        "nb_nouveaux": 0,
        "nb_revisions_dues": 0,
        "nb_revisions_en_retard": 0,
        "maitrise_moyenne_pct": 0.0,
    }


# ---------------------------------------------------------------------------
# 2. matieres_avec_revisions_dues
# ---------------------------------------------------------------------------
def test_matieres_avec_revisions_dues_filtre_correctement(session):
    """Seules les matières ayant au moins un chapitre dû ≤ aujourd'hui sont
    listées. Une matière dont la révision tombe demain n'est PAS pré-sélectionnée."""
    today = date.today()
    m_due = _make_matiere(session, "À réviser")
    m_future = _make_matiere(session, "Plus tard")
    m_vide = _make_matiere(session, "Vide")

    _make_chap(session, m_due, 1, niveau=1, date_prochaine=today)
    _make_chap(session, m_future, 1, niveau=2, date_prochaine=today + timedelta(days=3))

    ids = matieres_avec_revisions_dues(session)
    assert m_due.id in ids
    assert m_future.id not in ids
    assert m_vide.id not in ids


# ---------------------------------------------------------------------------
# 3. estimer_charge_minutes
# ---------------------------------------------------------------------------
def test_estimer_charge_minutes_somme_les_temps_estimes(session):
    """Pour 3 chapitres cochés (1.5h + 0.5h + 2h), la charge totale doit
    être 240 minutes."""
    m = _make_matiere(session, "Physique")
    ch1 = _make_chap(session, m, 1, temps_estime_h=1.5)
    ch2 = _make_chap(session, m, 2, temps_estime_h=0.5)
    ch3 = _make_chap(session, m, 3, temps_estime_h=2.0)

    selection = [{"matiere_id": m.id, "chapitre_ids": [ch1.id, ch2.id, ch3.id]}]
    assert estimer_charge_minutes(session, selection) == 240


def test_estimer_charge_minutes_ignore_selection_sans_chapitres(session):
    """Une matière sans ``chapitre_ids`` explicites compte pour 0 — son
    temps sera estimé par l'IA au moment de la génération."""
    m = _make_matiere(session, "Maths")
    _make_chap(session, m, 1, temps_estime_h=3.0)

    selection_vide = [{"matiere_id": m.id, "chapitre_ids": []}]
    assert estimer_charge_minutes(session, selection_vide) == 0

    assert estimer_charge_minutes(session, None) == 0
    assert estimer_charge_minutes(session, []) == 0
