"""Onglet **Profil étudiant**.

Le profil est un singleton applicatif : une seule ligne dans la table ``profil``.
Le formulaire est divisé en 6 sections expansibles, conformément au cahier des
charges :

1. Identité & rythme
2. Capacité de travail
3. Contraintes fixes récurrentes
4. Transport
5. Santé & alimentation
6. Paramètres IA (Gemini)

La clé API Gemini n'est jamais transmise ailleurs que vers l'API Google : elle
est stockée localement dans la base SQLite.
"""

from __future__ import annotations

from datetime import time
from typing import Any

import pandas as pd
import streamlit as st

from database import Profil, get_session, session_scope


# ---------------------------------------------------------------------------
# Constantes d'affichage
# ---------------------------------------------------------------------------
JOURS: list[str] = [
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
]

CHRONOTYPES: dict[str, str] = {
    "leve_tot": "Lève-tôt",
    "couche_tard": "Couche-tard",
    "intermediaire": "Intermédiaire",
}

PIC_CONCENTRATION: dict[str, str] = {
    "matin": "Matin",
    "apres_midi": "Après-midi",
    "soir": "Soir",
}

METHODES_TRAVAIL: dict[str, str] = {
    "pomodoro": "Pomodoro (25/5)",
    "blocs_longs": "Blocs longs (1h30 – 2h)",
    "mixte": "Mixte — adapté à la tâche",
}

CAPACITE_WEEKEND: dict[str, str] = {
    "plein": "Oui, à plein régime",
    "partiel": "Partiellement (samedi OU dimanche)",
    "non": "Non — le week-end est sacré",
}

TOLERANCE_FATIGUE: dict[str, str] = {
    "faible": "Faible — je m'épuise vite",
    "moyenne": "Moyenne",
    "elevee": "Élevée — j'encaisse bien",
}

MODELES_GEMINI: list[str] = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-3-flash-preview",
    "gemini-2.0-flash",
]


# ---------------------------------------------------------------------------
# Accès BD — détaché de la session pour éviter les soucis de lazy-load Streamlit
# ---------------------------------------------------------------------------
def load_profil() -> dict[str, Any]:
    """Charge le profil sous forme de dict pur.

    Retourne ``{}`` s'il n'existe pas encore — c'est le marqueur "première
    utilisation".
    """
    with get_session() as session:
        p = session.query(Profil).first()
        if p is None:
            return {}
        return {
            "id": p.id,
            "nom": p.nom or "",
            "heure_lever": p.heure_lever or time(7, 0),
            "heure_coucher": p.heure_coucher or time(23, 30),
            "heures_sommeil_cible": float(p.heures_sommeil_cible or 8.0),
            "chronotype": p.chronotype or "intermediaire",
            "pic_concentration": p.pic_concentration or "matin",
            "duree_max_session_min": int(p.duree_max_session_min or 50),
            "pause_entre_sessions_min": int(p.pause_entre_sessions_min or 10),
            "methode_travail": p.methode_travail or "mixte",
            "capacite_weekend": p.capacite_weekend or "partiel",
            "tolerance_fatigue": p.tolerance_fatigue or "moyenne",
            "temps_transport_min": int(p.temps_transport_min or 0),
            "nb_repas_par_jour": int(p.nb_repas_par_jour or 3),
            "duree_repas_min": int(p.duree_repas_min or 30),
            "duree_prep_repas_min": int(p.duree_prep_repas_min or 30),
            "besoin_sieste": bool(p.besoin_sieste),
            "duree_sieste_min": int(p.duree_sieste_min or 20),
            "contraintes_fixes": list(p.contraintes_fixes or []),
            "gemini_api_key": p.gemini_api_key or "",
            "gemini_model": p.gemini_model or "gemini-2.5-flash",
        }


def save_profil(data: dict[str, Any]) -> None:
    """Upsert du profil (singleton)."""
    with session_scope() as session:
        p = session.query(Profil).first()
        if p is None:
            p = Profil()
            session.add(p)
        for key, value in data.items():
            if key == "id":
                continue
            setattr(p, key, value)


# ---------------------------------------------------------------------------
# Test de connexion Gemini
# ---------------------------------------------------------------------------
def test_gemini_connection(api_key: str, model: str) -> tuple[bool, str]:
    """Effectue un appel minimal à l'API Gemini pour valider la clé."""
    if not api_key.strip():
        return False, "Clé API vide."

    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except ImportError:
        return False, "Package `google-genai` non installé."

    try:
        client = genai.Client(api_key=api_key.strip())
        response = client.models.generate_content(
            model=model,
            contents="Réponds uniquement avec le mot : OK",
            config=types.GenerateContentConfig(
                temperature=0.0,
            ),
        )
        text = (getattr(response, "text", "") or "").strip()
        if text:
            return True, f"Connexion réussie. Réponse du modèle : « {text[:80]} »"
            
        # --- Le fameux mouchard est ici ---
        raison = "Inconnue"
        if hasattr(response, "candidates") and response.candidates:
            raison = response.candidates[0].finish_reason
        return False, f"Le modèle a refusé de répondre. Code d'arrêt : {raison}"
        
    except Exception as exc: 
        return False, f"Échec : {type(exc).__name__} — {str(exc)[:300]}"


# ---------------------------------------------------------------------------
# Rendu Streamlit
# ---------------------------------------------------------------------------
def render() -> None:
    """Point d'entrée appelé par ``st.Page``."""
    st.title("👤 Profil étudiant")
    st.caption(
        "Ces réglages alimentent l'IA à chaque génération de planning. "
        "Pas besoin de tout remplir d'un coup — tu peux y revenir."
    )

    data = load_profil()
    is_new = not data  # profil vide → première utilisation
    if is_new:
        st.info(
            "👋 **Première utilisation détectée.** "
            "Remplis ton profil, puis clique sur **Enregistrer** en bas de page."
        )
        data = _defaults()

    # === Section 1 — Identité & rythme =====================================
    with st.expander("🌅 Identité & rythme", expanded=is_new):
        col1, col2 = st.columns(2)
        with col1:
            nom = st.text_input(
                "Prénom", value=data["nom"], placeholder="Ton prénom"
            )
            heure_lever = st.time_input(
                "Heure de lever habituelle", value=data["heure_lever"]
            )
            heure_coucher = st.time_input(
                "Heure de coucher habituelle", value=data["heure_coucher"]
            )
        with col2:
            heures_sommeil = st.slider(
                "Heures de sommeil cible",
                min_value=5.0, max_value=10.0,
                value=data["heures_sommeil_cible"], step=0.5,
            )
            chronotype = st.radio(
                "Chronotype",
                options=list(CHRONOTYPES.keys()),
                format_func=lambda k: CHRONOTYPES[k],
                index=list(CHRONOTYPES).index(data["chronotype"]),
            )
            pic_concentration = st.radio(
                "Pic de concentration",
                options=list(PIC_CONCENTRATION.keys()),
                format_func=lambda k: PIC_CONCENTRATION[k],
                index=list(PIC_CONCENTRATION).index(data["pic_concentration"]),
                horizontal=True,
            )

    # === Section 2 — Capacité de travail ===================================
    with st.expander("💪 Capacité de travail", expanded=is_new):
        col1, col2 = st.columns(2)
        with col1:
            duree_max_session = st.slider(
                "Durée maximale d'une session sans pause (min)",
                min_value=20, max_value=120,
                value=data["duree_max_session_min"], step=5,
            )
            pause_entre_sessions = st.slider(
                "Durée d'une pause entre sessions (min)",
                min_value=5, max_value=20,
                value=data["pause_entre_sessions_min"], step=1,
            )
        with col2:
            methode_travail = st.selectbox(
                "Méthode de travail préférée",
                options=list(METHODES_TRAVAIL.keys()),
                format_func=lambda k: METHODES_TRAVAIL[k],
                index=list(METHODES_TRAVAIL).index(data["methode_travail"]),
            )
            tolerance_fatigue = st.selectbox(
                "Tolérance à la fatigue",
                options=list(TOLERANCE_FATIGUE.keys()),
                format_func=lambda k: TOLERANCE_FATIGUE[k],
                index=list(TOLERANCE_FATIGUE).index(data["tolerance_fatigue"]),
            )

        capacite_weekend = st.radio(
            "Capacité de travail le week-end",
            options=list(CAPACITE_WEEKEND.keys()),
            format_func=lambda k: CAPACITE_WEEKEND[k],
            index=list(CAPACITE_WEEKEND).index(data["capacite_weekend"]),
        )

    # === Section 3 — Contraintes fixes récurrentes =========================
    with st.expander("📌 Contraintes fixes récurrentes", expanded=is_new):
        st.caption(
            "Créneaux bloqués **chaque semaine** : cours en présentiel, job étudiant, "
            "sport en club… Ces blocs seront verrouillés dans tous les plannings "
            "générés. Utilise le ➕ en bas du tableau pour ajouter une ligne."
        )

        df_contraintes = _build_constraints_df(data["contraintes_fixes"])
        edited = st.data_editor(
            df_contraintes,
            num_rows="dynamic",
            width="stretch",
            hide_index=True,
            column_config={
                "jour": st.column_config.SelectboxColumn(
                    "Jour", options=JOURS, required=True,
                ),
                "heure_debut": st.column_config.TextColumn(
                    "Début (HH:MM)", required=True,
                    help="Format 24 h, ex. : 08:30",
                ),
                "heure_fin": st.column_config.TextColumn(
                    "Fin (HH:MM)", required=True,
                    help="Format 24 h, ex. : 10:30",
                ),
                "libelle": st.column_config.TextColumn(
                    "Libellé", required=True,
                    help="Ex. : « TD Droit », « Job étudiant »",
                ),
            },
            key="profil_contraintes_editor",
        )
        # On extrait ici (sera validé à l'enregistrement)
        contraintes_brutes = edited.to_dict(orient="records")

    # === Section 4 — Transport ============================================
    with st.expander("🚌 Transport", expanded=is_new):
        temps_transport = st.number_input(
            "Temps de trajet domicile ↔ université (aller simple, en minutes)",
            min_value=0, max_value=180,
            value=data["temps_transport_min"], step=5,
        )
        if temps_transport > 0:
            st.caption(
                f"⚙️ Des blocs de transport de **{temps_transport} min** seront "
                "automatiquement insérés avant et après chaque cours en présentiel."
            )

    # === Section 5 — Santé & alimentation =================================
    with st.expander("🍽️ Santé & alimentation", expanded=is_new):
        col1, col2 = st.columns(2)
        with col1:
            nb_repas = st.number_input(
                "Nombre de repas par jour",
                min_value=1, max_value=5,
                value=data["nb_repas_par_jour"], step=1,
            )
            duree_repas = st.number_input(
                "Durée moyenne d'un repas (min)",
                min_value=10, max_value=120,
                value=data["duree_repas_min"], step=5,
            )
            duree_prep_repas = st.number_input(
                "Temps de préparation des repas par jour (min)",
                min_value=0, max_value=180,
                value=data["duree_prep_repas_min"], step=5,
            )
        with col2:
            besoin_sieste = st.checkbox(
                "Besoin d'une sieste quotidienne",
                value=data["besoin_sieste"],
            )
            duree_sieste = st.number_input(
                "Durée de la sieste (min)",
                min_value=10, max_value=90,
                value=data["duree_sieste_min"], step=5,
                disabled=not besoin_sieste,
            )

    # === Section 6 — Paramètres IA (Gemini) ===============================
    with st.expander("🤖 Paramètres IA (Gemini)", expanded=is_new):
        st.caption(
            "🔒 La clé API est stockée **uniquement** dans ta base SQLite locale. "
            "Elle n'est utilisée que pour les appels à l'API Google Gemini "
            "(analyse de PDF et génération de planning)."
        )
        api_key = st.text_input(
            "Clé API Gemini",
            value=data["gemini_api_key"],
            type="password",
            placeholder="AIza...",
            help="Récupère ta clé sur https://aistudio.google.com/apikey",
        )

        # Si le modèle stocké n'est plus dans la liste, on l'ajoute pour ne pas
        # perdre l'info — utile si Google publie un nouveau modèle.
        models_options = list(MODELES_GEMINI)
        if data["gemini_model"] not in models_options:
            models_options.insert(0, data["gemini_model"])

        gemini_model = st.selectbox(
            "Modèle Gemini",
            options=models_options,
            index=models_options.index(data["gemini_model"]),
            help="**Flash** = rapide & économique. **Pro** = plus précis mais plus lent.",
        )

        col_btn, col_msg = st.columns([1, 3])
        with col_btn:
            test_clicked = st.button(
                "🔌 Tester la connexion", width="stretch"
            )
        if test_clicked:
            with col_msg:
                with st.spinner("Test en cours…"):
                    ok, msg = test_gemini_connection(api_key, gemini_model)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)

   # === Bouton enregistrer ===============================================
    st.divider()
    col_save, col_save_msg = st.columns([1, 3])
    with col_save:
        save_clicked = st.button(
            "💾 Enregistrer le profil",
            type="primary", width="stretch",
        )

    # === Zone de danger (Phase de test) ===================================
    st.divider()
    with st.expander("🧨 Zone de danger (Réinitialisation)"):
        st.warning(
            "Tu es en phase de test ? Ce bouton effacera absolument TOUT : "
            "ton profil, tes cours, tes PDFs importés et tous les plannings générés. "
            "Action irréversible."
        )
        if st.button("🗑️ Réinitialiser toute l'application", type="primary", width='stretch'):
            from database.db import reset_db
            reset_db()
            st.success("💥 Base de données et PDFs effacés avec succès. L'application redémarre...")
            st.rerun()

    # === Traitement de l'enregistrement ===================================
    if not save_clicked:
        return

    # --- Validation des contraintes fixes ---
    contraintes_validees: list[dict[str, str]] = []
    erreurs: list[str] = []
    for i, c in enumerate(contraintes_brutes, start=1):
        jour = (c.get("jour") or "").strip()
        hd = (c.get("heure_debut") or "").strip()
        hf = (c.get("heure_fin") or "").strip()
        lib = (c.get("libelle") or "").strip()

        if not any([jour, hd, hf, lib]):
            continue
        if not all([jour, hd, hf, lib]):
            erreurs.append(f"Ligne {i} : tous les champs sont obligatoires.")
            continue
        if not _is_valid_time(hd) or not _is_valid_time(hf):
            erreurs.append(f"Ligne {i} ({lib}) : format d'heure invalide (HH:MM).")
            continue
        if _to_minutes(hd) >= _to_minutes(hf):
            erreurs.append(f"Ligne {i} ({lib}) : l'heure de fin doit être après l'heure de début.")
            continue
        contraintes_validees.append(
            {"jour": jour, "heure_debut": hd, "heure_fin": hf, "libelle": lib}
        )

    if erreurs:
        with col_save_msg:
            for e in erreurs:
                st.error(e)
        return

    payload = {
        "nom": (nom or "").strip(),
        "heure_lever": heure_lever,
        "heure_coucher": heure_coucher,
        "heures_sommeil_cible": float(heures_sommeil),
        "chronotype": chronotype,
        "pic_concentration": pic_concentration,
        "duree_max_session_min": int(duree_max_session),
        "pause_entre_sessions_min": int(pause_entre_sessions),
        "methode_travail": methode_travail,
        "capacite_weekend": capacite_weekend,
        "tolerance_fatigue": tolerance_fatigue,
        "temps_transport_min": int(temps_transport),
        "nb_repas_par_jour": int(nb_repas),
        "duree_repas_min": int(duree_repas),
        "duree_prep_repas_min": int(duree_prep_repas),
        "besoin_sieste": bool(besoin_sieste),
        "duree_sieste_min": int(duree_sieste),
        "contraintes_fixes": contraintes_validees,
        "gemini_api_key": (api_key or "").strip(),
        "gemini_model": gemini_model,
    }
    try:
        save_profil(payload)
    except Exception as exc:
        with col_save_msg:
            st.error(f"Erreur lors de l'enregistrement : {exc}")
        return

    with col_save_msg:
        st.success("✅ Profil enregistré.")
    st.toast("Profil enregistré", icon="✅")

# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------
def _defaults() -> dict[str, Any]:
    """Valeurs par défaut pour un profil neuf."""
    return {
        "id": None,
        "nom": "",
        "heure_lever": time(7, 0),
        "heure_coucher": time(23, 30),
        "heures_sommeil_cible": 8.0,
        "chronotype": "intermediaire",
        "pic_concentration": "matin",
        "duree_max_session_min": 50,
        "pause_entre_sessions_min": 10,
        "methode_travail": "mixte",
        "capacite_weekend": "partiel",
        "tolerance_fatigue": "moyenne",
        "temps_transport_min": 0,
        "nb_repas_par_jour": 3,
        "duree_repas_min": 30,
        "duree_prep_repas_min": 30,
        "besoin_sieste": False,
        "duree_sieste_min": 20,
        "contraintes_fixes": [],
        "gemini_api_key": "",
        "gemini_model": "gemini-2.5-flash",
    }


def _build_constraints_df(contraintes: list[dict[str, str]]) -> pd.DataFrame:
    """DataFrame avec les colonnes attendues, même pour une liste vide."""
    cols = ["jour", "heure_debut", "heure_fin", "libelle"]
    if not contraintes:
        return pd.DataFrame({c: pd.Series(dtype="string") for c in cols})
    df = pd.DataFrame(contraintes)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols].astype("string")


def _is_valid_time(s: str) -> bool:
    """Renvoie True si ``s`` est au format HH:MM (24 h)."""
    try:
        parts = s.split(":")
        if len(parts) != 2:
            return False
        h, m = int(parts[0]), int(parts[1])
        return 0 <= h <= 23 and 0 <= m <= 59
    except (ValueError, AttributeError):
        return False


def _to_minutes(s: str) -> int:
    """Convertit ``"HH:MM"`` en minutes depuis minuit (suppose ``_is_valid_time`` OK)."""
    h, m = s.split(":")
    return int(h) * 60 + int(m)
