"""Service de **report inter-semaines**.

Règle fondamentale (issue de la spec) :

> Seules les tâches **non-obligatoires** sont reportées.

Tâches JAMAIS reportées (``obligatoire == True``) : sommeil, repas, transport,
cours en présentiel, contraintes fixes.

Tâches reportées automatiquement si ``statut`` ∈ {``"non_fait"``, ``"partiellement"``} :
révisions de cours, projets persos, intendance, dev perso, courses…

Concrètement, ce service :

1. :func:`compute_reported_items` — scanne la semaine précédente et retourne
   la liste des items à reporter (en pur dict, sans persistance).
2. :func:`transfer_reported_items_to_new_week` — pré-remplit le champ
   ``projets_config`` de la nouvelle SaisieHebdo avec les items reportés
   (tagués ``reportee_depuis_semaine_id`` pour les distinguer dans l'UI).

L'AI planner verra ensuite ces items pré-remplis dans le prompt et leur
donnera une priorité plus élevée.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from database.models import SaisieHebdo, Semaine, Tache


def compute_reported_items(
    session: Session,
    previous_week_id: int,
) -> list[dict[str, Any]]:
    """Scanne la semaine précédente et retourne la liste des tâches à reporter.

    Une tâche est reportée si :
      - ``obligatoire == False`` (règle fondamentale)
      - ``statut`` ∈ {``"non_fait"``, ``"partiellement"``}
    """
    tasks = (
        session.query(Tache)
        .filter(
            Tache.semaine_id == previous_week_id,
            Tache.obligatoire.is_(False),
            Tache.statut.in_(["non_fait", "partiellement"]),
        )
        .all()
    )

    reported: list[dict[str, Any]] = []
    for t in tasks:
        reported.append({
            "titre": t.titre or "Tâche sans titre",
            "type_original": t.type or "autre",
            "duree_min": int(t.duree_min or 30),
            "reportee_depuis_semaine_id": previous_week_id,
            "statut_precedent": t.statut,
            "commentaire_etudiant": t.commentaire_etudiant or "",
            "matiere_id": t.matiere_id,
            "chapitre_ids": t.chapitre_ids,
        })
    return reported


def transfer_reported_items_to_new_week(
    session: Session,
    previous_week_id: int,
    new_saisie: SaisieHebdo,
) -> int:
    """Pré-remplit la nouvelle SaisieHebdo avec les items reportés.

    Tous les items reportés (quel que soit leur type d'origine) atterrissent
    dans ``projets_config`` avec un flag ``reportee_depuis_semaine_id``
    qui permet à l'UI de les distinguer (badge orange dans
    ``modules/hebdo/projets.py``).

    Args:
        session: session SQLAlchemy active.
        previous_week_id: id de la semaine précédente.
        new_saisie: la SaisieHebdo de la nouvelle semaine (déjà ajoutée
            à la session).

    Returns:
        Le nombre d'items effectivement reportés.
    """
    items = compute_reported_items(session, previous_week_id)
    if not items:
        return 0

    sem_origine = session.get(Semaine, previous_week_id)
    label_origine = (
        f"S{sem_origine.numero_semaine:02d}/{sem_origine.annee}"
        if sem_origine
        else f"semaine #{previous_week_id}"
    )

    # On ajoute aux projets existants (a priori la nouvelle saisie est vide,
    # mais on est défensif).
    current_projets = list(new_saisie.projets_config or [])

    for item in items:
        # Priorité dépendante du statut : non_fait → Haute, partiellement → Normale
        priorite = "Haute" if item["statut_precedent"] == "non_fait" else "Normale"

        current_projets.append({
            "libelle": item["titre"],
            "duree_min": item["duree_min"],
            "deadline": "",
            "priorite": priorite,
            "projet_long_terme": False,
            # Métadonnées de report — utilisées par l'UI et l'AI planner
            "reportee_depuis_semaine_id": item["reportee_depuis_semaine_id"],
            "reportee_label": label_origine,
            "type_original": item["type_original"],
            "commentaire_etudiant": item["commentaire_etudiant"],
        })

    # Important : ré-affecter la liste pour que SQLAlchemy détecte
    # le changement sur la colonne JSON (mutations in-place pas détectées).
    new_saisie.projets_config = current_projets
    return len(items)


__all__ = [
    "compute_reported_items",
    "transfer_reported_items_to_new_week",
]
