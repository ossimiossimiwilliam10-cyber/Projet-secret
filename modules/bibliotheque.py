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

from database import Chapitre, Matiere, Utilisateur, UE, get_session, session_scope
from database.db import PDF_DIR
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
            "nb_matieres": len(ue.matieres),
            "nb_chapitres": sum(len(m.chapitres) for m in ue.matieres),
        }
        for ue in ues
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
    st.title("📚 Bibliothèque")
    st.caption(
        "Organise ton programme selon la hiérarchie **🎓 UE ▸ 📘 Matière ▸ 📑 Chapitre**, "
        "importe les PDFs, et active la révision espacée."
    )

    # 1. Sections UE et Matières (CRUD)
    _render_ues_section()
    st.divider()
    _render_matieres_section()
    st.divider()

    # 2. Import unifié — 1 ou N PDFs → 1 ou N chapitres rattachés à une Matière
    _render_import_unifie()

    st.divider()
    st.subheader("Mon Programme")

    with get_session() as session:
        # Eager loading complet : 3 requêtes au lieu de potentiellement
        # 1 + N(matières) + N×M(chapitres) sur l'affichage hiérarchique.
        ues = (
            session.query(UE)
            .options(selectinload(UE.matieres))
            .filter_by(actif=True)
            .order_by(UE.nom)
            .all()
        )
        matieres = (
            session.query(Matiere)
            .options(selectinload(Matiere.ue), selectinload(Matiere.chapitres))
            .filter_by(actif=True)
            .order_by(Matiere.nom)
            .all()
        )
        chapitres = session.query(Chapitre).all()

        if not chapitres:
            st.info(
                "Ta bibliothèque est vide. Ajoute un premier cours via le formulaire ci-dessus."
            )
            return

        # Index pour O(1) lookups : un chapitre est soit rattaché à une
        # matière, soit autonome (rare, surtout pendant la transition).
        chaps_par_matiere: dict[int, list[Chapitre]] = {}
        chaps_autonomes: list[Chapitre] = []

        for ch in chapitres:
            if ch.matiere_id:
                chaps_par_matiere.setdefault(ch.matiere_id, []).append(ch)
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
            # Skip cette UE si elle n'a pas de matières rattachées.
            if not ue_matieres:
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