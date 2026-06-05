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
from services.profil_service import get_gemini_credentials
from services.gamification_service import (
    attribuer_xp_quiz,
    attribuer_xp_promotion_leitner,
    get_or_create_utilisateur,
    GainXP,
)


# ===========================================================================
# Point d'entrée — appelé par Streamlit en script
# ===========================================================================
def main() -> None:
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
    _render_pomodoro()
    st.divider()

    tab_fiche, tab_flashcards, tab_qcm, tab_quiz, tab_feynman = st.tabs(["📋 Fiche IA", "🗂️ Flashcards", "🔘 QCM", "✍️ Quiz ouvert", "🎙️ Feynman"])
    with tab_fiche:
        _render_fiche_tab(chap_id, chap_snapshot)
    with tab_flashcards:
        _render_flashcards_tab(chap_id, chap_snapshot)
    with tab_qcm:
        _render_qcm_tab(chap_id)
    with tab_quiz:
        _render_quiz_tab(chap_id)
    with tab_feynman:
        _render_feynman_tab(chap_id)


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
        "fiche_ia": chap.fiche_ia,
        "notes": chap.notes or "",
        "version": int(chap.version or 1),
        "has_qcm_cache": bool(chap.qcm_cache),
        "has_quiz_cache": bool(chap.quiz_cache),
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


def _render_back_button() -> None:
    """Petit bouton retour qui nettoie le state du chapitre courant."""
    col_back, _ = st.columns([1, 6])
    with col_back:
        if st.button("← Retour", key="back_from_session", help="Retour au planning"):
            st.session_state.pop("target_chapitre_id", None)
            # Tentative de redirection ; sinon on rerun sur cette page.
            try:
                st.switch_page("generation")
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
                    "matiere_nom": chap.matiere_obj.nom if chap.matiere_obj else "?",
                    "niveau": chap.niveau_actuel or 0,
                    "label": label,
                    "color": color,
                })
        else:
            items = []

    if items:
        st.divider()
        st.subheader("📅 Tes chapitres à réviser")
        st.caption("Voici ce qui demande ton attention. Clique pour ouvrir la session.")
        for it in items:
            with st.container(border=True):
                c1, c2, c3 = st.columns([4, 2, 1])
                c1.markdown(f"**{it['titre']}**  \n_{it['matiere_nom']}_")
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
# Onglet 1 — Fiche IA + Notes perso
# ===========================================================================
def _render_fiche_tab(chap_id: int, snap: dict) -> None:
    """Affiche / génère la fiche IA, plus la zone de notes personnelles."""
    fiche = snap["fiche_ia"]

    if not fiche:
        st.info(
            "ℹ️ Aucune fiche IA pour ce chapitre. La générer prend ~30-60 sec "
            "et **coûte une requête Gemini** (puis elle est mise en cache)."
        )
        if st.button(
            "✨ Générer la fiche avec Gemini",
            type="primary",
            key=f"gen_fiche_{chap_id}",
            width='stretch',
        ):
            _generate_fiche(chap_id, force=False)
    else:
        with st.expander("📄 Fiche de révision", expanded=True):
            st.markdown(fiche)

        col_a, col_b = st.columns([1, 5])
        with col_a:
            if st.button("🔄 Regénérer", key=f"regen_fiche_{chap_id}", help="Force une nouvelle génération"):
                _generate_fiche(chap_id, force=True)

    # --- Notes personnelles -------------------------------------------------
    st.divider()
    st.subheader("✍️ Mes notes personnelles")
    notes = st.text_area(
        "Notes",
        value=snap["notes"],
        height=150,
        key=f"notes_input_{chap_id}",
        label_visibility="collapsed",
        placeholder="Ce que tu retiens, tes propres exemples, tes confusions...",
    )
    if st.button("💾 Sauvegarder mes notes", key=f"save_notes_{chap_id}"):
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


def _generate_fiche(chap_id: int, force: bool) -> None:
    """Encapsule l'appel Gemini de génération de fiche avec gestion d'erreur."""
    with st.spinner("🧠 Gemini compose la fiche... (30-60 sec)"):
        try:
            with session_scope() as session:
                rs.generer_fiche_ia(session, chap_id, force_regenerate=force)
            st.toast("Fiche générée ✅")
            st.rerun()
        except Exception as exc:
            st.error(f"❌ Erreur de génération : {exc}")

# ===========================================================================
# Onglet Flashcards
# ===========================================================================
def _render_flashcards_tab(chap_id: int, snap: dict) -> None:
    """Affiche et gère les flashcards générées par l'IA."""
    with get_session() as session:
        chap = session.get(Chapitre, chap_id)
        flashcards = list(chap.flashcards_cache or [])

    if not flashcards:
        st.info("ℹ️ Aucune flashcard générée pour ce chapitre. La génération prend ~30 sec.")
        if st.button("✨ Générer des Flashcards", type="primary", key=f"gen_fc_{chap_id}", width='stretch'):
            with st.spinner("🧠 L'IA compose les flashcards..."):
                try:
                    with session_scope() as session:
                        from services.ai_flashcards_service import generer_flashcards
                        chap_obj = session.get(Chapitre, chap_id)
                        fcs = generer_flashcards(session, chap_obj, nb_cartes=8)
                        chap_obj.flashcards_cache = fcs
                    st.toast("Flashcards générées ✅")
                    st.rerun()
                except Exception as exc:
                    st.error(f"❌ Erreur de génération : {exc}")
        return

    st.subheader(f"🗂️ Flashcards ({len(flashcards)})")
    
    # State pour le parcours des flashcards
    fc_index_key = f"fc_idx_{chap_id}"
    fc_flipped_key = f"fc_flipped_{chap_id}"
    if fc_index_key not in st.session_state:
        st.session_state[fc_index_key] = 0
    if fc_flipped_key not in st.session_state:
        st.session_state[fc_flipped_key] = False

    idx = st.session_state[fc_index_key]
    flipped = st.session_state[fc_flipped_key]

    if idx >= len(flashcards):
        st.success("🎉 Vous avez terminé toutes les flashcards de ce paquet !")
        if st.button("🔄 Recommencer"):
            st.session_state[fc_index_key] = 0
            st.session_state[fc_flipped_key] = False
            st.rerun()
        return

    fc = flashcards[idx]
    
    # Affichage de la carte
    st.markdown(f"**Carte {idx + 1} / {len(flashcards)}**")
    with st.container(border=True):
        st.markdown(f"### 🤔 {fc.get('front', '')}")
        if flipped:
            st.divider()
            st.markdown(f"### 💡 {fc.get('back', '')}")
            
    # Actions
    if not flipped:
        if st.button("🔄 Révéler la réponse", use_container_width=True, type="primary"):
            st.session_state[fc_flipped_key] = True
            st.rerun()
    else:
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("🔴 Difficile", use_container_width=True):
                # À l'avenir, pourrait impacter Leitner
                st.session_state[fc_index_key] += 1
                st.session_state[fc_flipped_key] = False
                st.rerun()
        with col2:
            if st.button("🟠 Correct", use_container_width=True):
                st.session_state[fc_index_key] += 1
                st.session_state[fc_flipped_key] = False
                st.rerun()
        with col3:
            if st.button("🟢 Facile", use_container_width=True):
                st.session_state[fc_index_key] += 1
                st.session_state[fc_flipped_key] = False
                st.rerun()


# ===========================================================================
# Onglet 2 — QCM
# ===========================================================================
def _render_qcm_tab(chap_id: int) -> None:
    """QCM 4 choix interactif : génère → l'utilisateur répond → score → Leitner."""
    k_submitted = f"qcm_submitted_{chap_id}"

    # Charge le QCM en cache (s'il existe)
    with get_session() as session:
        chap = session.get(Chapitre, chap_id)
        questions = list(chap.qcm_cache or [])

    if not questions:
        st.info(
            "ℹ️ Aucun QCM généré pour ce chapitre. La 1ʳᵉ génération prend ~30 sec "
            "et te donne 5 questions à 4 choix."
        )
        if st.button(
            "✨ Générer un QCM (5 questions)",
            type="primary",
            key=f"gen_qcm_{chap_id}",
            width='stretch',
        ):
            _generate_qcm(chap_id, force=False)
        return

    # Si déjà soumis, on affiche les résultats
    if st.session_state.get(k_submitted):
        _render_qcm_results(chap_id, questions)
        return

    # Sinon : formulaire interactif
    st.subheader(f"🔘 QCM — {len(questions)} questions")
    st.caption("Réponds à toutes les questions, puis valide en bas.")

    for i, q in enumerate(questions, 1):
        with st.container(border=True):
            st.markdown(f"**Q{i}.** {q['question']}")
            st.radio(
                f"Réponse Q{i}",
                options=q["options"],
                key=f"qcm_choice_{chap_id}_{i}",
                label_visibility="collapsed",
                index=None,
            )

    if st.button(
        "✅ Valider mon QCM",
        type="primary",
        key=f"submit_qcm_{chap_id}",
        width='stretch',
    ):
        _submit_qcm(chap_id, questions)


def _submit_qcm(chap_id: int, questions: list[dict]) -> None:
    """Calcule le score du QCM et applique Leitner."""
    user_details = []
    nb_correct = 0
    for i, q in enumerate(questions, 1):
        chosen = st.session_state.get(f"qcm_choice_{chap_id}_{i}")
        if chosen is None:
            st.error(f"❌ Tu n'as pas répondu à la Q{i}.")
            return
        # La 1ʳᵉ lettre de l'option choisie (A, B, C, D)
        letter = chosen.strip()[0].upper() if chosen.strip() else ""
        is_correct = (letter == q["correct"].upper())
        if is_correct:
            nb_correct += 1
        user_details.append({
            "chosen_text": chosen,
            "chosen_letter": letter,
            "is_correct": is_correct,
        })

    score_num = nb_correct / len(questions) if questions else 0.0

    gains_xp: list[dict] = []
    replanning_auto_fait = False
    try:
        with session_scope() as session:
            leitner = rs.appliquer_resultat_quiz(session, chap_id, score_num, mode="qcm")
            # --- F3a : XP du quiz + éventuelle promotion Leitner ----------
            profil = get_or_create_utilisateur(session)
            chap_obj = session.get(Chapitre, chap_id)
            g_quiz = attribuer_xp_quiz(session, profil, score_num, chap_obj)
            gains_xp.append({"type": "quiz", "gain": _serialize_gain(g_quiz)})
            if leitner["niveau_apres"] > leitner["niveau_avant"]:
                g_promo = attribuer_xp_promotion_leitner(
                    session, profil,
                    leitner["niveau_avant"], leitner["niveau_apres"],
                    chap_obj, niveau_max_leitner=rs.MAX_NIVEAU,
                )
                if g_promo:
                    gains_xp.append({"type": "promotion", "gain": _serialize_gain(g_promo)})
            # --- A1 : Replanning auto silencieux si quiz raté ------------
            replan_auto = bool(profil.systeme.replanning_auto_actif and score_num < 0.5)
    except Exception as exc:
        st.error(f"❌ Erreur lors de l'application de Leitner : {exc}")
        return

    # Replanning auto (en dehors du scope pour avoir un session_scope dédié)
    if replan_auto:
        replanning_auto_fait = _try_replan_auto()

    st.session_state[f"qcm_submitted_{chap_id}"] = True
    st.session_state[f"qcm_result_{chap_id}"] = {
        "score_num": score_num,
        "nb_correct": nb_correct,
        "nb_total": len(questions),
        "details": user_details,
        "leitner": leitner,
        "gains_xp": gains_xp,
        "replanning_auto_fait": replanning_auto_fait,
    }
    st.rerun()


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


def _try_replan_auto() -> bool:
    """A1 — replanning auto silencieux. Retourne True si fait avec succès."""
    try:
        from services.ai_planner import replan_remaining_week
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
            replan_remaining_week(session, semaine.id)
        return True
    except Exception:
        # On ne plante pas le quiz si le replanning échoue (Gemini down, etc.)
        return False


def _render_qcm_results(chap_id: int, questions: list[dict]) -> None:
    """Affiche le bilan détaillé du QCM + bouton replan."""
    result = st.session_state.get(f"qcm_result_{chap_id}", {})
    if not result:
        st.warning("⚠️ Résultat introuvable. Recommence le QCM.")
        return

    score = result["score_num"]
    leitner = result["leitner"]

    # Banner principal
    pct = int(score * 100)
    if score >= 0.9:
        st.success(f"🏆 **Excellent !** {result['nb_correct']}/{result['nb_total']} ({pct}%)")
        st.balloons()
    elif score >= 0.5:
        st.info(f"⚡ **Correct.** {result['nb_correct']}/{result['nb_total']} ({pct}%)")
    else:
        st.warning(f"📚 **À retravailler.** {result['nb_correct']}/{result['nb_total']} ({pct}%)")

    # Évolution Leitner
    _render_leitner_evolution(leitner)

    # F3a : XP gagnés + achievements + replanning auto
    _render_gains_xp_block(result.get("gains_xp", []), result.get("replanning_auto_fait", False))

    # Détail Q par Q
    with st.expander("🔍 Détail des questions", expanded=False):
        for i, (q, d) in enumerate(zip(questions, result["details"]), 1):
            icon = "✅" if d["is_correct"] else "❌"
            st.markdown(f"{icon} **Q{i}.** {q['question']}")
            if d["is_correct"]:
                st.caption(f"Bonne réponse : **{q['correct']}**. _{q.get('explication', '')}_")
            else:
                st.caption(
                    f"Tu as répondu **{d['chosen_letter']}**, "
                    f"la bonne réponse était **{q['correct']}**. "
                    f"_{q.get('explication', '')}_"
                )
            st.write("")

    # Actions
    st.divider()
    a, b, c = st.columns(3)
    with a:
        if st.button("🔄 Recommencer ce QCM", key=f"retry_qcm_{chap_id}", width='stretch'):
            _reset_qcm_state(chap_id, len(questions))
            st.rerun()
    with b:
        if st.button("✨ Générer un nouveau QCM", key=f"new_qcm_{chap_id}", width='stretch'):
            _generate_qcm(chap_id, force=True)
    with c:
        if st.button("⚙️ Réajuster ma semaine", key=f"replan_qcm_{chap_id}",
                     type="primary", width='stretch'):
            _trigger_replan()


def _generate_qcm(chap_id: int, force: bool) -> None:
    """Encapsule l'appel Gemini de génération de QCM."""
    with st.spinner("🧠 Gemini crée le QCM... (~30 sec)"):
        try:
            with session_scope() as session:
                rs.generer_qcm(session, chap_id, nb=5, force_regenerate=force)
            if force:
                # Force = on regénère ; on reset aussi l'état précédent
                _reset_qcm_state(chap_id, 5)
            st.toast("QCM prêt ✅")
            st.rerun()
        except Exception as exc:
            st.error(f"❌ Erreur de génération : {exc}")


def _reset_qcm_state(chap_id: int, nb_questions: int) -> None:
    """Nettoie l'état de session du QCM pour permettre de recommencer."""
    st.session_state.pop(f"qcm_submitted_{chap_id}", None)
    st.session_state.pop(f"qcm_result_{chap_id}", None)
    for i in range(1, nb_questions + 1):
        st.session_state.pop(f"qcm_choice_{chap_id}_{i}", None)


# ===========================================================================
# Onglet 3 — Quiz ouvert
# ===========================================================================
def _render_quiz_tab(chap_id: int) -> None:
    """Quiz à questions ouvertes : Gemini génère, l'étudiant répond en texte,
    Gemini évalue, Leitner s'applique."""
    k_submitted = f"quiz_submitted_{chap_id}"

    with get_session() as session:
        chap = session.get(Chapitre, chap_id)
        questions = list(chap.quiz_cache or [])

    if not questions:
        st.info(
            "ℹ️ Aucun quiz ouvert généré. Plus exigeant que le QCM, mais aussi "
            "plus formateur : Gemini évaluera tes réponses libres."
        )
        if st.button(
            "✨ Générer un quiz (5 questions ouvertes)",
            type="primary",
            key=f"gen_quiz_{chap_id}",
            width='stretch',
        ):
            _generate_quiz(chap_id, force=False)
        return

    if st.session_state.get(k_submitted):
        _render_quiz_results(chap_id, questions)
        return

    st.subheader(f"✍️ Quiz ouvert — {len(questions)} questions")
    st.caption("Réponds avec tes propres mots. Gemini évaluera chaque réponse.")

    for i, q in enumerate(questions, 1):
        with st.container(border=True):
            st.markdown(f"**Q{i}.** {q}")
            st.text_area(
                f"Réponse Q{i}",
                key=f"quiz_rep_{chap_id}_{i}",
                height=120,
                label_visibility="collapsed",
                placeholder="Ta réponse...",
            )

    if st.button(
        "✅ Soumettre mes réponses",
        type="primary",
        key=f"submit_quiz_{chap_id}",
        width='stretch',
    ):
        _submit_quiz(chap_id, questions)


def _submit_quiz(chap_id: int, questions: list[str]) -> None:
    """Récupère les réponses, demande à Gemini d'évaluer, applique Leitner."""
    reponses = [
        st.session_state.get(f"quiz_rep_{chap_id}_{i}", "") for i in range(1, len(questions) + 1)
    ]
    if not any(r.strip() for r in reponses):
        st.error("❌ Tu n'as répondu à aucune question.")
        return

    gains_xp: list[dict] = []
    replanning_auto_fait = False
    with st.spinner("🧠 Gemini évalue tes réponses... (~30-60 sec)"):
        try:
            with session_scope() as session:
                evaluation = rs.evaluer_quiz_ouvert(session, chap_id, questions, reponses)
                leitner = rs.appliquer_resultat_quiz(
                    session, chap_id, evaluation["score_num"], mode="quiz"
                )
                # --- F3a : XP --------------------------------------------
                profil = get_or_create_utilisateur(session)
                chap_obj = session.get(Chapitre, chap_id)
                g_quiz = attribuer_xp_quiz(session, profil, evaluation["score_num"], chap_obj)
                gains_xp.append({"type": "quiz", "gain": _serialize_gain(g_quiz)})
                if leitner["niveau_apres"] > leitner["niveau_avant"]:
                    g_promo = attribuer_xp_promotion_leitner(
                        session, profil,
                        leitner["niveau_avant"], leitner["niveau_apres"],
                        chap_obj, niveau_max_leitner=rs.MAX_NIVEAU,
                    )
                    if g_promo:
                        gains_xp.append({"type": "promotion", "gain": _serialize_gain(g_promo)})
                replan_auto = bool(
                    profil.systeme.replanning_auto_actif and evaluation["score_num"] < 0.5
                )
        except Exception as exc:
            st.error(f"❌ Erreur d'évaluation : {exc}")
            return

    if replan_auto:
        replanning_auto_fait = _try_replan_auto()

    st.session_state[f"quiz_submitted_{chap_id}"] = True
    st.session_state[f"quiz_result_{chap_id}"] = {
        "evaluation": evaluation,
        "leitner": leitner,
        "reponses": reponses,
        "gains_xp": gains_xp,
        "replanning_auto_fait": replanning_auto_fait,
    }
    st.rerun()


def _render_quiz_results(chap_id: int, questions: list[str]) -> None:
    """Affiche le bilan du quiz ouvert avec feedback Gemini."""
    result = st.session_state.get(f"quiz_result_{chap_id}", {})
    if not result:
        st.warning("⚠️ Résultat introuvable.")
        return

    evaluation = result["evaluation"]
    leitner = result["leitner"]
    score = evaluation.get("score_num", 0.0)
    pct = int(score * 100)
    verdict = (evaluation.get("verdict") or "").lower()
    message = evaluation.get("message", "")

    # Banner
    if score >= 0.9:
        st.success(f"🏆 **{verdict.capitalize() or 'Excellent'}** ({pct}%)")
        if message:
            st.caption(message)
        st.balloons()
    elif score >= 0.5:
        st.info(f"⚡ **{verdict.capitalize() or 'Correct'}** ({pct}%)")
        if message:
            st.caption(message)
    else:
        st.warning(f"📚 **{verdict.capitalize() or 'À retravailler'}** ({pct}%)")
        if message:
            st.caption(message)

    _render_leitner_evolution(leitner)

    # F3a : XP gagnés + achievements + replanning auto
    _render_gains_xp_block(result.get("gains_xp", []), result.get("replanning_auto_fait", False))

    # Détail correction
    with st.expander("🔍 Correction détaillée par Gemini", expanded=True):
        resultats = evaluation.get("resultats") or []
        for i, (q, rep, res) in enumerate(zip(questions, result["reponses"], resultats), 1):
            score_q = (res.get("score") or "incorrect").lower()
            icon = {"correct": "✅", "partiel": "🟡", "incorrect": "❌"}.get(score_q, "❓")
            st.markdown(f"{icon} **Q{i}.** {q}")
            st.caption(f"💬 _Ta réponse :_ {rep.strip() or '*(vide)*'}")
            feedback = res.get("feedback", "").strip()
            if feedback:
                st.caption(f"🎓 _Feedback :_ {feedback}")
            st.write("")

    st.divider()
    a, b, c = st.columns(3)
    with a:
        if st.button("🔄 Recommencer", key=f"retry_quiz_{chap_id}", width='stretch'):
            _reset_quiz_state(chap_id, len(questions))
            st.rerun()
    with b:
        if st.button("✨ Nouvelles questions", key=f"new_quiz_{chap_id}", width='stretch'):
            _generate_quiz(chap_id, force=True)
    with c:
        if st.button("⚙️ Réajuster ma semaine", key=f"replan_quiz_{chap_id}",
                     type="primary", width='stretch'):
            _trigger_replan()


def _generate_quiz(chap_id: int, force: bool) -> None:
    """Encapsule l'appel Gemini pour générer un quiz ouvert."""
    with st.spinner("🧠 Gemini compose les questions... (~30 sec)"):
        try:
            with session_scope() as session:
                rs.generer_quiz_ouvert(session, chap_id, nb=5, force_regenerate=force)
            if force:
                _reset_quiz_state(chap_id, 5)
            st.toast("Quiz prêt ✅")
            st.rerun()
        except Exception as exc:
            st.error(f"❌ Erreur de génération : {exc}")


def _reset_quiz_state(chap_id: int, nb_questions: int) -> None:
    """Nettoie l'état de session du quiz ouvert."""
    st.session_state.pop(f"quiz_submitted_{chap_id}", None)
    st.session_state.pop(f"quiz_result_{chap_id}", None)
    for i in range(1, nb_questions + 1):
        st.session_state.pop(f"quiz_rep_{chap_id}_{i}", None)


# ===========================================================================
# Helpers partagés
# ===========================================================================
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


def _trigger_replan() -> None:
    """Lance ai_planner.replan_remaining_week() pour redistribuer les tâches restantes."""
    try:
        from utils.helpers import get_or_create_current_week
        from services.ai_planner import replan_remaining_week
    except ImportError as exc:
        st.error(f"❌ Impossible d'importer le planner : {exc}")
        return

    with st.spinner("🧠 Gemini réorganise tes jours restants... (~30-45 sec)"):
        try:
            with session_scope() as session:
                semaine, _, _ = get_or_create_current_week(session, transfer_reported=False)
                result = replan_remaining_week(session, semaine.id)
        except Exception as exc:
            st.error(f"❌ Erreur de réajustement : {exc}")
            return

    st.success("✅ Planning réajusté !")
    score = result.get("score_realisme")
    if score is not None:
        st.caption(f"📊 Score de réalisme : **{score}/100**")
    justif = result.get("justification_globale")
    if justif:
        st.info(f"💡 **Stratégie :** {justif}")
    ecartees = result.get("taches_ecartees") or []
    if ecartees:
        with st.expander(f"⚠️ Tâches écartées ({len(ecartees)})", expanded=False):
            for t in ecartees:
                if isinstance(t, dict):
                    st.markdown(f"- **{t.get('titre', '?')}** — {t.get('raison', '')}")
                else:
                    st.markdown(f"- {t}")


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

# ===========================================================================
# Onglet 4 — Simulateur Feynman (Évaluation IA)
# ===========================================================================
def _render_feynman_tab(chap_id: int) -> None:
    """Interface pour capturer l'audio de l'étudiant et l'évaluer."""
    st.subheader("🎙️ Simulateur Feynman")
    st.caption("Explique les concepts de ce chapitre à voix haute, sans regarder tes notes. L'IA corrigera tes erreurs.")

    # On utilise le tout nouveau micro NATIVEMENT intégré à Streamlit (sans bug !)
    audio_file = st.audio_input("Enregistre ton explication ici")

    # Si un audio a bien été enregistré
    if audio_file is not None:
        # st.audio_input affiche déjà un petit lecteur audio intégré, c'est magique.
        
        if st.button("🧠 Évaluer mon explication", type="primary"):
            # L'audio est sous forme de fichier, on le convertit en "bytes" pour Gemini
            audio_bytes = audio_file.read()
            _evaluer_feynman(audio_bytes, chap_id)

def _evaluer_feynman(audio_bytes: bytes, chap_id: int) -> None:
    """Envoie l'audio à Gemini pour le comparer avec la fiche de cours."""
    import tempfile
    import os
    from google import genai
    from database.models import Utilisateur, Chapitre

    with st.spinner("🧠 Gemini écoute, transcrit et évalue ta démonstration... (ça peut prendre 30 sec)"):
        try:
            # 1. On récupère la clé API ET le contenu du chapitre
            with get_session() as session:
                chapitre = session.get(Chapitre, chap_id)
                api_key, model_name = get_gemini_credentials(session)

                if not api_key:
                    st.error("❌ Clé API Gemini introuvable.")
                    return
                
                titre_chapitre = chapitre.titre if chapitre else "Inconnu"
                # On utilise la fiche IA comme correction officielle. 
                # Si elle n'existe pas, on prévient Gemini.
                fiche_cours = chapitre.fiche_ia if (chapitre and chapitre.fiche_ia) else "(Aucune fiche IA générée. Base ton évaluation sur le titre du chapitre uniquement.)"

            if model_name.startswith("deepseek"):
                st.error("L'évaluation audio avec la technique de Feynman n'est pas encore supportée par DeepSeek. Veuillez utiliser un modèle Gemini.")
                return

            client = genai.Client(api_key=api_key)

            # 2. Sauvegarde temporaire du fichier audio
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
                tmp_file.write(audio_bytes)
                tmp_path = tmp_file.name

            try:
                # 3. Upload et Prompt magistral pour l'IA
                uploaded_file = client.files.upload(file=tmp_path)
                
                prompt = f"""Tu es un professeur de sciences de niveau universitaire, expert et très exigeant. 
Ton étudiant s'entraîne avec la technique de Feynman.

Voici le sujet précis du chapitre : {titre_chapitre}
Voici les points clés théoriques attendus (issus de sa Fiche de Révision) :
{fiche_cours}

Ta tâche :
1. Écoute l'explication audio de l'étudiant.
2. Fais une courte transcription (résumé) de ce qu'il a dit dans une section "📝 Ce que j'ai entendu".
3. Rédige une évaluation stricte dans une section "🎓 Retour du Professeur" :
   - ✅ Ce qui est mathématiquement ou théoriquement exact.
   - ❌ Ce qui est faux, confus, ou les termes techniques mal employés (corrige-les de manière didactique).
   - ⚠️ Les éléments cruciaux de la fiche qu'il a oublié de mentionner.
4. Donne-lui une note sur 10 de clarté et de maîtrise.
"""
                response = client.models.generate_content(
                    model=model_name,
                    contents=[uploaded_file, prompt]
                )
                
                # 4. Affichage du résultat
                st.success("Analyse terminée !")
                st.markdown(response.text)
                
            finally:
                # Nettoyage rigoureux des fichiers
                if 'uploaded_file' in locals():
                    client.files.delete(name=uploaded_file.name)
                os.remove(tmp_path)
                
        except Exception as e:
            st.error(f"❌ Erreur lors de l'analyse : {e}")
main()