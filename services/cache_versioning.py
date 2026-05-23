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

# Bump cette constante à chaque refonte du prompt système de generer_fiche_ia.
# Toutes les fiches générées avec une version antérieure seront régénérées
# à la prochaine demande.
FICHE_PROMPT_VERSION = 1


def texte_sha256(texte: str) -> str:
    """SHA-256 hex du texte source d'un chapitre (64 caractères)."""
    return hashlib.sha256((texte or "").encode("utf-8")).hexdigest()


def fiche_cache_is_valid(
    *,
    cached_model: str | None,
    cached_prompt_version: int | None,
    cached_texte_sha: str | None,
    current_model: str,
    current_texte_sha: str,
) -> bool:
    """Détermine si la fiche en cache est encore utilisable.

    Si l'un quelconque des trois facteurs ne correspond pas, retourne
    ``False`` → l'appelant doit régénérer.
    """
    if not cached_model or not cached_prompt_version or not cached_texte_sha:
        return False
    if cached_model != current_model:
        return False
    if cached_prompt_version != FICHE_PROMPT_VERSION:
        return False
    if cached_texte_sha != current_texte_sha:
        return False
    return True


__all__ = [
    "FICHE_PROMPT_VERSION",
    "texte_sha256",
    "fiche_cache_is_valid",
]
