"""Tests du backfill `Tache.duree_min`.

Vérifie que `database.db._backfill_duree_min` répare les tâches legacy
créées avant le fix de `_save_planning_to_db` (qui n'écrivait pas
duree_min à l'insert).
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import Semaine, Tache
import database.db as db_mod


@pytest.fixture
def engine_in_memory(monkeypatch):
    """Engine in-memory + monkey-patch sur le module."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "SessionLocal", SessionLocal)
    yield engine, SessionLocal
    engine.dispose()


def _make_semaine_with_tache(SessionLocal, **tache_overrides) -> int:
    """Helper : crée une semaine + une tâche, retourne l'id de la tâche."""
    s = SessionLocal()
    sem = Semaine(
        numero_semaine=1, annee=2026,
        date_debut=dt.date(2026, 1, 5),
        date_fin=dt.date(2026, 1, 11),
        statut="generee",
    )
    s.add(sem)
    s.commit()
    base = {
        "semaine_id": sem.id,
        "type": "etude",
        "titre": "Test",
        "jour": "lundi",
        "heure_debut": dt.time(8, 30),
        "heure_fin": dt.time(10, 0),
        "duree_min": 0,  # legacy : pas calculé à l'insert
        "statut": "a_faire",
    }
    base.update(tache_overrides)
    t = Tache(**base)
    s.add(t)
    s.commit()
    tid = t.id
    s.close()
    return tid


def test_backfill_legacy_duree_min_zero(engine_in_memory):
    engine, SessionLocal = engine_in_memory
    tid = _make_semaine_with_tache(SessionLocal, duree_min=0)

    nb = db_mod._backfill_duree_min(verbose=False)
    assert nb == 1

    s = SessionLocal()
    t = s.get(Tache, tid)
    assert t.duree_min == 90  # 10:00 - 08:30
    s.close()


def test_backfill_ne_touche_pas_les_valeurs_correctes(engine_in_memory):
    """Les tâches déjà bien renseignées ne doivent pas être modifiées."""
    engine, SessionLocal = engine_in_memory
    tid = _make_semaine_with_tache(SessionLocal, duree_min=42)

    nb = db_mod._backfill_duree_min(verbose=False)
    assert nb == 0  # rien à backfiller

    s = SessionLocal()
    t = s.get(Tache, tid)
    assert t.duree_min == 42  # inchangé
    s.close()


def test_backfill_idempotent(engine_in_memory):
    """Deux appels successifs : le 2e ne touche rien (les lignes
    backfillées au 1er appel ont maintenant duree_min > 0)."""
    engine, SessionLocal = engine_in_memory
    _make_semaine_with_tache(SessionLocal, duree_min=0)

    nb1 = db_mod._backfill_duree_min(verbose=False)
    nb2 = db_mod._backfill_duree_min(verbose=False)
    assert nb1 == 1
    assert nb2 == 0


def test_backfill_db_sans_table_ne_plante_pas(monkeypatch):
    """Sur une DB fraîche sans table `taches`, le backfill doit retourner 0."""
    engine = create_engine("sqlite:///:memory:")
    # Pas de Base.metadata.create_all → table absente
    monkeypatch.setattr(db_mod, "engine", engine)
    nb = db_mod._backfill_duree_min(verbose=False)
    assert nb == 0
    engine.dispose()
