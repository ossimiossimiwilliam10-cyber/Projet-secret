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

from database.models import Chapitre, Cours, Profil, SaisieHebdo, Semaine


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
        ``[{"chapitre_id", "cours", "titre", "niveau_leitner", "date_due", "etat"}, ...]``
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

        items.append({
            "chapitre_id": chap.id,
            "cours": chap.cours.nom if chap.cours else "?",
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
        items.append({
            "chapitre_id": chap.id,
            "cours": chap.cours.nom if chap.cours else "?",
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

    # -- 2. Cours sélectionnés --
    cours_a_planifier = []
    if saisie.cours_selectionnes:
        for c_sel in saisie.cours_selectionnes:
            cours_id = c_sel.get("cours_id")
            cours_db = session.get(Cours, cours_id)
            if not cours_db:
                continue

            ch_ids = c_sel.get("chapitre_ids", [])
            if ch_ids:
                chapitres_db = (
                    session.query(Chapitre)
                    .filter(Chapitre.id.in_(ch_ids))
                    .all()
                )
                chapitres_details = [
                    {
                        "chapitre_id": ch.id,
                        "numero": ch.numero,
                        "titre": ch.titre,
                        "temps_estime_h": ch.temps_estime_h,
                        "niveau_leitner": ch.niveau_actuel or 0,
                    }
                    for ch in chapitres_db
                ]
            else:
                chapitres_details = "Révision globale du cours"

            cours_a_planifier.append({
                "cours_id": cours_db.id,
                "nom_cours": cours_db.nom,
                "matiere": cours_db.matiere,
                "type_travail": c_sel.get("type_travail"),
                "urgence": c_sel.get("urgence"),
                "chapitres_cibles": chapitres_details,
                "conseil_methode_ia": (
                    cours_db.pdf_analyse.get("conseils_methode", "")
                    if cours_db.pdf_analyse else ""
                ),
            })

    # -- 2 bis. Chapitres dus pour révision espacée (NOUVEAU Phase D) --
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

    # -- 3. Compilation du prompt final --
    prompt = f"""Tu es un expert en planification et en sciences cognitives.
Tu dois générer un planning hebdomadaire détaillé, réaliste et optimisé pour un étudiant.

DATES : Semaine du {semaine.date_debut} au {semaine.date_fin}.

=== PROFIL ÉTUDIANT & RYTHME BIOLOGIQUE ===
Heure de lever habituelle : {_format_time(profil.heure_lever)}
Heure de coucher habituelle : {_format_time(profil.heure_coucher)}
Métriques de travail : {_safe_json_str(profil_data)}
Repas : {profil.nb_repas_par_jour} repas/jour (durée: {profil.duree_repas_min}min, prep: {profil.duree_prep_repas_min}min)
Sieste requise : {"Oui (" + str(profil.duree_sieste_min) + " min)" if profil.besoin_sieste else "Non"}

=== CONTRAINTES FIXES ABSOLUES (À placer obligatoirement aux heures indiquées) ===
{_safe_json_str(contraintes_totales)}

=== ÉTUDES & TRAVAUX ACADÉMIQUES ===
Cours à travailler (chaque cours/chapitre a un ID — réutilise-les dans "chapitre_ids") :
{_safe_json_str(cours_a_planifier)}

Travaux ponctuels (Devoirs) : {_safe_json_str(saisie.travaux_ponctuels)}

=== 🧠 CHAPITRES DUS POUR RÉVISION ESPACÉE (PRIORITÉ HAUTE) ===
Ces chapitres ont une date de révision (algo Leitner) qui tombe d'ici la fin de la semaine.
Tu DOIS leur dédier au moins une session d'étude chacun cette semaine, en utilisant
leur "chapitre_id" dans les tâches d'étude que tu crées. NE LES OUBLIE PAS.
{_safe_json_str(chapitres_dus)}

=== 🎯 OBJECTIFS PERSONNELS DE L'ÉTUDIANT (PONDÉRATIONS À RESPECTER) ===
{_format_ponderations_pour_prompt(ponderations)}

=== 💬 CONSIGNES EXCEPTIONNELLES DE L'ÉTUDIANT POUR CETTE SEMAINE ===
Ces consignes ont été saisies juste avant le lancement de la génération. Elles sont
prioritaires sur tes choix automatiques (mais ne doivent jamais contredire les règles
de sécurité comme le sommeil ou les contraintes fixes absolues).
{consignes_txt}

=== 🗺️ TRAJETS HABITUELS DE L'ÉTUDIANT (durées en minutes) ===
Dictionnaire des trajets récurrents avec leur durée exacte. Identifie le trajet
pertinent à partir du libellé de la contrainte (ex. « Strasbourg-Luxembourg » pour
un cours/travail au Luxembourg, « Appartement-Fac » pour un cours à l'université).
Si aucun trajet ne correspond, utilise le temps de transport par défaut ({profil.temps_transport_min} min).
{_safe_json_str(trajets_habituels)}

=== 📊 CHECK-IN BIOMÉCANIQUE DU JOUR ===
Auto-évaluation de l'étudiant sur 1-10 (1 = forme olympique, 10 = épuisé)
pour la journée du {date.today().isoformat()}. Si la valeur est null, aucun
check-in n'a été saisi aujourd'hui — applique alors les règles standard.
{_safe_json_str(checkin_data) if checkin_data else "Aucun check-in pour aujourd'hui."}

=== SPORT & PHYSIQUE ===
{_safe_json_str(saisie.sport_config)}

=== COURSES & REPAS (Meal Prep) ===
{_safe_json_str(saisie.courses_config)}

=== PROJETS & DÉVELOPPEMENT PERSONNEL ===
Projets Perso : {_safe_json_str(saisie.projets_config)}
Habitudes (Dev Perso) : {_safe_json_str(saisie.dev_perso_config)}

=== SOCIAL, INTENDANCE & AJUSTEMENTS ===
Social/Loisirs : {_safe_json_str(saisie.social_config)}
Intendance/Admin : {_safe_json_str(saisie.intendance_config)}
Ajustements (Énergie, Type semaine, Événements exceptionnels) : {_safe_json_str(saisie.ajustements)}

=== RÈGLES OBLIGATOIRES À RESPECTER ===
1. SOMMEIL & REPAS : Ne JAMAIS empiéter sur le sommeil. Placer tous les repas.
2. RÉCUPÉRATION SPORT : Si une séance de sport est 'Intense', ne pas placer de révision théorique dense dans les 2 heures qui suivent.
3. CHRONOTYPE : Place les tâches d'étude les plus urgentes ou difficiles sur le pic de concentration ({profil.pic_concentration}).
4. BUFFER : Laisse environ 20% de temps libre non planifié pour parer aux imprévus. Ne remplis pas les journées à 100%.
5. TRANSPORT : Ajoute un bloc de transport avant et après chaque contrainte fixe ou cours en présentiel. Réfère-toi au dictionnaire des trajets habituels pour déterminer la durée exacte. Si le lieu est inconnu, utilise le temps de transport par défaut.
6. ÉQUILIBRE : Si le type de semaine est 'Light / Chill' ou l'énergie est 'Fatigué', réduis drastiquement les sessions d'étude non urgentes et augmente les loisirs.
7. OBLIGATOIRE : Les repas, le sommeil, les contraintes fixes, le sport et le transport ont l'attribut "obligatoire": true. Le reste a "obligatoire": false.
8. CHAPITRE_IDS OBLIGATOIRES : Pour chaque tâche de type 'etude', tu DOIS inclure "chapitre_ids": [id1, id2] dans le JSON. Utilise UNIQUEMENT les IDs réels listés dans "Cours à travailler" ou dans "Chapitres dus pour révision espacée" — n'invente JAMAIS d'ID.
9. PRIORITÉ AUX RÉVISIONS DUES : Si la section "Chapitres dus pour révision espacée" contient des chapitres, ils ont PRIORITÉ ABSOLUE sur les autres sessions d'étude facultatives. Garantis-leur à chacun au moins un slot d'étude dans la semaine. Si la semaine est très chargée, écarte plutôt les tâches non-urgentes (et mentionne-les dans "taches_ecartees").
10. OBJECTIFS PERSONNELS : La section "Objectifs personnels" liste des chapitres avec un coefficient (1.0 = normal, 2.0 = double priorité, 3.0 = priorité max). Pour les chapitres avec un coefficient ≥ 1.5, alloue PROPORTIONNELLEMENT plus de temps d'étude (par ex. un chapitre à 2.5× doit avoir 2-3 sessions là où un chapitre à 1.0× en aurait une). Les chapitres avec coefficient ≤ 0.9 peuvent recevoir moins de temps (l'étudiant les maîtrise déjà).
11. RÈGLE ABSOLUE DE DÉPLACEMENT : Le temps de trajet DOIT être SOUSTRAIT de l'heure de début de l'événement. Si un examen, un cours ou le travail au Luxembourg commence à 16h00 et que le trajet dure 30 minutes, le départ du transport est OBLIGATOIREMENT à 15h30. Ne planifie jamais un trajet qui empiète sur l'événement.
12. RÈGLE DE LOAD BALANCING : Prends en compte le Check-in Biomécanique du jour de l'étudiant. Si la fatigue physique ou la charge mentale dépasse 7/10, tu DOIS alléger la journée. Remplace les sessions de théorie dense par des tâches à faible friction cognitive (lecture légère, révision passive, flashcards) et garantis des blocs de récupération.

=== FORMAT DE SORTIE ATTENDU ===
Tu dois retourner UNIQUEMENT un objet JSON valide, sans aucun texte avant ou après, structuré EXACTEMENT comme ceci :
{{
  "score_realisme": 85,
  "alertes": ["liste de conflits ou objectifs impossibles à tenir ce qui a forcé des reports"],
  "justification_globale": "Explication en 3 phrases de ta stratégie pour cette semaine.",
  "taches_ecartees": ["Tâches qui n'ont pas pu être placées par manque de temps"],
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
        "justification": "Révision Leitner due le 22/05"
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