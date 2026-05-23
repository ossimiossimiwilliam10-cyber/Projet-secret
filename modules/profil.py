"""Onglet **Utilisateur étudiant**.

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

import time as _time_mod
from datetime import time
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy.orm import selectinload

from database import Utilisateur, get_session, session_scope
from services.crypto import (
    decrypt_api_key,
    encrypt_api_key,
    is_encrypted,
    mask_for_display,
)
from services.gamification_service import progression_niveau
from services.profil_validator import validate_biometrie


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
    "gemini-2.0-flash",
]

# Liste unifiée utilisée dans l'UI (Gemini + DeepSeek).
MODELES_IA: list[str] = MODELES_GEMINI + [
    "deepseek-v4-pro",
]


# ---------------------------------------------------------------------------
# Accès BD — détaché de la session pour éviter les soucis de lazy-load Streamlit
# ---------------------------------------------------------------------------
def load_profil() -> dict[str, Any]:
    """Charge le profil sous forme de dict pur.

    Retourne ``{}`` s'il n'existe pas encore — c'est le marqueur "première
    utilisation".

    Optimisations :
    - Eager loading des 4 sous-configs (1 requête au lieu de 5).
    - Déchiffrement automatique de la clé API Gemini.
    """
    with get_session() as session:
        p = (
            session.query(Utilisateur)
            .options(
                selectinload(Utilisateur.biometrie),
                selectinload(Utilisateur.logistique),
                selectinload(Utilisateur.systeme),
                selectinload(Utilisateur.gamification),
            )
            .first()
        )
        if p is None:
            return {}

        # Déchiffrement transparent de la clé API (legacy en clair géré)
        gemini_key_stored = p.systeme.gemini_api_key or ""
        gemini_key_clear = decrypt_api_key(gemini_key_stored)

        return {
            "id": p.id,
            "nom": p.nom or "",
            "prenom": p.prenom or "",
            "heure_lever": p.biometrie.heure_lever or time(7, 0),
            "heure_coucher": p.biometrie.heure_coucher or time(23, 30),
            "heures_sommeil_cible": float(p.biometrie.heures_sommeil_cible or 8.0),
            "chronotype": p.biometrie.chronotype or "intermediaire",
            "pic_concentration": p.biometrie.pic_concentration or "matin",
            "duree_max_session_min": int(p.biometrie.duree_max_session_min or 50),
            "pause_entre_sessions_min": int(p.biometrie.pause_entre_sessions_min or 10),
            "methode_travail": p.biometrie.methode_travail or "mixte",
            "capacite_weekend": p.biometrie.capacite_weekend or "partiel",
            "tolerance_fatigue": p.biometrie.tolerance_fatigue or "moyenne",
            "heures_etude_cible_par_semaine": float(p.biometrie.heures_etude_cible_par_semaine or 21.0),
            "heures_etude_plafond_par_jour": float(p.biometrie.heures_etude_plafond_par_jour or 6.0),
            "temps_transport_min": int(p.logistique.temps_transport_min or 0),
            "trajets_habituels": dict(p.logistique.trajets_habituels or {}),
            "nb_repas_par_jour": int(p.logistique.nb_repas_par_jour or 3),
            "duree_repas_min": int(p.logistique.duree_repas_min or 30),
            "duree_prep_repas_min": int(p.logistique.duree_prep_repas_min or 30),
            "besoin_sieste": bool(p.biometrie.besoin_sieste),
            "duree_sieste_min": int(p.biometrie.duree_sieste_min or 20),
            "contraintes_fixes": list(p.logistique.contraintes_fixes or []),
            "gemini_api_key": gemini_key_clear,
            "gemini_api_key_encrypted_was_legacy": (
                bool(gemini_key_stored) and not is_encrypted(gemini_key_stored)
            ),
            "gemini_model": p.systeme.gemini_model or "gemini-2.5-flash",
        }


_GAMIFICATION_ATTRS = frozenset([
    "xp", "niveau", "streak_jours", "streak_record", "derniere_activite_xp",
    "nb_quiz_total", "nb_chapitres_maitrise", "nb_seances_sport_total",
])
_SYSTEME_ATTRS = frozenset([
    "gemini_api_key", "gemini_model", "replanning_auto_actif",
])
_BIOMETRIE_ATTRS = frozenset([
    "heure_lever", "heure_coucher", "heures_sommeil_cible", "chronotype",
    "pic_concentration", "duree_max_session_min", "pause_entre_sessions_min",
    "methode_travail", "capacite_weekend", "tolerance_fatigue",
    "heures_etude_cible_par_semaine", "heures_etude_plafond_par_jour",
    "besoin_sieste", "duree_sieste_min",
])
_LOGISTIQUE_ATTRS = frozenset([
    "temps_transport_min", "trajets_habituels", "nb_repas_par_jour",
    "duree_repas_min", "duree_prep_repas_min", "contraintes_fixes",
])
# Champs internes qui ne doivent jamais être écrits en base.
_TRANSIENT_KEYS = frozenset(["id", "gemini_api_key_encrypted_was_legacy"])


def save_profil(data: dict[str, Any]) -> None:
    """Upsert du profil (singleton).

    - Chiffre la clé API Gemini avant écriture.
    - Flush après ``add()`` pour synchroniser les FK des sous-configs.
    """
    with session_scope() as session:
        from database.models import (
            BiometrieConfig,
            GamificationState,
            LogistiqueConfig,
            SystemeConfig,
        )
        p = session.query(Utilisateur).first()
        if p is None:
            p = Utilisateur(
                gamification=GamificationState(),
                systeme=SystemeConfig(),
                logistique=LogistiqueConfig(),
                biometrie=BiometrieConfig(),
            )
            session.add(p)
            session.flush()  # garantit p.id pour les FK des sous-configs

        for key, value in data.items():
            if key in _TRANSIENT_KEYS:
                continue
            # Chiffrement transparent de la clé Gemini avant persistance.
            if key == "gemini_api_key":
                value = encrypt_api_key(value)
            if key in _GAMIFICATION_ATTRS:
                setattr(p.gamification, key, value)
            elif key in _SYSTEME_ATTRS:
                setattr(p.systeme, key, value)
            elif key in _BIOMETRIE_ATTRS:
                setattr(p.biometrie, key, value)
            elif key in _LOGISTIQUE_ATTRS:
                setattr(p.logistique, key, value)
            else:
                setattr(p, key, value)


# ---------------------------------------------------------------------------
# Test de connexion Gemini
# ---------------------------------------------------------------------------
# Erreurs réseau pour lesquelles un retry a du sens (timeout, 5xx, etc.).
# Pas d'AttributeError : on filtre via le contenu du message dans le code.
_RETRYABLE_HINTS = ("timeout", "timed out", "503", "502", "504", "connection reset")


def _is_retryable_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(h in msg for h in _RETRYABLE_HINTS)


def test_gemini_connection(api_key: str, model: str, max_retries: int = 3) -> tuple[bool, str]:
    """Effectue un appel minimal à l'API Gemini pour valider la clé.

    Retry exponentiel (1s, 2s, 4s) sur erreurs réseau transitoires uniquement
    — une clé invalide ou un modèle inconnu ne sont **pas** retryés.
    """
    if not api_key.strip():
        return False, "Clé API vide."

    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except ImportError:
        return False, "Package `google-genai` non installé."

    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            client = genai.Client(api_key=api_key.strip())
            response = client.models.generate_content(
                model=model,
                contents="Réponds uniquement avec le mot : OK",
                config=types.GenerateContentConfig(
                    temperature=0.0,
                ),
            )
            break  # succès — on sort de la boucle
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < max_retries - 1 and _is_retryable_error(exc):
                _time_mod.sleep(2 ** attempt)  # 1s, 2s, 4s
                continue
            # Erreur non-retryable OU dernier essai : on traite ci-dessous
            response = None
            break
    else:
        response = None

    if response is None and last_exc is not None:
        # Diagnostic contextuel
        nom = type(last_exc).__name__
        msg = str(last_exc)[:300]
        if "401" in msg or "403" in msg or "unauthor" in msg.lower():
            return False, f"🔐 Clé API refusée par Gemini. Vérifie qu'elle est correcte et active.\n\nDétail : {msg}"
        if "404" in msg:
            return False, f"❓ Modèle introuvable. Vérifie le nom (`{model}`).\n\nDétail : {msg}"
        if "429" in msg or "quota" in msg.lower() or "rate" in msg.lower():
            return False, f"⏱️ Quota Gemini dépassé pour le moment. Réessaie dans quelques minutes.\n\nDétail : {msg}"
        if _is_retryable_error(last_exc):
            return False, f"⏰ Connexion à Gemini impossible après {max_retries} tentatives.\n\nDétail : {msg}"
        return False, f"Échec : {nom} — {msg}"

    # Analyse de la réponse — refus ou texte vide
    text = (getattr(response, "text", "") or "").strip()
    if text:
        return True, f"Connexion réussie. Réponse du modèle : « {text[:80] } »"

    # Traduction des codes finish_reason Gemini en messages humains.
    raisons_fr: dict[str, str] = {
        "STOP":                "✅ Réponse terminée normalement (mais texte vide — étrange).",
        "MAX_TOKENS":          "📏 Réponse coupée car trop longue.",
        "SAFETY":              "🛡️ Filtré par les règles de sécurité Gemini.",
        "RECITATION":          "📚 Le modèle a refusé pour cause de récitation (contenu copyrighté).",
        "PROHIBITED_CONTENT":  "🚫 Contenu interdit détecté par Gemini.",
        "LANGUAGE":            "🌐 Langue non supportée par ce modèle.",
        "OTHER":               "❓ Raison inconnue du côté Gemini.",
        "BLOCKLIST":           "⛔ Mot interdit détecté.",
    }
    raison_brute = "Inconnue"
    if hasattr(response, "candidates") and response.candidates:
        raison_brute = str(response.candidates[0].finish_reason or "Inconnue")
    raison_key = raison_brute.split(".")[-1].upper()
    raison_fr = raisons_fr.get(raison_key, f"Code Gemini inconnu : {raison_brute}")
    return False, f"Le modèle a refusé de répondre. {raison_fr}"


# Alias rétrocompatible — le code UI appelle `test_llm_connection`.
test_llm_connection = test_gemini_connection


# ---------------------------------------------------------------------------
# Rendu Streamlit
# ---------------------------------------------------------------------------
def render() -> None:
    """Point d'entrée appelé par ``st.Page``."""

    from services.scheduler_engine import calculer_velocite_historique

    profil_existe = False
    xp_total = 0
    nb_seances_sport = 0
    velocite_result = None
    with get_session() as session:
        profil_db = (
            session.query(Utilisateur)
            .options(selectinload(Utilisateur.gamification))
            .first()
        )
        if profil_db:
            profil_existe = True
            velocite_result = calculer_velocite_historique(session, profil_db.id)
            if profil_db.gamification:
                xp_total = profil_db.gamification.xp or 0
                nb_seances_sport = profil_db.gamification.nb_seances_sport_total or 0

    if profil_existe:
        prog = progression_niveau(xp_total)

        col_lvl, col_xp, col_sport = st.columns([1, 2, 1])
        with col_lvl:
            st.metric("🏆 Niveau", prog["niveau"])
        with col_xp:
            ratio = prog["ratio"]
            st.write(f"✨ {prog['xp_dans_palier']:,} / {prog['xp_palier_taille']:,} XP")
            st.progress(ratio)
        with col_sport:
            st.metric("🏋️ Séances Sport", nb_seances_sport)
        st.divider()

    st.title("👤 Utilisateur étudiant")
    st.caption(
        "Ces réglages alimentent l'IA à chaque génération de planning. "
        "Pas besoin de tout remplir d'un coup — tu peux y revenir."
    )

    # Bandeau vélocité — adapté à la confiance de l'échantillon.
    if velocite_result is not None:
        pct = int(velocite_result.multiplicateur * 100)
        if velocite_result.confiance == "aucune":
            st.caption(f"📊 {velocite_result.message}")
        elif velocite_result.confiance == "faible":
            st.warning(
                f"📊 **Vélocité provisoire : {pct}%** (échantillon faible) — "
                f"{velocite_result.message}"
            )
        else:
            color = "green" if pct >= 100 else "orange" if pct >= 80 else "red"
            st.info(f"**Vélocité historique : :{color}[{pct}%]** — {velocite_result.message}")

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

        st.divider()
        st.markdown("##### 🎯 Quota d'étude (cours + révisions perso)")
        st.caption(
            "Définis ton **objectif hebdomadaire** (total d'heures visées sur "
            "la semaine) et ton **plafond journalier** (ne jamais dépasser). "
            "L'IA répartira intelligemment ton objectif sur les 7 jours sans "
            "jamais dépasser le plafond. Si tu déclares ton check-in du jour "
            "fatigué (> 7/10), le plafond est réduit de 30 % pour ce jour-là."
        )
        col_h1, col_h2 = st.columns(2)
        with col_h1:
            heures_etude_cible_par_semaine = st.slider(
                "📆 Objectif hebdo (total)",
                min_value=1.0, max_value=70.0,
                value=float(data["heures_etude_cible_par_semaine"]),
                step=0.5,
                format="%.1f h / semaine",
                help="Ex. : 40 h pour un étudiant en période d'examens, "
                     "20-25 h en rythme normal avec cours + job.",
            )
        with col_h2:
            heures_etude_plafond_par_jour = st.slider(
                "🛑 Plafond / jour",
                min_value=1.0, max_value=14.0,
                value=float(data["heures_etude_plafond_par_jour"]),
                step=0.5,
                format="%.1f h / jour",
                help="L'IA ne placera jamais plus que ça sur une journée. "
                     "Au-delà de 8 h, garde en tête que la qualité chute.",
            )

        # Validation visuelle : objectif hebdo doit être atteignable avec
        # le plafond × 7. Sinon l'IA ne pourra jamais le tenir.
        plafond_x_7 = heures_etude_plafond_par_jour * 7
        if heures_etude_cible_par_semaine > plafond_x_7:
            st.error(
                f"⚠️ Incohérence : ton objectif ({heures_etude_cible_par_semaine:.1f} h) "
                f"est supérieur au plafond multiplié par 7 jours "
                f"({plafond_x_7:.1f} h). L'IA ne pourra pas l'atteindre. "
                f"Augmente le plafond ou baisse l'objectif."
            )
        elif heures_etude_cible_par_semaine > plafond_x_7 * 0.9:
            st.warning(
                f"ℹ️ Ton objectif ({heures_etude_cible_par_semaine:.1f} h) est "
                f"très proche du maximum théorique ({plafond_x_7:.1f} h). "
                f"L'IA aura peu de marge pour ajuster en cas d'imprévu."
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
            "Temps de trajet par défaut (aller simple, en minutes)",
            min_value=0, max_value=300,
            value=data["temps_transport_min"], step=5,
            help="Utilisé si aucun trajet spécifique ne correspond au lieu.",
        )
        if temps_transport > 0:
            st.caption(
                f"⚙️ Si aucun trajet spécifique n'est défini, des blocs de "
                f"**{temps_transport} min** seront utilisés."
            )

        st.markdown("##### 🗺️ Trajets habituels")
        st.caption(
            "Définis ici tes trajets récurrents et leur durée en minutes. "
            "L'IA s'en servira pour planifier précisément. "
            "Ex. : `Appartement-Fac` → 15, `Strasbourg-Luxembourg` → 150."
        )

        trajets_dict = data["trajets_habituels"] or {}
        df_trajets = pd.DataFrame(
            [{"nom": k, "duree_min": int(v)} for k, v in trajets_dict.items()]
            or [{"nom": "", "duree_min": 0}]
        )
        edited_trajets = st.data_editor(
            df_trajets,
            num_rows="dynamic",
            width="stretch",
            hide_index=True,
            column_config={
                "nom": st.column_config.TextColumn(
                    "Trajet", required=True,
                    help="Ex. : « Appartement-Fac », « Strasbourg-Luxembourg »",
                ),
                "duree_min": st.column_config.NumberColumn(
                    "Durée (minutes)", min_value=0, max_value=600, step=5,
                    required=True,
                ),
            },
            key="profil_trajets_editor",
        )
        trajets_brutes = edited_trajets.to_dict(orient="records")

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

    # === Section 6 — Paramètres IA (Gemini & DeepSeek) ===============================
    with st.expander("🤖 Paramètres IA (Gemini & DeepSeek)", expanded=is_new):
        st.caption(
            "🔒 La clé API est **chiffrée** (Fernet AES-128) avant stockage en base. "
            "Elle n'est utilisée que pour les appels à l'API Google Gemini ou DeepSeek "
            "(analyse de PDF et génération de planning)."
        )

        existing_key = data["gemini_api_key"]
        if existing_key:
            st.markdown(
                f"🔐 Clé configurée : `{mask_for_display(existing_key)}` — "
                "laisse vide pour conserver, ou colle une nouvelle clé pour la remplacer."
            )
            if data.get("gemini_api_key_encrypted_was_legacy"):
                st.warning(
                    "⚠️ Cette clé était stockée en clair (avant cette version). "
                    "Elle sera **chiffrée automatiquement** au prochain « Enregistrer »."
                )
        api_key_input = st.text_input(
            "Clé API LLM (Google ou DeepSeek)",
            value="",
            type="password",
            placeholder="AIza... ou sk-..." if not existing_key else "•••••••• (laisser vide pour conserver)",
            help="Récupère ta clé sur https://aistudio.google.com/apikey ou https://platform.deepseek.com",
        )
        # Logique : champ vide = on garde l'existante (mais en re-chiffrant si legacy)
        api_key = api_key_input.strip() if api_key_input.strip() else existing_key

        # Si le modèle stocké n'est plus dans la liste, on l'ajoute pour ne pas perdre l'info
        models_options = list(MODELES_IA)
        if data["gemini_model"] not in models_options:
            models_options.insert(0, data["gemini_model"])

        gemini_model = st.selectbox(
            "Modèle IA",
            options=models_options,
            index=models_options.index(data["gemini_model"]),
            help="**Gemini Flash** = ultra-rapide. **DeepSeek-V4-Pro** = raisonnement très profond.",
        )

        col_btn, col_msg = st.columns([1, 3])
        with col_btn:
            test_clicked = st.button(
                "🔌 Tester la connexion", width="stretch"
            )
        if test_clicked:
            with col_msg:
                with st.spinner("Test en cours…"):
                    ok, msg = test_llm_connection(api_key, gemini_model)
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

    # === Sauvegarde & Restauration =========================================
    st.divider()
    with st.expander("💾 Sauvegarde & Restauration", expanded=False):
        st.caption(
            "Streamlit Cloud peut perdre les fichiers locaux en cas de "
            "redéploiement d'instance. **Télécharge ta sauvegarde régulièrement** "
            "(une fois par semaine suffit) pour protéger ton avancement Leitner, "
            "ton XP, tes chapitres et tes PDFs analysés."
        )

        col_dl, col_restore = st.columns(2)

        with col_dl:
            st.markdown("##### 📤 Télécharger une sauvegarde")
            try:
                from services.backup_service import create_backup_zip, make_backup_filename
                backup_bytes = create_backup_zip()
                st.download_button(
                    label=f"💾 Télécharger ({len(backup_bytes) / 1024:.0f} Ko)",
                    data=backup_bytes,
                    file_name=make_backup_filename(),
                    mime="application/zip",
                    type="primary",
                    width="stretch",
                    help="Zip contenant planning.db + tous les PDFs + un MANIFEST.",
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"❌ Erreur lors de la préparation : {exc}")

        with col_restore:
            st.markdown("##### 📥 Restaurer une sauvegarde")
            uploaded = st.file_uploader(
                "Fichier .zip de sauvegarde",
                type=["zip"],
                key="backup_restore_uploader",
                help="Doit être un zip produit par cette même app.",
            )
            if uploaded is not None:
                st.warning(
                    "⚠️ **Action destructive** : ta base actuelle et tes PDFs "
                    "seront ÉCRASÉS par le contenu de la sauvegarde."
                )
                confirm = st.checkbox(
                    "Je confirme vouloir tout remplacer",
                    key="backup_restore_confirm",
                )
                if confirm and st.button(
                    "📥 Restaurer maintenant",
                    type="primary", width="stretch",
                    key="backup_restore_btn",
                ):
                    try:
                        from services.backup_service import restore_from_zip
                        resultat = restore_from_zip(uploaded.getvalue())
                        st.success(
                            f"✅ Sauvegarde restaurée — DB rétablie, "
                            f"{resultat['nb_pdfs']} PDF(s) restauré(s). "
                            "L'app va se recharger."
                        )
                        st.toast("Restauration réussie", icon="✅")
                        st.rerun()
                    except ValueError as exc:
                        st.error(f"❌ Fichier invalide : {exc}")
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"❌ Erreur lors de la restauration : {exc}")

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

    # --- Validation des trajets habituels (avec déduplication explicite) ---
    trajets_valides: dict[str, Any] = {}
    doublons: list[str] = []
    for t in trajets_brutes:
        nom_trajet = (t.get("nom") or "").strip()
        duree = t.get("duree_min")
        if not nom_trajet or duree in (None, ""):
            continue
        try:
            duree_int = int(duree)
        except (TypeError, ValueError):
            continue
        if duree_int <= 0:
            continue
        if nom_trajet in trajets_valides:
            doublons.append(nom_trajet)
        trajets_valides[nom_trajet] = {
            "duree_min": duree_int,
            "mode": str(t.get("mode", "Transport en commun")),
            "fatigue": str(t.get("fatigue", "Modérée")),
            "etude_possible": bool(t.get("etude_possible", False)),
        }

    if doublons:
        with col_save_msg:
            st.warning(
                f"ℹ️ Trajets dupliqués (la dernière valeur a été gardée) : "
                f"{', '.join(set(doublons))}"
            )

    payload = {
        "nom": (nom or "").strip(),
        "prenom": (nom or "").strip(),
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
        "heures_etude_cible_par_semaine": float(heures_etude_cible_par_semaine),
        "heures_etude_plafond_par_jour": float(heures_etude_plafond_par_jour),
        "temps_transport_min": int(temps_transport),
        "trajets_habituels": trajets_valides,
        "nb_repas_par_jour": int(nb_repas),
        "duree_repas_min": int(duree_repas),
        "duree_prep_repas_min": int(duree_prep_repas),
        "besoin_sieste": bool(besoin_sieste),
        "duree_sieste_min": int(duree_sieste),
        "contraintes_fixes": contraintes_validees,
        "gemini_api_key": (api_key or "").strip(),
        "gemini_model": gemini_model,
    }

    # --- Validation biométrique stricte (6 invariants) ---
    bio_errors = validate_biometrie(payload)
    bio_blocking = [e for e in bio_errors if e.severite == "error"]
    bio_warnings = [e for e in bio_errors if e.severite == "warning"]
    if bio_blocking:
        with col_save_msg:
            for err in bio_blocking:
                st.error(f"**{err.champ}** — {err.message}")
        return
    if bio_warnings:
        with col_save_msg:
            for warn in bio_warnings:
                st.warning(f"**{warn.champ}** — {warn.message}")

    try:
        save_profil(payload)
    except Exception as exc:
        with col_save_msg:
            st.error(f"Erreur lors de l'enregistrement : {exc}")
        return

    with col_save_msg:
        st.success("✅ Utilisateur enregistré.")
    st.toast("Utilisateur enregistré", icon="✅")

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
        "heures_etude_cible_par_semaine": 21.0,
        "heures_etude_plafond_par_jour": 6.0,
        "temps_transport_min": 0,
        "trajets_habituels": {},
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
