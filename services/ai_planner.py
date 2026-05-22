"""Service de planification IA.

Construit le prompt global à partir des données de l'étudiant, interroge Gemini,
et retourne un JSON structuré prêt à être inséré dans la base de données.

Phase D : enrichissement du prompt avec les **chapitres dus pour révision
espacée** (algo Leitner). Quand un chapitre arrive à échéance, le planner doit
le prioriser.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from database.models import Profil, SaisieHebdo, Semaine
from services.scheduler_engine import (
    JOURS,
    calculer_cible_hebdo_minutes,
    calculer_quota_etude_minutes,
    lisser_revisions_leitner,
    repartir_nouveaux_chapitres,
)


# ---------------------------------------------------------------------------
# Helpers de formatage
# ---------------------------------------------------------------------------
def _format_time(t) -> str:
    if not t:
        return ""
    return t.strftime("%H:%M")


def _safe_json_str(data: Any) -> str:
    """Convertit un objet en chaîne JSON formatée proprement pour le prompt."""
    if not data:
        return "Aucune donnée."
    try:
        return json.dumps(data, indent=2, ensure_ascii=False, default=str)
    except Exception:
        return str(data)


def _get_chapitres_dus_pour_semaine(session: Session, semaine: Semaine) -> list[dict[str, Any]]:
    """Liste les chapitres dont la révision tombe d'ici la fin de la semaine.

    Utilise ``revision_service.chapitres_a_reviser`` avec ``date_max =
    semaine.date_fin``. Les chapitres jamais initialisés (date_prochaine NULL)
    sont exclus — on ne veut pas surcharger Gemini avec tous les chapitres du
    semestre.

    Returns:
        Liste de dicts décrivant chaque chapitre dû, formatée pour le prompt :
        ``[{"chapitre_id", "matiere", "titre", "niveau_leitner", "date_due", "etat"}, ...]``
    """
    # Import local : évite tout risque de cycle d'import au démarrage
    from services.revision_service import chapitres_a_reviser, MAX_NIVEAU

    chaps = chapitres_a_reviser(
        session,
        date_max=semaine.date_fin,
        inclure_jamais_revises=False,
    )

    today = date.today()
    items: list[dict[str, Any]] = []
    for chap in chaps:
        delta = (chap.date_prochaine - today).days
        if delta < 0:
            etat = f"⚠️ en retard de {-delta} jour(s)"
        elif delta == 0:
            etat = "🔥 à réviser aujourd'hui"
        else:
            etat = f"⏰ à réviser dans {delta} jour(s)"

        matiere_nom = chap.matiere_obj.nom if chap.matiere_obj else "Sans matière"

        items.append({
            "chapitre_id": chap.id,
            "matiere": matiere_nom,
            "titre": chap.titre,
            "niveau_leitner": f"{chap.niveau_actuel or 0}/{MAX_NIVEAU}",
            "date_due": chap.date_prochaine.isoformat(),
            "etat": etat,
        })
    return items


def _get_ponderations_objectifs(session: Session) -> list[dict[str, Any]]:
    """F3b — Liste les chapitres pondérés par les objectifs personnels actifs.

    Retourne une liste enrichie avec le titre du chapitre et le coefficient
    agrégé (max de tous les objectifs actifs sur ce chapitre).
    """
    from services.objectif_service import obtenir_ponderations_actives

    pond_dict = obtenir_ponderations_actives(session)
    if not pond_dict:
        return []

    # Hydrater avec les infos chapitre pour que Gemini comprenne
    from database.models import Chapitre
    chap_ids = list(pond_dict.keys())
    chapitres = session.query(Chapitre).filter(Chapitre.id.in_(chap_ids)).all()

    items: list[dict[str, Any]] = []
    for chap in chapitres:
        coef = pond_dict.get(chap.id, 1.0)
        
        matiere_nom = chap.matiere_obj.nom if chap.matiere_obj else "Sans matière"

        items.append({
            "chapitre_id": chap.id,
            "matiere": matiere_nom,
            "titre": chap.titre,
            "coefficient_priorite": round(coef, 2),
            "maitrise_actuelle_pct": float(chap.maitrise_pct or 0.0),
        })
    # Tri décroissant par coefficient → les plus urgents d'abord
    items.sort(key=lambda x: -x["coefficient_priorite"])
    return items


def _format_ponderations_pour_prompt(ponderations: list[dict[str, Any]]) -> str:
    """Formate joliment les pondérations pour Gemini, ou une note si vide."""
    if not ponderations:
        return "(Aucun objectif personnel actif — pas de pondération particulière.)"
    return _safe_json_str(ponderations)


# ---------------------------------------------------------------------------
# 1. Construction du Prompt
# ---------------------------------------------------------------------------
def build_planner_prompt(
    session: Session,
    semaine: Semaine,
    saisie: SaisieHebdo,
    profil: Profil,
    consignes_manuelles: str = "",
) -> str:
    """Compile toutes les données en un prompt structuré pour Gemini."""
    # Activités professionnelles actives pour cette semaine
    from database.models import Job

    jobs_db = (
        session.query(Job)
        .filter(
            (Job.semaine_id == semaine.id)
            | (
                (Job.semaine_id.is_(None))
                & (
                    (Job.date_debut.is_(None))
                    | (Job.date_debut <= semaine.date_fin)
                )
                & ((Job.date_fin.is_(None)) | (Job.date_fin >= semaine.date_debut))
            )
        )
        .all()
    )

    # Fusion des contraintes fixes globales et des horaires de travail
    contraintes_totales = list(profil.contraintes_fixes or [])
    for j in jobs_db:
        contraintes_totales.append(
            {
                "jour": j.jour,
                "heure_debut": j.heure_debut.strftime("%H:%M"),
                "heure_fin": j.heure_fin.strftime("%H:%M"),
                "libelle": f"💼 TRAVAIL : {j.titre}",
            }
        )

    # -- 1. Profil --
    profil_data = {
        "heures_sommeil_cible": profil.heures_sommeil_cible,
        "chronotype": profil.chronotype,
        "pic_concentration": profil.pic_concentration,
        "duree_max_session_min": profil.duree_max_session_min,
        "pause_entre_sessions_min": profil.pause_entre_sessions_min,
        "capacite_weekend": profil.capacite_weekend,
        "tolerance_fatigue": profil.tolerance_fatigue,
        "temps_transport_min_aller": profil.temps_transport_min,
    }

    # -- 2. Chapitres dus pour révision espacée (Leitner) ----------------
    chapitres_dus = _get_chapitres_dus_pour_semaine(session, semaine)

    # -- 2 ter. Pondérations des objectifs personnels (F3b) --
    ponderations = _get_ponderations_objectifs(session)

    # -- 2 quater. Consignes manuelles à la volée (Chantier 2) --
    consignes_txt = (consignes_manuelles or "").strip() or "(Aucune consigne exceptionnelle cette semaine.)"

    # -- 2 quinquies. Trajets habituels (Chantier 3) --
    trajets_habituels = dict(getattr(profil, "trajets_habituels", None) or {})

    # -- 2 sexies. Check-in biomécanique du jour (Chantier 4) --
    from database.models import CheckInQuotidien

    checkin_row = (
        session.query(CheckInQuotidien)
        .filter(CheckInQuotidien.date == date.today())
        .first()
    )
    if checkin_row is not None:
        checkin_data = {
            "date": checkin_row.date.isoformat(),
            "fatigue_physique": checkin_row.fatigue_physique,
            "charge_mentale": checkin_row.charge_mentale,
            "qualite_sommeil": checkin_row.qualite_sommeil,
        }
    else:
        checkin_data = None

    # -- 3. Pré-calculs déterministes (refonte) ---------------------------
    # On délègue à scheduler_engine tout ce qui est arithmétique : répartition
    # des nouveaux chapitres, lissage des révisions ±1 jour, quota d'étude.
    # Le LLM ne reçoit que des « ingrédients prêts à placer ».
    quota_etude_min = calculer_quota_etude_minutes(profil, checkin_data)
    cible_hebdo_min = calculer_cible_hebdo_minutes(profil)

    repartition_nouveaux = repartir_nouveaux_chapitres(
        session, saisie.matieres_selectionnees, semaine,
    )
    charges_initiales = {
        j: sum(ch["temps_estime_min"] for ch in repartition_nouveaux[j])
        for j in JOURS
    }
    repartition_revisions, charges_finales = lisser_revisions_leitner(
        chapitres_dus, semaine, quota_etude_min, charges_initiales,
    )

    # -- 4. Compilation du prompt XML structuré ---------------------------
    prompt = f"""<MISSION>
Tu es un planificateur cognitif. Ci-dessous, une liste d'INGRÉDIENTS déjà
pré-répartis par jour par notre moteur Python. Ton SEUL rôle : placer chaque
ingrédient au bon créneau horaire de la journée indiquée, en respectant les
contraintes vitales et la pédagogie. INTERDIT de déplacer un ingrédient d'un
jour à un autre — Python l'a déjà équilibré pour toi.

DATES : Semaine du {semaine.date_debut} au {semaine.date_fin}.
</MISSION>

<CONTRAINTES_VITALES>
Règles non-négociables. Ne JAMAIS les violer.

- SOMMEIL : lever {_format_time(profil.heure_lever)}, coucher {_format_time(profil.heure_coucher)}. Cible {profil.heures_sommeil_cible} h/nuit.
- REPAS : {profil.nb_repas_par_jour} repas/jour ({profil.duree_repas_min} min chacun, prep {profil.duree_prep_repas_min if not (saisie.courses_config or {}).get("meal_prep") else 10} min).
- SIESTE : {"Oui (" + str(profil.duree_sieste_min) + " min)" if profil.besoin_sieste else "Non"}.
- OBLIGATOIRE : repas, sommeil, contraintes fixes, sport, transport, jobs ont {{"obligatoire": true}}. Le reste a {{"obligatoire": false}}.
- MEAL PREP : Si {{"meal_prep": true}} dans Courses, la préparation quotidienne des repas est réduite à 10 min.
- MENU : Si un "menu_hebdo" est présent, mentionne-le brièvement dans les suggestions.

Contraintes fixes absolues (placer aux heures exactes indiquées) :
{_safe_json_str(contraintes_totales)}
</CONTRAINTES_VITALES>

<LOGISTIQUE_TRAJETS>
Le temps de trajet DOIT être SOUSTRAIT de l'heure de début de l'événement.
Si un événement commence à 16:00 et que le trajet fait 30 min, le départ
du transport est OBLIGATOIREMENT à 15:30. Jamais d'empiètement.

Dictionnaire des trajets habituels (durées en minutes) — identifie le
trajet via le libellé de la contrainte (ex. « Strasbourg-Luxembourg »
pour un job au Luxembourg) :
{_safe_json_str(trajets_habituels)}

Trajet par défaut si aucun ne correspond : {profil.temps_transport_min} min.
</LOGISTIQUE_TRAJETS>

<PEDAGOGIE_ET_RYTHME>
- CHRONOTYPE : pic de concentration sur {profil.pic_concentration}. Place
  les études les plus difficiles à ce moment.
- DURÉE DE SESSION : maximum {profil.duree_max_session_min} min consécutives,
  puis pause {profil.pause_entre_sessions_min} min.
- RÉCUPÉRATION POST-SPORT : pas de théorie dense dans les 2 h qui suivent
  une séance « Intense ».
- BUFFER : laisse ~20 % de temps libre par jour pour les imprévus.
- LOAD BALANCING : si le check-in indique fatigue > 7 ou mental > 7,
  remplace les sessions denses par des tâches à faible friction (lecture
  légère, flashcards) et garantis des blocs de récupération.

Check-in biomécanique du jour ({date.today().isoformat()}) — échelle 1-10
(1 = forme olympique, 10 = épuisé) :
{_safe_json_str(checkin_data) if checkin_data else "Aucun check-in pour aujourd'hui."}

Objectifs personnels actifs (pondérations à respecter) :
{_format_ponderations_pour_prompt(ponderations)}
</PEDAGOGIE_ET_RYTHME>

<INGREDIENTS_PRE_CALCULES>
Ces listes ont DÉJÀ été équilibrées par notre moteur. Tu places chaque
ingrédient dans le JOUR indiqué — INTERDIT de le déplacer.

QUOTAS D'ÉTUDE — à respecter STRICTEMENT.
- Objectif HEBDOMADAIRE d'étude (cours + révisions perso, hors travaux ponctuels) : {cible_hebdo_min} min ({cible_hebdo_min / 60:.1f} h sur la semaine).
- Plafond JOURNALIER (modulé par le check-in) : {quota_etude_min} min ({quota_etude_min / 60:.1f} h max par jour).
Tu places les chapitres pré-calculés ci-dessous en visant l'objectif
hebdomadaire SANS dépasser le plafond journalier sur aucun jour.

[NOUVEAUX CHAPITRES — max 1 par matière par jour, déjà respecté]
{_safe_json_str(repartition_nouveaux)}

[RÉVISIONS LEITNER — déjà lissées avec tolérance ±1 jour, surcharge marquée]
{_safe_json_str(repartition_revisions)}

[CHARGE D'ÉTUDE PRÉVUE par jour après pré-calcul, minutes]
{_safe_json_str(charges_finales)}

[TRAVAUX PONCTUELS — devoirs à rendre, à placer où tu peux dans la semaine]
{_safe_json_str(saisie.travaux_ponctuels)}

[SPORT, COURSES/REPAS, PROJETS, DEV PERSO, SOCIAL, INTENDANCE — à placer librement]
Sport       : {_safe_json_str(saisie.sport_config)}
Courses     : {_safe_json_str(saisie.courses_config)}
Projets     : {_safe_json_str(saisie.projets_config)}
Dev perso   : {_safe_json_str(saisie.dev_perso_config)}
Social      : {_safe_json_str(saisie.social_config)}
Intendance  : {_safe_json_str(saisie.intendance_config)}
Ajustements : {_safe_json_str(saisie.ajustements)}
</INGREDIENTS_PRE_CALCULES>

<CONSIGNES_ETUDIANT>
Consignes saisies juste avant la génération. Prioritaires sur tes choix
automatiques mais jamais sur les CONTRAINTES_VITALES.
{consignes_txt}
</CONSIGNES_ETUDIANT>

<FORMAT_SORTIE>
Retourne UNIQUEMENT un objet JSON valide, sans aucun texte ni markdown
autour, EXACTEMENT structuré comme ceci :
{{
  "score_realisme": 85,
  "alertes": ["liste de conflits ou objectifs impossibles à tenir"],
  "justification_globale": "Explication en 3 phrases de ta stratégie.",
  "taches_ecartees": ["Tâches non placées par manque de temps"],
  "suggestions": ["1 ou 2 conseils pour bien vivre cette semaine"],
  "planning": {{
    "lundi": [
      {{
        "heure_debut": "09:00",
        "heure_fin": "10:00",
        "titre": "Maths - Chapitre X",
        "type": "etude",
        "chapitre_ids": [10],
        "obligatoire": false,
        "justification": "Nouveau chapitre pré-réparti par Python"
      }}
    ],
    "mardi": [],
    "mercredi": [],
    "jeudi": [],
    "vendredi": [],
    "samedi": [],
    "dimanche": []
  }}
}}

RÈGLES JSON :
- "chapitre_ids" obligatoire pour chaque tâche de type 'etude'. Utilise
  UNIQUEMENT les IDs présents dans <INGREDIENTS_PRE_CALCULES>. N'invente
  jamais d'ID.
- "obligatoire": true uniquement pour repas/sommeil/contraintes fixes/
  sport/transport/jobs.
</FORMAT_SORTIE>
"""
    return prompt


# ---------------------------------------------------------------------------
# 2. Appel API & Parsing
# ---------------------------------------------------------------------------
def generate_schedule_from_ai(
    session: Session,
    semaine_id: int,
    consignes_manuelles: str = "",
) -> dict[str, Any]:
    """Orchestre la création du prompt, l'appel à Gemini et le renvoi du JSON."""
    # 1. Récupération des données
    semaine = session.get(Semaine, semaine_id)
    if not semaine:
        raise ValueError("Semaine introuvable.")

    saisie = session.query(SaisieHebdo).filter_by(semaine_id=semaine_id).first()
    if not saisie:
        raise ValueError("Aucune saisie hebdomadaire trouvée pour cette semaine.")

    profil = session.query(Profil).first()
    if not profil or not profil.gemini_api_key:
        raise ValueError("Clé API Gemini introuvable dans le profil.")

    # 2. Construction du prompt
    prompt = build_planner_prompt(session, semaine, saisie, profil, consignes_manuelles=consignes_manuelles)

    # 3. Appel à Gemini via le SDK google-genai
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("Package `google-genai` non installé.") from exc

    client = genai.Client(api_key=profil.gemini_api_key.strip())

    response = client.models.generate_content(
        model=profil.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )

    text_response = getattr(response, "text", "") or ""
    if not text_response.strip():
        raison = "Inconnue"
        if hasattr(response, "candidates") and response.candidates:
            raison = response.candidates[0].finish_reason
        raise ValueError(f"Gemini a refusé de répondre. Code d'arrêt (finish_reason) : {raison}")

    # 4. Nettoyage et Parsing robuste du JSON
    return _parse_gemini_json(text_response)


def _parse_gemini_json(text: str) -> dict[str, Any]:
    """Parse la réponse de Gemini en s'assurant d'enlever les balises markdown si présentes."""
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
        # Tentative de regex en dernier recours
        match = re.search(r"\{.*\}", s, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Impossible de décoder le JSON généré par l'IA : {exc}") from exc
        raise ValueError("Aucun JSON valide trouvé dans la réponse de l'IA.")


# ===========================================================================
# 3. Recalcul adaptatif — redistribuer les jours restants
# ===========================================================================
def detecter_chapitres_manquants(session: Session, semaine: Semaine) -> list[int]:
    """Liste les chapitre_ids qui DEVRAIENT être planifiés dans la semaine
    mais qui ne le sont pas encore (= aucune Tache existante ne les référence).

    Source de vérité « ce qui devrait être là » :
      - ``SaisieHebdo.matieres_selectionnees[*].chapitre_ids`` : les chapitres
        explicitement choisis par l'étudiant.
      - ``_get_chapitres_dus_pour_semaine`` : les chapitres dus pour révision
        espacée d'ici la fin de la semaine (Leitner).

    Sont considérés « déjà planifiés » tous les chapitre_ids présents dans
    ``Tache.chapitre_ids`` (n'importe quelle Tâche de la semaine, n'importe
    quel statut).
    """
    from database.models import Tache

    cibles: set[int] = set()

    saisie = session.query(SaisieHebdo).filter_by(semaine_id=semaine.id).first()
    if saisie and saisie.matieres_selectionnees:
        for m in saisie.matieres_selectionnees:
            for ch_id in m.get("chapitre_ids") or []:
                try:
                    cibles.add(int(ch_id))
                except (TypeError, ValueError):
                    pass

    for chap in _get_chapitres_dus_pour_semaine(session, semaine):
        try:
            cibles.add(int(chap["chapitre_id"]))
        except (KeyError, TypeError, ValueError):
            pass

    deja: set[int] = set()
    taches = session.query(Tache).filter_by(semaine_id=semaine.id).all()
    for t in taches:
        for ch_id in t.chapitre_ids or []:
            try:
                deja.add(int(ch_id))
            except (TypeError, ValueError):
                pass

    return sorted(cibles - deja)


def integrer_nouveautes_a_semaine(session: Session, semaine_id: int) -> dict[str, Any]:
    """Intègre les nouveaux chapitres / révisions au planning existant
    SANS perdre l'avancement (tâches validées, XP, statuts Leitner).

    Stratégie de préservation (identique à ``replan_remaining_week``) :

    Intouchable :
      - Toutes les tâches dont le statut est ``fait`` / ``partiellement`` /
        ``non_fait`` (l'utilisateur a déjà tranché).
      - Toutes les tâches **obligatoires** (sommeil, repas, contraintes
        fixes, sport, transport, jobs) — mêmes jours, mêmes heures.

    Réorganisable :
      - Les tâches ``a_faire`` non obligatoires des jours restants
        (aujourd'hui inclus). Elles peuvent être déplacées, redistribuées,
        ou écartées par Gemini pour faire de la place aux nouveautés.

    Ajouté :
      - Les chapitres manquants détectés par ``detecter_chapitres_manquants``
        — placés dans les créneaux libres des jours restants, en respectant
        le chronotype et le plafond horaire.

    Si aucune nouveauté n'est détectée, lève ``ValueError`` (à l'appelant
    d'afficher un message clair à l'utilisateur).
    """
    import datetime
    from database.models import Chapitre, Tache

    semaine = session.get(Semaine, semaine_id)
    if not semaine:
        raise ValueError("Semaine introuvable.")

    profil = session.query(Profil).first()
    if not profil or not profil.gemini_api_key:
        raise ValueError("Clé API Gemini absente du profil.")

    today = datetime.date.today()
    if today < semaine.date_debut or today > semaine.date_fin:
        raise ValueError(
            f"Aujourd'hui ({today}) n'est pas dans la semaine ciblée "
            f"({semaine.date_debut} → {semaine.date_fin})."
        )

    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    jour_idx_today = today.weekday()
    jours_restants = jours[jour_idx_today:]

    # 1. Détection des nouveautés
    chapitre_ids_manquants = detecter_chapitres_manquants(session, semaine)
    if not chapitre_ids_manquants:
        raise ValueError(
            "Aucune nouveauté à intégrer — toutes tes matières sélectionnées "
            "et toutes les révisions dues sont déjà au planning."
        )

    # 2. Hydratation des chapitres manquants (titre, matière, niveau Leitner)
    chapitres_manquants = (
        session.query(Chapitre).filter(Chapitre.id.in_(chapitre_ids_manquants)).all()
    )
    nouveautes_data: list[dict[str, Any]] = []
    for ch in chapitres_manquants:
        matiere_nom = ch.matiere_obj.nom if ch.matiere_obj else "Sans matière"
        niveau = int(ch.niveau_actuel or 0)
        duree_min = int(round(float(ch.temps_estime_h or 1.0) * 60))
        nouveautes_data.append({
            "chapitre_id": ch.id,
            "matiere": matiere_nom,
            "titre": ch.titre,
            "niveau_leitner": niveau,
            "duree_estimee_min": duree_min,
        })

    # 3. Classement des tâches existantes (cf. replan_remaining_week)
    all_tasks = session.query(Tache).filter_by(semaine_id=semaine_id).all()

    taches_finies = [t for t in all_tasks if t.statut in ("fait", "partiellement", "non_fait")]
    taches_obligatoires_futures = [
        t for t in all_tasks
        if t.jour in jours_restants and t.obligatoire
    ]
    taches_a_reorganiser = [
        t for t in all_tasks
        if t.jour in jours_restants and not t.obligatoire and t.statut == "a_faire"
    ]

    def _t_to_dict(t: Tache, with_status: bool = False) -> dict[str, Any]:
        d = {
            "titre": t.titre,
            "type": t.type,
            "duree_min": int(t.duree_min or 0),
            "jour_initial": t.jour,
            "heure_debut": t.heure_debut.strftime("%H:%M"),
            "heure_fin": t.heure_fin.strftime("%H:%M"),
            "chapitre_ids": t.chapitre_ids or [],
        }
        if with_status:
            d["statut"] = t.statut
        return d

    # 4. Prompt Gemini — ciblé sur l'intégration
    prompt = f"""Tu es un planificateur expert. L'étudiant a généré son planning hebdo,
puis a ajouté de nouveaux chapitres dans sa bibliothèque (ou de nouvelles révisions
Leitner sont dues). Ta mission : INTÉGRER ces nouveautés au planning existant SANS
toucher à ce qui est déjà validé ou obligatoire.

PROFIL :
- Heure de lever : {profil.heure_lever}
- Heure de coucher : {profil.heure_coucher}
- Pic de concentration : {profil.pic_concentration}
- Durée max d'une session : {profil.duree_max_session_min} min
- Pause entre sessions : {profil.pause_entre_sessions_min} min
- Plafond d'étude / jour : {profil.heures_etude_plafond_par_jour or 6.0} h

CONTEXTE :
- Date du jour : {today.strftime('%A %d/%m/%Y')} ({jours[jour_idx_today]})
- Jours restants dans la semaine : {jours_restants}

⛔ INTOUCHABLES — tâches validées (historique) :
{json.dumps([_t_to_dict(t, with_status=True) for t in taches_finies], indent=2, ensure_ascii=False)}

⛔ INTOUCHABLES — tâches obligatoires (sommeil, repas, contraintes fixes, sport, etc.) :
{json.dumps([_t_to_dict(t) for t in taches_obligatoires_futures], indent=2, ensure_ascii=False)}

🔄 RÉORGANISABLES — tâches « à faire » non obligatoires (tu peux les bouger, les
fusionner, ou les écarter avec une justification pour faire de la place) :
{json.dumps([_t_to_dict(t) for t in taches_a_reorganiser], indent=2, ensure_ascii=False)}

➕ NOUVEAUTÉS À INTÉGRER (chapitres qui DOIVENT apparaître dans le planning final) :
{json.dumps(nouveautes_data, indent=2, ensure_ascii=False)}

RÈGLES :
1. Tu ne génères QUE les jours restants ({jours_restants}). Les jours passés ne te
   concernent pas.
2. Tu ne dois pas dépasser {profil.heures_etude_plafond_par_jour or 6.0} h d'étude sur
   un même jour.
3. Pour chaque tâche d'étude, inclus "chapitre_ids": [id, ...] avec les IDs réels.
4. Si tu ne peux pas placer toutes les nouveautés, mentionne dans "taches_ecartees"
   celles qui débordent avec une raison.
5. Garde les tâches obligatoires aux mêmes jours/heures qu'elles avaient.
6. Le résultat doit produire un planning complet et cohérent pour les jours restants
   (tâches obligatoires + tâches réorganisées + nouveautés).

Retourne UNIQUEMENT un JSON :
{{
  "score_realisme": 0-100,
  "alertes": ["..."],
  "justification_globale": "Explication courte de ce que tu as fait.",
  "taches_ecartees": [{{"titre": "...", "raison": "..."}}],
  "nb_nouveautes_integrees": 0,
  "planning_jours_restants": {{
    {", ".join([f'"{j}": []' for j in jours_restants])}
  }}
}}

Chaque entrée d'un jour : {{"heure_debut": "HH:MM", "heure_fin": "HH:MM", "titre": "...", "type": "...", "chapitre_ids": [], "obligatoire": false, "justification": "..."}}.
"""

    # 5. Appel Gemini
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("Package `google-genai` non installé.") from exc

    client = genai.Client(api_key=profil.gemini_api_key.strip())
    response = client.models.generate_content(
        model=profil.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.3,
        ),
    )
    text = getattr(response, "text", "") or ""
    if not text.strip():
        raise ValueError("Gemini a renvoyé une réponse vide.")

    result = _parse_gemini_json(text)
    # On renvoie la liste détectée dans le résultat pour que l'UI puisse
    # afficher ce qui a été tenté d'intégrer.
    result["chapitre_ids_manquants_detectes"] = chapitre_ids_manquants

    # 6. Application en base : suppression des tâches réorganisables + insertion
    ids_a_supprimer = [t.id for t in taches_a_reorganiser]
    if ids_a_supprimer:
        session.query(Tache).filter(Tache.id.in_(ids_a_supprimer)).delete(
            synchronize_session=False,
        )
        session.flush()

    def _str_to_time_local(s: str) -> datetime.time:
        h, m = map(int, s.split(":"))
        return datetime.time(h, m)

    planning = result.get("planning_jours_restants", {}) or {}
    nb_ajoutees = 0
    for jour, taches in planning.items():
        if jour not in jours_restants:
            continue
        for t_data in taches or []:
            # On ne re-crée pas les obligatoires (déjà en base, immuables).
            if bool(t_data.get("obligatoire", False)):
                continue
            try:
                session.add(Tache(
                    semaine_id=semaine_id,
                    type=str(t_data.get("type", "autre")).lower(),
                    titre=t_data.get("titre", "Tâche sans nom"),
                    jour=jour,
                    heure_debut=_str_to_time_local(t_data["heure_debut"]),
                    heure_fin=_str_to_time_local(t_data["heure_fin"]),
                    obligatoire=False,
                    justification_ia=t_data.get("justification", ""),
                    statut="a_faire",
                    chapitre_ids=t_data.get("chapitre_ids", []),
                ))
                nb_ajoutees += 1
            except (KeyError, ValueError) as exc:
                print(f"[integrer_nouveautes] Tâche ignorée ({jour}) : {exc}")

    result["nb_taches_ajoutees"] = nb_ajoutees
    return result


def replan_remaining_week(session: Session, semaine_id: int) -> dict[str, Any]:
    """Demande à Gemini de redistribuer les tâches restantes sur les jours qui
    restent dans la semaine, en tenant compte du retard accumulé.

    Conserve :
      - Toutes les tâches passées (statut ``fait`` / ``partiellement`` / ``non_fait``)
        intactes pour l'historique.
      - Les tâches obligatoires futures (sommeil, repas, cours présentiel,
        contraintes fixes) aux mêmes heures.

    Réorganise :
      - Les tâches futures non-obligatoires + les tâches passées encore en
        ``a_faire`` (retard pur).
    """
    import datetime
    from database.models import Tache

    semaine = session.get(Semaine, semaine_id)
    if not semaine:
        raise ValueError("Semaine introuvable.")

    profil = session.query(Profil).first()
    if not profil or not profil.gemini_api_key:
        raise ValueError("Clé API Gemini absente du profil.")

    today = datetime.date.today()
    if today < semaine.date_debut or today > semaine.date_fin:
        raise ValueError(
            f"Aujourd'hui ({today}) n'est pas dans la semaine ciblée "
            f"({semaine.date_debut} → {semaine.date_fin})."
        )

    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    jour_idx_today = today.weekday()
    jours_restants = jours[jour_idx_today:]

    all_tasks = session.query(Tache).filter_by(semaine_id=semaine_id).all()

    taches_finies = [t for t in all_tasks if t.statut in ("fait", "partiellement", "non_fait")]
    taches_obligatoires_futures = [
        t for t in all_tasks
        if t.jour in jours_restants and t.obligatoire
    ]
    taches_a_redistribuer = [
        t for t in all_tasks
        if (
            (t.jour in jours_restants and not t.obligatoire and t.statut == "a_faire")
            or (t.jour not in jours_restants and not t.obligatoire and t.statut == "a_faire")
        )
    ]

    def _t_to_dict(t: Tache, with_status: bool = False) -> dict[str, Any]:
        d = {
            "titre": t.titre,
            "type": t.type,
            "duree_min": int(t.duree_min or 0),
            "jour_initial": t.jour,
            "heure_debut": t.heure_debut.strftime("%H:%M"),
            "heure_fin": t.heure_fin.strftime("%H:%M"),
        }
        if with_status:
            d["statut"] = t.statut
            if t.commentaire_etudiant:
                d["ce_qui_reste"] = t.commentaire_etudiant
        return d

    # Phase D : on inclut aussi les chapitres dus pour révision dans le replan
    chapitres_dus = _get_chapitres_dus_pour_semaine(session, semaine)

    prompt = f"""Tu es un planificateur expert. Tu dois redistribuer les tâches restantes d'un étudiant sur les jours restants de la semaine, en tenant compte du retard.

PROFIL :
- Heure de lever : {profil.heure_lever}
- Heure de coucher : {profil.heure_coucher}
- Pic de concentration : {profil.pic_concentration}
- Durée max d'une session : {profil.duree_max_session_min} min
- Pause entre sessions : {profil.pause_entre_sessions_min} min

CONTEXTE :
- Date du jour : {today.strftime('%A %d/%m/%Y')} ({jours[jour_idx_today]})
- Jours restants dans la semaine : {jours_restants}

TÂCHES DÉJÀ EFFECTUÉES / ÉCHOUÉES (historique, NE PAS replanifier) :
{json.dumps([_t_to_dict(t, with_status=True) for t in taches_finies], indent=2, ensure_ascii=False)}

TÂCHES OBLIGATOIRES À CONSERVER TELLES QUELLES (mêmes heures, mêmes jours) :
{json.dumps([_t_to_dict(t) for t in taches_obligatoires_futures], indent=2, ensure_ascii=False)}

TÂCHES À REDISTRIBUER (le retard + ce qui restait à faire) :
{json.dumps([_t_to_dict(t) for t in taches_a_redistribuer], indent=2, ensure_ascii=False)}

🧠 CHAPITRES DUS POUR RÉVISION ESPACÉE (PRIORITÉ HAUTE) :
Si la liste ci-dessous n'est pas vide, certains de ces chapitres méritent une session d'étude supplémentaire
dans les jours restants (idéalement déjà couverts par les tâches à redistribuer, mais sinon à ajouter).
{json.dumps(chapitres_dus, indent=2, ensure_ascii=False, default=str)}

RÈGLES :
1. Ne touche PAS aux tâches obligatoires.
2. Si la charge est trop forte, écarte certaines tâches non-prioritaires et mentionne-les dans "taches_ecartees" avec une raison.
3. Respecte le chronotype et la durée max de session.
4. N'invente pas de tâches qui ne sont pas dans la liste à redistribuer (sauf, en dernier recours, une session pour un chapitre dû non encore couvert).
5. Réutilise les durées d'origine sauf si tu juges légitime de les diminuer (mentionne-le alors dans la justification).
6. Pour chaque tâche d'étude, inclus "chapitre_ids": [id, ...] avec les IDs réels.

Retourne UNIQUEMENT un JSON :
{{
  "score_realisme": 0-100,
  "alertes": ["..."],
  "justification_globale": "Explication courte de ta nouvelle répartition.",
  "taches_ecartees": [{{"titre": "...", "raison": "..."}}],
  "planning_jours_restants": {{
    {", ".join([f'"{j}": []' for j in jours_restants])}
  }}
}}

Chaque entrée d'un jour : {{"heure_debut": "HH:MM", "heure_fin": "HH:MM", "titre": "...", "type": "...", "chapitre_ids": [], "obligatoire": false, "justification": "..."}}.
"""

    # Appel Gemini
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("Package `google-genai` non installé.") from exc

    client = genai.Client(api_key=profil.gemini_api_key.strip())
    response = client.models.generate_content(
        model=profil.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.3,
        ),
    )
    text = getattr(response, "text", "") or ""
    if not text.strip():
        raise ValueError("Gemini a renvoyé une réponse vide.")

    result = _parse_gemini_json(text)

    # Application en base : supprime les tâches à redistribuer, puis re-crée
    ids_a_supprimer = [t.id for t in taches_a_redistribuer]
    if ids_a_supprimer:
        session.query(Tache).filter(Tache.id.in_(ids_a_supprimer)).delete(synchronize_session=False)
        session.flush()

    def _str_to_time_local(s: str) -> datetime.time:
        h, m = map(int, s.split(":"))
        return datetime.time(h, m)

    planning = result.get("planning_jours_restants", {}) or {}
    for jour, taches in planning.items():
        if jour not in jours_restants:
            continue
        for t_data in taches or []:
            try:
                session.add(Tache(
                    semaine_id=semaine_id,
                    type=str(t_data.get("type", "autre")).lower(),
                    titre=t_data.get("titre", "Tâche sans nom"),
                    jour=jour,
                    heure_debut=_str_to_time_local(t_data["heure_debut"]),
                    heure_fin=_str_to_time_local(t_data["heure_fin"]),
                    obligatoire=bool(t_data.get("obligatoire", False)),
                    justification_ia=t_data.get("justification", ""),
                    statut="a_faire",
                    chapitre_ids=t_data.get("chapitre_ids", []),
                ))
            except (KeyError, ValueError) as exc:
                print(f"[replan] Tâche ignorée ({jour}) : {exc}")

    return result