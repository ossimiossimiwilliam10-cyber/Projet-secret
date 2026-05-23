"""Utilitaires partagés pour les appels Gemini.

Mutualise le retry exponentiel et la classification transient/permanent
des erreurs Gemini. Originellement local à ``pdf_analyzer``, déplacé ici
quand ``revision_service`` et ``ai_planner`` ont eu besoin du même
comportement.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")

# Backoff exponentiel : 2s, 4s, 8s — total max ~14s d'attente cumulée
# avant abandon. Suffisant pour absorber les 503/timeouts transitoires
# côté Gemini sans bloquer l'UI trop longtemps.
GEMINI_MAX_RETRIES = 3
GEMINI_BACKOFF_BASE_S = 2.0

_TRANSIENT_MARKERS = ("429", "503", "504", "overloaded", "timeout", "deadline")
_PERMANENT_MARKERS = ("400", "401", "403", "api key", "invalid", "permission")


def is_transient_gemini_error(exc: BaseException) -> bool:
    """Vrai si l'erreur Gemini est probablement transitoire (à retry).

    Transitoires : 429 (rate limit), 503 (overloaded), 504 (gateway
    timeout), erreurs réseau (``ConnectionError``, ``TimeoutError``).
    Permanentes (no-retry) : 400/401/403, "api key", "invalid".
    """
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    msg = str(exc).lower()
    if any(m in msg for m in _PERMANENT_MARKERS):
        return False
    return any(m in msg for m in _TRANSIENT_MARKERS)


def gemini_call_with_retry(call_fn: Callable[[], T]) -> T:
    """Exécute ``call_fn()`` avec retry exponentiel sur erreurs transitoires.

    Lève immédiatement si l'erreur est non transitoire, ou après épuisement
    des retries si toutes les tentatives ont échoué transitoirement.
    """
    last_exc: Exception | None = None
    for attempt in range(GEMINI_MAX_RETRIES):
        try:
            return call_fn()
        except Exception as exc:
            last_exc = exc
            if not is_transient_gemini_error(exc):
                raise
            if attempt < GEMINI_MAX_RETRIES - 1:
                time.sleep(GEMINI_BACKOFF_BASE_S * (2 ** attempt))
    assert last_exc is not None
    raise last_exc


__all__ = [
    "GEMINI_MAX_RETRIES",
    "GEMINI_BACKOFF_BASE_S",
    "is_transient_gemini_error",
    "gemini_call_with_retry",
]
