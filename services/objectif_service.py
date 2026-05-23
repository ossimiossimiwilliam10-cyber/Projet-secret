"""Service de gestion des objectifs personnalisés — F3b.

Workflow :
1. L'étudiant définit un objectif (nom, cours visé, note cible, date cible, description)
2. Avant de le créer, on demande à Gemini de proposer une **stratégie** :
   pondérations par chapitre, heures à investir, conseils, niveau de réalisme.
3. L'étudiant peut **adopter** la stratégie (créer l'objectif + activer les
   pondérations) ou demander une autre proposition.
4. Tant que l'objectif est actif, les pondérations sont appliquées par
   l'ai_planner dans tous les futurs plannings hebdomadaires.

Le service est **stateless** au sens où il ne maintient pas d'état entre les
appels. Toutes les fonctions prennent une session SQLAlchemy.
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from database.models import Chapitre, Objectif, Utilisateur
from services.gemini_utils import gemini_call_with_retry
from services.profil_service import get_gemini_credentials


# ===========================================================================
# 1. Snapshot de l'état actuel pour le prompt Gemini
# ===========================================================================
def _build_etat_chapitres(session: Session, matiere_id: int | None) -> list[dict[str, Any]]:
    """Construit la liste des chapitres pertinents pour l'objectif.

    Si matiere_id est défini → chapitres de cette matière uniquement.
    Sinon → tous les chapitres actifs de toutes les matières.
    """
    from database.models import Matiere
    
    query = session.query(Chapitre).join(Matiere, Chapitre.matiere_id == Matiere.id).filter(Matiere.actif.is_(True))
    if matiere_id is not None:
        query = query.filter(Matiere.id == matiere_id)

    chapitres: list[dict[str, Any]] = []
    for chap in query.all():
        matiere = chap.matiere_obj
        chapitres.append({
            "id": chap.id,
            "titre": chap.titre,
            "numero": chap.numero,
            "matiere": matiere.nom if matiere else "",
            "niveau_actuel": chap.niveau_actuel or 0,
            "maitrise_pct": float(chap.maitrise_pct or 0.0),
            "date_examen_cours": None,  # La date d'examen est au niveau cours, on l'omet pour simplifier
            "duree_examen_min": 120,
            "coefficient_cours": 1.0,
        })
    return chapitres


# ===========================================================================
# 2. Prompt builder + appel Gemini
# ===========================================================================
def _build_strategie_prompt(
    nom: str,
    description: str,
    matiere_nom: str | None,
    note_cible: float | None,
    date_cible: datetime.date,
    nb_jours_restants: int,
    chapitres: list[dict[str, Any]],
) -> str:
    """Construit le prompt pour demander une stratégie à Gemini."""
    cible_str = "Toutes les matières (objectif global)" if not matiere_nom else matiere_nom
    note_str = f"{note_cible}/20" if note_cible is not None else "(qualitatif — pas de note précise)"

    chapitres_json = json.dumps(chapitres, ensure_ascii=False, indent=2)

    prompt = f"""Tu es un coach pédagogique IA qui aide un étudiant à atteindre ses objectifs académiques.

OBJECTIF DE L'ÉTUDIANT :
- Titre : "{nom}"
- Description / motivations : "{description or '(non précisé)'}"
- Matière visée : {cible_str}
- Note cible : {note_str}
- Date cible : {date_cible.isoformat()} (dans {nb_jours_restants} jours)

ÉTAT ACTUEL DES CHAPITRES CONCERNÉS :
{chapitres_json}

LÉGENDE :
- niveau_actuel (0-13) : niveau Leitner de mémorisation (0 = jamais vu, 13 = parfaitement mémorisé long terme)
- maitrise_pct (0-100) : pourcentage de maîtrise du chapitre
- date_examen_cours : date de l'examen du cours parent (null si pas planifié)
- coefficient_cours : importance du cours dans la moyenne

TA MISSION :
Propose une stratégie réaliste et actionnable pour atteindre l'objectif. Sois HONNÊTE sur le niveau de réalisme — si la cible est très ambitieuse vu le temps restant et l'état actuel, dis-le.

Tu dois retourner UNIQUEMENT du JSON valide (PAS de markdown, PAS de texte autour) avec cette structure EXACTE :

{{
  "realisme": "tres_ambitieux" | "ambitieux" | "realiste" | "facile",
  "justification": "Diagnostic en 2-3 phrases : pourquoi tu penses que c'est réaliste/ambitieux ?",
  "heures_total_estimees": <int>,
  "heures_par_semaine": <float>,
  "ponderations_chapitres": {{
    "<chapitre_id>": <float>
  }},
  "ordre_priorite": [<chapitre_id>, <chapitre_id>, ...],
  "conseils": [
    "Conseil 1 (court, actionnable)",
    "Conseil 2",
    "Conseil 3"
  ]
}}

RÈGLES :
1. ponderations_chapitres : poids entre 0.5 et 3.0 pour CHAQUE chapitre listé ci-dessus.
   - 1.0 = priorité normale (par défaut)
   - 1.5-2.0 = priorité élevée (chapitres faibles ou critiques pour l'objectif)
   - 2.5-3.0 = priorité maximale (chapitres faibles ET critiques)
   - 0.5-0.9 = priorité réduite (chapitres déjà bien maîtrisés)
   Les clés DOIVENT être les IDs (sous forme de chaîne) des chapitres listés ci-dessus.
2. ordre_priorite : liste ordonnée des IDs de chapitres (du plus urgent au moins urgent).
3. heures_total_estimees : ton estimation honnête du temps total à investir.
4. heures_par_semaine : moyenne hebdomadaire recommandée (= heures_total / nb semaines restantes).
5. conseils : entre 3 et 5, courts (1 phrase chacun), spécifiques à la situation.
6. Si l'objectif est INATTEIGNABLE dans le temps imparti, mets realisme="tres_ambitieux" et dis-le clairement dans justification, mais propose quand même la meilleure stratégie possible.

RAPPEL : RETOURNE UNIQUEMENT LE JSON, RIEN D'AUTRE.
"""
    return prompt


def _parse_gemini_json(text: str) -> dict[str, Any]:
    """Parse robuste du JSON retourné par Gemini (mutualisé avec ai_planner)."""
    s = text.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", s, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Impossible de décoder le JSON : {exc}") from exc
        raise ValueError("Aucun JSON valide dans la réponse de Gemini.")


def proposer_strategie(
    session: Session,
    nom: str,
    description: str,
    matiere_id: int | None,
    note_cible: float | None,
    date_cible: datetime.date,
) -> dict[str, Any]:
    """Demande à Gemini de proposer une stratégie pour l'objectif.

    Returns:
        dict avec les clés : realisme, justification, heures_total_estimees,
        heures_par_semaine, ponderations_chapitres, ordre_priorite, conseils.

    Raises:
        ValueError si pas de clé API ou si Gemini répond mal.
    """
    profil = session.query(Utilisateur).first()
    _gemini_key, _gemini_model = get_gemini_credentials(session)
    if not profil or not _gemini_key:
        raise ValueError("Clé API Gemini introuvable dans le profil.")

    # Snapshot état actuel
    chapitres = _build_etat_chapitres(session, matiere_id)
    if not chapitres:
        raise ValueError(
            "Aucun chapitre pertinent trouvé pour cet objectif. "
            "Ajoute des cours et leurs chapitres avant de créer un objectif."
        )

    matiere_nom = None
    if matiere_id is not None:
        from database.models import Matiere
        matiere = session.get(Matiere, matiere_id)
        matiere_nom = matiere.nom if matiere else None

    today = datetime.date.today()
    nb_jours = max(1, (date_cible - today).days)

    prompt = _build_strategie_prompt(
        nom=nom,
        description=description,
        matiere_nom=matiere_nom,
        note_cible=note_cible,
        date_cible=date_cible,
        nb_jours_restants=nb_jours,
        chapitres=chapitres,
    )

    # Appel Gemini
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("Package `google-genai` non installé.") from exc

    client = genai.Client(api_key=_gemini_key)
    response = gemini_call_with_retry(
        lambda: client.models.generate_content(
            model=_gemini_model or "gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3,
            ),
        )
    )

    text = getattr(response, "text", "") or ""
    if not text.strip():
        raison = "Inconnue"
        if hasattr(response, "candidates") and response.candidates:
            raison = response.candidates[0].finish_reason
        raise ValueError(f"Gemini a refusé de répondre (raison : {raison}).")

    strategie = _parse_gemini_json(text)

    # Validation/nettoyage minimal du retour
    strategie = _valider_strategie(strategie, chapitres)
    return strategie


def _valider_strategie(strategie: dict, chapitres: list[dict]) -> dict:
    """Nettoie / borne les valeurs retournées par Gemini.

    - Vire les pondérations qui ne correspondent à aucun chapitre listé.
    - Clamp les valeurs entre 0.5 et 3.0.
    - Garantit la présence de toutes les clés attendues.
    """
    chap_ids_valides = {str(c["id"]) for c in chapitres}
    out: dict[str, Any] = {}

    out["realisme"] = str(strategie.get("realisme", "realiste")).lower()
    if out["realisme"] not in ("tres_ambitieux", "ambitieux", "realiste", "facile"):
        out["realisme"] = "realiste"

    out["justification"] = str(strategie.get("justification", "")).strip()
    out["heures_total_estimees"] = int(strategie.get("heures_total_estimees", 0) or 0)
    out["heures_par_semaine"] = float(strategie.get("heures_par_semaine", 0.0) or 0.0)

    # Pondérations
    raw_pond = strategie.get("ponderations_chapitres", {}) or {}
    pond_clean: dict[str, float] = {}
    for cid, coef in raw_pond.items():
        cid_str = str(cid)
        if cid_str not in chap_ids_valides:
            continue
        try:
            c = float(coef)
        except (TypeError, ValueError):
            continue
        pond_clean[cid_str] = max(0.5, min(3.0, c))
    # Si Gemini en a oublié, on met 1.0 par défaut
    for cid in chap_ids_valides:
        pond_clean.setdefault(cid, 1.0)
    out["ponderations_chapitres"] = pond_clean

    # Ordre priorité
    raw_ordre = strategie.get("ordre_priorite", []) or []
    out["ordre_priorite"] = [
        int(x) for x in raw_ordre
        if str(x) in chap_ids_valides
    ]

    # Conseils
    raw_conseils = strategie.get("conseils", []) or []
    out["conseils"] = [str(c).strip() for c in raw_conseils if str(c).strip()][:6]

    return out


# ===========================================================================
# 3. CRUD des objectifs
# ===========================================================================
def creer_objectif(
    session: Session,
    nom: str,
    description: str,
    matiere_id: int | None,
    note_cible: float | None,
    date_cible: datetime.date,
    strategie: dict,
) -> Objectif:
    """Crée et persiste un objectif avec la stratégie adoptée."""
    # On dérive les ponderations (cache rapide) depuis la stratégie
    ponderations = strategie.get("ponderations_chapitres", {}) or {}

    obj = Objectif(
        nom=nom.strip(),
        description=(description or "").strip(),
        matiere_id=matiere_id,
        note_cible=note_cible,
        date_cible=date_cible,
        statut="actif",
        strategie_ia=strategie,
        ponderations=ponderations,
    )
    session.add(obj)
    session.flush()
    return obj


def marquer_atteint(session: Session, objectif_id: int) -> None:
    """Marque un objectif comme atteint."""
    obj = session.get(Objectif, objectif_id)
    if obj is None:
        return
    obj.statut = "atteint"
    obj.date_atteinte = datetime.datetime.utcnow()


def abandonner(session: Session, objectif_id: int) -> None:
    """Marque un objectif comme abandonné (les pondérations cessent de s'appliquer)."""
    obj = session.get(Objectif, objectif_id)
    if obj is None:
        return
    obj.statut = "abandonne"


def supprimer(session: Session, objectif_id: int) -> None:
    """Suppression définitive d'un objectif (utile pour les tests/erreurs)."""
    obj = session.get(Objectif, objectif_id)
    if obj is not None:
        session.delete(obj)


# ===========================================================================
# 4. Helper pour l'ai_planner : agrège les pondérations actives
# ===========================================================================
def obtenir_ponderations_actives(session: Session) -> dict[int, float]:
    """Retourne un dict {chapitre_id: coefficient combiné} pour tous les
    chapitres concernés par au moins un objectif actif.

    Si un chapitre apparaît dans plusieurs objectifs, on prend le **max**
    (raisonnable car le plus restrictif).
    """
    out: dict[int, float] = {}
    objectifs = session.query(Objectif).filter(Objectif.statut == "actif").all()
    for obj in objectifs:
        pond = obj.ponderations or {}
        for cid_str, coef in pond.items():
            try:
                cid = int(cid_str)
                c = float(coef)
            except (TypeError, ValueError):
                continue
            if c <= 0:
                continue
            out[cid] = max(out.get(cid, 0.0), c)
    return out


def progression_objectif(session: Session, objectif: Objectif) -> dict[str, Any]:
    """Calcule la progression d'un objectif basée sur la maîtrise des chapitres.

    Renvoie :
    - maitrise_actuelle (0-100) : moyenne pondérée des chapitres concernés
    - maitrise_cible_estimee (0-100) : si note_cible est définie, on l'estime
      comme note_cible × 5 (ex: 14/20 → 70 % de maîtrise).
    - ratio_progression (0-1) : maitrise_actuelle / maitrise_cible
    - jours_restants : jusqu'à date_cible
    """
    pond = objectif.ponderations or {}
    if not pond:
        return {
            "maitrise_actuelle": 0.0,
            "maitrise_cible_estimee": None,
            "ratio_progression": 0.0,
            "jours_restants": (objectif.date_cible - datetime.date.today()).days,
        }

    # Moyenne pondérée des maîtrises
    chap_ids = [int(k) for k in pond.keys()]
    chapitres = session.query(Chapitre).filter(Chapitre.id.in_(chap_ids)).all()
    if not chapitres:
        return {
            "maitrise_actuelle": 0.0,
            "maitrise_cible_estimee": None,
            "ratio_progression": 0.0,
            "jours_restants": (objectif.date_cible - datetime.date.today()).days,
        }

    total_pond = 0.0
    total_maitrise_pondere = 0.0
    for chap in chapitres:
        coef = float(pond.get(str(chap.id), 1.0))
        m = float(chap.maitrise_pct or 0.0)
        total_pond += coef
        total_maitrise_pondere += coef * m
    maitrise_actuelle = total_maitrise_pondere / total_pond if total_pond > 0 else 0.0

    # Estimation de la maîtrise cible (si note cible définie)
    if objectif.note_cible is not None:
        maitrise_cible = min(100.0, float(objectif.note_cible) * 5.0)
        ratio = min(1.0, maitrise_actuelle / maitrise_cible) if maitrise_cible > 0 else 0.0
    else:
        maitrise_cible = None
        ratio = maitrise_actuelle / 100.0  # juste un indicateur visuel

    return {
        "maitrise_actuelle": round(maitrise_actuelle, 1),
        "maitrise_cible_estimee": round(maitrise_cible, 1) if maitrise_cible else None,
        "ratio_progression": round(ratio, 3),
        "jours_restants": (objectif.date_cible - datetime.date.today()).days,
    }


__all__ = [
    "proposer_strategie",
    "creer_objectif",
    "marquer_atteint",
    "abandonner",
    "supprimer",
    "obtenir_ponderations_actives",
    "progression_objectif",
]
