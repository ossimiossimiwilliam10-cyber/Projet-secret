"""Package database — re-exporte tout ce qui est utilisé par l'app."""

from .db import (
    Base,
    BASE_DIR,
    DATA_DIR,
    PDF_DIR,
    DB_PATH,
    DATABASE_URL,
    engine,
    SessionLocal,
    init_db,
    get_session,
    session_scope,
    reset_db,
    migrate_schema,
)
from .models import (
    Utilisateur,
    Achievement,
    UE,
    Matiere,
    Chapitre,
    Semaine,
    Tache,
    SaisieHebdo,
    Job,
    Objectif,
    CheckInQuotidien,
)

__all__ = [
    # db
    "Base",
    "BASE_DIR",
    "DATA_DIR",
    "PDF_DIR",
    "DB_PATH",
    "DATABASE_URL",
    "engine",
    "SessionLocal",
    "init_db",
    "get_session",
    "session_scope",
    "reset_db",
    "migrate_schema",
    # models
    "Utilisateur",
    "Achievement",
    "UE",
    "Matiere",
    "Chapitre",
    "Semaine",
    "Tache",
    "SaisieHebdo",
    "Job",
    "Objectif",
    "CheckInQuotidien",
]