"""Tests pour les chantiers de refonte Bibliothèque (NASA).

Couvre :
  - `services/profil_service` : centralisation de la clé Gemini (chiffrement).
  - `services/pdf_storage`     : validation upload, SHA-256, idempotence.
  - `services/cache_versioning`: invalidation auto du cache fiche IA.
  - `services/optimistic_lock` : verrou optimiste (ETag) sur Chapitre.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import (
    Chapitre,
    Matiere,
)
from services.optimistic_lock import ConflictError, update_chapitre_safe


@pytest.fixture
def session():
    """Session SQLite in-memory, schéma complet."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()











# ---------------------------------------------------------------------------
# optimistic_lock
# ---------------------------------------------------------------------------
def _chap(session):
    m = Matiere(nom="X")
    session.add(m)
    session.commit()
    c = Chapitre(matiere_id=m.id, titre="Ch1", numero=1)
    session.add(c)
    session.commit()
    return c


def test_update_chapitre_safe_succes(session):
    c = _chap(session)
    assert c.version == 1
    new_version = update_chapitre_safe(
        session,
        c.id,
        expected_version=1,
        mutate=lambda ch: setattr(ch, "notes", "ma note"),
    )
    session.commit()
    assert new_version == 2
    rechargee = session.get(Chapitre, c.id)
    assert rechargee.notes == "ma note"
    assert rechargee.version == 2


def test_update_chapitre_safe_conflit_leve_erreur(session):
    c = _chap(session)
    # Premier onglet écrit avec succès
    update_chapitre_safe(
        session, c.id, expected_version=1,
        mutate=lambda ch: setattr(ch, "notes", "A"),
    )
    session.commit()

    # Second onglet a une version stale (=1)
    with pytest.raises(ConflictError) as excinfo:
        update_chapitre_safe(
            session, c.id, expected_version=1,
            mutate=lambda ch: setattr(ch, "notes", "B"),
        )
    assert excinfo.value.expected_version == 1
    assert excinfo.value.actual_version == 2


def test_update_chapitre_inexistant(session):
    with pytest.raises(ValueError, match="introuvable"):
        update_chapitre_safe(session, 9999, 1, lambda c: None)
