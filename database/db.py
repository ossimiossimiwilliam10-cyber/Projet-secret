"""
Initialisation de la base SQLite et de la session SQLAlchemy.

Tout est local : la base se trouve dans ``planning_app/data/planning.db``
et les PDFs des cours dans ``planning_app/data/pdfs/``.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

# ---------------------------------------------------------------------------
# Chemins
# ---------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = BASE_DIR / "data"
PDF_DIR: Path = DATA_DIR / "pdfs"

DATA_DIR.mkdir(parents=True, exist_ok=True)
PDF_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH: Path = DATA_DIR / "planning.db"
DATABASE_URL: str = f"sqlite:///{DB_PATH}"

# ---------------------------------------------------------------------------
# Moteur & session
# ---------------------------------------------------------------------------
engine: Engine = create_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):  # noqa: D401
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)

Base = declarative_base()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def init_db() -> None:
    """Crée toutes les tables si elles n'existent pas déjà."""
    from . import models  # noqa: F401  (import à effet de bord volontaire)
    Base.metadata.create_all(bind=engine)


def get_session() -> Session:
    """Retourne une nouvelle session SQLAlchemy."""
    return SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager qui commit en cas de succès et rollback sinon."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_db() -> None:
    """Supprime toutes les tables, les recrée, et vide le dossier PDF (phase de test)."""
    from . import models  # noqa: F401
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    if PDF_DIR.exists():
        for file in PDF_DIR.iterdir():
            if file.is_file() and file.suffix.lower() == ".pdf":
                try:
                    file.unlink()
                except Exception as e:
                    print(f"Impossible de supprimer le fichier {file} : {e}")


# ---------------------------------------------------------------------------
# Migration douce du schéma — pour ajouter des colonnes sans détruire les données
# ---------------------------------------------------------------------------
# Liste des colonnes attendues sur chaque table.
# Format : table_name -> { col_name: "SQL_TYPE_AND_DEFAULT" }
# Cette liste doit refléter ce qu'on a ajouté APRÈS la création initiale d'une BD.
# Les nouvelles tables (comme `jobs`) sont créées automatiquement par init_db().
_EXPECTED_COLUMNS = {
    "chapitres": {
        # Algo Leitner
        "niveau_actuel":   "INTEGER DEFAULT 0",
        "date_prochaine":  "DATE",
        "historique_quiz": "JSON",
        # Données IA + caches
        "fiche_ia":        "TEXT",
        "qcm_cache":       "JSON",
        "quiz_cache":      "JSON",
        "texte_cache":     "TEXT",
        # Notes perso
        "notes":           "TEXT DEFAULT ''",
        # Horodatages (ajoutés en Phase A — restaient à migrer)
        "created_at":      "DATETIME",
        "updated_at":      "DATETIME",
    },
    "profil": {
        # Gamification (F3a)
        "xp":                       "INTEGER DEFAULT 0",
        "niveau":                   "INTEGER DEFAULT 1",
        "streak_jours":             "INTEGER DEFAULT 0",
        "streak_record":            "INTEGER DEFAULT 0",
        "derniere_activite_xp":     "DATE",
        "nb_quiz_total":            "INTEGER DEFAULT 0",
        "nb_chapitres_maitrise":    "INTEGER DEFAULT 0",
        "nb_seances_sport_total":   "INTEGER DEFAULT 0",
        "replanning_auto_actif":    "BOOLEAN DEFAULT 1",
        # Quota d'étude — cours + révisions perso confondus.
        "heures_etude_cible_par_semaine": "FLOAT DEFAULT 21.0",
        "heures_etude_plafond_par_jour":  "FLOAT DEFAULT 6.0",
    },
    "jobs": {
        # Lieu du job — pour croiser avec trajets_habituels du profil.
        "lieu": "VARCHAR(200) DEFAULT ''",
    },
    # `ues`, `matieres`, `achievements`, `objectifs` sont ENTIÈREMENT
    # créées par create_all() (pas besoin de migration des colonnes existantes).
}


def migrate_schema(verbose: bool = True) -> dict[str, list[str]]:
    """Ajoute en douceur les colonnes manquantes via ALTER TABLE.

    Cette fonction est **idempotente** et **sûre** : si la colonne existe déjà,
    elle est laissée tranquille. Si le schéma est à jour, aucune modif n'est faite.

    À appeler après ``init_db()`` au démarrage de l'app — c'est ce qui permet
    d'ajouter ``texte_cache``, ``qcm_cache``, etc. à une BD existante **sans
    perdre les données** (cours, chapitres, plannings).

    Returns:
        dict { table_name: [colonnes ajoutées] } — utile pour logger / afficher.
    """
    ajouts: dict[str, list[str]] = {}
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table_name, expected_cols in _EXPECTED_COLUMNS.items():
            if table_name not in existing_tables:
                # init_db() s'en occupera (CREATE TABLE)
                continue

            existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
            for col_name, col_sql in expected_cols.items():
                if col_name in existing_cols:
                    continue
                try:
                    conn.execute(
                        text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_sql}")
                    )
                    ajouts.setdefault(table_name, []).append(col_name)
                    if verbose:
                        print(f"[migrate_schema] ✓ {table_name}.{col_name} ({col_sql})")
                except Exception as exc:  # noqa: BLE001
                    if verbose:
                        print(f"[migrate_schema] ✗ {table_name}.{col_name} : {exc}")

    if verbose and not ajouts:
        print("[migrate_schema] Schéma déjà à jour, rien à faire.")
    return ajouts


__all__ = [
    "BASE_DIR",
    "DATA_DIR",
    "PDF_DIR",
    "DB_PATH",
    "DATABASE_URL",
    "engine",
    "SessionLocal",
    "Base",
    "init_db",
    "get_session",
    "session_scope",
    "reset_db",
    "migrate_schema",
]