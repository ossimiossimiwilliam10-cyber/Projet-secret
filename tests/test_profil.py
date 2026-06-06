"""Tests unitaires pour l'onglet Profil et ses services.

Couvre :
- Validation biométrique (6 invariants)
- Chiffrement/déchiffrement de la clé API Gemini (Fernet)
- Vélocité historique avec confiance (3 niveaux)
- Helpers de validation des contraintes fixes

Utilise une SQLite en mémoire pour les tests qui touchent la DB.
"""

from __future__ import annotations

import os
import sys
from datetime import date, time, timedelta
from pathlib import Path

# Configure la clé de chiffrement avant tout import qui pourrait l'utiliser
os.environ.setdefault("LLM_VAULT_KEY", "test_vault_key_for_unit_tests_only")

# Permet de lancer les tests depuis le répertoire racine
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


# ---------------------------------------------------------------------------
# Fixture : DB SQLite en mémoire avec tout le schéma
# ---------------------------------------------------------------------------
@pytest.fixture
def in_memory_session():
    """Crée une DB SQLite en mémoire avec toutes les tables, retourne une session."""
    from database.db import Base
    from database import models  # noqa: F401 — charge les modèles

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = Session()
    yield session
    session.close()


# ===========================================================================
# Validation biométrique
# ===========================================================================
class TestValidateBiometrie:
    """Tests des 6 invariants biométriques."""

    def _base_payload(self) -> dict:
        return {
            "heure_lever": time(7, 0),
            "heure_coucher": time(23, 0),
            "heures_sommeil_cible": 8.0,
            "duree_max_session_min": 50,
            "pause_entre_sessions_min": 10,
            "heures_etude_plafond_par_jour": 6.0,
            "heures_etude_cible_par_semaine": 21.0,
            "besoin_sieste": False,
            "duree_sieste_min": 20,
        }

    def test_profil_par_defaut_est_valide(self):
        from services.profil_validator import validate_biometrie
        errors = validate_biometrie(self._base_payload())
        # Pas d'erreur bloquante sur un profil par défaut
        bloquants = [e for e in errors if e.severite == "error"]
        assert bloquants == []

    def test_invariant_1_lever_egal_coucher(self):
        from services.profil_validator import validate_biometrie
        p = self._base_payload()
        p["heure_lever"] = time(7, 0)
        p["heure_coucher"] = time(7, 0)
        errors = validate_biometrie(p)
        assert any(e.champ == "heure_coucher" for e in errors)

    def test_invariant_2_sommeil_incoherent(self):
        """Lever 7h, coucher 23h → 16h éveillé → 8h sommeil OK ; mais 12h cible KO."""
        from services.profil_validator import validate_biometrie
        p = self._base_payload()
        p["heures_sommeil_cible"] = 12.0  # incompatible avec 16h éveillé
        errors = validate_biometrie(p)
        assert any(e.champ == "heures_sommeil_cible" for e in errors)

    def test_invariant_3_pause_plus_longue_que_session(self):
        from services.profil_validator import validate_biometrie
        p = self._base_payload()
        p["duree_max_session_min"] = 30
        p["pause_entre_sessions_min"] = 30
        errors = validate_biometrie(p)
        assert any(e.champ == "pause_entre_sessions_min" for e in errors)

    def test_invariant_4_plafond_irrealiste(self):
        """16h éveillé - 4h vie = 12h budget ; un plafond de 14h doit échouer."""
        from services.profil_validator import validate_biometrie
        p = self._base_payload()
        p["heures_etude_plafond_par_jour"] = 14.0
        # Cohérent avec invariant 5 :
        p["heures_etude_cible_par_semaine"] = 14.0
        errors = validate_biometrie(p)
        assert any(e.champ == "heures_etude_plafond_par_jour" for e in errors)

    def test_invariant_5_cible_hebdo_inatteignable(self):
        from services.profil_validator import validate_biometrie
        p = self._base_payload()
        p["heures_etude_plafond_par_jour"] = 4.0  # plafond × 7 = 28h
        p["heures_etude_cible_par_semaine"] = 50.0  # objectif > 28h → KO
        errors = validate_biometrie(p)
        assert any(e.champ == "heures_etude_cible_par_semaine" for e in errors)

    def test_invariant_6_sieste_hors_range(self):
        from services.profil_validator import validate_biometrie
        p = self._base_payload()
        p["besoin_sieste"] = True
        p["duree_sieste_min"] = 5  # < 10 min → inutile biologiquement
        errors = validate_biometrie(p)
        assert any(e.champ == "duree_sieste_min" for e in errors)

    def test_sieste_hors_range_ignoree_si_pas_besoin(self):
        from services.profil_validator import validate_biometrie
        p = self._base_payload()
        p["besoin_sieste"] = False
        p["duree_sieste_min"] = 200  # absurde, mais sieste désactivée
        errors = validate_biometrie(p)
        assert not any(e.champ == "duree_sieste_min" for e in errors)

    def test_chronotype_nocturne_lever_18h_coucher_2h(self):
        """Étudiant nocturne : lever 18h, coucher 2h du matin → 8h éveillé."""
        from services.profil_validator import validate_biometrie
        p = self._base_payload()
        p["heure_lever"] = time(18, 0)
        p["heure_coucher"] = time(2, 0)
        # 8h éveillé, sommeil implicite = 16h → cible 8h hors tolérance ±2h
        errors = validate_biometrie(p)
        assert any(e.champ == "heures_sommeil_cible" for e in errors)


# ===========================================================================
# Chiffrement clé API Gemini
# ===========================================================================
class TestCrypto:
    """Tests du module services/crypto.py."""

    def test_encrypt_decrypt_roundtrip(self):
        from services.crypto import encrypt_api_key, decrypt_api_key
        original = "AIzaSyDXXXXXXXXXXXXXXXXXXXXXXXXXX12345"
        encrypted = encrypt_api_key(original)
        assert encrypted.startswith("enc:v1:")
        assert decrypt_api_key(encrypted) == original

    def test_encrypt_empty_returns_empty(self):
        from services.crypto import encrypt_api_key, decrypt_api_key
        assert encrypt_api_key("") == ""
        assert encrypt_api_key("   ") == ""
        assert decrypt_api_key("") == ""

    def test_encrypt_is_idempotent(self):
        """Ré-encrypter une valeur déjà chiffrée ne la double-chiffre pas."""
        from services.crypto import encrypt_api_key
        original = "AIzaSyTest"
        once = encrypt_api_key(original)
        twice = encrypt_api_key(once)
        assert once == twice

    def test_decrypt_legacy_plaintext_passthrough(self):
        """Une clé sans préfixe `enc:v1:` est considérée comme legacy en clair."""
        from services.crypto import decrypt_api_key
        assert decrypt_api_key("AIzaPlaintextLegacyKey") == "AIzaPlaintextLegacyKey"

    def test_decrypt_corrupted_returns_empty(self):
        """Token corrompu → chaîne vide (l'utilisateur devra re-saisir)."""
        from services.crypto import decrypt_api_key
        assert decrypt_api_key("enc:v1:corrupted_garbage") == ""

    def test_mask_for_display_short_key(self):
        from services.crypto import mask_for_display
        assert mask_for_display("") == ""
        assert mask_for_display("AB") == "••"
        assert mask_for_display("ABCDEFGHIJKL") == "••••••••IJKL"

    def test_is_encrypted_detection(self):
        from services.crypto import is_encrypted, encrypt_api_key
        assert not is_encrypted("")
        assert not is_encrypted("AIzaPlaintext")
        assert is_encrypted(encrypt_api_key("AIzaTest"))

    def test_different_keys_produce_different_ciphertexts(self):
        """Deux chiffrements de la même clé donnent des tokens différents (nonce)."""
        from services.crypto import encrypt_api_key, decrypt_api_key
        plaintext = "AIzaSyTest"
        e1 = encrypt_api_key(plaintext)
        e2 = encrypt_api_key(plaintext)
        # Fernet inclut un timestamp + IV, donc différents
        assert e1 != e2
        assert decrypt_api_key(e1) == decrypt_api_key(e2) == plaintext


# ===========================================================================
# Vélocité historique avec confiance
# ===========================================================================
class TestVelociteHistorique:
    """Tests de calculer_velocite_historique sur DB en mémoire."""

    def test_velocite_aucune_donnee_sans_semaines(self, in_memory_session):
        """Profil tout neuf, aucune semaine passée → confiance 'aucune'."""
        from services.scheduler_engine import calculer_velocite_historique
        result = calculer_velocite_historique(in_memory_session, utilisateur_id=1)
        assert result.confiance == "aucune"
        assert result.multiplicateur == 1.0
        assert result.echantillon_taches == 0

    def test_velocite_aucune_donnee_sans_taches_etude(self, in_memory_session):
        """Semaine passée existe mais aucune tâche d'étude → confiance 'aucune'."""
        from database.models import Semaine
        from services.scheduler_engine import calculer_velocite_historique

        s = Semaine(
            numero_semaine=20, annee=2026,
            date_debut=date.today() - timedelta(days=14),
            date_fin=date.today() - timedelta(days=8),
        )
        in_memory_session.add(s)
        in_memory_session.commit()

        result = calculer_velocite_historique(in_memory_session, utilisateur_id=1)
        assert result.confiance == "aucune"
        assert result.echantillon_taches == 0

    def test_velocite_confiance_faible_petit_echantillon(self, in_memory_session):
        """3 tâches d'étude < 5 → confiance 'faible'."""
        from database.models import Semaine, Tache
        from services.scheduler_engine import calculer_velocite_historique

        s = Semaine(
            numero_semaine=20, annee=2026,
            date_debut=date.today() - timedelta(days=14),
            date_fin=date.today() - timedelta(days=8),
        )
        in_memory_session.add(s)
        in_memory_session.flush()
        for i in range(3):
            in_memory_session.add(Tache(
                semaine_id=s.id, type="etude", titre=f"T{i}",
                jour="lundi", heure_debut=time(9, 0), heure_fin=time(10, 0),
                duree_min=60, statut="fait" if i < 2 else "a_faire",
                obligatoire=False,
            ))
        in_memory_session.commit()

        result = calculer_velocite_historique(in_memory_session, utilisateur_id=1)
        assert result.confiance == "faible"
        assert result.echantillon_taches == 3
        # 2/3 fait → ratio 0.67, lissé à (0.67+1)/2 = 0.83
        assert 0.8 <= result.multiplicateur <= 0.9

    def test_velocite_confiance_elevee_grand_echantillon(self, in_memory_session):
        """10 tâches dont toutes complétées → confiance 'elevee', multiplicateur 1.0+."""
        from database.models import Semaine, Tache
        from services.scheduler_engine import calculer_velocite_historique

        s = Semaine(
            numero_semaine=20, annee=2026,
            date_debut=date.today() - timedelta(days=14),
            date_fin=date.today() - timedelta(days=8),
        )
        in_memory_session.add(s)
        in_memory_session.flush()
        for i in range(10):
            in_memory_session.add(Tache(
                semaine_id=s.id, type="etude", titre=f"T{i}",
                jour="lundi", heure_debut=time(9, 0), heure_fin=time(10, 0),
                duree_min=60, statut="fait", obligatoire=False,
            ))
        in_memory_session.commit()

        result = calculer_velocite_historique(in_memory_session, utilisateur_id=1)
        assert result.confiance == "elevee"
        assert result.echantillon_taches == 10
        # 100% fait → (1.0+1)/2 = 1.0, plafonné à 1.1
        assert result.multiplicateur == 1.0
        assert result.temps_prevu_min == 600
        assert result.temps_fait_min == 600

    def test_velocite_plancher_a_0_5(self, in_memory_session):
        """Si l'étudiant ne fait rien, le plancher est 0.5 (pas 0)."""
        from database.models import Semaine, Tache
        from services.scheduler_engine import calculer_velocite_historique

        s = Semaine(
            numero_semaine=20, annee=2026,
            date_debut=date.today() - timedelta(days=14),
            date_fin=date.today() - timedelta(days=8),
        )
        in_memory_session.add(s)
        in_memory_session.flush()
        for i in range(10):
            in_memory_session.add(Tache(
                semaine_id=s.id, type="etude", titre=f"T{i}",
                jour="lundi", heure_debut=time(9, 0), heure_fin=time(10, 0),
                duree_min=60, statut="a_faire", obligatoire=False,
            ))
        in_memory_session.commit()

        result = calculer_velocite_historique(in_memory_session, utilisateur_id=1)
        assert result.multiplicateur == 0.5  # plancher
        assert result.temps_fait_min == 0

    def test_velocite_unpacking_retrocompat(self, in_memory_session):
        """L'ancien code `mult, msg = calculer_velocite_historique(...)` doit fonctionner."""
        from services.scheduler_engine import calculer_velocite_historique
        result = calculer_velocite_historique(in_memory_session, utilisateur_id=1)
        mult, msg = result
        assert isinstance(mult, float)
        assert isinstance(msg, str)


# ===========================================================================
# Helpers de validation horaire (profil.py)
# ===========================================================================
class TestHelpersHoraires:
    """Tests des helpers _is_valid_time et _to_minutes."""

    def test_is_valid_time_formats_corrects(self):
        from modules.profil import _is_valid_time
        assert _is_valid_time("00:00")
        assert _is_valid_time("23:59")
        assert _is_valid_time("09:30")

    def test_is_valid_time_formats_invalides(self):
        from modules.profil import _is_valid_time
        assert not _is_valid_time("")
        assert not _is_valid_time("24:00")
        assert not _is_valid_time("12:60")
        assert not _is_valid_time("abc")
        assert not _is_valid_time("12")
        assert not _is_valid_time("12:30:45")

    def test_to_minutes(self):
        from modules.profil import _to_minutes
        assert _to_minutes("00:00") == 0
        assert _to_minutes("01:30") == 90
        assert _to_minutes("23:59") == 23 * 60 + 59
