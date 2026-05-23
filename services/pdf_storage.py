"""Utilitaires de **sécurité** et **maintenance** pour les PDFs uploadés.

Refonte NASA — chantier 4 (sécurité fichiers) :

- :func:`validate_pdf_upload`     — refuse les fichiers trop gros, non-PDF,
                                    ou aux noms suspects.
- :func:`safe_pdf_filename`        — génère un nom de fichier déterministe,
                                    sans path traversal possible.
- :func:`find_orphan_pdfs`         — liste les PDFs sur disque qui ne sont
                                    plus référencés par aucun chapitre.
- :func:`cleanup_orphan_pdfs`      — supprime ces orphelins (avec dry-run).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from database.db import PDF_DIR

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# 25 Mio : un cours de 500 pages denses fait ~15 Mio en pratique.
MAX_PDF_SIZE_BYTES = 25 * 1024 * 1024

# Signature magique des PDFs (en-tête "%PDF-")
PDF_MAGIC = b"%PDF-"


class PdfValidationError(ValueError):
    """Erreur métier de validation d'un upload PDF."""


def validate_pdf_upload(file_bytes: bytes, filename: str) -> None:
    """Lève :class:`PdfValidationError` si le PDF est invalide.

    Critères :
      - taille ≤ 25 Mio
      - en-tête commence par ``%PDF-``
      - nom de fichier non vide, sans caractères de path traversal
    """
    if not file_bytes:
        raise PdfValidationError("Fichier vide.")

    size = len(file_bytes)
    if size > MAX_PDF_SIZE_BYTES:
        size_mio = size / (1024 * 1024)
        raise PdfValidationError(
            f"PDF trop gros ({size_mio:.1f} Mio) — limite : "
            f"{MAX_PDF_SIZE_BYTES // (1024 * 1024)} Mio."
        )

    # Vérifie la signature magique : empêche d'uploader un .exe renommé .pdf.
    if not file_bytes.startswith(PDF_MAGIC):
        raise PdfValidationError(
            "Le fichier n'a pas la signature d'un PDF valide "
            "(en-tête %PDF- manquant)."
        )

    if not filename or not filename.strip():
        raise PdfValidationError("Nom de fichier vide.")

    # Path traversal : refuse `..`, `/`, `\`.
    if any(ch in filename for ch in ("..", "/", "\\", "\0")):
        raise PdfValidationError(
            f"Nom de fichier suspect (caractères interdits) : {filename!r}"
        )


def safe_pdf_filename(matiere_id: int, timestamp: int, index: int) -> str:
    """Génère un nom de PDF déterministe et sûr.

    Format : ``m{matiere_id}-{timestamp}-{index}.pdf`` — aucun caractère
    utilisateur ne s'y glisse.
    """
    if matiere_id < 0 or timestamp < 0 or index < 0:
        raise ValueError("Les composants du nom doivent être positifs.")
    return f"m{int(matiere_id)}-{int(timestamp)}-{int(index)}.pdf"


def find_orphan_pdfs(session: "Session") -> list[Path]:
    """Retourne la liste des PDFs sur disque non référencés par un chapitre.

    Un PDF est référencé s'il apparaît dans le champ JSON ``pdfs`` d'un
    chapitre actif.
    """
    from database import Chapitre

    referenced: set[str] = set()
    for chap in session.query(Chapitre).all():
        for entry in (chap.pdfs or []):
            if isinstance(entry, dict) and entry.get("path"):
                referenced.add(Path(entry["path"]).name)

    on_disk = [p for p in PDF_DIR.glob("*.pdf") if p.is_file()]
    return [p for p in on_disk if p.name not in referenced]


def cleanup_orphan_pdfs(session: "Session", dry_run: bool = True) -> list[str]:
    """Supprime les PDFs orphelins. Retourne la liste des noms traités.

    Par défaut en ``dry_run=True`` : aucune suppression réelle, juste
    l'aperçu de ce qui serait supprimé.
    """
    orphans = find_orphan_pdfs(session)
    if not dry_run:
        for p in orphans:
            try:
                p.unlink()
            except OSError:
                pass
    return [p.name for p in orphans]


__all__ = [
    "MAX_PDF_SIZE_BYTES",
    "PdfValidationError",
    "validate_pdf_upload",
    "safe_pdf_filename",
    "find_orphan_pdfs",
    "cleanup_orphan_pdfs",
]
