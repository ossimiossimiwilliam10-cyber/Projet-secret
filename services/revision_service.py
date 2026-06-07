"""Service de **révision espacée** (algo Leitner) + outils IA pour la salle d'étude.

Cette intégration porte les fonctionnalités de l'ancienne app desktop
(``app_revisions.py``) dans le pipeline Streamlit :

- Système Leitner à 14 niveaux : chaque chapitre a un ``niveau_actuel`` (0–13)
  et une ``date_prochaine`` de révision. Après un quiz, ces deux valeurs sont
  recalculées selon le score.
- Génération du planning :
  - **Fiche structurée** (idée centrale, notions, formules, pièges, mini-quiz, mnémo)
  - **QCM** à 4 choix (avec explications)
  - **Quiz ouvert** (questions textuelles, réponses libres évaluées par Gemini)
- Cache du texte extrait du PDF par chapitre — évite de payer le LLM ET de
  réextraire le PDF à chaque ouverture.

Toutes les fonctions ont une signature ``(session, chapitre_id, …)`` et opèrent
in-place sur l'objet ``Chapitre``. Le commit reste à la charge de l'appelant
(typiquement via ``session_scope()``).
"""

from __future__ import annotations

import json
import re
import datetime
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.db import BASE_DIR
from database.models import Chapitre, Utilisateur



# ===========================================================================
# 1. Constantes — algo Leitner
# ===========================================================================
# Intervalles de révision en jours, indexés par le niveau de maîtrise (0 → 13).
# Niveau 0 = vu pour la première fois → revoir dans 1 jour.
# Niveau 13 = chapitre maîtrisé long-terme → revoir dans 6 ans (sécurité).
INTERVALLES_J: list[int] = [
    1, 3, 5, 7, 14, 30, 60, 90, 180, 365, 730, 1095, 1460, 1825, 2190,
]
MAX_NIVEAU: int = len(INTERVALLES_J) - 1  # = 14

# Limite de caractères stockés dans ``Chapitre.texte_cache``. Au-delà, on coupe
# (Gemini reste très efficace même sur un sous-extrait dense). ~20 k tokens.
MAX_TEXTE_CACHE_CHARS: int = 80_000


# ===========================================================================
# 2. Algo Leitner — application du résultat d'un quiz / QCM
# ===========================================================================
def appliquer_resultat_quiz(
    session: Session,
    chapitre_id: int,
    score_num: float,
    mode: str = "qcm",
) -> dict[str, Any]:
    """Applique l'algo Leitner après un quiz / QCM.

    Règles (identiques à l'ancienne app desktop) :

    - **score ≥ 0.9** 🏆 → niveau **+= 1**, prochaine = today + ``INTERVALLES_J[nouveau]``
    - **0.5 ≤ score < 0.9** ⚡ → niveau **stagne**, prochaine = today + ``INTERVALLES_J[niveau] / 2``
    - **score < 0.5** 📚 → niveau **-= 1**, prochaine = today + ``INTERVALLES_J[nouveau]``

    Args:
        session: session SQLAlchemy active (commit à la charge de l'appelant).
        chapitre_id: id du chapitre concerné.
        score_num: score normalisé entre 0.0 et 1.0.
        mode: ``"qcm"`` ou ``"quiz"`` — purement informatif (stocké dans
            l'historique pour les stats).

    Returns:
        Un dict avec les infos utiles pour l'UI :
        ``{niveau_avant, niveau_apres, delta, date_prochaine, jours_avant_revision,
            label, color, reussi, score_num}``.

    Raises:
        ValueError: si le chapitre n'existe pas.
    """
    chap = session.get(Chapitre, chapitre_id)
    if chap is None:
        raise ValueError(f"Chapitre #{chapitre_id} introuvable.")

    niveau_avant = int(chap.niveau_actuel or 0)
    score_num = float(score_num)
    # Auto-normalisation : si > 1.0, on suppose un pourcentage 0-100
    if score_num > 1.0:
        score_num = score_num / 100.0
    score_num = max(0.0, min(1.0, score_num))
    today = date.today()

    # --- FSRS-lite : Calcul du retard réel ---
    jours_ecoules = 0
    if chap.historique_quiz and len(chap.historique_quiz) > 0:
        dernier_quiz = chap.historique_quiz[-1]
        if "date" in dernier_quiz:
            try:
                jours_ecoules = (today - date.fromisoformat(dernier_quiz["date"])).days
            except ValueError:
                pass
                
    intervalle_cible = INTERVALLES_J[min(niveau_avant, MAX_NIVEAU)]
    if jours_ecoules == 0:
        jours_ecoules = intervalle_cible
        
    ratio_attente = jours_ecoules / max(1, intervalle_cible)

    if score_num >= 0.9:
        # 🏆 Excellent
        # Bonus si rappel réussi malgré un grand retard (FSRS-lite)
        bonus = 1
        if ratio_attente >= 3.0:
            bonus = 3
        elif ratio_attente >= 2.0:
            bonus = 2
            
        niveau_apres = min(niveau_avant + bonus, MAX_NIVEAU)
        prochaine = today + timedelta(days=INTERVALLES_J[niveau_apres])
        label = f"🏆 Excellent (+{bonus})" if bonus > 1 else "🏆 Excellent"
        color = "green"
        reussi = True
    elif score_num >= 0.5:
        # ⚡ Correct — niveau stagne, on revient à mi-chemin
        niveau_apres = niveau_avant
        demi = max(intervalle_cible // 2, 1)
        prochaine = today + timedelta(days=demi)
        label = "⚡ Correct"
        color = "orange"
        reussi = True
    else:
        # 📚 À retravailler
        # Malus accru si oubli très rapide (ratio < 0.5)
        malus = 2 if ratio_attente < 0.5 and niveau_avant > 1 else 1
        niveau_apres = max(niveau_avant - malus, 0)
        prochaine = today + timedelta(days=INTERVALLES_J[niveau_apres])
        label = "📚 À retravailler"
        color = "red"
        reussi = False

    # Mise à jour du chapitre
    chap.niveau_actuel = niveau_apres
    chap.date_prochaine = prochaine

    # Bump du maitrise_pct existant (cohérence avec l'UI Bibliothèque)
    # On dérive un % indicatif à partir du niveau Leitner.
    chap.maitrise_pct = float(round(niveau_apres / MAX_NIVEAU * 100, 1))

    # Historique (capé à 50 entrées pour ne pas exploser)
    historique = list(chap.historique_quiz or [])
    historique.append({
        "date": date.today().isoformat(),
        "mode": mode,
        "score": round(score_num, 3),
        "reussi": reussi,
        "niveau_avant": niveau_avant,
        "niveau_apres": niveau_apres,
    })
    chap.historique_quiz = historique[-50:]

    jours_avant = (prochaine - today).days
    return {
        "niveau_avant": niveau_avant,
        "niveau_apres": niveau_apres,
        "delta": niveau_apres - niveau_avant,
        "date_prochaine": prochaine,
        "jours_avant_revision": jours_avant,
        "label": label,
        "color": color,
        "reussi": reussi,
        "score_num": score_num,
    }


# ===========================================================================
# 3. Status helpers — pour l'UI
# ===========================================================================
def label_couleur_status(chap: Chapitre, today: date | None = None) -> tuple[str, str]:
    """Renvoie ``(emoji_label, color)`` selon l'urgence de révision.

    Codes :
      - 🏆 violet : chapitre maîtrisé long terme (niveau 13)
      - 🔥 rouge : révision en retard (date_prochaine ≤ today)
      - ⏰ orange : révision sous 3 jours
      - ✅ vert : révision dans plus de 3 jours
      - 📅 gris : pas de date prévue (chapitre jamais initialisé)
    """
    if today is None:
        today = date.today()

    if chap.date_prochaine is None:
        return "📅 Pas de plan", "gray"

    if (chap.niveau_actuel or 0) >= MAX_NIVEAU:
        return "🏆 Maîtrisé", "violet"

    delta = (chap.date_prochaine - today).days
    if delta <= 0:
        return f"🔥 À réviser ({-delta}j de retard)" if delta < 0 else "🔥 À réviser aujourd'hui", "red"
    if delta <= 3:
        return f"⏰ Dans {delta}j", "orange"
    return f"✅ Dans {delta}j", "green"


def chapitres_a_reviser(
    session: Session,
    date_max: date | None = None,
    inclure_jamais_revises: bool = False,
) -> list[Chapitre]:
    """Liste les chapitres dont la révision est due.

    Args:
        date_max: borne supérieure de date_prochaine. Si None, utilise today.
        inclure_jamais_revises: si True, inclut les chapitres avec date_prochaine NULL.

    Returns:
        Liste de Chapitre triée par date_prochaine ascendante.
    """
    if date_max is None:
        date_max = date.today()

    from sqlalchemy.orm import selectinload

    # Eager-load matiere_obj : les callers (ai_planner, UI revisions) lisent
    # systématiquement `chap.matiere_obj.nom` dans la boucle → sans cet
    # `selectinload`, on payerait 1 requête par chapitre dû.
    query = (
        session.query(Chapitre)
        .filter(Chapitre.trashed.isnot(True))
        .options(selectinload(Chapitre.matiere_obj))
    )
    if inclure_jamais_revises:
        query = query.filter(
            (Chapitre.date_prochaine.is_(None))
            | (Chapitre.date_prochaine <= date_max)
        )
    else:
        query = query.filter(
            Chapitre.date_prochaine.isnot(None),
            Chapitre.date_prochaine <= date_max,
        )
    return query.order_by(Chapitre.date_prochaine.asc().nullsfirst()).all()


# ===========================================================================
# 4. Initialisation — pour les chapitres fraîchement créés
# ===========================================================================
def initialiser_chapitre_pour_revision(
    session: Session,
    chapitre_id: int,
    delai_initial_jours: int | None = None,
    niveau_initial: int | None = None,
) -> bool:
    """Pose une date_prochaine sur UN chapitre s'il n'en a pas encore.

    Utilisée par ``modules/bibliotheque._process_import_unifie`` après la
    création d'un chapitre via ``apply_analysis_to_matiere``.

    Args:
        delai_initial_jours: délai avant la première révision (en jours).
            Si None, utilise INTERVALLES_J[niveau_initial] ou INTERVALLES_J[0].
        niveau_initial: niveau Leitner de départ (0 par défaut). Permet
            de ne pas repartir de zéro pour des chapitres déjà révisés.

    Returns:
        ``True`` si le chapitre a reçu une date_prochaine, ``False`` s'il
        en avait déjà une (ou s'il n'existe pas).
    """
    if niveau_initial is not None:
        niveau_initial = max(0, min(niveau_initial, MAX_NIVEAU))
    else:
        niveau_initial = 0

    if delai_initial_jours is None:
        delai_initial_jours = INTERVALLES_J[niveau_initial]

    chap = session.get(Chapitre, chapitre_id)
    if chap is None or chap.date_prochaine is not None:
        return False

    chap.niveau_actuel = niveau_initial
    chap.date_prochaine = date.today() + timedelta(days=delai_initial_jours)
    return True


# ===========================================================================
# 5. Gestion de la corbeille (Trash/Restore)
# ===========================================================================
def trash_chapitre(session: Session, chapitre_id: int) -> bool:
    """Met un chapitre à la corbeille.
    Returns True si mis à la corbeille, False sinon.
    """
    chap = session.get(Chapitre, chapitre_id)
    if chap and not getattr(chap, "trashed", False):
        chap.trashed = True
        return True
    return False


def restore_chapitre(session: Session, chapitre_id: int) -> bool:
    """Restaure un chapitre de la corbeille.
    Returns True si restauré, False sinon.
    """
    chap = session.get(Chapitre, chapitre_id)
    if chap and getattr(chap, "trashed", False):
        chap.trashed = False
        return True
    return False


# ===========================================================================
# 4 bis. Vue de supervision globale (méthode des J)
# ===========================================================================
def repartition_par_niveau(session: Session) -> dict[int, int]:
    """Compte le nombre de chapitres dans chaque niveau Leitner.

    Inclut tous les chapitres actifs (avec ou sans ``date_prochaine``).
    Le niveau 0 contient les « jamais commencés » qui ont été initialisés
    mais n'ont pas encore eu de quiz réussi.

    Returns:
        Dict ``{niveau: nb_chapitres}`` couvrant les niveaux présents.
    """
    rows = (
        session.query(Chapitre.niveau_actuel, func.count(Chapitre.id))
        .group_by(Chapitre.niveau_actuel)
        .all()
    )
    return {int(niveau or 0): int(nb) for niveau, nb in rows}


def chapitres_par_jour_futur(
    session: Session,
    days_ahead: int = 28,
    today: date | None = None,
    matiere_ids: list[int] | None = None,
) -> dict[date, list[Chapitre]]:
    """Pour chaque jour des ``days_ahead`` prochains jours (à partir
    d'aujourd'hui inclus), liste les chapitres dont ``date_prochaine``
    tombe ce jour-là.

    Les chapitres en retard (``date_prochaine < today``) sont rapatriés
    sur la clé ``today`` pour qu'ils apparaissent à traiter en priorité.

    Args:
        days_ahead: nombre de jours à projeter (28 = 4 semaines).
        today: date de référence (par défaut ``date.today()``).
        matiere_ids: filtrer par matière. Si None, toutes.

    Returns:
        Dict ``{date: [chapitres]}`` ordonné par date croissante.
    """
    today = today or date.today()
    date_max = today + timedelta(days=days_ahead - 1)

    query = session.query(Chapitre).filter(Chapitre.date_prochaine.isnot(None))
    if matiere_ids is not None:
        if not matiere_ids:
            return {}
        query = query.filter(Chapitre.matiere_id.in_(matiere_ids))

    # Le filtre supérieur inclut tous les retards (date < today).
    query = query.filter(Chapitre.date_prochaine <= date_max)
    chapitres = query.all()

    result: dict[date, list[Chapitre]] = {}
    for chap in chapitres:
        d = chap.date_prochaine
        # Les retards atterrissent sur today (à traiter en priorité).
        if d < today:
            d = today
        result.setdefault(d, []).append(chap)
    # Tri par date croissante (Python 3.7+ : dict préserve l'ordre).
    return dict(sorted(result.items()))


def dette_revision(
    session: Session,
    today: date | None = None,
    matiere_ids: list[int] | None = None,
) -> list[Chapitre]:
    """Chapitres dont la date de révision est STRICTEMENT passée (vrai
    retard). Triés par ancienneté du retard (plus en retard d'abord).
    """
    today = today or date.today()
    query = session.query(Chapitre).filter(
        Chapitre.date_prochaine.isnot(None),
        Chapitre.date_prochaine < today,
    )
    if matiere_ids is not None:
        if not matiere_ids:
            return []
        query = query.filter(Chapitre.matiere_id.in_(matiere_ids))
    return query.order_by(Chapitre.date_prochaine.asc()).all()


def chapitres_jamais_initialises(
    session: Session,
    matiere_ids: list[int] | None = None,
) -> list[Chapitre]:
    """Chapitres existants en base mais sans date_prochaine — la file de
    révision n'a pas démarré pour eux."""
    query = session.query(Chapitre).filter(Chapitre.date_prochaine.is_(None))
    if matiere_ids is not None:
        if not matiere_ids:
            return []
        query = query.filter(Chapitre.matiere_id.in_(matiere_ids))
    return query.order_by(Chapitre.matiere_id, Chapitre.numero).all()


# ===========================================================================
# 4 ter. Projection long terme (anticipation été / vacances)
# ===========================================================================
# Durée présumée d'une révision Leitner (en minutes), partagée avec
# scheduler_engine. Sert au calcul de la charge horaire projetée.
DUREE_REVISION_MIN: int = 30


def projeter_chapitre(
    niveau_actuel: int,
    date_prochaine: date,
    horizon: date,
) -> list[tuple[date, int]]:
    """Projette toutes les futures dates de révision d'un chapitre.

    Trajectoire **optimiste** : suppose que l'étudiant réussit chaque
    quiz et monte donc d'un niveau à chaque révision. C'est la
    projection théorique « si tout se passe bien ».

    À partir de l'état actuel (``niveau_actuel``, ``date_prochaine``),
    on déroule la séquence en utilisant ``INTERVALLES_J`` :

      - Au moment du quiz courant (date = ``date_prochaine``), niveau
        passe à ``niveau_actuel + 1``.
      - Prochaine date = date courante + ``INTERVALLES_J[nouveau_niveau]``.
      - On répète tant que la date ne dépasse pas ``horizon`` et que
        le niveau n'a pas atteint ``MAX_NIVEAU``.

    Args:
        niveau_actuel: niveau Leitner actuel du chapitre (0-13).
        date_prochaine: première date future de révision en DB.
        horizon: dernière date à considérer (inclus).

    Returns:
        Liste ``[(date, niveau_apres_quiz), ...]`` triée par date
        croissante. Vide si ``date_prochaine > horizon``.
    """
    projections: list[tuple[date, int]] = []
    courante = date_prochaine
    niveau = int(niveau_actuel or 0)

    while courante <= horizon:
        niveau_apres = min(niveau + 1, MAX_NIVEAU)
        projections.append((courante, niveau_apres))
        if niveau_apres >= MAX_NIVEAU:
            break  # plus de quiz nécessaires
        courante = courante + timedelta(days=INTERVALLES_J[niveau_apres])
        niveau = niveau_apres

    return projections


def projeter_tous_chapitres(
    session: Session,
    horizon_jours: int,
    today: date | None = None,
    matiere_ids: list[int] | None = None,
) -> dict[date, list[dict[str, Any]]]:
    """Pour chaque jour des ``horizon_jours`` à venir, liste les
    révisions projetées (théoriques) de tous les chapitres actifs.

    Combine la date actuelle déjà en DB + toutes les dates futures
    calculées par :func:`projeter_chapitre`.

    Chaque entrée du dict est un ``{chapitre_id, matiere_id, matiere,
    titre, niveau_avant, niveau_apres, niveau_actuel_db, duree_min}``
    pour permettre la détection de conflits côté UI.

    Args:
        horizon_jours: profondeur de la projection (en jours).
        today: référence (par défaut ``date.today()``).
        matiere_ids: filtre matière. Si None, toutes.

    Returns:
        Dict trié par date croissante.
    """
    today = today or date.today()
    horizon = today + timedelta(days=horizon_jours)

    # Eager-load la matière de chaque chapitre : sinon `chap.matiere_obj.nom`
    # dans la boucle ci-dessous générerait 1 requête par chapitre (N+1).
    from sqlalchemy.orm import selectinload

    query = (
        session.query(Chapitre)
        .options(selectinload(Chapitre.matiere_obj))
        .filter(Chapitre.date_prochaine.isnot(None))
    )
    if matiere_ids is not None:
        if not matiere_ids:
            return {}
        query = query.filter(Chapitre.matiere_id.in_(matiere_ids))
    chapitres = query.all()

    result: dict[date, list[dict[str, Any]]] = {}
    for chap in chapitres:
        date_depart = chap.date_prochaine
        if date_depart is None:
            continue

        # Rapatriement des retards sur today.
        if date_depart < today:
            date_depart = today

        projections = projeter_chapitre(
            int(chap.niveau_actuel or 0),
            date_depart,
            horizon,
        )

        matiere_nom = chap.matiere_obj.nom if chap.matiere_obj else "Sans matière"

        for date_proj, niveau_apres in projections:
            niveau_avant = max(0, niveau_apres - 1)
            result.setdefault(date_proj, []).append({
                "chapitre_id": chap.id,
                "matiere_id": chap.matiere_id,
                "matiere": matiere_nom,
                "titre": chap.titre,
                "niveau_avant": niveau_avant,
                "niveau_apres": niveau_apres,
                "niveau_actuel_db": int(chap.niveau_actuel or 0),
                "duree_min": DUREE_REVISION_MIN,
            })

    return dict(sorted(result.items()))


def detecter_conflits_jour(
    projections_jour: list[dict[str, Any]],
    plafond_minutes: int,
) -> dict[str, Any]:
    """Détecte les conflits sur un jour donné de la projection.

    Deux règles métier vérifiées :
      1. **Max 1 nouveau chapitre par matière par jour** — un chapitre
         est considéré « nouveau » si la projection le fait passer du
         niveau 0 au niveau 1 (toute première révision, J1).
      2. **Plafond horaire journalier** — somme des ``duree_min`` de
         toutes les révisions projetées vs ``plafond_minutes``.

    Returns:
        ``{
            "nb_total": int,
            "charge_min": int,
            "charge_ok": bool,
            "matieres_doublons_nouveaux": list[str],
        }``
    """
    nb_total = len(projections_jour)
    charge_min = sum(int(p.get("duree_min") or 0) for p in projections_jour)
    charge_ok = plafond_minutes <= 0 or charge_min <= plafond_minutes

    # Doublons sur les "nouveaux" (niveau_avant == 0).
    par_matiere_nouveaux: dict[str, int] = {}
    for p in projections_jour:
        if p["niveau_avant"] == 0:
            par_matiere_nouveaux[p["matiere"]] = par_matiere_nouveaux.get(p["matiere"], 0) + 1
    matieres_doublons_nouveaux = [m for m, n in par_matiere_nouveaux.items() if n > 1]

    return {
        "nb_total": nb_total,
        "charge_min": charge_min,
        "charge_ok": charge_ok,
        "matieres_doublons_nouveaux": matieres_doublons_nouveaux,
    }


def lisser_automatiquement_dates_leitner(
    session: Session,
    tolerance_jours: int = 4,
    plafond_minutes_par_jour: int | None = None,
    today: date | None = None,
    matiere_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Décale automatiquement les ``date_prochaine`` des chapitres pour
    résoudre deux types de conflits, avec une tolérance bornée.

    Règle 1 — **Max 1 nouveau chapitre par matière par jour** :
        Pour les chapitres de niveau Leitner 0 (premier passage), on
        garde le premier de chaque matière à sa date d'origine et on
        décale les suivants vers les jours libres dans
        ``[date, date + tolerance_jours]``.

    Règle 2 — **Plafond horaire journalier** (si fourni) :
        Si la charge cumulée d'un jour dépasse ``plafond_minutes_par_jour``,
        on déplace les chapitres « les moins urgents » (niveau Leitner
        le plus élevé) vers les jours suivants disponibles dans la
        tolérance.

    Si aucun jour libre n'est trouvé dans la fenêtre de tolérance pour
    un chapitre, on **laisse sa date d'origine intacte** et on le
    mentionne dans ``non_decalables`` du compte-rendu.

    Args:
        tolerance_jours: nb max de jours qu'on s'autorise à reporter
            une révision (par défaut 3 — l'algo Leitner reste valide).
        plafond_minutes_par_jour: si None, seule la règle 1 s'applique.
        today: date de référence (par défaut ``date.today()``).
        matiere_ids: filtre matière.

    Returns:
        ``{"nb_decalages": int, "decalages": [...], "nb_non_decalables": int,
        "non_decalables": [...]}`` — décrit ce qui a été modifié.
    """
    today = today or date.today()
    horizon = today + timedelta(days=tolerance_jours * 4 + 30)

    query = session.query(Chapitre).filter(
        Chapitre.date_prochaine.isnot(None),
        Chapitre.date_prochaine >= today,
        Chapitre.date_prochaine <= horizon,
    )
    if matiere_ids is not None:
        if not matiere_ids:
            return {"nb_decalages": 0, "decalages": [],
                    "nb_non_decalables": 0, "non_decalables": []}
        query = query.filter(Chapitre.matiere_id.in_(matiere_ids))
    chapitres = query.all()

    decalages: list[dict[str, Any]] = []
    non_decalables: list[dict[str, Any]] = []

    # --- Règle 1 : 1 nouveau par matière par jour ---------------------
    # Index « occupé » par (matiere_id, date) → True si déjà un nouveau ce jour-là.
    occupes_nouveaux: set[tuple[int, date]] = set()
    chapitres_tries = sorted(chapitres, key=lambda c: (c.date_prochaine, c.id))

    for ch in chapitres_tries:
        if (ch.niveau_actuel or 0) != 0 or ch.matiere_id is None:
            continue
        date_origine = ch.date_prochaine
        key = (ch.matiere_id, date_origine)

        if key not in occupes_nouveaux:
            occupes_nouveaux.add(key)
            continue

        # Conflit : chercher un jour libre dans la tolérance.
        place = False
        for offset in range(1, tolerance_jours + 1):
            nouveau_jour = date_origine + timedelta(days=offset)
            nk = (ch.matiere_id, nouveau_jour)
            if nk not in occupes_nouveaux:
                ch.date_prochaine = nouveau_jour
                occupes_nouveaux.add(nk)
                decalages.append({
                    "chapitre_id": ch.id,
                    "titre": ch.titre,
                    "matiere": ch.matiere_obj.nom if ch.matiere_obj else "?",
                    "date_origine": date_origine.isoformat(),
                    "nouvelle_date": nouveau_jour.isoformat(),
                    "raison": "doublon matière (règle 1 nouveau / matière / jour)",
                })
                place = True
                break

        if not place:
            non_decalables.append({
                "chapitre_id": ch.id,
                "titre": ch.titre,
                "matiere": ch.matiere_obj.nom if ch.matiere_obj else "?",
                "date_origine": date_origine.isoformat(),
                "raison": "doublon matière, aucun jour libre dans la tolérance",
            })

    # --- Règle 2 : plafond horaire par jour ---------------------------
    if plafond_minutes_par_jour and plafond_minutes_par_jour > 0:
        # Recompte les chapitres par jour APRÈS les décalages règle 1.
        charge_par_jour: dict[date, list[Chapitre]] = {}
        for ch in chapitres:
            charge_par_jour.setdefault(ch.date_prochaine, []).append(ch)

        for jour, chaps_du_jour in list(charge_par_jour.items()):
            charge = len(chaps_du_jour) * DUREE_REVISION_MIN
            if charge <= plafond_minutes_par_jour:
                continue

            nb_a_decaler = (charge - plafond_minutes_par_jour + DUREE_REVISION_MIN - 1) // DUREE_REVISION_MIN
            # On déplace en priorité les chapitres au niveau Leitner le PLUS
            # élevé (déjà bien ancrés, peuvent se permettre d'attendre).
            candidats = sorted(chaps_du_jour, key=lambda c: -(c.niveau_actuel or 0))

            for ch in candidats[:nb_a_decaler]:
                date_origine = ch.date_prochaine
                place = False
                for offset in range(1, tolerance_jours + 1):
                    nouveau_jour = date_origine + timedelta(days=offset)
                    deja = len(charge_par_jour.get(nouveau_jour, []))
                    if (deja + 1) * DUREE_REVISION_MIN <= plafond_minutes_par_jour:
                        ch.date_prochaine = nouveau_jour
                        charge_par_jour.setdefault(nouveau_jour, []).append(ch)
                        charge_par_jour[jour].remove(ch)
                        decalages.append({
                            "chapitre_id": ch.id,
                            "titre": ch.titre,
                            "matiere": ch.matiere_obj.nom if ch.matiere_obj else "?",
                            "date_origine": date_origine.isoformat(),
                            "nouvelle_date": nouveau_jour.isoformat(),
                            "raison": f"plafond {plafond_minutes_par_jour} min/jour dépassé",
                        })
                        place = True
                        break

                if not place:
                    non_decalables.append({
                        "chapitre_id": ch.id,
                        "titre": ch.titre,
                        "matiere": ch.matiere_obj.nom if ch.matiere_obj else "?",
                        "date_origine": date_origine.isoformat(),
                        "raison": "plafond dépassé, aucun jour libre dans la tolérance",
                    })

    return {
        "nb_decalages": len(decalages),
        "decalages": decalages,
        "nb_non_decalables": len(non_decalables),
        "non_decalables": non_decalables,
    }
__all__ = [
    "INTERVALLES_J",
    "MAX_NIVEAU",
    "MAX_TEXTE_CACHE_CHARS",
    "appliquer_resultat_quiz",
    "label_couleur_status",
    "chapitres_a_reviser",
    "repartition_par_niveau",
    "chapitres_par_jour_futur",
    "dette_revision",
    "chapitres_jamais_initialises",
    "projeter_chapitre",
    "projeter_tous_chapitres",
    "detecter_conflits_jour",
    "DUREE_REVISION_MIN",
]
