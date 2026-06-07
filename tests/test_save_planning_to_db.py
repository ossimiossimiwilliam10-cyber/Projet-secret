"""Tests d'intégration `_save_planning_to_db` — bridge Gemini → DB.

Vérifie que le JSON validé par `planner_validator` est correctement
matérialisé en objets `Tache` côté SQLAlchemy. Couvre la régénération
(suppression des anciennes tâches), les champs optionnels, et la
robustesse aux entrées partielles.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import Semaine, Tache
from modules.generation import _save_planning_to_db, _str_to_time
import database.db as db_mod


@pytest.fixture
def setup_engine(monkeypatch):
    """Engine in-memory + monkey-patch sur le session_scope global de
    modules.generation qui utilise database.db.session_scope.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    monkeypatch.setattr(db_mod, "SessionLocal", SessionLocal)
    monkeypatch.setattr(db_mod, "engine", engine)

    yield SessionLocal
    engine.dispose()


def _make_semaine(SessionLocal) -> int:
    """Crée une semaine de test et retourne son id."""
    s = SessionLocal()
    sem = Semaine(
        numero_semaine=1,
        annee=2026,
        date_debut=dt.date(2026, 1, 5),
        date_fin=dt.date(2026, 1, 11),
        statut="en_attente",
    )
    s.add(sem)
    s.commit()
    sid = sem.id
    s.close()
    return sid


def _planning(jour="lundi", **task_overrides) -> dict:
    """Construit un dict planning_json minimaliste mais valide."""
    task = {
        "type": "etude",
        "titre": "Chap 3 — Thermo",
        "heure_debut": "08:30",
        "heure_fin": "10:00",
        "obligatoire": False,
        "justification": "Bloc focus matin.",
        "chapitre_ids": [12, 13],
        **task_overrides,
    }
    return {
        "score_realisme": 80,
        "justification_globale": "Semaine équilibrée.",
        "planning": {jour: [task]},
    }


# ---------------------------------------------------------------------------
# Cas nominal
# ---------------------------------------------------------------------------
def test_save_cree_les_taches(setup_engine):
    sid = _make_semaine(setup_engine)
    nb = _save_planning_to_db(sid, _planning())
    assert nb == 1

    s = setup_engine()
    taches = s.query(Tache).filter_by(semaine_id=sid).all()
    assert len(taches) == 1
    assert taches[0].titre == "Chap 3 — Thermo"
    assert taches[0].jour == "lundi"
    assert taches[0].heure_debut == dt.time(8, 30)
    assert taches[0].heure_fin == dt.time(10, 0)
    assert taches[0].chapitre_ids == [12, 13]
    # Anti-régression : duree_min DOIT être calculée à l'insert pour que
    # les agrégations SQL (sum(Tache.duree_min)) fonctionnent côté Dashboard.
    assert taches[0].duree_min == 90  # 08:30 → 10:00
    s.close()


def test_save_met_a_jour_la_semaine(setup_engine):
    sid = _make_semaine(setup_engine)
    planning = _planning()
    _save_planning_to_db(sid, planning)

    s = setup_engine()
    sem = s.get(Semaine, sid)
    assert sem.statut == "generee"
    s.close()


def test_save_multiple_jours(setup_engine):
    sid = _make_semaine(setup_engine)
    planning = {
        "score_realisme": 80,
        "justification_globale": "",
        "planning": {
            "lundi": [
                {"type": "etude", "titre": "T1", "heure_debut": "08:00",
                 "heure_fin": "09:00", "chapitre_ids": []},
            ],
            "mardi": [
                {"type": "revision", "titre": "T2", "heure_debut": "10:00",
                 "heure_fin": "11:00", "chapitre_ids": [5]},
                {"type": "sport", "titre": "T3", "heure_debut": "18:00",
                 "heure_fin": "19:00", "chapitre_ids": []},
            ],
        },
    }
    nb = _save_planning_to_db(sid, planning)
    assert nb == 3

    s = setup_engine()
    par_jour = {}
    for t in s.query(Tache).filter_by(semaine_id=sid).all():
        par_jour.setdefault(t.jour, []).append(t.titre)
    assert par_jour["lundi"] == ["T1"]
    assert sorted(par_jour["mardi"]) == ["T2", "T3"]
    s.close()


# ---------------------------------------------------------------------------
# Régénération : reset des anciennes tâches
# ---------------------------------------------------------------------------
def test_save_supprime_les_anciennes_taches(setup_engine):
    sid = _make_semaine(setup_engine)
    _save_planning_to_db(sid, _planning(titre="Ancien"))

    s = setup_engine()
    assert s.query(Tache).count() == 1
    assert s.query(Tache).first().titre == "Ancien"
    s.close()

    # Régénération
    _save_planning_to_db(sid, _planning(titre="Nouveau"))

    s = setup_engine()
    taches = s.query(Tache).all()
    assert len(taches) == 1  # l'ancienne a disparu
    assert taches[0].titre == "Nouveau"
    s.close()


# ---------------------------------------------------------------------------
# Robustesse aux entrées partielles
# ---------------------------------------------------------------------------
def test_save_planning_vide_ne_plante_pas(setup_engine):
    sid = _make_semaine(setup_engine)
    planning = {
        "score_realisme": 0,
        "justification_globale": "Rien à planifier.",
        "planning": {},
    }
    nb = _save_planning_to_db(sid, planning)
    assert nb == 0


def test_save_semaine_inexistante_retourne_0(setup_engine):
    setup_engine  # juste pour activer le monkeypatch
    nb = _save_planning_to_db(99999, _planning())
    assert nb == 0


def test_save_tache_avec_heure_invalide_skip(setup_engine):
    """`_save_planning_to_db` a un try/except autour de chaque tâche.
    Une tâche avec heure absente ou cassée ne tue pas le batch."""
    sid = _make_semaine(setup_engine)
    planning = {
        "score_realisme": 50,
        "justification_globale": "",
        "planning": {
            "lundi": [
                # Tâche cassée : heure_debut absente
                {"type": "etude", "titre": "Cassée", "chapitre_ids": []},
                # Tâche valide qui devrait passer
                {"type": "etude", "titre": "OK",
                 "heure_debut": "10:00", "heure_fin": "11:00",
                 "chapitre_ids": []},
            ],
        },
    }
    nb = _save_planning_to_db(sid, planning)
    assert nb == 1  # seule la valide est créée

    s = setup_engine()
    assert s.query(Tache).first().titre == "OK"
    s.close()


# ---------------------------------------------------------------------------
# Helper _str_to_time
# ---------------------------------------------------------------------------
def test_str_to_time_format_standard():
    assert _str_to_time("08:30") == dt.time(8, 30)
    assert _str_to_time("23:59") == dt.time(23, 59)
    assert _str_to_time("00:00") == dt.time(0, 0)
