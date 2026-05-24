"""Sauvegarde / restauration de la base et des PDFs — version enrichie.

Features :
- Manifest détaillé (stats, hash)
- Vérification d'intégrité SHA256
- Auto-backup silencieux
- Rétention automatique (5 derniers)
- Rappel de backup
"""

from __future__ import annotations

import datetime
import hashlib
import io
import shutil
import sqlite3
import zipfile
from pathlib import Path

from database.db import BASE_DIR, DATA_DIR, DB_PATH, PDF_DIR

ARCHIVE_PREFIX = "exocerveau_backup"
SQLITE_MAGIC = b"SQLite format 3\x00"

# Dossier de stockage des auto-backups
AUTO_BACKUP_DIR = DATA_DIR / "auto_backups"
AUTO_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
MAX_AUTO_BACKUPS = 5

# Fichier trace du dernier backup (timestamp)
_LAST_BACKUP_FILE = DATA_DIR / ".last_backup"


# ===========================================================================
# Manifest enrichi
# ===========================================================================
def _build_manifest() -> str:
    """Construit un manifest détaillé avec stats DB."""
    db_path = Path(DB_PATH)
    pdf_dir = Path(PDF_DIR)
    db_size = db_path.stat().st_size if db_path.exists() else 0
    nb_pdfs = sum(1 for _ in pdf_dir.glob("*.pdf")) if pdf_dir.exists() else 0
    pdfs_size = sum(f.stat().st_size for f in pdf_dir.glob("*.pdf")) if pdf_dir.exists() else 0

    # Stats DB
    stats = {}
    if db_path.exists():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            c = conn.cursor()
            for table, label in [
                ("utilisateurs", "Profil"), ("semestres", "Semestres"),
                ("ues", "UE"), ("matieres", "Matieres"), ("chapitres", "Chapitres"),
                ("semaines", "Semaines"), ("taches", "Taches"),
            ]:
                try:
                    c.execute(f"SELECT COUNT(*) FROM {table}")
                    stats[table] = c.fetchone()[0]
                except Exception:
                    stats[table] = "?"
            # XP/Niveau
            try:
                c.execute("SELECT xp, niveau, streak_jours FROM gamification_state LIMIT 1")
                row = c.fetchone()
                if row:
                    stats["XP"], stats["Niveau"], stats["Streak"] = row[0], row[1], row[2]
            except Exception:
                pass
            conn.close()
        except Exception:
            pass

    lines = [
        "Exocerveau backup",
        f"Date : {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"DB : {db_size / 1024:.0f} Ko",
        f"PDFs : {nb_pdfs} fichiers ({pdfs_size / 1024:.0f} Ko)",
        f"Total : {(db_size + pdfs_size) / 1024:.0f} Ko",
    ]
    if stats:
        lines.append(f"Profil : {'oui' if stats.get('utilisateurs', 0) else 'non'}")
        lines.append(f"Semestres : {stats.get('semestres', '?')} | UE : {stats.get('ues', '?')} | Matieres : {stats.get('matieres', '?')} | Chapitres : {stats.get('chapitres', '?')}")
        lines.append(f"Semaines : {stats.get('semaines', '?')} | Taches : {stats.get('taches', '?')}")
        xp = stats.get("XP", "?")
        niv = stats.get("Niveau", "?")
        streak = stats.get("Streak", "?")
        lines.append(f"XP : {xp} | Niveau : {niv} | Streak : {streak} jours")

    return "\n".join(lines)


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ===========================================================================
# Création
# ===========================================================================
def make_backup_filename(now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now()
    return f"{ARCHIVE_PREFIX}_{now.strftime('%Y-%m-%d_%H-%M')}.zip"


def create_backup_zip() -> bytes:
    db_path = Path(DB_PATH)
    pdf_dir = Path(PDF_DIR)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        if db_path.exists():
            zf.write(db_path, arcname="planning.db")
        if pdf_dir.exists():
            for pdf_file in sorted(pdf_dir.iterdir()):
                if pdf_file.is_file() and pdf_file.suffix.lower() == ".pdf":
                    zf.write(pdf_file, arcname=f"pdfs/{pdf_file.name}")
        manifest = _build_manifest()
        zf.writestr("MANIFEST.txt", manifest)

    zip_bytes = buffer.getvalue()
    sha = _compute_sha256(zip_bytes)

    # Réécrire avec le hash dans le manifest
    buffer2 = io.BytesIO()
    with zipfile.ZipFile(buffer2, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        if db_path.exists():
            zf.write(db_path, arcname="planning.db")
        if pdf_dir.exists():
            for pdf_file in sorted(pdf_dir.iterdir()):
                if pdf_file.is_file() and pdf_file.suffix.lower() == ".pdf":
                    zf.write(pdf_file, arcname=f"pdfs/{pdf_file.name}")
        zf.writestr("MANIFEST.txt", manifest + f"\nSHA256 : {sha}")

    return buffer2.getvalue()


# ===========================================================================
# Auto-backup
# ===========================================================================
def auto_backup() -> bool:
    """Sauvegarde automatique silencieuse (sans interaction utilisateur).
    Stocke dans ``data/auto_backups/``. Nettoie les plus vieux.
    Retourne True si le backup a été créé.
    """
    try:
        zip_bytes = create_backup_zip()
        filename = make_backup_filename()
        path = AUTO_BACKUP_DIR / filename
        path.write_bytes(zip_bytes)

        # Rétention : garder les MAX_AUTO_BACKUPS plus récents
        existing = sorted(AUTO_BACKUP_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in existing[MAX_AUTO_BACKUPS:]:
            try:
                old.unlink()
            except OSError:
                pass

        # Tracer le timestamp
        _LAST_BACKUP_FILE.write_text(datetime.datetime.now().isoformat())
        return True
    except Exception:
        return False


def get_last_backup_age_days() -> int | None:
    """Retourne le nombre de jours depuis le dernier backup, ou None si jamais fait."""
    try:
        if not _LAST_BACKUP_FILE.exists():
            backups = sorted(AUTO_BACKUP_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
            if backups:
                last_ts = datetime.datetime.fromtimestamp(backups[0].stat().st_mtime)
                return (datetime.datetime.now() - last_ts).days
            return None
        last_str = _LAST_BACKUP_FILE.read_text().strip()
        last_dt = datetime.datetime.fromisoformat(last_str)
        return (datetime.datetime.now() - last_dt).days
    except Exception:
        return None


# ===========================================================================
# Restauration (avec vérification intégrité)
# ===========================================================================
def restore_from_zip(zip_bytes: bytes) -> dict[str, int | str]:
    db_path = Path(DB_PATH)
    pdf_dir = Path(PDF_DIR)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # 0. Vérification intégrité SHA256 (si présent dans le manifest)
    try:
        zf_check = zipfile.ZipFile(io.BytesIO(zip_bytes))
        with zf_check:
            if "MANIFEST.txt" in zf_check.namelist():
                manifest_text = zf_check.read("MANIFEST.txt").decode("utf-8")
                for line in manifest_text.split("\n"):
                    if line.startswith("SHA256 : "):
                        expected_sha = line.replace("SHA256 : ", "").strip()
                        actual_sha = _compute_sha256(zip_bytes)
                        if expected_sha != actual_sha:
                            raise ValueError(
                                f"❌ Intégrité du backup compromise !\n"
                                f"Attendu : {expected_sha[:16]}...\n"
                                f"Reçu    : {actual_sha[:16]}...\n"
                                f"Le fichier a peut-être été corrompu pendant le transfert."
                            )
                        break
        zf_check.close()
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Fichier zip invalide : {exc}") from exc

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Fichier zip invalide : {exc}") from exc

    with zf:
        noms = zf.namelist()
        if "planning.db" not in noms:
            raise ValueError("Ce zip ne contient pas de planning.db.")

        with zf.open("planning.db") as src:
            head = src.read(len(SQLITE_MAGIC))
        if head != SQLITE_MAGIC:
            raise ValueError("Le planning.db du zip n'est pas une base SQLite valide.")

        backup_path: Path | None = None
        if db_path.exists():
            backup_path = db_path.with_suffix(".db.bak")
            try:
                shutil.copy2(db_path, backup_path)
            except OSError:
                backup_path = None

        for f in pdf_dir.glob("*.pdf"):
            try:
                f.unlink()
            except OSError:
                pass

        tmp_path = db_path.with_suffix(".db.tmp")
        try:
            with zf.open("planning.db") as src, tmp_path.open("wb") as dst:
                dst.write(src.read())
            tmp_path.replace(db_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

        nb_pdfs = 0
        for name in noms:
            if name.startswith("pdfs/") and not name.endswith("/"):
                cible = pdf_dir / Path(name).name
                with zf.open(name) as src, cible.open("wb") as dst:
                    dst.write(src.read())
                nb_pdfs += 1

        manifest = zf.read("MANIFEST.txt").decode("utf-8") if "MANIFEST.txt" in noms else ""

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
    "auto_backup",
    "get_last_backup_age_days",
]
