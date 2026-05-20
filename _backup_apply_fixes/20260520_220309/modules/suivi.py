"""Onglet **Suivi quotidien & bilan de semaine**.

L'étudiant marque ses tâches ✅ Terminée / ⚠️ Partielle / ❌ Non faite.
Si plusieurs tâches sont en retard, un bouton « Recalculer le reste de la
semaine » appelle Gemini pour redistribuer les tâches restantes.

Les tâches *obligatoires* (sommeil, repas, transport, cours présentiels) sont
affichées en lecture seule — elles ne peuvent pas être marquées non-faites.

Note : la maîtrise des chapitres associés à une tâche d'étude est bumpée de
+5 % chaque fois qu'une tâche est marquée ``fait`` (capée à 100 %). C'est un
proxy léger ; l'utilisateur peut toujours ajuster manuellement la maîtrise
dans l'onglet **Bibliothèque**.
"""

from __future__ import annotations

import datetime
from typing import Any

import streamlit as st
from sqlalchemy.orm import Session

from database.db import get_session, session_scope
from database.models import Chapitre, Semaine, Tache
from services.ai_planner import replan_remaining_week


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

# Bump de maîtrise appliqué à un chapitre quand une tâche qui le concerne est faite.
MAITRISE_BUMP_FAIT = 5
MAITRISE_BUMP_PARTIEL = 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_current_week(session: Session) -> Semaine | None:
    today = datetime.date.today()
    iso_year, iso_week, _ = today.isocalendar()
    return session.query(Semaine).filter_by(
        annee=iso_year, numero_semaine=iso_week,
    ).first()


def _jour_from_date(d: datetime.date) -> str:
    return JOURS[d.weekday()]


def _update_task_status(task_id: int, statut: str, commentaire: str = "") -> None:
    """Met à jour le statut d'une tâche et bumpe la maîtrise des chapitres
    associés si pertinent."""
    with session_scope() as s:
        t = s.query(Tache).get(task_id)
        if t is None:
            return
        t.statut = statut
        if commentaire:
            t.commentaire_etudiant = commentaire

        # Bump de maîtrise si tâche d'étude liée à un ou plusieurs chapitres
        if t.cours_id and t.chapitre_ids and statut in ("fait", "partiellement"):
            bump = MAITRISE_BUMP_FAIT if statut == "fait" else MAITRISE_BUMP_PARTIEL
            for ch_id in t.chapitre_ids:
                ch = s.query(Chapitre).get(ch_id)
                if ch and ch.maitrise_pct < 100:
                    ch.maitrise_pct = min(100, int(ch.maitrise_pct or 0) + bump)


def _compute_completion_score(tasks: list[Tache]) -> float:
    """Taux de complétion : fait = 1 pt, partiel = 0.5 pt, autres = 0."""
    if not tasks:
        return 0.0
    points = sum(
        1.0 if t.statut == "fait"
        else 0.5 if t.statut == "partiellement"
        else 0.0
        for t in tasks
    )
    return points / len(tasks) * 100


def _persist_weekly_score(semaine_id: int, score: float) -> None:
    with session_scope() as s:
        sem = s.query(Semaine).get(semaine_id)
        if sem:
            sem.taux_completion_pct = round(score, 1)


# ---------------------------------------------------------------------------
# Rendu UI
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("📊 Suivi quotidien")
    st.caption("Valide tes tâches au fil de la journée. L'IA peut redistribuer le reste de la semaine si tu prends du retard.")

    with get_session() as session:
        semaine = _get_current_week(session)
        if semaine is None:
            st.warning(
                "⚠️ Aucune semaine en cours. Va sur l'onglet **Études** pour initialiser la semaine, "
                "puis sur **Génération IA** pour créer un planning."
            )
            return

        if semaine.statut != "generee":
            st.warning(
                "⚠️ Aucun planning n'a encore été généré pour cette semaine. "
                "Va sur **Génération IA** pour en créer un."
            )
            return

        # En-tête : sélecteur de jour
        today = datetime.date.today()
        jour_aujourd_hui = _jour_from_date(today)

        col_titre, col_picker = st.columns([3, 1])
        with col_titre:
            st.subheader(
                f"📅 Semaine {semaine.numero_semaine} — "
                f"du {semaine.date_debut.strftime('%d/%m')} "
                f"au {semaine.date_fin.strftime('%d/%m')}"
            )
        with col_picker:
            idx_default = JOURS.index(jour_aujourd_hui) if jour_aujourd_hui in JOURS else 0
            jour_select = st.selectbox(
                "Jour à valider",
                options=JOURS,
                index=idx_default,
                format_func=lambda j: j.capitalize() + (" (aujourd'hui)" if j == jour_aujourd_hui else ""),
            )

        # Tâches du jour sélectionné
        taches_jour = (
            session.query(Tache)
            .filter_by(semaine_id=semaine.id, jour=jour_select)
            .order_by(Tache.heure_debut)
            .all()
        )

        if not taches_jour:
            st.info(f"Aucune tâche planifiée le {jour_select}.")
        else:
            _render_day_metrics(taches_jour)
            st.divider()
            for t in taches_jour:
                _render_task_row(t)

        # Section recalcul (uniquement si on est dans la fenêtre de la semaine)
        st.divider()
        _render_replan_section(session, semaine)

        # Bilan de la semaine
        st.divider()
        _render_weekly_stats(session, semaine)


def _render_day_metrics(taches: list[Tache]) -> None:
    nb_a_faire = sum(1 for t in taches if t.statut == "a_faire")
    nb_fait = sum(1 for t in taches if t.statut == "fait")
    nb_partiel = sum(1 for t in taches if t.statut == "partiellement")
    nb_non_fait = sum(1 for t in taches if t.statut == "non_fait")

    cols = st.columns(4)
    cols[0].metric("⏳ À faire", nb_a_faire)
    cols[1].metric("✅ Terminées", nb_fait)
    cols[2].metric("⚠️ Partielles", nb_partiel)
    cols[3].metric("❌ Non faites", nb_non_fait)


def _render_task_row(t: Tache) -> None:
    """Une ligne tâche avec ses boutons de validation (sauf si obligatoire)."""
    statut_meta = {
        "a_faire":       ("⏳", "blue",   "À faire"),
        "fait":          ("✅", "green",  "Terminée"),
        "partiellement": ("⚠️", "orange", "Partielle"),
        "non_fait":      ("❌", "red",    "Non faite"),
        "reporte":       ("🔁", "violet", "Reportée"),
    }
    emoji, color, label = statut_meta.get(t.statut, ("•", "gray", t.statut))

    with st.container(border=True):
        col_h, col_t, col_s = st.columns([1, 4, 1])
        with col_h:
            st.markdown(f"**{t.heure_debut.strftime('%H:%M')}**")
            st.caption(f"→ {t.heure_fin.strftime('%H:%M')}")
            duree = t.duree_min or _compute_duree_min(t)
            st.caption(f"⏱️ {duree} min")
        with col_t:
            lock = "🔒 " if t.obligatoire else ""
            st.markdown(f"{lock}**{t.titre}**")
            if t.justification_ia:
                st.caption(f"💭 *{t.justification_ia}*")
            if t.commentaire_etudiant:
                st.caption(f"📝 *{t.commentaire_etudiant}*")
        with col_s:
            st.markdown(f":{color}[{emoji} {label}]")

        # Boutons : pas affichés pour les tâches obligatoires (immutables)
        if t.obligatoire:
            return

        col_a, col_b, col_c, col_d = st.columns(4)
        with col_a:
            if st.button("✅ Terminée", key=f"fait_{t.id}", width="stretch"):
                _update_task_status(t.id, "fait")
                st.toast("Tâche terminée", icon="✅")
                st.rerun()
        with col_b:
            if st.button("⚠️ Partielle", key=f"partiel_{t.id}", width="stretch"):
                st.session_state[f"show_comment_{t.id}"] = True
                st.rerun()
        with col_c:
            if st.button("❌ Non faite", key=f"non_fait_{t.id}", width="stretch"):
                _update_task_status(t.id, "non_fait")
                st.toast("Tâche notée non faite", icon="❌")
                st.rerun()
        with col_d:
            if t.statut != "a_faire":
                if st.button("↩️ Annuler", key=f"reset_{t.id}", width="stretch",
                             help="Remettre cette tâche en « à faire »"):
                    _update_task_status(t.id, "a_faire")
                    st.rerun()

        # Champ commentaire si "Partielle" cliqué
        if st.session_state.get(f"show_comment_{t.id}"):
            with st.form(f"form_partial_{t.id}"):
                comment = st.text_area(
                    "Ce qui reste à faire (optionnel)",
                    placeholder="Ex. : j'ai fait les exercices 1 à 3, il reste 4 à 6.",
                    height=80,
                )
                col_v, col_a = st.columns(2)
                with col_v:
                    if st.form_submit_button("✓ Valider", type="primary", width="stretch"):
                        _update_task_status(t.id, "partiellement", comment.strip())
                        st.session_state[f"show_comment_{t.id}"] = False
                        st.toast("Tâche partielle enregistrée", icon="⚠️")
                        st.rerun()
                with col_a:
                    if st.form_submit_button("Annuler", width="stretch"):
                        st.session_state[f"show_comment_{t.id}"] = False
                        st.rerun()


def _compute_duree_min(t: Tache) -> int:
    """Fallback pour les tâches sans duree_min en base."""
    h1 = t.heure_debut.hour * 60 + t.heure_debut.minute
    h2 = t.heure_fin.hour * 60 + t.heure_fin.minute
    return max(0, h2 - h1)


# ---------------------------------------------------------------------------
# Section recalcul adaptatif
# ---------------------------------------------------------------------------
def _render_replan_section(session: Session, semaine: Semaine) -> None:
    """Bouton « Recalculer le reste de la semaine » — visible si retard détecté."""
    today = datetime.date.today()
    if today < semaine.date_debut or today > semaine.date_fin:
        return  # on n'est pas dans la semaine en question

    jour_idx = today.weekday()
    jours_passes = JOURS[:jour_idx]  # excluant aujourd'hui

    # Tâches non faites avant aujourd'hui (vrai retard)
    retard = (
        session.query(Tache)
        .filter(
            Tache.semaine_id == semaine.id,
            Tache.jour.in_(jours_passes),
            Tache.obligatoire.is_(False),
            Tache.statut.in_(["a_faire", "non_fait", "partiellement"]),
        )
        .count()
    )

    if retard < 2:
        return  # pas besoin de bouton

    st.warning(
        f"⚠️ Tu as **{retard} tâches en retard** sur les jours passés. "
        "Souhaites-tu que Gemini redistribue le reste de la semaine ?"
    )
    col1, _ = st.columns([1, 2])
    with col1:
        clicked = st.button(
            "🔄 Recalculer avec Gemini", type="primary", width="stretch",
        )

    if not clicked:
        return

    with st.status("Recalcul en cours…", expanded=True) as status:
        try:
            st.write("📊 Analyse de l'état actuel de la semaine…")
            with session_scope() as write_session:
                result = replan_remaining_week(write_session, semaine.id)

            st.write(f"✓ Score de réalisme : **{result.get('score_realisme', '?')}/100**")
            if result.get("justification_globale"):
                st.write(f"💭 _{result['justification_globale']}_")
            if result.get("taches_ecartees"):
                st.write(f"📤 **{len(result['taches_ecartees'])} tâches écartées** par manque de temps :")
                for t in result["taches_ecartees"][:5]:
                    if isinstance(t, dict):
                        st.write(f"  • {t.get('titre', '?')} — _{t.get('raison', '')}_")
                    else:
                        st.write(f"  • {t}")
            if result.get("alertes"):
                for a in result["alertes"]:
                    st.warning(f"⚠️ {a}")

            status.update(label="✅ Planning mis à jour", state="complete")
            st.toast("Recalcul réussi", icon="🎉")
        except Exception as exc:  # noqa: BLE001
            status.update(label=f"❌ Erreur : {exc}", state="error")
            st.error(str(exc))


# ---------------------------------------------------------------------------
# Bilan de la semaine
# ---------------------------------------------------------------------------
def _render_weekly_stats(session: Session, semaine: Semaine) -> None:
    all_tasks = session.query(Tache).filter_by(semaine_id=semaine.id).all()
    if not all_tasks:
        return

    # On n'inclut PAS les tâches obligatoires dans le score (sinon il est
    # toujours élevé grâce au sommeil et aux repas qui « passent » tout seuls).
    non_oblig = [t for t in all_tasks if not t.obligatoire]
    if not non_oblig:
        return

    score = _compute_completion_score(non_oblig)

    # Persistance en BD pour le dashboard plus tard
    _persist_weekly_score(semaine.id, score)

    st.subheader("📈 Bilan de la semaine")
    nb_fait = sum(1 for t in non_oblig if t.statut == "fait")
    nb_partiel = sum(1 for t in non_oblig if t.statut == "partiellement")
    nb_non_fait = sum(1 for t in non_oblig if t.statut == "non_fait")
    nb_a_faire = sum(1 for t in non_oblig if t.statut == "a_faire")

    cols = st.columns(4)
    cols[0].metric("Taux de complétion", f"{score:.0f} %", help="Hors tâches obligatoires (sommeil, repas, transport).")
    cols[1].metric("✅ Faites", nb_fait)
    cols[2].metric("⚠️ Partielles", nb_partiel)
    cols[3].metric("⏳ + ❌ Restantes", nb_a_faire + nb_non_fait)

    st.progress(score / 100.0, text=f"{score:.0f} % de la semaine traité (objectif : 80 %)")

    if score >= 80:
        st.success("🎉 Très bonne semaine ! Tu tiens ton planning.")
    elif score >= 50:
        st.info("👌 Semaine correcte. Reste-t-il des tâches que tu peux rattraper ?")
    else:
        st.warning("💪 Semaine difficile. C'est OK — utilise le recalcul si nécessaire.")


__all__ = ["render"]
