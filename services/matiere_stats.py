"""Statistiques cognitives par matière, utilisées par l'onglet « Études ».

Module pur : prend des objets ORM, ne touche pas à Streamlit. Toute la
logique est testable en isolation.

Fournit :

- :func:`stats_matiere` : tableau de bord par matière (nb chapitres,
  nouveaux, révisions dues, retards, maîtrise moyenne).
- :func:`matieres_avec_revisions_dues` : ids des matières qui méritent
  d'être travaillées cette semaine (utilisé pour la pré-sélection
  automatique du lundi matin).
- :func:`estimer_charge_minutes` : somme des ``temps_estime_h`` des
  chapitres sélectionnés, en minutes (utilisé pour l'indicateur live
  de surcharge).
- :func:`format_label_matiere` : libellé enrichi affiché dans le
  multiselect.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from database.models import Chapitre, Matiere


# ---------------------------------------------------------------------------
# Calcul des stats par matière
# ---------------------------------------------------------------------------
def stats_matiere(session: Session, matiere: Matiere) -> dict[str, Any]:
    """Compte les indicateurs cognitifs pour une matière donnée.

    Définitions :
      - ``nb_chapitres`` : total des chapitres rattachés.
      - ``nb_nouveaux`` : chapitres jamais commencés (``niveau_actuel == 0``
        ET ``date_prochaine is None``). C'est de la « première lecture ».
      - ``nb_revisions_dues`` : chapitres dont ``date_prochaine`` est ≤
        aujourd'hui (à réviser maintenant ou hier).
      - ``nb_revisions_en_retard`` : sous-ensemble des dues dont
        ``date_prochaine < today`` strictement (du retard).
      - ``maitrise_moyenne_pct`` : moyenne arithmétique des
        ``maitrise_pct`` des chapitres, 0.0 si pas de chapitre.
    """
    today = date.today()
    chapitres = list(matiere.chapitres or [])

    if not chapitres:
        return {
            "nb_chapitres": 0,
            "nb_nouveaux": 0,
            "nb_revisions_dues": 0,
            "nb_revisions_en_retard": 0,
            "maitrise_moyenne_pct": 0.0,
        }

    nb_nouveaux = sum(
        1 for c in chapitres
        if (c.niveau_actuel or 0) == 0 and c.date_prochaine is None
    )
    dues = [c for c in chapitres if c.date_prochaine is not None and c.date_prochaine <= today]
    nb_en_retard = sum(1 for c in dues if c.date_prochaine < today)
    maitrise_moy = sum(float(c.maitrise_pct or 0) for c in chapitres) / len(chapitres)

    return {
        "nb_chapitres": len(chapitres),
        "nb_nouveaux": nb_nouveaux,
        "nb_revisions_dues": len(dues),
        "nb_revisions_en_retard": nb_en_retard,
        "maitrise_moyenne_pct": round(maitrise_moy, 1),
    }


# ---------------------------------------------------------------------------
# Pré-sélection automatique
# ---------------------------------------------------------------------------
def matieres_avec_revisions_dues(
    session: Session,
    date_max: date | None = None,
) -> list[int]:
    """IDs des matières dont au moins un chapitre a une révision due ≤ date_max.

    Utilisé pour pré-sélectionner les bonnes matières le lundi matin :
    l'étudiant n'a plus à se demander ce qui est urgent.
    """
    if date_max is None:
        date_max = date.today()

    rows = (
        session.query(Chapitre.matiere_id)
        .filter(
            Chapitre.matiere_id.isnot(None),
            Chapitre.date_prochaine.isnot(None),
            Chapitre.date_prochaine <= date_max,
        )
        .distinct()
        .all()
    )
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Estimation de la charge horaire d'une sélection
# ---------------------------------------------------------------------------
def estimer_charge_minutes(
    session: Session,
    matieres_selectionnees: list[dict[str, Any]] | None,
) -> int:
    """Somme les ``temps_estime_h`` (en minutes) des chapitres cochés.

    Pour chaque entrée de la sélection hebdo qui contient des ``chapitre_ids``
    explicites, on additionne les temps estimés. Une matière sans
    ``chapitre_ids`` (« révision globale ») compte pour 0 — son temps
    sera estimé par l'algorithme au moment de la génération.
    """
    if not matieres_selectionnees:
        return 0

    tous_ids: list[int] = []
    for m_sel in matieres_selectionnees:
        ids = m_sel.get("chapitre_ids") or []
        tous_ids.extend(int(i) for i in ids if i is not None)

    if not tous_ids:
        return 0

    chapitres = (
        session.query(Chapitre)
        .filter(Chapitre.id.in_(tous_ids))
        .all()
    )
    total_h = sum(float(c.temps_estime_h or 0.0) for c in chapitres)
    return int(round(total_h * 60))


# ---------------------------------------------------------------------------
# Présentation
# ---------------------------------------------------------------------------
def format_label_matiere(matiere: Matiere, stats: dict[str, Any]) -> str:
    """Libellé enrichi pour le multiselect de l'onglet Études.

    Affiche le nom (+ Semestre / UE si rattachée) suivi d'icônes indicatives.
    """
    base = matiere.nom
    if matiere.ue:
        prefix = ""
        if matiere.ue.semestre:
            prefix = f"{matiere.ue.semestre.nom} ▸ "
        base = f"{prefix}{matiere.ue.nom} ▸ {base}"

    if stats["nb_chapitres"] == 0:
        return f"{base}   ⚠️ aucun chapitre"

    parts: list[str] = []
    if stats["nb_revisions_dues"] > 0:
        icon = "⚠️" if stats["nb_revisions_en_retard"] > 0 else "🔥"
        parts.append(f"{icon} {stats['nb_revisions_dues']} due(s)")
    if stats["nb_nouveaux"] > 0:
        parts.append(f"📑 {stats['nb_nouveaux']} nouveau(x)")
    parts.append(f"📊 {stats['maitrise_moyenne_pct']:.0f}%")

    return f"{base}   ·   " + "   ".join(parts)


__all__ = [
    "stats_matiere",
    "matieres_avec_revisions_dues",
    "estimer_charge_minutes",
    "format_label_matiere",
]
