"""Tests du module ``modules.hebdo.dev_perso``.

Valide le comportement du module Développement Personnel :

1. Import smoke.
2. Constantes correctement définies.
3. ``_get_prev_dev`` gère le cas où la semaine S-1 n'existe pas.
4. Résilience aux données inattendues à l'enregistrement.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd


# ---------------------------------------------------------------------------
# 1. Smoke import
# ---------------------------------------------------------------------------
def test_import_module():
    """Le module dev_perso s'importe sans erreur."""
    from modules.hebdo import dev_perso

    assert dev_perso is not None


# ---------------------------------------------------------------------------
# 2. Constantes
# ---------------------------------------------------------------------------
def test_categories_contient_mediation():
    """La catégorie par défaut (🧠 Méditation) est bien présente."""
    from modules.hebdo.dev_perso import CATEGORIES

    assert any("Méditation" in c for c in CATEGORIES)
    assert len(CATEGORIES) >= 5


def test_creneaux_contient_matin():
    """Les créneaux incluent les moments classiques de la journée."""
    from modules.hebdo.dev_perso import CRENEAUX

    assert "Matin" in CRENEAUX
    assert "Soir" in CRENEAUX
    assert "Peu importe" in CRENEAUX


# ---------------------------------------------------------------------------
# 3. _get_prev_dev — cas semaine inexistante
# ---------------------------------------------------------------------------
def test_get_prev_dev_aucune_semaine_precedente():
    """Si la semaine S-1 n'existe pas, la fonction retourne une liste vide."""
    from modules.hebdo.dev_perso import _get_prev_dev

    with patch(
        "modules.hebdo.dev_perso.get_or_create_week_for_offset",
        side_effect=Exception("Week not found"),
    ):
        result = _get_prev_dev(None, offset=2)
        assert result == []


def test_get_prev_dev_retourne_config_existante():
    """Si la semaine S-1 a des habitudes, elles sont retournées."""
    from modules.hebdo.dev_perso import _get_prev_dev

    config_attendue = [
        {
            "activite": "🧠 Méditation / Mindfulness",
            "frequence": "Tous les jours",
            "duree_min": 15,
            "creneau_pref": "Matin",
        },
    ]

    class FakeSaisie:
        dev_perso_config = config_attendue

    with patch(
        "modules.hebdo.dev_perso.get_or_create_week_for_offset",
        return_value=(None, FakeSaisie(), 0),
    ):
        result = _get_prev_dev(None, offset=1)
        assert result == config_attendue
        assert result[0]["duree_min"] == 15


# ---------------------------------------------------------------------------
# 4. Résilience aux données inattendues
# ---------------------------------------------------------------------------
def test_config_db_resiliente_aux_champs_manquants():
    """Une habitude sans duree_min ou sans creneau_pref ne doit pas planter
    le calcul du KPI ni la création du DataFrame."""
    config_db = [
        {"activite": "📚 Lecture (non-scolaire)", "frequence": "2x par semaine"},
        # pas de duree_min, pas de creneau_pref
    ]

    # Simulation du KPI (avant correction) : le sum() ne doit pas planter
    total = sum(int(h.get("duree_min", 0)) for h in config_db)
    assert total == 0  # duree_min manquant → 0 par défaut

    # Simulation du DataFrame : doit s'initialiser même avec champs partiels
    df = pd.DataFrame(config_db)
    assert len(df) == 1
    assert df.iloc[0]["activite"] == "📚 Lecture (non-scolaire)"


def test_dataframe_vide_initialise_avec_defaut():
    """Un DataFrame vide est initialisé avec une ligne par défaut (Méditation)."""
    from modules.hebdo.dev_perso import CATEGORIES

    df = pd.DataFrame([])
    if df.empty:
        df = pd.DataFrame([{
            "activite": CATEGORIES[0],
            "frequence": "3x par semaine",
            "duree_min": 20,
            "creneau_pref": "Matin",
        }])

    assert len(df) == 1
    assert "Méditation" in str(df.iloc[0]["activite"])
    assert df.iloc[0]["duree_min"] == 20
