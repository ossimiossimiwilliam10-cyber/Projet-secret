"""Page **🎯 Objectifs** — gestion des objectifs personnalisés avec stratégie IA.

L'utilisateur :
1. Voit la liste de ses objectifs (actifs + atteints + abandonnés).
2. Crée un nouvel objectif via un formulaire.
3. Demande une proposition de stratégie au LLM (preview avant adoption).
4. Adopte (ou refuse) la stratégie → l'objectif est persisté + les
   pondérations sont activées dans tous les futurs plannings.
5. Marque un objectif comme atteint / abandonné.
"""

from __future__ import annotations

import datetime

import streamlit as st

from database import Matiere, Objectif, get_session, session_scope
from services import objectif_service as objs


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("🎯 Mes objectifs")
    st.caption(
        "Définis tes objectifs personnels (notes, maîtrise, deadlines) et "
        "laisse l'IA te proposer une stratégie sur-mesure."
    )

    # Choix du sous-mode (création / vue)
    mode = st.radio(
        "Mode",
        ["📋 Mes objectifs", "➕ Nouvel objectif"],
        horizontal=True,
        label_visibility="collapsed",
    )

    if mode == "➕ Nouvel objectif":
        _render_form_creation()
    else:
        _render_liste_objectifs()


# ---------------------------------------------------------------------------
# Mode 1 — Liste des objectifs
# ---------------------------------------------------------------------------
def _render_liste_objectifs() -> None:
    with get_session() as session:
        actifs = session.query(Objectif).filter(Objectif.statut == "actif").all()
        atteints = session.query(Objectif).filter(Objectif.statut == "atteint").all()
        abandonnes = session.query(Objectif).filter(Objectif.statut == "abandonne").all()

        # Snapshot pour rendu hors session
        actifs_snap = [_snapshot_objectif(session, o) for o in actifs]
        atteints_snap = [_snapshot_objectif(session, o) for o in atteints]
        abandonnes_snap = [_snapshot_objectif(session, o) for o in abandonnes]

    if not actifs_snap and not atteints_snap and not abandonnes_snap:
        st.info(
            "ℹ️ Pas encore d'objectif défini. Clique sur **'➕ Nouvel objectif'** "
            "pour commencer."
        )
        return

    # Objectifs actifs
    st.subheader(f"🟢 Actifs ({len(actifs_snap)})")
    if not actifs_snap:
        st.caption("_Aucun objectif actif pour le moment._")
    for snap in actifs_snap:
        _render_card_objectif(snap, statut="actif")

    # Atteints
    if atteints_snap:
        with st.expander(f"✅ Objectifs atteints ({len(atteints_snap)})", expanded=False):
            for snap in atteints_snap:
                _render_card_objectif(snap, statut="atteint")

    # Abandonnés
    if abandonnes_snap:
        with st.expander(f"🗑️ Objectifs abandonnés ({len(abandonnes_snap)})", expanded=False):
            for snap in abandonnes_snap:
                _render_card_objectif(snap, statut="abandonne")


def _snapshot_objectif(session, obj: Objectif) -> dict:
    """Snapshot dict d'un objectif pour rendu hors session."""
    matiere_nom = obj.matiere.nom if obj.matiere else None
    prog = objs.progression_objectif(session, obj) if obj.statut == "actif" else None
    return {
        "id": obj.id,
        "nom": obj.nom,
        "description": obj.description,
        "matiere_nom": matiere_nom,
        "note_cible": obj.note_cible,
        "date_cible": obj.date_cible,
        "date_atteinte": obj.date_atteinte,
        "strategie": obj.strategie_ia or {},
        "progression": prog,
    }


def _render_card_objectif(snap: dict, statut: str) -> None:
    """Affiche une carte d'objectif."""
    obj_id = snap["id"]
    couleurs_statut = {
        "actif": "#10b981",
        "atteint": "#3b82f6",
        "abandonne": "#9ca3af",
    }
    couleur = couleurs_statut.get(statut, "#6b7280")

    cible_str = f"{snap['note_cible']}/20" if snap["note_cible"] is not None else "Qualitatif"
    matiere_str = snap["matiere_nom"] or "🌐 Global (toutes les matières)"
    deadline_str = snap["date_cible"].strftime("%d/%m/%Y") if snap["date_cible"] else "—"

    # Card header
    with st.container():
        st.markdown(
            f"<div style='background:{couleur}10;border-left:4px solid {couleur};"
            f"padding:14px 18px;border-radius:6px;margin-bottom:8px;'>"
            f"<div style='display:flex;justify-content:space-between;align-items:start;'>"
            f"<div style='flex:1;'>"
            f"<div style='font-weight:700;font-size:1.1rem;color:{couleur};'>{snap['nom']}</div>"
            f"<div style='color:#4b5563;font-size:0.9rem;margin-top:4px;'>"
            f"📘 {matiere_str} · 🎯 Cible : <b>{cible_str}</b> · 📅 Deadline : <b>{deadline_str}</b>"
            f"</div></div></div>",
            unsafe_allow_html=True,
        )

        # Progression (uniquement actifs)
        if statut == "actif" and snap["progression"]:
            prog = snap["progression"]
            _render_progression_bar(prog)

        # Stratégie IA (expander)
        strat = snap["strategie"]
        if strat:
            with st.expander("🧠 Stratégie IA", expanded=False):
                _render_strategie_recap(strat)

        # Boutons d'action
        if statut == "actif":
            c1, c2, c3 = st.columns(3)
            with c1:
                if st.button("✅ Marquer comme atteint", key=f"atteindre_{obj_id}", width='stretch'):
                    with session_scope() as session:
                        objs.marquer_atteint(session, obj_id)
                    st.toast("🏆 Objectif atteint, bravo !", icon="✅")
                    st.rerun()
            with c2:
                if st.button("⏸️ Abandonner", key=f"abandonner_{obj_id}", width='stretch'):
                    with session_scope() as session:
                        objs.abandonner(session, obj_id)
                    st.toast("Objectif abandonné.", icon="⏸️")
                    st.rerun()
            with c3:
                if st.button("🗑️ Supprimer", key=f"supprimer_{obj_id}",
                             type="secondary", width='stretch'):
                    with session_scope() as session:
                        objs.supprimer(session, obj_id)
                    st.toast("Objectif supprimé.", icon="🗑️")
                    st.rerun()
        elif statut in ("atteint", "abandonne"):
            if st.button("🗑️ Supprimer définitivement", key=f"supprimer_{obj_id}",
                         type="secondary"):
                with session_scope() as session:
                    objs.supprimer(session, obj_id)
                st.rerun()


def _render_progression_bar(prog: dict) -> None:
    """Affiche une barre de progression vers la cible."""
    actuelle = prog["maitrise_actuelle"]
    cible = prog["maitrise_cible_estimee"]
    ratio = prog["ratio_progression"]
    jours = prog["jours_restants"]

    pct = int(ratio * 100)

    if jours < 0:
        deadline_msg = f"⚠️ Deadline dépassée de {-jours} jour(s)"
        deadline_color = "#ef4444"
    elif jours == 0:
        deadline_msg = "🔥 C'EST AUJOURD'HUI"
        deadline_color = "#ef4444"
    elif jours <= 7:
        deadline_msg = f"🔥 {jours} jour(s) restants"
        deadline_color = "#f59e0b"
    else:
        deadline_msg = f"📅 {jours} jours restants"
        deadline_color = "#6b7280"

    if cible is not None:
        progress_label = f"{actuelle:.0f}% / {cible:.0f}% de maîtrise"
    else:
        progress_label = f"{actuelle:.0f}% de maîtrise"

    st.markdown(
        f"<div style='margin-top:8px;'>"
        f"<div style='display:flex;justify-content:space-between;font-size:0.85rem;color:#4b5563;'>"
        f"<span>{progress_label}</span>"
        f"<span style='color:{deadline_color};font-weight:600;'>{deadline_msg}</span>"
        f"</div>"
        f"<div style='background:#e5e7eb;height:10px;border-radius:5px;overflow:hidden;margin-top:4px;'>"
        f"<div style='background:linear-gradient(90deg,#3b82f6,#10b981);"
        f"width:{pct}%;height:100%;transition:width 0.6s ease;'></div>"
        f"</div></div>",
        unsafe_allow_html=True,
    )


def _render_strategie_recap(strat: dict) -> None:
    """Affiche le récap de la stratégie IA dans un expander."""
    realisme_labels = {
        "facile": ("🟢", "Facile", "#10b981"),
        "realiste": ("🟡", "Réaliste", "#84cc16"),
        "ambitieux": ("🟠", "Ambitieux", "#f59e0b"),
        "tres_ambitieux": ("🔴", "Très ambitieux", "#ef4444"),
    }
    real = strat.get("realisme", "realiste")
    icone, label, couleur = realisme_labels.get(real, ("⚪", real, "#6b7280"))

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Réalisme", f"{icone} {label}")
    with c2:
        st.metric("Heures totales", f"{strat.get('heures_total_estimees', 0)}h")
    with c3:
        st.metric("Hebdo recommandées", f"{strat.get('heures_par_semaine', 0):.1f}h")

    just = strat.get("justification", "")
    if just:
        st.markdown(f"💡 **Diagnostic IA :** {just}")

    # Conseils
    conseils = strat.get("conseils", [])
    if conseils:
        st.markdown("**🎯 Conseils :**")
        for c in conseils:
            st.markdown(f"- {c}")

    # Pondérations (cachées dans un sous-expander car techniques)
    pond = strat.get("ponderations_chapitres", {})
    if pond:
        st.caption(
            f"📊 {len(pond)} chapitres pondérés (boostés dans tes futurs plannings). "
            f"Max boost : ×{max(pond.values()):.1f}"
        )


# ---------------------------------------------------------------------------
# Mode 2 — Formulaire de création
# ---------------------------------------------------------------------------
def _render_form_creation() -> None:
    """Formulaire d'objectif → preview de stratégie → adoption."""
    st.subheader("➕ Nouvel objectif personnalisé")

    # Récup des cours pour le selectbox
    with get_session() as session:
        matiere_list = session.query(Matiere).filter(Matiere.actif.is_(True)).order_by(Matiere.nom).all()
        matiere_options = {"🌐 Global (toutes les matières)": None}
        for m in matiere_list:
            matiere_options[f"📘 {m.nom}"] = m.id

    # Form
    with st.form("form_create_objectif", clear_on_submit=False):
        nom = st.text_input(
            "Titre de l'objectif*",
            placeholder="Ex: 15/20 au partiel d'algèbre",
            max_chars=200,
        )
        description = st.text_area(
            "Description (motivations, contraintes, contexte)",
            placeholder="Ex: Examen mi-juin. Je suis faible sur diagonalisation. J'ai 2h/jour dispo.",
            height=80,
            max_chars=2000,  # borne pour éviter d'envoyer un mur de texte au LLM
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            matiere_label = st.selectbox("Matière visée", list(matiere_options.keys()))
            matiere_id = matiere_options[matiere_label]
        with c2:
            note_cible = st.number_input(
                "Note cible /20 (0 = qualitatif)",
                min_value=0.0, max_value=20.0, value=14.0, step=0.5,
            )
        with c3:
            date_cible = st.date_input(
                "Date cible*",
                value=datetime.date.today() + datetime.timedelta(days=30),
                min_value=datetime.date.today(),
            )

        submitted = st.form_submit_button("🧠 Demander une stratégie à l'IA", type="primary")

    if submitted:
        if not nom.strip():
            st.error("❌ Le titre est obligatoire.")
            return
        if date_cible <= datetime.date.today():
            st.error("❌ La date cible doit être dans le futur.")
            return

        note_value = note_cible if note_cible > 0 else None

        with st.spinner("🧠 Gemini analyse ton état actuel et prépare une stratégie… (~30 sec)"):
            try:
                with get_session() as session:
                    strategie = objs.proposer_strategie(
                        session=session,
                        nom=nom,
                        description=description,
                        matiere_id=matiere_id,
                        note_cible=note_value,
                        date_cible=date_cible,
                    )
            except Exception as exc:
                st.error(f"❌ Erreur Gemini : {exc}")
                return

        # On stocke en session_state pour pouvoir afficher la preview + bouton adopter
        st.session_state["objectif_preview"] = {
            "nom": nom, "description": description, "matiere_id": matiere_id,
            "note_cible": note_value, "date_cible": date_cible.isoformat(),
            "strategie": strategie,
        }
        st.rerun()

    # Preview de la stratégie si dispo
    preview = st.session_state.get("objectif_preview")
    if preview:
        st.divider()
        st.markdown("### 🧠 Stratégie proposée par l'IA")
        _render_strategie_recap(preview["strategie"])

        st.divider()
        a, b = st.columns(2)
        with a:
            if st.button("✅ Adopter cette stratégie", type="primary", width='stretch'):
                try:
                    with session_scope() as session:
                        objs.creer_objectif(
                            session=session,
                            nom=preview["nom"],
                            description=preview["description"],
                            matiere_id=preview["matiere_id"],
                            note_cible=preview["note_cible"],
                            date_cible=datetime.date.fromisoformat(preview["date_cible"]),
                            strategie=preview["strategie"],
                        )
                except Exception as exc:
                    st.error(f"❌ Erreur lors de la création : {exc}")
                    return
                st.session_state.pop("objectif_preview", None)
                st.toast("🎯 Objectif créé ! Les pondérations sont actives.", icon="✅")
                st.balloons()
                st.rerun()
        with b:
            if st.button("🔄 Demander une autre proposition", width='stretch'):
                st.session_state.pop("objectif_preview", None)
                st.rerun()




if __name__ == "__main__":
    render()
