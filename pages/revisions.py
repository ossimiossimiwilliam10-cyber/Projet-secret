"""Page **🧠 Révisions (Méthode des J)** — supervision globale de la
répétition espacée.

Donne une vue stratégique à long terme :
- Combien de chapitres dans chaque niveau Leitner ?
- Lesquels sont en retard (« dette de révision ») ?
- Qu'est-ce qui tombe les 28 prochains jours, semaine par semaine ?
- Liste détaillée filtrable par matière, avec lien vers la salle d'étude.

L'algorithme Leitner lui-même (intervalles J1, J3, J7, J14, J30, J60,
J90, J180, etc.) est dans ``services.revision_service``. Cette page ne
fait que VISUALISER l'état actuel — elle n'altère rien.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from database import Matiere, get_session
from services.revision_service import (
    INTERVALLES_J,
    MAX_NIVEAU,
    chapitres_jamais_initialises,
    chapitres_par_jour_futur,
    dette_revision,
    repartition_par_niveau,
)

# ---------------------------------------------------------------------------
# Constantes UI
# ---------------------------------------------------------------------------
JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

# Code couleur par « zone » d'apprentissage.
def _color_for_niveau(niveau: int) -> str:
    """Code couleur progressif : rouge = découverte fragile, vert = ancrage."""
    if niveau <= 1:    # J1 / J3 — découverte
        return "#ef4444"
    if niveau <= 3:    # J7 / J14 — consolidation
        return "#f59e0b"
    if niveau <= 5:    # J30 / J60 — ancrage moyen terme
        return "#84cc16"
    if niveau <= 7:    # J90 / J180 — ancrage solide
        return "#10b981"
    return "#3b82f6"   # J365+ — maîtrise long terme


def _label_for_niveau(niveau: int) -> str:
    """Libellé J pour un niveau donné, basé sur INTERVALLES_J."""
    if 0 <= niveau < len(INTERVALLES_J):
        jours = INTERVALLES_J[niveau]
        return f"J{jours}"
    return f"niveau {niveau}"


def _etat_chapitre(chap, today: date) -> tuple[str, str]:
    """Renvoie (label, couleur) pour l'état actuel d'un chapitre."""
    if chap.date_prochaine is None:
        return "🆕 Jamais commencé", "#9ca3af"
    delta = (chap.date_prochaine - today).days
    if delta < 0:
        return f"⚠️ En retard de {-delta}j", "#ef4444"
    if delta == 0:
        return "🔥 Aujourd'hui", "#f59e0b"
    if delta == 1:
        return "📅 Demain", "#f59e0b"
    if delta <= 7:
        return f"📅 Dans {delta}j", "#10b981"
    return f"⏳ Dans {delta}j", "#6b7280"


def _matiere_label(chap) -> str:
    return chap.matiere_obj.nom if chap.matiere_obj else "Sans matière"


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
def _render_kpis(session, matiere_ids: list[int] | None) -> None:
    """Bandeau du haut — 4 KPI essentiels."""
    today = date.today()

    dette = dette_revision(session, today=today, matiere_ids=matiere_ids)
    par_jour = chapitres_par_jour_futur(
        session, days_ahead=28, today=today, matiere_ids=matiere_ids,
    )
    # Chapitres dus aujourd'hui = inclut les retards rapatriés sur today.
    nb_aujourdhui = len(par_jour.get(today, []))
    nb_dette = len(dette)

    # Total des chapitres dus dans les 7 prochains jours (hors retards déjà comptés).
    nb_7j = 0
    for d, chaps in par_jour.items():
        if d <= today + timedelta(days=7):
            nb_7j += len(chaps)

    # Total chapitres actifs (avec une date posée).
    from database.models import Chapitre
    q_total = session.query(Chapitre).filter(Chapitre.date_prochaine.isnot(None))
    if matiere_ids is not None:
        if not matiere_ids:
            nb_total = 0
        else:
            nb_total = q_total.filter(Chapitre.matiere_id.in_(matiere_ids)).count()
    else:
        nb_total = q_total.count()

    cols = st.columns(4)
    cols[0].metric(
        "🔥 En retard", nb_dette,
        delta="à rattraper" if nb_dette else "à jour",
        delta_color="inverse" if nb_dette else "off",
    )
    cols[1].metric("📅 Aujourd'hui", nb_aujourdhui)
    cols[2].metric("🕓 7 prochains jours", nb_7j)
    cols[3].metric("📦 Chapitres actifs", nb_total)

    # Message synthétique.
    if nb_dette == 0 and nb_aujourdhui == 0:
        st.success("🎉 Aucune révision en attente. File de révision à jour !")
    elif nb_dette > 0:
        st.warning(
            f"⚠️ **Dette de révision : {nb_dette} chapitre(s) en retard.** "
            "Tu peux commencer par les plus anciens en bas de page."
        )
    elif nb_aujourdhui > 0:
        st.info(f"📅 {nb_aujourdhui} chapitre(s) à réviser aujourd'hui pour rester à jour.")


def _render_distribution_leitner(session, matiere_ids: list[int] | None) -> None:
    """Histogramme des chapitres par niveau Leitner.

    Donne une vision instantanée de l'avancée : combien sont au stade
    découverte (J1/J3) vs ancrage long terme (J365+).
    """
    st.subheader("📊 Répartition par niveau Leitner")
    st.caption(
        "Combien de chapitres dans chaque « boîte » de l'algo Leitner. "
        "Plus tu progresses à droite, plus tes connaissances sont ancrées "
        "à long terme."
    )

    # On filtre par matière si demandé.
    from database.models import Chapitre
    query = session.query(Chapitre)
    if matiere_ids is not None:
        if not matiere_ids:
            st.info("Aucune matière sélectionnée.")
            return
        query = query.filter(Chapitre.matiere_id.in_(matiere_ids))

    rows = query.all()
    if not rows:
        st.info("Aucun chapitre en base pour le moment.")
        return

    counts: dict[int, int] = {}
    for ch in rows:
        n = int(ch.niveau_actuel or 0)
        counts[n] = counts.get(n, 0) + 1

    data = []
    for n in range(0, MAX_NIVEAU + 1):
        data.append({
            "Niveau": _label_for_niveau(n),
            "Nombre de chapitres": counts.get(n, 0),
            "Couleur": _color_for_niveau(n),
        })

    df = pd.DataFrame(data)
    fig = px.bar(
        df, x="Niveau", y="Nombre de chapitres",
        color="Niveau",
        color_discrete_map={row["Niveau"]: row["Couleur"] for row in data},
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=10, b=0),
        height=260,
        showlegend=False,
        xaxis_title="",
        yaxis_title="Chapitres",
    )
    st.plotly_chart(fig, width="stretch")


def _render_calendrier_28j(session, matiere_ids: list[int] | None) -> None:
    """Calendrier prévisionnel sur 4 semaines."""
    st.subheader("📅 Calendrier des 4 prochaines semaines")
    st.caption(
        "Pour chaque jour à venir, les chapitres dont la date de révision "
        "Leitner tombe ce jour-là. Les retards sont rapatriés sur aujourd'hui."
    )

    today = date.today()
    par_jour = chapitres_par_jour_futur(
        session, days_ahead=28, today=today, matiere_ids=matiere_ids,
    )

    # Heatmap : un point par jour, taille = nb chapitres.
    data = []
    for i in range(28):
        d = today + timedelta(days=i)
        chaps = par_jour.get(d, [])
        data.append({
            "Date": d,
            "Jour": JOURS_FR[d.weekday()].capitalize()[:3] + " " + d.strftime("%d/%m"),
            "Chapitres": len(chaps),
            "Semaine": f"S{d.isocalendar().week:02d}",
        })

    df = pd.DataFrame(data)
    fig = px.bar(
        df, x="Jour", y="Chapitres", color="Semaine",
        color_discrete_sequence=px.colors.qualitative.Set2,
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=10, b=0),
        height=280,
        xaxis_tickangle=-45,
        legend_title_text="",
    )
    st.plotly_chart(fig, width="stretch")

    # Détail jour par jour des 14 prochains jours (les 14 suivants sont
    # plus prospectifs, on ne les détaille pas pour rester lisible).
    with st.expander("🔎 Détail jour par jour (14 prochains jours)", expanded=False):
        for i in range(14):
            d = today + timedelta(days=i)
            chaps = par_jour.get(d, [])
            if not chaps:
                continue
            jour_lbl = JOURS_FR[d.weekday()].capitalize()
            tag = ""
            if d == today:
                tag = " 📍 aujourd'hui"
            elif d == today + timedelta(days=1):
                tag = " (demain)"
            st.markdown(f"**{jour_lbl} {d.strftime('%d/%m')}{tag} — {len(chaps)} chapitre(s)**")
            for chap in chaps:
                niveau = int(chap.niveau_actuel or 0)
                badge_color = _color_for_niveau(niveau)
                etat_label, etat_color = _etat_chapitre(chap, today)
                st.markdown(
                    f"<div style='padding:6px 12px;margin:4px 0;"
                    f"border-left:3px solid {badge_color};background:{badge_color}10;"
                    f"border-radius:4px;'>"
                    f"<b>{chap.titre}</b> "
                    f"<span style='color:#6b7280;font-size:0.85rem;'>· {_matiere_label(chap)}</span>"
                    f"<span style='float:right;font-size:0.8rem;color:{etat_color};font-weight:600;'>"
                    f"{_label_for_niveau(niveau)} · {etat_label}"
                    f"</span></div>",
                    unsafe_allow_html=True,
                )


def _render_liste_detaillee(session, matiere_ids: list[int] | None) -> None:
    """Liste cliquable des chapitres dus dans la fenêtre (urgent en haut)."""
    st.subheader("🎯 À traiter en priorité")
    st.caption(
        "Triés du plus en retard au plus lointain. Clique pour ouvrir un "
        "chapitre dans la salle d'étude."
    )

    today = date.today()
    par_jour = chapitres_par_jour_futur(
        session, days_ahead=14, today=today, matiere_ids=matiere_ids,
    )
    # Aplatir + trier par date d'origine (les retards d'abord).
    items: list[tuple[date, "Chapitre"]] = []  # noqa: F821 (forward ref)
    for d_affichee, chaps in par_jour.items():
        for chap in chaps:
            items.append((chap.date_prochaine, chap))
    items.sort(key=lambda x: x[0])

    if not items:
        st.info("Aucune révision à traiter dans les 2 prochaines semaines.")
        return

    for date_due, chap in items[:30]:
        niveau = int(chap.niveau_actuel or 0)
        badge_color = _color_for_niveau(niveau)
        etat_label, etat_color = _etat_chapitre(chap, today)

        col_info, col_btn = st.columns([5, 1])
        with col_info:
            st.markdown(
                f"<div style='padding:8px 12px;border-left:3px solid {badge_color};"
                f"background:{badge_color}10;border-radius:4px;'>"
                f"<div><b>{chap.titre}</b> "
                f"<span style='color:#6b7280;font-size:0.85rem;'>· {_matiere_label(chap)}</span></div>"
                f"<div style='font-size:0.8rem;margin-top:2px;'>"
                f"<span style='color:{badge_color};font-weight:600;'>{_label_for_niveau(niveau)}</span>"
                f" · <span style='color:{etat_color};font-weight:600;'>{etat_label}</span>"
                f" · 📅 prévue {date_due.strftime('%d/%m')}"
                f"</div></div>",
                unsafe_allow_html=True,
            )
        with col_btn:
            if st.button("🧠 Réviser", key=f"goto_rev_{chap.id}", width="stretch"):
                st.session_state.target_chapitre_id = chap.id
                try:
                    st.switch_page("pages/session_etude.py")
                except Exception:  # noqa: BLE001
                    st.session_state.navigate_to_session = True


def _render_jamais_commences(session, matiere_ids: list[int] | None) -> None:
    """Section pliable pour les chapitres qui n'ont pas encore de date."""
    chaps = chapitres_jamais_initialises(session, matiere_ids=matiere_ids)
    if not chaps:
        return

    with st.expander(f"🆕 Chapitres jamais commencés ({len(chaps)})", expanded=False):
        st.caption(
            "Ces chapitres sont dans ta bibliothèque mais n'ont pas encore "
            "été placés dans la file Leitner. Ouvre-les pour commencer."
        )
        for chap in chaps:
            col_info, col_btn = st.columns([5, 1])
            with col_info:
                st.markdown(
                    f"<div style='padding:6px 12px;border-left:3px solid #9ca3af;"
                    f"background:#9ca3af15;border-radius:4px;'>"
                    f"<b>{chap.titre}</b> "
                    f"<span style='color:#6b7280;font-size:0.85rem;'>· {_matiere_label(chap)}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with col_btn:
                if st.button("▶️ Ouvrir", key=f"goto_new_{chap.id}", width="stretch"):
                    st.session_state.target_chapitre_id = chap.id
                    try:
                        st.switch_page("pages/session_etude.py")
                    except Exception:  # noqa: BLE001
                        st.session_state.navigate_to_session = True


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("🧠 Vue d'ensemble — Méthode des J")
    st.caption(
        "Supervision globale de ta répétition espacée. Tous tes chapitres, "
        "où ils en sont dans le cycle Leitner (J1 → J3 → J7 → J14 → J30 → "
        "J60 → J90 → J180 → …), et ce qui tombe les prochaines semaines."
    )

    with get_session() as session:
        # Filtre matière partagé par toutes les sections.
        matieres = (
            session.query(Matiere).filter_by(actif=True).order_by(Matiere.nom).all()
        )
        if not matieres:
            st.info(
                "📭 Aucune matière en base. Ajoute une matière et importe "
                "des PDFs dans **Bibliothèque** pour démarrer."
            )
            return

        nom_to_id = {m.nom: m.id for m in matieres}
        choix = st.multiselect(
            "Filtrer par matière",
            options=list(nom_to_id.keys()),
            default=[],
            help="Laisse vide pour voir toutes les matières.",
        )
        matiere_ids = [nom_to_id[c] for c in choix] if choix else None

        # === KPIs ===
        _render_kpis(session, matiere_ids)

        st.divider()

        # === Distribution Leitner ===
        _render_distribution_leitner(session, matiere_ids)

        st.divider()

        # === Calendrier 28 jours ===
        _render_calendrier_28j(session, matiere_ids)

        st.divider()

        # === Liste prioritaire ===
        _render_liste_detaillee(session, matiere_ids)

        st.divider()

        # === Jamais commencés ===
        _render_jamais_commences(session, matiere_ids)


# Exécution Streamlit
render()
