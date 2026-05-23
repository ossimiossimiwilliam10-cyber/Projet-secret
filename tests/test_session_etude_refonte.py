"""Tests pour les chantiers Session d'étude (A + B + C).

Couvre :
  - `services/gemini_utils`     : retry exponentiel, classification erreurs
  - `services/qcm_validator`    : validation stricte QCM + quiz ouverts
  - `services/cache_versioning` : `cache_is_valid` générique (QCM/quiz)
"""

from __future__ import annotations

import pytest

from services.cache_versioning import (
    QCM_PROMPT_VERSION,
    QUIZ_PROMPT_VERSION,
    cache_is_valid,
)
from services.gemini_utils import (
    GEMINI_MAX_RETRIES,
    gemini_call_with_retry,
    is_transient_gemini_error,
)
from services.qcm_validator import (
    VALID_LETTERS,
    validate_qcm_questions,
    validate_quiz_questions,
)


# ===========================================================================
# gemini_utils — classification d'erreurs
# ===========================================================================
class _FakeGeminiError(Exception):
    """Simule une erreur Gemini avec message contrôlé."""


def test_is_transient_503():
    assert is_transient_gemini_error(_FakeGeminiError("503 Service Unavailable")) is True


def test_is_transient_429_rate_limit():
    assert is_transient_gemini_error(_FakeGeminiError("429 Too Many Requests")) is True


def test_is_transient_timeout():
    assert is_transient_gemini_error(_FakeGeminiError("Deadline exceeded")) is True


def test_is_transient_connection_error():
    assert is_transient_gemini_error(ConnectionError("network down")) is True
    assert is_transient_gemini_error(TimeoutError("timed out")) is True


def test_is_transient_401_permanent():
    assert is_transient_gemini_error(_FakeGeminiError("401 Unauthorized")) is False


def test_is_transient_403_permanent():
    assert is_transient_gemini_error(_FakeGeminiError("403 Forbidden")) is False


def test_is_transient_api_key_invalid_permanent():
    assert is_transient_gemini_error(_FakeGeminiError("API key not valid")) is False


def test_is_transient_inconnue_rejette():
    # Ni transient ni permanent connu → considéré non-transient (safe default).
    assert is_transient_gemini_error(_FakeGeminiError("weird error")) is False


# ===========================================================================
# gemini_utils — retry logic
# ===========================================================================
def test_retry_succes_au_premier_essai():
    calls = {"n": 0}

    def ok():
        calls["n"] += 1
        return "OK"

    assert gemini_call_with_retry(ok) == "OK"
    assert calls["n"] == 1


def test_retry_permanente_ne_retry_pas():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise _FakeGeminiError("401 Unauthorized")

    with pytest.raises(_FakeGeminiError):
        gemini_call_with_retry(boom)
    assert calls["n"] == 1  # un seul essai


def test_retry_transitoire_puis_succes(monkeypatch):
    # Évite d'attendre 2s + 4s pendant les tests
    monkeypatch.setattr("services.gemini_utils.time.sleep", lambda _: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _FakeGeminiError("503 Overloaded")
        return "OK"

    assert gemini_call_with_retry(flaky) == "OK"
    assert calls["n"] == 3


def test_retry_epuisement_des_essais(monkeypatch):
    monkeypatch.setattr("services.gemini_utils.time.sleep", lambda _: None)
    calls = {"n": 0}

    def always_503():
        calls["n"] += 1
        raise _FakeGeminiError("503")

    with pytest.raises(_FakeGeminiError):
        gemini_call_with_retry(always_503)
    assert calls["n"] == GEMINI_MAX_RETRIES


# ===========================================================================
# qcm_validator — QCM strict
# ===========================================================================
def _valid_qcm() -> dict:
    return {
        "question": "Quelle est la capitale de la France ?",
        "options": ["Paris", "Londres", "Berlin", "Rome"],
        "correct": "A",
        "explication": "Paris est la capitale historique.",
    }


def test_qcm_valide_passe():
    out = validate_qcm_questions([_valid_qcm()])
    assert len(out) == 1
    assert out[0]["correct"] == "A"


def test_qcm_strip_prefixe_options():
    q = _valid_qcm()
    q["options"] = ["A) Paris", "B) Londres", "C) Berlin", "D) Rome"]
    out = validate_qcm_questions([q])
    assert out[0]["options"] == ["Paris", "Londres", "Berlin", "Rome"]


def test_qcm_rejette_question_trop_courte():
    q = _valid_qcm()
    q["question"] = "OK"
    with pytest.raises(ValueError):
        validate_qcm_questions([q])


def test_qcm_rejette_3_options():
    q = _valid_qcm()
    q["options"] = ["A", "B", "C"]
    with pytest.raises(ValueError):
        validate_qcm_questions([q])


def test_qcm_rejette_5_options():
    q = _valid_qcm()
    q["options"] = ["A", "B", "C", "D", "E"]
    with pytest.raises(ValueError):
        validate_qcm_questions([q])


def test_qcm_rejette_options_dupliquees():
    q = _valid_qcm()
    q["options"] = ["Paris", "Paris", "Berlin", "Rome"]
    with pytest.raises(ValueError):
        validate_qcm_questions([q])


def test_qcm_rejette_lettre_invalide():
    q = _valid_qcm()
    q["correct"] = "E"
    with pytest.raises(ValueError):
        validate_qcm_questions([q])


def test_qcm_dedoublonne_questions_identiques():
    q1 = _valid_qcm()
    q2 = _valid_qcm()  # même question, doublon
    q3 = _valid_qcm()
    q3["question"] = "Quelle est la capitale du Royaume-Uni ?"
    q3["correct"] = "B"
    out = validate_qcm_questions([q1, q2, q3])
    assert len(out) == 2  # le doublon est éliminé


def test_qcm_rejette_liste_vide():
    with pytest.raises(ValueError):
        validate_qcm_questions([])


def test_qcm_rejette_si_zero_valide_apres_filtre():
    # Toutes invalides → ValueError final
    with pytest.raises(ValueError):
        validate_qcm_questions([
            {"question": "x", "options": ["a"], "correct": "A"},
        ])


def test_qcm_lettres_valides():
    assert VALID_LETTERS == {"A", "B", "C", "D"}


# ===========================================================================
# qcm_validator — quiz ouvert
# ===========================================================================
def test_quiz_extraction_format_numerote():
    text = "1. Quelle est la définition de l'entropie ?\n2. Décris le second principe."
    out = validate_quiz_questions(text)
    assert len(out) == 2
    assert out[0].startswith("Quelle est")
    assert out[1].startswith("Décris")


def test_quiz_strip_prefixe_Q():
    text = "Q1. Première question importante ici.\nQ2) Deuxième question.\n"
    out = validate_quiz_questions(text)
    assert out[0] == "Première question importante ici."
    assert out[1] == "Deuxième question."


def test_quiz_dedoublonne():
    text = (
        "1. Définis l'entropie thermodynamique.\n"
        "2. Définis l'entropie thermodynamique.\n"
        "3. Énonce le second principe de Carnot.\n"
    )
    out = validate_quiz_questions(text)
    assert len(out) == 2


def test_quiz_respecte_max_questions():
    lines = "\n".join(f"{i}. Question numero {i} suffisamment longue." for i in range(1, 11))
    out = validate_quiz_questions(lines, max_questions=3)
    assert len(out) == 3


def test_quiz_rejette_si_vide():
    with pytest.raises(ValueError):
        validate_quiz_questions("")


def test_quiz_ignore_lignes_trop_courtes():
    text = "1. OK\n2. Question valide suffisamment longue."
    out = validate_quiz_questions(text)
    assert len(out) == 1


# ===========================================================================
# cache_versioning — helper générique
# ===========================================================================
def test_cache_is_valid_qcm_tout_match():
    assert cache_is_valid(
        cached_model="gemini-2.5-flash",
        cached_prompt_version=QCM_PROMPT_VERSION,
        cached_texte_sha="abc",
        current_model="gemini-2.5-flash",
        current_prompt_version=QCM_PROMPT_VERSION,
        current_texte_sha="abc",
    ) is True


def test_cache_is_valid_quiz_prompt_version_change():
    assert cache_is_valid(
        cached_model="gemini-2.5-flash",
        cached_prompt_version=QUIZ_PROMPT_VERSION - 1,
        cached_texte_sha="abc",
        current_model="gemini-2.5-flash",
        current_prompt_version=QUIZ_PROMPT_VERSION,
        current_texte_sha="abc",
    ) is False


def test_cache_is_valid_aucune_metadonnee():
    assert cache_is_valid(
        cached_model=None,
        cached_prompt_version=None,
        cached_texte_sha=None,
        current_model="gemini-2.5-flash",
        current_prompt_version=1,
        current_texte_sha="x",
    ) is False
