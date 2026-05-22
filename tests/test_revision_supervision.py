"""Tests des helpers de supervision Méthode des J (revision_service).

Sécurise les 4 nouveaux helpers utilisés par ``pages/revisions.py`` :

- ``repartition_par_niveau`` compte correctement les chapitres dans
  chaque boîte Leitner.
- ``chapitres_par_jour_futur`` agrège par date et rapatrie les retards
  sur ``today``.
- ``dette_revision`` ne retourne que les chapitres strictement en retard,
  triés par ancienneté.
- ``chapitres_jamais_initialises`` isole ceux sans ``date_prochaine``.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import Chapitre, Matiere
from services.revision_service import (
    chapitres_jamais_initialises,
    chapitres_par_jour_futur,
    dette_revision,
    repartition_par_niveau,
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
# 1. repartition_par_niveau
# ---------------------------------------------------------------------------
def test_repartition_par_niveau_compte_par_boite_leitner(session):
    m = _make_matiere(session, "Maths")
    _make_chap(session, m, 1, niveau=0)
    _make_chap(session, m, 2, niveau=0)
    _make_chap(session, m, 3, niveau=2)
    _make_chap(session, m, 4, niveau=5)

    repartition = repartition_par_niveau(session)
    assert repartition.get(0) == 2
    assert repartition.get(2) == 1
    assert repartition.get(5) == 1


# ---------------------------------------------------------------------------
# 2. chapitres_par_jour_futur
# ---------------------------------------------------------------------------
def test_chapitres_par_jour_futur_rapatrie_les_retards_sur_today(session):
    """Un chapitre dont ``date_prochaine`` est passée doit apparaître sur
    la clé ``today`` (à traiter en priorité)."""
    today = date.today()
    m = _make_matiere(session, "Physique")
    chap_retard = _make_chap(session, m, 1, niveau=1, date_prochaine=today - timedelta(days=5))
    chap_today = _make_chap(session, m, 2, niveau=1, date_prochaine=today)
    chap_demain = _make_chap(session, m, 3, niveau=1, date_prochaine=today + timedelta(days=1))

    result = chapitres_par_jour_futur(session, days_ahead=14, today=today)
    ids_today = {c.id for c in result.get(today, [])}
    ids_demain = {c.id for c in result.get(today + timedelta(days=1), [])}

    assert chap_retard.id in ids_today
    assert chap_today.id in ids_today
    assert chap_demain.id in ids_demain
    # Le retard ne doit PAS être dupliqué à sa date d'origine.
    assert (today - timedelta(days=5)) not in result


def test_chapitres_par_jour_futur_respecte_horizon(session):
    """Les chapitres au-delà de ``days_ahead`` ne sont pas inclus."""
    today = date.today()
    m = _make_matiere(session, "Info")
    _make_chap(session, m, 1, niveau=1, date_prochaine=today + timedelta(days=20))
    _make_chap(session, m, 2, niveau=1, date_prochaine=today + timedelta(days=40))

    result = chapitres_par_jour_futur(session, days_ahead=28, today=today)
    total = sum(len(v) for v in result.values())
    assert total == 1  # seul le J+20 est dans l'horizon de 28 jours.


def test_chapitres_par_jour_futur_filtre_par_matiere(session):
    """Si ``matiere_ids`` est fourni, seuls ces chapitres remontent."""
    today = date.today()
    m_a = _make_matiere(session, "A")
    m_b = _make_matiere(session, "B")
    chap_a = _make_chap(session, m_a, 1, niveau=1, date_prochaine=today)
    _make_chap(session, m_b, 1, niveau=1, date_prochaine=today)

    result = chapitres_par_jour_futur(
        session, days_ahead=7, today=today, matiere_ids=[m_a.id]
    )
    assert all(c.id == chap_a.id for chaps in result.values() for c in chaps)


# ---------------------------------------------------------------------------
# 3. dette_revision
# ---------------------------------------------------------------------------
def test_dette_revision_strict_passe_et_trie(session):
    """Seuls les chapitres dont date_prochaine < today sont en dette,
    triés du plus ancien au plus récent."""
    today = date.today()
    m = _make_matiere(session, "Maths")
    chap_aujourdhui = _make_chap(session, m, 1, niveau=1, date_prochaine=today)
    chap_hier = _make_chap(session, m, 2, niveau=1, date_prochaine=today - timedelta(days=1))
    chap_avant_hier = _make_chap(session, m, 3, niveau=1, date_prochaine=today - timedelta(days=10))

    dette = dette_revision(session, today=today)
    ids = [c.id for c in dette]

    assert chap_aujourdhui.id not in ids  # today n'est pas un retard.
    assert ids[0] == chap_avant_hier.id  # le plus en retard d'abord.
    assert ids[1] == chap_hier.id


# ---------------------------------------------------------------------------
# 4. chapitres_jamais_initialises
# ---------------------------------------------------------------------------
def test_chapitres_jamais_initialises_isole_les_sans_date(session):
    today = date.today()
    m = _make_matiere(session, "Bio")
    chap_neuf = _make_chap(session, m, 1, niveau=0, date_prochaine=None)
    _make_chap(session, m, 2, niveau=1, date_prochaine=today)

    result = chapitres_jamais_initialises(session)
    assert [c.id for c in result] == [chap_neuf.id]
