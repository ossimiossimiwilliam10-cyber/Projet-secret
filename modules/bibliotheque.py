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

from sqlalchemy.orm import selectinload

from database import Chapitre, Matiere, Semestre, UE, Utilisateur, get_session, session_scope, PDF_DIR
from services.pdf_analyzer import analyze_pdf, apply_analysis_to_matiere
from services.pdf_storage import (
    PdfValidationError,
    compute_sha256,
    find_existing_upload,
    record_upload,
    safe_pdf_filename,
    validate_pdf_upload,
)
from services.profil_service import get_gemini_credentials
from services.revision_service import (
    initialiser_chapitre_pour_revision,
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
    """Récupère la clé API (déchiffrée) et le modèle depuis le profil."""
    with get_session() as session:
        return get_gemini_credentials(session)


def _get_ues_snapshot(session: Session) -> list[dict[str, Any]]:
    """Récupère toutes les UE actives en snapshot dict (survit à la fermeture
    de session).

    Eager loading : 1 requête au lieu de 1+N+N×M (UE → matières → chapitres).
    """
    ues = (
        session.query(UE)
        .options(selectinload(UE.matieres).selectinload(Matiere.chapitres))
        .filter_by(actif=True)
        .order_by(UE.nom)
        .all()
    )
    return [
        {
            "id": ue.id,
            "nom": ue.nom,
            "code": ue.code,
            "semestre": ue.semestre,
            "credits_ects": ue.credits_ects,
            "couleur": ue.couleur,
            "semestre_id": ue.semestre_id,
            "nb_matieres": len(ue.matieres),
            "nb_chapitres": sum(len(m.chapitres) for m in ue.matieres),
        }
        for ue in ues
    ]


def _get_semestres_snapshot(session: Session) -> list[dict[str, Any]]:
    """Récupère tous les semestres actifs en snapshot dict.

    Eager loading : 1 requête (Semestre → UE → Matières → Chapitres).
    """
    semestres = (
        session.query(Semestre)
        .options(selectinload(Semestre.ues).selectinload(UE.matieres).selectinload(Matiere.chapitres))
        .filter_by(actif=True)
        .order_by(Semestre.nom)
        .all()
    )
    return [
        {
            "id": s.id,
            "nom": s.nom,
            "code": s.code,
            "date_debut": s.date_debut,
            "date_fin": s.date_fin,
            "nb_ues": len(s.ues),
            "nb_matieres": sum(len(ue.matieres) for ue in s.ues),
            "nb_chapitres": sum(sum(len(m.chapitres) for m in ue.matieres) for ue in s.ues),
            "ects_total": sum(ue.credits_ects or 0 for ue in s.ues),
        }
        for s in semestres
    ]


def _get_matieres_snapshot(session: Session) -> list[dict[str, Any]]:
    """Snapshot dict des matières actives (survit à la fermeture de session).

    Eager loading : 1 requête au lieu de 1+N (Matière → UE → chapitres).
    """
    matieres = (
        session.query(Matiere)
        .options(selectinload(Matiere.ue), selectinload(Matiere.chapitres))
        .filter_by(actif=True)
        .order_by(Matiere.nom)
        .all()
    )
    return [
        {
            "id": m.id,
            "nom": m.nom,
            "code": m.code,
            "ue_id": m.ue_id,
            "ue_nom": m.ue.nom if m.ue else None,
            "ue_couleur": m.ue.couleur if m.ue else "#6b7280",
            "nb_chapitres": len(m.chapitres),
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
# Section : gestion des Semestres
# ---------------------------------------------------------------------------
def _render_semestres_section() -> None:
    """Liste les Semestres existants + formulaire de création."""
    st.subheader("📅 Mes Semestres")
    st.caption(
        "Un semestre regroupe plusieurs UE. Ex: le semestre *S5* "
        "contient les UE *Maths*, *Physique*, *Droit*. "
        "C'est le plus haut niveau d'organisation de ton programme."
    )

    with get_session() as session:
        semestre_items = _get_semestres_snapshot(session)

    if semestre_items:
        editing_id = st.session_state.get("editing_semestre")
        for s in semestre_items:
            with st.container(border=True):
                if editing_id == s["id"]:
                    _render_semestre_edit_form(s)
                else:
                    col_a, col_b, col_c1, col_c2 = st.columns([5, 2, 0.5, 0.5])
                    with col_a:
                        meta = []
                        if s["code"]:
                            meta.append(f"`{s['code']}`")
                        if s["date_debut"] and s["date_fin"]:
                            meta.append(f"{s['date_debut']} → {s['date_fin']}")
                        meta_str = " · ".join(meta) if meta else "—"
                        st.markdown(
                            f"<div>"
                            f"<b style='font-size:1.05rem;'>📅 {s['nom']}</b><br/>"
                            f"<span style='color:#6b7280; font-size:0.85rem;'>{meta_str}</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                    with col_b:
                        st.markdown(
                            f"<div style='padding-top:0.7rem;'>"
                            f"🎓 <b>{s['nb_ues']}</b> UE · 📘 <b>{s['nb_matieres']}</b> matières · "
                            f"🎯 <b>{s['ects_total']:.0f}</b> ECTS</div>",
                            unsafe_allow_html=True,
                        )
                    with col_c1:
                        if st.button("✏️", key=f"edit_sem_{s['id']}", help="Modifier ce semestre"):
                            st.session_state["editing_semestre"] = s["id"]
                            st.rerun()
                    with col_c2:
                        if st.button("🗑️", key=f"del_sem_{s['id']}", help="Supprimer (détache les UE)"):
                            with session_scope() as sess:
                                s_db = sess.get(Semestre, s["id"])
                                if s_db:
                                    sess.delete(s_db)
                            st.toast(f"Semestre '{s['nom']}' supprimé", icon="🗑️")
                            st.rerun()
    else:
        st.info("ℹ️ Aucun semestre pour le moment. Crée-en un pour organiser tes UE par période.")

    with st.expander("➕ Créer un nouveau semestre", expanded=False):
        with st.form("form_create_semestre", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                nom_sem = st.text_input("Nom*", placeholder="Ex: Semestre 5")
                code_sem = st.text_input("Code", placeholder="Ex: S5")
            with col2:
                date_debut_sem = st.date_input("Date de début", value=None)
                date_fin_sem = st.date_input("Date de fin", value=None)
            if st.form_submit_button("Créer le semestre", type="primary", width='stretch'):
                if not nom_sem.strip():
                    st.error("Le nom est obligatoire.")
                else:
                    with session_scope() as session:
                        session.add(Semestre(
                            nom=nom_sem.strip(), code=code_sem.strip(),
                            date_debut=date_debut_sem, date_fin=date_fin_sem,
                            actif=True,
                        ))
                    st.toast(f"Semestre '{nom_sem}' créé ✅", icon="📅")
                    st.rerun()


def _render_semestre_edit_form(s: dict) -> None:
    """Formulaire inline d'édition d'un semestre."""
    st.markdown(f"**✏️ Modifier le semestre : {s['nom']}**")
    col1, col2 = st.columns(2)
    with col1:
        new_nom = st.text_input("Nom*", value=s["nom"], key=f"edit_sem_nom_{s['id']}")
        new_code = st.text_input("Code", value=s["code"] or "", key=f"edit_sem_code_{s['id']}")
    with col2:
        new_debut = st.date_input("Date début", value=s["date_debut"], key=f"edit_sem_debut_{s['id']}")
        new_fin = st.date_input("Date fin", value=s["date_fin"], key=f"edit_sem_fin_{s['id']}")

    col_save, col_cancel = st.columns(2)
    with col_save:
        if st.button("💾 Enregistrer", key=f"save_sem_{s['id']}", type="primary", width='stretch'):
            if not new_nom.strip():
                st.error("Le nom est obligatoire.")
                return
            with session_scope() as session:
                s_db = session.get(Semestre, s["id"])
                if s_db:
                    s_db.nom = new_nom.strip()
                    s_db.code = new_code.strip()
                    s_db.date_debut = new_debut
                    s_db.date_fin = new_fin
            st.session_state.pop("editing_semestre", None)
            st.toast(f"Semestre '{new_nom}' mis à jour ✅", icon="✏️")
            st.rerun()
    with col_cancel:
        if st.button("✖ Annuler", key=f"cancel_sem_{s['id']}", width='stretch'):
            st.session_state.pop("editing_semestre", None)
            st.rerun()


# ---------------------------------------------------------------------------
# Section : gestion des UE
# ---------------------------------------------------------------------------
def _render_ues_section() -> None:
    """Liste les UE existantes + formulaire de création."""
    st.subheader("🎓 Mes Unités d'Enseignement (UE)")
    st.caption(
        "Une UE regroupe plusieurs matières. Ex: l'UE *Mathématiques* "
        "contient les matières *Analyse* et *Algèbre linéaire*. L'UE porte "
        "les crédits ECTS et le code semestre."
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
                            f"<div style='padding-top:0.7rem;'>📘 <b>{ue['nb_matieres']}</b> matière(s) · 📑 <b>{ue['nb_chapitres']}</b> chapitre(s)</div>",
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
                            help="Supprimer cette UE (les matières rattachées deviendront autonomes)",
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
                # Selectbox Semestre de rattachement
                with get_session() as s2:
                    semestres = s2.query(Semestre).filter_by(actif=True).order_by(Semestre.nom).all()
                sem_choices = {"— Aucun semestre —": None}
                for sem in semestres:
                    sem_choices[f"📅 {sem.nom}"] = sem.id
                sem_choice = st.selectbox("Semestre de rattachement", options=list(sem_choices.keys()))
                semestre_id_ue = sem_choices[sem_choice]

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
                    semestre_id=semestre_id_ue,
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
        "Chaque matière contient plusieurs chapitres, avec leurs PDFs (cours magistral, TD, polycopié…). "
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
                f"📑 <b>{m['nb_chapitres']}</b> chapitre(s)</div>",
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
                help="Supprimer cette matière (ses chapitres et leurs PDFs seront aussi supprimés)",
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
# Import unifié — 1 ou N PDFs vers une Matière (refonte bibliothèque)
# ---------------------------------------------------------------------------
def _process_import_unifie(
    uploaded_pdfs: list,
    matiere_id: int,
    labels: list[str],
    api_key: str,
    model: str,
) -> tuple[int, int, list[tuple[str, str]]]:
    """Analyse un ou plusieurs PDFs et crée les chapitres rattachés à la Matière.

    Chaque PDF est analysé indépendamment par Gemini, qui détecte les chapitres
    présents dans le document. Chaque chapitre détecté devient une ligne en
    base, avec le PDF source référencé dans le champ ``pdfs``.

    Returns:
        ``(nb_pdfs_ok, nb_chapitres_total, erreurs)`` où ``erreurs`` est une
        liste de tuples ``(nom_fichier, message)``.
    """
    with session_scope() as session:
        matiere = session.get(Matiere, matiere_id)
        matiere_nom = matiere.nom if matiere else "?"

    total = len(uploaded_pdfs)
    progress = st.progress(0.0, text="Initialisation…")
    placeholder = st.empty()

    pdfs_ok = 0
    chapitres_total = 0
    erreurs: list[tuple[str, str]] = []

    for i, (pdf_file, label) in enumerate(zip(uploaded_pdfs, labels), 1):
        label_clean = (label or "").strip() or _nom_cours_from_filename(pdf_file.name)
        progress.progress((i - 1) / total, text=f"PDF {i}/{total} : {label_clean}…")
        placeholder.info(
            f"🧠 Analyse Gemini en cours pour **{label_clean}** "
            f"({pdf_file.name}) — PDF {i}/{total}"
        )

        try:
            # 1. Validation + empreinte SHA-256.
            pdf_bytes = pdf_file.getvalue()
            validate_pdf_upload(pdf_bytes, pdf_file.name)
            sha = compute_sha256(pdf_bytes)

            # 2. Idempotence : si ce PDF a déjà été ingéré sur cette matière,
            # on saute l'analyse Gemini (économie d'argent).
            with session_scope() as session:
                existing = find_existing_upload(session, sha, matiere_id)
                already_imported = existing is not None
                existing_nb_chap = existing.nb_chapitres_crees if existing else 0

            if already_imported:
                erreurs.append((
                    pdf_file.name,
                    f"Déjà importé sur cette matière "
                    f"({existing_nb_chap} chapitres existants) — ignoré.",
                ))
                continue

            # 3. Écriture sur disque (nom déterministe, pas de path traversal).
            pdf_filename = safe_pdf_filename(matiere_id, int(_time.time()), i)
            pdf_path = PDF_DIR / pdf_filename
            pdf_path.write_bytes(pdf_bytes)
            pdf_rel = str(pdf_path.relative_to(PDF_DIR.parent.parent))

            # 4. Analyse Gemini du PDF. Si ça échoue (après retry), on
            # supprime le PDF qu'on vient d'écrire pour ne pas laisser
            # d'orphelin sur disque (atomicité).
            try:
                analyse = analyze_pdf(
                    pdf_path=pdf_path,
                    cours_nom=label_clean,
                    matiere=matiere_nom,
                    api_key=api_key,
                    model=model,
                )
            except Exception:
                pdf_path.unlink(missing_ok=True)
                raise

            # 5. Création des chapitres + trace de l'upload (atomique côté DB).
            # Si la transaction échoue, on supprime aussi le PDF.
            try:
                with session_scope() as session:
                    new_ids = apply_analysis_to_matiere(
                        session=session,
                        matiere_id=matiere_id,
                        analysis=analyse,
                        pdf_path=pdf_rel,
                        pdf_label=label_clean,
                    )
                    for chap_id in new_ids:
                        initialiser_chapitre_pour_revision(session, chap_id)
                    record_upload(
                        session,
                        sha=sha,
                        matiere_id=matiere_id,
                        filename_original=pdf_file.name,
                        filename_stored=pdf_filename,
                        label=label_clean,
                        nb_chapitres=len(new_ids),
                    )
            except Exception:
                pdf_path.unlink(missing_ok=True)
                raise

            pdfs_ok += 1
            chapitres_total += len(new_ids)
        except PdfValidationError as exc:
            erreurs.append((pdf_file.name, f"Validation : {exc}"))
        except Exception as exc:
            erreurs.append((pdf_file.name, str(exc)))

    progress.progress(1.0, text=f"Terminé : {pdfs_ok}/{total} PDFs traités.")
    placeholder.empty()
    return pdfs_ok, chapitres_total, erreurs


def _render_import_unifie() -> None:
    """Section d'import unifiée — 1 ou plusieurs PDFs rattachés à une Matière.

    Remplace les deux anciens formulaires séparés ("formulaire détaillé" avec
    coef/ECTS/dates d'examen, et "import batch multi-PDFs"). Tout passe par
    un seul mécanisme : tu choisis une matière, tu déposes 1 à N PDFs,
    Gemini détecte les chapitres dans chacun et les crée.
    """
    api_key, model = _get_api_config()
    if not api_key:
        st.warning(
            "⚠️ Aucune clé API Gemini n'est configurée. "
            "Rends-toi dans l'onglet **Utilisateur** pour en ajouter une avant "
            "d'importer un PDF."
        )
        return

    with get_session() as session:
        matieres = (
            session.query(Matiere)
            .filter_by(actif=True)
            .order_by(Matiere.nom)
            .all()
        )
        matiere_options = {m.nom: m.id for m in matieres}

    if not matiere_options:
        st.info(
            "📘 Crée d'abord une **Matière** dans la section ci-dessus avant "
            "d'importer des PDFs."
        )
        return

    with st.expander("📥 Importer des PDFs (1 ou plusieurs)", expanded=False):
        st.caption(
            "Sélectionne **la matière** de rattachement, puis dépose **un ou "
            "plusieurs PDFs**. Pour chacun, Gemini détecte les chapitres et "
            "les crée. Un même chapitre peut recueillir plusieurs PDFs plus "
            "tard via sa carte ci-dessous."
        )

        matiere_label = st.selectbox(
            "📘 Matière de rattachement*",
            options=list(matiere_options.keys()),
            key="import_unifie_matiere",
        )
        matiere_id = matiere_options[matiere_label]

        uploaded_pdfs = st.file_uploader(
            "Dépose 1 à N PDFs*",
            type=["pdf"],
            accept_multiple_files=True,
            key="import_unifie_uploader",
        )

        if not uploaded_pdfs:
            return

        st.caption(
            f"📄 **{len(uploaded_pdfs)} PDF(s) sélectionné(s)** — "
            "le libellé sert juste de mémo (modifiable) :"
        )
        df_preview = pd.DataFrame([
            {
                "Fichier": pdf.name,
                "Taille": f"{len(pdf.getvalue()) / 1024:.0f} ko",
                "Libellé": _nom_cours_from_filename(pdf.name),
            }
            for pdf in uploaded_pdfs
        ])
        edited_df = st.data_editor(
            df_preview,
            hide_index=True,
            width="stretch",
            disabled=["Fichier", "Taille"],
            column_config={
                "Fichier":  st.column_config.TextColumn(width="medium"),
                "Taille":   st.column_config.TextColumn(width="small"),
                "Libellé":  st.column_config.TextColumn(
                    width="medium", required=True,
                    help="Ex. : « Cours magistral », « Polycopié », « TD série 1 »",
                ),
            },
            key="import_unifie_editor",
        )

        eta_min = len(uploaded_pdfs) * 45 / 60
        st.caption(
            f"⏱️ Temps estimé : ~**{eta_min:.1f} min** "
            f"({len(uploaded_pdfs)} PDF × ~45 s d'analyse Gemini)."
        )

        if st.button(
            f"🚀 Importer et analyser ({len(uploaded_pdfs)} PDF{'s' if len(uploaded_pdfs) > 1 else ''})",
            type="primary",
            width="stretch",
            key="btn_import_unifie",
        ):
            labels = edited_df["Libellé"].fillna("").tolist()
            pdfs_ok, chapitres_total, erreurs = _process_import_unifie(
                uploaded_pdfs, matiere_id, labels, api_key, model,
            )

            total = len(uploaded_pdfs)
            if pdfs_ok == total and not erreurs:
                st.success(
                    f"✅ **{pdfs_ok}/{total}** PDF(s) analysé(s), "
                    f"**{chapitres_total}** chapitre(s) créé(s) dans « {matiere_label} »."
                )
                st.balloons()
                st.rerun()
            elif pdfs_ok > 0:
                st.warning(
                    f"⚠️ **{pdfs_ok}/{total}** PDFs traités "
                    f"({chapitres_total} chapitres), **{len(erreurs)}** erreur(s) :"
                )
                for nom, err in erreurs:
                    st.error(f"**{nom}** : {err}")
            else:
                st.error("❌ Aucun PDF n'a pu être importé.")
                for nom, err in erreurs:
                    st.error(f"**{nom}** : {err}")


# ---------------------------------------------------------------------------
# Helpers d'import — utilisés par _render_import_unifie
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



# ---------------------------------------------------------------------------
# Helpers visuels pour le Programme
# ---------------------------------------------------------------------------
def _chapter_title_html(ch: Chapitre) -> str:
    """Titre d'expander avec barre de progression intégrée."""
    m = int(ch.maitrise_pct or 0)
    bar_width = min(m // 10, 10)
    bar = "█" * bar_width + "░" * (10 - bar_width)
    label_rev, _ = label_couleur_status(ch)
    return f"📑 Ch.{ch.numero} : {ch.titre}  [{bar}] {m}%  {label_rev}"


def _render_ue_header_html(ue: UE, pct: float) -> None:
    """Bandeau UE avec barre de complétion."""
    meta_parts = []
    if ue.code:
        meta_parts.append(f"`{ue.code}`")
    if ue.credits_ects:
        meta_parts.append(f"{ue.credits_ects:.0f} ECTS")
    meta_str = " · ".join(meta_parts) if meta_parts else ""
    st.markdown(
        f"<div style='display:flex; align-items:center; gap:10px; "
        f"margin-top:1.2rem; margin-bottom:0.4rem; margin-left:1rem; "
        f"border-left:4px solid {ue.couleur}; padding:6px 14px; "
        f"background:{ue.couleur}15; border-radius:4px;'>"
        f"<div style='font-size:1.15rem; font-weight:600;'>🎓 {ue.nom}</div>"
        f"<div style='color:#6b7280; font-size:0.85rem;'>{meta_str}</div>"
        f"<div style='margin-left:auto; font-size:0.85rem;'>"
        f"Maîtrise UE : {int(pct)}%</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.progress(int(pct) / 100.0, text=f"  ")


# ---------------------------------------------------------------------------
# Carte d'un Chapitre (refonte UX : confirmation suppression)
# ---------------------------------------------------------------------------
def _render_carte_chapitre(chap: Chapitre, session: Session) -> None:
    """Affiche la carte détaillée d'un chapitre."""
    maitrise = chap.maitrise_pct or 0.0
    label_rev, color_rev = label_couleur_status(chap)

    # Ligne 1 : Progression
    col_prog, col_meta = st.columns([3, 1])
    with col_prog:
        st.progress(int(maitrise) / 100.0, text=f"Maîtrise : {int(maitrise)}%")
    with col_meta:
        st.caption(f"⏱️ {chap.temps_estime_h}h")
    st.divider()

    # Ligne 2 : Actions & PDF
    col_act, col_pdf = st.columns([1, 1])
    with col_act:
        st.markdown("##### ⚙️ Progression & Révision")
        with st.form(f"form_prog_{chap.id}", border=False):
            new_maitrise = st.slider("Niveau de maîtrise (%)", 0, 100, int(maitrise), 5)
            new_etape = st.selectbox(
                "Prochaine étape",
                options=TYPES_TRAVAIL,
                index=TYPES_TRAVAIL.index(chap.type_travail_restant) if chap.type_travail_restant in TYPES_TRAVAIL else 0,
            )
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
            unsafe_allow_html=True,
        )
        if st.button("🧠 Salle d'étude", key=f"btn_study_{chap.id}", type="primary"):
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
            import html as _html
            for idx, pdf_info in enumerate(pdfs):
                col_p1, col_p2 = st.columns([4, 1])
                with col_p1:
                    label_safe = _html.escape(str(pdf_info.get('label', 'Document')))
                    path_safe = _html.escape(str(pdf_info.get('path', '')))
                    st.markdown(
                        f"📎 <strong>{label_safe}</strong><br><small>{path_safe}</small>",
                        unsafe_allow_html=True,
                    )
                with col_p2:
                    if st.button("🗑️", key=f"del_pdf_{chap.id}_{idx}"):
                        try:
                            ch_db = session.get(Chapitre, chap.id)
                            if ch_db:
                                current_pdfs = list(ch_db.pdfs)
                                current_pdfs.pop(idx)
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
                                    "uploaded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                })
                                ch_db.pdfs = current_pdfs
                                session.commit()
                                st.toast("PDF ajouté !", icon="📄")
                                st.rerun()
                        except Exception as e:
                            session.rollback()
                            st.error(f"Erreur : {e}")

    # Zone danger — avec confirmation
    st.divider()
    st.markdown("##### 🧨 Zone de danger")
    confirm_key = f"confirm_del_{chap.id}"
    if confirm_key not in st.session_state:
        st.session_state[confirm_key] = False

    if not st.session_state[confirm_key]:
        if st.button("🗑️ Supprimer ce chapitre", key=f"del_chap_{chap.id}", type="secondary"):
            st.session_state[confirm_key] = True
            st.rerun()
    else:
        st.warning(f"⚠️ **Confirmation** : supprimer « {chap.titre} » ? Cette action est irréversible.")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("✅ Oui, supprimer", key=f"confirm_yes_{chap.id}", type="primary"):
                try:
                    ch_db = session.get(Chapitre, chap.id)
                    if ch_db:
                        session.delete(ch_db)
                        session.commit()
                    st.session_state.pop(confirm_key, None)
                    st.toast("Chapitre supprimé.", icon="🗑️")
                    st.rerun()
                except Exception as e:
                    session.rollback()
                    st.error(f"Erreur : {e}")
        with col_no:
            if st.button("❌ Annuler", key=f"confirm_no_{chap.id}"):
                st.session_state.pop(confirm_key, None)
                st.rerun()


# ---------------------------------------------------------------------------
# Rendu principal — avec regroupement par Semestre
# ---------------------------------------------------------------------------
def _render_kpis() -> None:
    """En-tête : 5 chiffres-clés (Semestres, UE, Matières, Chapitres, ECTS) + maîtrise."""
    with get_session() as session:
        nb_semestres = session.query(Semestre).filter_by(actif=True).count()
        nb_ues = session.query(UE).filter_by(actif=True).count()
        nb_matieres = session.query(Matiere).filter_by(actif=True).count()
        chapitres = session.query(Chapitre).all()
        nb_chapitres = len(chapitres)
        maitrise_moy = sum(float(c.maitrise_pct or 0) for c in chapitres) / nb_chapitres if nb_chapitres else 0.0
        ects_total = sum(ue.credits_ects or 0 for ue in session.query(UE).filter_by(actif=True).all())

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("📅 Semestres", nb_semestres)
    c2.metric("🎓 UE", nb_ues)
    c3.metric("📘 Matières", nb_matieres)
    c4.metric("📑 Chapitres", nb_chapitres)
    c5.metric("🎯 ECTS", f"{ects_total:.0f}")
    st.progress(int(maitrise_moy) / 100.0, text=f"📈 Maîtrise globale moyenne : {maitrise_moy:.0f}%")


def _render_programme_section() -> None:
    """Section « Mon Programme » — hiérarchie Semestre → UE → Matière → Chapitre
    avec recherche, filtres, urgences, barres de complétion."""
    with get_session() as session:
        semestres = (
            session.query(Semestre)
            .options(selectinload(Semestre.ues).selectinload(UE.matieres))
            .filter_by(actif=True).order_by(Semestre.nom).all()
        )
        ues = (
            session.query(UE).options(selectinload(UE.matieres))
            .filter_by(actif=True).order_by(UE.nom).all()
        )
        matieres = (
            session.query(Matiere)
            .options(selectinload(Matiere.ue), selectinload(Matiere.chapitres))
            .filter_by(actif=True).order_by(Matiere.nom).all()
        )
        chapitres = session.query(Chapitre).all()

        if not chapitres:
            st.info("Ta bibliothèque est vide. Importe un premier PDF via l'onglet « 📥 Importer ».")
            return

        chaps_par_matiere: dict[int, list[Chapitre]] = {}
        for ch in chapitres:
            if ch.matiere_id:
                chaps_par_matiere.setdefault(ch.matiere_id, []).append(ch)

        matieres_par_ue: dict[int, list[Matiere]] = {}
        matieres_sans_ue: list[Matiere] = []
        for m in matieres:
            if m.ue_id:
                matieres_par_ue.setdefault(m.ue_id, []).append(m)
            else:
                matieres_sans_ue.append(m)

        ues_par_semestre: dict[int, list[UE]] = {}
        ues_sans_semestre: list[UE] = []
        for ue in ues:
            if ue.semestre_id:
                ues_par_semestre.setdefault(ue.semestre_id, []).append(ue)
            else:
                ues_sans_semestre.append(ue)

        # --- Barre d'outils ---
        col_search, col_btns = st.columns([3, 2])
        with col_search:
            search_query = st.text_input(
                "🔍 Rechercher un chapitre, une matière, une UE...",
                placeholder="Ex: thermodynamique",
                key="prog_search",
                label_visibility="collapsed",
            )
        with col_btns:
            cb1, cb2, cb3 = st.columns(3)
            with cb1:
                if st.button("📂 Tout déplier", width="stretch", key="expand_all"):
                    st.session_state["prog_view"] = "all"
                    st.rerun()
            with cb2:
                if st.button("📁 Tout replier", width="stretch", key="collapse_all"):
                    st.session_state["prog_view"] = "none"
                    st.rerun()
            with cb3:
                nb_urgents = sum(
                    1 for ch in chapitres
                    if ch.date_prochaine
                    and (ch.date_prochaine - datetime.date.today()).days <= 0
                    and (ch.maitrise_pct or 0) < 50
                )
                if st.button(f"⚠️ Urgences ({nb_urgents})", width="stretch", key="expand_urgent"):
                    st.session_state["prog_view"] = "urgent"
                    st.rerun()

        view = st.session_state.get("prog_view", "all")
        search_lower = search_query.strip().lower() if search_query else ""

        def _matches(ch: Chapitre, m: Matiere, u: UE | None) -> bool:
            if not search_lower:
                return True
            return any(search_lower in s for s in [
                ch.titre.lower(), m.nom.lower(), (m.code or "").lower(),
                (u.nom.lower() if u else ""), ((u.code or "").lower() if u else ""),
            ])

        # --- Section « À réviser aujourd'hui » ---
        urgents: list[tuple[Chapitre, Matiere | None, UE | None]] = []
        for ch in chapitres:
            if ch.date_prochaine and (ch.date_prochaine - datetime.date.today()).days <= 0 and (ch.maitrise_pct or 0) < 50:
                m = next((x for x in matieres if x.id == ch.matiere_id), None)
                urgents.append((ch, m, m.ue if m else None))

        if urgents:
            st.markdown(
                f"<div style='margin-top:1rem; padding:10px 14px; background:#fef2f2; "
                f"border-left:4px solid #dc2626; border-radius:4px;'>"
                f"🔴 <b>{len(urgents)} chapitre(s) à réviser aujourd'hui</b>"
                f"</div>", unsafe_allow_html=True,
            )
            for ch, m, u in urgents:
                expanded = view in ("all", "urgent")
                with st.expander(_chapter_title_html(ch), expanded=expanded):
                    _render_carte_chapitre(ch, session)
            st.divider()

        # --- Hiérarchie : Semestre → UE → Matière → Chapitre ---
        for sem in semestres:
            sem_ues = ues_par_semestre.get(sem.id, [])
            if not sem_ues:
                continue
            sem_total, sem_maitrises = 0, []
            for ue in sem_ues:
                for m in matieres_par_ue.get(ue.id, []):
                    sem_total += len(chaps_par_matiere.get(m.id, []))
                    sem_maitrises.extend(ch.maitrise_pct or 0 for ch in chaps_par_matiere.get(m.id, []))
            if sem_total == 0:
                continue
            sem_pct = sum(sem_maitrises) / len(sem_maitrises) if sem_maitrises else 0
            ects_sem = sum(ue.credits_ects or 0 for ue in sem_ues)

            st.markdown(
                f"<div style='margin-top:1.5rem; padding:8px 14px; "
                f"border-left:5px solid #6366f1; background:#6366f108; border-radius:4px;'>"
                f"<span style='font-size:1.3rem; font-weight:700;'>📅 {sem.nom}</span>  "
                f"<span style='color:#6b7280;'>{sem_total} chap. · 🎯 {ects_sem:.0f} ECTS · Complétion {int(sem_pct)}%</span>"
                f"</div>", unsafe_allow_html=True,
            )
            st.progress(int(sem_pct) / 100.0, text=f"  ")

            for ue in sem_ues:
                _render_ue_in_programme(ue, matieres_par_ue, chaps_par_matiere, session, view, _matches)

        for ue in ues_sans_semestre:
            if matieres_par_ue.get(ue.id):
                _render_ue_in_programme(ue, matieres_par_ue, chaps_par_matiere, session, view, _matches)

        for matiere in matieres_sans_ue:
            matiere_chaps = chaps_par_matiere.get(matiere.id, [])
            if not matiere_chaps:
                continue
            st.markdown(
                f"<div style='margin-top:1.5rem; padding:6px 12px; "
                f"border-left:4px solid #6b7280; background:#6b728015; border-radius:4px;'>"
                f"<span style='font-size:1.1rem; font-weight:600;'>📘 {matiere.nom}</span>"
                f"<span style='color:#6b7280; font-size:0.9rem;'> (matière sans UE)</span></div>",
                unsafe_allow_html=True,
            )
            for ch in matiere_chaps:
                if search_lower and not (search_lower in ch.titre.lower() or search_lower in matiere.nom.lower() or (matiere.code and search_lower in matiere.code.lower())):
                    continue
                expanded = view == "all"
                if view == "urgent":
                    expanded = (ch.date_prochaine and (ch.date_prochaine - datetime.date.today()).days <= 0 and (ch.maitrise_pct or 0) < 50)
                with st.expander(_chapter_title_html(ch), expanded=expanded):
                    _render_carte_chapitre(ch, session)


def _render_ue_in_programme(ue, matieres_par_ue, chaps_par_matiere, session, view, _matches) -> None:
    """Affiche une UE et ses matières/chapitres dans le programme."""
    ue_matieres = matieres_par_ue.get(ue.id, [])
    if not ue_matieres:
        return
    ue_maitrises = []
    for m in ue_matieres:
        ue_maitrises.extend(ch.maitrise_pct or 0 for ch in chaps_par_matiere.get(m.id, []))
    ue_pct = sum(ue_maitrises) / len(ue_maitrises) if ue_maitrises else 0
    _render_ue_header_html(ue, ue_pct)

    for matiere in ue_matieres:
        matiere_chaps = chaps_par_matiere.get(matiere.id, [])
        if not matiere_chaps:
            continue
        code_part = f" · `{matiere.code}`" if matiere.code else ""
        st.markdown(
            f"<div style='margin:0.4rem 0 0.3rem 1.5rem; color:#374151;'>"
            f"<b>📘 {matiere.nom}</b><span style='color:#9ca3af; font-size:0.85rem;'>{code_part}</span>"
            f"</div>", unsafe_allow_html=True,
        )
        for ch in matiere_chaps:
            if not _matches(ch, matiere, ue):
                continue
            expanded = view == "all"
            if view == "urgent":
                expanded = (ch.date_prochaine and (ch.date_prochaine - datetime.date.today()).days <= 0 and (ch.maitrise_pct or 0) < 50)
            with st.expander(_chapter_title_html(ch), expanded=expanded):
                _render_carte_chapitre(ch, session)


# ---------------------------------------------------------------------------
# Point d'entrée — orchestration en onglets
# ---------------------------------------------------------------------------
def render() -> None:
    """Point d'entrée appelé par st.Page.

    Structure UI :
      - en-tête : titre + 5 KPIs (Semestres, UE, Matières, Chapitres, ECTS) + maîtrise
      - tabs : 📥 Importer | 📚 Mon Programme | ⚙️ Gérer
    """
    st.title("📚 Bibliothèque")
    st.caption(
        "Organise ton programme selon la hiérarchie "
        "**📅 Semestre ▸ 🎓 UE ▸ 📘 Matière ▸ 📑 Chapitre**, importe les PDFs, "
        "et active la révision espacée."
    )

    _render_kpis()
    st.divider()

    tab_import, tab_programme, tab_admin = st.tabs(
        ["📥 Importer", "📚 Mon Programme", "⚙️ Gérer"]
    )

    with tab_import:
        _render_import_unifie()

    with tab_programme:
        _render_programme_section()

    with tab_admin:
        _render_semestres_section()
        st.divider()
        _render_ues_section()
        st.divider()
        _render_matieres_section()