"""Onglet **Import externe** (Version Image et Texte)."""

from __future__ import annotations

import json
from PIL import Image
import streamlit as st

from database.db import get_session, session_scope
from database.models import Utilisateur
from services.gemini_utils import gemini_call_with_retry
from services.profil_service import get_gemini_credentials

def _parse_gemini_json(text: str) -> dict:
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
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise RuntimeError("Le package google-genai n'est pas installé.")

    prompt = """Tu es un expert en lecture d'emplois du temps.
Analyse l'image ci-jointe qui représente un planning.
Extrais tous les événements temporels (cours, shifts de travail, etc.) et convertis-les au format JSON strict.

RÈGLES :
- "jour" doit être en minuscules : lundi, mardi, mercredi, jeudi, vendredi, samedi, dimanche.
- "heure_debut" et "heure_fin" au format "HH:MM".
- Ignore les événements sans heure précise.
- Déduis les jours en fonction des colonnes si l'image utilise des dates.

RETOURNE UNIQUEMENT UN JSON AU FORMAT SUIVANT :
{
  "evenements": [
    {"jour": "lundi", "heure_debut": "08:00", "heure_fin": "10:00", "libelle": "CM Mathématiques"},
    {"jour": "samedi", "heure_debut": "06:00", "heure_fin": "14:00", "libelle": "Travail"}
  ]
}
"""
    client = genai.Client(api_key=api_key)
    response = gemini_call_with_retry(

        lambda: client.models.generate_content(
        model=model,
        contents=[prompt, image],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),

        )

    )

    text = getattr(response, "text", "") or ""
    resultat = _parse_gemini_json(text)
    return resultat.get("evenements", [])

def render() -> None:
    st.title("📸 Import d'emploi du temps")
    st.caption("Prends en photo ton planning (ENT, planning de travail). L'IA va extraire les horaires toute seule.")

    with get_session() as session:
        profil = session.query(Utilisateur).first()
        api_key, model = get_gemini_credentials(session)
        if not profil or not api_key:
            st.warning("⚠️ Clé API Gemini manquante. Configure ton profil d'abord.")
            return

        uploaded_file = st.file_uploader("Upload ton image (PNG, JPG)", type=["png", "jpg", "jpeg"])

        if st.button("🧠 Analyser l'image", type="primary"):
            if not uploaded_file:
                st.error("Il faut uploader une image.")
                return

            with st.spinner("L'IA traite ton image..."):
                try:
                    image = Image.open(uploaded_file)
                    nouveaux_evenements = _extraire_planning_image_ia(
                        image,
                        api_key,
                        model,
                    )

                    if nouveaux_evenements:
                        contraintes_actuelles = list(profil.logistique.contraintes_fixes or [])
                        contraintes_actuelles.extend(nouveaux_evenements)

                        with session_scope() as write_session:
                            profil_to_update = write_session.get(Utilisateur, profil.id)
                            profil_to_update.contraintes_fixes = contraintes_actuelles

                        st.success(f"✅ {len(nouveaux_evenements)} contraintes ajoutées à ton profil !")
                        st.json(nouveaux_evenements)
                    else:
                        st.info("Aucun créneau trouvé sur l'image.")

                except Exception as e:
                    st.error(f"❌ Erreur lors de l'extraction : {e}")

__all__ = ["render"]