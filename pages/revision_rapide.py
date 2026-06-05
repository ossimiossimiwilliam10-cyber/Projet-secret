"""Page de révision rapide (Mode Swipe)."""

from __future__ import annotations

import streamlit as st
from database import get_session, session_scope, Chapitre
from services.revision_service import chapitres_a_reviser, appliquer_resultat_quiz, label_couleur_status

def render() -> None:
    st.title("⚡ Révision rapide")
    st.caption("Parcourt tous tes chapitres urgents en mode swipe. Valide ou passe.")

    with get_session() as session:
        # Récupère tous les chapitres dus
        urgents = chapitres_a_reviser(session)

    if not urgents:
        st.success("🎉 Aucun chapitre à réviser ! Tout est à jour.")
        if st.button("← Retour au tableau de bord"):
            st.switch_page("pages/revisions.py")
        return

    if "quick_idx" not in st.session_state:
        st.session_state.quick_idx = 0
    idx = st.session_state.quick_idx

    if idx >= len(urgents):
        st.balloons()
        st.success(f"🎉 Session terminée ! {len(urgents)} chapitres parcourus.")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Recommencer"):
                st.session_state.quick_idx = 0
                st.rerun()
        with col2:
            if st.button("← Retour au tableau de bord"):
                del st.session_state.quick_idx
                st.switch_page("pages/revisions.py")
        return

    chap = urgents[idx]
    
    st.progress(idx / len(urgents), text=f"Chapitre {idx+1}/{len(urgents)}")

    label, color = label_couleur_status(chap)
    mat_nom = chap.matiere_obj.nom if chap.matiere_obj else "Sans matière"

    with st.container(border=True):
        st.markdown(f"## {chap.titre}")
        st.caption(f"📘 {mat_nom} · Niveau {chap.niveau_actuel or 0} · <span style='color:{color}'>{label}</span>", unsafe_allow_html=True)
        st.progress((chap.niveau_actuel or 0) / 13)

        if chap.notes:
            st.info(chap.notes)
            
        if getattr(chap, "fiche_ia", None):
            with st.expander("🧠 Voir la fiche IA"):
                st.markdown(chap.fiche_ia)

        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Je connais — Valider", width="stretch", key=f"quick_ok_{chap.id}", type="primary"):
                with session_scope() as s:
                    # Simulation d'un quiz réussi (score = 1.0)
                    appliquer_resultat_quiz(s, chap.id, 1.0, mode="quick_swipe")
                st.session_state.quick_idx += 1
                st.rerun()
        with col2:
            if st.button("⏭️ Pas maintenant — Passer", width="stretch", key=f"quick_skip_{chap.id}"):
                st.session_state.quick_idx += 1
                st.rerun()

render()
