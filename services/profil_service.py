"""Service centralisé d'accès aux credentials Gemini de l'utilisateur.

Avant cette centralisation, **8 fichiers** lisaient ``profil.systeme.gemini_api_key``
directement. Depuis la refonte sécurité (chiffrement Fernet), il faut
TOUJOURS passer par :func:`get_gemini_credentials` qui s'occupe du
déchiffrement transparent et de la gestion des cas dégradés.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from database import Utilisateur
from services.crypto import decrypt_api_key

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


DEFAULT_MODEL = "gemini-2.5-flash"


def get_gemini_credentials(session: "Session") -> tuple[str, str]:
    """Retourne ``(api_key_clair, model)`` pour l'utilisateur singleton.

    - ``api_key_clair`` : chaîne vide si pas de profil ou pas de clé.
    - ``model`` : le modèle configuré, ou ``gemini-2.5-flash`` par défaut.

    Cette fonction **déchiffre** la clé stockée (préfixe ``enc:v1:``) ou
    la retourne telle quelle si elle est en legacy clair.
    """
    p = session.query(Utilisateur).first()
    if p is None or p.systeme is None:
        return "", DEFAULT_MODEL
    api_key = decrypt_api_key(p.systeme.gemini_api_key or "")
    model = p.systeme.gemini_model or DEFAULT_MODEL
    return api_key, model


def has_gemini_credentials(session: "Session") -> bool:
    """Vrai si l'utilisateur a une clé Gemini déchiffrable et non vide."""
    api_key, _ = get_gemini_credentials(session)
    return bool(api_key.strip())


__all__ = [
    "DEFAULT_MODEL",
    "get_gemini_credentials",
    "has_gemini_credentials",
]
