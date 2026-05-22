"""Tests du lissage automatique des dates Leitner.

``services.revision_service.lisser_automatiquement_dates_leitner``
résout deux types de conflits dans la file de révision espacée :

1. Max 1 nouveau chapitre par matière par jour (niveau Leitner 0).
2. Plafond horaire journalier (en minutes).

La tolérance ±N jours borne le décalage : si aucun jour libre n'est
trouvé dans la fenêtre, le chapitre reste à sa date d'origine et est
listé dans ``non_decalables``.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import Chapitre, Matiere
from services.revision_service import (
    DUREE_REVISION_MIN,
    lisser_automatiquement_dates_leitner,
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


def _make_chap(
    session,
    matiere: Matiere,
    numero: int,
    *,
    niveau: int = 0,
    date_prochaine: date | None = None,
) -> Chapitre:
    ch = Chapitre(
        matiere_id=matiere.id,
        numero=numero,
        titre=f"{matiere.nom} - Chap {numero}",
        niveau_actuel=niveau,
        date_prochaine=date_prochaine,
    )
    session.add(ch)
    session.commit()
    return ch


# ---------------------------------------------------------------------------
# Règle 1 — Max 1 nouveau par matière par jour
# ---------------------------------------------------------------------------
def test_lissage_etale_les_nouveaux_doublons_meme_matiere(session):
    """Le scénario exact qu'a montré l'utilisateur : 3 chapitres niveau 0
    d'une même matière tous prévus demain → on en garde 1 demain, on
    décale les 2 autres sur les jours suivants dans la tolérance."""
    today = date.today()
    demain = today + timedelta(days=1)

    m = Matiere(nom="Physique")
    session.add(m)
    session.commit()
    chaps = [
        _make_chap(session, m, i + 1, date_prochaine=demain)
        for i in range(3)
    ]

    resultat = lisser_automatiquement_dates_leitner(
        session, tolerance_jours=3, today=today,
    )
    session.commit()

    # Recharge les chapitres après lissage.
    session.expire_all()
    chaps_recharges = [session.get(Chapitre, c.id) for c in chaps]
    dates_finales = sorted(c.date_prochaine for c in chaps_recharges)

    assert dates_finales == [demain, demain + timedelta(days=1), demain + timedelta(days=2)]
    assert resultat["nb_decalages"] == 2
    assert resultat["nb_non_decalables"] == 0


def test_lissage_respecte_la_tolerance_et_marque_non_decalable(session):
    """Si plus de chapitres qu'il n'y a de jours libres dans la
    tolérance, ceux qu'on ne peut pas placer restent à leur date
    d'origine et sont listés dans non_decalables."""
    today = date.today()
    demain = today + timedelta(days=1)

    m = Matiere(nom="Maths")
    session.add(m)
    session.commit()
    # 6 chapitres, tolérance 3 → on a 4 slots (demain, +1, +2, +3) → 2 non décalables.
    chaps = [
        _make_chap(session, m, i + 1, date_prochaine=demain)
        for i in range(6)
    ]

    resultat = lisser_automatiquement_dates_leitner(
        session, tolerance_jours=3, today=today,
    )
    session.commit()

    assert resultat["nb_decalages"] == 3   # 3 sur 5 doublons trouvent un jour libre
    assert resultat["nb_non_decalables"] == 2

    # Vérifie qu'aucun chapitre n'a une date au-delà de la tolérance.
    session.expire_all()
    for c in chaps:
        ch = session.get(Chapitre, c.id)
        assert (ch.date_prochaine - demain).days <= 3


def test_lissage_n_etale_pas_les_matieres_differentes(session):
    """Deux chapitres de matières différentes le même jour ne sont pas
    un doublon (la règle est par matière)."""
    today = date.today()
    demain = today + timedelta(days=1)

    m_a = Matiere(nom="Physique")
    m_b = Matiere(nom="Maths")
    session.add_all([m_a, m_b])
    session.commit()
    ch_a = _make_chap(session, m_a, 1, date_prochaine=demain)
    ch_b = _make_chap(session, m_b, 1, date_prochaine=demain)

    resultat = lisser_automatiquement_dates_leitner(
        session, tolerance_jours=3, today=today,
    )
    session.commit()

    assert resultat["nb_decalages"] == 0
    session.expire_all()
    assert session.get(Chapitre, ch_a.id).date_prochaine == demain
    assert session.get(Chapitre, ch_b.id).date_prochaine == demain


def test_lissage_ignore_les_chapitres_deja_avances(session):
    """La règle « 1 nouveau / matière / jour » s'applique aux chapitres
    de niveau 0. Les niveaux > 0 (déjà commencés) ne déclenchent pas
    de décalage règle 1 même si plusieurs sont le même jour."""
    today = date.today()
    demain = today + timedelta(days=1)

    m = Matiere(nom="Info")
    session.add(m)
    session.commit()
    # Tous niveau 3 → la règle 1 ne s'applique pas.
    chaps = [
        _make_chap(session, m, i + 1, niveau=3, date_prochaine=demain)
        for i in range(3)
    ]

    resultat = lisser_automatiquement_dates_leitner(
        session, tolerance_jours=3, today=today,
    )
    session.commit()
    assert resultat["nb_decalages"] == 0
    session.expire_all()
    for c in chaps:
        assert session.get(Chapitre, c.id).date_prochaine == demain


# ---------------------------------------------------------------------------
# Règle 2 — Plafond horaire journalier
# ---------------------------------------------------------------------------
def test_lissage_decale_quand_plafond_depasse(session):
    """5 révisions de 30 min = 150 min un même jour. Plafond = 60 min →
    on doit décaler au moins 3 chapitres pour passer sous le plafond."""
    today = date.today()
    demain = today + timedelta(days=1)

    m = Matiere(nom="Bio")
    session.add(m)
    session.commit()
    # 5 chapitres déjà avancés (niveau 3, donc règle 1 ne s'applique pas).
    chaps = [
        _make_chap(session, m, i + 1, niveau=3, date_prochaine=demain)
        for i in range(5)
    ]

    resultat = lisser_automatiquement_dates_leitner(
        session,
        tolerance_jours=3,
        plafond_minutes_par_jour=60,  # max 2 révisions/jour à 30 min
        today=today,
    )
    session.commit()

    assert resultat["nb_decalages"] >= 3  # il faut au moins 3 décalages

    # Vérifie qu'aucun jour de la fenêtre n'a > 2 révisions.
    session.expire_all()
    par_jour: dict[date, int] = {}
    for c in chaps:
        ch = session.get(Chapitre, c.id)
        par_jour[ch.date_prochaine] = par_jour.get(ch.date_prochaine, 0) + 1

    for jour, nb in par_jour.items():
        assert nb * DUREE_REVISION_MIN <= 60, f"{jour}: {nb} chapitres > plafond"
