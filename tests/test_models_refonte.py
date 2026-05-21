"""Tests du schéma DB introduit par la refonte bibliothèque (étape 1).

Vérifie les ajouts strictement structurels — pas de logique métier ici,
juste le contrat du modèle :

- ``Matiere.professeur`` accepte une valeur texte.
- ``Chapitre.matiere_id`` permet le rattachement direct à une Matière
  sans passer par un Cours.
- ``Chapitre.pdfs`` accepte une liste arbitraire de dicts décrivant les
  documents pédagogiques attachés.
- ``Matiere.chapitres`` remonte bien les chapitres rattachés.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import Chapitre, Matiere, UE


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


def test_matiere_accepte_un_professeur(session):
    """Le champ ``professeur`` ajouté à Matière est persisté correctement."""
    m = Matiere(nom="Algèbre L1", professeur="Mme Dupont")
    session.add(m)
    session.commit()

    rechargee = session.query(Matiere).filter_by(nom="Algèbre L1").first()
    assert rechargee.professeur == "Mme Dupont"


def test_chapitre_rattache_a_matiere_sans_cours(session):
    """Un chapitre peut désormais exister avec uniquement un ``matiere_id``,
    sans avoir besoin d'un Cours parent (la couche intermédiaire disparaît
    à l'étape 3)."""
    m = Matiere(nom="Mécanique du point")
    session.add(m)
    session.flush()

    ch = Chapitre(
        matiere_id=m.id,
        numero=1,
        titre="Cinématique 1D",
    )
    session.add(ch)
    session.commit()

    rechargé = session.query(Chapitre).filter_by(titre="Cinématique 1D").first()
    assert rechargé.matiere_id == m.id
    assert rechargé.cours_id is None
    assert rechargé.matiere_obj.nom == "Mécanique du point"


def test_chapitre_porte_une_liste_de_pdfs(session):
    """``Chapitre.pdfs`` accepte une liste arbitraire de dicts décrivant
    les documents pédagogiques (cours magistral, TD, polycopié…)."""
    m = Matiere(nom="Thermodynamique")
    session.add(m)
    session.flush()

    docs = [
        {"path": "data/pdfs/12-cm.pdf", "label": "Cours magistral",
         "uploaded_at": "2026-05-21T10:00:00"},
        {"path": "data/pdfs/12-td.pdf", "label": "TD série 1",
         "uploaded_at": "2026-05-21T10:05:00"},
    ]
    ch = Chapitre(matiere_id=m.id, numero=1, titre="Principes", pdfs=docs)
    session.add(ch)
    session.commit()

    rechargé = session.query(Chapitre).filter_by(titre="Principes").first()
    assert isinstance(rechargé.pdfs, list)
    assert len(rechargé.pdfs) == 2
    assert rechargé.pdfs[0]["label"] == "Cours magistral"
    assert rechargé.pdfs[1]["path"].endswith("12-td.pdf")


def test_relation_matiere_remonte_ses_chapitres(session):
    """``Matiere.chapitres`` doit lister les chapitres rattachés, triés
    par numéro grâce au ``order_by`` de la relation."""
    ue = UE(nom="UE Physique")
    session.add(ue)
    session.flush()

    m = Matiere(nom="Optique", ue_id=ue.id)
    session.add(m)
    session.flush()

    # Insertion volontairement dans le désordre pour valider order_by.
    session.add_all([
        Chapitre(matiere_id=m.id, numero=3, titre="Lentilles minces"),
        Chapitre(matiere_id=m.id, numero=1, titre="Réflexion"),
        Chapitre(matiere_id=m.id, numero=2, titre="Réfraction"),
    ])
    session.commit()

    m_rechargée = session.query(Matiere).filter_by(nom="Optique").first()
    numeros = [c.numero for c in m_rechargée.chapitres]
    assert numeros == [1, 2, 3]
