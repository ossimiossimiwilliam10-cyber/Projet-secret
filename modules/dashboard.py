"""Onglet **Tableau de bord semestre**.

Visualisation des métriques globales : grille de planning hebdomadaire,
progression des cours, charge de travail et calendrier des examens.
"""

from __future__ import annotations

import datetime
import html as html_lib
from datetime import date, time

import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit.components.v1 import html as st_html
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.db import get_session, session_scope
from database.models import Chapitre, CheckInQuotidien, Matiere, Semaine, Tache, Profil
from services.gamification_service import (
    progression_niveau,
    NIVEAU_MAX,
)


# ===========================================================================
# Palette de couleurs partagée — alignée sur celle de generation.py
# ===========================================================================
TYPE_COLORS: dict[str, str] = {
    "etude":       "#4cd137",
    "sport":       "#e84118",
    "courses":     "#00a8ff",
    "projet":      "#9c88ff",
    "dev_perso":   "#00bc8c",
    "social":      "#e1b12c",
    "intendance":  "#718093",
    "repas":       "#fbc531",
    "sommeil":     "#273c75",
    "transport":   "#bdc3c7",
    "cours_presentiel": "#a29bfe",
    "travail":     "#d63031",
    "autre":       "#353b48",
}

TYPE_LABELS: dict[str, str] = {
    "etude":       "📚 Études",
    "sport":       "🏃 Sport",
    "courses":     "🛒 Courses",
    "projet":      "🎯 Projets",
    "dev_perso":   "🌱 Dev perso",
    "social":      "🍹 Social",
    "intendance":  "🧹 Intendance",
    "repas":       "🍽️ Repas",
    "sommeil":     "😴 Sommeil",
    "transport":   "🚇 Transport",
    "cours_presentiel": "🎓 Cours",
    "travail":     "💼 Travail",
}


def _color_for(task_type: str) -> str:
    return TYPE_COLORS.get((task_type or "autre").lower(), TYPE_COLORS["autre"])


# ===========================================================================
# Point d'entrée
# ===========================================================================
def render() -> None:
    # --- Interception du clic depuis la grille --------------------------
    # Quand l'utilisateur clique sur une case d'étude dans la grille HTML,
    # le JS écrit l'URL ?chap_id=X. Streamlit re-rend la page, on lit le
    # paramètre, on prépare le state, on redirige vers la salle d'étude.
    chap_id_param = st.query_params.get("chap_id")
    if chap_id_param:
        try:
            chap_id = int(chap_id_param)
            with get_session() as _s:
                if _s.get(Chapitre, chap_id) is not None:
                    st.session_state.target_chapitre_id = chap_id
                    st.query_params.clear()
                    st.switch_page("pages/session_etude.py")
                    return  # par sécurité, normalement switch_page interrompt
        except (ValueError, TypeError):
            pass
        # Si on arrive ici, le param était invalide — on nettoie
        st.query_params.clear()

    st.title("📈 Tableau de bord du semestre")
    st.caption(
        "Vue d'ensemble de tes semaines : planning visuel, progression, "
        "charge de travail et examens à venir."
    )

    with get_session() as session:
        # NEW F3a — Barre XP / niveau / streak
        _render_xp_bar(session)

        # Chantier 4 — Check-in biomécanique du jour
        _render_checkin_quotidien(session)

        # 0. Grille visuelle de la semaine
        _render_planning_grid(session)

        st.divider()

        # 1. KPIs globaux
        _render_kpis(session)

        st.divider()

        # 2. Graphiques : Progression et Charge
        col_graph1, col_graph2 = st.columns(2)
        with col_graph1:
            _render_progression_cours(session)
        with col_graph2:
            _render_charge_hebdo(session)


# ===========================================================================
# Chantier 4 — Check-in biomécanique quotidien
# ===========================================================================
def _render_checkin_quotidien(session: Session) -> None:
    """Affiche le check-in du jour avec 3 sliders et un bouton d'enregistrement."""
    aujourdhui = date.today()
    existing = (
        session.query(CheckInQuotidien)
        .filter(CheckInQuotidien.date == aujourdhui)
        .first()
    )
    val_fatigue = int(existing.fatigue_physique) if existing else 5
    val_mental = int(existing.charge_mentale) if existing else 5
    val_sommeil = int(existing.qualite_sommeil) if existing else 5

    with st.expander("📊 Check-in Biomécanique du jour", expanded=(existing is None)):
        st.caption(
            "Évalue ton état actuel sur une échelle de 1 à 10. "
            "L'IA s'en servira pour adapter la difficulté des sessions d'étude. "
            "(1 = forme olympique, 10 = épuisé)"
        )
        col1, col2, col3 = st.columns(3)
        with col1:
            fatigue_physique = st.slider(
                "💪 Fatigue physique", 1, 10, val_fatigue,
                help="Après boxe, entrepôt, longue marche…",
            )
        with col2:
            charge_mentale = st.slider(
                "🧠 Charge mentale", 1, 10, val_mental,
                help="Stress, examens à venir, semaine intense…",
            )
        with col3:
            qualite_sommeil = st.slider(
                "😴 Qualité du sommeil", 1, 10, val_sommeil,
                help="1 = très mauvais, 10 = parfaitement reposé",
            )

        col_btn, col_msg = st.columns([1, 3])
        with col_btn:
            if st.button("💾 Enregistrer mon état", type="primary", width="stretch"):
                with session_scope() as write_session:
                    row = (
                        write_session.query(CheckInQuotidien)
                        .filter(CheckInQuotidien.date == aujourdhui)
                        .first()
                    )
                    if row is None:
                        row = CheckInQuotidien(date=aujourdhui)
                        write_session.add(row)
                    row.fatigue_physique = int(fatigue_physique)
                    row.charge_mentale = int(charge_mentale)
                    row.qualite_sommeil = int(qualite_sommeil)
                with col_msg:
                    st.success("✅ Check-in enregistré.")
                st.toast("Check-in enregistré", icon="✅")

        if existing is not None:
            with col_msg:
                st.caption(
                    f"Dernier check-in : fatigue {existing.fatigue_physique}/10, "
                    f"mental {existing.charge_mentale}/10, "
                    f"sommeil {existing.qualite_sommeil}/10"
                )


# ===========================================================================
# F3a — Barre XP / Niveau / Streak
# ===========================================================================
def _render_xp_bar(session: Session) -> None:
    """Bandeau au sommet du dashboard avec niveau, XP, streak."""
    profil = session.query(Profil).first()
    if profil is None:
        return

    xp_total = profil.xp or 0
    streak = profil.streak_jours or 0
    streak_record = profil.streak_record or 0
    nb_quiz = profil.nb_quiz_total or 0
    nb_maitrise = profil.nb_chapitres_maitrise or 0

    prog = progression_niveau(xp_total)
    niveau = prog["niveau"]
    ratio = prog["ratio"]

    if prog["max_atteint"]:
        progression_label = f"NIVEAU MAX ATTEINT • {xp_total:,} XP"
        ratio_pct = 100
    else:
        progression_label = (
            f"{prog['xp_dans_palier']:,} / {prog['xp_palier_taille']:,} XP "
            f"vers niveau {niveau + 1}"
        )
        ratio_pct = int(ratio * 100)

    # Flamme qui grossit selon streak
    if streak == 0:
        flame_icon, flame_color = "💤", "#9ca3af"
    elif streak < 3:
        flame_icon, flame_color = "🔥", "#f59e0b"
    elif streak < 7:
        flame_icon, flame_color = "🔥🔥", "#ef4444"
    elif streak < 30:
        flame_icon, flame_color = "🔥🔥🔥", "#dc2626"
    else:
        flame_icon, flame_color = "🔥💥🔥", "#b91c1c"

    html = f"""
    <div style="
        background:linear-gradient(135deg, #1e293b, #334155);
        border-radius:12px; padding:16px 20px; margin-bottom:12px;
        color:white; box-shadow:0 4px 12px rgba(0,0,0,0.15);
    ">
      <div style="display:grid;grid-template-columns:1fr 2.5fr 1fr 1fr;gap:20px;align-items:center;">
        <!-- Niveau -->
        <div>
          <div style="font-size:0.7rem;opacity:0.7;text-transform:uppercase;letter-spacing:0.05em;">Niveau</div>
          <div style="font-size:2.5rem;font-weight:800;line-height:1;">{niveau}</div>
          <div style="font-size:0.7rem;opacity:0.55;">/{NIVEAU_MAX}</div>
        </div>
        <!-- Barre XP -->
        <div>
          <div style="font-size:0.75rem;opacity:0.85;margin-bottom:4px;">{progression_label}</div>
          <div style="background:rgba(255,255,255,0.15);height:14px;border-radius:7px;overflow:hidden;">
            <div style="
              background:linear-gradient(90deg, #fbbf24, #f59e0b);
              height:100%;width:{ratio_pct}%;
              transition:width 0.6s ease;
              box-shadow:0 0 8px rgba(251,191,36,0.6);
            "></div>
          </div>
          <div style="font-size:0.7rem;opacity:0.6;margin-top:4px;">⭐ {xp_total:,} XP total</div>
        </div>
        <!-- Streak -->
        <div style="text-align:center;border-left:1px solid rgba(255,255,255,0.15);padding-left:20px;">
          <div style="font-size:0.7rem;opacity:0.7;text-transform:uppercase;letter-spacing:0.05em;">Streak</div>
          <div style="font-size:1.6rem;line-height:1;">{flame_icon}</div>
          <div style="font-size:1.4rem;font-weight:700;color:{flame_color};line-height:1.1;">{streak}j</div>
          <div style="font-size:0.65rem;opacity:0.5;">Record : {streak_record}j</div>
        </div>
        <!-- Stats -->
        <div style="text-align:center;border-left:1px solid rgba(255,255,255,0.15);padding-left:20px;">
          <div style="font-size:0.7rem;opacity:0.7;text-transform:uppercase;letter-spacing:0.05em;">Stats</div>
          <div style="font-size:0.85rem;margin-top:4px;">📝 {nb_quiz} quiz</div>
          <div style="font-size:0.85rem;">🧠 {nb_maitrise} maîtrisé(s)</div>
        </div>
      </div>
    </div>
    """
    if hasattr(st, "html"):
        st.html(html)
    else:
        st.markdown(html, unsafe_allow_html=True)


# ===========================================================================
# Section 0 — Grille de planning hebdomadaire
# ===========================================================================
def _render_planning_grid(session: Session) -> None:
    """Affiche le planning de la semaine en grille style Google Calendar.

    Comportement :
    - Sélecteur de semaine (par défaut : celle qui contient aujourd'hui).
    - Heures affichées : min/max des tâches, arrondies aux 30 min.
    - Cellules colorées par type, avec `rowspan` pour les tâches longues.
    - Tâches "fait" légèrement transparentes, "non_fait" très transparentes.
    """
    st.subheader("🗓️ Planning de la semaine")

    # --- Récupération des semaines existantes ---------------------------
    semaines = session.query(Semaine).order_by(Semaine.date_debut.desc()).all()
    if not semaines:
        st.info(
            "Aucune semaine planifiée pour le moment. Va dans **✨ Génération IA** "
            "pour créer ton premier planning."
        )
        return

    # Trouver la semaine actuelle (par défaut)
    today = date.today()
    semaine_par_defaut = next(
        (s for s in semaines if s.date_debut <= today <= s.date_fin),
        semaines[0],
    )

    # --- Sélecteur de semaine -------------------------------------------
    def _label(s: Semaine) -> str:
        return (
            f"S{s.numero_semaine:02d} — "
            f"{s.date_debut.strftime('%d %b')} → {s.date_fin.strftime('%d %b %Y')}"
        )

    options = {_label(s): s.id for s in semaines}
    default_label = _label(semaine_par_defaut)
    default_idx = list(options.keys()).index(default_label)

    col_sel, col_status = st.columns([3, 1])
    with col_sel:
        selected_label = st.selectbox(
            "Choisir une semaine",
            options=list(options.keys()),
            index=default_idx,
            label_visibility="collapsed",
        )
    semaine = session.get(Semaine, options[selected_label])

    with col_status:
        if semaine.score_realisme is not None:
            score = semaine.score_realisme
            color = "green" if score >= 80 else "orange" if score >= 50 else "red"
            st.markdown(
                f"<div style='text-align:center; padding-top:0.3rem;'>"
                f"<span style='color:{color}; font-weight:600;'>📊 Score IA : {score}/100</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

    # --- Récupération des tâches ----------------------------------------
    taches = (
        session.query(Tache)
        .filter_by(semaine_id=semaine.id)
        .order_by(Tache.jour, Tache.heure_debut)
        .all()
    )
    if not taches:
        st.info(
            f"La semaine S{semaine.numero_semaine} n'a pas encore de planning. "
            f"Va dans **✨ Génération IA** pour la créer."
        )
        return

    # --- Mini-KPIs en bandeau --------------------------------------------
    _render_mini_kpis(taches)

    # --- Construction de la grille ---------------------------------------
    _render_grid_html(taches)

    # Indique aux étudiants qu'ils peuvent cliquer sur les cases d'étude.
    # Affiché uniquement si au moins une tâche est cliquable.
    nb_clickables = sum(
        1 for t in taches
        if t.type == "etude" and t.chapitre_ids and len(t.chapitre_ids) > 0
    )
    if nb_clickables > 0:
        st.caption(
            f"🧠 Clique sur une case d'étude (avec l'icône 🧠) pour ouvrir la "
            f"**Salle d'étude** du chapitre correspondant — {nb_clickables} "
            f"session(s) cliquable(s) cette semaine."
        )

    # --- Légende --------------------------------------------------------
    _render_legend(taches)

    # --- Justification IA (si présente) ----------------------------------
    if semaine.bilan_ia:
        with st.expander("💡 Stratégie de l'IA pour cette semaine", expanded=False):
            st.info(semaine.bilan_ia)


def _render_mini_kpis(taches: list[Tache]) -> None:
    """Bandeau de mini-métriques au-dessus de la grille."""
    by_type: dict[str, int] = {}
    nb_fait = 0
    nb_total_planif = 0
    for t in taches:
        dur = int(t.duree_min or 0)
        if not dur and t.heure_debut and t.heure_fin:
            # Recalcule si duree_min est vide
            dur = (
                (t.heure_fin.hour * 60 + t.heure_fin.minute)
                - (t.heure_debut.hour * 60 + t.heure_debut.minute)
            )
        by_type[t.type] = by_type.get(t.type, 0) + max(dur, 0)
        if not t.obligatoire:
            nb_total_planif += 1
            if t.statut == "fait":
                nb_fait += 1

    completion = (nb_fait / nb_total_planif * 100) if nb_total_planif else 0

    c1, c2, c3, c4, c5, c6 = st.columns(5) # Streamlit gère le wrap auto ou on peut ajuster
    c1.metric("📚 Études",     f"{by_type.get('etude', 0) / 60:.1f} h")
    c2.metric("🏃 Sport",      f"{by_type.get('sport', 0) / 60:.1f} h")
    c3.metric("🎯 Projets",    f"{by_type.get('projet', 0) / 60:.1f} h")
    c4.metric("🍹 Social",     f"{by_type.get('social', 0) / 60:.1f} h")
    c5.metric("🌱 Dev Perso",  f"{by_type.get('dev_perso', 0) / 60:.1f} h")
    c6 = st.columns(1)[0]
    st.metric("✅ Complétion hebdomadaire", f"{int(completion)} %")


def _render_grid_html(taches: list[Tache]) -> None:
    """Génère et rend la grille HTML."""
    jours_keys = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    jours_label = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

    # Détermine la plage horaire à afficher (min/max des tâches arrondi 30 min)
    if not taches:
        return

    starts = [t.heure_debut for t in taches if t.heure_debut]
    ends = [t.heure_fin for t in taches if t.heure_fin]
    min_t = min(starts)
    max_t = max(ends)

    # Floor pour le début, ceil pour la fin
    start_hour = min_t.hour
    if min_t.minute >= 30:
        start_min = 30
    else:
        start_min = 0
    end_hour = max_t.hour
    if max_t.minute == 0:
        end_min = 0
    elif max_t.minute <= 30:
        end_min = 30
    else:
        end_hour += 1
        end_min = 0

    # Nombre total de slots de 30 min
    total_minutes = (end_hour * 60 + end_min) - (start_hour * 60 + start_min)
    nb_slots = max(total_minutes // 30, 1)

    def slot_of(t: time, mode: str) -> int:
        """Convertit time en index de slot (0 = start)."""
        minutes_total = t.hour * 60 + t.minute
        minutes_start = start_hour * 60 + start_min
        delta = minutes_total - minutes_start
        if mode == "floor":
            return delta // 30
        else:  # ceil
            return (delta + 29) // 30

    # Grille : dict jour -> liste de (None | "OCCUPIED" | Tache)
    grid: dict[str, list] = {j: [None] * nb_slots for j in jours_keys}

    for t in taches:
        if t.jour not in grid:
            continue
        s_start = slot_of(t.heure_debut, "floor")
        s_end = slot_of(t.heure_fin, "ceil")
        if s_end <= s_start:
            s_end = s_start + 1
        s_start = max(s_start, 0)
        s_end = min(s_end, nb_slots)
        if s_start >= nb_slots:
            continue
        # Si conflit (slot déjà occupé), on ignore cette tâche
        if any(grid[t.jour][i] is not None for i in range(s_start, s_end)):
            continue
        grid[t.jour][s_start] = t
        for i in range(s_start + 1, s_end):
            grid[t.jour][i] = "OCCUPIED"

    # === Génération HTML ===
    def _attr(s: str) -> str:
        """Échappe une string pour usage dans un attribut HTML (style/title) :
        - Quotes simples ET doubles échappées
        - Sauts de ligne remplacés par espaces (sinon casse le parser)
        """
        s = html_lib.escape(s, quote=True)
        s = s.replace("\n", " ").replace("\r", " ")
        return s

    rows = []
    # Header
    th_cells = "<th class='hcol'>Heure</th>"
    for jl in jours_label:
        th_cells += f"<th>{jl}</th>"
    th_cells += "<th class='hcol'>Heure</th>"
    rows.append(f"<tr>{th_cells}</tr>")

    # Body
    for slot in range(nb_slots):
        minute_offset = slot * 30 + start_min
        h_total = start_hour * 60 + minute_offset
        h_disp = h_total // 60
        m_disp = h_total % 60
        hour_label = f"{h_disp:02d}:{m_disp:02d}"

        row = f"<tr><td class='hcol'>{hour_label}</td>"
        for jk in jours_keys:
            cell = grid[jk][slot]
            if cell == "OCCUPIED":
                # rowspan déjà posé en amont, on saute cette cellule
                continue
            elif cell is None:
                # Slot vide. &nbsp; garantit que la cellule n'est pas supprimée par
                # le browser ou un sanitizer (les <td/> vides peuvent être collapsés).
                cls = "empty hour-mark" if m_disp == 0 else "empty"
                row += f"<td class='{cls}'>&nbsp;</td>"
            else:
                t: Tache = cell
                s_end = slot_of(t.heure_fin, "ceil")
                rowspan = min(s_end, nb_slots) - slot

                color = _color_for(t.type)
                titre = html_lib.escape(t.titre or "")
                # Tronque le titre selon le nombre de slots
                max_len = 20 + rowspan * 10
                if len(titre) > max_len:
                    titre = titre[:max_len - 1] + "…"

                # Opacité selon le statut
                if t.statut == "fait":
                    opacity = 0.55
                    decoration = "text-decoration: line-through;"
                elif t.statut == "non_fait":
                    opacity = 0.35
                    decoration = ""
                elif t.statut == "partiellement":
                    opacity = 0.75
                    decoration = ""
                else:
                    opacity = 1.0
                    decoration = ""

                # Lock icon pour obligatoire
                lock = "🔒 " if t.obligatoire else ""

                time_str = (
                    f"{t.heure_debut.strftime('%H:%M')}"
                    f"–{t.heure_fin.strftime('%H:%M')}"
                )

                # Tooltip = titre + justification IA, totalement échappé
                tooltip = t.titre or ""
                if t.justification_ia:
                    tooltip += f" · {t.justification_ia}"
                tooltip = _attr(tooltip)

                style_attr = _attr(f"background:{color}; opacity:{opacity}; {decoration}")

                # --- Click handler : seulement pour les tâches d'étude
                # avec un chapitre_id associé. On utilise un <a target="_top">
                # plutôt que onclick="window.top..." car le JS cross-origin est
                # bloqué par certaines configs sandbox d'iframe. Un lien
                # target="_top" est TOUJOURS autorisé (c'est son rôle).
                if t.chapitre_ids and len(t.chapitre_ids) > 0 and t.type == "etude":
                    first_chap = int(t.chapitre_ids[0])
                    # La cellule devient un wrapper minimaliste, le <a> remplit
                    # toute la surface et reçoit le clic.
                    inner_html = (
                        f'<a href="?chap_id={first_chap}" target="_top" '
                        f'style="display:block; width:100%; height:100%; '
                        f'color:inherit; text-decoration:none; padding:4px 6px;">'
                        f'<div class="task-time">{time_str}</div>'
                        f'<div class="task-title">{lock}{titre}</div>'
                        f'</a>'
                    )
                    cell_html = (
                        f'<td class="task clickable" rowspan="{rowspan}" '
                        f'style="{style_attr}; padding:0;" '
                        f'title="{tooltip}">{inner_html}</td>'
                    )
                else:
                    cell_html = (
                        f'<td class="task" rowspan="{rowspan}" '
                        f'style="{style_attr}" '
                        f'title="{tooltip}">'
                        f'<div class="task-time">{time_str}</div>'
                        f'<div class="task-title">{lock}{titre}</div>'
                        f'</td>'
                    )
                row += cell_html
        row += f"<td class='hcol'>{hour_label}</td></tr>"
        rows.append(row)

    # === CSS ===
    css = """
    <style>
    .planning-wrapper {
        width: 100%;
        overflow-x: auto;
        margin: 0.5rem 0;
    }
    table.planning-grid {
        border-collapse: collapse;
        width: 100%;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-size: 0.85rem;
        table-layout: fixed;
    }
    .planning-grid th {
        background: #1f2937;
        color: white;
        padding: 8px 4px;
        text-align: center;
        font-weight: 600;
        border: 1px solid #374151;
        font-size: 0.82rem;
    }
    .planning-grid td {
        border: 1px solid #e5e7eb;
        padding: 0;
        height: 26px;
        text-align: center;
        vertical-align: top;
        font-size: 0.78rem;
    }
    .planning-grid td.hcol {
        background: #f9fafb;
        color: #6b7280;
        font-weight: 500;
        font-size: 0.72rem;
        padding: 0 6px;
        white-space: nowrap;
        width: 55px;
        text-align: right;
        border-right: 2px solid #d1d5db;
        border-left: 2px solid #d1d5db;
    }
    .planning-grid td.empty {
        background: white;
    }
    .planning-grid td.empty.hour-mark {
        border-top: 1px solid #d1d5db;
    }
    .planning-grid td.task {
        padding: 4px 6px;
        color: #1f2937;
        text-align: left;
        cursor: help;
        transition: transform 0.1s ease, box-shadow 0.1s ease;
    }
    .planning-grid td.task:hover {
        transform: scale(1.02);
        box-shadow: 0 2px 8px rgba(0,0,0,0.2);
        z-index: 5;
        position: relative;
    }
    .planning-grid td.task.clickable {
        cursor: pointer;
    }
    .planning-grid td.task.clickable:hover {
        transform: scale(1.04);
        box-shadow: 0 4px 14px rgba(0,0,0,0.35);
        outline: 2px solid white;
        outline-offset: -2px;
        z-index: 10;
    }
    .planning-grid td.task.clickable::after {
        content: "🧠";
        position: absolute;
        top: 2px;
        right: 4px;
        font-size: 0.65rem;
        opacity: 0.6;
    }
    .planning-grid td.task.clickable {
        position: relative;
    }
    .task-time {
        font-size: 0.68rem;
        opacity: 0.85;
        line-height: 1.1;
        font-weight: 500;
    }
    .task-title {
        font-size: 0.76rem;
        font-weight: 600;
        margin-top: 1px;
        line-height: 1.2;
        word-break: break-word;
    }
    </style>
    """

    # Hauteur estimée (utilisée par le fallback iframe)
    height_px = 60 + nb_slots * 28 + 20

    # Approche A (privilégiée) : st.html — disponible depuis Streamlit 1.33+
    # Rend le HTML directement dans le DOM Streamlit, SANS iframe. Les liens
    # <a href="?chap_id=N"> peuvent donc naviguer la page parent normalement,
    # sans collision avec les politiques cross-origin.
    #
    # Approche B (fallback) : st.components.v1.html — iframe. Les liens fonctionnent
    # uniquement avec target="_top" (déjà ajouté), si la sandbox iframe l'autorise.
    full_doc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">{css}</head>
<body style="margin:0; padding:0; background:transparent;">
<div class='planning-wrapper'>
<table class='planning-grid'>
{"".join(rows)}
</table>
</div>
</body></html>"""

    inline_html = f"""{css}
<div class='planning-wrapper'>
<table class='planning-grid'>
{"".join(rows)}
</table>
</div>"""

    if hasattr(st, "html"):
        # Pas d'iframe → liens natifs OK
        st.html(inline_html)
    else:
        # Vieille version Streamlit : fallback iframe (les <a target="_top">
        # doivent fonctionner si la sandbox iframe ne bloque pas top-navigation)
        st_html(full_doc, height=height_px, scrolling=False)


def _render_legend(taches: list[Tache]) -> None:
    """Légende des couleurs : seulement les types présents dans la semaine."""
    types_presents = sorted({t.type for t in taches if t.type})
    if not types_presents:
        return

    chips = []
    for t in types_presents:
        color = _color_for(t)
        label = TYPE_LABELS.get(t, t.capitalize())
        chips.append(
            f"<span style='display:inline-flex; align-items:center; gap:6px; "
            f"padding:4px 10px; margin:3px; background:{color}30; "
            f"border-left:3px solid {color}; border-radius:3px; "
            f"font-size:0.8rem;'>{label}</span>"
        )
    st.markdown(
        f"<div style='margin-top:0.5rem;'>{''.join(chips)}</div>",
        unsafe_allow_html=True,
    )


# ===========================================================================
# Section 1 — KPIs globaux
# ===========================================================================
def _render_kpis(session: Session) -> None:
    semaines = (
        session.query(Semaine)
        .filter(Semaine.statut == "generee")
        .order_by(Semaine.date_debut)
        .all()
    )

    total_minutes = session.query(func.sum(Tache.duree_min)).filter(
        Tache.statut.in_(["fait", "partiellement"])
    ).scalar() or 0
    heures_travail_total = int(total_minutes // 60)

    taux_valides = [
        s.taux_completion_pct for s in semaines
        if s.taux_completion_pct is not None and s.taux_completion_pct > 0
    ]
    moyenne_completion = sum(taux_valides) / len(taux_valides) if taux_valides else 0.0

    streak = 0
    for s in reversed(semaines):
        if s.taux_completion_pct and s.taux_completion_pct >= 80:
            streak += 1
        else:
            break

    # Focus = matière avec le plus de minutes d'étude faites cumulées.
    top_matiere_row = (
        session.query(Tache.matiere_id, func.sum(Tache.duree_min).label("total"))
        .filter(Tache.matiere_id.isnot(None), Tache.statut == "fait")
        .group_by(Tache.matiere_id)
        .order_by(func.sum(Tache.duree_min).desc())
        .first()
    )
    top_matiere_nom = "Aucune"
    if top_matiere_row:
        m = session.get(Matiere, top_matiere_row[0])
        if m:
            top_matiere_nom = m.nom

    cols = st.columns(4)
    cols[0].metric("🔥 Streak (>80%)", f"{streak} sem.",
                   help="Nombre de semaines consécutives au-dessus de 80% de complétion.")
    cols[1].metric("✅ Complétion moy.", f"{moyenne_completion:.0f} %")
    cols[2].metric("⏱️ Heures cumulées", f"{heures_travail_total} h")
    cols[3].metric(
        "📚 Focus actuel",
        top_matiere_nom[:15] + "..." if len(top_matiere_nom) > 15 else top_matiere_nom,
    )


# ===========================================================================
# Section 2 — Graphiques Plotly
# ===========================================================================
def _render_progression_cours(session: Session) -> None:
    st.subheader("Maîtrise par matière")
    matieres_actives = session.query(Matiere).filter_by(actif=True).all()

    data = []
    for m in matieres_actives:
        nb_chapitres = len(m.chapitres)
        if nb_chapitres == 0:
            continue
        maitrise = sum(ch.maitrise_pct for ch in m.chapitres) / nb_chapitres
        data.append({"Matière": m.nom, "Maîtrise (%)": maitrise})

    if not data:
        st.info("Ajoute des matières et des chapitres dans la bibliothèque pour voir ta progression.")
        return

    df = pd.DataFrame(data).sort_values(by="Maîtrise (%)", ascending=True)
    fig = px.bar(
        df, x="Maîtrise (%)", y="Matière",
        orientation='h', range_x=[0, 100],
        color="Maîtrise (%)", color_continuous_scale="Teal",
    )
    fig.update_layout(showlegend=False, margin=dict(l=0, r=0, t=10, b=0), height=300)
    st.plotly_chart(fig, width='stretch')


def _render_charge_hebdo(session: Session) -> None:
    st.subheader("Charge d'étude par semaine")
    semaines = session.query(Semaine).order_by(Semaine.annee, Semaine.numero_semaine).all()

    data = []
    for s in semaines:
        minutes = session.query(func.sum(Tache.duree_min)).filter(
            Tache.semaine_id == s.id,
            Tache.type == "etude"
        ).scalar() or 0
        data.append({"Semaine": f"S{s.numero_semaine}", "Heures d'étude": minutes / 60})

    if not data:
        st.info("Aucun planning généré pour l'instant.")
        return

    df = pd.DataFrame(data)
    fig = px.line(df, x="Semaine", y="Heures d'étude", markers=True)
    fig.update_traces(line_color="#4cd137", marker=dict(size=8))
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=300, yaxis_title="Heures")
    st.plotly_chart(fig, width='stretch')


# ===========================================================================

__all__ = ["render"]