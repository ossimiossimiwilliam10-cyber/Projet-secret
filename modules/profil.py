"""Onglet **Utilisateur etudiant**.

Le profil est un singleton applicatif : une seule ligne dans la table ``profil``.
Le formulaire est divise en 5 sections expansibles :

1. Identite & rythme
2. Capacite de travail
3. Contraintes fixes recurrentes
4. Transport & Lieux
5. Sante & alimentation
6. Parametres IA (DeepSeek)

La cle API DeepSeek est chiffree (Fernet AES-128) avant stockage en base.
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

# Fusion chronotype + pic de concentration en un seul champ intuitif.
# Mapping : cle -> (chronotype, pic_concentration)
PRODUCTIVITE: dict[str, tuple[str, str]] = {
    "matin":       ("leve_tot",       "matin"),
    "apres_midi":  ("intermediaire",  "apres_midi"),
    "soir":        ("couche_tard",    "soir"),
}
PRODUCTIVITE_LABELS: dict[str, str] = {
    "matin":       "🌅 Matin (leve-tot)",
    "apres_midi":  "☀️ Apres-midi",
    "soir":        "🌙 Soir (couche-tard)",
}

METHODES_TRAVAIL: dict[str, str] = {
    "pomodoro": "Pomodoro (25/5)",
    "blocs_longs": "Blocs longs (1h30 - 2h)",
    "mixte": "Mixte - adapte a la tache",
}

CAPACITE_WEEKEND: dict[str, str] = {
    "plein": "Oui, a plein regime",
    "partiel": "Partiellement (samedi OU dimanche)",
    "non": "Non - le week-end est sacre",
}

TOLERANCE_FATIGUE: dict[str, str] = {
    "faible": "Faible - je m'epuise vite",
    "moyenne": "Moyenne",
    "elevee": "Elevee - j'encaisse bien",
}

MODELES_IA: list[str] = [
    "deepseek-v4-pro",
]


# ---------------------------------------------------------------------------
# Acces BD - detache de la session pour eviter les soucis de lazy-load Streamlit
# ---------------------------------------------------------------------------
def load_profil() -> dict[str, Any]:
    """Charge le profil sous forme de dict pur.

    Retourne ``{}`` s'il n'existe pas encore - c'est le marqueur "premiere
    utilisation".

    Optimisations :
    - Eager loading des 4 sous-configs (1 requete au lieu de 5).
    - Dechiffrement automatique de la cle API DeepSeek.
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

        # Dechiffrement transparent de la cle API (legacy en clair gere)
        key_stored = p.systeme.gemini_api_key or ""
        key_clear = decrypt_api_key(key_stored)

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
            "nb_repas_par_jour": int(p.logistique.nb_repas_par_jour or 3),
            "duree_repas_min": int(p.logistique.duree_repas_min or 30),
            "duree_prep_repas_min": int(p.logistique.duree_prep_repas_min or 30),
            "besoin_sieste": bool(p.biometrie.besoin_sieste),
            "duree_sieste_min": int(p.biometrie.duree_sieste_min or 20),
            "contraintes_fixes": list(p.logistique.contraintes_fixes or []),
            "transport": _load_transport_config(dict(p.logistique.trajets_habituels or {})),
            "deepseek_api_key": key_clear,
            "deepseek_api_key_encrypted_was_legacy": (
                bool(key_stored) and not is_encrypted(key_stored)
            ),
            "deepseek_model": p.systeme.gemini_model or "deepseek-v4-pro",
        }


_GAMIFICATION_ATTRS = frozenset([
    "xp", "niveau", "streak_jours", "streak_record", "derniere_activite_xp",
    "nb_quiz_total", "nb_chapitres_maitrise", "nb_seances_sport_total",
])
_SYSTEME_ATTRS = frozenset([
    "gemini_api_key", "gemini_model", "google_maps_api_key", "replanning_auto_actif",
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
# Champs internes qui ne doivent jamais etre ecrits en base.
_TRANSIENT_KEYS = frozenset(["id", "deepseek_api_key_encrypted_was_legacy"])


def save_profil(data: dict[str, Any]) -> None:
    """Upsert du profil (singleton).

    - Chiffre la cle API DeepSeek avant ecriture.
    - Flush apres ``add()`` pour synchroniser les FK des sous-configs.
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
            # Chiffrement transparent de la cle DeepSeek avant persistance.
            if key == "gemini_api_key":
                value = encrypt_api_key(value)
            # Mapping : cle UI "transport" → colonne DB "trajets_habituels"
            if key == "transport":
                key = "trajets_habituels"
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
# Test de connexion DeepSeek
# ---------------------------------------------------------------------------
_RETRYABLE_HINTS = ("timeout", "timed out", "503", "502", "504", "connection reset")


def _is_retryable_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(h in msg for h in _RETRYABLE_HINTS)


def _test_deepseek_connection(api_key: str, model: str, max_retries: int = 3) -> tuple[bool, str]:
    """Test minimal de l'API DeepSeek (compatible OpenAI)."""
    if not api_key.strip():
        return False, "Cle API vide."

    try:
        import openai
    except ImportError:
        return False, "Package `openai` non installe. Lance : pip install openai"

    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            client = openai.OpenAI(
                api_key=api_key.strip(),
                base_url="https://api.deepseek.com/v1",
            )
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Dis juste OK"}],
                max_tokens=50,
            )
            text = resp.choices[0].message.content or ""
            if not text.strip():
                text = getattr(resp.choices[0].message, "reasoning_content", "") or ""
            if text.strip():
                return True, f"Connexion DeepSeek reussie. Reponse : << {text.strip()[:80]} >>"
            return True, "Connexion DeepSeek OK - cle valide, modele operationnel ✅"
        except Exception as exc:
            last_exc = exc
            msg = str(exc)[:300]
            if "401" in msg or "403" in msg or "unauthor" in msg.lower() or "invalid" in msg.lower():
                return False, f"🔐 Cle DeepSeek refusee. Verifie ta cle sur platform.deepseek.com.\n\nDetail : {msg}"
            if "404" in msg:
                return False, f"❓ Modele DeepSeek introuvable (`{model}`).\n\nDetail : {msg}"
            if "429" in msg or "quota" in msg.lower() or "rate" in msg.lower():
                return False, f"⏱️ Quota DeepSeek depasse. Reessaie dans quelques minutes.\n\nDetail : {msg}"
            if attempt < max_retries - 1 and _is_retryable_error(exc):
                _time_mod.sleep(2 ** attempt)
                continue
            return False, f"Echec DeepSeek : {type(exc).__name__} - {msg}"

    assert last_exc is not None
    return False, f"Echec DeepSeek apres {max_retries} tentatives."


# ---------------------------------------------------------------------------
# Rendu Streamlit
# ---------------------------------------------------------------------------
def render() -> None:
    """Point d'entree appele par ``st.Page``."""

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
            st.caption(f"✨ {prog['xp_dans_palier']:,} / {prog['xp_palier_taille']:,} XP")
            st.progress(ratio)
        with col_sport:
            st.metric("🏋️ Seances Sport", nb_seances_sport)
        st.divider()

    st.title("👤 Utilisateur etudiant")
    st.caption(
        "Ces reglages alimentent l'IA a chaque generation de planning. "
        "Pas besoin de tout remplir d'un coup - tu peux y revenir."
    )

    # Bandeau velocite - adapte a la confiance de l'echantillon.
    if velocite_result is not None:
        pct = int(velocite_result.multiplicateur * 100)
        if velocite_result.confiance == "aucune":
            st.caption(f"📊 {velocite_result.message}")
        elif velocite_result.confiance == "faible":
            st.warning(
                f"📊 **Velocite provisoire : {pct}%** (echantillon faible) - "
                f"{velocite_result.message}"
            )
        else:
            if pct >= 100:
                st.success(f"**Velocite historique : {pct}%** - {velocite_result.message}")
            elif pct >= 80:
                st.warning(f"**Velocite historique : {pct}%** - {velocite_result.message}")
            else:
                st.error(f"**Velocite historique : {pct}%** - {velocite_result.message}")

    data = load_profil()
    is_new = not data  # profil vide -> premiere utilisation
    if is_new:
        st.info(
            "👋 **Premiere utilisation detectee.** "
            "Remplis ton profil, puis clique sur **Enregistrer** en bas de page."
        )
        data = _defaults()

    # --- Afficher les messages persistants (restauration, etc.) ---
    for msg_key in ("restore_msg",):
        msg = st.session_state.pop(msg_key, None)
        if msg:
            level, text = msg
            if level == "error":
                st.error(text)
            elif level == "warning":
                st.warning(text)
            elif level == "success":
                st.success(text)
            else:
                st.info(text)

    # === Section 1 - Identite & rythme =====================================
    with st.expander("🌅 Identite & rythme", expanded=is_new):
        col1, col2 = st.columns(2)
        with col1:
            nom = st.text_input(
                "Nom", value=data["nom"], placeholder="Ex: Dupont"
            )
            prenom = st.text_input(
                "Prenom", value=data.get("prenom", ""), placeholder="Ex: Jean"
            )
            heure_lever = st.time_input(
                "Heure de lever habituelle", value=data["heure_lever"]
            )
        with col2:
            heure_coucher = st.time_input(
                "Heure de coucher habituelle", value=data["heure_coucher"]
            )
            heures_sommeil = st.slider(
                "Heures de sommeil cible",
                min_value=5.0, max_value=10.0,
                value=data["heures_sommeil_cible"], step=0.5,
            )
            # Mapping inverse : trouver la cle a partir des valeurs stockees
            default_key = "matin"
            mapping_trouve = False
            for key, (chrono_val, pic_val) in PRODUCTIVITE.items():
                if chrono_val == data.get("chronotype") and pic_val == data.get("pic_concentration"):
                    default_key = key
                    mapping_trouve = True
                    break
            if not mapping_trouve and data.get("chronotype"):
                st.warning(
                    f"⚠️ Combinaison chronotype/pic inconnue "
                    f"({data.get('chronotype')}/{data.get('pic_concentration')}). "
                    f"Réinitialisé sur « Matin ». Vérifie ton choix ci-dessous."
                )
                default_key = "matin"
            # Sécurité : si la clé n'est pas dans le mapping (corruption DB)
            if default_key not in PRODUCTIVITE_LABELS:
                default_key = "matin"
            productivite_choisie = st.radio(
                "Je suis le plus productif...",
                options=list(PRODUCTIVITE_LABELS.keys()),
                format_func=lambda k: PRODUCTIVITE_LABELS[k],
                index=list(PRODUCTIVITE_LABELS.keys()).index(default_key),
                horizontal=True,
            )
            # Deriver les deux valeurs
            chronotype, pic_concentration = PRODUCTIVITE[productivite_choisie]

    # === Section 2 - Capacite de travail ===================================
    with st.expander("💪 Capacite de travail", expanded=is_new):
        col1, col2 = st.columns(2)
        with col1:
            methode_travail = st.selectbox(
                "Methode de travail preferee",
                options=list(METHODES_TRAVAIL.keys()),
                format_func=lambda k: METHODES_TRAVAIL[k],
                index=list(METHODES_TRAVAIL.keys()).index(data["methode_travail"])
                if data.get("methode_travail") in METHODES_TRAVAIL else 0,
            )
            # Si Pomodoro, force le slider a une valeur coherente (25 min)
            if methode_travail == "pomodoro":
                st.session_state["profil_duree_max_session"] = min(
                    data.get("duree_max_session_min", 25), 30
                )
            duree_max_session = st.slider(
                "Duree maximale d'une session sans pause (min)",
                min_value=20, max_value=120,
                value=st.session_state.get("profil_duree_max_session", data["duree_max_session_min"]),
                step=5,
                disabled=(methode_travail == "pomodoro"),
                key="profil_duree_max_session",
                help="Avec Pomodoro, la duree est bloquee a 25-30 min maximum.",
            )
            pause_entre_sessions = st.slider(
                "Duree d'une pause entre sessions (min)",
                min_value=5, max_value=20,
                value=data["pause_entre_sessions_min"], step=1,
            )
        with col2:
            tolerance_fatigue = st.selectbox(
                "Tolerance a la fatigue",
                options=list(TOLERANCE_FATIGUE.keys()),
                format_func=lambda k: TOLERANCE_FATIGUE[k],
                index=list(TOLERANCE_FATIGUE.keys()).index(data["tolerance_fatigue"])
                if data.get("tolerance_fatigue") in TOLERANCE_FATIGUE else 0,
            )
            if methode_travail == "pomodoro":
                st.caption("🍅 Mode Pomodoro : sessions de 25-30 min, entrecoupees de courtes pauses.")

        capacite_weekend = st.radio(
            "Capacite de travail le week-end",
            options=list(CAPACITE_WEEKEND.keys()),
            format_func=lambda k: CAPACITE_WEEKEND[k],
            index=list(CAPACITE_WEEKEND.keys()).index(data["capacite_weekend"])
            if data.get("capacite_weekend") in CAPACITE_WEEKEND else 0,
        )

        st.divider()
        st.markdown("##### 🎯 Quota d'etude (cours + revisions perso)")
        st.caption(
            "Definis ton **objectif hebdomadaire** (total d'heures visees sur "
            "la semaine) et ton **plafond journalier** (ne jamais depasser). "
            "L'IA repartira intelligemment ton objectif sur les 7 jours sans "
            "jamais depasser le plafond. Si tu declares ton check-in du jour "
            "fatigue (> 7/10), le plafond est reduit de 30 % pour ce jour-la."
        )
        col_h1, col_h2 = st.columns(2)
        with col_h1:
            heures_etude_cible_par_semaine = st.slider(
                "📆 Objectif hebdo (total)",
                min_value=1.0, max_value=70.0,
                value=float(data["heures_etude_cible_par_semaine"]),
                step=0.5,
                format="%.1f h / semaine",
                help="Ex. : 40 h pour un etudiant en periode d'examens, "
                     "20-25 h en rythme normal avec cours + job.",
            )
        with col_h2:
            heures_etude_plafond_par_jour = st.slider(
                "🛑 Plafond / jour",
                min_value=1.0, max_value=14.0,
                value=float(data["heures_etude_plafond_par_jour"]),
                step=0.5,
                format="%.1f h / jour",
                help="L'IA ne placera jamais plus que ca sur une journee. "
                     "Au-dela de 8 h, garde en tete que la qualite chute.",
            )

        # Validation visuelle : objectif hebdo doit etre atteignable avec
        # le plafond x 7. Sinon l'IA ne pourra jamais le tenir.
        plafond_x_7 = heures_etude_plafond_par_jour * 7
        if heures_etude_cible_par_semaine > plafond_x_7:
            st.error(
                f"⚠️ Incoherence : ton objectif ({heures_etude_cible_par_semaine:.1f} h) "
                f"est superieur au plafond multiplie par 7 jours "
                f"({plafond_x_7:.1f} h). L'IA ne pourra pas l'atteindre. "
                f"Augmente le plafond ou baisse l'objectif."
            )
        elif heures_etude_cible_par_semaine > plafond_x_7 * 0.9:
            st.warning(
                f"ℹ️ Ton objectif ({heures_etude_cible_par_semaine:.1f} h) est "
                f"tres proche du maximum theorique ({plafond_x_7:.1f} h). "
                f"L'IA aura peu de marge pour ajuster en cas d'imprevu."
            )

    # === Section 3 - Contraintes fixes recurrentes =========================
    contraintes_brutes: list[dict] = []  # défini même si expander fermé
    with st.expander("📌 Contraintes fixes recurrentes", expanded=is_new):
        st.caption(
            "Creneaux bloques **chaque semaine** : cours en presentiel, job etudiant, "
            "sport en club... Ces blocs seront verrouilles dans tous les plannings "
            "generes."
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
                    "Debut (HH:MM)", required=True,
                    help="Format 24 h, ex. : 08:30",
                ),
                "heure_fin": st.column_config.TextColumn(
                    "Fin (HH:MM)", required=True,
                    help="Format 24 h, ex. : 10:30",
                ),
                "libelle": st.column_config.TextColumn(
                    "Libelle", required=True,
                    help="Ex. : << TD Droit >>, << Job etudiant >>",
                ),
                "lieu": st.column_config.TextColumn(
                    "Lieu", required=False,
                    help="Ex. : << Fac >>, << Luxembourg >>",
                ),
            },
            key="profil_contraintes_editor",
        )
        contraintes_brutes = edited.to_dict(orient="records")

    # === Section 4 - Transport & Lieux ======================================
    transport_config: dict[str, Any] = data.get("transport", {})  # défini même si expander fermé
    with st.expander("🚌 Transport & Lieux", expanded=is_new):
        st.caption(
            "Definis tes lieux et les temps de trajet entre eux. "
            "L'IA utilisera ces durees pour caler tes deplacements dans le planning."
        )

        transport = data.get("transport", {})
        lieux: list[str] = transport.get("lieux", [])
        trajets: dict[str, int] = transport.get("trajets", {})
        bidirectionnel: bool = transport.get("bidirectionnel", True)
        mode_principal: str = transport.get("mode", "transit")
        lieu_principal: str = transport.get("lieu_principal", "")

        # --- Edition des lieux ---
        st.markdown("##### 📍 Mes lieux")
        df_lieux = pd.DataFrame({"lieu": pd.Series(lieux, dtype="string")} if lieux else {"lieu": pd.Series([], dtype="string")})
        edited_lieux = st.data_editor(
            df_lieux,
            num_rows="dynamic",
            width="stretch",
            hide_index=True,
            column_config={
                "lieu": st.column_config.TextColumn(
                    "Nom du lieu", required=True,
                    help="Ex: Appartement, Fac, Salle de sport, Gare",
                ),
            },
            key="profil_lieux_v3",
        )
        nouveaux_lieux: list[str] = []
        for _, row in edited_lieux.iterrows():
            l = str(row.get("lieu") or "").strip()
            if l and l not in nouveaux_lieux:
                nouveaux_lieux.append(l)

        # --- Options ---
        col_opt1, col_opt2, col_opt3 = st.columns(3)
        with col_opt1:
            modes = {"transit": "🚌 Transports", "driving": "🚗 Voiture", "bicycling": "🚲 Velo", "walking": "🚶 A pied"}
            mode_principal = st.selectbox(
                "Mode principal", options=list(modes.keys()),
                format_func=lambda k: modes[k],
                index=list(modes.keys()).index(mode_principal) if mode_principal in modes else 0,
                key="transport_mode",
            )
        with col_opt2:
            bidirectionnel = st.checkbox("🔄 Bidirectionnel (A→B = B→A)", value=bidirectionnel, key="transport_bidi")
        with col_opt3:
            lieu_options = ["(aucun)"] + nouveaux_lieux
            lieu_idx = 0
            if lieu_principal and lieu_principal in nouveaux_lieux:
                lieu_idx = nouveaux_lieux.index(lieu_principal) + 1
            lieu_principal = st.selectbox(
                "⭐ Lieu principal", options=lieu_options, index=lieu_idx, key="transport_home",
            )
            if lieu_principal == "(aucun)":
                lieu_principal = ""

        # --- Matrice des temps ---
        # Fusion : partir des valeurs existantes, surchargées par les widgets.
        nouveaux_trajets: dict[str, int] = dict(trajets)
        if len(nouveaux_lieux) >= 2:
            st.markdown("##### ⏱️ Temps de trajet (minutes)")

            # Quick-fill
            col_fill, _ = st.columns([1, 3])
            with col_fill:
                default_min = st.number_input("Remplissage rapide (min)", min_value=1, max_value=300, value=20, step=5, key="transport_fill_val")
                if st.button("⚡ Appliquer aux trajets non definis", key="transport_fill_btn"):
                    for i, a in enumerate(nouveaux_lieux):
                        for b in nouveaux_lieux[i+1:]:
                            k = f"{a}↔{b}"
                            if k not in nouveaux_trajets or nouveaux_trajets.get(k, 0) == 0:
                                nouveaux_trajets[k] = int(default_min)
                                # Injecter dans le session_state pour que la valeur
                                # survive au st.rerun() et s'affiche dans le widget.
                                st.session_state[f"trajet_v4_{k}"] = int(default_min)
                    st.rerun()

            for i, lieu_a in enumerate(nouveaux_lieux):
                for lieu_b in nouveaux_lieux[i+1:]:
                    key_ab = f"{lieu_a}↔{lieu_b}"
                    key_ba = f"{lieu_b}↔{lieu_a}"
                    existing = nouveaux_trajets.get(key_ab, 0)

                    if bidirectionnel:
                        duree = st.number_input(
                            f"{lieu_a} ↔ {lieu_b}",
                            min_value=0, max_value=600, step=5,
                            value=int(existing) if existing else 0,
                            key=f"trajet_v4_{key_ab}",
                        )
                        if duree > 0:
                            nouveaux_trajets[key_ab] = int(duree)
                            nouveaux_trajets[key_ba] = int(duree)
                        else:
                            nouveaux_trajets.pop(key_ab, None)
                            nouveaux_trajets.pop(key_ba, None)
                    else:
                        col_a, col_b = st.columns(2)
                        with col_a:
                            d_ab = st.number_input(
                                f"{lieu_a} → {lieu_b}",
                                min_value=0, max_value=600, step=5,
                                value=int(nouveaux_trajets.get(key_ab, 0)),
                                key=f"trajet_v4_{key_ab}",
                            )
                        with col_b:
                            d_ba = st.number_input(
                                f"{lieu_b} → {lieu_a}",
                                min_value=0, max_value=600, step=5,
                                value=int(nouveaux_trajets.get(key_ba, 0)),
                                key=f"trajet_v4_{key_ba}",
                            )
                        if d_ab > 0:
                            nouveaux_trajets[key_ab] = int(d_ab)
                        else:
                            nouveaux_trajets.pop(key_ab, None)
                        if d_ba > 0:
                            nouveaux_trajets[key_ba] = int(d_ba)
                        else:
                            nouveaux_trajets.pop(key_ba, None)
        else:
            if nouveaux_lieux:
                st.info("Ajoute au moins 2 lieux pour definir des trajets.")
            # Nettoyage : ne garder que les trajets X↔Y / X→Y dont les deux lieux existent
            lieux_set = set(nouveaux_lieux)
            nouveaux_trajets = {
                k: v for k, v in trajets.items()
                if _trajet_valide(k, lieux_set)
            }

        # Nettoyage final : supprimer tout trajet dont au moins un lieu est absent
        lieux_set_final = set(nouveaux_lieux)
        nouveaux_trajets = {
            k: v for k, v in nouveaux_trajets.items()
            if _trajet_valide(k, lieux_set_final)
        }

        # Assembler la config transport
        transport_config = {
            "lieux": nouveaux_lieux,
            "trajets": nouveaux_trajets,
            "mode": mode_principal,
            "bidirectionnel": bidirectionnel,
            "lieu_principal": lieu_principal,
        }

    # === Section 5 - Sante & alimentation =================================
    with st.expander("🍽️ Sante & alimentation", expanded=is_new):
        col1, col2 = st.columns(2)
        with col1:
            nb_repas = st.number_input(
                "Nombre de repas par jour",
                min_value=1, max_value=5,
                value=data.get("nb_repas_par_jour", 3), step=1,
            )
            duree_repas = st.number_input(
                "Duree moyenne d'un repas (min)",
                min_value=10, max_value=120,
                value=data.get("duree_repas_min", 30), step=5,
            )
            duree_prep_repas = st.number_input(
                "Temps de preparation des repas par jour (min)",
                min_value=0, max_value=180,
                value=data.get("duree_prep_repas_min", 30), step=5,
            )
        with col2:
            besoin_sieste = st.checkbox(
                "Besoin d'une sieste quotidienne",
                value=data.get("besoin_sieste", False),
            )
            duree_sieste = st.number_input(
                "Duree de la sieste (min)",
                min_value=10, max_value=90,
                value=data.get("duree_sieste_min", 20), step=5,
                disabled=not besoin_sieste,
            )

    # === Section 6 - Parametres IA (DeepSeek) ==============================
    with st.expander("🤖 Parametres IA (DeepSeek)", expanded=is_new):
        st.caption(
            "🔒 La cle API est **chiffree** (Fernet AES-128) avant stockage en base. "
            "Elle n'est utilisee que pour les appels a l'API DeepSeek "
            "(analyse de PDF et generation de planning)."
        )

        existing_key = data.get("deepseek_api_key", "")
        delete_key = False
        if existing_key:
            st.markdown(
                f"🔐 Cle configuree : `{mask_for_display(existing_key)}` - "
                "laisse vide pour conserver, ou colle une nouvelle cle pour la remplacer."
            )
            if data.get("deepseek_api_key_encrypted_was_legacy"):
                st.warning(
                    "⚠️ Cette cle etait stockee en clair (avant cette version). "
                    "Elle sera **chiffree automatiquement** au prochain << Enregistrer >>."
                )
            delete_key = st.checkbox("🗑️ Supprimer la cle existante", key="delete_api_key")
            if delete_key:
                api_key_input = ""
                st.caption("⚠️ La cle sera definitivement supprimee au prochain enregistrement.")
            else:
                # Bouton pour révéler/masquer la clé
                show_key = st.checkbox("👁️ Afficher la cle", key="show_api_key")
                api_key_input = st.text_input(
                    "Cle API DeepSeek",
                    value=existing_key if show_key else "",
                    type="default" if show_key else "password",
                    placeholder="........ (laisser vide pour conserver)",
                    help="Recupere ta cle sur https://platform.deepseek.com",
                )
        else:
            api_key_input = st.text_input(
                "Cle API DeepSeek",
                value="",
                type="password",
                placeholder="sk-...",
                help="Recupere ta cle sur https://platform.deepseek.com",
            )
        # Logique : suppression explicite => vide, sinon champ vide = on garde l'existante
        if delete_key:
            api_key = ""
        else:
            api_key = api_key_input.strip() if api_key_input.strip() else existing_key

        # Si le modele stocke n'est plus dans la liste, on l'ajoute pour ne pas perdre l'info
        stored_model = data.get("deepseek_model", "deepseek-v4-pro")
        models_options = list(MODELES_IA)
        if stored_model not in models_options:
            models_options.insert(0, stored_model)

        deepseek_model = st.selectbox(
            "Modele IA",
            options=models_options,
            index=models_options.index(stored_model) if stored_model in models_options else 0,
            help="**DeepSeek-V4-Pro** = raisonnement tres profond.",
        )

        st.divider()
        test_clicked = st.button("🔌 Tester DeepSeek", width="stretch")
        if test_clicked:
            with st.spinner("Test DeepSeek..."):
                ok, msg = _test_deepseek_connection(api_key, deepseek_model)
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
            "redeploiement d'instance. **Telecharge ta sauvegarde regulierement** "
            "(une fois par semaine suffit) pour proteger ton avancement Leitner, "
            "ton XP, tes chapitres et tes PDFs analyses."
        )

        col_dl, col_restore = st.columns(2)

        with col_dl:
            st.markdown("##### 📤 Telecharger une sauvegarde")
            try:
                from services.backup_service import create_backup_zip, make_backup_filename
                backup_bytes = create_backup_zip()
                st.download_button(
                    label=f"💾 Telecharger ({len(backup_bytes) / 1024:.0f} Ko)",
                    data=backup_bytes,
                    file_name=make_backup_filename(),
                    mime="application/zip",
                    type="primary",
                    width="stretch",
                    help="Zip contenant planning.db + tous les PDFs + un MANIFEST.",
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"❌ Erreur lors de la preparation : {exc}")

        with col_restore:
            st.markdown("##### 📥 Restaurer une sauvegarde")
            uploaded = st.file_uploader(
                "Fichier .zip de sauvegarde",
                type=["zip"],
                key="backup_restore_uploader",
                help="Doit etre un zip produit par cette meme app.",
            )
            if uploaded is not None:
                st.warning(
                    "⚠️ **Action destructive** : ta base actuelle et tes PDFs "
                    "seront ECRASES par le contenu de la sauvegarde."
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
                        # Vider TOUS les caches Streamlit pour que le rechargement
                        # lise bien la nouvelle DB (sinon l'ancien profil fantôme
                        # reste affiché).
                        st.cache_data.clear()
                        st.cache_resource.clear()
                        st.session_state["restore_msg"] = ("success",
                            f"✅ Sauvegarde restauree - DB retablie, "
                            f"{resultat['nb_pdfs']} PDF(s) restaure(s). "
                            "L'app va se recharger."
                        )
                        st.rerun()
                    except ValueError as exc:
                        st.session_state["restore_msg"] = ("error", f"❌ Fichier invalide : {exc}")
                        st.rerun()
                    except Exception as exc:
                        st.session_state["restore_msg"] = ("error", f"❌ Erreur lors de la restauration : {exc}")
                        st.rerun()

    # === Zone de danger (Phase de test) ===================================
    st.divider()
    with st.expander("🧨 Zone de danger (Reinitialisation)"):
        st.warning(
            "Tu es en phase de test ? Ce bouton effacera absolument TOUT : "
            "ton profil, tes cours, tes PDFs importes et tous les plannings generes. "
            "Action irreversible."
        )
        confirm_reset = st.checkbox(
            "⚠️ Je confirme vouloir TOUT effacer",
            key="reset_confirm",
        )
        if confirm_reset and st.button("🗑️ Reinitialiser toute l'application", type="primary", width='stretch'):
            from database.db import reset_db
            reset_db()
            st.cache_data.clear()
            st.cache_resource.clear()
            st.success("💥 Base de donnees et PDFs effaces avec succes. L'application redemarre...")
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
        lieu = (c.get("lieu") or "").strip()

        if not any([jour, hd, hf, lib]):
            continue
        if not all([jour, hd, hf, lib]):
            erreurs.append(f"Ligne {i} : jour, debut, fin et libelle sont obligatoires.")
            continue
        if not _is_valid_time(hd) or not _is_valid_time(hf):
            erreurs.append(f"Ligne {i} ({lib}) : format d'heure invalide (HH:MM).")
            continue
        if _to_minutes(hd) >= _to_minutes(hf):
            erreurs.append(f"Ligne {i} ({lib}) : l'heure de fin doit etre apres l'heure de debut.")
            continue
        contraintes_validees.append(
            {"jour": jour, "heure_debut": hd, "heure_fin": hf, "libelle": lib, "lieu": lieu}
        )

    if erreurs:
        with col_save_msg:
            for e in erreurs:
                st.error(e)
        return

    payload = {
        "nom": (nom or "").strip(),
        "prenom": (prenom or "").strip(),
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
        "nb_repas_par_jour": int(nb_repas),
        "duree_repas_min": int(duree_repas),
        "duree_prep_repas_min": int(duree_prep_repas),
        "besoin_sieste": bool(besoin_sieste),
        "duree_sieste_min": int(duree_sieste),
        "contraintes_fixes": contraintes_validees,
        "trajets_habituels": transport_config,
        "gemini_api_key": (api_key or "").strip(),
        "gemini_model": deepseek_model,
        "google_maps_api_key": data.get("google_maps_api_key", ""),
    }

    # --- Validation biometrique stricte (6 invariants) ---
    bio_errors = validate_biometrie(payload)
    bio_blocking = [e for e in bio_errors if e.severite == "error"]
    bio_warnings = [e for e in bio_errors if e.severite == "warning"]
    if bio_blocking:
        with col_save_msg:
            for err in bio_blocking:
                st.error(f"**{err.champ}** - {err.message}")
        return
    if bio_warnings:
        with col_save_msg:
            for warn in bio_warnings:
                st.warning(f"**{warn.champ}** - {warn.message}")

    try:
        save_profil(payload)
    except Exception as exc:
        with col_save_msg:
            st.error(f"Erreur lors de l'enregistrement : {exc}")
        return

    with col_save_msg:
        st.success("✅ Utilisateur enregistre.")
    st.toast("Utilisateur enregistre", icon="✅")


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------
def _defaults() -> dict[str, Any]:
    """Valeurs par defaut pour un profil neuf."""
    return {
        "id": None,
        "nom": "",
        "prenom": "",
        "heure_lever": time(7, 0),
        "heure_coucher": time(23, 30),
        "heures_sommeil_cible": 8.0,
        "chronotype": "leve_tot",
        "pic_concentration": "matin",
        "duree_max_session_min": 50,
        "pause_entre_sessions_min": 10,
        "methode_travail": "mixte",
        "capacite_weekend": "partiel",
        "tolerance_fatigue": "moyenne",
        "heures_etude_cible_par_semaine": 21.0,
        "heures_etude_plafond_par_jour": 6.0,
        "nb_repas_par_jour": 3,
        "duree_repas_min": 30,
        "duree_prep_repas_min": 30,
        "besoin_sieste": False,
        "duree_sieste_min": 20,
        "contraintes_fixes": [],
        "transport": {"lieux": [], "trajets": {}, "mode": "transit", "bidirectionnel": True, "lieu_principal": ""},
        "deepseek_api_key": "",
        "deepseek_model": "deepseek-v4-pro",
    }


def _build_constraints_df(contraintes: list[dict[str, str]]) -> pd.DataFrame:
    """DataFrame avec les colonnes attendues, meme pour une liste vide."""
    cols = ["jour", "heure_debut", "heure_fin", "libelle", "lieu"]
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
    """Convertit ``\"HH:MM\"`` en minutes depuis minuit (suppose ``_is_valid_time`` OK)."""
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _trajet_valide(key: str, lieux: set[str]) -> bool:
    """Un trajet X↔Y ou X→Y est valide si X et Y sont dans ``lieux``."""
    key_normalized = key.replace("→", "↔")
    parts = key_normalized.split("↔")
    return len(parts) == 2 and parts[0] in lieux and parts[1] in lieux


def _load_transport_config(raw: dict) -> dict[str, Any]:
    """Normalise la config transport stockee en base."""
    return {
        "lieux": raw.get("lieux", []),
        "trajets": {str(k): int(v) for k, v in raw.get("trajets", {}).items()},
        "mode": raw.get("mode", "transit"),
        "bidirectionnel": bool(raw.get("bidirectionnel", True)),
        "lieu_principal": raw.get("lieu_principal", ""),
    }
