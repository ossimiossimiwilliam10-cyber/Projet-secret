"""Tests pour `services/planner_validator.py`.

Le validateur protège l'app contre les réponses Gemini cabossées sur
le planning hebdomadaire (le pipeline le plus visible côté utilisateur).
"""

from __future__ import annotations

import pytest

from services.planner_validator import (
    JOURS_VALIDES,
    PlanningValidationError,
    TYPES_VALIDES,
    validate_partial_planning,
    validate_planning,
)


def _tache_valide(**overrides) -> dict:
    base = {
        "type": "etude",
        "titre": "Chapitre 3 — Thermodynamique",
        "heure_debut": "08:30",
        "heure_fin": "10:00",
        "obligatoire": False,
        "justification": "Bloc de focus du matin.",
        "chapitre_ids": [12, 13],
    }
    base.update(overrides)
    return base


def _planning_valide(**overrides) -> dict:
    base = {
        "score_realisme": 75,
        "justification_globale": "Semaine raisonnable.",
        "planning": {
            "lundi": [_tache_valide()],
            "mardi": [],
            "mercredi": [],
            "jeudi": [],
            "vendredi": [],
            "samedi": [],
            "dimanche": [],
        },
    }
    base.update(overrides)
    return base


# ===========================================================================
# Cas valides
# ===========================================================================
def test_planning_valide_passe():
    out = validate_planning(_planning_valide())
    assert out["score_realisme"] == 75
    assert out["justification_globale"] == "Semaine raisonnable."
    assert len(out["planning"]["lundi"]) == 1
    assert out["planning"]["lundi"][0]["titre"].startswith("Chapitre")


def test_jours_manquants_remplis_a_vide():
    p = _planning_valide()
    p["planning"] = {"lundi": [_tache_valide()]}  # un seul jour
    out = validate_planning(p)
    for j in JOURS_VALIDES:
        assert j in out["planning"]
        if j != "lundi":
            assert out["planning"][j] == []


def test_score_clampe_a_100():
    out = validate_planning(_planning_valide(score_realisme=999))
    assert out["score_realisme"] == 100


def test_score_clampe_a_0():
    out = validate_planning(_planning_valide(score_realisme=-50))
    assert out["score_realisme"] == 0


def test_score_invalide_devient_0():
    out = validate_planning(_planning_valide(score_realisme="très bien"))
    assert out["score_realisme"] == 0


def test_type_inconnu_devient_autre():
    p = _planning_valide()
    p["planning"]["lundi"][0]["type"] = "meditation_quantique"
    out = validate_planning(p)
    assert out["planning"]["lundi"][0]["type"] == "autre"


def test_chapitre_ids_int_strings_caste():
    p = _planning_valide()
    p["planning"]["lundi"][0]["chapitre_ids"] = ["12", 13, "abc", None]
    out = validate_planning(p)
    # "abc" et None ignorés, "12" castée
    assert out["planning"]["lundi"][0]["chapitre_ids"] == [12, 13]


def test_jour_fantaisiste_ignore():
    p = _planning_valide()
    p["planning"]["lunaredi"] = [_tache_valide()]
    out = validate_planning(p)
    assert "lunaredi" not in out["planning"]
    assert set(out["planning"].keys()) == set(JOURS_VALIDES)


def test_tache_invalide_ignoree_silencieusement():
    p = _planning_valide()
    p["planning"]["mardi"] = [
        _tache_valide(),  # valide
        {"titre": "incomplete"},  # invalide → ignorée
        _tache_valide(heure_debut="14:00", heure_fin="15:00"),  # valide
    ]
    out = validate_planning(p)
    assert len(out["planning"]["mardi"]) == 2


def test_titre_tronque_a_200_chars():
    p = _planning_valide()
    p["planning"]["lundi"][0]["titre"] = "x" * 500
    out = validate_planning(p)
    assert len(out["planning"]["lundi"][0]["titre"]) == 200


# ===========================================================================
# Cas d'erreur — top level
# ===========================================================================
def test_rejette_non_dict():
    with pytest.raises(PlanningValidationError):
        validate_planning("not a dict")


def test_rejette_planning_absent():
    with pytest.raises(PlanningValidationError, match="planning"):
        validate_planning({"score_realisme": 50})


def test_rejette_planning_pas_dict():
    with pytest.raises(PlanningValidationError, match="planning"):
        validate_planning({"planning": "lundi: etude"})


def test_rejette_si_zero_tache_valide_apres_filtre():
    """Toutes les tâches sont invalides → ValueError final."""
    p = {
        "score_realisme": 50,
        "planning": {
            "lundi": [{"junk": True}],
            "mardi": [],
        },
    }
    with pytest.raises(PlanningValidationError, match="aucune tâche"):
        validate_planning(p)


# ===========================================================================
# Validation des heures
# ===========================================================================
def test_heure_format_invalide_rejette_tache():
    p = _planning_valide()
    p["planning"]["mardi"] = [_tache_valide(heure_debut="8h30")]
    out = validate_planning(p)
    assert out["planning"]["mardi"] == []  # tâche silencieusement rejetée


def test_heure_25h_rejette_tache():
    p = _planning_valide()
    p["planning"]["mardi"] = [_tache_valide(heure_debut="25:00")]
    out = validate_planning(p)
    assert out["planning"]["mardi"] == []


def test_heure_fin_avant_debut_rejette():
    p = _planning_valide()
    p["planning"]["mardi"] = [
        _tache_valide(heure_debut="10:00", heure_fin="08:00")
    ]
    out = validate_planning(p)
    assert out["planning"]["mardi"] == []


def test_heure_fin_egale_debut_rejette():
    p = _planning_valide()
    p["planning"]["mardi"] = [
        _tache_valide(heure_debut="10:00", heure_fin="10:00")
    ]
    out = validate_planning(p)
    assert out["planning"]["mardi"] == []


# ===========================================================================
# Validation des champs individuels
# ===========================================================================
def test_titre_vide_rejette():
    p = _planning_valide()
    p["planning"]["mardi"] = [_tache_valide(titre="")]
    out = validate_planning(p)
    assert out["planning"]["mardi"] == []


def test_chapitre_ids_pas_liste_rejette_tache():
    p = _planning_valide()
    p["planning"]["mardi"] = [_tache_valide(chapitre_ids="12,13")]
    out = validate_planning(p)
    assert out["planning"]["mardi"] == []


def test_constants_exportees():
    assert "etude" in TYPES_VALIDES
    assert "lundi" in JOURS_VALIDES
    assert len(JOURS_VALIDES) == 7


# ===========================================================================
# validate_partial_planning (replan + intégration nouveautés)
# ===========================================================================
def _partial_planning_valide() -> dict:
    return {
        "score_realisme": 80,
        "justification_globale": "Replan suite quiz raté.",
        "planning_jours_restants": {
            "jeudi": [_tache_valide()],
            "vendredi": [],
        },
    }


def test_partial_planning_valide():
    out = validate_partial_planning(
        _partial_planning_valide(),
        allowed_jours=["jeudi", "vendredi", "samedi", "dimanche"],
    )
    assert len(out["planning_jours_restants"]["jeudi"]) == 1
    assert out["planning_jours_restants"]["vendredi"] == []


def test_partial_planning_tolere_0_tache_valide():
    """Un replan peut légitimement ne rien recommander (pas d'erreur)."""
    raw = {
        "planning_jours_restants": {"jeudi": [], "vendredi": []},
    }
    out = validate_partial_planning(
        raw, allowed_jours=["jeudi", "vendredi"]
    )
    assert out["planning_jours_restants"]["jeudi"] == []
    assert out["planning_jours_restants"]["vendredi"] == []


def test_partial_planning_filtre_jours_non_autorises():
    """Si l'IA renvoie des jours passés alors qu'on n'en voulait pas."""
    raw = {
        "planning_jours_restants": {
            "lundi": [_tache_valide()],  # jour passé, non autorisé
            "vendredi": [_tache_valide()],  # autorisé
        },
    }
    out = validate_partial_planning(
        raw, allowed_jours=["jeudi", "vendredi"]
    )
    assert "lundi" not in out["planning_jours_restants"]
    assert len(out["planning_jours_restants"]["vendredi"]) == 1


def test_partial_planning_cle_par_defaut():
    """La clé par défaut est `planning_jours_restants`."""
    out = validate_partial_planning(_partial_planning_valide())
    assert "planning_jours_restants" in out


def test_partial_planning_cle_custom():
    raw = {"mon_planning": {"lundi": [_tache_valide()]}}
    out = validate_partial_planning(raw, key="mon_planning")
    assert "mon_planning" in out
    assert len(out["mon_planning"]["lundi"]) == 1


def test_partial_planning_cle_absente_rejette():
    with pytest.raises(PlanningValidationError, match="planning_jours_restants"):
        validate_partial_planning({"score_realisme": 50})
