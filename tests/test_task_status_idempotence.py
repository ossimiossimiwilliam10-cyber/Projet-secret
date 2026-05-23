"""Tests anti-régression pour l'idempotence de `_update_task_status`.

Bug initial : toggler une tâche fait → a_faire → fait donnait XP +
bump maîtrise à chaque fois. L'utilisateur pouvait farmer indéfiniment.

Fix vérifié ici :
  1. Whitelist des statuts (rejet de toute valeur inconnue).
  2. XP attribué UNIQUEMENT à la 1re transition vers {fait, partiellement}.
  3. Maitrise_pct boostée UNIQUEMENT à la 1re transition.
  4. Transitions vers a_faire/non_fait ne donnent jamais d'XP.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import (
    BiometrieConfig,
    Chapitre,
    GamificationState,
    LogistiqueConfig,
    Matiere,
    Semaine,
    SystemeConfig,
    Tache,
    Utilisateur,
)
import database.db as db_mod


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(db_mod, "SessionLocal", SessionLocal)
    monkeypatch.setattr(db_mod, "engine", engine)

    # Création d'un utilisateur complet + matière + chapitre + semaine + tâche
    s = SessionLocal()
    u = Utilisateur(nom="Test")
    s.add(u)
    s.flush()
    s.add_all([
        GamificationState(utilisateur_id=u.id),
        BiometrieConfig(utilisateur_id=u.id),
        LogistiqueConfig(utilisateur_id=u.id),
        SystemeConfig(utilisateur_id=u.id),
    ])
    m = Matiere(nom="Algèbre")
    s.add(m)
    s.flush()
    c = Chapitre(matiere_id=m.id, numero=1, titre="Ch1", maitrise_pct=0.0)
    s.add(c)
    sem = Semaine(
        numero_semaine=1, annee=2026,
        date_debut=dt.date(2026, 1, 5),
        date_fin=dt.date(2026, 1, 11),
    )
    s.add(sem)
    s.flush()
    t = Tache(
        semaine_id=sem.id, type="etude", titre="Cours",
        jour="lundi",
        heure_debut=dt.time(8, 0), heure_fin=dt.time(9, 0),
        duree_min=60, statut="a_faire",
        chapitre_ids=[c.id],
    )
    s.add(t)
    s.commit()
    tid = t.id
    cid = c.id
    s.close()
    yield SessionLocal, tid, cid
    engine.dispose()


def test_rejette_statut_inconnu(setup):
    from modules.suivi import _update_task_status
    SessionLocal, tid, cid = setup
    with pytest.raises(ValueError, match="invalide"):
        _update_task_status(tid, "vacances_au_soleil")


def test_premiere_validation_donne_xp(setup):
    from modules.suivi import _update_task_status
    SessionLocal, tid, cid = setup

    gain = _update_task_status(tid, "fait")
    assert gain is not None
    assert gain.xp_gagne > 0

    s = SessionLocal()
    g = s.query(GamificationState).first()
    assert g.xp == gain.xp_gagne  # cumulé en base
    s.close()


def test_toggle_ne_donne_pas_xp_supplementaire(setup):
    """fait → a_faire → fait : seulement 1 attribution d'XP au total."""
    from modules.suivi import _update_task_status
    SessionLocal, tid, cid = setup

    _update_task_status(tid, "fait")
    s = SessionLocal()
    xp_apres_premier = s.query(GamificationState).first().xp
    s.close()

    # Toggle off → on
    _update_task_status(tid, "a_faire")
    gain_2 = _update_task_status(tid, "fait")
    assert gain_2 is None  # pas de gain au 2e fait

    s = SessionLocal()
    xp_final = s.query(GamificationState).first().xp
    s.close()
    assert xp_final == xp_apres_premier  # toujours le même XP


def test_toggle_ne_re_bumpe_pas_la_maitrise(setup):
    """Même règle pour maitrise_pct : pas de farming par toggle."""
    from modules.suivi import _update_task_status
    SessionLocal, tid, cid = setup

    _update_task_status(tid, "fait")
    s = SessionLocal()
    maitrise_apres_premier = s.get(Chapitre, cid).maitrise_pct
    s.close()
    assert maitrise_apres_premier > 0  # bumpée une fois

    # Toggle multiple fois
    for _ in range(5):
        _update_task_status(tid, "a_faire")
        _update_task_status(tid, "fait")

    s = SessionLocal()
    maitrise_finale = s.get(Chapitre, cid).maitrise_pct
    s.close()
    assert maitrise_finale == maitrise_apres_premier


def test_fait_vers_partiellement_pas_de_re_recompense(setup):
    """Transition fait → partiellement (deux statuts récompensés) :
    pas de double XP."""
    from modules.suivi import _update_task_status
    SessionLocal, tid, cid = setup

    _update_task_status(tid, "fait")
    s = SessionLocal()
    xp1 = s.query(GamificationState).first().xp
    s.close()

    gain = _update_task_status(tid, "partiellement")
    assert gain is None  # déjà récompensé

    s = SessionLocal()
    xp2 = s.query(GamificationState).first().xp
    s.close()
    assert xp2 == xp1


def test_a_faire_vers_non_fait_aucun_xp(setup):
    """non_fait n'est pas un statut récompensé."""
    from modules.suivi import _update_task_status
    SessionLocal, tid, cid = setup

    gain = _update_task_status(tid, "non_fait")
    assert gain is None

    s = SessionLocal()
    assert s.query(GamificationState).first().xp == 0
    assert s.get(Chapitre, cid).maitrise_pct == 0.0
    s.close()
