"""Tests pour `services/gamification_service`.

Couvre :
  - Courbe XP → niveau (formule quadratique)
  - Logique streak (init, continuation, reset, noop)
  - Multiplicateur XP par streak (capé à 3×)
  - Bug DDD : les attributs de gamification sont sur GamificationState,
    pas sur Utilisateur (verrouille le fix du commit précédent).
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import (
    BiometrieConfig,
    GamificationState,
    LogistiqueConfig,
    SystemeConfig,
    Utilisateur,
)
from services.gamification_service import (
    NIVEAU_MAX,
    STREAK_MULTIPLICATEUR_CAP,
    STREAK_MULTIPLICATEUR_PAR_JOUR,
    calcul_multiplicateur_streak,
    calculer_niveau_pour_xp,
    progression_niveau,
    update_streak,
    xp_total_pour_niveau,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture
def utilisateur(session):
    """Crée un utilisateur avec ses 4 sous-configs."""
    u = Utilisateur(nom="Test")
    session.add(u)
    session.flush()
    g = GamificationState(utilisateur_id=u.id)
    session.add_all([
        g,
        BiometrieConfig(utilisateur_id=u.id),
        LogistiqueConfig(utilisateur_id=u.id),
        SystemeConfig(utilisateur_id=u.id),
    ])
    session.commit()
    session.refresh(u)
    return u


# ===========================================================================
# Courbe XP → niveau
# ===========================================================================
def test_xp_niveau_1_egal_0():
    assert xp_total_pour_niveau(1) == 0


def test_xp_niveau_2_egal_100():
    """Formule : 50 × (n-1) × n. n=2 → 50×1×2 = 100."""
    assert xp_total_pour_niveau(2) == 100


def test_xp_niveau_5():
    """50 × 4 × 5 = 1000."""
    assert xp_total_pour_niveau(5) == 1000


def test_xp_niveau_borne_a_niveau_max():
    assert xp_total_pour_niveau(NIVEAU_MAX + 10) == xp_total_pour_niveau(NIVEAU_MAX)


def test_calculer_niveau_pour_xp_zero():
    assert calculer_niveau_pour_xp(0) == 1


def test_calculer_niveau_pour_xp_negatif():
    assert calculer_niveau_pour_xp(-50) == 1


def test_calculer_niveau_pour_xp_99_reste_niveau_1():
    assert calculer_niveau_pour_xp(99) == 1


def test_calculer_niveau_pour_xp_100_passe_niveau_2():
    assert calculer_niveau_pour_xp(100) == 2


def test_calculer_niveau_pour_xp_999_reste_niveau_4():
    assert calculer_niveau_pour_xp(999) == 4


def test_calculer_niveau_pour_xp_1000_passe_niveau_5():
    assert calculer_niveau_pour_xp(1000) == 5


def test_progression_niveau_milieu_palier():
    """À 250 XP : niveau 3 (palier 300 inatteint), ratio = (250-100)/200."""
    p = progression_niveau(250)
    assert p["niveau"] == 2
    assert p["xp_palier"] == 100
    assert p["xp_suivant"] == 300
    assert p["xp_dans_palier"] == 150
    assert 0 < p["ratio"] < 1


def test_progression_niveau_max():
    big = xp_total_pour_niveau(NIVEAU_MAX) + 1000
    p = progression_niveau(big)
    assert p["niveau"] == NIVEAU_MAX
    assert p["max_atteint"] is True
    assert p["ratio"] == 1.0


# ===========================================================================
# Streak
# ===========================================================================
def test_streak_init_premiere_activite(utilisateur):
    res = update_streak(utilisateur, today=dt.date(2026, 1, 10))
    assert res["streak"] == 1
    assert res["change"] == "init"
    assert res["record_battu"] is True
    assert utilisateur.gamification.streak_jours == 1
    assert utilisateur.gamification.derniere_activite_xp == dt.date(2026, 1, 10)


def test_streak_continuation_lendemain(utilisateur):
    utilisateur.gamification.derniere_activite_xp = dt.date(2026, 1, 10)
    utilisateur.gamification.streak_jours = 3
    utilisateur.gamification.streak_record = 5

    res = update_streak(utilisateur, today=dt.date(2026, 1, 11))
    assert res["streak"] == 4
    assert res["change"] == "cont"
    assert res["record_battu"] is False  # 4 < 5


def test_streak_record_battu(utilisateur):
    utilisateur.gamification.derniere_activite_xp = dt.date(2026, 1, 10)
    utilisateur.gamification.streak_jours = 5
    utilisateur.gamification.streak_record = 5

    res = update_streak(utilisateur, today=dt.date(2026, 1, 11))
    assert res["streak"] == 6
    assert res["record_battu"] is True
    assert utilisateur.gamification.streak_record == 6


def test_streak_reset_si_trou_2_jours(utilisateur):
    utilisateur.gamification.derniere_activite_xp = dt.date(2026, 1, 10)
    utilisateur.gamification.streak_jours = 10
    utilisateur.gamification.streak_record = 10

    res = update_streak(utilisateur, today=dt.date(2026, 1, 13))  # +3 jours
    assert res["streak"] == 1
    assert res["change"] == "reset"
    assert res["record_battu"] is False
    # Record précédent conservé
    assert utilisateur.gamification.streak_record == 10


def test_streak_noop_meme_jour(utilisateur):
    utilisateur.gamification.derniere_activite_xp = dt.date(2026, 1, 10)
    utilisateur.gamification.streak_jours = 7

    res = update_streak(utilisateur, today=dt.date(2026, 1, 10))
    assert res["streak"] == 7
    assert res["change"] == "noop"
    # Le streak n'a pas été modifié
    assert utilisateur.gamification.streak_jours == 7


# ===========================================================================
# Multiplicateur de streak
# ===========================================================================
def test_multiplicateur_streak_1_egal_1():
    assert calcul_multiplicateur_streak(1) == 1.0


def test_multiplicateur_streak_2_au_dessus_de_1():
    """Streak 2 = 1.1^1 = 1.1."""
    assert calcul_multiplicateur_streak(2) == pytest.approx(1.1, rel=1e-6)


def test_multiplicateur_streak_capi_a_3():
    """Avec base 1.1, le cap 3.0 est atteint vers streak 12-13."""
    assert calcul_multiplicateur_streak(100) == STREAK_MULTIPLICATEUR_CAP
    assert calcul_multiplicateur_streak(50) == STREAK_MULTIPLICATEUR_CAP


def test_multiplicateur_strictement_croissant_avant_cap():
    seuils = [calcul_multiplicateur_streak(n) for n in range(1, 10)]
    for a, b in zip(seuils, seuils[1:]):
        assert b >= a  # croissance monotone


# ===========================================================================
# Verrou du fix DDD (anti-régression critique)
# ===========================================================================
def test_streak_lit_et_ecrit_sur_gamification_pas_sur_utilisateur(utilisateur):
    """Anti-régression : `streak_jours` doit aller via la relation
    `utilisateur.gamification`, pas être un attribut de `Utilisateur`."""
    # Avant le fix, ces lignes plantaient en AttributeError.
    update_streak(utilisateur, today=dt.date(2026, 1, 5))
    assert utilisateur.gamification.streak_jours == 1
    # Confirme qu'aucun attribut parasite n'a été posé sur Utilisateur
    assert "streak_jours" not in utilisateur.__dict__
