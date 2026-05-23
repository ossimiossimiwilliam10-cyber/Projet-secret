"""Sauvegarde / restauration locale de la base et des PDFs.

Streamlit Cloud peut, en cas de redéploiement d'instance, perdre les
fichiers locaux (notamment ``data/planning.db`` et ``data/pdfs/``).
Tout l'avancement Leitner, l'XP, les chapitres, les analyses IA mises
en cache, sont dans ces fichiers — c'est un risque silencieux pour un
projet personnel utilisé sur plusieurs mois.

Ce service expose deux fonctions pures :

- :func:`create_backup_zip` produit un ``bytes`` contenant un zip de
  la base + des PDFs, avec un nom horodaté.
- :func:`restore_from_zip` prend un ``bytes`` zip et écrase la base +
  les PDFs sur disque, après backup défensif de l'état actuel.

Aucune dépendance externe — uniquement la stdlib (``zipfile``, ``io``).
"""

from __future__ import annotations

import datetime
import io
import shutil
import zipfile
from pathlib import Path

from database.db import BASE_DIR, DB_PATH, PDF_DIR

# Préfixe de tous les fichiers à l'intérieur du zip. Garantit qu'une
# restauration n'éparpille pas des fichiers dans tous les sens.
ARCHIVE_PREFIX = "exocerveau_backup"

# Magic bytes obligatoires en tête d'un fichier SQLite valide.
# Empêche de restaurer accidentellement un fichier renommé ".db" qui n'en
# serait pas un (et qui corromprait l'app au prochain démarrage).
SQLITE_MAGIC = b"SQLite format 3\x00"

# Chemins relatifs au repo, utilisés à l'écriture du zip et à la
# restauration. ``DB_PATH`` est absolu donc on le rebase sur BASE_DIR.
_DB_REL = Path(DB_PATH).relative_to(BASE_DIR) if Path(DB_PATH).is_absolute() else Path(DB_PATH)
_PDF_REL = Path(PDF_DIR).relative_to(BASE_DIR) if Path(PDF_DIR).is_absolute() else Path(PDF_DIR)


def make_backup_filename(now: datetime.datetime | None = None) -> str:
    """Nom de fichier suggéré pour le téléchargement, horodaté.

    Format : ``exocerveau_backup_YYYY-MM-DD_HH-MM.zip``.
    """
    now = now or datetime.datetime.now()
    return f"{ARCHIVE_PREFIX}_{now.strftime('%Y-%m-%d_%H-%M')}.zip"


def create_backup_zip() -> bytes:
    """Construit en mémoire un zip qui contient :

    - ``planning.db`` à la racine de l'archive (la base SQLite).
    - ``pdfs/<fichier>`` pour chaque PDF présent dans ``data/pdfs/``.
    - ``MANIFEST.txt`` avec la date et la version de schéma simplifiée
      (pour future compatibilité).

    Renvoie le contenu binaire prêt à être servi à
    ``st.download_button``. Aucun fichier temporaire sur disque.
    """
    buffer = io.BytesIO()

    db_path = Path(DB_PATH)
    pdf_dir = Path(PDF_DIR)

    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 1. Base SQLite (le fichier le plus important).
        if db_path.exists():
            zf.write(db_path, arcname="planning.db")

        # 2. Tous les PDFs (les supports de cours).
        if pdf_dir.exists():
            for pdf_file in sorted(pdf_dir.iterdir()):
                if pdf_file.is_file() and pdf_file.suffix.lower() == ".pdf":
                    zf.write(pdf_file, arcname=f"pdfs/{pdf_file.name}")

        # 3. Manifest pour traçabilité.
        manifest = (
            f"Exocerveau backup\n"
            f"Date : {datetime.datetime.now().isoformat(timespec='seconds')}\n"
            f"DB présente : {db_path.exists()}\n"
            f"Nb PDFs : {sum(1 for _ in pdf_dir.glob('*.pdf')) if pdf_dir.exists() else 0}\n"
        )
        zf.writestr("MANIFEST.txt", manifest)

    return buffer.getvalue()


def restore_from_zip(zip_bytes: bytes) -> dict[str, int | str]:
    """Restaure la base et les PDFs depuis un zip produit par
    :func:`create_backup_zip`.

    **DESTRUCTIF** : écrase ``data/planning.db`` et le contenu de
    ``data/pdfs/``. L'appelant doit confirmer auprès de l'utilisateur
    avant d'invoquer.

    Garde-fous mis en place :
      1. Validation du zip lui-même (BadZipFile rejette).
      2. Présence de ``planning.db`` dans l'archive.
      3. Validation des **magic bytes SQLite** : empêche de restaurer
         un fichier renommé ``.db`` qui n'en serait pas un (sinon l'app
         crasherait au démarrage suivant).
      4. **Backup défensif** de la DB actuelle vers ``planning.db.bak``
         avant écrasement. Si l'utilisateur s'aperçoit que la restauration
         a effacé des données non sauvegardées, il peut récupérer.
      5. **Écriture atomique** : on extrait d'abord vers ``planning.db.tmp``
         puis rename, pour qu'une interruption à mi-écriture ne laisse
         pas une DB partielle.

    Args:
        zip_bytes: contenu binaire du fichier .zip.

    Returns:
        Dict ``{"db_restauree": bool, "nb_pdfs": int, "manifest": str,
        "backup_path": str|None}``.

    Raises:
        ValueError: zip invalide, planning.db absent, ou magic bytes
            SQLite incorrects.
    """
    db_path = Path(DB_PATH)
    pdf_dir = Path(PDF_DIR)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Fichier zip invalide : {exc}") from exc

    with zf:
        noms = zf.namelist()
        if "planning.db" not in noms:
            raise ValueError(
                "Ce zip ne contient pas de planning.db — "
                "ce n'est probablement pas une sauvegarde Exocerveau."
            )

        # 1. Validation magic bytes SQLite (avant tout effet de bord).
        with zf.open("planning.db") as src:
            head = src.read(len(SQLITE_MAGIC))
        if head != SQLITE_MAGIC:
            raise ValueError(
                "Le `planning.db` du zip n'a pas les magic bytes SQLite "
                "valides — refus pour éviter de corrompre l'app."
            )

        # 2. Backup défensif de l'état actuel (DB only — les PDFs sont moins
        # critiques et déjà dans le zip si l'utilisateur en avait).
        backup_path: Path | None = None
        if db_path.exists():
            backup_path = db_path.with_suffix(".db.bak")
            try:
                shutil.copy2(db_path, backup_path)
            except OSError:
                backup_path = None

        # 3. Vider data/pdfs/ avant restauration (sinon on accumule des
        # PDFs orphelins de l'ancienne installation).
        for f in pdf_dir.glob("*.pdf"):
            try:
                f.unlink()
            except OSError:
                pass

        # 4. Extraction atomique de la DB : écriture dans .tmp puis rename.
        tmp_path = db_path.with_suffix(".db.tmp")
        try:
            with zf.open("planning.db") as src, tmp_path.open("wb") as dst:
                dst.write(src.read())
            tmp_path.replace(db_path)  # rename atomique sur la plupart des FS
        except Exception:
            # Échec d'écriture : nettoyage du tmp.
            tmp_path.unlink(missing_ok=True)
            raise

        # 5. Extraction des PDFs.
        nb_pdfs = 0
        for name in noms:
            if name.startswith("pdfs/") and not name.endswith("/"):
                cible = pdf_dir / Path(name).name
                with zf.open(name) as src, cible.open("wb") as dst:
                    dst.write(src.read())
                nb_pdfs += 1

        manifest = (
            zf.read("MANIFEST.txt").decode("utf-8")
            if "MANIFEST.txt" in noms else ""
        )

    return {
        "db_restauree": True,
        "nb_pdfs": nb_pdfs,
        "manifest": manifest,
        "backup_path": str(backup_path) if backup_path else None,
    }


__all__ = [
    "make_backup_filename",
    "create_backup_zip",
    "restore_from_zip",
]
