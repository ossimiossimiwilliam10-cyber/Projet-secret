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

from database.models import Chapitre, Semaine

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
# Moteur Circadien
# ---------------------------------------------------------------------------
import math

def calculer_courbe_energie(
    heure_lever: date.time | None,
    heure_coucher: date.time | None,
    chronotype: str,
    checkin: dict[str, Any] | None,
) -> dict[str, list[str]]:
    """Génère une courbe d'énergie mathématique sur la journée.

    Définit les "Créneaux Haute Énergie" (pics de la courbe, > 0.8)
    et les "Créneaux Basse Énergie" (creux de la courbe, < 0.4).

    Args:
        heure_lever: Heure de lever de l'étudiant.
        heure_coucher: Heure de coucher de l'étudiant.
        chronotype: "matin", "soir", ou "intermediaire".
        checkin: Dictionnaire optionnel contenant "fatigue_physique".

    Returns:
        Dict avec deux clés : "haute_energie" et "basse_energie",
        contenant des listes de chaînes horaires (ex: "09:00-11:30").
    """
    from datetime import datetime, time, timedelta

    hl = heure_lever or time(7, 0)
    hc = heure_coucher or time(23, 0)
    
    # Conversion en minutes depuis minuit
    hl_min = hl.hour * 60 + hl.minute
    hc_min = hc.hour * 60 + hc.minute
    if hc_min <= hl_min:
        hc_min += 24 * 60 # Coucher le lendemain

    duree_journee_min = hc_min - hl_min
    if duree_journee_min <= 0:
        return {"haute_energie": [], "basse_energie": []}

    # Modulateur de Biorhythme selon le chronotype
    offset_pic_pct = 0.25 # Matin par défaut (quart de la journée)
    if chronotype == "couche_tard":
        offset_pic_pct = 0.65
    elif chronotype == "intermediaire":
        offset_pic_pct = 0.40

    pic_absolu_min = hl_min + int(duree_journee_min * offset_pic_pct)

    # Modulateur d'amplitude selon le checkin
    amplitude_max = 1.0
    if checkin:
        fatigue = float(checkin.get("fatigue_physique", 0) or 0)
        if fatigue > 7:
            amplitude_max = 0.7 # Écrase la courbe si épuisé

    # Calcul par segments de 30 minutes
    segments_haute = []
    segments_basse = []
    
    current_min = hl_min
    while current_min < hc_min:
        # Fonction normale (cloche) centrée sur le pic absolu
        # σ = largeur de la cloche (ex: 3 heures)
        sigma = 180.0
        exponent = -0.5 * ((current_min - pic_absolu_min) / sigma) ** 2
        score_energie = amplitude_max * math.exp(exponent)
        
        # Ajout d'une baisse "post-déjeuner" (coup de barre classique vers 14h/14h30)
        # On simule ça par un creux forcé autour de 14h (840 min)
        creux_dej = -0.3 * math.exp(-0.5 * ((current_min - 840) / 90.0) ** 2)
        score_energie += creux_dej
        
        score_energie = max(0.0, min(1.0, score_energie))
        
        h_str = f"{(current_min // 60) % 24:02d}:{(current_min % 60):02d}"
        
        if score_energie >= 0.75:
            segments_haute.append(h_str)
        elif score_energie <= 0.40:
            segments_basse.append(h_str)
            
        current_min += 30

    # Fonction utilitaire pour grouper les "09:00", "09:30", "10:00" en "09:00-10:30"
    def grouper_segments(segments):
        if not segments:
            return []
        groupes = []
        debut = segments[0]
        precedent = segments[0]
        
        for s in segments[1:]:
            # Vérifie si contigu (30 min d'écart)
            h_prev, m_prev = map(int, precedent.split(":"))
            h_curr, m_curr = map(int, s.split(":"))
            
            diff_min = (h_curr * 60 + m_curr) - (h_prev * 60 + m_prev)
            if diff_min < 0: diff_min += 24*60
            
            if diff_min == 30:
                precedent = s
            else:
                # Ferme le groupe
                h_fin, m_fin = map(int, precedent.split(":"))
                fin_min = h_fin * 60 + m_fin + 30
                fin_str = f"{(fin_min // 60) % 24:02d}:{(fin_min % 60):02d}"
                groupes.append(f"{debut}-{fin_str}")
                debut = s
                precedent = s
                
        # Dernier groupe
        h_fin, m_fin = map(int, precedent.split(":"))
        fin_min = h_fin * 60 + m_fin + 30
        fin_str = f"{(fin_min // 60) % 24:02d}:{(fin_min % 60):02d}"
        groupes.append(f"{debut}-{fin_str}")
        return groupes

    return {
        "haute_energie": grouper_segments(segments_haute),
        "basse_energie": grouper_segments(segments_basse)
    }

# ---------------------------------------------------------------------------
# Quota d'étude
# ---------------------------------------------------------------------------
def calculer_quota_etude_minutes(
    profil: Any,
    checkin: dict[str, Any] | None,
) -> int:
    """**Plafond** d'étude max autorisé sur UNE journée, en minutes.

    C'est la limite que l'IA ne doit pas dépasser un jour donné (pour
    respecter le rythme et éviter la sur-charge), peu importe l'objectif
    hebdomadaire.

    Source (ordre de priorité) :
      1. ``profil.heures_etude_plafond_par_jour`` × 60, si renseigné > 0.
      2. Fallback historique : ``duree_max_session_min`` × ``SESSIONS_PAR_JOUR``
         si l'étudiant n'a pas (encore) défini son plafond.
      3. Dernier recours : 50 min × 3 = 150 min.

    Modulation par le check-in biomécanique du jour : si fatigue physique
    ou charge mentale > ``SEUIL_FATIGUE`` (7/10), on applique
    ``COEF_REDUCTION_QUOTA_FATIGUE`` (-30 % par défaut).
    """
    plafond = float(getattr(profil, "heures_etude_plafond_par_jour", None) or 0)
    if plafond > 0:
        base = int(round(plafond * 60))
    else:
        duree_session = int(getattr(profil, "duree_max_session_min", None) or 50)
        base = duree_session * SESSIONS_PAR_JOUR

    if not checkin:
        return base

    fatigue = int(checkin.get("fatigue_physique", 0) or 0)
    mental = int(checkin.get("charge_mentale", 0) or 0)
    if fatigue > SEUIL_FATIGUE or mental > SEUIL_FATIGUE:
        return int(base * COEF_REDUCTION_QUOTA_FATIGUE)
    return base


def calculer_cible_hebdo_minutes(profil: Any) -> int:
    """Total d'heures d'étude visé sur la **semaine** (en minutes).

    C'est l'objectif que l'IA cherche à atteindre en répartissant les
    chapitres sur les 7 jours, sans dépasser le plafond journalier.

    Source (ordre de priorité) :
      1. ``profil.heures_etude_cible_par_semaine`` × 60 si renseigné > 0.
      2. Fallback : 7 × plafond journalier (cible = plafond plein × 7).
      3. Dernier recours : 21 h (≈ 3 h/jour) en minutes.

    Aucune modulation check-in ici : c'est un objectif de référence
    stable, pas un plafond instantané.
    """
    cible = float(getattr(profil, "heures_etude_cible_par_semaine", None) or 0)
    if cible > 0:
        return int(round(cible * 60))
    plafond_min = calculer_quota_etude_minutes(profil, checkin=None)
    if plafond_min > 0:
        return plafond_min * 7
    return 21 * 60


# ---------------------------------------------------------------------------
# Répartition des nouveaux chapitres
# ---------------------------------------------------------------------------
def repartir_nouveaux_chapitres(
    session: Session,
    matieres_selectionnees: list[dict[str, Any]] | None,
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
        ``{chapitre_id, matiere_id, matiere, titre, numero, temps_estime_min}``.
    """
    repartition: dict[str, list[dict[str, Any]]] = {j: [] for j in JOURS}

    if not matieres_selectionnees:
        return repartition

    # Regrouper les chapitres nouveaux par matière (clé d'agrégation).
    par_matiere: dict[str, list[dict[str, Any]]] = {}
    for c_sel in matieres_selectionnees:
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

            matiere_nom = ch.matiere_obj.nom if ch.matiere_obj else "Sans matière"
            par_matiere.setdefault(matiere_nom, []).append({
                "chapitre_id": ch.id,
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
    "calculer_cible_hebdo_minutes",
    "repartir_nouveaux_chapitres",
    "lisser_revisions_leitner",
    "calculer_courbe_energie",
    "calculer_velocite_historique",
    "VelociteResult",
]

# ---------------------------------------------------------------------------
# Vélocité historique (Anti-Burnout)
# ---------------------------------------------------------------------------
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class VelociteResult:
    """Résultat structuré du calcul de vélocité historique.

    Permet à l'appelant (UI ou IA) de distinguer une vélocité réellement
    fondée sur des données d'une valeur par défaut quand l'échantillon est
    trop faible.
    """
    multiplicateur: float
    message: str
    confiance: Literal["elevee", "faible", "aucune"]
    echantillon_taches: int
    temps_prevu_min: int
    temps_fait_min: int

    # --- Rétro-compatibilité : permet l'unpacking `mult, msg = result` ---
    def __iter__(self):  # pragma: no cover
        yield self.multiplicateur
        yield self.message


def calculer_velocite_historique(session: Session, utilisateur_id: int) -> VelociteResult:
    """Analyse les 3 dernières semaines échues pour calculer un multiplicateur de réalisme.

    Si l'étudiant prévoit systématiquement 20h mais n'en fait que 10h,
    la vélocité baissera, permettant au moteur de lisser ses objectifs.

    Retourne un :class:`VelociteResult` :
    - ``confiance="aucune"`` : pas d'historique → multiplicateur 1.0 (neutre)
    - ``confiance="faible"`` : <5 tâches → multiplicateur calculé mais à prendre avec recul
    - ``confiance="elevee"`` : ≥5 tâches → multiplicateur fiable
    """
    from datetime import date
    from database.models import Semaine, Tache

    today = date.today()

    # Les 3 dernières semaines terminées
    semaines_passees = (
        session.query(Semaine)
        .filter(Semaine.date_fin < today)
        .order_by(Semaine.date_fin.desc())
        .limit(3)
        .all()
    )

    if not semaines_passees:
        return VelociteResult(
            multiplicateur=1.0,
            message="Aucune semaine passée — la vélocité s'affichera dès la 2ᵉ semaine.",
            confiance="aucune",
            echantillon_taches=0,
            temps_prevu_min=0,
            temps_fait_min=0,
        )

    semaine_ids = [s.id for s in semaines_passees]

    taches_etude = (
        session.query(Tache)
        .filter(
            Tache.semaine_id.in_(semaine_ids),
            Tache.type == "etude",
            Tache.obligatoire.is_(False)
        )
        .all()
    )

    if not taches_etude:
        return VelociteResult(
            multiplicateur=1.0,
            message="Pas de tâches d'étude dans les semaines passées — vélocité neutre.",
            confiance="aucune",
            echantillon_taches=0,
            temps_prevu_min=0,
            temps_fait_min=0,
        )

    temps_prevu = 0
    temps_fait = 0

    for t in taches_etude:
        duree = t.duree_min or 0
        temps_prevu += duree
        if t.statut == "fait":
            temps_fait += duree
        elif t.statut == "partiellement":
            temps_fait += int(duree * 0.5)

    if temps_prevu == 0:
        return VelociteResult(
            multiplicateur=1.0,
            message="Aucun temps prévu dans les semaines passées — vélocité neutre.",
            confiance="aucune",
            echantillon_taches=len(taches_etude),
            temps_prevu_min=0,
            temps_fait_min=0,
        )

    ratio_brut = temps_fait / temps_prevu
    # Lissage pour éviter les punitions trop brutales : moyenne avec 1.0
    multiplicateur = (ratio_brut + 1.0) / 2.0
    # Plancher à 0.5 et plafond à 1.1 (léger bonus si très efficace)
    multiplicateur = max(0.5, min(1.1, multiplicateur))

    confiance: Literal["elevee", "faible", "aucune"]
    confiance = "elevee" if len(taches_etude) >= 5 else "faible"

    pct = int(multiplicateur * 100)
    real_pct = int(ratio_brut * 100)

    if confiance == "faible":
        msg = (
            f"Sur {len(taches_etude)} tâche(s) seulement, tu as complété {real_pct}% "
            f"du prévu. Estimation à confirmer (ajusté à {pct}%)."
        )
    else:
        msg = (
            f"Sur les 3 dernières semaines, tu as complété {real_pct}% du prévu "
            f"({len(taches_etude)} tâches analysées). Capacité ajustée à {pct}%."
        )

    return VelociteResult(
        multiplicateur=multiplicateur,
        message=msg,
        confiance=confiance,
        echantillon_taches=len(taches_etude),
        temps_prevu_min=temps_prevu,
        temps_fait_min=temps_fait,
    )
