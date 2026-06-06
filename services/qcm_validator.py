"""Validation stricte du JSON QCM renvoyé par Gemini.

Avant : on acceptait 2-4 options et on forçait ``correct="A"`` si la
réponse était invalide → questions cabossées affichées à l'utilisateur,
crashes UI si la structure était inattendue.

Après : on rejette les questions non conformes et on dédoublonne. Si
zéro question valide reste, on lève :class:`ValueError` pour que
l'appelant puisse demander au LLM de regénérer (ou afficher une
erreur claire à l'utilisateur).
"""

from __future__ import annotations

import re
from typing import Any

# Préfixes "A) ", "B) ", "1) ", "1." que Gemini ajoute parfois aux options,
# en double avec la lettre déjà encodée par ``correct``. On les strip.
_OPTION_PREFIX_RE = re.compile(r"^\s*[A-D1-4]\s*[\.\)\-:]\s*", re.IGNORECASE)

VALID_LETTERS = {"A", "B", "C", "D"}
MIN_QUESTION_CHARS = 10
MAX_QUESTION_CHARS = 500
MAX_OPTION_CHARS = 300


def _strip_option_prefix(text: str) -> str:
    """Retire le préfixe ``A)``, ``1.`` ou similaire d'une option."""
    return _OPTION_PREFIX_RE.sub("", text).strip()


def _normalize_question(q: Any) -> dict | None:
    """Tente de normaliser une question QCM. Retourne ``None`` si invalide."""
    if not isinstance(q, dict):
        return None

    question = str(q.get("question") or "").strip()
    if not (MIN_QUESTION_CHARS <= len(question) <= MAX_QUESTION_CHARS):
        return None

    options_raw = q.get("options")
    if not isinstance(options_raw, list) or len(options_raw) != 4:
        return None

    options = [_strip_option_prefix(str(o)) for o in options_raw]
    if any(not o or len(o) > MAX_OPTION_CHARS for o in options):
        return None

    # Pas de doublons d'options (insensible à la casse + espaces).
    seen = {o.lower() for o in options}
    if len(seen) != 4:
        return None

    correct = str(q.get("correct") or "").strip().upper()[:1]
    if correct not in VALID_LETTERS:
        return None

    explication = str(q.get("explication") or "").strip()

    return {
        "question": question,
        "options": options,
        "correct": correct,
        "explication": explication,
    }


def validate_qcm_questions(raw_questions: list[Any]) -> list[dict]:
    """Valide et dédoublonne une liste brute de questions QCM.

    Args:
        raw_questions: ce que renvoie Gemini après parsing JSON.

    Returns:
        Liste de questions normalisées et valides.

    Raises:
        ValueError: si la liste d'entrée est vide ou si aucune question
            valide ne ressort de la validation.
    """
    if not isinstance(raw_questions, list) or not raw_questions:
        raise ValueError("Gemini n'a pas renvoyé de tableau de questions.")

    cleaned: list[dict] = []
    seen_questions: set[str] = set()
    for q in raw_questions:
        normalized = _normalize_question(q)
        if normalized is None:
            continue
        # Dédoublonnage sur le texte de la question (insensible à la casse).
        key = normalized["question"].lower()
        if key in seen_questions:
            continue
        seen_questions.add(key)
        cleaned.append(normalized)

    if not cleaned:
        raise ValueError(
            "Aucune question QCM valide après validation stricte. "
            "Gemini a probablement renvoyé un format inattendu."
        )
    return cleaned


def validate_quiz_questions(raw_text: str, max_questions: int = 5) -> list[str]:
    """Valide une liste de questions ouvertes (format texte numéroté).

    Args:
        raw_text: réponse brute de Gemini (texte multi-lignes).
        max_questions: nombre maximum de questions à garder.

    Returns:
        Liste de questions nettoyées (sans préfixe numérique).

    Raises:
        ValueError: si aucune question valide n'est extraite.
    """
    questions: list[str] = []
    seen: set[str] = set()
    for line in (raw_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^(Q?\d+)\s*[\.\)\-:]\s*", "", line).strip()
        if len(line) < MIN_QUESTION_CHARS or len(line) > MAX_QUESTION_CHARS:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        questions.append(line)
        if len(questions) >= max_questions:
            break

    if not questions:
        raise ValueError(
            "Aucune question ouverte valide extraite de la réponse Gemini."
        )
    return questions


__all__ = [
    "VALID_LETTERS",
    "MIN_QUESTION_CHARS",
    "MAX_QUESTION_CHARS",
    "MAX_OPTION_CHARS",
    "validate_qcm_questions",
    "validate_quiz_questions",
]
