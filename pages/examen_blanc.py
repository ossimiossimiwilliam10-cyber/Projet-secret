"""Page Examen Blanc — Évaluation globale multi-chapitres."""

from __future__ import annotations

import streamlit as st
from database import get_session, session_scope, Chapitre, Matiere

def render() -> None:
    st.title("📝 Examen Blanc")
    st.caption("Génère un examen blanc multi-matières pour évaluer ta compréhension globale.")

    if "exam_state" not in st.session_state:
        st.session_state.exam_state = "setup" # setup, taking, result
        st.session_state.exam_questions = []
        st.session_state.exam_context = ""
        st.session_state.exam_eval = None
        st.session_state.exam_chap_ids = []

    if st.session_state.exam_state == "setup":
        _render_setup()
    elif st.session_state.exam_state == "taking":
        _render_taking()
    elif st.session_state.exam_state == "result":
        _render_result()

def _render_setup() -> None:
    st.subheader("⚙️ Configuration de l'examen")
    
    with get_session() as session:
        matieres = session.query(Matiere).filter_by(actif=True).all()
        mat_options = {m.nom: m.id for m in matieres}
        
    if not mat_options:
        st.info("Aucune matière trouvée.")
        return

    selected_mats = st.multiselect("Choisis les matières à inclure", list(mat_options.keys()))
    nb_questions = st.slider("Nombre de questions", min_value=1, max_value=15, value=5)

    if st.button("🚀 Générer l'examen", type="primary", use_container_width=True):
        if not selected_mats:
            st.error("Sélectionne au moins une matière.")
            return
            
        mat_ids = [mat_options[m] for m in selected_mats]
        with get_session() as session:
            chaps = session.query(Chapitre).filter(Chapitre.matiere_id.in_(mat_ids)).all()
            
        if not chaps:
            st.error("Aucun chapitre trouvé pour les matières sélectionnées.")
            return

        with st.spinner("🧠 L'IA prépare ton examen..."):
            try:
                from services.ai_exam_service import generer_examen
                with get_session() as session:
                    questions, context = generer_examen(session, chaps, nb_questions)
                    st.session_state.exam_questions = questions
                    st.session_state.exam_context = context
                    st.session_state.exam_chap_ids = [c.id for c in chaps]
                    st.session_state.exam_state = "taking"
                st.rerun()
            except Exception as e:
                st.error(f"Erreur de génération: {e}")

def _render_taking() -> None:
    st.subheader(f"📝 Examen en cours — {len(st.session_state.exam_questions)} questions")
    
    with st.form("exam_form"):
        responses = []
        for i, q in enumerate(st.session_state.exam_questions):
            st.markdown(f"**Question {i+1}** : {q}")
            resp = st.text_area(f"Réponse {i+1}", key=f"q_{i}", label_visibility="collapsed")
            responses.append(resp)
            
        submitted = st.form_submit_button("✅ Soumettre mes réponses", type="primary", use_container_width=True)
        if submitted:
            # Vérifier que toutes les questions ont une réponse (même vide)
            with st.spinner("🧠 L'IA évalue tes réponses..."):
                try:
                    from services.ai_exam_service import evaluer_examen
                    with get_session() as session:
                        result = evaluer_examen(
                            session, 
                            st.session_state.exam_questions, 
                            responses, 
                            st.session_state.exam_context
                        )
                        st.session_state.exam_eval = result
                        st.session_state.exam_state = "result"
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur lors de l'évaluation: {e}")
                    
    if st.button("❌ Annuler l'examen"):
        st.session_state.exam_state = "setup"
        st.rerun()

def _render_result() -> None:
    res = st.session_state.exam_eval
    if not res:
        st.error("Aucun résultat trouvé.")
        return

    score = res.get("score_num", 0)
    verdict = res.get("verdict", "?")

    st.markdown("---")
    st.markdown(f"## 🎯 Résultat : {int(score * 100)}%")
    if verdict.lower() == "réussi":
        st.success(f"✅ **{verdict.upper()}** — Bravo !")
        st.balloons()
    else:
        st.warning(f"📚 **{verdict.upper()}** — Continue à réviser.")

    st.markdown(res.get("message", ""))
    
    st.subheader("Détail par question")
    for j, r in enumerate(res.get("resultats", [])):
        score_str = r.get("score", "")
        if score_str == "correct":
            emoji = "✅"
        elif score_str == "partiel":
            emoji = "⚠️"
        else:
            emoji = "❌"
            
        with st.expander(f"{emoji} Question {j+1}", expanded=True):
            st.markdown(f"**Q:** {st.session_state.exam_questions[j]}")
            st.markdown(f"**Feedback:** {r.get('feedback', '')}")

    st.divider()
    if st.button("🔄 Passer un nouvel examen", type="primary", use_container_width=True):
        st.session_state.exam_state = "setup"
        st.rerun()

render()
