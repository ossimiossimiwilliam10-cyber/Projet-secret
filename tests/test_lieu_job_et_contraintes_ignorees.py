"""Tests des deux fixes : Job.lieu + contraintes_ignorees.

1. Le champ ``Job.lieu`` est persisté et exposé dans le prompt avec un
   libellé enrichi (📍 lieu) + un champ ``lieu`` séparé pour faciliter
   le matching côté IA.

2. Les contraintes fixes du profil listées dans
   ``saisie.ajustements["contraintes_ignorees"]`` SONT exclues du
   prompt — l'IA ne les voit même pas.
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import Job, Profil, SaisieHebdo, Semaine
from services.ai_planner import build_planner_prompt


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


def _setup_min(session) -> tuple[Semaine, SaisieHebdo, Profil]:
    today = date.today()
    semaine = Semaine(
        annee=today.year, numero_semaine=today.isocalendar().week,
        date_debut=today, date_fin=today + timedelta(days=6),
    )
    session.add(semaine)
    session.flush()
    saisie = SaisieHebdo(semaine_id=semaine.id)
    profil = Profil(
        nom="Test",
        heure_lever=time(7, 0), heure_coucher=time(23, 0),
        temps_transport_min=20,
        trajets_habituels={"Strasbourg-Luxembourg": 150, "Appartement-Fac": 15},
        gemini_api_key="fake",
        gemini_model="gemini-2.5-flash",
    )
    session.add_all([saisie, profil])
    session.commit()
    return semaine, saisie, profil


# ---------------------------------------------------------------------------
# 1. Job.lieu exposé dans le prompt
# ---------------------------------------------------------------------------
def test_job_lieu_est_persiste_et_relisible(session):
    """Le champ ``lieu`` doit pouvoir être stocké et relu."""
    semaine, _, _ = _setup_min(session)
    j = Job(
        semaine_id=semaine.id,
        titre="Magasinier", jour="samedi",
        heure_debut=time(9, 0), heure_fin=time(17, 0),
        lieu="Luxembourg",
    )
    session.add(j)
    session.commit()
    session.expire_all()

    relu = session.query(Job).first()
    assert relu.lieu == "Luxembourg"


def test_job_lieu_apparait_dans_le_prompt(session):
    """Le libellé enrichi du job dans le prompt contient le lieu."""
    semaine, saisie, profil = _setup_min(session)
    session.add(Job(
        semaine_id=semaine.id,
        titre="Magasinier", jour="samedi",
        heure_debut=time(9, 0), heure_fin=time(17, 0),
        lieu="Luxembourg",
    ))
    session.commit()

    prompt = build_planner_prompt(session, semaine, saisie, profil)
    # Libellé enrichi avec le lieu.
    assert "Magasinier" in prompt
    assert "Luxembourg" in prompt
    # La section logistique trajets explique le matching lieu → trajet.
    assert "Strasbourg-Luxembourg" in prompt


def test_job_sans_lieu_garde_le_comportement_actuel(session):
    """Si un job n'a pas de lieu, pas d'icône 📍 dans le libellé."""
    semaine, saisie, profil = _setup_min(session)
    session.add(Job(
        semaine_id=semaine.id,
        titre="Tutorat", jour="mardi",
        heure_debut=time(18, 0), heure_fin=time(20, 0),
        lieu="",
    ))
    session.commit()
    prompt = build_planner_prompt(session, semaine, saisie, profil)
    assert "Tutorat" in prompt
    # Pas d'icône lieu sans lieu défini.
    assert "📍 Tutorat" not in prompt


# ---------------------------------------------------------------------------
# 2. Contraintes fixes ignorées
# ---------------------------------------------------------------------------
def test_contrainte_ignoree_est_absente_du_prompt(session):
    """Si une contrainte fixe est listée dans ``contraintes_ignorees``,
    elle ne doit PAS apparaître dans le prompt envoyé à Gemini."""
    semaine, saisie, profil = _setup_min(session)
    profil.contraintes_fixes = [
        {"jour": "lundi", "heure_debut": "08:00", "heure_fin": "10:00", "libelle": "CM Mathématiques"},
        {"jour": "mardi", "heure_debut": "14:00", "heure_fin": "16:00", "libelle": "Exam de substitution"},
    ]
    saisie.ajustements = {
        "contraintes_ignorees": ["Exam de substitution"],
    }
    session.commit()

    prompt = build_planner_prompt(session, semaine, saisie, profil)

    # La contrainte normale est toujours là.
    assert "CM Mathématiques" in prompt
    # Mais celle ignorée est filtrée.
    assert "Exam de substitution" not in prompt


def test_aucun_filtre_si_liste_ignorees_vide(session):
    """Par défaut, toutes les contraintes fixes apparaissent."""
    semaine, saisie, profil = _setup_min(session)
    profil.contraintes_fixes = [
        {"jour": "lundi", "heure_debut": "08:00", "heure_fin": "10:00", "libelle": "TD Physique"},
    ]
    session.commit()

    prompt = build_planner_prompt(session, semaine, saisie, profil)
    assert "TD Physique" in prompt
