"""Validateur centralisé pour le profil utilisateur.

Vérifie 6 invariants métier *avant* l'enregistrement, pour éviter qu'un
profil incohérent se retrouve en base et fasse exploser le moteur
circadien ou la génération de planning.

Chaque invariant produit au plus une `ValidationError` typée, avec
un champ ciblé pour que l'UI puisse l'afficher au bon endroit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any


@dataclass(frozen=True)
class ValidationError:
    """Une erreur de validation localisée sur un champ."""
    champ: str
    message: str
    severite: str = "error"  # "error" | "warning"


def _to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _duree_eveille_heures(lever: time, coucher: time) -> float:
    """Heures éveillées entre lever et coucher (gère le passage minuit)."""
    m_lever = _to_minutes(lever)
    m_coucher = _to_minutes(coucher)
    if m_coucher > m_lever:
        delta_min = m_coucher - m_lever
    else:
        delta_min = (24 * 60 - m_lever) + m_coucher
    return delta_min / 60.0


def validate_biometrie(payload: dict[str, Any]) -> list[ValidationError]:
    """Vérifie les invariants biométriques.

    Invariants vérifiés :
    1. heure_coucher != heure_lever (sinon journée de 0 ou 24h)
    2. heures_sommeil_cible cohérent avec (24h - durée éveillée), tolérance ±2h
    3. pause_entre_sessions_min < duree_max_session_min (sinon plus de pause que de travail)
    4. heures_etude_plafond_par_jour ≤ durée éveillée - sommeil (sinon irréaliste)
    5. heures_etude_cible_par_semaine ≤ plafond × 7 (sinon objectif inatteignable)
    6. duree_sieste_min ∈ [10, 90] (sinon hors range biologique)
    """
    errors: list[ValidationError] = []

    lever = payload.get("heure_lever")
    coucher = payload.get("heure_coucher")
    sommeil_cible = float(payload.get("heures_sommeil_cible", 8.0))
    pause = int(payload.get("pause_entre_sessions_min", 10))
    session = int(payload.get("duree_max_session_min", 50))
    plafond = float(payload.get("heures_etude_plafond_par_jour", 6.0))
    cible_hebdo = float(payload.get("heures_etude_cible_par_semaine", 21.0))
    besoin_sieste = bool(payload.get("besoin_sieste", False))
    duree_sieste = int(payload.get("duree_sieste_min", 20))

    # Invariant 1 : lever != coucher
    if lever is not None and coucher is not None:
        if _to_minutes(lever) == _to_minutes(coucher):
            errors.append(ValidationError(
                champ="heure_coucher",
                message="L'heure de coucher ne peut pas être identique à l'heure de lever.",
            ))
        else:
            # Invariant 2 : cohérence sommeil
            eveille_h = _duree_eveille_heures(lever, coucher)
            sommeil_implicite = 24.0 - eveille_h
            if abs(sommeil_cible - sommeil_implicite) > 2.0:
                errors.append(ValidationError(
                    champ="heures_sommeil_cible",
                    message=(
                        f"Incohérence : tes horaires impliquent {sommeil_implicite:.1f} h "
                        f"de sommeil, mais tu vises {sommeil_cible:.1f} h. "
                        f"Ajuste lever/coucher ou la cible (tolérance ±2 h)."
                    ),
                    severite="warning",
                ))

            # Invariant 4 : plafond compatible avec budget éveillé
            # Budget disponible = éveillé - sommeil_perdu_éventuel - 4h de vie
            budget_max = eveille_h - 4.0  # 4h minimum pour repas/transport/social
            if plafond > budget_max:
                errors.append(ValidationError(
                    champ="heures_etude_plafond_par_jour",
                    message=(
                        f"Le plafond ({plafond:.1f} h) dépasse ton budget journalier "
                        f"réaliste ({budget_max:.1f} h éveillé moins vie courante). "
                        f"Baisse-le ou décale ton lever."
                    ),
                ))

    # Invariant 3 : pause < session
    if pause >= session:
        errors.append(ValidationError(
            champ="pause_entre_sessions_min",
            message=(
                f"Pause ({pause} min) ≥ session de travail ({session} min) : "
                "ça n'a pas de sens. Réduis la pause ou allonge la session."
            ),
        ))

    # Invariant 5 : cible hebdo ≤ plafond × 7
    plafond_x_7 = plafond * 7
    if cible_hebdo > plafond_x_7:
        errors.append(ValidationError(
            champ="heures_etude_cible_par_semaine",
            message=(
                f"Objectif hebdo ({cible_hebdo:.1f} h) > plafond × 7 jours "
                f"({plafond_x_7:.1f} h). L'IA ne pourra jamais le tenir. "
                "Augmente le plafond ou baisse l'objectif."
            ),
        ))

    # Invariant 6 : sieste réaliste
    if besoin_sieste and not (10 <= duree_sieste <= 90):
        errors.append(ValidationError(
            champ="duree_sieste_min",
            message=(
                f"Sieste de {duree_sieste} min hors de la plage biologique "
                "[10–90] min. < 10 min n'a aucun effet, > 90 min entraîne "
                "une inertie du sommeil."
            ),
        ))

    return errors


__all__ = ["ValidationError", "validate_biometrie"]
