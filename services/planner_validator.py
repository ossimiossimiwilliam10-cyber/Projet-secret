"""Validation stricte du JSON de planning hebdomadaire renvoyé par Gemini.

Avant : ``_parse_llm_json`` se contentait de parser le JSON sans
valider sa structure. Si Gemini renvoyait un schéma cassé (manque
``planning``, jours invalides, heures malformées, types faux), le bug
se manifestait silencieusement en aval — tâches ignorées sans avertissement
dans ``_save_planning_to_db``, ou crash UI sur ``KeyError``.

Après : :func:`validate_planning` vérifie le schéma complet et retourne
soit un dict normalisé propre, soit une :class:`PlanningValidationError`
décrivant précisément ce qui ne va pas.
"""

from __future__ import annotations

import re
from typing import Any

JOURS_VALIDES = (
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
)

# Types de tâches acceptés. La liste vient de l'usage observé dans
# `modules/generation.py` et l'enum implicite de `Tache.type`.
TYPES_VALIDES = {
    "etude", "revision", "lecture", "exercices", "fiches",
    "sport", "pause", "trajet", "repas", "sommeil",
    "travail", "loisir", "autre",
}

# Format strict HH:MM (24h). ex: "08:30", "14:00".
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

MAX_TITRE_CHARS = 200
MAX_JUSTIFICATION_CHARS = 500


class PlanningValidationError(ValueError):
    """Erreur de validation du planning renvoyé par Gemini."""


def _validate_time(value: Any, field_name: str) -> str:
    """Retourne la chaîne HH:MM ou lève."""
    if not isinstance(value, str):
        raise PlanningValidationError(
            f"{field_name} doit être une chaîne HH:MM, reçu {type(value).__name__}."
        )
    if not _TIME_RE.match(value):
        raise PlanningValidationError(
            f"{field_name} mal formé : {value!r} (attendu HH:MM, ex. '08:30')."
        )
    return value


def _time_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _validate_tache(tache: Any, jour: str, index: int) -> dict:
    """Valide une tâche unique. Retourne le dict normalisé ou lève."""
    where = f"jour={jour!r} tache#{index}"
    if not isinstance(tache, dict):
        raise PlanningValidationError(
            f"{where} : doit être un objet, reçu {type(tache).__name__}."
        )

    heure_debut = _validate_time(tache.get("heure_debut"), f"{where}.heure_debut")
    heure_fin = _validate_time(tache.get("heure_fin"), f"{where}.heure_fin")
    if _time_to_minutes(heure_fin) <= _time_to_minutes(heure_debut):
        raise PlanningValidationError(
            f"{where} : heure_fin ({heure_fin}) doit être strictement "
            f"après heure_debut ({heure_debut})."
        )

    titre = str(tache.get("titre") or "").strip()
    if not titre:
        raise PlanningValidationError(f"{where} : titre vide.")
    titre = titre[:MAX_TITRE_CHARS]

    type_ = str(tache.get("type") or "autre").strip().lower()
    if type_ not in TYPES_VALIDES:
        type_ = "autre"

    obligatoire = bool(tache.get("obligatoire", False))
    justification = str(tache.get("justification") or "").strip()[:MAX_JUSTIFICATION_CHARS]

    chapitre_ids_raw = tache.get("chapitre_ids") or []
    if not isinstance(chapitre_ids_raw, list):
        raise PlanningValidationError(
            f"{where} : chapitre_ids doit être une liste, reçu "
            f"{type(chapitre_ids_raw).__name__}."
        )
    chapitre_ids: list[int] = []
    for cid in chapitre_ids_raw:
        try:
            chapitre_ids.append(int(cid))
        except (TypeError, ValueError):
            continue  # IDs invalides sont silencieusement ignorés

    return {
        "type": type_,
        "titre": titre,
        "heure_debut": heure_debut,
        "heure_fin": heure_fin,
        "obligatoire": obligatoire,
        "justification": justification,
        "chapitre_ids": chapitre_ids,
    }


def validate_planning(raw: Any) -> dict:
    """Valide et normalise un JSON de planning renvoyé par Gemini.

    Args:
        raw: dict parsé depuis le JSON Gemini.

    Returns:
        Dict normalisé avec la même structure mais validée :
        ``{score_realisme, justification_globale, planning: {jour: [taches]}}``.

    Raises:
        PlanningValidationError: si la structure top-level ou les tâches
            sont invalides au point de ne pas pouvoir être réparées.
    """
    return _validate_planning_dict(raw, key="planning", allowed_jours=JOURS_VALIDES)


def validate_partial_planning(
    raw: Any,
    key: str = "planning_jours_restants",
    allowed_jours: tuple[str, ...] | list[str] | None = None,
) -> dict:
    """Valide un planning partiel (replanning, intégration nouveautés).

    Schéma identique à :func:`validate_planning` mais :
      - clé top-level configurable (par défaut ``planning_jours_restants``)
      - jours autorisés restreignables (ex : seuls les jours futurs de la semaine)
      - 0 tâche valide est toléré (un replan peut ne rien recommander)

    Args:
        raw: dict parsé.
        key: nom de la clé du planning dans ``raw``.
        allowed_jours: jours acceptables. ``None`` → tous les jours.

    Returns:
        Dict normalisé : ``{key: {jour: [taches]}}`` + métadonnées libres.

    Raises:
        PlanningValidationError: si la structure est irrécupérable.
    """
    allowed = tuple(allowed_jours) if allowed_jours else JOURS_VALIDES
    return _validate_planning_dict(
        raw, key=key, allowed_jours=allowed, allow_empty=True
    )


def _validate_planning_dict(
    raw: Any,
    key: str,
    allowed_jours: tuple[str, ...] | list[str],
    allow_empty: bool = False,
) -> dict:
    """Implémentation commune des deux validateurs.

    Si ``allow_empty=False`` (planning complet), au moins une tâche valide
    doit ressortir, sinon erreur. Sinon (planning partiel/incrémental),
    on tolère 0 tâche.
    """
    if not isinstance(raw, dict):
        raise PlanningValidationError(
            f"Réponse Gemini doit être un objet, reçu {type(raw).__name__}."
        )

    try:
        score = int(raw.get("score_realisme") or 0)
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))

    justification = str(raw.get("justification_globale") or "").strip()

    planning_raw = raw.get(key)
    if not isinstance(planning_raw, dict):
        raise PlanningValidationError(
            f"Clé `{key}` absente ou non-objet (reçu "
            f"{type(planning_raw).__name__}). Gemini a probablement répondu "
            f"hors-schéma."
        )

    allowed_set = set(allowed_jours)
    cleaned_planning: dict[str, list[dict]] = {j: [] for j in allowed_jours}
    for jour, taches in planning_raw.items():
        jour_norm = str(jour).strip().lower()
        if jour_norm not in allowed_set:
            continue
        if not isinstance(taches, list):
            continue
        for idx, tache in enumerate(taches):
            try:
                cleaned_planning[jour_norm].append(
                    _validate_tache(tache, jour_norm, idx)
                )
            except PlanningValidationError:
                continue

    nb_taches_total = sum(len(v) for v in cleaned_planning.values())
    if nb_taches_total == 0 and not allow_empty:
        raise PlanningValidationError(
            "Le planning ne contient aucune tâche valide après nettoyage."
        )

    return {
        "score_realisme": score,
        "justification_globale": justification,
        key: cleaned_planning,
    }


__all__ = [
    "JOURS_VALIDES",
    "TYPES_VALIDES",
    "MAX_TITRE_CHARS",
    "MAX_JUSTIFICATION_CHARS",
    "PlanningValidationError",
    "validate_planning",
    "validate_partial_planning",
]
