"""
Initialisation de la base SQLite et de la session SQLAlchemy.

Tout est local : la base se trouve dans ``planning_app/data/planning.db``
et les PDFs des cours dans ``planning_app/data/pdfs/``.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

import os
from dotenv import load_dotenv

try:
    from supabase import create_client, Client
except ImportError:
    Client = None
    create_client = None

load_dotenv()

# ---------------------------------------------------------------------------
# Supabase Client
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY and create_client:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------------------------------------------------------------------
# Chemins
# ---------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = BASE_DIR / "data"
PDF_DIR: Path = DATA_DIR / "pdfs"

DATA_DIR.mkdir(parents=True, exist_ok=True)
PDF_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Moteur & session
# ---------------------------------------------------------------------------
# Par défaut, on utilise la base locale SQLite. Si DATABASE_URL est fournie
# (ex: Supabase), on utilise PostgreSQL.
DB_PATH: Path = DATA_DIR / "planning.db"
DEFAULT_DATABASE_URL = f"sqlite:///{DB_PATH}"

DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine: Engine = create_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    connect_args=connect_args,
)

@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):  # noqa: D401
    if DATABASE_URL.startswith("sqlite"):
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
                except Exception as e:  # noqa: BLE001
                    logging.getLogger(__name__).warning(
                        "Impossible de supprimer le fichier %s : %s", file, e,
                    )


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
        # Versioning du cache IA — invalidation auto si modèle/prompt/contenu change.
        "fiche_ia_model":           "VARCHAR(100)",
        "fiche_ia_prompt_version":  "INTEGER",
        "fiche_ia_texte_sha":       "VARCHAR(64)",
        "fiche_ia_generated_at":    "DATETIME",
        "qcm_cache_model":          "VARCHAR(100)",
        "qcm_cache_prompt_version": "INTEGER",
        "qcm_cache_texte_sha":      "VARCHAR(64)",
        "quiz_cache_model":         "VARCHAR(100)",
        "quiz_cache_prompt_version":"INTEGER",
        "quiz_cache_texte_sha":     "VARCHAR(64)",
        # Flashcards & Corbeille (V2)
        "flashcards_cache":         "JSON",
        "trashed":                  "BOOLEAN DEFAULT 0",
        # Notes perso
        "notes":           "TEXT DEFAULT ''",
        # Versioning optimiste pour éviter les écrasements multi-onglets.
        "version":         "INTEGER DEFAULT 1",
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
    "taches": {
        # Flag d'idempotence pour bloquer le farming XP par toggle.
        "xp_attribue": "BOOLEAN DEFAULT 0",
    },
    "ues": {
        "semestre_id": "INTEGER REFERENCES semestres(id) ON DELETE SET NULL",
    },
    "systeme_config": {
        "google_maps_api_key": "VARCHAR(200) DEFAULT ''",
        "deepseek_api_key": "VARCHAR(500) DEFAULT ''",
        "deepseek_model": "VARCHAR(50) DEFAULT 'deepseek-chat'",
    },
    # `matieres`, `achievements`, `objectifs` sont ENTIÈREMENT
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
                        logging.getLogger(__name__).info(
                            "[migrate_schema] ✓ %s.%s (%s)",
                            table_name, col_name, col_sql,
                        )
                except Exception as exc:  # noqa: BLE001
                    if verbose:
                        logging.getLogger(__name__).warning(
                            "[migrate_schema] ✗ %s.%s : %s",
                            table_name, col_name, exc,
                        )

    if verbose and not ajouts:
        logging.getLogger(__name__).info(
            "[migrate_schema] Schéma déjà à jour, rien à faire.",
        )

    _backfill_duree_min(verbose=verbose)
    _backfill_j5_insertion(verbose=verbose)
    return ajouts


def _backfill_duree_min(verbose: bool = True) -> int:
    """Backfill ``Tache.duree_min`` pour les lignes legacy où il vaut 0.

    Bug historique : `_save_planning_to_db` ne renseignait pas `duree_min`
    à l'insert, du coup toutes les agrégations SQL `sum(duree_min)` côté
    Dashboard renvoyaient 0. Le fix d'insert est en place mais les tâches
    déjà en base avant le fix ont besoin d'être backfillées.

    Calcule `duree_min = (heure_fin - heure_debut)` en minutes pour toutes
    les lignes où `duree_min IS NULL OR duree_min = 0` ET les heures sont
    présentes.

    Idempotent : une 2e exécution ne touche rien (les lignes ont déjà été
    backfillées). Retourne le nombre de lignes affectées.
    """
    with engine.begin() as conn:
        # Vérifier que la table existe (peut ne pas exister sur une DB fraîche)
        inspector = inspect(engine)
        if "taches" not in inspector.get_table_names():
            return 0

        if engine.dialect.name == "sqlite":
            sql = """
                UPDATE taches
                SET duree_min = CAST(
                    (strftime('%s', '2000-01-01 ' || heure_fin)
                     - strftime('%s', '2000-01-01 ' || heure_debut)) / 60
                    AS INTEGER
                )
                WHERE (duree_min IS NULL OR duree_min = 0)
                  AND heure_debut IS NOT NULL
                  AND heure_fin IS NOT NULL
            """
        elif engine.dialect.name == "postgresql":
            sql = """
                UPDATE taches
                SET duree_min = CAST(
                    EXTRACT(EPOCH FROM (heure_fin::time - heure_debut::time)) / 60 AS INTEGER
                )
                WHERE (duree_min IS NULL OR duree_min = 0)
                  AND heure_debut IS NOT NULL
                  AND heure_fin IS NOT NULL
            """
        else:
            return 0
            
        result = conn.execute(text(sql))
        nb = result.rowcount or 0
        if verbose and nb > 0:
            logging.getLogger(__name__).info(
                "[backfill] ✓ Tache.duree_min : %d ligne(s) recalculée(s)", nb,
            )
        return nb


def _backfill_j5_insertion(verbose: bool = True) -> int:
    """Backfill des `niveau_actuel` après l'insertion du J5 dans INTERVALLES_J.

    Avant : index 2 correspondait à J7.  Après : index 2 correspond à J5.
    Pour que les chapitres existants gardent le même intervalle effectif,
    on incrémente `niveau_actuel` de 1 pour tous ceux dont le niveau >= 2.

    Idempotent : vérifie un flag `j5_migrated` dans `systeme_config`.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    if "chapitres" not in tables:
        return 0

    with engine.begin() as conn:
        # Vérifier si la migration a déjà eu lieu
        if "systeme_config" in tables:
            cols = {c["name"] for c in inspector.get_columns("systeme_config")}
            # On utilise une colonne temporaire pour le flag
            if "j5_migrated" not in cols:
                try:
                    conn.execute(text(
                        "ALTER TABLE systeme_config ADD COLUMN j5_migrated BOOLEAN DEFAULT 0"
                    ))
                except Exception:  # noqa: BLE001
                    pass  # Colonne déjà présente (race condition)

            row = conn.execute(text(
                "SELECT j5_migrated FROM systeme_config LIMIT 1"
            )).fetchone()
            if row and row[0]:
                return 0  # Déjà migré

        # Incrémenter niveau_actuel de 1 pour les chapitres >= 2
        result = conn.execute(text(
            "UPDATE chapitres SET niveau_actuel = niveau_actuel + 1 "
            "WHERE niveau_actuel IS NOT NULL AND niveau_actuel >= 2"
        ))
        nb = result.rowcount or 0

        # Marquer la migration comme faite
        if "systeme_config" in tables:
            conn.execute(text(
                "UPDATE systeme_config SET j5_migrated = 1"
            ))

        if verbose:
            if nb > 0:
                logging.getLogger(__name__).info(
                    "[backfill] ✓ J5 insertion : %d chapitre(s) niveau_actuel incrémenté(s)", nb,
                )
            else:
                logging.getLogger(__name__).info(
                    "[backfill] J5 insertion : aucun chapitre à migrer."
                )
        return nb


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