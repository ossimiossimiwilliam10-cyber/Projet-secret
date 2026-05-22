"""Tests de la détection des nouveautés à intégrer au planning.

``services.ai_planner.detecter_chapitres_manquants`` est appelé par le
bouton « 🔁 Intégrer mes nouveautés » : si la liste est vide, le bouton
ne s'affiche pas.

L'invariant clé : un chapitre est « manquant » s'il devrait être au
planning (matières sélectionnées dans SaisieHebdo OU révision Leitner
due cette semaine) ET qu'aucune Tâche existante de la semaine ne le
référence dans son champ ``chapitre_ids``.
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import Chapitre, Matiere, SaisieHebdo, Semaine, Tache
from services.ai_planner import detecter_chapitres_manquants


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
    saisie = SaisieHebdo(semaine_id=s.id)
    session.add(saisie)
    session.commit()
    return s


def _make_chap(
    session,
    matiere: Matiere,
    numero: int,
    *,
    niveau: int = 0,
    date_prochaine: date | None = None,
) -> Chapitre:
    ch = Chapitre(
        matiere_id=matiere.id,
        numero=numero,
        titre=f"{matiere.nom} - Chap {numero}",
        niveau_actuel=niveau,
        date_prochaine=date_prochaine,
    )
    session.add(ch)
    session.commit()
    return ch


def _make_tache_etude(session, semaine: Semaine, chapitre_ids: list[int]) -> Tache:
    t = Tache(
        semaine_id=semaine.id,
        type="etude",
        titre="Session",
        jour="lundi",
        heure_debut=time(9, 0),
        heure_fin=time(10, 0),
        chapitre_ids=chapitre_ids,
        statut="a_faire",
    )
    session.add(t)
    session.commit()
    return t


def test_aucun_manquant_quand_tout_est_planifie(session, semaine_courante):
    """Si chaque chapitre sélectionné apparaît dans une Tache existante,
    ``detecter_chapitres_manquants`` retourne une liste vide."""
    m = Matiere(nom="Maths")
    session.add(m)
    session.commit()
    ch1 = _make_chap(session, m, 1)
    ch2 = _make_chap(session, m, 2)

    saisie = session.query(SaisieHebdo).filter_by(semaine_id=semaine_courante.id).first()
    saisie.matieres_selectionnees = [
        {"matiere_id": m.id, "chapitre_ids": [ch1.id, ch2.id]}
    ]
    session.commit()

    _make_tache_etude(session, semaine_courante, [ch1.id, ch2.id])

    manquants = detecter_chapitres_manquants(session, semaine_courante)
    assert manquants == []


def test_chapitre_ajoute_apres_generation_est_detecte(session, semaine_courante):
    """Cas réel : l'utilisateur a généré son planning avec 1 chapitre,
    puis a coché un 2e chapitre dans Études. Le 2e doit être détecté."""
    m = Matiere(nom="Maths")
    session.add(m)
    session.commit()
    ch1 = _make_chap(session, m, 1)
    ch2 = _make_chap(session, m, 2)

    # Planning généré ne couvre que ch1.
    _make_tache_etude(session, semaine_courante, [ch1.id])

    # Mais la saisie hebdo a depuis été enrichie avec ch2.
    saisie = session.query(SaisieHebdo).filter_by(semaine_id=semaine_courante.id).first()
    saisie.matieres_selectionnees = [
        {"matiere_id": m.id, "chapitre_ids": [ch1.id, ch2.id]}
    ]
    session.commit()

    manquants = detecter_chapitres_manquants(session, semaine_courante)
    assert manquants == [ch2.id]


def test_revision_leitner_due_est_detectee_meme_sans_saisie(session, semaine_courante):
    """Un chapitre qui n'est pas dans matieres_selectionnees mais qui a sa
    date_prochaine ≤ date_fin de la semaine doit être détecté."""
    m = Matiere(nom="Physique")
    session.add(m)
    session.commit()

    # Chapitre dont la révision Leitner tombe en milieu de semaine.
    ch_due = _make_chap(
        session, m, 1, niveau=2,
        date_prochaine=semaine_courante.date_debut + timedelta(days=2),
    )
    # Aucune Tâche ne le référence, aucune SaisieHebdo non plus.

    manquants = detecter_chapitres_manquants(session, semaine_courante)
    assert ch_due.id in manquants


def test_chapitre_partiellement_couvert_dans_une_tache_n_est_pas_manquant(
    session, semaine_courante,
):
    """Si une Tache liste plusieurs chapitre_ids dont le nôtre, on
    considère que ce chapitre est planifié — même si la Tâche est mixte."""
    m = Matiere(nom="Info")
    session.add(m)
    session.commit()
    ch_a = _make_chap(session, m, 1)
    ch_b = _make_chap(session, m, 2)

    saisie = session.query(SaisieHebdo).filter_by(semaine_id=semaine_courante.id).first()
    saisie.matieres_selectionnees = [
        {"matiere_id": m.id, "chapitre_ids": [ch_a.id, ch_b.id]}
    ]
    session.commit()

    # Une seule Tache qui couvre les deux.
    _make_tache_etude(session, semaine_courante, [ch_a.id, ch_b.id])

    manquants = detecter_chapitres_manquants(session, semaine_courante)
    assert manquants == []
