"""Service centralise d'acces aux credentials LLM de l'utilisateur.

Avant cette centralisation, **8 fichiers** lisaient ``profil.systeme.gemini_api_key``
directement. Depuis la refonte securite (chiffrement Fernet), il faut
TOUJOURS passer par :func:`get_llm_credentials` qui s'occupe du
dechiffrement transparent et de la gestion des cas degrades.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from database import Utilisateur
from services.crypto import decrypt_api_key

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


DEFAULT_MODEL = "deepseek-v4-pro"


def get_llm_credentials(session: "Session") -> tuple[str, str]:
    """Retourne ``(api_key_clair, model)`` pour l'utilisateur singleton.

    - ``api_key_clair`` : chaine vide si pas de profil ou pas de cle.
    - ``model`` : le modele configure, ou ``deepseek-v4-pro`` par defaut.

    Cette fonction **dechiffre** la cle stockee (prefixe ``enc:v1:``) ou
    la retourne telle quelle si elle est en legacy clair.
    """
    p = session.query(Utilisateur).first()
    if p is None or p.systeme is None:
        return "", DEFAULT_MODEL
    api_key_deepseek = decrypt_api_key(p.systeme.deepseek_api_key or "")
    api_key_gemini = decrypt_api_key(p.systeme.gemini_api_key or "")
    
    # Tout passer sur DeepSeek par défaut comme demandé par l'utilisateur
    if api_key_deepseek:
        model = p.systeme.deepseek_model or "deepseek-chat"
        return api_key_deepseek, model
    
    # Fallback sur Gemini
    model = p.systeme.gemini_model or DEFAULT_MODEL
    return api_key_gemini, model


def has_llm_credentials(session: "Session") -> bool:
    """Vrai si l'utilisateur a une cle LLM dechiffrable et non vide."""
    api_key, _ = get_llm_credentials(session)
    return bool(api_key.strip())


# Backward compat aliases
get_gemini_credentials = get_llm_credentials
has_gemini_credentials = has_llm_credentials


__all__ = [
    "DEFAULT_MODEL",
    "get_llm_credentials",
    "has_llm_credentials",
    "get_gemini_credentials",
    "has_gemini_credentials",
]
