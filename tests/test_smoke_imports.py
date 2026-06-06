"""Smoke tests : vérifie que tous les modules s'importent sans erreur.

Capture les régressions évidentes (imports cassés, typos dans les noms
de fonctions, dépendances circulaires) avant qu'elles n'arrivent dans
l'UI Streamlit où elles produisent une erreur opaque.

Ces tests sont volontairement bête-mais-utiles : ils n'invoquent aucune
fonction, ils chargent juste le module.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest


# Liste explicite — plus prédictible qu'une découverte dynamique, et
# documente les modules attendus.
_MODULES = [
    # Couche database
    "database.db",
    "database.models",
    # Couche services
    "services.ai_exam_service",
    "services.ai_flashcards_service",
    "services.ai_planner",
    "services.backup_service",
    "services.cache_versioning",
    "services.crypto",
    "services.data_integrity",
    "services.gamification_service",
    "services.gemini_utils",
    "services.ical_exporter",
    "services.matiere_stats",
    "services.objectif_service",
    "services.optimistic_lock",
    "services.pdf_analyzer",
    "services.pdf_storage",
    "services.planner_validator",
    "services.profil_service",
    "services.profil_validator",
    "services.qcm_validator",
    "services.report_service",
    "services.revision_service",
    "services.scheduler_engine",
    # Couche modules (UI helpers)
    "modules.achievements",
    "modules.aide",
    "modules.bibliotheque",
    "modules.dashboard",
    "modules.generation",
    "modules.historique",
    "modules.import_externe",
    "modules.objectifs",
    "modules.preparer_semaine",
    "modules.profil",
    "modules.revision_rapide",
    "modules.revisions_j",
    "modules.session_etude",
    "modules.suivi",
    "modules.travail",
    "modules.hebdo.ajustements",
    "modules.hebdo.courses",
    "modules.hebdo.dev_perso",
    "modules.hebdo.etudes",
    "modules.hebdo.intendance",
    "modules.hebdo.projets",
    "modules.hebdo.social",
    "modules.hebdo.sport",
    "modules._widgets_checkin",
    # Couche pages
    "pages.aujourdhui",
    "pages.centre_etude",
    "pages.configuration",
    "pages.planification",
    "pages.progression",
    # pages.overview est un blueprint Flask orphelin (legacy, jamais
    # importé). Skip délibérément du smoke test.
    # Utils
    "utils.helpers",
]


@pytest.mark.parametrize("module_name", _MODULES)
def test_module_imports_sans_erreur(module_name):
    """Charge le module. Si une typo ou un import circulaire existe, ça
    pète ici, avant d'arriver dans une UI Streamlit où l'erreur est
    affichée comme un mur de stack trace illisible."""
    try:
        importlib.import_module(module_name)
    except ImportError as exc:
        pytest.fail(f"Échec import {module_name} : {exc}")
    except AttributeError as exc:
        # Typique du bug DDD : `from X import Y` où Y n'existe plus.
        pytest.fail(
            f"AttributeError au chargement de {module_name} : {exc}. "
            "Probablement un nom de fonction renommé (cf. CLAUDE.md)."
        )


def test_pas_de_modules_orphelins():
    """Garde-fou : aucun nouveau .py dans services/ qui ne soit pas
    listé dans _MODULES. Force la maintenance de la liste."""
    import services
    decouverts = {
        name for _, name, _ in pkgutil.iter_modules(services.__path__)
        if not name.startswith("_")
    }
    listés = {
        m.split(".", 1)[1] for m in _MODULES
        if m.startswith("services.")
    }
    orphelins = decouverts - listés
    assert not orphelins, (
        f"Nouveaux modules services/ non listés dans _MODULES : {orphelins}. "
        "Ajoute-les pour qu'ils soient smoke-testés."
    )
