"""Tests pour `services/objectif_service._valider_strategie`.

Vérifie le nettoyage / bornage des stratégies renvoyées par Gemini :
  - clamp des pondérations [0.5, 3.0]
  - filtrage des chapitres inconnus
  - défaut realisme
  - garde-fou contre les stratégies vides (coquille Gemini)
"""

from __future__ import annotations

import pytest

from services.objectif_service import _valider_strategie


_CHAPITRES = [
    {"id": 1, "titre": "Ch1"},
    {"id": 2, "titre": "Ch2"},
]


def _strategie_valide() -> dict:
    return {
        "realisme": "ambitieux",
        "justification": "Tu es à 40% sur les 2 chapitres, 3 semaines restantes.",
        "heures_total_estimees": 20,
        "heures_par_semaine": 7.0,
        "ponderations_chapitres": {"1": 2.0, "2": 1.5},
        "ordre_priorite": [1, 2],
        "conseils": ["Commence par le chapitre 1", "Fais des QCM"],
    }


def test_strategie_valide_passe():
    out = _valider_strategie(_strategie_valide(), _CHAPITRES)
    assert out["realisme"] == "ambitieux"
    assert out["heures_total_estimees"] == 20
    assert out["ponderations_chapitres"] == {"1": 2.0, "2": 1.5}


def test_clamp_ponderations():
    s = _strategie_valide()
    s["ponderations_chapitres"] = {"1": 99.0, "2": 0.01}
    out = _valider_strategie(s, _CHAPITRES)
    assert out["ponderations_chapitres"]["1"] == 3.0   # clampé au max
    assert out["ponderations_chapitres"]["2"] == 0.5   # clampé au min


def test_ponderations_chapitre_inconnu_filtre():
    s = _strategie_valide()
    s["ponderations_chapitres"] = {"1": 2.0, "999": 2.5}  # 999 n'existe pas
    out = _valider_strategie(s, _CHAPITRES)
    assert "999" not in out["ponderations_chapitres"]
    # Le chapitre 2 manquant reçoit un défaut 1.0
    assert out["ponderations_chapitres"]["2"] == 1.0


def test_realisme_inconnu_devient_realiste():
    s = _strategie_valide()
    s["realisme"] = "magique"
    out = _valider_strategie(s, _CHAPITRES)
    assert out["realisme"] == "realiste"


def test_strategie_vide_leve_erreur():
    """Coquille Gemini : {} → ValueError au lieu d'une stratégie creuse."""
    with pytest.raises(ValueError, match="vide"):
        _valider_strategie({}, _CHAPITRES)


def test_strategie_sans_justification_ni_conseils_ni_heures_leve():
    s = {
        "realisme": "realiste",
        "justification": "",
        "heures_total_estimees": 0,
        "conseils": [],
        "ponderations_chapitres": {},
    }
    with pytest.raises(ValueError, match="vide"):
        _valider_strategie(s, _CHAPITRES)


def test_strategie_avec_juste_justification_passe():
    """Si au moins la justification est présente, ce n'est pas vide."""
    s = {
        "realisme": "realiste",
        "justification": "Diagnostic présent.",
        "heures_total_estimees": 0,
        "conseils": [],
        "ponderations_chapitres": {},
    }
    out = _valider_strategie(s, _CHAPITRES)
    assert out["justification"] == "Diagnostic présent."
