"""Service de gestion des objectifs personnalisés — F3b.

Workflow :
1. L'étudiant définit un objectif (nom, cours visé, note cible, date cible, description)
2. Avant de le créer, on demande au LLM de proposer une **stratégie** :
   pondérations par chapitre, heures à investir, conseils, niveau de réalisme.
3. L'étudiant peut **adopter** la stratégie (créer l'objectif + activer les
   pondérations) ou demander une autre proposition.
4. Tant que l'objectif est actif, les pondérations sont appliquées par
   l'ai_planner dans tous les futurs plannings hebdomadaires.

Le service est **stateless** au sens où il ne maintient pas d'état entre les
appels. Toutes les fonctions prennent une session SQLAlchemy.
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from database.models import Chapitre, Objectif, Utilisateur



# ===========================================================================
# 3. CRUD des objectifs
# ===========================================================================
def creer_objectif(
    session: Session,
    nom: str,
    description: str,
    matiere_id: int | None,
    note_cible: float | None,
    date_cible: datetime.date,
    strategie: dict,
) -> Objectif:
    """Crée et persiste un objectif avec la stratégie adoptée."""
    # On dérive les ponderations (cache rapide) depuis la stratégie
    ponderations = strategie.get("ponderations_chapitres", {}) or {}

    obj = Objectif(
        nom=nom.strip(),
        description=(description or "").strip(),
        matiere_id=matiere_id,
        note_cible=note_cible,
        date_cible=date_cible,
        statut="actif",
        strategie_ia={},
        ponderations={},
    )
    session.add(obj)
    session.flush()
    return obj


def marquer_atteint(session: Session, objectif_id: int) -> None:
    """Marque un objectif comme atteint."""
    obj = session.get(Objectif, objectif_id)
    if obj is None:
        return
    obj.statut = "atteint"
    obj.date_atteinte = datetime.datetime.now(datetime.timezone.utc)


def abandonner(session: Session, objectif_id: int) -> None:
    """Marque un objectif comme abandonné (les pondérations cessent de s'appliquer)."""
    obj = session.get(Objectif, objectif_id)
    if obj is None:
        return
    obj.statut = "abandonne"


def supprimer(session: Session, objectif_id: int) -> None:
    """Suppression définitive d'un objectif (utile pour les tests/erreurs)."""
    obj = session.get(Objectif, objectif_id)
    if obj is not None:
        session.delete(obj)


# ===========================================================================
# 4. Helper pour l'ai_planner : agrège les pondérations actives
# ===========================================================================
def obtenir_ponderations_actives(session: Session) -> dict[int, float]:
    """Retourne un dict {chapitre_id: coefficient combiné} pour tous les
    chapitres concernés par au moins un objectif actif.

    Si un chapitre apparaît dans plusieurs objectifs, on prend le **max**
    (raisonnable car le plus restrictif).
    """
    out: dict[int, float] = {}
    objectifs = session.query(Objectif).filter(Objectif.statut == "actif").all()
    for obj in objectifs:
        pond = obj.ponderations or {}
        for cid_str, coef in pond.items():
            try:
                cid = int(cid_str)
                c = float(coef)
            except (TypeError, ValueError):
                continue
            if c <= 0:
                continue
            out[cid] = max(out.get(cid, 0.0), c)
    return out


def progression_objectif(session: Session, objectif: Objectif) -> dict[str, Any]:
    """Calcule la progression d'un objectif basée sur la maîtrise des chapitres.

    Renvoie :
    - maitrise_actuelle (0-100) : moyenne pondérée des chapitres concernés
    - maitrise_cible_estimee (0-100) : si note_cible est définie, on l'estime
      comme note_cible × 5 (ex: 14/20 → 70 % de maîtrise).
    - ratio_progression (0-1) : maitrise_actuelle / maitrise_cible
    - jours_restants : jusqu'à date_cible
    """
    pond = objectif.ponderations or {}
    if not pond:
        return {
            "maitrise_actuelle": 0.0,
            "maitrise_cible_estimee": None,
            "ratio_progression": 0.0,
            "jours_restants": (objectif.date_cible - datetime.date.today()).days,
        }

    # Moyenne pondérée des maîtrises
    chap_ids = [int(k) for k in pond.keys()]
    chapitres = session.query(Chapitre).filter(Chapitre.id.in_(chap_ids)).all()
    if not chapitres:
        return {
            "maitrise_actuelle": 0.0,
            "maitrise_cible_estimee": None,
            "ratio_progression": 0.0,
            "jours_restants": (objectif.date_cible - datetime.date.today()).days,
        }

    total_pond = 0.0
    total_maitrise_pondere = 0.0
    for chap in chapitres:
        coef = float(pond.get(str(chap.id), 1.0))
        m = float(chap.maitrise_pct or 0.0)
        total_pond += coef
        total_maitrise_pondere += coef * m
    maitrise_actuelle = total_maitrise_pondere / total_pond if total_pond > 0 else 0.0

    # Estimation de la maîtrise cible (si note cible définie)
    if objectif.note_cible is not None:
        maitrise_cible = min(100.0, float(objectif.note_cible) * 5.0)
        ratio = min(1.0, maitrise_actuelle / maitrise_cible) if maitrise_cible > 0 else 0.0
    else:
        maitrise_cible = None
        ratio = maitrise_actuelle / 100.0  # juste un indicateur visuel

    return {
        "maitrise_actuelle": round(maitrise_actuelle, 1),
        "maitrise_cible_estimee": round(maitrise_cible, 1) if maitrise_cible else None,
        "ratio_progression": round(ratio, 3),
        "jours_restants": (objectif.date_cible - datetime.date.today()).days,
    }


__all__ = [
    "creer_objectif",
    "marquer_atteint",
    "abandonner",
    "supprimer",
    "obtenir_ponderations_actives",
    "progression_objectif",
]
