"""Onglet **Import externe** (Version Image).

Prends en photo ton planning papier → Gemini extrait les créneaux.
"""

from __future__ import annotations

import json

import streamlit as st
from PIL import Image

from database import Utilisateur, get_session, session_scope
from services.llm_utils import llm_call_with_retry
from services.profil_service import get_llm_api_key


def _parse_llm_json(text: str) -> dict:
    s = text.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines and lines[0].startswith("```json"):
            s = "\n".join(lines[1:-1])
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return {}


def _extraire_planning_image_ia(image: Image.Image, api_key: str, model: str) -> list[dict]:
    if model.startswith("deepseek"):
        raise ValueError(
            "L'import par image n'est pas encore supporté par DeepSeek. "
            "Bascule sur un modèle LLM dans ton profil."
        )
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise RuntimeError("Le package google-genai n'est pas installé.")

    prompt = """Tu es un expert en lecture d'emplois du temps.
Analyse l'image ci-jointe qui représente un planning.
Extrais tous les événements temporels (cours, shifts de travail, etc.) et convertis-les au format JSON strict.

RÈGLES :
- \"jour\" doit être en minuscules : lundi, mardi, mercredi, jeudi, vendredi, samedi, dimanche.
- \"heure_debut\" et \"heure_fin\" au format \"HH:MM\".
- Ignore les événements sans heure précise.
- Déduis les jours en fonction des colonnes si l'image utilise des dates.

RETOURNE UNIQUEMENT UN JSON AU FORMAT SUIVANT :
{
  \"evenements\": [
    {\"jour\": \"lundi\", \"heure_debut\": \"08:00\", \"heure_fin\": \"10:00\", \"libelle\": \"CM Mathématiques\"},
    {\"jour\": \"samedi\", \"heure_debut\": \"06:00\", \"heure_fin\": \"14:00\", \"libelle\": \"Travail\"}
  ]
}"""
    client = genai.Client(api_key=api_key)
    response = llm_call_with_retry(
        lambda: client.models.generate_content(
            model=model, contents=[prompt, image],
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1),
        ),
        context="import_planning",
    )
    text = getattr(response, "text", "") or ""
    resultat = _parse_llm_json(text)
    return resultat.get("evenements", [])


def render() -> None:
    st.title("📸 Import d'emploi du temps")
    st.caption(
        "Prends en photo ton planning (ENT, planning de travail). "
        "L'IA va extraire les horaires toute seule et les ajouter à tes contraintes fixes."
    )

    with get_session() as session:
        profil = session.query(Utilisateur).first()
        api_key, model = get_llm_api_key(session)
        if not profil or not api_key:
            st.warning("⚠️ Clé API manquante. Configure ton profil d'abord (👤 Utilisateur).")
            return

        # Indicateur du modèle actif
        st.caption(f"🤖 Modèle actif : **{model}**")

        uploaded_file = st.file_uploader(
            "Upload ton image (PNG, JPG)", type=["png", "jpg", "jpeg"],
            help="Photo de ton EDT papier, capture d'écran de ton ENT, etc.",
        )

        if uploaded_file:
            st.image(uploaded_file, caption="Aperçu", width=400)

        if st.button("🧠 Analyser l'image", type="primary", disabled=not uploaded_file):
            if not uploaded_file:
                st.error("Il faut uploader une image.")
                return

            with st.spinner("🧠 L'IA analyse ton image... (~15-30s)"):
                try:
                    image = Image.open(uploaded_file)
                    nouveaux_evenements = _extraire_planning_image_ia(image, api_key, model)

                    if nouveaux_evenements:
                        contraintes_actuelles = list(profil.logistique.contraintes_fixes or []) if profil.logistique else []
                        contraintes_actuelles.extend(nouveaux_evenements)

                        with session_scope() as write_session:
                            p = write_session.get(Utilisateur, profil.id)
                            p.logistique.contraintes_fixes = contraintes_actuelles

                        st.success(f"✅ **{len(nouveaux_evenements)}** contrainte(s) ajoutée(s) à ton profil !")
                        st.json(nouveaux_evenements)
                        st.info("💡 Ces créneaux sont maintenant dans tes **Contraintes fixes récurrentes** (👤 Utilisateur). L'IA les verrouillera dans tous tes plannings.")
                    else:
                        st.info("Aucun créneau trouvé sur l'image. Essaie avec une photo plus nette ?")

                except ValueError as e:
                    st.warning(str(e))
                except Exception as e:
                    st.error(f"❌ Erreur : {e}")
