"""Tests de la couche de décision déterministe ``services.scheduler_engine``.

Sécurise les trois invariants critiques de la refonte :

1. **Max 1 nouveau chapitre par matière par jour** (problème de l'indigestion).
2. **Flexibilité ±1 jour pour les révisions Leitner** quand le jour-cible
   est saturé.
3. **Quota d'étude modulé par le check-in biomécanique** (-30 % si fatigue
   ou charge mentale > 7/10).
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import Chapitre, Matiere, Semaine
from services.scheduler_engine import (
    DUREE_REVISION_MIN,
    JOURS,
    SESSIONS_PAR_JOUR,
    calculer_quota_etude_minutes,
    lisser_revisions_leitner,
    repartir_nouveaux_chapitres,
)


# ---------------------------------------------------------------------------
# Fixtures communes
# ---------------------------------------------------------------------------
@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


@pytest.fixture
def semaine_courante(session):
    today = date.today()
    s = Semaine(
        annee=today.year,
        numero_semaine=today.isocalendar().week,
        date_debut=today,
        date_fin=today + timedelta(days=6),
    )
    session.add(s)
    session.commit()
    return s


def _make_matiere(session, nom: str) -> Matiere:
    m = Matiere(nom=nom)
    session.add(m)
    session.commit()
    return m


def _make_chap(session, matiere: Matiere, numero: int, niveau: int = 0) -> Chapitre:
    ch = Chapitre(
        matiere_id=matiere.id,
        numero=numero,
        titre=f"{matiere.nom} - Chap {numero}",
        niveau_actuel=niveau,
        temps_estime_h=1.0,
    )
    session.add(ch)
    session.commit()
    return ch


# ---------------------------------------------------------------------------
# 1. Quota d'étude
# ---------------------------------------------------------------------------
def test_quota_etude_base_sans_checkin():
    """Sans check-in, le quota = duree_max_session_min × 3."""
    profil = SimpleNamespace(duree_max_session_min=50)
    assert calculer_quota_etude_minutes(profil, None) == 50 * SESSIONS_PAR_JOUR


def test_quota_etude_reduit_si_fatigue_haute():
    """Fatigue physique > 7/10 → quota réduit de 30 %."""
    profil = SimpleNamespace(duree_max_session_min=60)
    base = 60 * SESSIONS_PAR_JOUR
    q_fatigue = calculer_quota_etude_minutes(
        profil, {"fatigue_physique": 9, "charge_mentale": 4}
    )
    assert q_fatigue == int(base * 0.70)
    assert q_fatigue < base


def test_quota_etude_reduit_si_mental_haute():
    """Charge mentale > 7/10 → même règle de réduction."""
    profil = SimpleNamespace(duree_max_session_min=50)
    base = 50 * SESSIONS_PAR_JOUR
    q_mental = calculer_quota_etude_minutes(
        profil, {"fatigue_physique": 3, "charge_mentale": 9}
    )
    assert q_mental == int(base * 0.70)


def test_quota_etude_inchange_si_checkin_normal():
    """Check-in dans la zone verte (≤7) → pas de réduction."""
    profil = SimpleNamespace(duree_max_session_min=50)
    base = 50 * SESSIONS_PAR_JOUR
    q = calculer_quota_etude_minutes(
        profil, {"fatigue_physique": 6, "charge_mentale": 6}
    )
    assert q == base


def test_quota_etude_utilise_heures_cible_si_renseigne():
    """Si l'étudiant a réglé son quota dans le profil (heures_etude_cible_par_jour),
    cette valeur prime sur le calcul historique duree_max_session × N."""
    profil = SimpleNamespace(
        duree_max_session_min=50,
        heures_etude_cible_par_jour=4.0,
    )
    q = calculer_quota_etude_minutes(profil, None)
    # 4 h = 240 min — pas le fallback 50 × 3 = 150 min.
    assert q == 240


def test_quota_etude_heures_cible_reste_module_par_le_checkin():
    """Le réglage explicite est toujours sujet à la modulation -30 %
    quand la fatigue ou la charge mentale dépasse 7/10."""
    profil = SimpleNamespace(
        duree_max_session_min=50,
        heures_etude_cible_par_jour=4.0,
    )
    q = calculer_quota_etude_minutes(
        profil, {"fatigue_physique": 9, "charge_mentale": 4}
    )
    # 240 × 0.70 = 168
    assert q == 168


def test_quota_etude_fallback_si_heures_cible_zero():
    """Si heures_etude_cible_par_jour vaut 0 (non renseigné), on retombe sur
    l'ancien calcul duree_max_session × SESSIONS_PAR_JOUR."""
    profil = SimpleNamespace(
        duree_max_session_min=60,
        heures_etude_cible_par_jour=0.0,
    )
    q = calculer_quota_etude_minutes(profil, None)
    assert q == 60 * SESSIONS_PAR_JOUR


# ---------------------------------------------------------------------------
# 2. Répartition des nouveaux chapitres
# ---------------------------------------------------------------------------
def test_max_un_nouveau_chapitre_par_matiere_par_jour(session, semaine_courante):
    """L'invariant central de la refonte : jamais deux nouveaux chapitres
    de la même matière le même jour."""
    matiere = _make_matiere(session, "Algèbre L1")
    chaps = [_make_chap(session, matiere, i + 1) for i in range(7)]
    sel = [{"matiere_id": matiere.id, "chapitre_ids": [c.id for c in chaps]}]

    repartition = repartir_nouveaux_chapitres(session, sel, semaine_courante)

    for jour in JOURS:
        matieres_du_jour = [c["matiere"] for c in repartition[jour]]
        assert len(matieres_du_jour) == len(set(matieres_du_jour)), (
            f"Doublon de matière sur {jour} : {matieres_du_jour}"
        )
    # Tous les chapitres ont été placés.
    total = sum(len(v) for v in repartition.values())
    assert total == 7


def test_chapitres_deja_vus_sont_ignores(session, semaine_courante):
    """Un chapitre avec niveau_actuel > 0 n'est PAS un nouveau chapitre :
    il ne doit pas être inclus dans la répartition des nouveaux."""
    matiere = _make_matiere(session, "Physique L1")
    ch_nouveau = _make_chap(session, matiere, 1, niveau=0)
    ch_revu = _make_chap(session, matiere, 2, niveau=3)
    sel = [{"matiere_id": matiere.id, "chapitre_ids": [ch_nouveau.id, ch_revu.id]}]

    repartition = repartir_nouveaux_chapitres(session, sel, semaine_courante)

    tous_les_ids = [c["chapitre_id"] for v in repartition.values() for c in v]
    assert ch_nouveau.id in tous_les_ids
    assert ch_revu.id not in tous_les_ids


def test_matieres_differentes_peuvent_partager_un_jour(session, semaine_courante):
    """Deux nouveaux chapitres de MATIÈRES DIFFÉRENTES peuvent tomber le
    même jour — la règle limite seulement les doublons par matière."""
    m_a = _make_matiere(session, "Algèbre")
    m_b = _make_matiere(session, "Mécanique")
    ch_a = _make_chap(session, m_a, 1)
    ch_b = _make_chap(session, m_b, 1)
    sel = [
        {"matiere_id": m_a.id, "chapitre_ids": [ch_a.id]},
        {"matiere_id": m_b.id, "chapitre_ids": [ch_b.id]},
    ]

    repartition = repartir_nouveaux_chapitres(session, sel, semaine_courante)

    # On vérifie juste qu'aucun jour ne contient deux fois "Algèbre" ou "Mécanique".
    for jour in JOURS:
        matieres = [c["matiere"] for c in repartition[jour]]
        assert len(matieres) == len(set(matieres))


# ---------------------------------------------------------------------------
# 3. Lissage des révisions Leitner
# ---------------------------------------------------------------------------
def test_revision_placee_au_jour_cible_si_quota_dispo(semaine_courante):
    """Quand le jour-cible a de la place, la révision y reste sans décalage."""
    # date_due = jeudi (J+3 par rapport au lundi de la semaine)
    cible = semaine_courante.date_debut + timedelta(days=3)
    chapitres_dus = [{
        "chapitre_id": 42,
        "cours": "Maths",
        "titre": "Limites",
        "date_due": cible.isoformat(),
    }]

    repartition, charges = lisser_revisions_leitner(
        chapitres_dus,
        semaine_courante,
        quota_par_jour_min=300,
        charges_etude_existantes={j: 0 for j in JOURS},
    )

    assert len(repartition["jeudi"]) == 1
    rev = repartition["jeudi"][0]
    assert rev["chapitre_id"] == 42
    assert rev["decale"] is False
    assert rev["surcharge"] is False
    assert charges["jeudi"] == DUREE_REVISION_MIN


def test_revision_decalee_d_un_jour_si_jour_cible_sature(semaine_courante):
    """Si le jour-cible est plein, la révision glisse à J+1 (puis J-1)."""
    cible = semaine_courante.date_debut + timedelta(days=2)  # mercredi

    chapitres_dus = [{
        "chapitre_id": 7,
        "cours": "Physique",
        "titre": "Cinématique",
        "date_due": cible.isoformat(),
    }]
    charges_init = {j: 0 for j in JOURS}
    quota = 60
    # On sature mercredi : 60 - 30 = 30 → 30 + 30 dépasse le quota.
    charges_init["mercredi"] = 40

    repartition, _ = lisser_revisions_leitner(
        chapitres_dus, semaine_courante,
        quota_par_jour_min=quota,
        charges_etude_existantes=charges_init,
    )

    # La révision n'est PAS sur mercredi, elle a glissé.
    assert len(repartition["mercredi"]) == 0
    place_ailleurs = [j for j in JOURS if repartition[j]]
    assert len(place_ailleurs) == 1
    jour_final = place_ailleurs[0]
    assert jour_final in ("jeudi", "mardi")  # ±1 jour autour de mercredi
    assert repartition[jour_final][0]["decale"] is True
    assert repartition[jour_final][0]["jour_initial_cible"] == "mercredi"


def test_revision_marquee_surcharge_si_aucun_jour_dispo(semaine_courante):
    """Si J, J-1 et J+1 sont tous saturés, on tombe au jour-cible avec
    le drapeau ``surcharge=True`` (jamais d'oubli silencieux)."""
    cible = semaine_courante.date_debut + timedelta(days=2)  # mercredi
    chapitres_dus = [{
        "chapitre_id": 99,
        "cours": "Info",
        "titre": "Algos",
        "date_due": cible.isoformat(),
    }]
    # Tous les jours sont saturés (charge déjà > quota).
    charges_init = {j: 999 for j in JOURS}

    repartition, _ = lisser_revisions_leitner(
        chapitres_dus, semaine_courante,
        quota_par_jour_min=100,
        charges_etude_existantes=charges_init,
    )

    assert len(repartition["mercredi"]) == 1
    rev = repartition["mercredi"][0]
    assert rev["surcharge"] is True
    assert rev["decale"] is False
