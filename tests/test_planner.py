"""Tests de fumée pour ``services.ai_planner.build_planner_prompt``.

Objectif : figer le contrat du prompt — sections obligatoires présentes,
consignes manuelles injectées, check-in biomécanique reflété — sans
appeler Gemini ni dépendre d'une vraie base SQLite persistée.

Utilise une base SQLite in-memory recréée pour chaque test via la fixture
``session``.
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import CheckInQuotidien, Utilisateur, SaisieHebdo, Semaine
from services.ai_planner import build_planner_prompt


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def session():
    """Base SQLite in-memory fraîche, avec le schéma complet à jour."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _make_minimal_setup(session) -> tuple[Semaine, SaisieHebdo, Utilisateur]:
    """Crée le triplet minimal Semaine + SaisieHebdo + Utilisateur et le persiste."""
    today = date.today()
    semaine = Semaine(
        annee=today.year,
        numero_semaine=today.isocalendar().week,
        date_debut=today,
        date_fin=today + timedelta(days=6),
    )
    session.add(semaine)
    session.flush()

    saisie = SaisieHebdo(semaine_id=semaine.id)
    from database.models import BiometrieConfig, LogistiqueConfig, SystemeConfig, GamificationState
    profil = Utilisateur(
        nom="Test",
        biometrie=BiometrieConfig(
            heure_lever=time(7, 0),
            heure_coucher=time(23, 0),
        ),
        logistique=LogistiqueConfig(
            temps_transport_min=20,
        ),
        systeme=SystemeConfig(
            llm_api_key="fake-key",
            llm_model="gemini-2.5-flash",
        ),
        gamification=GamificationState()
    )
    session.add_all([saisie, profil])
    session.commit()
    return semaine, saisie, profil


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_build_planner_prompt_returns_string_with_xml_blocks(session):
    """Sur un profil minimal, le prompt doit contenir les balises XML
    structurantes de la refonte ainsi que les ingrédients pré-calculés."""
    semaine, saisie, profil = _make_minimal_setup(session)

    prompt = build_planner_prompt(session, semaine, saisie, profil)

    assert isinstance(prompt, str)
    assert len(prompt) > 500
    # Balises XML de la refonte
    for tag in [
        "<MISSION>", "</MISSION>",
        "<CONTRAINTES_VITALES>", "</CONTRAINTES_VITALES>",
        "<LOGISTIQUE_TRAJETS>", "</LOGISTIQUE_TRAJETS>",
        "<PEDAGOGIE_ET_RYTHME>", "</PEDAGOGIE_ET_RYTHME>",
        "<INGREDIENTS_PRE_CALCULES>", "</INGREDIENTS_PRE_CALCULES>",
        "<CONSIGNES_ETUDIANT>", "</CONSIGNES_ETUDIANT>",
        "<FORMAT_SORTIE>", "</FORMAT_SORTIE>",
    ]:
        assert tag in prompt, f"Balise manquante : {tag}"
    # Ingrédients déjà pré-calculés
    assert "QUOTAS D'ÉTUDE" in prompt
    assert "Objectif HEBDOMADAIRE" in prompt
    assert "Plafond JOURNALIER" in prompt
    assert "NOUVEAUX CHAPITRES" in prompt
    assert "RÉVISIONS LEITNER" in prompt


def test_consignes_manuelles_are_injected_when_provided(session):
    """Le texte des consignes manuelles doit apparaître tel quel, et le
    fallback doit s'afficher quand la chaîne est vide."""
    semaine, saisie, profil = _make_minimal_setup(session)

    consigne = "Jeudi je dois finir avant 18h impérativement."
    prompt_avec = build_planner_prompt(
        session, semaine, saisie, profil, consignes_manuelles=consigne
    )
    assert consigne in prompt_avec

    prompt_sans = build_planner_prompt(session, semaine, saisie, profil)
    assert "Aucune consigne exceptionnelle cette semaine." in prompt_sans
    assert consigne not in prompt_sans


def test_checkin_biomecanique_is_reflected_in_prompt(session):
    """Quand un check-in existe pour aujourd'hui, ses valeurs apparaissent
    dans le prompt ; sinon, le fallback explicite est présent."""
    semaine, saisie, profil = _make_minimal_setup(session)

    # Cas 1 : aucun check-in → fallback
    prompt_vide = build_planner_prompt(session, semaine, saisie, profil)
    assert "Aucun check-in pour aujourd'hui." in prompt_vide

    # Cas 2 : check-in posé pour aujourd'hui → valeurs injectées
    session.add(
        CheckInQuotidien(
            date=date.today(),
            fatigue_physique=9,
            charge_mentale=8,
            qualite_sommeil=3,
        )
    )
    session.commit()

    prompt_avec = build_planner_prompt(session, semaine, saisie, profil)
    assert '"fatigue_physique": 9' in prompt_avec
    assert '"charge_mentale": 8' in prompt_avec
    assert '"qualite_sommeil": 3' in prompt_avec
