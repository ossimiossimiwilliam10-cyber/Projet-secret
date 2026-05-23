"""Versioning du cache IA — invalidation automatique des fiches obsolètes.

Trois facteurs invalident une fiche IA :

1. **Modèle Gemini** : la fiche a été générée avec ``gemini-1.5-flash`` et
   on utilise maintenant ``gemini-2.5-flash`` → régénération.
2. **Version du prompt** : :data:`FICHE_PROMPT_VERSION` augmente à chaque
   refonte du prompt système (structure, ton, format de sortie).
3. **Contenu source** : si l'utilisateur ré-upload le PDF, le SHA-256 du
   texte change → la fiche d'avant est périmée.

Le cache n'a pas de TTL : une fiche reste valide tant que les trois
facteurs ne bougent pas — ce qui économise des appels Gemini coûteux.
"""

from __future__ import annotations

import hashlib

# Bump ces constantes à chaque refonte des prompts système des fonctions
# Gemini correspondantes. Toutes les générations en cache avec une version
# antérieure seront régénérées à la prochaine demande.
FICHE_PROMPT_VERSION = 1
QCM_PROMPT_VERSION = 1
QUIZ_PROMPT_VERSION = 1


def texte_sha256(texte: str) -> str:
    """SHA-256 hex du texte source d'un chapitre (64 caractères)."""
    return hashlib.sha256((texte or "").encode("utf-8")).hexdigest()


def cache_is_valid(
    *,
    cached_model: str | None,
    cached_prompt_version: int | None,
    cached_texte_sha: str | None,
    current_model: str,
    current_prompt_version: int,
    current_texte_sha: str,
) -> bool:
    """Détermine si un cache IA (fiche / QCM / quiz) est encore utilisable.

    Si l'un quelconque des trois facteurs (modèle, version du prompt,
    SHA du contenu) ne correspond pas, retourne ``False`` → l'appelant
    doit régénérer.
    """
    if not cached_model or not cached_prompt_version or not cached_texte_sha:
        return False
    if cached_model != current_model:
        return False
    if cached_prompt_version != current_prompt_version:
        return False
    if cached_texte_sha != current_texte_sha:
        return False
    return True


def fiche_cache_is_valid(
    *,
    cached_model: str | None,
    cached_prompt_version: int | None,
    cached_texte_sha: str | None,
    current_model: str,
    current_texte_sha: str,
) -> bool:
    """Spécialisation pour la fiche IA. Conservée pour rétro-compatibilité."""
    return cache_is_valid(
        cached_model=cached_model,
        cached_prompt_version=cached_prompt_version,
        cached_texte_sha=cached_texte_sha,
        current_model=current_model,
        current_prompt_version=FICHE_PROMPT_VERSION,
        current_texte_sha=current_texte_sha,
    )


__all__ = [
    "FICHE_PROMPT_VERSION",
    "QCM_PROMPT_VERSION",
    "QUIZ_PROMPT_VERSION",
    "texte_sha256",
    "cache_is_valid",
    "fiche_cache_is_valid",
]
