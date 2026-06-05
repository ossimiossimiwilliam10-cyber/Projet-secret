"""Service de génération de flashcards par IA.

Se base sur DeepSeek (ou Gemini) pour extraire des flashcards recto/verso
depuis le texte d'un chapitre.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from services.gemini_utils import call_llm
from services.profil_service import get_llm_credentials

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from database.models import Chapitre


logger = logging.getLogger("ai_flashcards")


def _clean_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def generer_flashcards(session: Session, chapitre: Chapitre, nb_cartes: int = 8) -> list[dict[str, str]]:
    """Génère une liste de flashcards recto/verso via le LLM.

    Args:
        session: Session SQLAlchemy pour récupérer la clé API.
        chapitre: Le chapitre contenant le `texte_cache`.
        nb_cartes: Le nombre cible de cartes à générer.

    Returns:
        Une liste de dictionnaires avec les clés "front" et "back".
        Ex: [{"front": "Qu'est-ce que X?", "back": "C'est Y."}]
    """
    api_key, model = get_llm_credentials(session)
    if not api_key:
        raise ValueError("Clé API introuvable. Configurez votre profil.")

    texte = chapitre.texte_cache or ""
    if not texte.strip():
        raise ValueError("Le chapitre ne contient aucun texte analysé. Importez un PDF d'abord.")

    matiere_nom = chapitre.matiere_obj.nom if chapitre.matiere_obj else "Sans matière"

    prompt = f"""Tu es un expert en pédagogie et répétition espacée (type Anki).
Ta mission est de créer {nb_cartes} flashcards de haute qualité pour le chapitre "{chapitre.titre}" de la matière "{matiere_nom}".

Voici le contenu de référence :
---
{texte[:40000]}
---

RÈGLES POUR LES FLASHCARDS :
1. Le recto ("front") doit poser une question claire, directe et non ambiguë.
2. Le verso ("back") doit donner la réponse précise et concise (idéalement 1 à 3 phrases).
3. Varie les types de questions (définitions, concepts clés, dates, formules, conséquences).
4. Utilise le format JSON strict.

Format JSON attendu :
{{
    "flashcards": [
        {{"front": "Question...", "back": "Réponse..."}},
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
            context="flashcards_gen",
        )
        
        cleaned = _clean_json(raw_resp)
        data = json.loads(cleaned)
        if "flashcards" not in data:
            # Fallback for models that just return the array
            if isinstance(data, list):
                return data
            raise ValueError("Le JSON retourné ne contient pas la clé 'flashcards'.")
            
        return data["flashcards"]
        
    except json.JSONDecodeError as e:
        logger.error(f"Erreur de parsing JSON pour les flashcards : {e}\nRéponse brute : {raw_resp}")
        raise ValueError("L'IA a retourné un format JSON invalide pour les flashcards.")
    except Exception as e:
        logger.exception("Erreur inattendue lors de la génération de flashcards.")
        raise RuntimeError(f"Erreur IA : {e}")
