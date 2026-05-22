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
    DUREE_REVISION_MIN,
    INTERVALLES_J,
    MAX_NIVEAU,
    chapitres_jamais_initialises,
    chapitres_par_jour_futur,
    detecter_conflits_jour,
    dette_revision,
    projeter_chapitre,
    projeter_tous_chapitres,
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


# ---------------------------------------------------------------------------
# 5. projeter_chapitre (projection optimiste J1 → J3 → J7 → …)
# ---------------------------------------------------------------------------
def test_projeter_chapitre_genere_la_sequence_attendue():
    """Démarrage à niveau 0 + date_prochaine = J+1 : la projection
    optimiste doit générer J1, J3, J7, J14, J30… aux dates correctes."""
    today = date.today()
    date_depart = today + timedelta(days=INTERVALLES_J[0])  # demain (J+1)
    horizon = today + timedelta(days=100)

    proj = projeter_chapitre(
        niveau_actuel=0, date_prochaine=date_depart, horizon=horizon,
    )

    # Doit contenir au moins J1, J1+3, J1+3+7, J1+3+7+14 dans la fenêtre.
    dates = [d for d, _ in proj]
    niveaux = [n for _, n in proj]

    assert dates[0] == today + timedelta(days=1)        # J1 (niveau passe 0 → 1)
    assert niveaux[0] == 1
    assert dates[1] == today + timedelta(days=1 + 3)    # J+3 (niveau 2)
    assert niveaux[1] == 2
    assert dates[2] == today + timedelta(days=1 + 3 + 7)   # J+7 (niveau 3)
    assert dates[3] == today + timedelta(days=1 + 3 + 7 + 14)  # J+14 (niveau 4)


def test_projeter_chapitre_respecte_horizon():
    """Les dates au-delà de l'horizon ne sont pas incluses."""
    today = date.today()
    proj = projeter_chapitre(
        niveau_actuel=0,
        date_prochaine=today + timedelta(days=1),
        horizon=today + timedelta(days=5),
    )
    # J+1 (OK) + J+4 (= 1+3, OK) + J+11 (> 5 → exclus).
    assert len(proj) == 2
    assert proj[0][0] == today + timedelta(days=1)
    assert proj[1][0] == today + timedelta(days=4)


def test_projeter_chapitre_capte_le_niveau_max():
    """Une fois MAX_NIVEAU atteint, on arrête la projection."""
    today = date.today()
    proj = projeter_chapitre(
        niveau_actuel=MAX_NIVEAU - 1,
        date_prochaine=today + timedelta(days=1),
        horizon=today + timedelta(days=99999),  # horizon volontairement énorme
    )
    # Le chapitre atteint MAX_NIVEAU à sa prochaine révision → exactement 1 entrée.
    assert len(proj) == 1
    assert proj[0][1] == MAX_NIVEAU


# ---------------------------------------------------------------------------
# 6. projeter_tous_chapitres (agrégation + filtre matière)
# ---------------------------------------------------------------------------
def test_projeter_tous_chapitres_agrege_par_date(session):
    """Deux chapitres avec la même date_prochaine doivent atterrir
    ensemble sur cette clé dans la projection agrégée."""
    today = date.today()
    m = _make_matiere(session, "Maths")
    _make_chap(session, m, 1, niveau=0, date_prochaine=today + timedelta(days=1))
    _make_chap(session, m, 2, niveau=0, date_prochaine=today + timedelta(days=1))

    proj = projeter_tous_chapitres(
        session, horizon_jours=2, today=today,
    )
    items_demain = proj.get(today + timedelta(days=1), [])
    assert len(items_demain) == 2
    assert all(it["niveau_avant"] == 0 for it in items_demain)
    assert all(it["niveau_apres"] == 1 for it in items_demain)


def test_projeter_tous_chapitres_rapatrie_les_retards_sur_today(session):
    """Un chapitre en retard reprend la projection à partir d'aujourd'hui,
    pas à partir de sa date_prochaine passée."""
    today = date.today()
    m = _make_matiere(session, "Maths")
    _make_chap(
        session, m, 1, niveau=0,
        date_prochaine=today - timedelta(days=5),
    )
    proj = projeter_tous_chapitres(session, horizon_jours=30, today=today)
    # La première occurrence doit être today (pas today-5).
    dates = sorted(proj.keys())
    assert dates[0] == today


# ---------------------------------------------------------------------------
# 7. detecter_conflits_jour
# ---------------------------------------------------------------------------
def test_detecter_conflits_charge_horaire_depassee():
    """Si la charge cumulée dépasse le plafond, ``charge_ok`` est False."""
    # 5 révisions de 30 min = 150 min, plafond = 60 min → dépassement.
    projs = [
        {"matiere": f"M{i}", "niveau_avant": 1, "niveau_apres": 2, "duree_min": 30}
        for i in range(5)
    ]
    c = detecter_conflits_jour(projs, plafond_minutes=60)
    assert c["nb_total"] == 5
    assert c["charge_min"] == 150
    assert c["charge_ok"] is False


def test_detecter_conflits_doublon_nouveau_par_matiere():
    """Deux chapitres « nouveaux » (niveau_avant=0) sur la même matière
    le même jour doivent être signalés."""
    projs = [
        {"matiere": "Maths", "niveau_avant": 0, "niveau_apres": 1, "duree_min": 30},
        {"matiere": "Maths", "niveau_avant": 0, "niveau_apres": 1, "duree_min": 30},
        {"matiere": "Physique", "niveau_avant": 0, "niveau_apres": 1, "duree_min": 30},
        {"matiere": "Maths", "niveau_avant": 2, "niveau_apres": 3, "duree_min": 30},  # révision, pas nouveau
    ]
    c = detecter_conflits_jour(projs, plafond_minutes=300)
    assert c["matieres_doublons_nouveaux"] == ["Maths"]
    # Physique n'a qu'un nouveau → pas dans la liste.
    # La révision Maths (niveau_avant=2) ne compte pas comme « nouveau ».
