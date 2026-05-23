"""Utilitaires partagés pour les appels Gemini.

Mutualise le retry exponentiel, la classification transient/permanent
des erreurs Gemini, et un logging structuré minimal des appels.
Originellement local à ``pdf_analyzer``, déplacé ici quand
``revision_service`` et ``ai_planner`` ont eu besoin du même comportement.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

T = TypeVar("T")

# Logger dédié — l'app peut le configurer (handler, niveau) au démarrage.
# Par défaut : propage au root logger (silencieux tant que rien n'est configuré).
logger = logging.getLogger("gemini")

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


def gemini_call_with_retry(
    call_fn: Callable[[], T], *, context: str = "gemini"
) -> T:
    """Exécute ``call_fn()`` avec retry exponentiel sur erreurs transitoires.

    Lève immédiatement si l'erreur est non transitoire, ou après épuisement
    des retries si toutes les tentatives ont échoué transitoirement.

    Args:
        call_fn: callback sans arguments, retourne la réponse Gemini.
        context: étiquette pour le logging (ex. ``"fiche_ia"``, ``"planner"``).
            Apparaît dans tous les messages de log liés à cet appel.
    """
    last_exc: Exception | None = None
    started = time.monotonic()
    for attempt in range(GEMINI_MAX_RETRIES):
        try:
            result = call_fn()
            elapsed_ms = int((time.monotonic() - started) * 1000)
            text_len = len(getattr(result, "text", "") or "")
            logger.info(
                "[%s] success attempt=%d elapsed=%dms text_len=%d",
                context, attempt + 1, elapsed_ms, text_len,
            )
            return result
        except Exception as exc:
            last_exc = exc
            transient = is_transient_gemini_error(exc)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if not transient:
                logger.error(
                    "[%s] permanent_error attempt=%d elapsed=%dms err=%r",
                    context, attempt + 1, elapsed_ms, str(exc)[:200],
                )
                raise
            if attempt < GEMINI_MAX_RETRIES - 1:
                backoff = GEMINI_BACKOFF_BASE_S * (2 ** attempt)
                logger.warning(
                    "[%s] transient_error attempt=%d retry_in=%.1fs err=%r",
                    context, attempt + 1, backoff, str(exc)[:200],
                )
                time.sleep(backoff)
            else:
                logger.error(
                    "[%s] retries_exhausted attempts=%d total_elapsed=%dms err=%r",
                    context, GEMINI_MAX_RETRIES, elapsed_ms, str(exc)[:200],
                )
    assert last_exc is not None
    raise last_exc


__all__ = [
    "GEMINI_MAX_RETRIES",
    "GEMINI_BACKOFF_BASE_S",
    "is_transient_gemini_error",
    "gemini_call_with_retry",
]
