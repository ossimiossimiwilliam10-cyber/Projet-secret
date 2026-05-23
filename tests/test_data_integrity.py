"""Tests pour `services/data_integrity` — audit + réparation des invariants.

Construit des DB délibérément cassées et vérifie que :
  1. `audit_all` détecte les incohérences avec la bonne sévérité.
  2. `repair_all(dry_run=False)` corrige ce qui peut l'être.
  3. Les checks individuels sont isolables.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import (
    Chapitre,
    GamificationState,
    Matiere,
    Objectif,
    Semaine,
    Tache,
    Utilisateur,
)
from services.data_integrity import (
    AuditReport,
    audit_all,
    check_chapitre_invariants,
    check_gamification_invariants,
    check_tache_invariants,
    repair_all,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    yield s
    s.close()
    engine.dispose()


# ===========================================================================
# Chapitre — bornes maitrise_pct et niveau_actuel
# ===========================================================================
def test_audit_db_propre_retourne_clean(session):
    """Une DB vide / saine ne doit produire aucune issue."""
    report = audit_all(session)
    assert report.is_clean
    assert report.summary()["total"] == 0


def test_audit_detecte_maitrise_pct_hors_bornes(session):
    m = Matiere(nom="Algèbre")
    session.add(m)
    session.commit()
    session.add(Chapitre(matiere_id=m.id, numero=1, titre="X", maitrise_pct=150.0))
    session.add(Chapitre(matiere_id=m.id, numero=1, titre="Y", maitrise_pct=-5.0))
    session.commit()

    issues = list(check_chapitre_invariants(session))
    assert len(issues) == 2
    assert all(i.severity == "critical" for i in issues)
    assert all(i.category == "chapitre.maitrise_pct" for i in issues)
    assert all(i.repairable for i in issues)


def test_audit_detecte_niveau_hors_bornes(session):
    from services.revision_service import MAX_NIVEAU

    m = Matiere(nom="X")
    session.add(m)
    session.commit()
    session.add(Chapitre(matiere_id=m.id, numero=1, titre="Z", niveau_actuel=MAX_NIVEAU + 5))
    session.add(Chapitre(matiere_id=m.id, numero=1, titre="W", niveau_actuel=-2))
    session.commit()

    issues = list(check_chapitre_invariants(session))
    cats = [i.category for i in issues]
    assert cats.count("chapitre.niveau_actuel") == 2


def test_repair_corrige_maitrise_pct_hors_bornes(session):
    m = Matiere(nom="X")
    session.add(m)
    session.commit()
    c = Chapitre(matiere_id=m.id, numero=1, titre="Y", maitrise_pct=200.0)
    session.add(c)
    session.commit()

    fixes = repair_all(session, dry_run=False)
    session.commit()
    assert fixes.get("chapitre.maitrise_pct") == 1
    session.refresh(c)
    assert c.maitrise_pct == 100.0


# ===========================================================================
# Gamification — niveau drift, streak stale, record incohérent
# ===========================================================================
def _setup_user_gamif(session) -> GamificationState:
    """Crée un Utilisateur avec gamification + sub-configs minimales."""
    from database.models import BiometrieConfig, LogistiqueConfig, SystemeConfig

    u = Utilisateur(nom="Test")
    session.add(u)
    session.flush()
    g = GamificationState(utilisateur_id=u.id)
    session.add_all([
        g,
        BiometrieConfig(utilisateur_id=u.id),
        LogistiqueConfig(utilisateur_id=u.id),
        SystemeConfig(utilisateur_id=u.id),
    ])
    session.commit()
    return g


def test_audit_detecte_niveau_drift(session):
    """Si xp=1000 mais niveau stocké = 1, alerte."""
    g = _setup_user_gamif(session)
    g.xp = 1000  # devrait correspondre au niveau 5
    g.niveau = 1
    session.commit()

    issues = list(check_gamification_invariants(session))
    cats = {i.category for i in issues}
    assert "gamification.niveau" in cats


def test_repair_corrige_niveau_drift(session):
    g = _setup_user_gamif(session)
    g.xp = 1000
    g.niveau = 1
    session.commit()

    fixes = repair_all(session, dry_run=False)
    session.commit()
    assert fixes.get("gamification.niveau") == 1
    session.refresh(g)
    assert g.niveau == 5


def test_audit_detecte_streak_record_inferieur(session):
    g = _setup_user_gamif(session)
    g.streak_jours = 10
    g.streak_record = 3  # incohérent : record < courant
    session.commit()

    issues = list(check_gamification_invariants(session))
    cats = [i.category for i in issues]
    assert "gamification.streak_record" in cats


def test_repair_bump_streak_record(session):
    g = _setup_user_gamif(session)
    g.streak_jours = 10
    g.streak_record = 3
    # On pose derniere_activite_xp à aujourd'hui pour ne pas déclencher
    # le reset auto "streak stale" qui interfèrerait avec ce test.
    g.derniere_activite_xp = dt.date.today()
    session.commit()

    repair_all(session, dry_run=False)
    session.commit()
    session.refresh(g)
    assert g.streak_record == 10  # bumpé au niveau du courant


def test_audit_detecte_streak_stale(session):
    """streak > 0 mais dernière activité il y a 5 jours → reset auto attendu."""
    g = _setup_user_gamif(session)
    g.streak_jours = 7
    g.derniere_activite_xp = dt.date.today() - dt.timedelta(days=5)
    session.commit()

    issues = list(check_gamification_invariants(session))
    cats = [i.category for i in issues]
    assert "gamification.streak_stale" in cats


def test_repair_reset_streak_stale(session):
    g = _setup_user_gamif(session)
    g.streak_jours = 7
    g.derniere_activite_xp = dt.date.today() - dt.timedelta(days=10)
    session.commit()

    repair_all(session, dry_run=False)
    session.commit()
    session.refresh(g)
    assert g.streak_jours == 0


# ===========================================================================
# Tache — heures, statut, drift duree_min
# ===========================================================================
def _make_semaine(session) -> int:
    sem = Semaine(
        numero_semaine=1, annee=2026,
        date_debut=dt.date(2026, 1, 5),
        date_fin=dt.date(2026, 1, 11),
    )
    session.add(sem)
    session.commit()
    return sem.id


def test_audit_detecte_heures_inversees(session):
    sid = _make_semaine(session)
    t = Tache(
        semaine_id=sid, type="etude", titre="X", jour="lundi",
        heure_debut=dt.time(10, 0), heure_fin=dt.time(9, 0),
        statut="a_faire",
    )
    session.add(t)
    session.commit()

    issues = list(check_tache_invariants(session))
    cats = [i.category for i in issues]
    assert "tache.heures_inversees" in cats
    # Non réparable (besoin d'intervention manuelle)
    inv = next(i for i in issues if i.category == "tache.heures_inversees")
    assert inv.repairable is False


def test_audit_detecte_drift_duree_min(session):
    sid = _make_semaine(session)
    t = Tache(
        semaine_id=sid, type="etude", titre="X", jour="lundi",
        heure_debut=dt.time(8, 0), heure_fin=dt.time(10, 0),
        duree_min=999,  # incohérent : devrait être 120
        statut="a_faire",
    )
    session.add(t)
    session.commit()

    issues = list(check_tache_invariants(session))
    drift = [i for i in issues if i.category == "tache.duree_drift"]
    assert len(drift) == 1
    assert drift[0].severity == "info"
    assert drift[0].repairable


def test_repair_corrige_drift_duree_min(session):
    sid = _make_semaine(session)
    t = Tache(
        semaine_id=sid, type="etude", titre="X", jour="lundi",
        heure_debut=dt.time(8, 0), heure_fin=dt.time(10, 0),
        duree_min=999,
        statut="a_faire",
    )
    session.add(t)
    session.commit()

    repair_all(session, dry_run=False)
    session.commit()
    session.refresh(t)
    assert t.duree_min == 120


def test_audit_detecte_statut_inconnu(session):
    sid = _make_semaine(session)
    t = Tache(
        semaine_id=sid, type="etude", titre="X", jour="lundi",
        heure_debut=dt.time(8, 0), heure_fin=dt.time(10, 0),
        statut="vacances_au_soleil",  # invalide
    )
    session.add(t)
    session.commit()

    issues = list(check_tache_invariants(session))
    cats = [i.category for i in issues]
    assert "tache.statut_inconnu" in cats


def test_repair_reset_statut_inconnu_a_a_faire(session):
    sid = _make_semaine(session)
    t = Tache(
        semaine_id=sid, type="etude", titre="X", jour="lundi",
        heure_debut=dt.time(8, 0), heure_fin=dt.time(10, 0),
        statut="bizarre",
    )
    session.add(t)
    session.commit()

    repair_all(session, dry_run=False)
    session.commit()
    session.refresh(t)
    assert t.statut == "a_faire"


# ===========================================================================
# Orphans — chapitre_ids référençant des chapitres supprimés
# ===========================================================================
def test_audit_detecte_tache_chapitre_orphelin(session):
    m = Matiere(nom="X")
    session.add(m)
    session.commit()
    c = Chapitre(matiere_id=m.id, numero=1, titre="Vivant")
    session.add(c)
    session.commit()

    sid = _make_semaine(session)
    t = Tache(
        semaine_id=sid, type="etude", titre="T", jour="lundi",
        heure_debut=dt.time(8, 0), heure_fin=dt.time(9, 0),
        chapitre_ids=[c.id, 99999],  # 99999 n'existe pas
        statut="a_faire",
    )
    session.add(t)
    session.commit()

    report = audit_all(session)
    orphs = [i for i in report.issues if i.category == "tache.chapitre_orphelin"]
    assert len(orphs) == 1
    assert orphs[0].context["orphan_chapitre_id"] == 99999


def test_repair_supprime_chapitre_orphelin_de_la_tache(session):
    m = Matiere(nom="X")
    session.add(m)
    session.commit()
    c = Chapitre(matiere_id=m.id, numero=1, titre="Vivant")
    session.add(c)
    session.commit()

    sid = _make_semaine(session)
    t = Tache(
        semaine_id=sid, type="etude", titre="T", jour="lundi",
        heure_debut=dt.time(8, 0), heure_fin=dt.time(9, 0),
        chapitre_ids=[c.id, 99999],
        statut="a_faire",
    )
    session.add(t)
    session.commit()

    repair_all(session, dry_run=False)
    session.commit()
    session.refresh(t)
    assert t.chapitre_ids == [c.id]


# ===========================================================================
# AuditReport API
# ===========================================================================
def test_audit_report_summary():
    report = AuditReport()
    from services.data_integrity import Issue
    report.add(Issue(severity="critical", category="x", entity="X#1", message=""))
    report.add(Issue(severity="warning", category="y", entity="Y#1", message=""))
    report.add(Issue(severity="warning", category="z", entity="Z#1", message=""))
    s = report.summary()
    assert s["total"] == 3
    assert s["critical"] == 1
    assert s["warning"] == 2
    assert s["info"] == 0
    assert not report.is_clean
    assert len(report.by_severity("warning")) == 2


def test_audit_dry_run_ne_commit_rien(session):
    """dry_run=True (défaut) ne doit toucher à AUCUNE ligne."""
    m = Matiere(nom="X")
    session.add(m)
    session.commit()
    c = Chapitre(matiere_id=m.id, numero=1, titre="Y", maitrise_pct=200.0)
    session.add(c)
    session.commit()

    fixes = repair_all(session, dry_run=True)
    session.refresh(c)
    assert c.maitrise_pct == 200.0  # inchangé
    assert fixes.get("chapitre.maitrise_pct") == 1  # mais détecté
