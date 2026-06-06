"""Service de génération et d'évaluation d'un examen blanc (multi-chapitres).

Inspiré de l'App V2, ce service agrège les textes de plusieurs chapitres,
demande au LLM de générer des questions de synthèse, puis évalue les réponses
de l'étudiant.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from services.llm_utils import call_llm
from services.profil_service import get_llm_credentials

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from database.models import Chapitre


logger = logging.getLogger("ai_exam")


def _clean_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()



def generer_examen(
    session: Session, 
    chapitres: list[Chapitre], 
    nb_questions: int = 5
) -> tuple[list[str], str]:
    """Génère un examen blanc à partir d'une liste de chapitres.

    Args:
        session: Session SQLAlchemy.
        chapitres: Liste des chapitres sélectionnés pour l'examen.
        nb_questions: Nombre de questions à générer.

    Returns:
        Un tuple contenant :
        - Une liste de chaînes de caractères (les questions).
        - Le contexte global agrégé (pour pouvoir évaluer les réponses ensuite).
    """
    api_key, model = get_llm_credentials(session)
    if not api_key:
        raise ValueError("Clé API introuvable. Configurez votre profil.")

    if not chapitres:
        raise ValueError("Aucun chapitre sélectionné pour l'examen.")

    # Agréger le contexte
    contexte_exam = ""
    matieres_exam = set()
    for c in chapitres:
        mat_nom = c.matiere_obj.nom if c.matiere_obj else "Sans matière"
        matieres_exam.add(mat_nom)
        texte = c.texte_cache or ""
        # Limiter à 4000 caractères par chapitre pour ne pas exploser le contexte
        contexte_exam += f"\n\n=== {c.titre} ({mat_nom}) ===\n{texte[:4000]}\n"

    matieres_str = ", ".join(matieres_exam)
    
    prompt = f"""Génère EXACTEMENT {nb_questions} questions d'examen sur les matières suivantes : {matieres_str}.
Il s'agit d'un examen blanc global pour tester la compréhension de l'étudiant.

Varie les types de questions :
- Définition ou concept fondamental
- Mécanisme ou fonctionnement
- Application ou exemple concret
- Comparaison ou synthèse entre plusieurs notions
- Cause/effet

Voici le contenu de référence tiré des cours de l'étudiant :
---
{contexte_exam[:60000]}
---

Format JSON STRICT (sans texte autour) :
{{
    "questions": [
        "Question 1...",
        "Question 2...",
        ...
    ]
}}
"""

    try:
        raw_resp = call_llm(
            api_key=api_key,
            model=model,
            prompt=prompt,
            json_mode=True,
            context="exam_gen",
        )
        
        cleaned = _clean_json(raw_resp)
        data = json.loads(cleaned)
        if "questions" not in data:
            raise ValueError("Le JSON retourné ne contient pas la clé 'questions'.")
            
        questions = [str(q).strip() for q in data["questions"] if str(q).strip()]
        return questions[:nb_questions], contexte_exam
        
    except json.JSONDecodeError as e:
        logger.error(f"Erreur de parsing JSON pour l'examen : {e}\nRéponse brute : {raw_resp}")
        raise ValueError("L'IA a retourné un format JSON invalide pour l'examen.")
    except Exception as e:
        logger.exception("Erreur inattendue lors de la génération de l'examen.")
        raise RuntimeError(f"Erreur IA : {e}")


def evaluer_examen(
    session: Session,
    questions: list[str],
    reponses: list[str],
    contexte: str
) -> dict:
    """Évalue les réponses de l'étudiant à un examen blanc.

    Args:
        session: Session SQLAlchemy.
        questions: Liste des questions posées.
        reponses: Liste des réponses de l'étudiant (même ordre).
        contexte: Le texte de référence agrégé des chapitres.

    Returns:
        Un dictionnaire contenant l'évaluation détaillée.
        Ex: {
            "resultats": [{"score": "correct|partiel|incorrect", "feedback": "..."}],
            "verdict": "réussi|à retravailler",
            "message": "Commentaire global",
            "score_num": 0.8
        }
    """
    api_key, model = get_llm_credentials(session)
    if not api_key:
        raise ValueError("Clé API introuvable. Configurez votre profil.")

    paires = "\n".join(
        [f"Q{i + 1}: {q}\nRéponse de l'étudiant: {r}\n" for i, (q, r) in enumerate(zip(questions, reponses))]
    )
    
    prompt = f"""Tu es un professeur exigeant et bienveillant.
Ta mission est d'évaluer l'examen blanc d'un étudiant.

Voici le contenu de référence :
---
{contexte[:40000]}
---

Voici les questions posées et les réponses de l'étudiant :
---
{paires}
---

Pour chaque question, attribue un score ("correct", "partiel" ou "incorrect") et fournis un feedback d'une à deux phrases maximum pour expliquer ce qui va ou ce qui manque.
Donne également un verdict global sur l'examen ("réussi" ou "à retravailler") ainsi qu'un message d'encouragement/synthèse.

Format JSON STRICT :
{{
    "resultats": [
        {{"score": "correct", "feedback": "..."}},
        ...
    ],
    "verdict": "réussi",
    "message": "..."
}}
"""

    try:
        raw_resp = call_llm(
            api_key=api_key,
            model=model,
            prompt=prompt,
            json_mode=True,
            context="exam_eval",
        )
        
        cleaned = _clean_json(raw_resp)
        data = json.loads(cleaned)
        if "resultats" not in data:
            raise ValueError("Le JSON retourné ne contient pas la clé 'resultats'.")
            
        corrects = sum(1 for r in data["resultats"] if r.get("score") == "correct")
        partiels = sum(1 for r in data["resultats"] if r.get("score") == "partiel")
        
        # Calcul du score sur 1.0 (correct = 1, partiel = 0.5)
        nb_total = max(len(data["resultats"]), 1)
        data["score_num"] = (corrects + 0.5 * partiels) / nb_total
        
        return data
        
    except json.JSONDecodeError as e:
        logger.error(f"Erreur de parsing JSON pour l'évaluation : {e}\nRéponse brute : {raw_resp}")
        raise ValueError("L'IA a retourné un format JSON invalide pour l'évaluation.")
    except Exception as e:
        logger.exception("Erreur inattendue lors de l'évaluation de l'examen.")
        raise RuntimeError(f"Erreur IA : {e}")
