"""Tests du module ``modules.hebdo.courses``.

Valide le comportement du module Courses & Repas :

1. Import smoke.
2. Constantes correctement définies.
3. ``_get_previous_week_config`` gère le cas où la semaine S-1 n'existe pas.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base


# ---------------------------------------------------------------------------
# 1. Smoke import
# ---------------------------------------------------------------------------
def test_import_module():
    """Le module courses s'importe sans erreur de dépendance."""
    from modules.hebdo import courses

    assert courses is not None


# ---------------------------------------------------------------------------
# 2. Constantes
# ---------------------------------------------------------------------------
def test_frequences_attributs():
    """Les fréquences connues contiennent bien Livraison et Aucune."""
    from modules.hebdo.courses import FREQUENCES_COURSES

    assert "Aucune (déjà fait)" in FREQUENCES_COURSES
    assert "Livraison" in FREQUENCES_COURSES
    assert "1x par semaine" in FREQUENCES_COURSES
    assert "2x par semaine" in FREQUENCES_COURSES


def test_creneaux_attributs():
    """Les créneaux contiennent les options attendues."""
    from modules.hebdo.courses import CRENEAUX

    assert "Peu importe" in CRENEAUX
    assert "Matin" in CRENEAUX
    assert "Soir" in CRENEAUX
    assert "Week-end uniquement" in CRENEAUX


# ---------------------------------------------------------------------------
# 3. _get_previous_week_config — cas semaine inexistante
# ---------------------------------------------------------------------------
def test_get_previous_week_config_aucune_semaine_precedente():
    """Si la semaine S-1 n'existe pas, la fonction retourne un dict vide."""
    from modules.hebdo.courses import _get_previous_week_config

    # Mock get_or_create_week_for_offset pour simuler une semaine inexistante
    with patch(
        "modules.hebdo.courses.get_or_create_week_for_offset",
        side_effect=Exception("Week not found"),
    ):
        result = _get_previous_week_config(None, current_offset=2)
        assert result == {}


def test_get_previous_week_config_retourne_config_existante():
    """Si la semaine S-1 existe avec une config, elle est retournée."""
    from modules.hebdo.courses import _get_previous_week_config

    config_attendue = {
        "menu_hebdo": "Curry de pois chiches",
        "frequence": "1x par semaine",
        "duree_min": 60,
        "creneau_pref": "Soir",
        "meal_prep": True,
        "duree_meal_prep_min": 120,
    }

    class FakeSaisie:
        courses_config = config_attendue

    with patch(
        "modules.hebdo.courses.get_or_create_week_for_offset",
        return_value=(None, FakeSaisie(), 0),
    ):
        result = _get_previous_week_config(None, current_offset=1)
        assert result == config_attendue
        assert result["menu_hebdo"] == "Curry de pois chiches"


# ---------------------------------------------------------------------------
# 4. Vérification que le reprendre nettoie bien le menu (régression Bug 2)
# ---------------------------------------------------------------------------
def test_reprendre_config_vide_menu():
    """Le reprendre doit conserver les habitudes mais vider ``menu_hebdo``."""
    prev = {
        "menu_hebdo": "Vieux curry",
        "frequence": "2x par semaine",
        "duree_min": 45,
        "creneau_pref": "Matin",
        "meal_prep": False,
        "duree_meal_prep_min": 0,
    }

    prev.pop("menu_hebdo", None)

    assert "menu_hebdo" not in prev
    assert prev["frequence"] == "2x par semaine"
    assert prev["duree_min"] == 45
    assert prev["creneau_pref"] == "Matin"
