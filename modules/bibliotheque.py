"""Onglet **Bibliothèque de cours**.

Permet de :
- Gérer des **UE** (Unités d'Enseignement) — regrouper des cours par discipline.
- Ajouter des cours (1 PDF à la fois OU batch multi-PDFs).
- Suivre la progression de maîtrise par chapitre.
- Activer / piloter la révision espacée (Leitner) — bouton "Réviser ce chapitre".
"""

from __future__ import annotations

import datetime
import time as _time
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from database import Chapitre, Cours, Matiere, Profil, UE, get_session, session_scope
from database.db import PDF_DIR
from services.pdf_analyzer import analyze_pdf, apply_analysis_to_course
from services.revision_service import (
    initialiser_chapitres_pour_revision,
    label_couleur_status,
    MAX_NIVEAU,
)


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
TYPES_TRAVAIL: list[str] = [
    "premiere_lecture",
    "comprehension",
    "fiches",
    "exercices",
    "relecture",
]

# Palette de couleurs proposée pour les UE (l'utilisateur peut surcharger)
UE_COLORS_DEFAULT = [
    "#4cd137",  # vert
    "#0097e6",  # bleu
    "#9c88ff",  # violet
    "#fbc531",  # jaune
    "#e84118",  # rouge
    "#00bc8c",  # vert sombre
    "#e1b12c",  # or
    "#d35400",  # orange foncé
]


# ---------------------------------------------------------------------------
# Helpers d'accès BD
# ---------------------------------------------------------------------------
def _get_api_config() -> tuple[str, str]:
    """Récupère la clé API et le modèle depuis le profil."""
    with get_session() as session:
        p = session.query(Profil).first()
        if not p or not p.gemini_api_key:
            return "", "gemini-2.5-flash"
        return p.gemini_api_key, p.gemini_model


def _delete_cours(cours_id: int) -> None:
    """Supprime un cours et son fichier PDF associé."""
    with session_scope() as session:
        cours = session.get(Cours, cours_id)
        if cours:
            if cours.pdf_path:
                pdf_file = PDF_DIR / f"{cours_id}.pdf"
                if pdf_file.exists():
                    pdf_file.unlink()
            session.delete(cours)


def _get_ues_snapshot(session: Session) -> list[dict[str, Any]]:
    """Récupère toutes les UE actives en snapshot dict (survit à la fermeture
    de session)."""
    ues = session.query(UE).filter_by(actif=True).order_by(UE.nom).all()
    return [
        {
            "id": ue.id,
            "nom": ue.nom,
            "code": ue.code,
            "semestre": ue.semestre,
            "credits_ects": ue.credits_ects,
            "couleur": ue.couleur,
            "nb_cours": len(ue.cours),
            "nb_matieres": len(ue.matieres),
        }
        for ue in ues
    ]


def _get_matieres_snapshot(session: Session) -> list[dict[str, Any]]:
    """Snapshot dict des matières actives (survit à la fermeture de session)."""
    matieres = session.query(Matiere).filter_by(actif=True).order_by(Matiere.nom).all()
    return [
        {
            "id": m.id,
            "nom": m.nom,
            "code": m.code,
            "ue_id": m.ue_id,
            "ue_nom": m.ue.nom if m.ue else None,
            "ue_couleur": m.ue.couleur if m.ue else "#6b7280",
            "nb_cours": len(m.cours),
        }
        for m in matieres
    ]


def _build_rattachement_options(
    ues: list[dict], matieres: list[dict],
) -> dict[str, dict[str, int | None]]:
    """Construit les options pour un selectbox combiné UE/Matière.

    Retourne {label_affiché: {"matiere_id": int|None, "ue_id": int|None}}.
    Ordre logique : Aucun → puis pour chaque UE, UE seule + ses matières →
    puis matières sans UE → enfin "aucune UE, aucune matière".
    """
    options: dict[str, dict[str, int | None]] = {
        "— Aucun rattachement (cours autonome) —": {"matiere_id": None, "ue_id": None},
    }

    # Groupement matières par UE pour respecter l'ordre
    matieres_par_ue: dict[int | None, list[dict]] = {}
    for m in matieres:
        matieres_par_ue.setdefault(m["ue_id"], []).append(m)

    # Pour chaque UE : option "UE seule" + ses matières
    for ue in ues:
        options[f"🎓 {ue['nom']}  (UE seule, sans matière)"] = {
            "matiere_id": None, "ue_id": ue["id"],
        }
        for m in matieres_par_ue.get(ue["id"], []):
            label = f"🎓 {ue['nom']} ▸ 📘 {m['nom']}"
            options[label] = {"matiere_id": m["id"], "ue_id": ue["id"]}

    # Matières SANS UE
    for m in matieres_par_ue.get(None, []):
        options[f"📘 {m['nom']}  (sans UE)"] = {"matiere_id": m["id"], "ue_id": None}

    return options


def _find_rattachement_label(
    options: dict[str, dict[str, int | None]],
    matiere_id: int | None,
    ue_id: int | None,
) -> str:
    """Trouve le label correspondant au rattachement actuel."""
    for label, vals in options.items():
        if vals["matiere_id"] == matiere_id and vals["ue_id"] == ue_id:
            return label
    return "— Aucun rattachement (cours autonome) —"


# ---------------------------------------------------------------------------
# Section : gestion des UE
# ---------------------------------------------------------------------------
def _render_ues_section() -> None:
    """Liste les UE existantes + formulaire de création."""
    st.subheader("🎓 Mes Unités d'Enseignement (UE)")
    st.caption(
        "Une UE regroupe plusieurs cours (matières). Ex: l'UE *Mathématiques* "
        "contient les cours *Analyse* et *Algèbre linéaire*. Le coefficient et "
        "la date d'examen restent au niveau de chaque cours."
    )

    with get_session() as session:
        ue_items = _get_ues_snapshot(session)

    if ue_items:
        editing_id = st.session_state.get("editing_ue")
        for ue in ue_items:
            with st.container(border=True):
                if editing_id == ue["id"]:
                    _render_ue_edit_form(ue)
                else:
                    col_a, col_b, col_c1, col_c2 = st.columns([5, 2, 0.5, 0.5])
                    with col_a:
                        meta = []
                        if ue["code"]:
                            meta.append(f"`{ue['code']}`")
                        if ue["semestre"]:
                            meta.append(ue["semestre"])
                        if ue["credits_ects"]:
                            meta.append(f"{ue['credits_ects']:.0f} ECTS")
                        meta_str = " · ".join(meta) if meta else "—"
                        st.markdown(
                            f"<div style='display:flex; align-items:center; gap:10px;'>"
                            f"<div style='width:14px; height:14px; background:{ue['couleur']}; "
                            f"border-radius:3px; flex-shrink:0;'></div>"
                            f"<div><b style='font-size:1.05rem;'>{ue['nom']}</b><br/>"
                            f"<span style='color:#6b7280; font-size:0.85rem;'>{meta_str}</span></div>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                    with col_b:
                        st.markdown(
                            f"<div style='padding-top:0.7rem;'>📚 <b>{ue['nb_cours']}</b> cours rattachés</div>",
                            unsafe_allow_html=True,
                        )
                    with col_c1:
                        if st.button(
                            "✏️",
                            key=f"edit_ue_{ue['id']}",
                            help="Modifier cette UE",
                        ):
                            st.session_state["editing_ue"] = ue["id"]
                            st.rerun()
                    with col_c2:
                        if st.button(
                            "🗑️",
                            key=f"del_ue_{ue['id']}",
                            help="Supprimer cette UE (les cours rattachés deviendront autonomes)",
                        ):
                            with session_scope() as s:
                                ue_db = s.get(UE, ue["id"])
                                if ue_db:
                                    s.delete(ue_db)
                            st.toast(f"UE '{ue['nom']}' supprimée", icon="🗑️")
                            st.rerun()
    else:
        st.info(
            "ℹ️ Aucune UE pour le moment. Crée-en une si tu veux regrouper tes cours."
        )

    # Formulaire de création
    with st.expander("➕ Créer une nouvelle UE", expanded=False):
        with st.form("form_create_ue", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                nom_ue = st.text_input("Nom de l'UE*", placeholder="Ex: Mathématiques")
                code_ue = st.text_input("Code (optionnel)", placeholder="Ex: MATH301")
                semestre_ue = st.text_input("Semestre (optionnel)", placeholder="Ex: S5")
            with col2:
                # Suggère une couleur basée sur le nb d'UE existantes
                default_color = UE_COLORS_DEFAULT[len(ue_items) % len(UE_COLORS_DEFAULT)]
                credits_ue = st.number_input(
                    "ECTS de l'UE (optionnel)",
                    min_value=0.0,
                    value=6.0,
                    step=0.5,
                )
                couleur_ue = st.color_picker("Couleur", value=default_color)

            submit_ue = st.form_submit_button(
                "Créer l'UE",
                type="primary",
                width='stretch',
            )

        if submit_ue:
            if not nom_ue.strip():
                st.error("Le nom de l'UE est obligatoire.")
                return
            with session_scope() as session:
                session.add(UE(
                    nom=nom_ue.strip(),
                    code=code_ue.strip(),
                    semestre=semestre_ue.strip(),
                    credits_ects=credits_ue if credits_ue > 0 else None,
                    couleur=couleur_ue,
                    actif=True,
                ))
            st.toast(f"UE '{nom_ue}' créée ✅", icon="🎓")
            st.rerun()


def _render_ue_edit_form(ue: dict) -> None:
    """Formulaire inline d'édition d'une UE existante."""
    st.markdown(f"**✏️ Modifier l'UE : {ue['nom']}**")
    col1, col2 = st.columns(2)
    with col1:
        new_nom = st.text_input(
            "Nom*",
            value=ue["nom"],
            key=f"edit_ue_nom_{ue['id']}",
        )
        new_code = st.text_input(
            "Code",
            value=ue["code"] or "",
            key=f"edit_ue_code_{ue['id']}",
        )
        new_semestre = st.text_input(
            "Semestre",
            value=ue["semestre"] or "",
            key=f"edit_ue_semestre_{ue['id']}",
        )
    with col2:
        new_ects = st.number_input(
            "ECTS",
            min_value=0.0,
            value=float(ue["credits_ects"] or 0.0),
            step=0.5,
            key=f"edit_ue_ects_{ue['id']}",
        )
        new_couleur = st.color_picker(
            "Couleur",
            value=ue["couleur"] or "#4cd137",
            key=f"edit_ue_couleur_{ue['id']}",
        )

    col_save, col_cancel = st.columns(2)
    with col_save:
        if st.button(
            "💾 Enregistrer",
            key=f"save_ue_{ue['id']}",
            type="primary",
            width='stretch',
        ):
            if not new_nom.strip():
                st.error("Le nom est obligatoire.")
                return
            with session_scope() as session:
                ue_db = session.get(UE, ue["id"])
                if ue_db:
                    ue_db.nom = new_nom.strip()
                    ue_db.code = new_code.strip()
                    ue_db.semestre = new_semestre.strip()
                    ue_db.credits_ects = new_ects if new_ects > 0 else None
                    ue_db.couleur = new_couleur
            st.session_state.pop("editing_ue", None)
            st.toast(f"UE '{new_nom}' mise à jour ✅", icon="✏️")
            st.rerun()
    with col_cancel:
        if st.button(
            "✖ Annuler",
            key=f"cancel_ue_{ue['id']}",
            width='stretch',
        ):
            st.session_state.pop("editing_ue", None)
            st.rerun()


# ---------------------------------------------------------------------------
# Section : gestion des Matières
# ---------------------------------------------------------------------------
def _render_matieres_section() -> None:
    """Liste des matières + formulaire de création."""
    st.subheader("📘 Mes Matières")
    st.caption(
        "Une matière est une **sous-discipline** d'une UE. "
        "Ex: l'UE *Maths* contient les matières *Algèbre* et *Analyse*. "
        "Chaque matière peut contenir plusieurs cours (PDFs : cours magistral, TD…). "
        "La matière hérite de la couleur de son UE."
    )

    with get_session() as session:
        ue_items = _get_ues_snapshot(session)
        matiere_items = _get_matieres_snapshot(session)

    if matiere_items:
        # Groupement visuel par UE
        matieres_par_ue: dict[int | None, list[dict]] = {}
        for m in matiere_items:
            matieres_par_ue.setdefault(m["ue_id"], []).append(m)

        # D'abord les matières rattachées à une UE, dans l'ordre des UE
        for ue in ue_items:
            if ue["id"] not in matieres_par_ue:
                continue
            st.markdown(
                f"<div style='color:#374151; font-weight:600; margin-top:0.6rem; "
                f"display:flex; align-items:center; gap:8px;'>"
                f"<div style='width:10px; height:10px; background:{ue['couleur']}; "
                f"border-radius:2px;'></div>🎓 {ue['nom']}</div>",
                unsafe_allow_html=True,
            )
            for m in matieres_par_ue[ue["id"]]:
                _render_matiere_row(m, ue_items)

        # Puis les matières sans UE
        if None in matieres_par_ue:
            st.markdown(
                "<div style='color:#374151; font-weight:600; margin-top:0.6rem;'>"
                "📁 Matières sans UE</div>",
                unsafe_allow_html=True,
            )
            for m in matieres_par_ue[None]:
                _render_matiere_row(m, ue_items)
    else:
        st.info("ℹ️ Aucune matière pour le moment. Crée-en une via le formulaire ci-dessous.")

    # Formulaire de création
    with st.expander("➕ Créer une nouvelle matière", expanded=False):
        with st.form("form_create_matiere", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                nom_m = st.text_input("Nom de la matière*", placeholder="Ex: Algèbre")
                code_m = st.text_input("Code (optionnel)", placeholder="Ex: MATH301-A")
            with col2:
                # Selectbox UE de rattachement
                ue_choices = {"— Aucune UE —": None}
                for ue in ue_items:
                    ue_choices[f"🎓 {ue['nom']}"] = ue["id"]
                ue_choice = st.selectbox(
                    "UE de rattachement (optionnel)",
                    options=list(ue_choices.keys()),
                )
                ue_id_m = ue_choices[ue_choice]

            submit_m = st.form_submit_button(
                "Créer la matière",
                type="primary",
                width='stretch',
            )

        if submit_m:
            if not nom_m.strip():
                st.error("Le nom de la matière est obligatoire.")
                return
            with session_scope() as session:
                session.add(Matiere(
                    nom=nom_m.strip(),
                    code=code_m.strip(),
                    ue_id=ue_id_m,
                    actif=True,
                ))
            st.toast(f"Matière '{nom_m}' créée ✅", icon="📘")
            st.rerun()


def _render_matiere_row(m: dict, ue_items: list[dict]) -> None:
    """Affiche une ligne pour une matière (avec boutons modify + delete) ou
    son formulaire d'édition."""
    editing_id = st.session_state.get("editing_matiere")
    with st.container(border=True):
        if editing_id == m["id"]:
            _render_matiere_edit_form(m, ue_items)
            return

        col_a, col_b, col_c1, col_c2 = st.columns([5, 2, 0.5, 0.5])
        with col_a:
            meta = []
            if m["code"]:
                meta.append(f"`{m['code']}`")
            meta_str = " · ".join(meta) if meta else "—"
            st.markdown(
                f"<div style='display:flex; align-items:center; gap:10px;'>"
                f"<div style='width:10px; height:10px; background:{m['ue_couleur']}; "
                f"border-radius:2px; flex-shrink:0;'></div>"
                f"<div><b>📘 {m['nom']}</b><br/>"
                f"<span style='color:#6b7280; font-size:0.82rem;'>{meta_str}</span></div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        with col_b:
            st.markdown(
                f"<div style='padding-top:0.6rem; font-size:0.9rem;'>"
                f"📚 <b>{m['nb_cours']}</b> cours</div>",
                unsafe_allow_html=True,
            )
        with col_c1:
            if st.button(
                "✏️", key=f"edit_matiere_{m['id']}",
                help="Modifier cette matière",
            ):
                st.session_state["editing_matiere"] = m["id"]
                st.rerun()
        with col_c2:
            if st.button(
                "🗑️", key=f"del_matiere_{m['id']}",
                help="Supprimer cette matière (les cours rattachés perdront ce rattachement)",
            ):
                with session_scope() as s:
                    m_db = s.get(Matiere, m["id"])
                    if m_db:
                        s.delete(m_db)
                st.toast(f"Matière '{m['nom']}' supprimée", icon="🗑️")
                st.rerun()


def _render_matiere_edit_form(m: dict, ue_items: list[dict]) -> None:
    """Formulaire inline d'édition d'une matière existante."""
    st.markdown(f"**✏️ Modifier la matière : {m['nom']}**")
    col1, col2 = st.columns(2)
    with col1:
        new_nom = st.text_input(
            "Nom*",
            value=m["nom"],
            key=f"edit_matiere_nom_{m['id']}",
        )
        new_code = st.text_input(
            "Code",
            value=m["code"] or "",
            key=f"edit_matiere_code_{m['id']}",
        )
    with col2:
        ue_choices = {"— Aucune UE —": None}
        for ue in ue_items:
            ue_choices[f"🎓 {ue['nom']}"] = ue["id"]

        current_label = "— Aucune UE —"
        for lbl, uid in ue_choices.items():
            if uid == m["ue_id"]:
                current_label = lbl
                break
        current_idx = list(ue_choices.keys()).index(current_label)

        new_ue_choice = st.selectbox(
            "UE de rattachement",
            options=list(ue_choices.keys()),
            index=current_idx,
            key=f"edit_matiere_ue_{m['id']}",
        )
        new_ue_id = ue_choices[new_ue_choice]

    col_save, col_cancel = st.columns(2)
    with col_save:
        if st.button(
            "💾 Enregistrer",
            key=f"save_matiere_{m['id']}",
            type="primary",
            width='stretch',
        ):
            if not new_nom.strip():
                st.error("Le nom est obligatoire.")
                return
            with session_scope() as session:
                m_db = session.get(Matiere, m["id"])
                if m_db:
                    m_db.nom = new_nom.strip()
                    m_db.code = new_code.strip()
                    m_db.ue_id = new_ue_id
            st.session_state.pop("editing_matiere", None)
            st.toast(f"Matière '{new_nom}' mise à jour ✅", icon="✏️")
            st.rerun()
    with col_cancel:
        if st.button(
            "✖ Annuler",
            key=f"cancel_matiere_{m['id']}",
            width='stretch',
        ):
            st.session_state.pop("editing_matiere", None)
            st.rerun()


# ---------------------------------------------------------------------------
# Formulaire d'ajout d'un cours (single PDF)
# ---------------------------------------------------------------------------
def _render_form_ajout() -> None:
    """Affiche et traite le formulaire d'ajout d'UN cours détaillé."""
    api_key, model = _get_api_config()

    if not api_key:
        st.warning(
            "⚠️ Aucune clé API Gemini n'est configurée. "
            "Rends-toi dans l'onglet **Profil** pour en ajouter une avant d'importer un cours."
        )
        return

    # Liste des UE et matières pour le selectbox combiné
    with get_session() as session:
        ue_items = _get_ues_snapshot(session)
        matiere_items = _get_matieres_snapshot(session)
    rattachement_options = _build_rattachement_options(ue_items, matiere_items)

    with st.expander("➕ Ajouter un cours (formulaire détaillé)", expanded=False):
        with st.form("form_ajout_cours", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                nom = st.text_input("Nom du cours*", max_chars=200,
                                    placeholder="Ex: Cours d'algèbre - polycopié")
                matiere = st.text_input("Libellé matière (libre, optionnel)", max_chars=100,
                                        help="Pour mémo. Le rattachement formel se fait via le selectbox ci-dessous.")
                professeur = st.text_input("Professeur", max_chars=200)
            with col2:
                coef = st.number_input("Coefficient", min_value=0.1, value=1.0, step=0.5)
                ects = st.number_input("Crédits ECTS du cours", min_value=0.0, value=3.0, step=0.5)
                date_exam = st.date_input("Date de l'examen", value=None)
                duree_exam = st.number_input("Durée de l'examen (min)", min_value=30, value=120, step=30)

            # Selectbox combiné UE ▸ Matière (F2)
            rattachement_choice = st.selectbox(
                "Rattachement (optionnel)",
                options=list(rattachement_options.keys()),
                help="Crée d'abord tes UE / matières dans les sections ci-dessus.",
            )
            rattachement = rattachement_options[rattachement_choice]
            ue_id_choisi = rattachement["ue_id"]
            matiere_id_choisi = rattachement["matiere_id"]

            st.divider()
            uploaded_pdf = st.file_uploader("Document du cours (PDF)*", type=["pdf"])

            submit = st.form_submit_button(
                "Ajouter et analyser (IA)",
                type="primary",
                width='stretch',
            )

        if submit:
            if not nom.strip():
                st.error("Le nom du cours est obligatoire.")
                return
            if not uploaded_pdf:
                st.error("Veuillez uploader un fichier PDF.")
                return

            # ÉTAPE 1 : Création coquille
            try:
                with session_scope() as session:
                    nouveau_cours = Cours(
                        nom=nom.strip(),
                        matiere=matiere.strip(),
                        professeur=professeur.strip(),
                        coefficient=coef,
                        credits_ects=ects,
                        date_examen=date_exam,
                        duree_examen_min=int(duree_exam),
                        actif=True,
                        ue_id=ue_id_choisi,
                        matiere_id=matiere_id_choisi,
                    )
                    session.add(nouveau_cours)
                    session.flush()
                    cours_id = nouveau_cours.id

                    pdf_path = PDF_DIR / f"{cours_id}.pdf"
                    pdf_path.write_bytes(uploaded_pdf.getvalue())
                    nouveau_cours.pdf_path = str(
                        pdf_path.relative_to(PDF_DIR.parent.parent)
                    )
            except Exception as e:
                st.error(f"Erreur lors de l'enregistrement initial : {e}")
                return

            # ÉTAPE 2 : Analyse Gemini
            analyse_reussie = False
            with st.spinner("🧠 Analyse du PDF par Gemini (30s à 1 min)..."):
                try:
                    analyse = analyze_pdf(
                        pdf_path=pdf_path,
                        cours_nom=nom.strip(),
                        matiere=matiere.strip(),
                        api_key=api_key,
                        model=model,
                    )
                    analyse_reussie = True
                except Exception as e:
                    st.error(f"❌ Échec de l'analyse IA : {e}")
                    _delete_cours(cours_id)
                    return

            # ÉTAPE 3 : Création chapitres + activation révision
            if analyse_reussie:
                try:
                    with session_scope() as session:
                        apply_analysis_to_course(session, cours_id, analyse)
                        session.flush()  # rend les chapitres visibles à la query suivante
                        
                        # Phase 3 : bascule vers la Matière et ajout du PDF au chapitre
                        cours_db = session.get(Cours, cours_id)
                        if cours_db:
                            pdf_info = {
                                "path": cours_db.pdf_path,
                                "label": "Document source",
                                "uploaded_at": datetime.datetime.now().isoformat()
                            }
                            for ch in cours_db.chapitres:
                                ch.matiere_id = matiere_id_choisi
                                ch.pdfs = [pdf_info]
                                
                        n_init = initialiser_chapitres_pour_revision(session, cours_id)
                    st.toast(
                        f"Cours '{nom}' ajouté — {n_init} chapitres rattachés à la matière ✅",
                        icon="✅",
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Erreur lors de la création des chapitres : {e}")
                    _delete_cours(cours_id)
                    return


# ---------------------------------------------------------------------------
# Import batch multi-PDFs
# ---------------------------------------------------------------------------
def _nom_cours_from_filename(filename: str) -> str:
    """Convertit un nom de fichier en nom de cours lisible.

    Exemples :
        'chimie_organique.pdf'      → 'Chimie organique'
        'L2-PHY-quantique-2024.pdf' → 'L2 PHY quantique 2024'
        'Bio.Mol.III.pdf'           → 'Bio Mol III'
    """
    stem = Path(filename).stem
    if stem.lstrip(".").lower() in ("", "pdf"):
        return "Nouveau cours"
    for sep in ("_", "-", "."):
        stem = stem.replace(sep, " ")
    stem = " ".join(stem.split())
    if not stem:
        return "Nouveau cours"
    return stem[0].upper() + stem[1:]


def _process_batch_import(
    uploaded_pdfs: list,
    noms_cours: list[str],
    matiere_commune: str,
    ects_default: float,
    duree_examen_default: int,
    api_key: str,
    model: str,
    ue_id_commune: int | None,
    matiere_id_commune: int | None,
) -> tuple[int, list[tuple[str, str]]]:
    """Importe en série N PDFs."""
    total = len(uploaded_pdfs)
    progress = st.progress(0.0, text="Initialisation…")
    placeholder = st.empty()

    success = 0
    erreurs: list[tuple[str, str]] = []
    t_global = _time.time()

    for i, (pdf_file, nom_cours) in enumerate(zip(uploaded_pdfs, noms_cours), 1):
        nom_cours = (nom_cours or "").strip() or _nom_cours_from_filename(pdf_file.name)
        progress.progress((i - 1) / total, text=f"PDF {i}/{total} : {nom_cours}…")
        placeholder.info(
            f"🧠 Analyse Gemini en cours pour **{nom_cours}** "
            f"({pdf_file.name}) — PDF {i}/{total}"
        )

        cours_id = None
        try:
            with session_scope() as session:
                nouveau_cours = Cours(
                    nom=nom_cours,
                    matiere=matiere_commune.strip(),
                    professeur="",
                    coefficient=1.0,
                    credits_ects=ects_default,
                    date_examen=None,
                    duree_examen_min=int(duree_examen_default),
                    actif=True,
                    ue_id=ue_id_commune,
                    matiere_id=matiere_id_commune,
                )
                session.add(nouveau_cours)
                session.flush()
                cours_id = nouveau_cours.id

                pdf_path = PDF_DIR / f"{cours_id}.pdf"
                pdf_path.write_bytes(pdf_file.getvalue())
                nouveau_cours.pdf_path = str(
                    pdf_path.relative_to(PDF_DIR.parent.parent)
                )

            analyse = analyze_pdf(
                pdf_path=pdf_path,
                cours_nom=nom_cours,
                matiere=matiere_commune.strip(),
                api_key=api_key,
                model=model,
            )

            with session_scope() as session:
                apply_analysis_to_course(session, cours_id, analyse)
                session.flush()
                
                # Phase 3 : bascule vers la Matière et ajout du PDF au chapitre
                cours_db = session.get(Cours, cours_id)
                if cours_db:
                    pdf_info = {
                        "path": cours_db.pdf_path,
                        "label": "Document source",
                        "uploaded_at": datetime.datetime.now().isoformat()
                    }
                    for ch in cours_db.chapitres:
                        ch.matiere_id = matiere_id_commune
                        ch.pdfs = [pdf_info]
                        
                initialiser_chapitres_pour_revision(session, cours_id)

            success += 1
        except Exception as exc:
            erreurs.append((pdf_file.name, str(exc)))
            if cours_id is not None:
                try:
                    _delete_cours(cours_id)
                except Exception:
                    pass

    progress.progress(1.0, text=f"Terminé en {int(_time.time() - t_global)}s")
    placeholder.empty()
    return success, erreurs


def _render_batch_import() -> None:
    """Section pour importer plusieurs PDFs d'un coup."""
    api_key, model = _get_api_config()
    if not api_key:
        return

    with get_session() as session:
        ue_items = _get_ues_snapshot(session)
        matiere_items = _get_matieres_snapshot(session)
    rattachement_options = _build_rattachement_options(ue_items, matiere_items)

    with st.expander("📦 Importer plusieurs PDFs d'un coup", expanded=False):
        st.caption(
            "Sélectionne plusieurs cours en PDF. Le nom de chaque cours est "
            "déduit du nom de fichier (modifiable). L'IA analyse les PDFs **en série** "
            "(~30-60 s/PDF), ne ferme pas l'onglet. Tu pourras éditer les dates "
            "d'examen, coefficients, etc. dans la liste des cours ci-dessous."
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            matiere_commune = st.text_input(
                "Libellé matière (libre, optionnel)",
                placeholder="Pour mémo",
                key="batch_matiere",
            )
        with col2:
            ects_default = st.number_input(
                "ECTS par défaut", min_value=0.0, value=3.0, step=0.5,
                key="batch_ects",
            )
        with col3:
            duree_examen_default = st.number_input(
                "Durée examen défaut (min)", min_value=30, value=120, step=30,
                key="batch_duree",
            )

        # Selectbox combiné UE ▸ Matière (F2)
        rattachement_choice_batch = st.selectbox(
            "Rattachement commun (optionnel)",
            options=list(rattachement_options.keys()),
            help="Tous les PDFs importés seront rattachés au même UE/Matière.",
            key="batch_rattachement",
        )
        rattachement_batch = rattachement_options[rattachement_choice_batch]
        ue_id_batch = rattachement_batch["ue_id"]
        matiere_id_batch = rattachement_batch["matiere_id"]

        uploaded_pdfs = st.file_uploader(
            "Sélectionne tes PDFs (Ctrl+clic pour plusieurs)*",
            type=["pdf"],
            accept_multiple_files=True,
            key="batch_pdf_uploader",
        )

        if not uploaded_pdfs:
            return

        st.caption(
            f"📄 **{len(uploaded_pdfs)} PDF(s) sélectionné(s)** — édite les noms si besoin :"
        )
        df_preview = pd.DataFrame([
            {
                "Fichier": pdf.name,
                "Taille": f"{len(pdf.getvalue()) / 1024:.0f} ko",
                "Nom du cours": _nom_cours_from_filename(pdf.name),
            }
            for pdf in uploaded_pdfs
        ])

        edited_df = st.data_editor(
            df_preview,
            hide_index=True,
            width='stretch',
            disabled=["Fichier", "Taille"],
            column_config={
                "Fichier":      st.column_config.TextColumn(width="medium"),
                "Taille":       st.column_config.TextColumn(width="small"),
                "Nom du cours": st.column_config.TextColumn(
                    width="medium",
                    required=True,
                    help="Tu peux modifier avant l'analyse IA.",
                ),
            },
            key="batch_names_editor",
        )

        eta_min = len(uploaded_pdfs) * 45 / 60
        st.caption(
            f"⏱️ Temps estimé : ~**{eta_min:.1f} min** "
            f"({len(uploaded_pdfs)} PDF × ~45s)"
        )

        if st.button(
            f"🚀 Importer les {len(uploaded_pdfs)} PDFs (analyse IA)",
            type="primary",
            width='stretch',
            key="btn_batch_import",
        ):
            noms_edites = edited_df["Nom du cours"].fillna("").tolist()
            if any(not n.strip() for n in noms_edites):
                st.error("❌ Tous les cours doivent avoir un nom.")
                return

            success, erreurs = _process_batch_import(
                uploaded_pdfs,
                noms_edites,
                matiere_commune,
                ects_default,
                int(duree_examen_default),
                api_key,
                model,
                ue_id_batch,
                matiere_id_batch,
            )

            total = len(uploaded_pdfs)
            if success == total:
                st.success(f"✅ **{success}/{total}** cours importés avec succès !")
                st.balloons()
                st.info(
                    "💡 Pense à éditer les **dates d'examen** et **coefficients** "
                    "dans la liste des cours ci-dessous."
                )
            elif success > 0:
                st.warning(
                    f"⚠️ **{success}/{total}** importés, **{len(erreurs)}** erreur(s)."
                )
                with st.expander("Détails des erreurs", expanded=True):
                    for nom, err in erreurs:
                        st.error(f"**{nom}** : {err}")
            else:
                st.error("❌ Aucun PDF n'a pu être importé.")
                with st.expander("Détails des erreurs", expanded=True):
                    for nom, err in erreurs:
                        st.error(f"**{nom}** : {err}")


# ---------------------------------------------------------------------------
# Carte d'un Chapitre (Nouvelle UI centré Chapitre)
# ---------------------------------------------------------------------------
def _render_carte_chapitre(chap: Chapitre, session: Session) -> None:
    """Affiche la carte détaillée d'un chapitre (remplace la carte cours)."""
    maitrise = chap.maitrise_pct or 0.0
    alerte = False
    
    # On détermine si on doit afficher une alerte basée sur la révision
    label_rev, color_rev = label_couleur_status(chap)
    if chap.date_prochaine:
        delta = (chap.date_prochaine - datetime.date.today()).days
        if delta <= 0 and maitrise < 50:
            alerte = True

    # Header du chapitre
    titre_expander = f"📑 Ch. {chap.numero} : {chap.titre}"
    titre_expander += f" — Maîtrise : {int(maitrise)}%"

    with st.expander(titre_expander, expanded=alerte):
        if alerte:
            st.error(
                f"⚠️ **Alerte :** Révision en retard et maîtrise "
                f"faible ({int(maitrise)}%)."
            )

        # Ligne 1 : Progression & Info IA
        col_prog, col_meta = st.columns([3, 1])
        with col_prog:
            st.progress(
                int(maitrise) / 100.0,
                text=f"Maîtrise actuelle : {int(maitrise)}%",
            )
        with col_meta:
            st.caption(f"⏱️ Temps estimé : {chap.temps_estime_h}h")
            
        st.divider()

        # Ligne 2 : Actions & PDF
        col_act, col_pdf = st.columns([1, 1])
        
        with col_act:
            st.markdown("##### ⚙️ Progression & Révision")
            
            with st.form(f"form_prog_{chap.id}", border=False):
                new_maitrise = st.slider("Niveau de maîtrise (%)", 0, 100, int(maitrise), 5)
                new_etape = st.selectbox("Prochaine étape", options=TYPES_TRAVAIL, index=TYPES_TRAVAIL.index(chap.type_travail_restant) if chap.type_travail_restant in TYPES_TRAVAIL else 0)
                
                if st.form_submit_button("💾 Enregistrer"):
                    try:
                        ch_db = session.get(Chapitre, chap.id)
                        if ch_db:
                            ch_db.maitrise_pct = new_maitrise
                            ch_db.type_travail_restant = new_etape
                            session.commit()
                            st.toast("Progression mise à jour !", icon="✅")
                            st.rerun()
                    except Exception as e:
                        session.rollback()
                        st.error(f"Erreur : {e}")

            st.markdown(
                f"<div style='color:{color_rev}; font-size:0.9rem; margin-top:10px;'>"
                f"<b>{label_rev}</b> (Niveau Leitner : {chap.niveau_actuel or 0}/{MAX_NIVEAU})"
                f"</div>",
                unsafe_allow_html=True
            )
            
            if st.button("🧠 Ouvrir la Salle d'étude", key=f"btn_study_{chap.id}", type="primary"):
                st.session_state.target_chapitre_id = chap.id
                try:
                    st.switch_page("pages/session_etude.py")
                except Exception:
                    st.rerun()

        with col_pdf:
            st.markdown("##### 📄 Documents (PDF)")
            pdfs = chap.pdfs or []
            
            if not pdfs:
                st.info("Aucun document associé.")
            else:
                for idx, pdf_info in enumerate(pdfs):
                    col_p1, col_p2 = st.columns([4, 1])
                    with col_p1:
                        st.markdown(f"📎 **{pdf_info.get('label', 'Document')}**\n<small>{pdf_info.get('path')}</small>", unsafe_allow_html=True)
                    with col_p2:
                        if st.button("🗑️", key=f"del_pdf_{chap.id}_{idx}"):
                            try:
                                ch_db = session.get(Chapitre, chap.id)
                                if ch_db:
                                    current_pdfs = list(ch_db.pdfs)
                                    current_pdfs.pop(idx)
                                    # Pour forcer SQLAlchemy à détecter la modif du JSON
                                    ch_db.pdfs = current_pdfs
                                    session.commit()
                                    st.rerun()
                            except Exception as e:
                                session.rollback()
                                st.error(f"Erreur : {e}")

            with st.expander("➕ Ajouter un PDF"):
                with st.form(f"form_pdf_{chap.id}", clear_on_submit=True, border=False):
                    new_pdf = st.file_uploader("Fichier", type=["pdf"])
                    new_label = st.text_input("Label (ex: TD, Fiche...)", value="Document")
                    if st.form_submit_button("Ajouter"):
                        if new_pdf:
                            pdf_path = PDF_DIR / f"chap_{chap.id}_{int(_time.time())}.pdf"
                            pdf_path.write_bytes(new_pdf.getvalue())
                            
                            try:
                                ch_db = session.get(Chapitre, chap.id)
                                if ch_db:
                                    current_pdfs = list(ch_db.pdfs or [])
                                    current_pdfs.append({
                                        "path": str(pdf_path.relative_to(PDF_DIR.parent.parent)),
                                        "label": new_label,
                                        "uploaded_at": datetime.datetime.now().isoformat()
                                    })
                                    ch_db.pdfs = current_pdfs
                                    session.commit()
                                    st.toast("PDF ajouté !", icon="📄")
                                    st.rerun()
                            except Exception as e:
                                session.rollback()
                                st.error(f"Erreur : {e}")

        # Zone danger
        st.divider()
        if st.button("🗑️ Supprimer le chapitre", key=f"del_chap_{chap.id}", type="secondary"):
            try:
                ch_db = session.get(Chapitre, chap.id)
                if ch_db:
                    session.delete(ch_db)
                    session.commit()
                    st.toast("Chapitre supprimé.", icon="🗑️")
                    st.rerun()
            except Exception as e:
                session.rollback()
                st.error(f"Erreur : {e}")


# ---------------------------------------------------------------------------
# Rendu principal — avec regroupement par UE
# ---------------------------------------------------------------------------
def render() -> None:
    """Point d'entrée appelé par st.Page."""
    st.title("📚 Bibliothèque de cours")
    st.caption(
        "Organise tes cours selon la hiérarchie **🎓 UE ▸ 📘 Matière ▸ 📑 Chapitre**, "
        "importe les PDFs, et active la révision espacée."
    )

    # 1. Sections UE et Matières (CRUD)
    _render_ues_section()
    st.divider()
    _render_matieres_section()
    st.divider()

    # 2. Imports
    _render_form_ajout()
    _render_batch_import()

    st.divider()
    st.subheader("Mon Programme")

    with get_session() as session:
        ues = session.query(UE).filter_by(actif=True).order_by(UE.nom).all()
        matieres = session.query(Matiere).filter_by(actif=True).order_by(Matiere.nom).all()
        chapitres = session.query(Chapitre).all()

        if not chapitres:
            st.info(
                "Ta bibliothèque est vide. Ajoute un premier cours via le formulaire ci-dessus."
            )
            return

        # Index pour O(1) lookups
        chaps_par_matiere: dict[int, list[Chapitre]] = {}
        chaps_par_ue_directe: dict[int, list[Chapitre]] = {}  # UE sans matière
        chaps_autonomes: list[Chapitre] = []
        
        for ch in chapitres:
            # Compatibilité : utiliser le nouveau matiere_id, sinon fallback sur l'ancien cours
            m_id = ch.matiere_id if ch.matiere_id else (ch.cours.matiere_id if ch.cours else None)
            u_id = ch.cours.ue_id if (ch.cours and not m_id) else None
            
            if m_id:
                chaps_par_matiere.setdefault(m_id, []).append(ch)
            elif u_id:
                chaps_par_ue_directe.setdefault(u_id, []).append(ch)
            else:
                chaps_autonomes.append(ch)

        matieres_par_ue: dict[int, list[Matiere]] = {}
        matieres_sans_ue: list[Matiere] = []
        for m in matieres:
            if m.ue_id:
                matieres_par_ue.setdefault(m.ue_id, []).append(m)
            else:
                matieres_sans_ue.append(m)

        # --- Affichage hiérarchique : UE → Matière → Chapitre -------------
        for ue in ues:
            ue_matieres = matieres_par_ue.get(ue.id, [])
            ue_chaps_directs = chaps_par_ue_directe.get(ue.id, [])
            # Skip cette UE si elle n'a rien à afficher
            if not ue_matieres and not ue_chaps_directs:
                continue

            meta_parts = []
            if ue.code:
                meta_parts.append(f"`{ue.code}`")
            if ue.semestre:
                meta_parts.append(ue.semestre)
            if ue.credits_ects:
                meta_parts.append(f"{ue.credits_ects:.0f} ECTS")
            meta_str = " · ".join(meta_parts) if meta_parts else ""

            st.markdown(
                f"<div style='display:flex; align-items:center; gap:10px; "
                f"margin-top:1.5rem; margin-bottom:0.5rem; "
                f"border-left:4px solid {ue.couleur}; padding:6px 12px; "
                f"background:{ue.couleur}15; border-radius:4px;'>"
                f"<div style='font-size:1.3rem; font-weight:600;'>🎓 {ue.nom}</div>"
                f"<div style='color:#6b7280; font-size:0.9rem;'>{meta_str}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

            # Affiche d'abord les matières (avec leurs chapitres)
            for matiere in ue_matieres:
                matiere_chaps = chaps_par_matiere.get(matiere.id, [])
                code_part = f" · `{matiere.code}`" if matiere.code else ""
                st.markdown(
                    f"<div style='margin-top:0.8rem; margin-bottom:0.6rem; "
                    f"margin-left:1rem; color:#374151;'>"
                    f"<b style='font-size:1.05rem;'>📘 {matiere.nom}</b>"
                    f"<span style='color:#9ca3af; font-size:0.85rem;'>{code_part}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if not matiere_chaps:
                    st.caption("  ↳ _Aucun chapitre rattaché pour le moment._")
                else:
                    for ch in matiere_chaps:
                        _render_carte_chapitre(ch, session)

            # Puis chapitres directement rattachés à l'UE (sans matière)
            if ue_chaps_directs:
                st.markdown(
                    "<div style='margin-top:0.8rem; margin-bottom:0.6rem; "
                    "margin-left:1rem; color:#374151;'>"
                    "<b style='font-size:1rem;'>📑 Chapitres sans matière (directement dans l'UE)</b>"
                    "</div>",
                    unsafe_allow_html=True,
                )
                for ch in ue_chaps_directs:
                    _render_carte_chapitre(ch, session)

        # --- Matières sans UE -------------------------------------------
        for matiere in matieres_sans_ue:
            matiere_chaps = chaps_par_matiere.get(matiere.id, [])
            if not matiere_chaps:
                continue
            st.markdown(
                f"<div style='display:flex; align-items:center; gap:10px; "
                f"margin-top:1.5rem; margin-bottom:0.5rem; "
                f"border-left:4px solid #6b7280; padding:6px 12px; "
                f"background:#6b728015; border-radius:4px;'>"
                f"<div style='font-size:1.2rem; font-weight:600;'>📘 {matiere.nom}</div>"
                f"<div style='color:#6b7280; font-size:0.9rem;'>(matière sans UE)</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
            for ch in matiere_chaps:
                _render_carte_chapitre(ch, session)

        # --- Chapitres totalement autonomes (ni UE ni matière) --------------
        if chaps_autonomes:
            st.markdown(
                "<div style='display:flex; align-items:center; gap:10px; "
                "margin-top:1.5rem; margin-bottom:0.5rem; "
                "border-left:4px solid #6b7280; padding:6px 12px; "
                "background:#6b728015; border-radius:4px;'>"
                "<div style='font-size:1.2rem; font-weight:600;'>📁 Chapitres autonomes</div>"
                "<div style='color:#6b7280; font-size:0.9rem;'>"
                "Chapitres orphelins (issus d'anciens cours sans rattachement)."
                "</div></div>",
                unsafe_allow_html=True,
            )
            for ch in chaps_autonomes:
                _render_carte_chapitre(ch, session)