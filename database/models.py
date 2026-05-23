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

from datetime import datetime, timezone

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
# Utilisateur & Configurations (Domain-Driven Design)
# ---------------------------------------------------------------------------

class Utilisateur(Base):
    """Racine de l'utilisateur (remplace l'ancien Profil monolithique)."""
    __tablename__ = "utilisateurs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nom = Column(String(100), nullable=False, default="")
    prenom = Column(String(100), nullable=True, default="")

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    gamification = relationship("GamificationState", back_populates="utilisateur", uselist=False, cascade="all, delete-orphan")
    systeme = relationship("SystemeConfig", back_populates="utilisateur", uselist=False, cascade="all, delete-orphan")
    logistique = relationship("LogistiqueConfig", back_populates="utilisateur", uselist=False, cascade="all, delete-orphan")
    biometrie = relationship("BiometrieConfig", back_populates="utilisateur", uselist=False, cascade="all, delete-orphan")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Utilisateur id={self.id} nom={self.nom!r}>"


class BiometrieConfig(Base):
    __tablename__ = "biometrie_config"
    id = Column(Integer, primary_key=True, autoincrement=True)
    utilisateur_id = Column(Integer, ForeignKey("utilisateurs.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    heure_lever = Column(Time, nullable=True)
    heure_coucher = Column(Time, nullable=True)
    heures_sommeil_cible = Column(Float, default=8.0)
    besoin_sieste = Column(Boolean, default=False)
    duree_sieste_min = Column(Integer, default=20)
    
    chronotype = Column(String(20), default="intermediaire")
    pic_concentration = Column(String(20), default="matin")
    
    duree_max_session_min = Column(Integer, default=50)
    pause_entre_sessions_min = Column(Integer, default=10)
    methode_travail = Column(String(20), default="mixte")
    capacite_weekend = Column(String(20), default="partiel")
    tolerance_fatigue = Column(String(20), default="moyenne")
    
    heures_etude_cible_par_semaine = Column(Float, default=21.0)
    heures_etude_plafond_par_jour = Column(Float, default=6.0)

    utilisateur = relationship("Utilisateur", back_populates="biometrie")


class LogistiqueConfig(Base):
    __tablename__ = "logistique_config"
    id = Column(Integer, primary_key=True, autoincrement=True)
    utilisateur_id = Column(Integer, ForeignKey("utilisateurs.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    temps_transport_min = Column(Integer, default=0)
    trajets_habituels = Column(JSON, default=dict)
    
    nb_repas_par_jour = Column(Integer, default=3)
    duree_repas_min = Column(Integer, default=30)
    duree_prep_repas_min = Column(Integer, default=30)
    
    contraintes_fixes = Column(JSON, default=list)

    utilisateur = relationship("Utilisateur", back_populates="logistique")


class SystemeConfig(Base):
    __tablename__ = "systeme_config"
    id = Column(Integer, primary_key=True, autoincrement=True)
    utilisateur_id = Column(Integer, ForeignKey("utilisateurs.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    gemini_api_key = Column(String(500), default="")
    gemini_model = Column(String(50), default="gemini-2.5-flash")
    replanning_auto_actif = Column(Boolean, default=True)

    utilisateur = relationship("Utilisateur", back_populates="systeme")


class GamificationState(Base):
    __tablename__ = "gamification_state"
    id = Column(Integer, primary_key=True, autoincrement=True)
    utilisateur_id = Column(Integer, ForeignKey("utilisateurs.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    xp = Column(Integer, default=0)
    niveau = Column(Integer, default=1)
    streak_jours = Column(Integer, default=0)
    streak_record = Column(Integer, default=0)
    derniere_activite_xp = Column(Date, nullable=True)
    nb_quiz_total = Column(Integer, default=0)
    nb_chapitres_maitrise = Column(Integer, default=0)
    nb_seances_sport_total = Column(Integer, default=0)

    utilisateur = relationship("Utilisateur", back_populates="gamification")


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
    date_obtention = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Achievement {self.code} @ {self.date_obtention}>"


# ---------------------------------------------------------------------------
# Semestre — niveau supérieur regroupant plusieurs UE (ex: "S5", "Semestre 1")
# ---------------------------------------------------------------------------
class Semestre(Base):
    """Semestre — regroupe plusieurs Unités d'Enseignement.

    Ex: \"Semestre 5\" contient les UE 'Mathématiques', 'Physique', 'Droit'.
    Le semestre porte le libellé officiel et la période (dates de début/fin).
    """

    __tablename__ = "semestres"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nom = Column(String(200), nullable=False)            # ex: "Semestre 5"
    code = Column(String(50), default="")                 # ex: "S5"
    date_debut = Column(Date, nullable=True)
    date_fin = Column(Date, nullable=True)
    actif = Column(Boolean, default=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relations
    ues = relationship(
        "UE",
        back_populates="semestre",
        order_by="UE.nom",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Semestre id={self.id} nom={self.nom!r}>"


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

    # Rattachement optionnel à un Semestre
    semestre_id = Column(
        Integer,
        ForeignKey("semestres.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relations
    semestre = relationship("Semestre", back_populates="ues")
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

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

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
    # Métadonnées de cache versionné — voir services.cache_versioning.
    # Permettent d'invalider automatiquement les caches IA quand le modèle
    # Gemini, le prompt, ou le contenu PDF change.
    fiche_ia_model = Column(String(100), nullable=True)
    fiche_ia_prompt_version = Column(Integer, nullable=True)
    fiche_ia_texte_sha = Column(String(64), nullable=True)
    fiche_ia_generated_at = Column(DateTime, nullable=True)
    qcm_cache_model = Column(String(100), nullable=True)
    qcm_cache_prompt_version = Column(Integer, nullable=True)
    qcm_cache_texte_sha = Column(String(64), nullable=True)
    quiz_cache_model = Column(String(100), nullable=True)
    quiz_cache_prompt_version = Column(Integer, nullable=True)
    quiz_cache_texte_sha = Column(String(64), nullable=True)

    # --- Notes personnelles ----------------------------------------------
    notes = Column(Text, default="")

    # --- Versioning optimiste (ETag) -------------------------------------
    # Incrémenté à chaque mutation côté UI via `update_chapitre_safe`.
    # Permet de détecter les écrasements concurrents (deux onglets ouverts).
    version = Column(Integer, default=1, nullable=False)

    # --- Horodatages -----------------------------------------------------
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relations
    matiere_obj = relationship("Matiere", back_populates="chapitres")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Chapitre id={self.id} matiere_id={self.matiere_id} "
            f"titre={self.titre!r}>"
        )


# ---------------------------------------------------------------------------
# PdfUpload — idempotence des uploads (SHA-256)
# ---------------------------------------------------------------------------
class PdfUpload(Base):
    """Trace chaque PDF uploadé par son empreinte SHA-256.

    Sert deux objectifs :
      1. **Idempotence** : si l'utilisateur re-dépose deux fois le même PDF
         (même contenu binaire), on évite de relancer une analyse Gemini.
      2. **Audit** : on garde la trace de quel fichier a été ingéré quand,
         pour quelle matière, et combien de chapitres en ont été extraits.
    """

    __tablename__ = "pdf_uploads"
    __table_args__ = (
        UniqueConstraint("sha256", "matiere_id", name="uq_pdfupload_sha_matiere"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sha256 = Column(String(64), nullable=False, index=True)
    matiere_id = Column(
        Integer, ForeignKey("matieres.id", ondelete="CASCADE"), nullable=False
    )
    filename_original = Column(String(255), default="")
    filename_stored = Column(String(255), default="")
    label = Column(String(200), default="")
    nb_chapitres_crees = Column(Integer, default=0)
    uploaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PdfUpload id={self.id} sha={self.sha256[:8]}... matiere={self.matiere_id}>"


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

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

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
    # Flag d'idempotence : True dès la 1re transition vers fait/partiellement.
    # Empêche le farming d'XP / de maîtrise par toggle fait → a_faire → fait.
    xp_attribue = Column(Boolean, default=False, nullable=False)

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

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

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
    # Liste de dicts {activite, type, duree_min, jour_pref, creneau_pref} —
    # le code modules/hebdo/social.py traite cette colonne comme une liste
    # (data_editor), pas comme un dict. Cohérent avec sport_config/projets_config.
    social_config = Column(JSON, default=list)
    intendance_config = Column(JSON, default=list)
    ajustements = Column(JSON, default=dict)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

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

    # Lieu du job — sert à croiser avec ``Profil.trajets_habituels`` pour
    # que l'IA calcule automatiquement le bon temps de trajet.
    # Ex. : "Luxembourg" → matche le trajet "Strasbourg-Luxembourg".
    # Optionnel ; si vide, l'IA utilise ``Profil.temps_transport_min``.
    lieu = Column(String(200), default="")

    date_debut = Column(Date, nullable=True)
    date_fin = Column(Date, nullable=True)

    semaine_id = Column(
        Integer, ForeignKey("semaines.id", ondelete="CASCADE"), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Job id={self.id} titre={self.titre!r} jour={self.jour!r} lieu={self.lieu!r}>"


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
    date_creation = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    date_atteinte = Column(DateTime, nullable=True)        # rempli quand statut → "atteint"
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

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
    "Utilisateur",
    "BiometrieConfig",
    "LogistiqueConfig",
    "SystemeConfig",
    "GamificationState",
    "Achievement",
    "Semestre",
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