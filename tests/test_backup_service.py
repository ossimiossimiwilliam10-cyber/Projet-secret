"""Tests du service de backup / restauration.

Sécurise le contrat : créer un zip, le restaurer, vérifier que le
contenu est intact (round-trip).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Redirige BASE_DIR / DATA_DIR / DB_PATH / PDF_DIR vers un tmp_path
    pour isoler les tests du vrai disque."""
    import database.db as db_mod
    import services.backup_service as backup_mod

    data_dir = tmp_path / "data"
    pdf_dir = data_dir / "pdfs"
    pdf_dir.mkdir(parents=True)
    db_path = data_dir / "planning.db"
    # Doit commencer par les magic bytes SQLite pour passer la validation
    # de `restore_from_zip` (ajoutée pour empêcher la restauration de
    # fichiers .db cabossés).
    db_path.write_bytes(b"SQLite format 3\x00FAKE_SQLITE_CONTENT_FOR_TEST")

    monkeypatch.setattr(db_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(db_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(db_mod, "DB_PATH", str(db_path))
    monkeypatch.setattr(db_mod, "PDF_DIR", pdf_dir)
    # Le module backup_service utilise les valeurs au moment de l'import
    # via Path() — on patch aussi directement ces noms.
    monkeypatch.setattr(backup_mod, "DB_PATH", str(db_path))
    monkeypatch.setattr(backup_mod, "PDF_DIR", pdf_dir)

    yield tmp_path


def test_create_backup_zip_contient_db_et_pdfs(tmp_data_dir):
    from services.backup_service import create_backup_zip
    import services.backup_service as backup_mod

    # Ajoute 2 PDFs factices.
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

        # Vérifie l'intégrité du contenu.
        assert zf.read("planning.db") == (
            b"SQLite format 3\x00FAKE_SQLITE_CONTENT_FOR_TEST"
        )
        assert zf.read("pdfs/chap1.pdf") == b"PDF1"


def test_restore_from_zip_round_trip(tmp_data_dir):
    """Crée un backup, modifie la base, restaure → doit retrouver l'état initial."""
    from services.backup_service import create_backup_zip, restore_from_zip
    import services.backup_service as backup_mod

    (Path(backup_mod.PDF_DIR) / "original.pdf").write_bytes(b"ORIGINAL")
    backup = create_backup_zip()

    # Pollue la base avec du contenu différent (toujours valide SQLite-magic-wise).
    Path(backup_mod.DB_PATH).write_bytes(b"SQLite format 3\x00AFTER_MODIFICATION")
    (Path(backup_mod.PDF_DIR) / "intrus.pdf").write_bytes(b"INTRUS")

    resultat = restore_from_zip(backup)
    assert resultat["db_restauree"] is True
    assert resultat["nb_pdfs"] == 1  # seulement original.pdf après restauration.

    # L'état initial est rétabli.
    assert Path(backup_mod.DB_PATH).read_bytes() == (
        b"SQLite format 3\x00FAKE_SQLITE_CONTENT_FOR_TEST"
    )
    assert (Path(backup_mod.PDF_DIR) / "original.pdf").read_bytes() == b"ORIGINAL"
    # Le PDF intrus a été supprimé (pas dans la sauvegarde).
    assert not (Path(backup_mod.PDF_DIR) / "intrus.pdf").exists()
    # Un backup défensif de la DB pré-restauration doit être créé.
    assert resultat["backup_path"] is not None
    assert Path(resultat["backup_path"]).exists()


def test_restore_rejette_zip_sans_db(tmp_data_dir):
    """Si le zip ne contient pas planning.db, on lève une erreur claire
    plutôt que de corrompre la base."""
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
    """Un planning.db qui n'a pas les magic bytes SQLite est rejeté pour
    éviter de corrompre l'app au prochain démarrage."""
    from services.backup_service import restore_from_zip

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as zf:
        # Pas les magic bytes SQLite — un .exe renommé .db par exemple.
        zf.writestr("planning.db", b"MZ\x90\x00This is an executable not a DB")
    with pytest.raises(ValueError, match="magic bytes SQLite"):
        restore_from_zip(buffer.getvalue())


def test_restore_cree_backup_defensif_de_la_db_actuelle(tmp_data_dir):
    """Avant d'écraser, l'ancienne DB doit être copiée en .db.bak."""
    from services.backup_service import create_backup_zip, restore_from_zip
    import services.backup_service as backup_mod

    # État A (l'état actuel à backuper avant restauration).
    contenu_actuel = b"SQLite format 3\x00CURRENT_STATE_AVANT_RESTORE"
    Path(backup_mod.DB_PATH).write_bytes(contenu_actuel)

    # Construit un zip de restauration avec un contenu différent.
    sauvegarde = io.BytesIO()
    with zipfile.ZipFile(sauvegarde, mode="w") as zf:
        zf.writestr("planning.db", b"SQLite format 3\x00OLDER_STATE")

    resultat = restore_from_zip(sauvegarde.getvalue())
    backup_path = Path(resultat["backup_path"])
    assert backup_path.exists()
    # Le backup contient bien l'état AVANT la restauration.
    assert backup_path.read_bytes() == contenu_actuel
