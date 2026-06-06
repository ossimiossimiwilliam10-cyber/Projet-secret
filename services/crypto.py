"""Chiffrement symétrique pour les secrets utilisateurs (clé API Gemini).

Utilise Fernet (AES-128 CBC + HMAC SHA-256) du package ``cryptography``,
qui est déjà une dépendance transitive de ``google-genai``.

**Modèle de menace** :
- La base SQLite locale peut être copiée (backup, partage du dossier).
- Un attaquant ayant accès au fichier ``.db`` ne doit pas pouvoir extraire
  la clé Gemini en clair.
- L'attaquant *avec* accès au système de fichiers **ET** à la clé maître
  peut tout déchiffrer — c'est attendu (on n'est pas un HSM).

**Clé maître** :
- Sur Streamlit Cloud : récupérée via ``st.secrets["LLM_VAULT_KEY"]``.
- En local : variable d'env ``LLM_VAULT_KEY``.
- Fallback dev : clé dérivée d'un fichier ``data/.vault_key`` créé au
  premier lancement (mode développement, pas pour la prod).

**Format de stockage en base** :
- Préfixe ``enc:v1:`` suivi du token Fernet en base64url. Le préfixe permet
  de détecter les anciennes valeurs en clair lors d'une migration.
- Une chaîne vide reste vide (pas de clé configurée).
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

ENC_PREFIX = "enc:v1:"
_VAULT_KEY_FILE = Path(__file__).resolve().parent.parent / "data" / ".vault_key"


def _load_vault_key() -> bytes:
    """Charge la clé maître Fernet (32 octets base64url).

    Priorité : st.secrets > variable d'env > fichier local dev.
    """
    # 1. Streamlit secrets (prod)
    try:
        import streamlit as st  # noqa: PLC0415
        if "LLM_VAULT_KEY" in st.secrets:
            return st.secrets["LLM_VAULT_KEY"].encode("ascii")
    except Exception:
        # st.secrets peut lever si pas dans un contexte Streamlit
        pass

    # 2. Variable d'environnement
    env_key = os.environ.get("LLM_VAULT_KEY")
    if env_key:
        return env_key.encode("ascii")

    # 3. Fichier local (dev fallback)
    if _VAULT_KEY_FILE.exists():
        return _VAULT_KEY_FILE.read_bytes().strip()

    # 4. Première exécution : on génère une clé déterministe basée sur le
    # chemin d'install. Mauvais pour la prod (clé prévisible si chemin connu)
    # mais OK pour le dev local où la DB ne quitte pas la machine.
    _VAULT_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    new_key = Fernet.generate_key()
    _VAULT_KEY_FILE.write_bytes(new_key)
    try:
        _VAULT_KEY_FILE.chmod(0o600)
    except OSError:
        pass
    return new_key


_fernet: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    """Cache du Fernet pour éviter de relire la clé à chaque appel."""
    global _fernet
    if _fernet is None:
        key = _load_vault_key()
        # Si la clé n'est pas au format Fernet attendu (44 chars base64url),
        # on la dérive via SHA-256 → base64 pour normaliser.
        if len(key) != 44 or not key.endswith(b"="):
            digest = hashlib.sha256(key).digest()
            key = base64.urlsafe_b64encode(digest)
        _fernet = Fernet(key)
    return _fernet


def encrypt_api_key(plaintext: str) -> str:
    """Chiffre une clé API. Renvoie une chaîne vide si l'entrée est vide.

    Le résultat est préfixé par ``enc:v1:`` pour permettre la détection
    lors d'une migration de valeurs en clair.
    """
    plaintext = (plaintext or "").strip()
    if not plaintext:
        return ""
    # Si déjà chiffré (idempotence), on ne re-chiffre pas
    if plaintext.startswith(ENC_PREFIX):
        return plaintext
    token = _get_fernet().encrypt(plaintext.encode("utf-8"))
    return ENC_PREFIX + token.decode("ascii")


def decrypt_api_key(ciphertext: str) -> str:
    """Déchiffre une clé API stockée.

    - Si la valeur ne commence pas par ``enc:v1:``, on suppose qu'elle
      est en clair (legacy) et on la retourne telle quelle.
    - Si le déchiffrement échoue (clé maître changée, corruption), on
      retourne une chaîne vide pour que l'utilisateur puisse la re-saisir.
    """
    if not ciphertext:
        return ""
    if not ciphertext.startswith(ENC_PREFIX):
        # Valeur legacy en clair — l'appelant doit la re-chiffrer au prochain save
        return ciphertext
    token = ciphertext[len(ENC_PREFIX):].encode("ascii")
    try:
        return _get_fernet().decrypt(token).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def is_encrypted(value: str) -> bool:
    """Vrai si la valeur est déjà chiffrée (utile pour migrations)."""
    return bool(value) and value.startswith(ENC_PREFIX)


def mask_for_display(plaintext: str, visible_chars: int = 4) -> str:
    """Renvoie une version masquée pour l'UI : ``••••••••XXXX``."""
    if not plaintext:
        return ""
    if len(plaintext) <= visible_chars:
        return "•" * len(plaintext)
    return "•" * 8 + plaintext[-visible_chars:]


__all__ = [
    "encrypt_api_key",
    "decrypt_api_key",
    "is_encrypted",
    "mask_for_display",
    "ENC_PREFIX",
]
