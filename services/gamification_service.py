"""Service de gamification — F3a.

Centralise toute la logique XP / niveaux / streak / achievements.

Architecture :
- ``ACHIEVEMENTS`` : catalogue en dur des badges, avec leur logique de déblocage.
- Fonctions ``attribuer_xp_*`` : appelées depuis les hooks (quiz, promotion Leitner…).
  Elles mettent à jour le profil, vérifient les level-ups, vérifient les nouveaux
  achievements débloqués, et retournent une liste d'événements à afficher.
- ``calculer_niveau_pour_xp`` / ``xp_total_pour_niveau`` : formule de progression.
- ``update_streak`` : met à jour la série quotidienne.

Toutes les fonctions DE MUTATION attendent une ``session`` SQLAlchemy ouverte.
Le commit est géré par l'appelant (généralement ``session_scope()``).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from database.models import Achievement, Chapitre, Profil


# ===========================================================================
# Configuration : barème XP
# ===========================================================================
NIVEAU_MAX: int = 50

# Quiz
XP_QUIZ_BASE: int = 10                  # base, même pour score raté
XP_QUIZ_REUSSI_BONUS: int = 20          # score ≥ 0.7
XP_QUIZ_BRILLANT_BONUS: int = 30        # score ≥ 0.9 (cumulé avec REUSSI)

# Sport
XP_SPORT_BASE: int = 40                 # XP de base pour une séance
XP_SPORT_INTENSE_BONUS: int = 20        # Bonus pour séance intense

# Promotion Leitner
XP_NIVEAU_LEITNER_UP: int = 30          # chaque +1 niveau Leitner
XP_CHAPITRE_MAITRISE: int = 200         # quand chapitre atteint niveau max

# Tâche complétée (réserve : si l'app valide les tâches une à une)
XP_TACHE_BASE_PAR_30MIN: int = 5
XP_TACHE_OBLIGATOIRE_MULTIPLICATEUR: float = 1.5

# Streak
XP_STREAK_JOURNALIER: int = 10
STREAK_MULTIPLICATEUR_PAR_JOUR: float = 1.1
STREAK_MULTIPLICATEUR_CAP: float = 3.0


# ===========================================================================
# Catalogue des achievements
# ===========================================================================
@dataclass(frozen=True)
class AchievementDef:
    code: str
    icone: str
    nom: str
    description: str
    rarete: str  # "commun" | "peu_commun" | "rare" | "epique" | "legendaire"
    # check(profil, event_type, event_data) → True si débloquable maintenant
    check: Any = field(repr=False)


RARETE_COULEURS: dict[str, str] = {
    "commun": "#9ca3af",      # gris
    "peu_commun": "#10b981",  # vert
    "rare": "#3b82f6",        # bleu
    "epique": "#a855f7",      # violet
    "legendaire": "#f59e0b",  # or
}


def _check_first_quiz(profil: Profil, event_type: str, data: dict) -> bool:
    return event_type == "quiz" and profil.nb_quiz_total >= 1


def _check_perfect_quiz(profil: Profil, event_type: str, data: dict) -> bool:
    return event_type == "quiz" and data.get("score", 0) >= 0.9


def _check_streak(seuil: int):
    def _f(profil: Profil, event_type: str, data: dict) -> bool:
        return profil.streak_jours >= seuil
    return _f


def _check_chapitres_maitrise(seuil: int):
    def _f(profil: Profil, event_type: str, data: dict) -> bool:
        return profil.nb_chapitres_maitrise >= seuil
    return _f


def _check_niveau(seuil: int):
    def _f(profil: Profil, event_type: str, data: dict) -> bool:
        return profil.niveau >= seuil
    return _f


def _check_nb_quiz(seuil: int):
    def _f(profil: Profil, event_type: str, data: dict) -> bool:
        return profil.nb_quiz_total >= seuil
    return _f


def _check_xp(seuil: int):
    def _f(profil: Profil, event_type: str, data: dict) -> bool:
        return profil.xp >= seuil
    return _f


def _check_nb_sport(seuil: int):
    def _f(profil: Profil, event_type: str, data: dict) -> bool:
        return (profil.nb_seances_sport_total or 0) >= seuil
    return _f


ACHIEVEMENTS: list[AchievementDef] = [
    # === Quiz ===
    AchievementDef(
        code="first_quiz", icone="🎯", nom="Premier pas",
        description="Tu as fait ton premier quiz.",
        rarete="commun", check=_check_first_quiz,
    ),
    AchievementDef(
        code="perfect_quiz", icone="🏆", nom="Score parfait",
        description="Tu as obtenu un score ≥ 90 % à un quiz.",
        rarete="rare", check=_check_perfect_quiz,
    ),
    AchievementDef(
        code="quiz_10", icone="📝", nom="Régulier",
        description="10 quiz complétés.",
        rarete="commun", check=_check_nb_quiz(10),
    ),
    AchievementDef(
        code="quiz_50", icone="📚", nom="Studieux",
        description="50 quiz complétés.",
        rarete="peu_commun", check=_check_nb_quiz(50),
    ),
    AchievementDef(
        code="quiz_100", icone="🎓", nom="Marathon de quiz",
        description="100 quiz complétés.",
        rarete="rare", check=_check_nb_quiz(100),
    ),
    # === Streak ===
    AchievementDef(
        code="streak_3", icone="🔥", nom="On commence à chauffer",
        description="3 jours consécutifs d'activité.",
        rarete="commun", check=_check_streak(3),
    ),
    AchievementDef(
        code="streak_7", icone="🔥", nom="Une semaine !",
        description="7 jours consécutifs d'activité.",
        rarete="peu_commun", check=_check_streak(7),
    ),
    AchievementDef(
        code="streak_30", icone="🌟", nom="Un mois plein",
        description="30 jours consécutifs d'activité.",
        rarete="epique", check=_check_streak(30),
    ),
    AchievementDef(
        code="streak_100", icone="👑", nom="Centurion",
        description="100 jours consécutifs d'activité.",
        rarete="legendaire", check=_check_streak(100),
    ),
    # === Chapitres maîtrisés ===
    AchievementDef(
        code="first_master", icone="🧠", nom="Première maîtrise",
        description="Premier chapitre amené au niveau Leitner maximum.",
        rarete="peu_commun", check=_check_chapitres_maitrise(1),
    ),
    AchievementDef(
        code="master_10", icone="💡", nom="Érudit",
        description="10 chapitres au niveau max.",
        rarete="rare", check=_check_chapitres_maitrise(10),
    ),
    AchievementDef(
        code="master_50", icone="💎", nom="Maître absolu",
        description="50 chapitres au niveau max.",
        rarete="legendaire", check=_check_chapitres_maitrise(50),
    ),
    # === Niveau utilisateur ===
    AchievementDef(
        code="level_5", icone="⭐", nom="Niveau 5",
        description="Tu as atteint le niveau 5.",
        rarete="commun", check=_check_niveau(5),
    ),
    AchievementDef(
        code="level_10", icone="🌠", nom="Niveau 10",
        description="Tu as atteint le niveau 10.",
        rarete="peu_commun", check=_check_niveau(10),
    ),
    AchievementDef(
        code="level_25", icone="💫", nom="Niveau 25",
        description="Tu as atteint le niveau 25.",
        rarete="rare", check=_check_niveau(25),
    ),
    AchievementDef(
        code="level_50", icone="👑", nom="Niveau MAX",
        description="Tu as atteint le niveau 50 (maximum).",
        rarete="legendaire", check=_check_niveau(50),
    ),
    # === Cumul XP ===
    AchievementDef(
        code="xp_1k", icone="✨", nom="Premier millier",
        description="1 000 XP cumulés.",
        rarete="commun", check=_check_xp(1_000),
    ),
    AchievementDef(
        code="xp_10k", icone="🚀", nom="Décamillionnaire d'XP",
        description="10 000 XP cumulés.",
        rarete="epique", check=_check_xp(10_000),
    ),
    AchievementDef(
        code="xp_50k", icone="🌌", nom="Légende vivante",
        description="50 000 XP cumulés.",
        rarete="legendaire", check=_check_xp(50_000),
    ),
    # === Sport ===
    AchievementDef(
        code="first_sport", icone="🏋️‍♀️", nom="Mens sana in corpore sano",
        description="Première séance de sport enregistrée.",
        rarete="commun", check=_check_nb_sport(1),
    ),
    AchievementDef(
        code="sport_10", icone="🏃‍♂️", nom="Athlète en herbe",
        description="10 séances de sport complétées.",
        rarete="peu_commun", check=_check_nb_sport(10),
    ),
    AchievementDef(
        code="sport_50", icone="🏆", nom="Guerrier",
        description="50 séances de sport. La discipline est ton alliée.",
        rarete="rare", check=_check_nb_sport(50),
    ),
]


def get_achievement_def(code: str) -> AchievementDef | None:
    """Retourne la définition d'un achievement par son code."""
    return next((a for a in ACHIEVEMENTS if a.code == code), None)


# ===========================================================================
# Calcul des niveaux
# ===========================================================================
def xp_total_pour_niveau(niveau: int) -> int:
    """XP cumulés requis pour atteindre ``niveau``.

    Formule progressive : 50 × (n-1) × n  → croissance quadratique.
    Niv 1 = 0 XP, Niv 2 = 100, Niv 5 = 1000, Niv 10 = 4500, Niv 50 = 122500.
    """
    if niveau <= 1:
        return 0
    if niveau > NIVEAU_MAX:
        niveau = NIVEAU_MAX
    return 50 * (niveau - 1) * niveau


def calculer_niveau_pour_xp(xp: int) -> int:
    """Retourne le niveau correspondant à un total XP donné."""
    if xp <= 0:
        return 1
    n = 1
    while n < NIVEAU_MAX and xp_total_pour_niveau(n + 1) <= xp:
        n += 1
    return n


def progression_niveau(xp: int) -> dict[str, int | float]:
    """Retourne l'état de progression pour l'UI.

    Renvoie : niveau, xp, xp_palier (seuil du niveau actuel),
              xp_suivant (seuil du niveau d'après), ratio (0-1).
    """
    niveau = calculer_niveau_pour_xp(xp)
    xp_palier = xp_total_pour_niveau(niveau)
    xp_suivant = xp_total_pour_niveau(niveau + 1) if niveau < NIVEAU_MAX else xp_palier
    if niveau >= NIVEAU_MAX:
        ratio = 1.0
        xp_dans_palier = 0
        xp_palier_taille = 1
    else:
        xp_palier_taille = xp_suivant - xp_palier
        xp_dans_palier = xp - xp_palier
        ratio = xp_dans_palier / xp_palier_taille if xp_palier_taille > 0 else 0.0
    return {
        "niveau": niveau,
        "xp": xp,
        "xp_palier": xp_palier,
        "xp_suivant": xp_suivant,
        "xp_dans_palier": xp_dans_palier,
        "xp_palier_taille": xp_palier_taille,
        "ratio": min(1.0, max(0.0, ratio)),
        "max_atteint": niveau >= NIVEAU_MAX,
    }


# ===========================================================================
# Streak
# ===========================================================================
def update_streak(profil: Profil, today: datetime.date | None = None) -> dict[str, Any]:
    """Met à jour le streak en fonction de la date du jour.

    - Si ``derniere_activite_xp`` == aujourd'hui → rien à faire (déjà compté)
    - Si == hier → streak +1
    - Si plus ancien → streak repart à 1
    - Si None (jamais joué) → streak = 1

    Retourne {streak: int, change: 'init'|'cont'|'reset'|'noop', record_battu: bool}.
    """
    today = today or datetime.date.today()
    derniere = profil.derniere_activite_xp

    if derniere == today:
        return {"streak": profil.streak_jours, "change": "noop", "record_battu": False}

    if derniere is None:
        nouveau_streak = 1
        change = "init"
    elif (today - derniere).days == 1:
        nouveau_streak = (profil.streak_jours or 0) + 1
        change = "cont"
    else:
        nouveau_streak = 1
        change = "reset"

    record_battu = nouveau_streak > (profil.streak_record or 0)

    profil.streak_jours = nouveau_streak
    profil.derniere_activite_xp = today
    if record_battu:
        profil.streak_record = nouveau_streak

    return {
        "streak": nouveau_streak,
        "change": change,
        "record_battu": record_battu,
    }


def calcul_multiplicateur_streak(streak: int) -> float:
    """Multiplicateur XP dérivé du streak (cap à 3×)."""
    if streak <= 1:
        return 1.0
    mult = STREAK_MULTIPLICATEUR_PAR_JOUR ** (streak - 1)
    return min(mult, STREAK_MULTIPLICATEUR_CAP)


# ===========================================================================
# Vérification des achievements
# ===========================================================================
def _achievements_deja_debloques(session: Session) -> set[str]:
    """Retourne l'ensemble des codes déjà débloqués.

    Force un flush pour voir les éventuels achievements ajoutés plus tôt
    dans la même session mais pas encore commités (autoflush=False).
    """
    session.flush()
    return {row.code for row in session.query(Achievement).all()}


def check_nouveaux_achievements(
    session: Session,
    profil: Profil,
    event_type: str = "",
    event_data: dict | None = None,
) -> list[AchievementDef]:
    """Vérifie quels achievements le profil débloque MAINTENANT.

    Insère les nouveaux dans la BD et retourne la liste pour affichage côté UI.
    """
    event_data = event_data or {}
    deja = _achievements_deja_debloques(session)
    nouveaux: list[AchievementDef] = []
    for ach in ACHIEVEMENTS:
        if ach.code in deja:
            continue
        try:
            if ach.check(profil, event_type, event_data):
                session.add(Achievement(code=ach.code))
                nouveaux.append(ach)
        except Exception:
            # Si le check plante, on ignore silencieusement — ne pas bloquer
            # l'attribution d'XP à cause d'un bug de catalogue.
            continue
    return nouveaux


# ===========================================================================
# Attribution d'XP — points d'entrée principaux
# ===========================================================================
@dataclass
class GainXP:
    """Représente un gain XP à afficher à l'utilisateur."""
    xp_gagne: int
    raison: str                      # ex: "Quiz réussi"
    niveau_avant: int = 0
    niveau_apres: int = 0
    nouveaux_achievements: list[AchievementDef] = field(default_factory=list)
    streak_actuel: int = 0
    streak_continue: bool = False    # True si streak vient de +1

    @property
    def level_up(self) -> bool:
        return self.niveau_apres > self.niveau_avant


def _appliquer_gain(
    session: Session,
    profil: Profil,
    xp_brut: int,
    raison: str,
    event_type: str,
    event_data: dict,
    update_streak_aussi: bool = True,
) -> GainXP:
    """Logique commune : applique multiplicateur streak, met à jour profil,
    check level-up, check achievements. Retourne le GainXP complet pour
    affichage."""
    streak_info = {"streak": profil.streak_jours, "change": "noop"}
    if update_streak_aussi:
        streak_info = update_streak(profil)

    mult = calcul_multiplicateur_streak(profil.streak_jours or 1)
    xp_final = int(round(xp_brut * mult))

    niveau_avant = profil.niveau or 1
    profil.xp = (profil.xp or 0) + xp_final
    profil.niveau = calculer_niveau_pour_xp(profil.xp)
    niveau_apres = profil.niveau

    nouveaux = check_nouveaux_achievements(session, profil, event_type, event_data)

    return GainXP(
        xp_gagne=xp_final,
        raison=raison,
        niveau_avant=niveau_avant,
        niveau_apres=niveau_apres,
        nouveaux_achievements=nouveaux,
        streak_actuel=profil.streak_jours or 0,
        streak_continue=streak_info["change"] in ("init", "cont"),
    )


def attribuer_xp_quiz(
    session: Session,
    profil: Profil,
    score: float,
    chapitre: Chapitre | None = None,
) -> GainXP:
    """À appeler après un quiz, quel que soit le score.

    Calcul :
    - Base = XP_QUIZ_BASE (10)
    - +20 si score ≥ 0.7 (quiz réussi)
    - +30 si score ≥ 0.9 (cumulé)
    - × multiplicateur streak
    """
    profil.nb_quiz_total = (profil.nb_quiz_total or 0) + 1

    xp = XP_QUIZ_BASE
    raison = f"Quiz fait (score {int(score * 100)}%)"
    if score >= 0.7:
        xp += XP_QUIZ_REUSSI_BONUS
        raison = f"Quiz réussi (score {int(score * 100)}%)"
    if score >= 0.9:
        xp += XP_QUIZ_BRILLANT_BONUS
        raison = f"Quiz brillant (score {int(score * 100)}%)"

    event_data = {"score": score, "chapitre_id": chapitre.id if chapitre else None}
    return _appliquer_gain(session, profil, xp, raison, "quiz", event_data)


def attribuer_xp_promotion_leitner(
    session: Session,
    profil: Profil,
    niveau_avant: int,
    niveau_apres: int,
    chapitre: Chapitre,
    niveau_max_leitner: int = 13,
) -> GainXP | None:
    """À appeler après une promotion de niveau Leitner (niveau_apres > niveau_avant).

    Si le chapitre atteint le niveau max → bonus + flag maîtrise.
    """
    if niveau_apres <= niveau_avant:
        return None  # pas de promotion

    nb_promotions = niveau_apres - niveau_avant
    xp = XP_NIVEAU_LEITNER_UP * nb_promotions
    raison = f"+{nb_promotions} niveau Leitner sur '{chapitre.titre[:30]}'"

    if niveau_apres >= niveau_max_leitner and niveau_avant < niveau_max_leitner:
        xp += XP_CHAPITRE_MAITRISE
        profil.nb_chapitres_maitrise = (profil.nb_chapitres_maitrise or 0) + 1
        raison += " · 🌟 Chapitre maîtrisé !"

    event_data = {
        "chapitre_id": chapitre.id,
        "niveau_avant": niveau_avant,
        "niveau_apres": niveau_apres,
    }
    # Pas de update_streak ici : généralement appelé juste après attribuer_xp_quiz
    # qui l'a déjà fait. On évite de double-update.
    return _appliquer_gain(
        session, profil, xp, raison, "promotion_leitner", event_data,
        update_streak_aussi=False,
    )


def attribuer_xp_tache(
    session: Session,
    profil: Profil,
    duree_min: int,
    obligatoire: bool = False,
    titre: str = "",
) -> GainXP:
    """À appeler quand une tâche est marquée terminée."""
    base = (duree_min / 30) * XP_TACHE_BASE_PAR_30MIN
    if obligatoire:
        base *= XP_TACHE_OBLIGATOIRE_MULTIPLICATEUR
    xp = max(1, int(round(base)))
    raison = f"Tâche faite : {titre[:30]}" if titre else "Tâche faite"
    return _appliquer_gain(session, profil, xp, raison, "tache_faite", {"duree_min": duree_min})


def attribuer_xp_sport(
    session: Session,
    profil: Profil,
    intensite: str = "",
    titre: str = "",
) -> GainXP:
    """À appeler quand une séance de sport est marquée terminée."""
    profil.nb_seances_sport_total = (profil.nb_seances_sport_total or 0) + 1
    
    xp = XP_SPORT_BASE
    if "Intense" in intensite:
        xp += XP_SPORT_INTENSE_BONUS
    
    raison = f"Séance de sport : {titre[:30]}" if titre else "Séance de sport"
    return _appliquer_gain(session, profil, xp, raison, "sport_fait", {"intensite": intensite})


# ===========================================================================
# Helper : récupère ou crée le profil singleton
# ===========================================================================
def get_or_create_profil(session: Session) -> Profil:
    """Retourne le profil unique, en le créant si besoin (cas premier lancement)."""
    profil = session.query(Profil).first()
    if profil is None:
        profil = Profil()
        session.add(profil)
        session.flush()
    return profil


__all__ = [
    # Catalogue
    "ACHIEVEMENTS",
    "AchievementDef",
    "RARETE_COULEURS",
    "get_achievement_def",
    # Niveaux
    "NIVEAU_MAX",
    "xp_total_pour_niveau",
    "calculer_niveau_pour_xp",
    "progression_niveau",
    # Streak
    "update_streak",
    "calcul_multiplicateur_streak",
    # XP
    "GainXP",
    "attribuer_xp_quiz",
    "attribuer_xp_promotion_leitner",
    "attribuer_xp_tache",
    "attribuer_xp_sport",
    "check_nouveaux_achievements",
    # Helper
    "get_or_create_profil",
]