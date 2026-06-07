"""Salle d'étude (Focus Mode) — révision d'un chapitre spécifique.

Cette page est appelée :
- Depuis le planning (``modules/generation.py``) : clic sur une tâche d'étude
  qui pose ``st.session_state.target_chapitre_id`` puis ``st.switch_page()``.
- Depuis la bibliothèque (à brancher en Phase D) : bouton "Réviser ce chapitre".
- Directement via la sidebar : affiche alors les chapitres dus du jour.

Trois onglets autonomes :
- 📋 Fiche IA : génère/affiche la fiche de révision + zone de notes perso.
- 🔘 QCM     : QCM 4 choix → applique l'algo Leitner.
- ✍️ Quiz   : questions ouvertes → Gemini évalue → applique Leitner.

Après tout quiz/QCM réussi ou raté, propose un bouton « Réajuster ma semaine »
qui appelle ``replan_remaining_week()`` de l'``ai_planner.py``.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from database import Chapitre, Matiere, get_session, session_scope
from services import revision_service as rs
from services.optimistic_lock import ConflictError, update_chapitre_safe
from services.profil_service import get_llm_api_key
from services.gamification_service import (
    attribuer_xp_quiz,
    attribuer_xp_promotion_leitner,
    get_or_create_utilisateur,
    GainXP,
)


# ===========================================================================
# Point d'entrée — appelé par Streamlit en script
# ===========================================================================
def render() -> None:
    chap_id = st.session_state.get("target_chapitre_id")
    if chap_id is None:
        _render_no_chapter_view()
        return

    # Charge le chapitre (read-only). Toute modif passera par session_scope().
    with get_session() as session:
        chap = session.get(Chapitre, chap_id)
        if chap is None:
            st.error(f"⚠️ Chapitre #{chap_id} introuvable. Il a peut-être été supprimé.")
            st.session_state.pop("target_chapitre_id", None)
            if st.button("← Retour"):
                st.rerun()
            return
        matiere = chap.matiere_obj
        # On lit toutes les infos nécessaires AVANT de fermer la session
        chap_snapshot = _snapshot_chapitre(chap, matiere)

    _render_back_button()
    _render_header(chap_snapshot)
    
    st.divider()
    _render_manual_validation(chap_id)
    _render_pomodoro()
    st.divider()
    _render_notes_perso(chap_id, chap_snapshot)


# ===========================================================================
# Helpers de présentation
# ===========================================================================
def _snapshot_chapitre(chap: Chapitre, matiere: Matiere | None) -> dict:
    """Capture toutes les infos d'un chapitre dans un dict — survit à la fermeture de la session."""
    label, color = rs.label_couleur_status(chap)
    return {
        "id": chap.id,
        "titre": chap.titre,
        "numero": chap.numero,
        "niveau_actuel": int(chap.niveau_actuel or 0),
        "date_prochaine": chap.date_prochaine,
        "maitrise_pct": float(chap.maitrise_pct or 0),
        "notes": chap.notes or "",
        "version": int(chap.version or 1),
        "matiere_nom": matiere.nom if matiere else "(matière inconnue)",
        "ue_nom": (matiere.ue.nom if matiere and matiere.ue else ""),
        "status_label": label,
        "status_color": color,
    }


def _render_header(snap: dict) -> None:
    """Affiche le breadcrumb, le titre et les 4 métriques principales."""
    breadcrumb = ""
    if snap["ue_nom"]:
        breadcrumb += f"🎓 **{snap['ue_nom']}** → "
    breadcrumb += f"📘 **{snap['matiere_nom']}** → 📑 Chapitre {snap['numero']}"
    st.caption(breadcrumb)
    st.title(f"🧠 {snap['titre']}")

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "📊 Niveau Leitner",
        f"{snap['niveau_actuel']} / {rs.MAX_NIVEAU}",
    )

    if snap["date_prochaine"]:
        delta = (snap["date_prochaine"] - date.today()).days
        if delta == 0:
            delta_str = "aujourd'hui"
        elif delta > 0:
            delta_str = f"dans {delta}j"
        else:
            delta_str = f"{-delta}j de retard"
        c2.metric(
            "📅 Prochaine révision",
            snap["date_prochaine"].strftime("%d/%m/%Y"),
            delta_str,
            delta_color="off",
        )
    else:
        c2.metric("📅 Prochaine révision", "—", "jamais initialisé")

    c3.metric("🎯 Maîtrise", f"{int(snap['maitrise_pct'])} %")

    c4.markdown(
        f"<div style='text-align:center; padding-top:0.8rem;'>"
        f"<span style='color:{snap['status_color']}; font-weight:600; font-size:1.05rem;'>"
        f"{snap['status_label']}</span></div>",
        unsafe_allow_html=True,
    )

def _render_pomodoro() -> None:
    """Affiche le timer Pomodoro intégré."""
    from datetime import datetime
    
    with st.expander("⏱️ Pomodoro (25 min travail / 5 min pause)", expanded=False):
        if "pomodoro_start" not in st.session_state:
            st.session_state.pomodoro_start = None
            st.session_state.pomodoro_mode = "work"
            
        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            if st.button("▶️ Démarrer", key="pomo_start"):
                st.session_state.pomodoro_start = datetime.now()
                st.session_state.pomodoro_mode = "work"
                st.rerun()
        with c2:
            if st.button("⏹️ Arrêter", key="pomo_stop"):
                st.session_state.pomodoro_start = None
                st.rerun()
        with c3:
            if st.button("🔄 Rafraîchir", key="pomo_refresh"):
                st.rerun()

        if st.session_state.pomodoro_start:
            elapsed = (datetime.now() - st.session_state.pomodoro_start).total_seconds()
            total = 25 * 60 if st.session_state.pomodoro_mode == "work" else 5 * 60
            remaining = int(total - elapsed)
            
            if remaining > 0:
                mins, secs = divmod(remaining, 60)
                mode_label = "💪 Travail" if st.session_state.pomodoro_mode == "work" else "☕ Pause"
                st.metric(mode_label, f"{mins:02d}:{secs:02d}")
                st.progress(min(elapsed / total, 1.0))
            else:
                if st.session_state.pomodoro_mode == "work":
                    st.session_state.pomodoro_start = datetime.now()
                    st.session_state.pomodoro_mode = "break"
                else:
                    st.session_state.pomodoro_start = None
                st.rerun()

def _render_manual_validation(chap_id: int) -> None:
    """Interface de validation manuelle pour la méthode des J."""
    st.subheader("✅ Validation de la Révision")
    st.caption("Auto-évalue ta maîtrise pour avancer dans la méthode des J sans faire de quiz.")
    
    c1, c2, c3 = st.columns(3)
    score_to_apply = None
    
    if c1.button("📚 À retravailler", use_container_width=True, help="Je ne m'en souvenais pas (Niveau -1)"):
        score_to_apply = 0.0
    if c2.button("⚡ Correct", use_container_width=True, help="Je m'en souvenais avec un peu d'effort (Niveau maintenu)"):
        score_to_apply = 0.6
    if c3.button("🏆 Excellent", use_container_width=True, help="Parfaitement retenu (Niveau +1)"):
        score_to_apply = 1.0

    if score_to_apply is not None:
        try:
            gains_xp: list[dict] = []
            replanning_auto_fait = False
            with session_scope() as session:
                leitner = rs.appliquer_resultat_quiz(session, chap_id, score_to_apply, mode="manuel")
                profil = get_or_create_utilisateur(session)
                chap_obj = session.get(Chapitre, chap_id)
                g_quiz = attribuer_xp_quiz(session, profil, score_to_apply, chap_obj)
                gains_xp.append({"type": "quiz", "gain": _serialize_gain(g_quiz)})
                if leitner["niveau_apres"] > leitner["niveau_avant"]:
                    g_promo = attribuer_xp_promotion_leitner(
                        session, profil,
                        leitner["niveau_avant"], leitner["niveau_apres"],
                        chap_obj, niveau_max_leitner=rs.MAX_NIVEAU,
                    )
                    if g_promo:
                        gains_xp.append({"type": "promo", "gain": _serialize_gain(g_promo)})
                        
                # A1 : Replanning auto si activé
                if profil.replanning_auto_actif:
                    replanning_auto_fait = _try_replan_auto()

            st.session_state[f"leitner_feedback_{chap_id}"] = {
                "leitner": leitner,
                "gains_xp": gains_xp,
                "replanning_auto_fait": replanning_auto_fait,
            }
            st.rerun()
        except Exception as exc:
            st.error(f"❌ Erreur de validation : {exc}")

    # Display feedback if any
    feedback = st.session_state.get(f"leitner_feedback_{chap_id}")
    if feedback:
        _render_leitner_evolution(feedback["leitner"])
        _render_gains_xp_block(feedback["gains_xp"], feedback["replanning_auto_fait"])
        if st.button("Masquer les résultats"):
            st.session_state.pop(f"leitner_feedback_{chap_id}", None)
            st.rerun()
        st.divider()


def _render_back_button() -> None:
    """Petit bouton retour qui nettoie le state du chapitre courant."""
    col_back, _ = st.columns([1, 6])
    with col_back:
        if st.button("← Retour", key="back_from_session", help="Retour au planning"):
            st.session_state.pop("target_chapitre_id", None)
            # Tentative de redirection ; sinon on rerun sur cette page.
            try:
                st.session_state.active_planif_tab = "✨ Génération du planning"
                st.switch_page("pages/planification.py")
            except Exception:
                st.rerun()


def _render_no_chapter_view() -> None:
    """Vue affichée quand on arrive sur la page sans target_chapitre_id."""
    st.title("🧠 Salle d'étude")
    st.info(
        "👉 Pour réviser un chapitre, **clique sur une tâche d'étude dans ton planning** "
        "ou choisis-en un dans la bibliothèque."
    )

    # Bonus : raccourci vers les chapitres dus aujourd'hui
    with get_session() as session:
        a_reviser = rs.chapitres_a_reviser(session, inclure_jamais_revises=True)[:8]
        # On snapshot les infos avant la fermeture de session
        if a_reviser:
            items = []
            for chap in a_reviser:
                label, color = rs.label_couleur_status(chap)
                items.append({
                    "id": chap.id,
                    "titre": chap.titre,
                    "matiere_nom": chap.matiere_obj.nom if chap.matiere_obj else "Sans matière",
                    "ue_nom": chap.matiere_obj.ue.nom if chap.matiere_obj and chap.matiere_obj.ue else "Sans UE",
                    "niveau": chap.niveau_actuel or 0,
                    "label": label,
                    "color": color,
                })
        else:
            items = []

    if items:
        st.divider()
        st.subheader("📅 Tes chapitres à réviser")
        st.caption("Voici ce qui demande ton attention, classé par UE et Matière.")
        
        # Grouper par UE > Matière
        grouped_items = {}
        for it in items:
            ue = it["ue_nom"]
            mat = it["matiere_nom"]
            if ue not in grouped_items:
                grouped_items[ue] = {}
            if mat not in grouped_items[ue]:
                grouped_items[ue][mat] = []
            grouped_items[ue][mat].append(it)
            
        for ue, mat_dict in grouped_items.items():
            st.markdown(f"### 🎓 {ue}")
            for mat, chaps in mat_dict.items():
                with st.expander(f"📘 {mat} ({len(chaps)} chapitres)", expanded=False):
                    for it in chaps:
                        with st.container(border=True):
                            c1, c2, c3 = st.columns([4, 2, 1])
                            c1.markdown(f"**{it['titre']}**")
                            c2.markdown(
                                f"<div style='color:{it['color']}; padding-top:0.4rem;'>{it['label']} "
                                f"<span style='color:gray;'>· Niv. {it['niveau']}</span></div>",
                                unsafe_allow_html=True,
                            )
                            with c3:
                                if st.button("▶️ Ouvrir", key=f"open_chap_{it['id']}", width='stretch'):
                                    st.session_state.target_chapitre_id = it["id"]
                                    st.rerun()
    else:
        st.divider()
        st.caption("ℹ️ Aucun chapitre dû pour le moment — bravo ! 🎉")


# ===========================================================================
# Notes personnelles
# ===========================================================================
def _render_notes_perso(chap_id: int, snap: dict) -> None:
    """Affiche la zone de notes personnelles."""
    st.subheader("✍️ Mes notes personnelles")
    notes = st.text_area(
        "Notes",
        value=snap["notes"],
        height=300,
        key=f"notes_input_{chap_id}",
        label_visibility="collapsed",
        placeholder="Ce que tu retiens, tes propres exemples, tes confusions...",
    )
    if st.button("💾 Sauvegarder mes notes", key=f"save_notes_{chap_id}", type="primary"):
        try:
            with session_scope() as session:
                update_chapitre_safe(
                    session,
                    chap_id,
                    expected_version=snap["version"],
                    mutate=lambda c: setattr(c, "notes", notes),
                )
            st.toast("Notes sauvegardées ✅")
            st.rerun()
        except ConflictError as exc:
            st.error(
                f"⚠️ Conflit multi-onglets — {exc}\n\n"
                "Recharge la page pour récupérer la dernière version "
                "avant de sauvegarder à nouveau."
            )

def _serialize_gain(g: GainXP) -> dict:
    """Sérialise un GainXP en dict pour stocker dans session_state."""
    return {
        "xp_gagne": g.xp_gagne,
        "raison": g.raison,
        "niveau_avant": g.niveau_avant,
        "niveau_apres": g.niveau_apres,
        "level_up": g.level_up,
        "streak_actuel": g.streak_actuel,
        "streak_continue": g.streak_continue,
        "nouveaux_achievements": [
            {"code": a.code, "icone": a.icone, "nom": a.nom,
             "description": a.description, "rarete": a.rarete}
            for a in g.nouveaux_achievements
        ],
    }


def _try_replan_auto(profil) -> bool:
    """A1 — replanning auto silencieux. Retourne True si fait avec succès."""
    try:
        from services.deterministic_planner import replan_remaining_week_deterministic
        from database.models import Semaine
        from datetime import date as _date
        today = _date.today()
        with session_scope() as session:
            semaine = (
                session.query(Semaine)
                .filter(Semaine.date_debut <= today, Semaine.date_fin >= today)
                .first()
            )
            if not semaine:
                return False
            replan_remaining_week_deterministic(session, profil, semaine)
        return True
    except Exception:
        # On ne plante pas le quiz si le replanning échoue (Gemini down, etc.)
        return False


def _render_leitner_evolution(leitner: dict) -> None:
    """Affiche la card 'Évolution Leitner' après quiz/QCM."""
    delta = leitner.get("delta", 0)
    niveau_apres = leitner.get("niveau_apres", 0)
    date_proch = leitner.get("date_prochaine")
    jours_avant = leitner.get("jours_avant_revision", 0)

    if delta > 0:
        delta_emoji = "📈"
        delta_str = f"+{delta} niveau"
    elif delta < 0:
        delta_emoji = "📉"
        delta_str = f"{delta} niveau"
    else:
        delta_emoji = "➡️"
        delta_str = "niveau stable"

    date_str = date_proch.strftime("%d/%m/%Y") if date_proch else "—"
    st.markdown(
        f"##### {delta_emoji} Évolution Leitner : **Niveau {niveau_apres} / {rs.MAX_NIVEAU}** "
        f"({delta_str})"
    )
    st.caption(f"📅 Prochaine révision suggérée : **{date_str}** (dans {jours_avant}j)")


def _trigger_replan(profil) -> None:
    """Lance replan_remaining_week_deterministic() pour redistribuer les tâches restantes."""
    try:
        from utils.helpers import get_or_create_current_week
        from services.deterministic_planner import replan_remaining_week_deterministic
    except ImportError as exc:
        st.error(f"❌ Impossible d'importer le planner : {exc}")
        return

    with st.spinner("⚙️ Réorganisation des jours restants..."):
        try:
            with session_scope() as session:
                semaine, _, _ = get_or_create_current_week(session, transfer_reported=False)
                replan_remaining_week_deterministic(session, profil, semaine)
        except Exception as exc:
            st.error(f"❌ Erreur de réajustement : {exc}")
            return

    st.success("✅ Planning réajusté !")


# ===========================================================================
# F3a : Affichage des gains XP / achievements / replanning auto
# ===========================================================================
def _render_gains_xp_block(gains_xp: list[dict], replanning_auto_fait: bool) -> None:
    """Affiche un bandeau récapitulatif XP juste après le score du quiz.

    Format :
    - Cumul XP gagné (somme des gains)
    - Détail des achievements débloqués (icones + nom + rareté)
    - Notification du replanning automatique si effectué
    """
    if not gains_xp:
        return

    # Calcul cumul
    xp_total = sum(g["gain"]["xp_gagne"] for g in gains_xp)
    level_up_global = any(g["gain"]["level_up"] for g in gains_xp)
    achievements_tous = [
        ach for g in gains_xp for ach in g["gain"]["nouveaux_achievements"]
    ]
    # Pour le level up, on prend le dernier état (niveau le plus haut atteint)
    niveau_final = max((g["gain"]["niveau_apres"] for g in gains_xp), default=1)
    streak_actuel = max((g["gain"]["streak_actuel"] for g in gains_xp), default=0)

    # Bandeau principal
    cols = st.columns([3, 2, 1])
    with cols[0]:
        st.markdown(
            f"<div style='background:linear-gradient(135deg,#fbbf24,#f59e0b);"
            f"padding:12px 16px;border-radius:8px;color:white;'>"
            f"<div style='font-size:0.85rem;opacity:0.9;'>⭐ XP GAGNÉS</div>"
            f"<div style='font-size:1.8rem;font-weight:700;line-height:1.1;'>+{xp_total}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with cols[1]:
        st.markdown(
            f"<div style='background:#3b82f6;padding:12px 16px;border-radius:8px;color:white;'>"
            f"<div style='font-size:0.85rem;opacity:0.9;'>NIVEAU{' 🆙' if level_up_global else ''}</div>"
            f"<div style='font-size:1.8rem;font-weight:700;line-height:1.1;'>{niveau_final}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with cols[2]:
        st.markdown(
            f"<div style='background:#ef4444;padding:12px 16px;border-radius:8px;color:white;'>"
            f"<div style='font-size:0.85rem;opacity:0.9;'>🔥 STREAK</div>"
            f"<div style='font-size:1.8rem;font-weight:700;line-height:1.1;'>{streak_actuel}j</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    # Détail des raisons (en caption)
    raisons = " · ".join(f"+{g['gain']['xp_gagne']} XP {g['gain']['raison']}" for g in gains_xp)
    st.caption(raisons)

    # Achievements débloqués
    if achievements_tous:
        st.markdown("##### 🏆 Achievement(s) débloqué(s) !")
        st.balloons()
        for ach in achievements_tous:
            couleur = _achievement_couleur(ach.get("rarete", "commun"))
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:12px;"
                f"background:{couleur}15;border-left:4px solid {couleur};"
                f"padding:10px 14px;border-radius:6px;margin-bottom:6px;'>"
                f"<div style='font-size:2rem;'>{ach['icone']}</div>"
                f"<div>"
                f"<div style='font-weight:600;color:{couleur};'>{ach['nom']}</div>"
                f"<div style='font-size:0.85rem;color:#4b5563;'>{ach['description']}</div>"
                f"<div style='font-size:0.7rem;color:#9ca3af;text-transform:uppercase;"
                f"letter-spacing:0.05em;margin-top:2px;'>{ach.get('rarete','commun')}</div>"
                f"</div></div>",
                unsafe_allow_html=True,
            )

    # Notification du replanning auto
    if replanning_auto_fait:
        st.info(
            "📅 **Planning ajusté automatiquement** — Vu ton score, l'IA a "
            "redistribué tes tâches d'étude restantes cette semaine pour "
            "renforcer ce chapitre. Va voir le tableau de bord."
        )


def _achievement_couleur(rarete: str) -> str:
    """Couleur selon la rareté d'un achievement (miroir de RARETE_COULEURS du service)."""
    return {
        "commun": "#9ca3af",
        "peu_commun": "#10b981",
        "rare": "#3b82f6",
        "epique": "#a855f7",
        "legendaire": "#f59e0b",
    }.get(rarete, "#9ca3af")
