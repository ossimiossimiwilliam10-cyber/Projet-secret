"""Onglet **Études** de la saisie hebdomadaire.

L'étudiant sélectionne ses matières à travailler cette semaine et leurs
chapitres. L'app l'aide activement avec :

- **Filtre par Semestre** + sélection rapide.
- **Bandeau résumé** en temps réel (charge vs objectif).
- **Suggestion IA** des chapitres à prioriser cette semaine.
- **Indicateurs cognitifs** par matière (révisions Leitner dues,
  nouveaux chapitres, % maîtrise).
- **Cartes de chapitres** avec jauges visuelles.
- **Champ note pour l'IA** par matière.
- **Reprise de la sélection** de la semaine précédente.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy.orm import selectinload

from database import Chapitre, Matiere, Semestre, UE, Utilisateur, SaisieHebdo, get_session, session_scope
from services.matiere_stats import (
    estimer_charge_minutes,
    format_label_matiere,
    matieres_avec_revisions_dues,
    stats_matiere,
)
from services.revision_service import label_couleur_status
from services.scheduler_engine import calculer_cible_hebdo_minutes
from utils.helpers import get_or_create_week_for_offset

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
URGENCES = ["Normale", "Prioritaire", "Exam dans moins de 7 jours"]
PRIORITES = ["Basse", "Normale", "Haute"]
TYPES_TRAVAIL = [
    "Première lecture",
    "Révision / Compréhension",
    "Fiches de synthèse",
    "Exercices / Pratique",
    "Prépa Examen",
]


# ---------------------------------------------------------------------------
# Helpers locaux
# ---------------------------------------------------------------------------
def _type_travail_suggere(chapitres_choisis_db: list[Chapitre]) -> str:
    """Devine le type de travail selon le niveau Leitner des chapitres."""
    if not chapitres_choisis_db:
        return TYPES_TRAVAIL[0]
    nb_nouveaux = sum(1 for c in chapitres_choisis_db if (c.niveau_actuel or 0) == 0)
    if nb_nouveaux >= len(chapitres_choisis_db) / 2:
        return "Première lecture"
    return "Révision / Compréhension"


def _open_chapitre_in_session_etude(chap_id: int) -> None:
    """Ouvre la salle d'étude pour un chapitre donné."""
    st.session_state.target_chapitre_id = chap_id
    try:
        st.switch_page("pages/session_etude.py")
    except Exception:
        st.session_state.navigate_to_session = True


def _get_previous_week_selection(session, current_offset: int):
    """Récupère la sélection de la semaine précédente (offset - 1)."""
    try:
        prev_semaine, prev_saisie, _ = get_or_create_week_for_offset(
            session, offset_weeks=current_offset - 1,
        )
        return prev_saisie.matieres_selectionnees or []
    except Exception:
        return []


def _chapter_title_with_bar(ch: Chapitre) -> str:
    """Titre visuel d'un chapitre avec barre de progression."""
    m = int(ch.maitrise_pct or 0)
    bar_width = min(m // 10, 10)
    bar = "█" * bar_width + "░" * (10 - bar_width)
    label_rev, _ = label_couleur_status(ch)
    return f"Ch.{ch.numero} : {ch.titre}  [{bar}] {m}%  {label_rev}"


def _suggest_priority_chapters(
    session, selected_matiere_ids: list[int], stats_par_id: dict,
) -> list[dict]:
    """Heuristique locale : suggère les chapitres à prioriser cette semaine.
    Retourne une liste de dicts {chapitre, raison}."""
    suggestions: list[dict] = []
    today = _dt.date.today()

    for matiere_id in selected_matiere_ids:
        chapitres = (
            session.query(Chapitre)
            .filter_by(matiere_id=matiere_id)
            .all()
        )
        for ch in chapitres:
            raison = None
            if ch.date_prochaine and ch.date_prochaine <= today and (ch.maitrise_pct or 0) < 50:
                raison = "🔴 Révision en retard"
            elif ch.date_prochaine and (ch.date_prochaine - today).days <= 3:
                raison = "🟡 Révision imminente"
            elif (ch.maitrise_pct or 0) < 30:
                raison = "📊 Maîtrise faible"
            elif (ch.niveau_actuel or 0) == 0 and ch.date_prochaine is None:
                raison = "📑 Nouveau chapitre"
            if raison:
                suggestions.append({"chapitre": ch, "raison": raison})

    # Top 5 max
    suggestions.sort(key=lambda s: (s["chapitre"].maitrise_pct or 0))
    return suggestions[:5]


# ---------------------------------------------------------------------------
# Rendu UI
# ---------------------------------------------------------------------------
def render() -> None:
    st.subheader("📚 Études de la semaine")
    st.caption(
        "Sélectionne tes matières et chapitres. "
        "L'IA s'occupera ensuite de la répartition."
    )

    offset_courant = int(st.session_state.get("semaine_target_offset", 0))

    with get_session() as session:
        semaine, saisie, nb_reportees = get_or_create_week_for_offset(
            session, offset_weeks=offset_courant,
        )

        st.info(
            f"📅 **Semaine {semaine.numero_semaine}** : "
            f"du {semaine.date_debut.strftime('%d/%m')} au {semaine.date_fin.strftime('%d/%m/%Y')}"
        )

        if nb_reportees > 0:
            st.warning(
                f"🔁 **{nb_reportees} tâche(s) reportées** de la semaine précédente. "
                "Va sur l'onglet **Projets & Tâches** pour les ajuster."
            )

        matieres_selectionnees_db: list[dict[str, Any]] = saisie.matieres_selectionnees or []
        travaux_ponctuels_db: list[dict[str, Any]] = saisie.travaux_ponctuels or []

        # --- Chargement des données ---
        toutes_matieres = (
            session.query(Matiere)
            .options(selectinload(Matiere.ue).selectinload(UE.semestre))
            .filter_by(actif=True)
            .order_by(Matiere.nom)
            .all()
        )
        if not toutes_matieres:
            st.warning("Ta bibliothèque est vide. Crée des matières et importe des PDFs avant de planifier.")
            return

        # Stats par matière
        stats_par_id: dict[int, dict[str, Any]] = {
            m.id: stats_matiere(session, m) for m in toutes_matieres
        }

        # --- Filtre par Semestre (Idée 1) ---
        semestres = (
            session.query(Semestre)
            .options(selectinload(Semestre.ues))
            .filter_by(actif=True)
            .order_by(Semestre.nom)
            .all()
        )

        col_filtre, col_quick, col_reuse = st.columns([2, 1, 1])
        with col_filtre:
            sem_choices = {"📅 Tous les semestres": None}
            for sem in semestres:
                sem_choices[f"📅 {sem.nom}"] = sem.id
            sem_filter = st.selectbox(
                "Filtrer par Semestre",
                options=list(sem_choices.keys()),
                key="etudes_semestre_filter",
                label_visibility="collapsed",
            )
            selected_sem_id = sem_choices[sem_filter]

        with col_quick:
            # Boutons de sélection rapide (un par semestre)
            for sem in semestres:
                if st.button(f"✅ Tout {sem.nom}", key=f"quick_sem_{sem.id}", width="stretch",
                             help=f"Ajouter toutes les matières de {sem.nom}"):
                    matiere_ids_in_sem = [
                        m.id for ue in sem.ues
                        for m in ue.matieres
                        if m.actif
                    ]
                    if matiere_ids_in_sem:
                        existing_ids = {m["matiere_id"] for m in matieres_selectionnees_db}
                        new_ids = set(matiere_ids_in_sem) - existing_ids
                        if new_ids:
                            for mid in new_ids:
                                matieres_selectionnees_db.append({
                                    "matiere_id": mid,
                                    "chapitre_ids": [],
                                    "type_travail": TYPES_TRAVAIL[0],
                                    "urgence": "Normale",
                                    "note_ia": "",
                                })
                            with session_scope() as ws:
                                s = ws.get(SaisieHebdo, saisie.id)
                                s.matieres_selectionnees = matieres_selectionnees_db
                            st.rerun()
                        else:
                            st.toast("Toutes les matières de ce semestre sont déjà sélectionnées.", icon="ℹ️")

        with col_reuse:
            if st.button("📋 Reprendre ma sélection précédente", width="stretch", key="reuse_prev",
                         help="Copie la sélection de la semaine dernière"):
                prev_selection = _get_previous_week_selection(session, offset_courant)
                if prev_selection:
                    saisie.matieres_selectionnees = prev_selection
                    with session_scope() as ws:
                        s = ws.get(SaisieHebdo, saisie.id)
                        s.matieres_selectionnees = prev_selection
                    st.toast("Sélection précédente reprise !", icon="📋")
                    st.rerun()
                else:
                    st.toast("Aucune sélection précédente trouvée.", icon="ℹ️")

        # --- Pré-sélection automatique ---
        # Flag session_state pour éviter de ré-autosélectionner après un
        # enregistrement volontairement vide (ex: semaine de vacances).
        auto_key = f"etudes_auto_done_{saisie.id}"
        auto_preselection = False
        if not matieres_selectionnees_db and not st.session_state.get(auto_key):
            ids_dus = set(matieres_avec_revisions_dues(session))
            if ids_dus:
                auto_preselection = True
                matiere_ids_deja_selectionnees = list(ids_dus)
                st.session_state[auto_key] = True
            else:
                matiere_ids_deja_selectionnees = []
        else:
            matiere_ids_deja_selectionnees = [
                m["matiere_id"] for m in matieres_selectionnees_db
            ]

        # --- Filtrage des matières par semestre ---
        if selected_sem_id:
            matieres_filtrees = [
                m for m in toutes_matieres
                if m.ue and m.ue.semestre_id == selected_sem_id
            ]
        else:
            matieres_filtrees = list(toutes_matieres)

        # Protection : les matières déjà sélectionnées d'un autre semestre
        # doivent rester dans les options pour éviter que Streamlit ne les
        # supprime silencieusement du multiselect.
        ids_filtrees = {m.id for m in matieres_filtrees}
        for mid in matiere_ids_deja_selectionnees:
            if mid not in ids_filtrees:
                extra = next((m for m in toutes_matieres if m.id == mid), None)
                if extra:
                    matieres_filtrees.append(extra)

        matieres_pre_selectionnees = [
            m for m in matieres_filtrees if m.id in matiere_ids_deja_selectionnees
        ]

        if auto_preselection:
            st.success(
                f"🤖 J'ai pré-sélectionné **{len(matieres_pre_selectionnees)} matière(s)** "
                "où Leitner a des révisions dues cette semaine. Décoche/ajuste si besoin."
            )

        # --- 1. Sélection des matières ---
        st.subheader("1. Matières à travailler")
        
        matiere_dict = {m.id: m for m in toutes_matieres}
        
        matieres_choisies_ids = st.multiselect(
            "Quelles matières souhaites-tu aborder cette semaine ?",
            options=[m.id for m in matieres_filtrees],
            default=[m.id for m in matieres_pre_selectionnees],
            format_func=lambda m_id: format_label_matiere(matiere_dict[m_id], stats_par_id[m_id]),
            help="Légende : 🔥 révisions dues — 📑 nouveaux — 📊 maîtrise.",
        )
        
        matieres_choisies = [matiere_dict[m_id] for m_id in matieres_choisies_ids]

        # --- Bandeau résumé en temps réel (Idée 3) ---
        nouvelles_matieres_selectionnees: list[dict[str, Any]] = []

        matieres_choisies_uniques: list[Matiere] = []
        _seen_ids: set[int] = set()
        for _m in matieres_choisies:
            if _m.id not in _seen_ids:
                _seen_ids.add(_m.id)
                matieres_choisies_uniques.append(_m)

        if matieres_choisies_uniques:
            # Calculs pour le bandeau
            total_chapitres = 0
            total_urgents = 0
            total_nouveaux = 0
            for matiere in matieres_choisies_uniques:
                chapitres = session.query(Chapitre).filter_by(matiere_id=matiere.id).all()
                total_chapitres += len(chapitres)
                today = _dt.date.today()
                for ch in chapitres:
                    if ch.date_prochaine and ch.date_prochaine <= today and (ch.maitrise_pct or 0) < 50:
                        total_urgents += 1
                    if (ch.niveau_actuel or 0) == 0 and ch.date_prochaine is None:
                        total_nouveaux += 1

            # Bandeau résumé
            st.markdown(
                f"<div style='margin:1rem 0; padding:10px 16px; background:#f0f9ff; "
                f"border-left:4px solid #0ea5e9; border-radius:4px;'>"
                f"<b>📊 Résumé de ta sélection</b><br>"
                f"<span style='font-size:0.9rem;'>"
                f"{len(matieres_choisies_uniques)} matière(s) · {total_chapitres} chapitres"
                f"{' · 🔴 ' + str(total_urgents) + ' révision(s) urgente(s)' if total_urgents else ''}"
                f"{' · 📑 ' + str(total_nouveaux) + ' nouveau(x)' if total_nouveaux else ''}"
                f"</span></div>",
                unsafe_allow_html=True,
            )

            # --- Suggestion IA des chapitres à prioriser (Idée 4) ---
            selected_ids = [m.id for m in matieres_choisies_uniques]
            suggestions = _suggest_priority_chapters(session, selected_ids, stats_par_id)
            if suggestions:
                st.markdown(
                    f"<div style='margin-bottom:1rem; padding:8px 14px; background:#fefce8; "
                    f"border-left:4px solid #eab308; border-radius:4px;'>"
                    f"🧠 <b>Chapitres suggérés à prioriser cette semaine :</b> "
                    f"</div>",
                    unsafe_allow_html=True,
                )
                cols = st.columns(min(len(suggestions), 5) or 1)
                for i, sug in enumerate(suggestions):
                    with cols[i % len(cols)]:
                        st.caption(f"{sug['raison']}  \n📑 *{sug['chapitre'].titre[:40]}*")

        # --- 2. Détail par matière choisie ---
        if matieres_choisies_uniques:
            for matiere in matieres_choisies_uniques:
                config_existante = next(
                    (m for m in matieres_selectionnees_db if m.get("matiere_id") == matiere.id),
                    {},
                )

                with st.expander(f"⚙️ {matiere.nom}", expanded=True):
                    chapitres_matiere = (
                        session.query(Chapitre)
                        .filter_by(matiere_id=matiere.id)
                        .order_by(Chapitre.numero)
                        .all()
                    )

                    if not chapitres_matiere:
                        st.caption("⚠️ Pas encore de chapitre. Importe un PDF depuis la Bibliothèque.")
                        # On garde la matière même sans chapitre
                        nouvelles_matieres_selectionnees.append({
                            "matiere_id": matiere.id,
                            "chapitre_ids": [],
                            "type_travail": TYPES_TRAVAIL[0],
                            "urgence": "Normale",
                            "note_ia": "",
                        })
                        continue

                    chap_by_id = {ch.id: ch for ch in chapitres_matiere}

                    # --- Cartes de chapitres (Idée 5) ---
                    st.caption("**📑 Chapitres** — coche ceux que tu veux travailler :")
                    ch_ids_def = [
                        ch_id for ch_id in config_existante.get("chapitre_ids", [])
                        if ch_id in chap_by_id
                    ]
                    chapitres_choisis: list[int] = []

                    cols_per_row = 2
                    for i in range(0, len(chapitres_matiere), cols_per_row):
                        row_chaps = chapitres_matiere[i:i + cols_per_row]
                        cols = st.columns(cols_per_row)
                        for j, ch in enumerate(row_chaps):
                            with cols[j]:
                                m = int(ch.maitrise_pct or 0)
                                bar_width = min(m // 10, 10)
                                bar = "█" * bar_width + "░" * (10 - bar_width)
                                label_rev, color_rev = label_couleur_status(ch)

                                is_checked = st.checkbox(
                                    f"Ch.{ch.numero} : {ch.titre[:35]}",
                                    value=ch.id in ch_ids_def,
                                    key=f"chap_{matiere.id}_{ch.id}",
                                )
                                st.markdown(
                                    f"<div style='font-size:0.78rem; color:#6b7280; margin-top:-12px; "
                                    f"margin-bottom:8px; margin-left:24px;'>"
                                    f"[{bar}] {m}% · "
                                    f"<span style='color:{color_rev};'>{label_rev}</span>"
                                    f" · {ch.temps_estime_h}h</div>",
                                    unsafe_allow_html=True,
                                )
                                if is_checked:
                                    chapitres_choisis.append(ch.id)

                                # Bouton salle d'étude
                                if st.button(
                                    "🧠", key=f"goto_m{matiere.id}_c{ch.id}",
                                    help=f"Ouvrir {ch.titre} en salle d'étude",
                                ):
                                    _open_chapitre_in_session_etude(ch.id)

                    # Type de travail suggéré
                    chapitres_choisis_db = [chap_by_id[i] for i in chapitres_choisis]
                    type_suggere = _type_travail_suggere(chapitres_choisis_db)
                    val_type = config_existante.get("type_travail") or type_suggere
                    idx_type = TYPES_TRAVAIL.index(val_type) if val_type in TYPES_TRAVAIL else 0

                    col1, col2 = st.columns(2)
                    with col1:
                        type_travail = st.selectbox(
                            "Type de travail",
                            options=TYPES_TRAVAIL,
                            index=idx_type,
                            key=f"type_{matiere.id}",
                            help=f"Suggéré : « {type_suggere} ».",
                        )
                    with col2:
                        idx_urg = 0
                        if config_existante.get("urgence") in URGENCES:
                            idx_urg = URGENCES.index(config_existante["urgence"])
                        urgence = st.select_slider(
                            "Urgence",
                            options=URGENCES,
                            value=URGENCES[idx_urg],
                            key=f"urg_{matiere.id}",
                        )

                    # --- Note pour l'IA (Idée 6) ---
                    note_ia = st.text_input(
                        "📝 Note pour l'IA (optionnel)",
                        value=config_existante.get("note_ia", ""),
                        placeholder="Ex: Priorise les exos du ch.5, évite le jeudi...",
                        key=f"note_ia_{matiere.id}",
                    )

                    nouvelles_matieres_selectionnees.append({
                        "matiere_id": matiere.id,
                        "chapitre_ids": chapitres_choisis,
                        "type_travail": type_travail,
                        "urgence": urgence,
                        "note_ia": note_ia.strip(),
                    })

        # --- Indicateur live de charge horaire ---
        charge_min = estimer_charge_minutes(session, nouvelles_matieres_selectionnees)
        if charge_min > 0:
            profil = session.query(Utilisateur).first()
            if profil is None:
                cible_hebdo_min = 0
                plafond_h = 0.0
            else:
                cible_hebdo_min = calculer_cible_hebdo_minutes(profil)
                plafond_h = float(
                    getattr(profil.biometrie, "heures_etude_plafond_par_jour", 0) or 0
                )
            pct = charge_min / cible_hebdo_min * 100 if cible_hebdo_min else 0
            heures_str = f"{charge_min / 60:.1f} h"
            cible_str = f"{cible_hebdo_min / 60:.1f} h"
            plafond_str = f"{plafond_h:.1f} h/jour" if plafond_h > 0 else "non défini"

            if charge_min < cible_hebdo_min * 0.6:
                st.info(f"📊 **Volume estimé : {heures_str}** / {cible_str} ({pct:.0f}%) — tu peux en ajouter.")
            elif charge_min <= cible_hebdo_min * 1.05:
                st.success(f"🎯 **Volume estimé : {heures_str}** — aligné sur {cible_str} ({pct:.0f}%). Plafond : {plafond_str}.")
            elif charge_min <= cible_hebdo_min * 1.2:
                st.warning(f"⚠️ **Volume estimé : {heures_str}** — au-dessus de {cible_str} ({pct:.0f}%). L'IA ajustera.")
            else:
                st.error(f"🚨 **Surcharge : {heures_str}** / {cible_str} ({pct:.0f}%). Allège ta sélection.")

        # --- 3. Travaux ponctuels ---
        st.divider()
        st.subheader("2. Travaux ponctuels")
        st.caption("Devoir à rendre, compte-rendu de TP, projet noté…")

        df_travaux = _build_travaux_df(travaux_ponctuels_db)
        edited_travaux = st.data_editor(
            df_travaux,
            num_rows="dynamic",
            width="stretch",
            column_config={
                "libelle": st.column_config.TextColumn("Libellé du devoir", required=True),
                "deadline": st.column_config.DatetimeColumn("Deadline (Date & Heure)", format="DD/MM/YYYY HH:mm"),
                "duree_min": st.column_config.NumberColumn("Durée estimée (min)", min_value=15, step=15, default=60),
                "priorite": st.column_config.SelectboxColumn("Priorité", options=PRIORITES, default="Normale"),
            },
            key="editor_travaux_ponctuels",
        )
        _render_alertes_deadlines(edited_travaux)

        # --- 4. Sauvegarde ---
        st.divider()
        if st.button("💾 Enregistrer mes objectifs d'études", type="primary"):
            travaux_propres = _clean_travaux(edited_travaux)
            try:
                with session_scope() as write_session:
                    saisie_to_update = write_session.get(SaisieHebdo, saisie.id)
                    saisie_to_update.matieres_selectionnees = nouvelles_matieres_selectionnees
                    saisie_to_update.travaux_ponctuels = travaux_propres
                st.success("✅ Tes objectifs d'études pour la semaine sont enregistrés !")
                st.toast("Objectifs sauvegardés", icon="✅")
            except Exception as e:  # noqa: BLE001
                import logging
                logging.getLogger("hebdo").exception("sauvegarde objectifs études")
                st.error(f"Erreur lors de la sauvegarde : {e}")


# ---------------------------------------------------------------------------
# Helpers privés — travaux ponctuels
# ---------------------------------------------------------------------------
def _build_travaux_df(travaux_ponctuels_db: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(travaux_ponctuels_db) if travaux_ponctuels_db else pd.DataFrame(
        columns=["libelle", "deadline", "duree_min", "priorite"]
    )
    if "deadline" in df.columns:
        df["deadline"] = pd.to_datetime(df["deadline"], errors="coerce")
        df = df.sort_values(by="deadline", ascending=True, na_position="last")
    return df.reset_index(drop=True)


def _render_alertes_deadlines(edited_travaux: pd.DataFrame) -> None:
    if edited_travaux.empty or "deadline" not in edited_travaux.columns:
        return
    now = _dt.datetime.now()
    seuil = now + _dt.timedelta(days=3)
    alertes: list[str] = []
    for _, row in edited_travaux.iterrows():
        deadline = row.get("deadline")
        libelle = (row.get("libelle") or "").strip()
        if not libelle or pd.isna(deadline):
            continue
        if deadline < now:
            alertes.append(f"📅 **{libelle}** : deadline **passée** ({deadline.strftime('%d/%m %H:%M')})")
        elif deadline <= seuil:
            jours = (deadline - now).days
            alertes.append(f"🚨 **{libelle}** : deadline dans {jours} jour(s) ({deadline.strftime('%d/%m %H:%M')})")
    if alertes:
        st.warning("**Deadlines proches ou dépassées :**\n\n" + "\n\n".join(alertes))


def _clean_travaux(edited_travaux: pd.DataFrame) -> list[dict[str, Any]]:
    travaux_propres: list[dict[str, Any]] = []
    for _, row in edited_travaux.iterrows():
        if pd.isna(row.get("libelle")) or str(row.get("libelle")).strip() == "":
            continue
        deadline_str = ""
        if pd.notna(row.get("deadline")):
            try:
                deadline_str = row["deadline"].strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
        travaux_propres.append({
            "libelle": str(row["libelle"]).strip(),
            "deadline": deadline_str,
            "duree_min": int(row.get("duree_min", 60)),
            "priorite": str(row.get("priorite", "Normale")),
        })
    return travaux_propres
