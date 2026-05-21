"""
Modèles SQLAlchemy de l'application.

Conventions :
- Une seule ligne dans ``profil`` (singleton applicatif — pas d'utilisateur multiple).
- Les champs ``JSON`` stockent des structures Python (listes ou dicts) sérialisées
  automatiquement par SQLAlchemy via le type ``JSON`` (supporté nativement par
  SQLite via le pilote sqlite3).
- Les horodatages ``created_at`` / ``updated_at`` utilisent UTC pour rester
  comparables d'un fuseau à l'autre.

Hiérarchie pédagogique : ``UE → Matière → Chapitre``.
- ``UE`` (Unité d'Enseignement) : conteneur logique, ex. "Mathématiques",
  "Physique". Porte les crédits ECTS et le code semestre.
- ``Matiere`` : sous-discipline d'une UE, ex. "Algèbre", "Analyse". Porte
  le professeur.
- ``Chapitre`` : subdivision d'une matière, granularité de la révision
  espacée et porteuse des PDFs pédagogiques (liste).

Le rattachement à une UE est OPTIONNEL pour Matière.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .db import Base


# ---------------------------------------------------------------------------
# Profil — singleton
# ---------------------------------------------------------------------------
class Profil(Base):
    """Profil unique de l'étudiant (une seule ligne attendue en pratique)."""

    __tablename__ = "profil"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nom = Column(String(100), nullable=False, default="")

    # --- Rythme & sommeil --------------------------------------------------
    heure_lever = Column(Time, nullable=True)
    heure_coucher = Column(Time, nullable=True)
    heures_sommeil_cible = Column(Float, default=8.0)
    chronotype = Column(String(20), default="intermediaire")
    pic_concentration = Column(String(20), default="matin")

    # --- Capacité de travail ----------------------------------------------
    duree_max_session_min = Column(Integer, default=50)
    pause_entre_sessions_min = Column(Integer, default=10)
    methode_travail = Column(String(20), default="mixte")
    capacite_weekend = Column(String(20), default="partiel")
    tolerance_fatigue = Column(String(20), default="moyenne")

    # --- Transport ---------------------------------------------------------
    temps_transport_min = Column(Integer, default=0)
    trajets_habituels = Column(JSON, default=dict)

    # --- Repas & sieste ----------------------------------------------------
    nb_repas_par_jour = Column(Integer, default=3)
    duree_repas_min = Column(Integer, default=30)
    duree_prep_repas_min = Column(Integer, default=30)
    besoin_sieste = Column(Boolean, default=False)
    duree_sieste_min = Column(Integer, default=20)

    # --- Contraintes fixes récurrentes ------------------------------------
    contraintes_fixes = Column(JSON, default=list)

    # --- Paramètres IA -----------------------------------------------------
    gemini_api_key = Column(String(500), default="")
    gemini_model = Column(String(50), default="gemini-2.5-flash")

    # --- Gamification (F3a) ----------------------------------------------
    xp = Column(Integer, default=0)                       # XP total cumulé
    niveau = Column(Integer, default=1)                   # niveau dérivé de xp
    streak_jours = Column(Integer, default=0)             # série actuelle
    streak_record = Column(Integer, default=0)            # record perso de streak
    derniere_activite_xp = Column(Date, nullable=True)    # dernière date où XP gagnés (pour streak)
    nb_quiz_total = Column(Integer, default=0)            # cumul quiz faits
    nb_chapitres_maitrise = Column(Integer, default=0)    # cumul chap au niveau max
    replanning_auto_actif = Column(Boolean, default=True) # A1 : auto-replanning ?

    # --- Horodatages -------------------------------------------------------
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Profil id={self.id} nom={self.nom!r}>"


# ---------------------------------------------------------------------------
# Achievement — badge débloqué par l'étudiant (F3a)
# ---------------------------------------------------------------------------
class Achievement(Base):
    """Achievement débloqué par l'utilisateur.

    Le **catalogue** des achievements (les badges existants avec leurs noms,
    icônes, conditions) est défini en dur dans ``services/gamification_service.py``.
    Cette table garde seulement l'historique de ce qui a été débloqué et quand.
    """

    __tablename__ = "achievements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(50), nullable=False, unique=True)  # ex: "perfect_quiz"
    date_obtention = Column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Achievement {self.code} @ {self.date_obtention}>"


# ---------------------------------------------------------------------------
# UE — Unité d'Enseignement (regroupement de cours)
# ---------------------------------------------------------------------------
class UE(Base):
    """Unité d'Enseignement — regroupe plusieurs matières.

    Ex: UE 'Mathématiques' contient les matières 'Analyse' et 'Algèbre
    linéaire'. L'UE porte les crédits ECTS et le code semestre ; l'UE
    est purement organisationnelle.
    """

    __tablename__ = "ues"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nom = Column(String(200), nullable=False)
    code = Column(String(50), default="")           # ex: "MATH301"
    semestre = Column(String(20), default="")        # ex: "S5"
    credits_ects = Column(Float, nullable=True)      # total de l'UE
    couleur = Column(String(20), default="#4cd137")  # hex code pour distinction visuelle
    actif = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    # Matières rattachées (ex: UE Maths → Algèbre, Analyse). Quand l'UE
    # est supprimée, les matières sont détachées (ue_id = NULL).
    matieres = relationship(
        "Matiere",
        back_populates="ue",
        order_by="Matiere.nom",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UE id={self.id} nom={self.nom!r} ({self.semestre})>"


# ---------------------------------------------------------------------------
# Matiere — sous-niveau d'une UE (ex: 'Algèbre' dans l'UE 'Mathématiques')
# ---------------------------------------------------------------------------
class Matiere(Base):
    """Matière — sous-discipline d'une UE.

    Ex: l'UE 'Mathématiques pour l'ingénieur' contient les matières
    'Algèbre' et 'Analyse'. Chaque matière peut contenir plusieurs cours
    (PDFs distincts : cours magistral, TD, polycopié...).

    Hérite de la couleur de l'UE (pas de champ couleur propre).
    Le rattachement à une UE est optionnel — une matière peut exister
    sans UE pour les utilisateurs sans cette hiérarchie.
    """

    __tablename__ = "matieres"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nom = Column(String(200), nullable=False)
    code = Column(String(50), default="")  # ex: 'MATH301-A'
    professeur = Column(String(200), default="")
    actif = Column(Boolean, default=True)

    # Rattachement optionnel à une UE
    ue_id = Column(
        Integer,
        ForeignKey("ues.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    ue = relationship("UE", back_populates="matieres")
    # Rattachement direct Matière → Chapitre (refonte bibliothèque).
    chapitres = relationship(
        "Chapitre",
        back_populates="matiere_obj",
        order_by="Chapitre.numero",
    )

    def __repr__(self) -> str:  # pragma: no cover
        ue_nom = self.ue.nom if self.ue else "—"
        return f"<Matiere id={self.id} nom={self.nom!r} ue={ue_nom!r}>"


# ---------------------------------------------------------------------------
# Chapitre — avec révision espacée (Leitner) + caches IA
# ---------------------------------------------------------------------------
class Chapitre(Base):
    """Chapitre d'une matière — granularité utilisée pour suivre la maîtrise,
    pour la révision espacée (algo Leitner), et pour stocker les PDFs
    pédagogiques.

    Rattaché directement à une ``Matiere`` via ``matiere_id``. Porte un
    champ ``pdfs`` (liste JSON de ``{path, label, uploaded_at}``) qui
    permet à un chapitre d'avoir plusieurs documents (cours magistral,
    TD, polycopié…).
    """

    __tablename__ = "chapitres"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Rattachement direct à la matière (refonte bibliothèque).
    matiere_id = Column(
        Integer,
        ForeignKey("matieres.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    numero = Column(Integer, nullable=False)
    titre = Column(String(300), nullable=False)

    # --- Documents pédagogiques (liste de PDFs) --------------------------
    # Chaque entrée : {"path": "data/pdfs/3-cm.pdf", "label": "Cours magistral",
    # "uploaded_at": "2026-05-21T10:30:00"}. Vide par défaut.
    pdfs = Column(JSON, default=list)

    # --- Estimation & suivi de maîtrise classique (% 0-100) ---------------
    maitrise_pct = Column(Float, default=0.0)
    type_travail_restant = Column(String(50), default="premiere_lecture")
    temps_estime_h = Column(Float, default=0.0)

    # --- Révision espacée (algo Leitner) ----------------------------------
    niveau_actuel = Column(Integer, default=0)
    date_prochaine = Column(Date, nullable=True)
    historique_quiz = Column(JSON, default=list)

    # --- Caches IA (évitent de re-payer Gemini à chaque ouverture) -------
    fiche_ia = Column(Text, nullable=True)
    qcm_cache = Column(JSON, nullable=True)
    quiz_cache = Column(JSON, nullable=True)
    texte_cache = Column(Text, nullable=True)

    # --- Notes personnelles ----------------------------------------------
    notes = Column(Text, default="")

    # --- Horodatages -----------------------------------------------------
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    matiere_obj = relationship("Matiere", back_populates="chapitres")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Chapitre id={self.id} matiere_id={self.matiere_id} "
            f"titre={self.titre!r}>"
        )


# ---------------------------------------------------------------------------
# Semaines & tâches
# ---------------------------------------------------------------------------
class Semaine(Base):
    __tablename__ = "semaines"
    __table_args__ = (
        UniqueConstraint("annee", "numero_semaine", name="uq_semaine_annee_numero"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    numero_semaine = Column(Integer, nullable=False)
    annee = Column(Integer, nullable=False)
    date_debut = Column(Date, nullable=False)
    date_fin = Column(Date, nullable=False)

    statut = Column(String(20), default="en_cours")
    score_realisme = Column(Integer, nullable=True)
    taux_completion_pct = Column(Float, default=0.0)
    bilan_ia = Column(Text, default="")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    taches = relationship(
        "Tache",
        back_populates="semaine",
        foreign_keys="Tache.semaine_id",
        cascade="all, delete-orphan",
        order_by="Tache.jour, Tache.heure_debut",
    )
    saisie = relationship(
        "SaisieHebdo",
        back_populates="semaine",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Semaine {self.annee}-W{self.numero_semaine:02d}>"


class Tache(Base):
    __tablename__ = "taches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    semaine_id = Column(
        Integer,
        ForeignKey("semaines.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    type = Column(String(30), default="etude")
    titre = Column(String(300), nullable=False)
    description = Column(Text, default="")

    jour = Column(String(15), nullable=False)
    heure_debut = Column(Time, nullable=False)
    heure_fin = Column(Time, nullable=False)
    duree_min = Column(Integer, default=0)

    priorite = Column(String(15), default="normale")
    obligatoire = Column(Boolean, default=False)
    statut = Column(String(20), default="a_faire")

    # Rattachement direct à la matière (refonte bibliothèque).
    matiere_id = Column(
        Integer,
        ForeignKey("matieres.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    chapitre_ids = Column(JSON, nullable=True)
    reportee_depuis_semaine_id = Column(
        Integer,
        ForeignKey("semaines.id", ondelete="SET NULL"),
        nullable=True,
    )

    justification_ia = Column(Text, default="")
    commentaire_etudiant = Column(Text, default="")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    semaine = relationship(
        "Semaine",
        back_populates="taches",
        foreign_keys=[semaine_id],
    )
    semaine_origine = relationship(
        "Semaine",
        foreign_keys=[reportee_depuis_semaine_id],
    )
    matiere_obj = relationship("Matiere", foreign_keys=[matiere_id])

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Tache id={self.id} {self.jour} "
            f"{self.heure_debut}-{self.heure_fin} {self.titre!r}>"
        )


# ---------------------------------------------------------------------------
# Saisies hebdomadaires
# ---------------------------------------------------------------------------
class SaisieHebdo(Base):
    __tablename__ = "saisies_hebdo"

    id = Column(Integer, primary_key=True, autoincrement=True)
    semaine_id = Column(
        Integer,
        ForeignKey("semaines.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Liste de dicts {matiere_id, chapitre_ids, type_travail, urgence}
    # (anciennement "cours_selectionnes" — renommé refonte bibliothèque).
    matieres_selectionnees = Column(JSON, default=list)
    travaux_ponctuels = Column(JSON, default=list)
    sport_config = Column(JSON, default=list)
    courses_config = Column(JSON, default=dict)
    projets_config = Column(JSON, default=list)
    dev_perso_config = Column(JSON, default=list)
    social_config = Column(JSON, default=dict)
    intendance_config = Column(JSON, default=list)
    ajustements = Column(JSON, default=dict)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    semaine = relationship("Semaine", back_populates="saisie")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SaisieHebdo semaine_id={self.semaine_id}>"


# ---------------------------------------------------------------------------
# Job — activités professionnelles
# ---------------------------------------------------------------------------
class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    titre = Column(String(200), nullable=False)
    jour = Column(String(15), nullable=False)
    heure_debut = Column(Time, nullable=False)
    heure_fin = Column(Time, nullable=False)

    date_debut = Column(Date, nullable=True)
    date_fin = Column(Date, nullable=True)

    semaine_id = Column(
        Integer, ForeignKey("semaines.id", ondelete="CASCADE"), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Job id={self.id} titre={self.titre!r} jour={self.jour!r}>"


# ---------------------------------------------------------------------------
# Objectif — objectif personnel de l'étudiant, avec stratégie IA (F3b)
# ---------------------------------------------------------------------------
class Objectif(Base):
    """Un objectif académique personnalisé de l'étudiant.

    Exemples : "Avoir 15 au partiel d'algèbre du 15 juin", "Maîtriser tous
    les chapitres d'analyse avant l'examen", "Réviser tout le cours de
    physique d'ici fin du semestre".

    Quand l'étudiant crée un objectif, Gemini analyse l'état actuel et
    propose une **stratégie** (pondérations par chapitre, heures à investir,
    conseils). Si l'étudiant l'adopte, les pondérations sont appliquées dans
    tous les futurs plannings hebdomadaires générés.
    """

    __tablename__ = "objectifs"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # --- Définition ---
    nom = Column(String(200), nullable=False)              # ex: "15 au partiel d'algèbre"
    description = Column(Text, default="")                 # contraintes, motivations, libre
    matiere_id = Column(                                   # matière visée (NULL = global, toutes les matières)
        Integer, ForeignKey("matieres.id", ondelete="SET NULL"), nullable=True, index=True
    )
    note_cible = Column(Float, nullable=True)              # /20 — optionnel (peut être un objectif "qualitatif")
    date_cible = Column(Date, nullable=False)              # deadline

    # --- État ---
    statut = Column(String(20), default="actif")          # "actif" | "atteint" | "abandonne"

    # --- Stratégie IA proposée par Gemini ---
    # Structure typique :
    # {
    #   "realisme": "ambitieux",
    #   "justification": "Tu es à 60% de maîtrise, il te reste 3 semaines.",
    #   "heures_total_estimees": 25,
    #   "heures_par_semaine": 8.5,
    #   "ponderations_chapitres": {"5": 2.5, "7": 1.8, ...},
    #   "ordre_priorite": [5, 7, 3, ...],
    #   "conseils": ["Concentre-toi sur les chapitres 5 et 7", ...]
    # }
    strategie_ia = Column(JSON, nullable=True)
    # Cache rapide des pondérations (extrait de strategie_ia pour query rapide).
    # Format : {"<chapitre_id>": float}. Utilisé par ai_planner pour booster
    # les priorités dans les plannings futurs.
    ponderations = Column(JSON, default=dict)

    # --- Horodatages ---
    date_creation = Column(DateTime, default=datetime.utcnow)
    date_atteinte = Column(DateTime, nullable=True)        # rempli quand statut → "atteint"
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relation vers la matière (optionnelle)
    matiere = relationship("Matiere", foreign_keys=[matiere_id])

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Objectif id={self.id} nom={self.nom!r} statut={self.statut}>"


class CheckInQuotidien(Base):
    """Auto-évaluation biomécanique quotidienne de l'étudiant.

    Une seule ligne par date — l'UI fait un upsert sur ``date``.
    Les trois métriques sont sur une échelle 1-10.
    """

    __tablename__ = "checkin_quotidien"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, unique=True, index=True, nullable=False)
    fatigue_physique = Column(Integer, nullable=False, default=5)
    charge_mentale = Column(Integer, nullable=False, default=5)
    qualite_sommeil = Column(Integer, nullable=False, default=5)

    def __repr__(self) -> str:
        return (
            f"<CheckInQuotidien date={self.date} "
            f"fatigue={self.fatigue_physique} mental={self.charge_mentale} "
            f"sommeil={self.qualite_sommeil}>"
        )


__all__ = [
    "Profil",
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