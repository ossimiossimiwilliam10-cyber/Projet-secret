"""Moteur de planification déterministe (couche pré-IA).

Ce module fait en Python tout ce qu'un LLM fait mal : compter, équilibrer,
lisser. Il ne touche jamais à Gemini ; il fournit à ``build_planner_prompt``
une répartition par jour déjà calculée, que l'IA n'aura plus qu'à habiller
en créneaux horaires.

Trois responsabilités :

1. **Répartir les nouveaux chapitres** sur la semaine en garantissant
   « max 1 nouveau chapitre par matière par jour » (problème de
   l'indigestion).

2. **Lisser les révisions Leitner** avec une tolérance de ±1 jour : si le
   jour-cible d'une révision est saturé en étude, on autorise un décalage
   d'un jour avant ou après. Jamais sur un nouveau chapitre.

3. **Calculer le quota d'étude par jour** à partir du profil et du
   check-in biomécanique du jour (modulation -30 % si fatigue ou charge
   mentale > 7/10).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from database.models import Chapitre, Cours, Semaine

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
JOURS: list[str] = [
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
]

# Durée présumée d'une révision Leitner (minutes). Constante pédagogique.
DUREE_REVISION_MIN: int = 30

# Coefficient multiplicateur pour transformer ``duree_max_session_min`` du
# profil en quota d'étude journalier. 3 sessions ≈ une demi-journée d'étude.
SESSIONS_PAR_JOUR: int = 3

# Réduction appliquée au quota quand le check-in biomécanique signale une
# fatigue physique ou une charge mentale > 7/10.
COEF_REDUCTION_QUOTA_FATIGUE: float = 0.70

# Seuil d'alerte sur le check-in (≥ ce score → on considère l'étudiant chargé).
SEUIL_FATIGUE: int = 7


# ---------------------------------------------------------------------------
# Quota d'étude par jour
# ---------------------------------------------------------------------------
def calculer_quota_etude_minutes(
    profil: Any,
    checkin: dict[str, Any] | None,
) -> int:
    """Quota d'étude max autorisé sur une journée, en minutes.

    Combine la durée max d'une session du profil (b) avec une modulation
    par le check-in biomécanique (c). Si le profil ne renseigne rien, on
    prend 50 min × 3 sessions = 150 min comme base raisonnable.
    """
    duree_session = int(getattr(profil, "duree_max_session_min", None) or 50)
    base = duree_session * SESSIONS_PAR_JOUR

    if not checkin:
        return base

    fatigue = int(checkin.get("fatigue_physique", 0) or 0)
    mental = int(checkin.get("charge_mentale", 0) or 0)
    if fatigue > SEUIL_FATIGUE or mental > SEUIL_FATIGUE:
        return int(base * COEF_REDUCTION_QUOTA_FATIGUE)
    return base


# ---------------------------------------------------------------------------
# Répartition des nouveaux chapitres
# ---------------------------------------------------------------------------
def repartir_nouveaux_chapitres(
    session: Session,
    cours_selectionnes: list[dict[str, Any]] | None,
    semaine: Semaine,
) -> dict[str, list[dict[str, Any]]]:
    """Répartit les chapitres « nouveaux » (niveau Leitner = 0) sur la
    semaine, en garantissant qu'il n'y aura **jamais plus d'un nouveau
    chapitre de la même matière le même jour**.

    Stratégie : pour chaque matière on déroule ses chapitres en
    avançant d'un jour à chaque chapitre. Le point de départ est décalé
    matière par matière pour éviter que toutes les matières démarrent
    le lundi.

    Returns:
        ``{"lundi": [<chapitre_dict>, ...], "mardi": [...], ...}``
        où ``<chapitre_dict>`` contient
        ``{chapitre_id, cours_id, matiere, titre, numero, temps_estime_min}``.
    """
    repartition: dict[str, list[dict[str, Any]]] = {j: [] for j in JOURS}

    if not cours_selectionnes:
        return repartition

    # Regrouper les chapitres nouveaux par matière (clé d'agrégation).
    par_matiere: dict[str, list[dict[str, Any]]] = {}
    for c_sel in cours_selectionnes:
        ch_ids = c_sel.get("chapitre_ids") or []
        if not ch_ids:
            continue

        chapitres_db = (
            session.query(Chapitre)
            .filter(Chapitre.id.in_(ch_ids))
            .all()
        )

        for ch in chapitres_db:
            if (ch.niveau_actuel or 0) != 0:
                continue  # déjà vu — c'est une révision, pas un nouveau chap.
            
            if ch.matiere_obj:
                matiere_nom = ch.matiere_obj.nom
            elif ch.cours:
                matiere_nom = ch.cours.matiere or ch.cours.nom or "Sans matière"
            else:
                matiere_nom = "Sans matière"

            par_matiere.setdefault(matiere_nom, []).append({
                "chapitre_id": ch.id,
                "cours_id": ch.cours_id,
                "matiere_id": ch.matiere_id,
                "matiere": matiere_nom,
                "titre": ch.titre,
                "numero": ch.numero,
                "temps_estime_min": int(round((ch.temps_estime_h or 1.0) * 60)),
            })

    # Distribution sur les 7 jours avec décalage par matière.
    for m_idx, (matiere, chaps) in enumerate(sorted(par_matiere.items())):
        for c_idx, chap_info in enumerate(chaps):
            jour_idx = (m_idx + c_idx) % 7
            repartition[JOURS[jour_idx]].append(chap_info)

    return repartition


# ---------------------------------------------------------------------------
# Lissage des révisions Leitner
# ---------------------------------------------------------------------------
def _date_iso_to_jour_idx(date_iso: str | None, semaine: Semaine) -> int:
    """Convertit une date ISO (YYYY-MM-DD) en index 0-6 dans la semaine.

    Clamp en dehors des bornes de la semaine : avant lundi → 0, après
    dimanche → 6. Une valeur invalide retombe sur 0 (lundi).
    """
    if not date_iso:
        return 0
    try:
        d = date.fromisoformat(date_iso)
    except (TypeError, ValueError):
        return 0
    delta = (d - semaine.date_debut).days
    if delta < 0:
        return 0
    if delta > 6:
        return 6
    return delta


def lisser_revisions_leitner(
    chapitres_dus: list[dict[str, Any]],
    semaine: Semaine,
    quota_par_jour_min: int,
    charges_etude_existantes: dict[str, int],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """Répartit les révisions Leitner sur la semaine avec tolérance ±1 jour.

    Args:
        chapitres_dus: liste fournie par
            ``ai_planner._get_chapitres_dus_pour_semaine`` (dicts contenant
            au moins ``chapitre_id``, ``matiere``, ``titre``, ``date_due``).
        semaine: pour convertir ``date_due`` en index de jour.
        quota_par_jour_min: charge d'étude max autorisée par jour.
        charges_etude_existantes: minutes d'étude déjà allouées par jour
            via les nouveaux chapitres (clé = nom du jour en français).

    Returns:
        ``(repartition, charges_finales)`` où :
          - ``repartition`` : ``{jour: [revision, ...]}``,
          - ``charges_finales`` : ``{jour: total_minutes_etude}`` après ajout
            des révisions.

    Chaque revision porte :
        ``{chapitre_id, matiere, titre, duree_estimee_min, jour_initial_cible,
           decale (bool), surcharge (bool)}``.
    """
    repartition: dict[str, list[dict[str, Any]]] = {j: [] for j in JOURS}
    charges: dict[str, int] = {j: int(charges_etude_existantes.get(j, 0)) for j in JOURS}

    for chap in chapitres_dus or []:
        date_due = chap.get("date_due")
        jour_cible_idx = _date_iso_to_jour_idx(date_due, semaine)
        jour_cible = JOURS[jour_cible_idx]

        # Ordre de préférence : jour cible, puis +1, puis -1.
        candidats: list[int] = [jour_cible_idx]
        if jour_cible_idx < 6:
            candidats.append(jour_cible_idx + 1)
        if jour_cible_idx > 0:
            candidats.append(jour_cible_idx - 1)

        revision: dict[str, Any] = {
            "chapitre_id": chap.get("chapitre_id"),
            "matiere": chap.get("matiere") or chap.get("cours"),
            "titre": chap.get("titre"),
            "duree_estimee_min": DUREE_REVISION_MIN,
            "jour_initial_cible": jour_cible,
        }

        place = False
        for j_idx in candidats:
            jour = JOURS[j_idx]
            if charges[jour] + DUREE_REVISION_MIN <= quota_par_jour_min:
                revision["decale"] = (j_idx != jour_cible_idx)
                revision["surcharge"] = False
                repartition[jour].append(revision)
                charges[jour] += DUREE_REVISION_MIN
                place = True
                break

        if not place:
            # Plus de place nulle part dans la fenêtre ±1 jour : on laisse
            # au jour cible et on marque la surcharge pour que l'IA en soit
            # informée et puisse écarter des tâches non-prioritaires.
            revision["decale"] = False
            revision["surcharge"] = True
            repartition[jour_cible].append(revision)
            charges[jour_cible] += DUREE_REVISION_MIN

    return repartition, charges


__all__ = [
    "JOURS",
    "DUREE_REVISION_MIN",
    "SESSIONS_PAR_JOUR",
    "calculer_quota_etude_minutes",
    "repartir_nouveaux_chapitres",
    "lisser_revisions_leitner",
]
