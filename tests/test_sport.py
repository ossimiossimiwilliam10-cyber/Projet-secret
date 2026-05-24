"""Tests du module ``modules.hebdo.sport``.

Sécurise les deux usages clés de l'onglet Sport :

1. Import smoke — le module s'importe sans erreur.
2. ``_compute_stats`` — comptage correct des séances / intensités.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# 1. Smoke import
# ---------------------------------------------------------------------------
def test_import_module():
    """Le module de sport s'importe sans erreur de dépendance."""
    from modules.hebdo import sport

    assert sport is not None


# ---------------------------------------------------------------------------
# 2. _compute_stats
# ---------------------------------------------------------------------------
def test_compute_stats_config_vide():
    """Une config vide (liste ou None-like) renvoie tous les compteurs à 0."""
    from modules.hebdo.sport import _compute_stats

    stats = _compute_stats([])
    assert stats == {
        "total_min": 0,
        "nb_intense": 0,
        "nb_moderee": 0,
        "nb_legere": 0,
        "nb_seances": 0,
    }


def test_compute_stats_distingue_intensites():
    """3 séances de 60 min : 1 légère, 1 modérée, 1 intense → 180 min total."""
    from modules.hebdo.sport import _compute_stats

    config = [
        {
            "type": "🏃‍♂️ Course / Cardio",
            "duree_min": 60,
            "intensite": "🟢 Légère (Récupération active)",
        },
        {
            "type": "🏋️‍♀️ Musculation / Force",
            "duree_min": 60,
            "intensite": "🟡 Modérée (Entraînement classique)",
        },
        {
            "type": "🥊 Boxe / Combat",
            "duree_min": 60,
            "intensite": "🔴 Intense (Sparring / Max PR)",
        },
    ]

    stats = _compute_stats(config)
    assert stats["total_min"] == 180
    assert stats["nb_intense"] == 1
    assert stats["nb_moderee"] == 1
    assert stats["nb_legere"] == 1
    assert stats["nb_seances"] == 3


def test_compute_stats_detecte_intense_via_emoji():
    """Le compteur nb_intense détecte bien l'emoji 🔴 dans l'intensité."""
    from modules.hebdo.sport import _compute_stats

    config = [
        {
            "type": "🥊 Boxe / Combat",
            "duree_min": 90,
            "intensite": "🔴 Intense (Sparring / Max PR)",
        },
        {
            "type": "🏋️‍♀️ Musculation / Force",
            "duree_min": 45,
            "intensite": "🔴 Intense (Sparring / Max PR)",
        },
    ]

    stats = _compute_stats(config)
    assert stats["nb_intense"] == 2
    assert stats["total_min"] == 135
    assert stats["nb_moderee"] == 0
    assert stats["nb_legere"] == 0


def test_compute_stats_alerte_trois_intenses():
    """Avec 3 séances intenses, l'alerte récupération devrait se déclencher
    (vérification du seuil ≥ 3 dans le module appelant)."""
    from modules.hebdo.sport import _compute_stats

    config = [
        {
            "type": "🥊 Boxe / Combat",
            "duree_min": 60,
            "intensite": "🔴 Intense (Sparring / Max PR)",
        },
        {
            "type": "🏋️‍♀️ Musculation / Force",
            "duree_min": 60,
            "intensite": "🔴 Intense (Sparring / Max PR)",
        },
        {
            "type": "🏊‍♂️ Natation",
            "duree_min": 45,
            "intensite": "🔴 Intense (Sparring / Max PR)",
        },
    ]

    stats = _compute_stats(config)
    assert stats["nb_intense"] >= 3  # seuil d'alerte
    assert stats["nb_seances"] == 3


def test_compute_stats_champs_manquants_resilients():
    """Les séances sans duree_min ou intensite ne plantent pas."""
    from modules.hebdo.sport import _compute_stats

    config = [
        {"type": "🎯 Autre"},  # aucun champ
        {"type": "🏃‍♂️ Course", "intensite": "🟢 Légère"},  # pas de duree_min
    ]

    stats = _compute_stats(config)
    assert stats["total_min"] == 0  # duree_min manquante → 0
    assert stats["nb_legere"] == 1
    assert stats["nb_seances"] == 2
