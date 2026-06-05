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

# Import défensif : si Streamlit Cloud a un cache d'une ancienne version
# de revision_service sans les helpers de supervision, on affiche un
# message explicite au lieu d'un ImportError opaque masqué par
# l'écran « original error message is redacted ».
try:
    from services.revision_service import (
        INTERVALLES_J,
        MAX_NIVEAU,
        chapitres_jamais_initialises,
        chapitres_par_jour_futur,
        detecter_conflits_jour,
        dette_revision,
        lisser_automatiquement_dates_leitner,
        projeter_tous_chapitres,
    )
    from services.scheduler_engine import calculer_quota_etude_minutes
except ImportError as _exc:
    import streamlit as _st_err
    _st_err.error(
        "🔄 L'app a besoin d'être redéployée — certains helpers de supervision "
        "ne sont pas encore disponibles sur ce serveur.\n\n"
        "**Solution** : dans Streamlit Cloud, clique sur **Manage app** "
        "(en bas à droite) → **Reboot app**.\n\n"
        f"Détail technique : `{_exc}`"
    )
    _st_err.stop()

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


def _render_projection_long_terme(session, matiere_ids: list[int] | None) -> None:
    """Projection théorique des futures révisions sur 30 / 90 / 180 jours.

    Suppose une trajectoire optimiste (chaque quiz réussi → niveau monte).
    Détecte deux types de conflits :
    - **Sur-charge** : un jour où la charge horaire prévue dépasse le
      plafond du profil.
    - **Doublons matière nouveaux** : un jour où plusieurs chapitres
      différents d'une même matière ont leur toute première révision (J1).
    """
    st.subheader("📈 Projection long terme (Méthode des J)")
    st.caption(
        "Vue **théorique** des futures révisions en supposant que chaque "
        "quiz est réussi (J1 → J3 → J7 → J14 → J30 → J60 → J90 → J180 → …). "
        "Idéal pour anticiper les périodes chargées (vacances, été, examens). "
        "Cette projection est recalculée à chaque ouverture de la page — "
        "elle reflète l'état Leitner actuel, pas une décision figée."
    )

    today = date.today()
    horizon_choix = st.radio(
        "Horizon",
        options=[30, 90, 180, 365],
        format_func=lambda d: f"{d} jours" if d < 365 else "1 an",
        index=1,
        horizontal=True,
    )
    horizon_jours = int(horizon_choix)

    projections = projeter_tous_chapitres(
        session, horizon_jours=horizon_jours, today=today, matiere_ids=matiere_ids,
    )

    if not projections:
        st.info("Aucune projection disponible — initialise des chapitres dans la file Leitner d'abord.")
        return

    # Récupération du plafond journalier pour détecter les sur-charges.
    from database.models import Utilisateur
    profil = session.query(Utilisateur).first()
    plafond_min = calculer_quota_etude_minutes(profil, checkin=None) if profil else 0

    # --- Détection des conflits jour par jour -------------------------
    conflits_par_jour: dict[date, dict] = {}
    for d, projs in projections.items():
        conflits_par_jour[d] = detecter_conflits_jour(projs, plafond_min)

    # --- KPIs synthétiques de la projection ---------------------------
    nb_jours_sur_charge = sum(1 for c in conflits_par_jour.values() if not c["charge_ok"])
    nb_jours_doublons = sum(1 for c in conflits_par_jour.values() if c["matieres_doublons_nouveaux"])
    total_revisions = sum(c["nb_total"] for c in conflits_par_jour.values())
    charge_totale_h = sum(c["charge_min"] for c in conflits_par_jour.values()) / 60

    cols = st.columns(4)
    cols[0].metric("📚 Révisions projetées", total_revisions)
    cols[1].metric("⏱️ Charge cumulée", f"{charge_totale_h:.1f} h")
    cols[2].metric(
        "🚨 Jours sur-chargés", nb_jours_sur_charge,
        delta="vs plafond /jour" if plafond_min > 0 else "(plafond non défini)",
        delta_color="inverse" if nb_jours_sur_charge else "off",
    )
    cols[3].metric(
        "⚠️ Jours avec doublons matière", nb_jours_doublons,
        delta="même matière, 2+ nouveaux" if nb_jours_doublons else "ok",
        delta_color="inverse" if nb_jours_doublons else "off",
    )

    # --- Graph principal sur tout l'horizon ---------------------------
    data = []
    for i in range(horizon_jours):
        d = today + timedelta(days=i)
        c = conflits_par_jour.get(d, {"nb_total": 0, "charge_ok": True, "matieres_doublons_nouveaux": []})
        nb = c["nb_total"]
        if nb == 0:
            etat = "Aucun"
        elif not c["charge_ok"]:
            etat = "🚨 Sur-charge"
        elif c["matieres_doublons_nouveaux"]:
            etat = "⚠️ Doublon matière"
        else:
            etat = "✅ OK"
        data.append({
            "Date": d.isoformat(),
            "Jour": d.strftime("%d/%m"),
            "Révisions": nb,
            "État": etat,
        })

    df = pd.DataFrame(data)
    etat_colors = {
        "Aucun": "#e5e7eb",
        "✅ OK": "#10b981",
        "⚠️ Doublon matière": "#f59e0b",
        "🚨 Sur-charge": "#ef4444",
    }
    fig = px.bar(
        df, x="Date", y="Révisions", color="État",
        color_discrete_map=etat_colors,
        hover_data={"Jour": True, "Date": False},
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=10, b=0),
        height=320,
        xaxis_title="",
        legend_title_text="",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    # Ligne de plafond horaire référence (en révisions, sachant 30 min/rev).
    if plafond_min > 0:
        ref_revisions = plafond_min / 30  # nb max théorique de révisions/jour
        fig.add_hline(
            y=ref_revisions,
            line_dash="dash",
            line_color="#9ca3af",
            annotation_text=f"Plafond ≈ {ref_revisions:.1f} révisions/jour",
            annotation_position="top right",
            annotation_font_size=10,
            annotation_font_color="#6b7280",
        )
    st.plotly_chart(fig, width="stretch")

    # --- Détail des jours à problème -----------------------------------
    jours_problematiques = [
        (d, c) for d, c in conflits_par_jour.items()
        if not c["charge_ok"] or c["matieres_doublons_nouveaux"]
    ]
    if jours_problematiques:
        # === Bouton d'auto-lissage (tolérance ±3 jours) ===
        st.warning(
            f"⚠️ **{len(jours_problematiques)} jour(s) en conflit** détectés dans "
            "la projection. Tu peux laisser l'IA répartir automatiquement avec "
            "une tolérance de ±3 jours."
        )

        col_btn, col_desc = st.columns([1, 2])
        with col_btn:
            lisser_clic = st.button(
                "🪄 Lisser automatiquement",
                type="primary", width="stretch",
                help="Décale les dates_prochaine en conflit dans une fenêtre "
                     "de [J, J+3] pour respecter (1) max 1 nouveau chapitre "
                     "par matière par jour et (2) ton plafond horaire.",
            )
        with col_desc:
            st.caption(
                "Préserve l'esprit de la méthode des J (un décalage maximum "
                "de 3 jours reste pédagogiquement valide). Les chapitres déjà "
                "à jour ne sont pas touchés."
            )

        if lisser_clic:
            from database.db import session_scope

            try:
                with session_scope() as write_session:
                    resultat = lisser_automatiquement_dates_leitner(
                        write_session,
                        tolerance_jours=3,
                        plafond_minutes_par_jour=plafond_min if plafond_min > 0 else None,
                        today=today,
                        matiere_ids=matiere_ids,
                    )

                nb_d = resultat["nb_decalages"]
                nb_nd = resultat["nb_non_decalables"]
                if nb_d == 0 and nb_nd == 0:
                    st.info("ℹ️ Aucun chapitre n'a eu besoin d'être décalé.")
                elif nb_d > 0 and nb_nd == 0:
                    st.success(
                        f"✅ **{nb_d} chapitre(s) décalé(s)** dans la "
                        f"tolérance ±3 jours. Le calendrier ci-dessus va "
                        f"se rafraîchir."
                    )
                else:
                    st.warning(
                        f"⚠️ **{nb_d} décalage(s) effectué(s)**, mais "
                        f"**{nb_nd} chapitre(s)** n'ont pas pu être lissés "
                        f"dans la fenêtre de tolérance."
                    )

                # Détail des décalages effectués pour transparence.
                if resultat["decalages"]:
                    with st.expander(f"📋 Détail des {nb_d} décalages", expanded=False):
                        for d in resultat["decalages"]:
                            st.markdown(
                                f"- **{d['titre']}** ({d['matiere']}) : "
                                f"`{d['date_origine']}` → `{d['nouvelle_date']}`  \n"
                                f"&nbsp;&nbsp;_{d['raison']}_"
                            )
                if resultat["non_decalables"]:
                    with st.expander(
                        f"🚧 Détail des {nb_nd} chapitres non lissables", expanded=False,
                    ):
                        for d in resultat["non_decalables"]:
                            st.markdown(
                                f"- **{d['titre']}** ({d['matiere']}) — "
                                f"date d'origine `{d['date_origine']}`  \n"
                                f"&nbsp;&nbsp;_{d['raison']}_"
                            )

                # Stocker le résultat dans la session pour l'afficher APRÈS
                # le st.rerun() (sinon le toast et les messages sont invisibles).
                st.session_state["lissage_result"] = resultat
                st.rerun()
            except (ValueError, KeyError) as exc:
                st.error(f"❌ Erreur lors du lissage : {exc}")
            except Exception as exc:  # noqa: BLE001
                import logging
                logging.getLogger("revisions").exception("lissage failed")
                st.error(
                    f"❌ Erreur inattendue lors du lissage : {exc}. "
                    "Consulte les logs pour le détail."
                )

        with st.expander(
            f"🔎 Détail des {len(jours_problematiques)} jour(s) à anticiper",
            expanded=False,
        ):
            st.caption(
                "Autres solutions si tu préfères : "
                "(a) initialise tes chapitres à des dates différentes ; "
                "(b) lance une **Génération IA** la semaine concernée — le "
                "scheduler répartira en respectant tes limites."
            )
            for d, c in sorted(jours_problematiques):
                jour_lbl = JOURS_FR[d.weekday()].capitalize()
                tags = []
                if not c["charge_ok"]:
                    tags.append(f"🚨 charge {c['charge_min']} min > plafond {plafond_min} min")
                if c["matieres_doublons_nouveaux"]:
                    mats = ", ".join(c["matieres_doublons_nouveaux"])
                    tags.append(f"⚠️ doublons nouveaux : {mats}")
                st.markdown(
                    f"**{jour_lbl} {d.strftime('%d/%m/%Y')}** — {c['nb_total']} révision(s)  \n"
                    + "  \n".join(f"&nbsp;&nbsp;{t}" for t in tags),
                    unsafe_allow_html=True,
                )

    # --- Détail répartition par semaine sur l'horizon ------------------
    with st.expander("📆 Vue semaine par semaine", expanded=False):
        # Agréger par numéro de semaine ISO.
        par_semaine: dict[tuple[int, int], int] = {}
        par_semaine_charge: dict[tuple[int, int], int] = {}
        for d, projs in projections.items():
            iso = d.isocalendar()
            key = (iso.year, iso.week)
            par_semaine[key] = par_semaine.get(key, 0) + len(projs)
            par_semaine_charge[key] = par_semaine_charge.get(key, 0) + sum(
                int(p.get("duree_min") or 0) for p in projs
            )

        if not par_semaine:
            st.caption("Aucune semaine à afficher.")
        else:
            for (yr, wk), nb in sorted(par_semaine.items()):
                charge_h = par_semaine_charge[(yr, wk)] / 60
                st.markdown(
                    f"- **Semaine {wk}** ({yr}) : {nb} révision(s), "
                    f"environ **{charge_h:.1f} h** d'étude projetée."
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
    
    col_rev, col_exam = st.columns(2)
    with col_rev:
        if st.button("⚡ Lancer la révision rapide", use_container_width=True, type="primary"):
            st.switch_page("pages/revision_rapide.py")
    with col_exam:
        if st.button("📝 Lancer un examen blanc", use_container_width=True):
            st.switch_page("pages/examen_blanc.py")

    # --- Afficher le résultat du lissage stocké en session (Bug 3) ---
    resultat = st.session_state.pop("lissage_result", None)
    if resultat is not None:
        nb_d = resultat["nb_decalages"]
        nb_nd = resultat["nb_non_decalables"]
        if nb_d == 0 and nb_nd == 0:
            st.info("ℹ️ Aucun chapitre n'a eu besoin d'être décalé.")
        elif nb_d > 0 and nb_nd == 0:
            st.success(
                f"✅ **{nb_d} chapitre(s) décalé(s)** dans la "
                f"tolérance ±3 jours. Le calendrier ci-dessus va "
                f"se rafraîchir."
            )
        else:
            st.warning(
                f"⚠️ **{nb_d} décalage(s) effectué(s)**, mais "
                f"**{nb_nd} chapitre(s)** n'ont pas pu être lissés "
                f"dans la fenêtre de tolérance."
            )
        st.toast(f"Lissage : {nb_d} décalage(s)", icon="🪄")
        # Détail des décalages
        if resultat["decalages"]:
            with st.expander(f"📋 Détail des {nb_d} décalages", expanded=False):
                for d in resultat["decalages"]:
                    st.markdown(
                        f"- **{d['titre']}** ({d['matiere']}) : "
                        f"`{d['date_origine']}` → `{d['nouvelle_date']}`  \n"
                        f"&nbsp;&nbsp;_{d['raison']}_"
                    )
        if resultat["non_decalables"]:
            with st.expander(f"🚧 Détail des {nb_nd} chapitres non lissables", expanded=False):
                for d in resultat["non_decalables"]:
                    st.markdown(
                        f"- **{d['titre']}** ({d['matiere']}) — "
                        f"date d'origine `{d['date_origine']}`  \n"
                        f"&nbsp;&nbsp;_{d['raison']}_"
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

        # === Projection long terme (30 / 90 / 180 / 365 jours) ===
        _render_projection_long_terme(session, matiere_ids)

        st.divider()

        # === Liste prioritaire ===
        _render_liste_detaillee(session, matiere_ids)

        st.divider()

        # === Jamais commencés ===
        _render_jamais_commences(session, matiere_ids)


