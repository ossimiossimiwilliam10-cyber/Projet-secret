"""Tests du service de backup / restauration.

Sécurise le contrat : créer un zip, le restaurer, vérifier que le
contenu est intact (round-trip).

Le service `restore_from_zip` est strict :
  1. zip valide
  2. présence de planning.db
  3. magic bytes SQLite
  4. vrai fichier SQLite ouvrable avec ≥ 1 utilisateur
  5. hash DB_SHA256 cohérent avec le MANIFEST

Les tests construisent donc de VRAIES bases SQLite (pas du faux contenu)
et passent par `create_backup_zip()` pour produire des zips au manifest
valide.
"""

from __future__ import annotations

import io
import sqlite3
import zipfile
from pathlib import Path

import pytest


def _make_real_sqlite_db(path: Path, marker: str = "X") -> None:
    """Crée une vraie base SQLite minimale avec une table `utilisateurs`
    contenant 1 ligne (pour passer le check 'backup vide').

    Le `marker` est stocké dans la table pour pouvoir distinguer deux
    bases dans les assertions de round-trip.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS utilisateurs (id INTEGER PRIMARY KEY, nom TEXT)")
        conn.execute("DELETE FROM utilisateurs")
        conn.execute("INSERT INTO utilisateurs (nom) VALUES (?)", (marker,))
        conn.commit()
    finally:
        conn.close()


def _read_marker(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT nom FROM utilisateurs LIMIT 1").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Redirige BASE_DIR / DATA_DIR / DB_PATH / PDF_DIR vers un tmp_path
    pour isoler les tests du vrai disque. Crée une vraie DB SQLite."""
    import database.db as db_mod
    import services.backup_service as backup_mod

    data_dir = tmp_path / "data"
    pdf_dir = data_dir / "pdfs"
    pdf_dir.mkdir(parents=True)
    db_path = data_dir / "planning.db"
    _make_real_sqlite_db(db_path, marker="ORIGINAL")

    monkeypatch.setattr(db_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(db_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(db_mod, "DB_PATH", str(db_path))
    monkeypatch.setattr(db_mod, "PDF_DIR", pdf_dir)
    monkeypatch.setattr(backup_mod, "DB_PATH", str(db_path))
    monkeypatch.setattr(backup_mod, "PDF_DIR", pdf_dir)

    yield tmp_path


def test_create_backup_zip_contient_db_et_pdfs(tmp_data_dir):
    from services.backup_service import create_backup_zip
    import services.backup_service as backup_mod

    (Path(backup_mod.PDF_DIR) / "chap1.pdf").write_bytes(b"PDF1")
    (Path(backup_mod.PDF_DIR) / "chap2.pdf").write_bytes(b"PDF2")

    zip_bytes = create_backup_zip()
    assert isinstance(zip_bytes, bytes)
    assert len(zip_bytes) > 0

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "planning.db" in names
        assert "pdfs/chap1.pdf" in names
        assert "pdfs/chap2.pdf" in names
        assert "MANIFEST.txt" in names
        # La DB embarquée est un vrai SQLite (magic bytes).
        assert zf.read("planning.db")[:16] == b"SQLite format 3\x00"
        assert zf.read("pdfs/chap1.pdf") == b"PDF1"


def test_restore_from_zip_round_trip(tmp_data_dir):
    """Crée un backup, modifie la base, restaure → doit retrouver l'état initial."""
    from services.backup_service import create_backup_zip, restore_from_zip
    import services.backup_service as backup_mod

    (Path(backup_mod.PDF_DIR) / "original.pdf").write_bytes(b"ORIGINAL")
    backup = create_backup_zip()

    # Pollue la base avec un AUTRE vrai SQLite + un PDF intrus.
    _make_real_sqlite_db(Path(backup_mod.DB_PATH), marker="MODIFIE")
    (Path(backup_mod.PDF_DIR) / "intrus.pdf").write_bytes(b"INTRUS")

    resultat = restore_from_zip(backup)
    assert resultat["db_restauree"] is True
    assert resultat["nb_pdfs"] == 1  # seulement original.pdf après restauration

    # L'état initial est rétabli (marqueur ORIGINAL de la DB sauvegardée).
    assert _read_marker(Path(backup_mod.DB_PATH)) == "ORIGINAL"
    assert (Path(backup_mod.PDF_DIR) / "original.pdf").read_bytes() == b"ORIGINAL"
    # Le PDF intrus a été supprimé (pas dans la sauvegarde).
    assert not (Path(backup_mod.PDF_DIR) / "intrus.pdf").exists()
    # Un backup défensif de la DB pré-restauration doit être créé.
    assert resultat["backup_path"] is not None
    assert Path(resultat["backup_path"]).exists()


def test_restore_rejette_zip_sans_db(tmp_data_dir):
    """Si le zip ne contient pas planning.db, on lève une erreur claire."""
    from services.backup_service import restore_from_zip

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as zf:
        zf.writestr("autre.txt", "rien à voir")

    with pytest.raises(ValueError, match="planning.db"):
        restore_from_zip(buffer.getvalue())


def test_restore_rejette_fichier_invalide(tmp_data_dir):
    """Un blob qui n'est pas un zip → ValueError, pas un crash bas-niveau."""
    from services.backup_service import restore_from_zip
    with pytest.raises(ValueError, match="zip invalide"):
        restore_from_zip(b"\x00\x01ceci n'est pas un zip")


def test_restore_rejette_db_sans_magic_bytes(tmp_data_dir):
    """Un planning.db sans magic bytes SQLite est rejeté pour éviter de
    corrompre l'app au prochain démarrage."""
    from services.backup_service import restore_from_zip

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as zf:
        zf.writestr("planning.db", b"MZ\x90\x00This is an executable not a DB")
    with pytest.raises(ValueError, match="SQLite"):
        restore_from_zip(buffer.getvalue())


def test_restore_rejette_backup_vide(tmp_data_dir):
    """Une DB SQLite valide mais SANS utilisateur est refusée (protège
    contre l'écrasement des données par un backup vide)."""
    from services.backup_service import restore_from_zip

    # Vraie DB SQLite avec table utilisateurs VIDE.
    empty_db = tmp_data_dir / "empty.db"
    conn = sqlite3.connect(str(empty_db))
    conn.execute("CREATE TABLE utilisateurs (id INTEGER PRIMARY KEY, nom TEXT)")
    conn.commit()
    conn.close()
    db_bytes = empty_db.read_bytes()

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as zf:
        zf.writestr("planning.db", db_bytes)
    with pytest.raises(ValueError, match="vide"):
        restore_from_zip(buffer.getvalue())


def test_restore_cree_backup_defensif_de_la_db_actuelle(tmp_data_dir):
    """Avant d'écraser, l'ancienne DB doit être copiée en .db.bak."""
    from services.backup_service import create_backup_zip, restore_from_zip
    import services.backup_service as backup_mod

    # État actuel = DB avec marqueur ORIGINAL (posé par la fixture).
    # On en fait un backup valide AVANT de polluer.
    backup = create_backup_zip()
    contenu_actuel = Path(backup_mod.DB_PATH).read_bytes()

    # On pollue avec un autre état pour vérifier que le .bak garde l'actuel.
    _make_real_sqlite_db(Path(backup_mod.DB_PATH), marker="POLLUTION")
    contenu_avant_restore = Path(backup_mod.DB_PATH).read_bytes()

    resultat = restore_from_zip(backup)
    backup_path = Path(resultat["backup_path"])
    assert backup_path.exists()
    # Le .bak contient l'état AVANT la restauration (POLLUTION), pas l'original.
    assert backup_path.read_bytes() == contenu_avant_restore
    # Et la DB restaurée a bien retrouvé le marqueur ORIGINAL.
    assert _read_marker(Path(backup_mod.DB_PATH)) == "ORIGINAL"
