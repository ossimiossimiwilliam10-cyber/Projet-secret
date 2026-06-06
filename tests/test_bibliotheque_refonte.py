"""Tests pour les chantiers de refonte Bibliothèque (NASA).

Couvre :
  - `services/profil_service` : centralisation de la clé Gemini (chiffrement).
  - `services/pdf_storage`     : validation upload, SHA-256, idempotence.
  - `services/cache_versioning`: invalidation auto du cache fiche IA.
  - `services/optimistic_lock` : verrou optimiste (ETag) sur Chapitre.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import (
    Chapitre,
    Matiere,
    PdfUpload,
    Utilisateur,
)
from services.cache_versioning import (
    FICHE_PROMPT_VERSION,
    fiche_cache_is_valid,
    texte_sha256,
)
from services.crypto import encrypt_api_key
from services.optimistic_lock import ConflictError, update_chapitre_safe
from services.pdf_storage import (
    MAX_PDF_SIZE_BYTES,
    PdfValidationError,
    compute_sha256,
    find_existing_upload,
    record_upload,
    safe_pdf_filename,
    validate_pdf_upload,
)
from services.profil_service import (
    DEFAULT_MODEL,
    get_llm_api_key,
    has_llm_credentials,
)


@pytest.fixture
def session():
    """Session SQLite in-memory, schéma complet."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# profil_service
# ---------------------------------------------------------------------------
def test_get_llm_api_key_aucun_profil(session):
    """Sans profil en base : clé vide + modèle par défaut."""
    api_key, model = get_llm_api_key(session)
    assert api_key == ""
    assert model == DEFAULT_MODEL
    assert has_llm_credentials(session) is False


def test_get_llm_api_key_clef_chiffree_dechiffree(session):
    """La clé chiffrée en base est restituée en clair par le service."""
    from database.models import BiometrieConfig, LogistiqueConfig, SystemeConfig

    u = Utilisateur(nom="Test")
    session.add(u)
    session.flush()
    cipher = encrypt_api_key("ma-vraie-cle-secrete")
    session.add(BiometrieConfig(utilisateur_id=u.id))
    session.add(LogistiqueConfig(utilisateur_id=u.id))
    session.add(SystemeConfig(
        utilisateur_id=u.id,
        llm_api_key=cipher,
        llm_model="gemini-2.5-pro",
    ))
    session.commit()

    api_key, model = get_llm_api_key(session)
    assert api_key == "ma-vraie-cle-secrete"
    assert model == "gemini-2.5-pro"
    assert has_llm_credentials(session) is True


# ---------------------------------------------------------------------------
# pdf_storage — validation
# ---------------------------------------------------------------------------
def test_validate_pdf_vide_rejette():
    with pytest.raises(PdfValidationError, match="vide"):
        validate_pdf_upload(b"", "test.pdf")


def test_validate_pdf_sans_signature_magique_rejette():
    with pytest.raises(PdfValidationError, match="signature"):
        validate_pdf_upload(b"<html>not a pdf</html>", "fake.pdf")


def test_validate_pdf_trop_gros_rejette():
    fake = b"%PDF-" + b"\x00" * (MAX_PDF_SIZE_BYTES + 1)
    with pytest.raises(PdfValidationError, match="trop gros"):
        validate_pdf_upload(fake, "lourd.pdf")


def test_validate_pdf_path_traversal_rejette():
    pdf = b"%PDF-1.4\n..."
    for bad_name in ["../etc/passwd.pdf", "a/b.pdf", "x\\y.pdf"]:
        with pytest.raises(PdfValidationError, match="suspect"):
            validate_pdf_upload(pdf, bad_name)


def test_validate_pdf_valide_passe():
    pdf = b"%PDF-1.4\n%%EOF"
    validate_pdf_upload(pdf, "cours.pdf")  # ne doit pas lever


# ---------------------------------------------------------------------------
# pdf_storage — SHA & filename
# ---------------------------------------------------------------------------
def test_compute_sha256_deterministe():
    assert compute_sha256(b"hello") == compute_sha256(b"hello")
    assert compute_sha256(b"hello") != compute_sha256(b"world")
    assert len(compute_sha256(b"x")) == 64


def test_safe_pdf_filename_format():
    assert safe_pdf_filename(7, 1700000000, 3) == "m7-1700000000-3.pdf"


def test_safe_pdf_filename_refuse_negatif():
    with pytest.raises(ValueError):
        safe_pdf_filename(-1, 1, 1)


# ---------------------------------------------------------------------------
# pdf_storage — idempotence via PdfUpload
# ---------------------------------------------------------------------------
def test_find_existing_upload_retourne_none_si_inconnu(session):
    m = Matiere(nom="Algèbre")
    session.add(m)
    session.commit()
    assert find_existing_upload(session, "a" * 64, m.id) is None


def test_record_et_retrouve_upload(session):
    m = Matiere(nom="Algèbre")
    session.add(m)
    session.commit()
    sha = "b" * 64
    record_upload(
        session,
        sha=sha,
        matiere_id=m.id,
        filename_original="cours.pdf",
        filename_stored="m1-1-1.pdf",
        label="Cours",
        nb_chapitres=5,
    )
    session.commit()

    found = find_existing_upload(session, sha, m.id)
    assert found is not None
    assert found.nb_chapitres_crees == 5
    assert found.label == "Cours"


def test_idempotence_meme_sha_meme_matiere_unique(session):
    """Le UniqueConstraint (sha, matiere_id) empêche la duplication."""
    from sqlalchemy.exc import IntegrityError

    m = Matiere(nom="X")
    session.add(m)
    session.commit()
    sha = "c" * 64
    session.add(PdfUpload(sha256=sha, matiere_id=m.id))
    session.commit()
    session.add(PdfUpload(sha256=sha, matiere_id=m.id))
    with pytest.raises(IntegrityError):
        session.commit()


# ---------------------------------------------------------------------------
# cache_versioning
# ---------------------------------------------------------------------------
def test_texte_sha256_deterministe():
    assert texte_sha256("abc") == texte_sha256("abc")
    assert texte_sha256("abc") != texte_sha256("abd")


def test_fiche_cache_invalide_si_jamais_genere():
    assert (
        fiche_cache_is_valid(
            cached_model=None,
            cached_prompt_version=None,
            cached_texte_sha=None,
            current_model="gemini-2.5-flash",
            current_texte_sha="x",
        )
        is False
    )


def test_fiche_cache_invalide_si_modele_change():
    assert (
        fiche_cache_is_valid(
            cached_model="gemini-1.5-flash",
            cached_prompt_version=FICHE_PROMPT_VERSION,
            cached_texte_sha="x",
            current_model="gemini-2.5-flash",
            current_texte_sha="x",
        )
        is False
    )


def test_fiche_cache_invalide_si_contenu_change():
    assert (
        fiche_cache_is_valid(
            cached_model="gemini-2.5-flash",
            cached_prompt_version=FICHE_PROMPT_VERSION,
            cached_texte_sha="old",
            current_model="gemini-2.5-flash",
            current_texte_sha="new",
        )
        is False
    )


def test_fiche_cache_valide_si_tout_match():
    assert (
        fiche_cache_is_valid(
            cached_model="gemini-2.5-flash",
            cached_prompt_version=FICHE_PROMPT_VERSION,
            cached_texte_sha="abc",
            current_model="gemini-2.5-flash",
            current_texte_sha="abc",
        )
        is True
    )


# ---------------------------------------------------------------------------
# optimistic_lock
# ---------------------------------------------------------------------------
def _chap(session):
    m = Matiere(nom="X")
    session.add(m)
    session.commit()
    c = Chapitre(matiere_id=m.id, titre="Ch1", numero=1)
    session.add(c)
    session.commit()
    return c


def test_update_chapitre_safe_succes(session):
    c = _chap(session)
    assert c.version == 1
    new_version = update_chapitre_safe(
        session,
        c.id,
        expected_version=1,
        mutate=lambda ch: setattr(ch, "notes", "ma note"),
    )
    session.commit()
    assert new_version == 2
    rechargee = session.get(Chapitre, c.id)
    assert rechargee.notes == "ma note"
    assert rechargee.version == 2


def test_update_chapitre_safe_conflit_leve_erreur(session):
    c = _chap(session)
    # Premier onglet écrit avec succès
    update_chapitre_safe(
        session, c.id, expected_version=1,
        mutate=lambda ch: setattr(ch, "notes", "A"),
    )
    session.commit()

    # Second onglet a une version stale (=1)
    with pytest.raises(ConflictError) as excinfo:
        update_chapitre_safe(
            session, c.id, expected_version=1,
            mutate=lambda ch: setattr(ch, "notes", "B"),
        )
    assert excinfo.value.expected_version == 1
    assert excinfo.value.actual_version == 2


def test_update_chapitre_inexistant(session):
    with pytest.raises(ValueError, match="introuvable"):
        update_chapitre_safe(session, 9999, 1, lambda c: None)
