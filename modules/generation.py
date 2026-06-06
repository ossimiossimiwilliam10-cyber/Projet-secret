"""Onglet **Génération du planning IA**.

Pilote la génération hebdomadaire par Gemini, sauvegarde le résultat
en base et affiche le planning généré avec ses indicateurs (score de
réalisme, stratégie IA, alertes, suggestions, statistiques).
"""

from __future__ import annotations

import datetime
from typing import Any

import streamlit as st
from sqlalchemy.orm import Session

from database import CheckInQuotidien, Utilisateur, Semaine, Tache, get_session, session_scope
from services.ai_planner import (
    detecter_chapitres_manquants,
    generate_schedule_from_ai,
    integrer_nouveautes_a_semaine,
)
from services.scheduler_engine import (
    calculer_cible_hebdo_minutes,
    calculer_quota_etude_minutes,
)


# ---------------------------------------------------------------------------
# Constantes UI
# ---------------------------------------------------------------------------
JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

TYPE_COLORS: dict[str, str] = {
    "repas":            "#fbc531",
    "sommeil":          "#273c75",
    "etude":            "#4cd137",
    "sport":            "#e84118",
    "courses":          "#00a8ff",
    "projet":           "#9c88ff",
    "dev_perso":        "#00bc8c",
    "social":           "#e1b12c",
    "intendance":       "#718093",
    "transport":        "#bdc3c7",
    "cours_presentiel": "#a29bfe",
    "travail":          "#d63031",
    "meal_prep":        "#fbc531",
}

TYPE_ICONS: dict[str, str] = {
    "repas":            "🍽️",
    "sommeil":          "😴",
    "etude":            "📚",
    "sport":            "🏃",
    "courses":          "🛒",
    "projet":           "🎯",
    "dev_perso":        "🌱",
    "social":           "🍹",
    "intendance":       "🧹",
    "transport":        "🚇",
    "cours_presentiel": "🎓",
    "travail":          "💼",
    "meal_prep":        "🍱",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _str_to_time(time_str: str) -> datetime.time:
    """Convertit 'HH:MM' en objet datetime.time."""
    h, m = map(int, time_str.split(":"))
    return datetime.time(h, m)


def _get_color_for_task(task_type: str) -> str:
    return TYPE_COLORS.get((task_type or "").lower(), "#353b48")


def _get_icon_for_task(task_type: str) -> str:
    return TYPE_ICONS.get((task_type or "").lower(), "•")


def _duree_min(t: Tache) -> int:
    """Durée en minutes, recalculée si duree_min est NULL."""
    if t.duree_min:
        return int(t.duree_min)
    if t.heure_debut and t.heure_fin:
        h1 = t.heure_debut.hour * 60 + t.heure_debut.minute
        h2 = t.heure_fin.hour * 60 + t.heure_fin.minute
        return max(0, h2 - h1)
    return 0


# ---------------------------------------------------------------------------
# Persistance du JSON Gemini
# ---------------------------------------------------------------------------
def _save_planning_to_db(semaine_id: int, planning_json: dict[str, Any]) -> int:
    """Transforme le JSON de Gemini en objets Tache et les sauvegarde.

    Returns:
        Le nombre de tâches effectivement créées (utile pour les stats).
    """
    nb_taches = 0
    with session_scope() as session:
        semaine = session.get(Semaine, semaine_id)
        if not semaine:
            return 0

        semaine.score_realisme = planning_json.get("score_realisme", 0)
        semaine.bilan_ia = planning_json.get("justification_globale", "")
        semaine.statut = "generee"

        # Reset des anciennes tâches en cas de régénération.
        session.query(Tache).filter_by(semaine_id=semaine_id).delete()

        planning_jours = planning_json.get("planning", {}) or {}
        for jour in JOURS_FR:
            for t_data in planning_jours.get(jour, []) or []:
                try:
                    h_debut = _str_to_time(t_data["heure_debut"])
                    h_fin = _str_to_time(t_data["heure_fin"])
                    # duree_min : calculée dès l'insert pour que les
                    # agrégations SQL (sum(Tache.duree_min)) fonctionnent.
                    # Sans ça, le KPI heures-travail-total du dashboard
                    # renvoyait toujours 0.
                    duree = (
                        (h_fin.hour * 60 + h_fin.minute)
                        - (h_debut.hour * 60 + h_debut.minute)
                    )
                    nouvelle = Tache(
                        semaine_id=semaine_id,
                        type=str(t_data.get("type") or "autre").lower(),
                        titre=t_data.get("titre", "Tâche sans nom"),
                        jour=jour,
                        heure_debut=h_debut,
                        heure_fin=h_fin,
                        duree_min=max(0, duree),
                        obligatoire=bool(t_data.get("obligatoire", False)),
                        justification_ia=t_data.get("justification", "") or "",
                        statut="a_faire",
                        chapitre_ids=t_data.get("chapitre_ids") or [],
                    )
                    session.add(nouvelle)
                    nb_taches += 1
                except (KeyError, ValueError, TypeError) as exc:
                    # Une tâche mal formée par Gemini ne doit pas faire
                    # exploser tout l'import.
                    import logging
                    logging.getLogger("generation").warning(
                        "Tache ignoree (%s): %s -- %s", jour, exc, t_data,
                    )
    return nb_taches


# ---------------------------------------------------------------------------
# Helpers d'affichage
# ---------------------------------------------------------------------------
def _render_score_card(score: int) -> None:
    """Carte du score de réalisme du planning."""
    if score >= 80:
        bg, fg, label = "#10b981", "#ffffff", "✅ Très réaliste"
    elif score >= 50:
        bg, fg, label = "#f59e0b", "#ffffff", "⚠️ Tendu mais faisable"
    else:
        bg, fg, label = "#ef4444", "#ffffff", "🚨 Irréaliste"

    st.markdown(
        f"<div style='background:{bg};color:{fg};padding:18px;"
        f"border-radius:10px;text-align:center;'>"
        f"<div style='font-size:0.85rem;opacity:0.85;letter-spacing:0.04em;'>SCORE DE RÉALISME</div>"
        f"<div style='font-size:2.6rem;font-weight:800;line-height:1;'>{score}<span style='font-size:1.2rem;opacity:0.7;'>/100</span></div>"
        f"<div style='font-size:0.9rem;opacity:0.95;margin-top:2px;'>{label}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _compute_planning_stats(taches: list[Tache]) -> dict[str, Any]:
    """Agrège : minutes par type, totaux, charge d'étude."""
    by_type_min: dict[str, int] = {}
    nb_obligatoires = 0
    nb_libres = 0
    for t in taches:
        dur = _duree_min(t)
        ttype = (t.type or "autre").lower()
        by_type_min[ttype] = by_type_min.get(ttype, 0) + dur
        if t.obligatoire:
            nb_obligatoires += 1
        else:
            nb_libres += 1
    total_min = sum(by_type_min.values())
    return {
        "by_type_min": by_type_min,
        "nb_obligatoires": nb_obligatoires,
        "nb_libres": nb_libres,
        "nb_total": nb_obligatoires + nb_libres,
        "total_min": total_min,
        "etude_min": by_type_min.get("etude", 0),
    }


def _render_planning_stats(stats: dict[str, Any], cible_hebdo_min: int) -> None:
    """Affiche les KPIs synthétiques sous le score, calibrés sur l'objectif
    hebdomadaire d'études du profil."""
    etude_pct = (
        round(stats["etude_min"] / cible_hebdo_min * 100)
        if cible_hebdo_min > 0
        else 0
    )

    cols = st.columns(4)
    cols[0].metric(
        "🗓️ Tâches planifiées",
        f"{stats['nb_total']}",
        delta=f"{stats['nb_libres']} libres",
        delta_color="off",
    )
    cols[1].metric(
        "📚 Heures d'étude",
        f"{stats['etude_min'] / 60:.1f} h",
        delta=f"{etude_pct}% de l'objectif hebdo",
        delta_color="off",
    )
    cols[2].metric(
        "🏃 Heures hors étude",
        f"{(stats['total_min'] - stats['etude_min']) / 60:.1f} h",
        delta=f"{stats['nb_obligatoires']} obligatoires",
        delta_color="off",
    )

    if cible_hebdo_min == 0:
        cols[3].metric("🎯 Objectif hebdo", "—")
    elif stats["etude_min"] > cible_hebdo_min * 1.2:
        cols[3].metric(
            "🎯 Objectif hebdo", "⚠️ Dépassé",
            delta=f"+{(stats['etude_min'] - cible_hebdo_min) / 60:.1f} h",
            delta_color="inverse",
        )
    elif stats["etude_min"] < cible_hebdo_min * 0.6:
        cols[3].metric(
            "🎯 Objectif hebdo", "🌱 Léger",
            delta=f"-{(cible_hebdo_min - stats['etude_min']) / 60:.1f} h",
            delta_color="off",
        )
    else:
        cols[3].metric("🎯 Objectif hebdo", "✅ Bien dosé", delta_color="off")


def _render_repartition_par_type(stats: dict[str, Any]) -> None:
    """Mini-bar visuelle de la répartition du temps par type de tâche."""
    by_type = stats["by_type_min"]
    if not by_type:
        return
    total = sum(by_type.values()) or 1

    # Tri décroissant
    items = sorted(by_type.items(), key=lambda kv: -kv[1])

    # Construction d'une barre HTML horizontale.
    segments = []
    for ttype, minutes in items:
        if minutes <= 0:
            continue
        width = minutes / total * 100
        color = _get_color_for_task(ttype)
        segments.append(
            f"<div title='{ttype} : {minutes} min' "
            f"style='background:{color};width:{width:.2f}%;height:18px;'></div>"
        )
    bar_html = (
        "<div style='display:flex;border-radius:6px;overflow:hidden;"
        "border:1px solid #e5e7eb;'>"
        + "".join(segments)
        + "</div>"
    )

    # Légende compacte sous la barre.
    legend_chips = []
    for ttype, minutes in items:
        if minutes <= 0:
            continue
        color = _get_color_for_task(ttype)
        icon = _get_icon_for_task(ttype)
        h = minutes / 60
        legend_chips.append(
            f"<span style='display:inline-flex;align-items:center;gap:4px;"
            f"margin-right:14px;font-size:0.85rem;color:#374151;'>"
            f"<span style='width:10px;height:10px;background:{color};border-radius:2px;'></span>"
            f"{icon} {ttype} <b>{h:.1f} h</b></span>"
        )
    legend_html = (
        "<div style='margin-top:6px;line-height:1.8;'>"
        + "".join(legend_chips)
        + "</div>"
    )

    st.markdown(
        "<div style='margin-top:8px;'>"
        "<div style='font-size:0.85rem;color:#6b7280;margin-bottom:4px;'>"
        "Répartition de la semaine par type de tâche"
        "</div>"
        + bar_html
        + legend_html
        + "</div>",
        unsafe_allow_html=True,
    )


def _render_insights(resultat_json: dict[str, Any]) -> None:
    """Affiche alertes / suggestions / tâches écartées de manière claire."""
    alertes = resultat_json.get("alertes") or []
    suggestions = resultat_json.get("suggestions") or []
    ecartees = resultat_json.get("taches_ecartees") or []

    if not (alertes or suggestions or ecartees):
        return

    with st.expander("🔍 Détails de la génération", expanded=bool(alertes or ecartees)):
        if alertes:
            st.markdown("##### ⚠️ Alertes")
            for a in alertes:
                st.warning(str(a))
        if ecartees:
            st.markdown(f"##### 📤 Tâches écartées ({len(ecartees)})")
            for t in ecartees:
                if isinstance(t, dict):
                    titre = t.get("titre", "?")
                    raison = t.get("raison", "")
                    st.markdown(f"- **{titre}** — _{raison}_")
                else:
                    st.markdown(f"- {t}")
        if suggestions:
            st.markdown("##### 💡 Suggestions")
            for s in suggestions:
                st.info(str(s))


def _render_jour_tab(jour_cible: str, taches_du_jour: list[Tache]) -> None:
    """Rendu d'un onglet jour : KPIs + liste des tâches cliquables."""
    if not taches_du_jour:
        st.write("Aucune tâche planifiée pour ce jour.")
        return

    # Mini-KPI du jour
    nb = len(taches_du_jour)
    duree_totale = sum(_duree_min(t) for t in taches_du_jour)
    etude_min = sum(_duree_min(t) for t in taches_du_jour if (t.type or "").lower() == "etude")

    c1, c2, c3 = st.columns(3)
    c1.metric("🗓️ Tâches", nb)
    c2.metric("⏱️ Temps total", f"{duree_totale / 60:.1f} h")
    c3.metric("📚 Études", f"{etude_min / 60:.1f} h")

    st.markdown("---")

    for t in taches_du_jour:
        color = _get_color_for_task(t.type)
        icon = _get_icon_for_task(t.type)
        oblig_icon = "🔒 " if t.obligatoire else ""
        duree = _duree_min(t)
        label = (
            f"{t.heure_debut.strftime('%H:%M')} → {t.heure_fin.strftime('%H:%M')}  ·  "
            f"{icon} {oblig_icon}{t.titre}  ·  {duree} min"
        )

        # Bandeau coloré pour signaler le type.
        st.markdown(
            f"<div style='border-left:4px solid {color};padding-left:8px;"
            f"margin:6px 0;'></div>",
            unsafe_allow_html=True,
        )

        is_etude = (t.type or "").lower() == "etude" and bool(t.chapitre_ids)
        if st.button(label, key=f"btn_t_{t.id}", width="stretch"):
            if is_etude:
                st.session_state.target_chapitre_id = t.chapitre_ids[0]
                try:
                    st.session_state.active_etude_tab = "🧠 Salle d'Étude"
                    st.switch_page("pages/centre_etude.py")
                except Exception:  # noqa: BLE001
                    st.session_state.navigate_to_session = True
            else:
                st.toast(
                    "Cette tâche n'est liée à aucun chapitre d'étude.",
                    icon="ℹ️",
                )

        # Justification IA et commentaires éventuels.
        if t.justification_ia:
            st.caption(f"💭 *{t.justification_ia}*")


# ---------------------------------------------------------------------------
# Rendu UI
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("✨ Génération du planning")
    st.caption(
        "Laisse l'IA construire ton emploi du temps de la semaine. "
        "Elle s'appuie sur ton profil, ta sélection d'études et le check-in du jour."
    )

    # === Sélecteur de semaine cible ===
    # Permet de planifier la semaine prochaine le dimanche soir (cas
    # d'usage classique). Le choix est partagé avec l'onglet Études via
    # st.session_state["semaine_target_offset"] pour rester cohérent.
    offset_courant = int(st.session_state.get("semaine_target_offset", 0))
    options = {
        0: "📅 Cette semaine (en cours)",
        1: "📆 Semaine prochaine",
    }
    nouveau_offset = st.radio(
        "Semaine à planifier",
        options=list(options.keys()),
        format_func=lambda k: options[k],
        index=list(options.keys()).index(offset_courant) if offset_courant in options else 0,
        horizontal=True,
        key="generation_semaine_target",
    )
    if nouveau_offset != offset_courant:
        st.session_state["semaine_target_offset"] = nouveau_offset
        st.rerun()

    # La session reste ouverte pendant tout le rendu pour servir les lectures
    # (checkin, détection chapitres manquants, stats). Les appels Gemini lents
    # ouvrent LEUR PROPRE session via session_scope() → pas de lock concurrent.
    with get_session() as session:
        # On crée la semaine cible si elle n'existe pas encore — l'utilisateur
        # peut basculer en avance sur la semaine prochaine sans devoir d'abord
        # passer par Études.
        from utils.helpers import get_or_create_week_for_offset
        semaine, _saisie_target, _ = get_or_create_week_for_offset(
            session, offset_weeks=offset_courant,
        )
        if not semaine:
            st.warning(
                "⚠️ Aucune semaine en cours. Ouvre d'abord l'onglet **Études** "
                "pour initialiser la saisie hebdo."
            )
            return

        profil = session.query(Utilisateur).first()
        checkin_row = (
            session.query(CheckInQuotidien)
            .filter(CheckInQuotidien.date == datetime.date.today())
            .first()
        )
        checkin = None
        if checkin_row:
            checkin = {
                "fatigue_physique": int(checkin_row.fatigue_physique or 0),
                "charge_mentale": int(checkin_row.charge_mentale or 0),
                "qualite_sommeil": int(checkin_row.qualite_sommeil or 0),
            }
        quota_etude_jour_min = calculer_quota_etude_minutes(profil, checkin)
        cible_hebdo_min = calculer_cible_hebdo_minutes(profil)

        # === Bandeau d'aide & contexte ===
        col_h1, col_h2 = st.columns([2, 1])
        with col_h1:
            st.info(
                f"📅 **Semaine {semaine.numero_semaine}** — "
                f"du {semaine.date_debut.strftime('%d/%m')} au {semaine.date_fin.strftime('%d/%m/%Y')}"
            )
        with col_h2:
            st.metric(
                "🎯 Objectif hebdo",
                f"{cible_hebdo_min / 60:.1f} h",
                help=f"Plafond journalier : {quota_etude_jour_min / 60:.1f} h.",
            )

        # === Lancement de la génération ===
        st.subheader("1. Lancer la planification")
        is_generee = semaine.statut == "generee"

        # Bandeau de feedback persistant — affiché une seule fois après une
        # génération / régénération / intégration réussie. Sans ce flag,
        # st.rerun() effaçait le message de succès avant que l'utilisateur
        # ne le voie, donnant l'impression que « rien ne s'est passé ».
        flash = st.session_state.pop("generation_flash", None)
        if flash:
            labels = {
                "first":       "🎉 Planning généré",
                "regen":       "🔄 Planning régénéré",
                "integration": "🔁 Nouveautés intégrées",
            }
            titre = labels.get(flash["kind"], "✅ Planning mis à jour")
            st.success(
                f"{titre} à **{flash['ts']}** · "
                f"**{flash['nb_taches']} tâche(s)** · "
                f"score de réalisme **{flash['score']}/100**. "
                "Vérifie le planning ci-dessous, et la stratégie de l'IA "
                "s'est mise à jour dans la section 2."
            )

        consignes_manuelles = st.text_area(
            "💬 Consignes exceptionnelles pour cette semaine (optionnel)",
            value="",
            placeholder="Ex : Jeudi je dois finir avant 18h. Pas de sport mardi car genou. "
                        "Priorise les maths cette semaine.",
            help="L'IA appliquera ces consignes en plus des règles habituelles.",
            max_chars=2000,
        )

        # 🎛️ Curseur d'équilibre de vie
        st.caption("**🎛️ Équilibre de vie** — ajuste la priorité donnée aux études vs vie perso :")
        balance = st.select_slider(
            "Priorité",
            options=["📚 Max études", "⚖️ Équilibré", "🌴 Semaine light"],
            value="⚖️ Équilibré",
            key="gen_balance",
            help="L'IA adaptera la densité du planning selon ton choix.",
        )

        # 🔮 Simulation "Et si..."
        with st.expander("🔮 Simulation : impact de ma fatigue sur le planning", expanded=False):
            sim_fatigue = st.slider("Si ma fatigue est de...", 1, 10, 5, key="sim_fatigue")
            reduc = 30 if sim_fatigue > 7 else 0
            sim_plafond = quota_etude_jour_min
            if reduc:
                sim_plafond = int(sim_plafond * 0.7)
            st.caption(
                f"Plafond estimé : **{sim_plafond / 60:.1f}h/jour** "
                f"{'(−30% fatigue)' if reduc else '(inchangé)'} "
                f"→ ~{sim_plafond * 7 / 60:.0f}h max cette semaine"
            )

        label_btn = "🔄 Régénérer le planning" if is_generee else "🚀 Générer le planning avec DeepSeek"
        if st.button(label_btn, type="primary", width="stretch"):
            with st.spinner("🧠 DeepSeek construit ton planning… (15-30 secondes)"):
                try:
                    resultat_json = generate_schedule_from_ai(
                        semaine.id,
                        consignes_manuelles=consignes_manuelles,
                    )
                    nb_taches = _save_planning_to_db(semaine.id, resultat_json)
                    # Feedback persistant via session_state — survit au rerun.
                    st.session_state["dernier_resultat_ia"] = resultat_json
                    st.session_state["generation_flash"] = {
                        "nb_taches": nb_taches,
                        "score": int(resultat_json.get("score_realisme", 0) or 0),
                        "kind": "regen" if is_generee else "first",
                        "ts": datetime.datetime.now().strftime("%H:%M:%S"),
                    }
                    st.toast("Planning prêt !", icon="🎉")
                    st.rerun()
                except (ValueError, RuntimeError) as exc:
                    # Erreurs métier connues : clé manquante, Gemini KO,
                    # JSON cabossé, schéma invalide. Message clair à l'utilisateur.
                    st.error(f"❌ Erreur lors de la génération : {exc}")
                except Exception as exc:  # noqa: BLE001
                    # Filet de sécurité : on log pour debug mais on ne crash pas l'UI.
                    import logging
                    logging.getLogger("gemini").exception("planner_generate failed")
                    st.error(
                        f"❌ Erreur inattendue lors de la génération : {exc}. "
                        "Consulte les logs pour le détail."
                    )

        # === Bouton incrémental : intégrer les nouveautés sans tout casser ===
        # Visible uniquement si un planning existe ET qu'on détecte au moins un
        # chapitre manquant (ajouté en biblio après génération OU révision
        # Leitner dont la date est tombée dans la semaine après coup).
        if is_generee:
            try:
                ids_manquants = detecter_chapitres_manquants(session, semaine)
            except Exception:  # noqa: BLE001
                ids_manquants = []

            if ids_manquants:
                st.info(
                    f"➕ **{len(ids_manquants)} nouveauté(s) détectée(s)** : "
                    "chapitres ajoutés ou révisions Leitner nouvellement dues, "
                    "qui ne sont pas encore dans ton planning."
                )
                if st.button(
                    "🔁 Intégrer mes nouveautés (sans perdre ma progression)",
                    type="secondary",
                    width="stretch",
                    help="Préserve toutes tes tâches validées (fait/partiel/non fait) "
                         "et tes tâches obligatoires. Ajoute juste les nouveaux "
                         "chapitres dans les créneaux libres restants.",
                ):
                    with st.spinner("🧠 DeepSeek intègre tes nouveautés…"):
                        try:
                            resultat_inc = integrer_nouveautes_a_semaine(
                                session, semaine.id,
                            )
                            nb_int = resultat_inc.get("nb_taches_ajoutees", 0)
                            st.session_state["dernier_resultat_ia"] = resultat_inc
                            st.session_state["generation_flash"] = {
                                "nb_taches": nb_int,
                                "score": int(resultat_inc.get("score_realisme", 0) or 0),
                                "kind": "integration",
                                "ts": datetime.datetime.now().strftime("%H:%M:%S"),
                            }
                            st.toast("Nouveautés intégrées !", icon="🔁")
                            st.rerun()
                        except ValueError as exc:
                            st.warning(f"ℹ️ {exc}")
                        except RuntimeError as exc:
                            st.error(f"❌ Configuration ou SDK manquant : {exc}")
                        except Exception as exc:  # noqa: BLE001
                            import logging
                            logging.getLogger("gemini").exception(
                                "planner_integrer_nouveautes failed"
                            )
                            st.error(
                                f"❌ Erreur inattendue lors de l'intégration : {exc}. "
                                "Consulte les logs pour le détail."
                            )

        # Insights de la dernière génération (consignes / alertes / suggestions /
        # tâches écartées). On les garde en session_state pour qu'ils survivent
        # au rerun déclenché juste après la sauvegarde.
        dernier_resultat = st.session_state.get("dernier_resultat_ia")
        if dernier_resultat:
            _render_insights(dernier_resultat)

        st.divider()

        # === Affichage du planning généré ===
        if not is_generee:
            st.info(
                "📭 Aucun planning généré pour cette semaine. Clique sur le bouton "
                "ci-dessus pour en créer un."
            )
            return

        st.subheader(f"2. Planning de la Semaine {semaine.numero_semaine}")

        # Score + stratégie
        col_strat, col_score = st.columns([3, 1])
        with col_strat:
            if semaine.bilan_ia:
                st.markdown(f"**🧠 Stratégie de l'IA**\n\n_{semaine.bilan_ia}_")
            else:
                st.caption("L'IA n'a pas fourni de stratégie pour cette semaine.")
        with col_score:
            _render_score_card(int(semaine.score_realisme or 0))

        # === Export iCal (pour Google Calendar / Apple Calendar / etc.) ===
        try:
            from services.ical_exporter import build_ics_for_semaine, make_ics_filename
            col_dl1, col_dl2, col_dl3 = st.columns([1, 1, 2])
            with col_dl1:
                ics_full = build_ics_for_semaine(
                    session, semaine.id, inclure_obligatoires=True,
                )
                st.download_button(
                    "📅 Export iCal (complet)",
                    data=ics_full,
                    file_name=make_ics_filename(semaine),
                    mime="text/calendar",
                    width="stretch",
                    help="Importe dans Google Calendar / Apple Calendar / "
                         "Outlook. Inclut TOUTES les tâches (sommeil, repas, "
                         "études, sport…) avec rappels 10 min avant les sessions "
                         "d'étude.",
                )
            with col_dl2:
                ics_light = build_ics_for_semaine(
                    session, semaine.id, inclure_obligatoires=False,
                )
                st.download_button(
                    "📅 Export iCal (sans sommeil/repas)",
                    data=ics_light,
                    file_name=f"light_{make_ics_filename(semaine)}",
                    mime="text/calendar",
                    width="stretch",
                    help="Version allégée — uniquement tes tâches libres "
                         "(études, projets, social, etc.). Idéal si ton "
                         "calendrier est déjà chargé.",
                )
            with col_dl3:
                st.caption(
                    "💡 **Astuce mobile** : importe le .ics dans ton calendrier "
                    "natif, tu recevras des notifications de rappel 10 min "
                    "avant chaque session d'étude. Tu peux ré-importer après "
                    "chaque régénération — les events se mettent à jour "
                    "(UID stable), pas de doublon."
                )
        except Exception as exc:  # noqa: BLE001
            st.caption(f"_(Export iCal indisponible : {exc})_")

        # Statistiques globales
        taches = (
            session.query(Tache)
            .filter_by(semaine_id=semaine.id)
            .order_by(Tache.jour, Tache.heure_debut)
            .all()
        )
        stats = _compute_planning_stats(taches)
        _render_planning_stats(stats, cible_hebdo_min)
        _render_repartition_par_type(stats)

        # 📊 Comparaison S-1
        try:
            from utils.helpers import get_or_create_week_for_offset
            _, s1_saisie, _ = get_or_create_week_for_offset(session, offset_weeks=offset_courant - 1)
            s1_taches = session.query(Tache).filter_by(semaine_id=s1_saisie.semaine_id).all() if s1_saisie else []
            if s1_taches:
                s1_stats = _compute_planning_stats(s1_taches)
                delta_etude = stats["etude_min"] - s1_stats["etude_min"]
                delta_sport = stats.get("by_type_min", {}).get("sport", 0) - s1_stats.get("by_type_min", {}).get("sport", 0)
                st.markdown(
                    f"<div style='margin-top:12px; padding:10px 16px; background:#f8fafc; "
                    f"border-radius:8px; font-size:0.9rem;'>"
                    f"📊 <b>vs S-1</b> : "
                    f"Étude {'📈 +' if delta_etude > 0 else '📉 ' if delta_etude < 0 else '='}{abs(delta_etude)}min · "
                    f"Sport {'📈 +' if delta_sport > 0 else '📉 ' if delta_sport < 0 else '='}{abs(delta_sport)}min"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        except Exception:
            pass

        st.markdown("---")

        # Tabs par jour
        today_jour = JOURS_FR[datetime.date.today().weekday()]
        labels = []
        for j in JOURS_FR:
            count = sum(1 for t in taches if t.jour == j)
            label = j.capitalize()
            if j == today_jour:
                label = f"📍 {label}"
            if count:
                label += f" ({count})"
            labels.append(label)

        onglets_jours = st.tabs(labels)
        for idx_jour, onglet in enumerate(onglets_jours):
            with onglet:
                _render_jour_tab(
                    JOURS_FR[idx_jour],
                    [t for t in taches if t.jour == JOURS_FR[idx_jour]],
                )


__all__ = ["render"]
