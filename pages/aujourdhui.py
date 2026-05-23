"""Page **📍 Aujourd'hui** — vue minimaliste centrée sur l'instant.

Le hub quotidien. Au lieu de naviguer entre Suivi, Génération, Études,
Révisions… tu ouvres cette page le matin (ou à n'importe quel moment) et
tu vois en une seule fois :

- Ton check-in du jour (à remplir si pas fait).
- Tes 3-5 prochaines tâches de la journée.
- Un bouton magique **« 🧭 Et maintenant je fais quoi ? »** qui te
  donne UNE action concrète selon l'heure actuelle, tes tâches restantes
  et ton check-in.

Pensé pour être **mobile-friendly** : peu de colonnes, gros boutons,
zéro graphique lourd.
"""

from __future__ import annotations

import datetime
from typing import Any

import streamlit as st

from database import CheckInQuotidien, Semaine, Tache, get_session
from services.revision_service import (
    chapitres_par_jour_futur,
    dette_revision,
)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

_TYPE_ICONS: dict[str, str] = {
    "etude":            "📚",
    "sport":            "🏃",
    "courses":          "🛒",
    "projet":           "🎯",
    "dev_perso":        "🌱",
    "social":           "🍹",
    "intendance":       "🧹",
    "repas":            "🍽️",
    "sommeil":          "😴",
    "transport":        "🚇",
    "cours_presentiel": "🎓",
    "travail":          "💼",
    "meal_prep":        "🍱",
}


def _icon(t: Tache) -> str:
    return _TYPE_ICONS.get((t.type or "").lower(), "•")


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def _today() -> datetime.date:
    return datetime.date.today()


# ---------------------------------------------------------------------------
# Récupération des données
# ---------------------------------------------------------------------------
def _get_semaine_courante(session) -> Semaine | None:
    today = _today()
    iso_year, iso_week, _w = today.isocalendar()
    return session.query(Semaine).filter_by(
        annee=iso_year, numero_semaine=iso_week,
    ).first()


def _get_taches_du_jour(session, semaine: Semaine) -> list[Tache]:
    """Tâches d'aujourd'hui triées par heure de début."""
    jour_fr = JOURS_FR[_today().weekday()]
    return (
        session.query(Tache)
        .filter_by(semaine_id=semaine.id, jour=jour_fr)
        .order_by(Tache.heure_debut)
        .all()
    )


# ---------------------------------------------------------------------------
# « Et maintenant je fais quoi ? » — logique du bouton magique
# ---------------------------------------------------------------------------
def _proposer_action(
    session,
    semaine: Semaine | None,
    taches_du_jour: list[Tache],
    checkin: dict[str, int] | None,
) -> dict[str, Any]:
    """Retourne une suggestion contextuelle.

    Logique :
    1. Si une tâche est EN COURS (heure_debut ≤ maintenant < heure_fin) → la pointer.
    2. Sinon, si une tâche commence dans ≤ 30 min → la pointer.
    3. Sinon, si une révision Leitner est en retard → proposer de la traiter.
    4. Sinon, si une révision Leitner tombe aujourd'hui → la proposer.
    5. Sinon, si check-in fatigué → suggérer une pause.
    6. Sinon, journée bouclée → suggérer de préparer demain.
    """
    now_dt = _now()
    now_t = now_dt.time()

    # 1. Tâche en cours
    for t in taches_du_jour:
        if t.heure_debut and t.heure_fin and t.heure_debut <= now_t < t.heure_fin:
            chap_id = (t.chapitre_ids or [None])[0] if t.chapitre_ids else None
            return {
                "type": "tache_en_cours",
                "titre": f"{_icon(t)} **Tu devrais être sur :** {t.titre}",
                "detail": f"⏰ De {t.heure_debut.strftime('%H:%M')} à {t.heure_fin.strftime('%H:%M')}",
                "tache": t,
                "chapitre_id": chap_id,
            }

    # 2. Prochaine tâche imminente (≤ 30 min)
    for t in taches_du_jour:
        if t.heure_debut and t.statut == "a_faire":
            heure_debut_dt = datetime.datetime.combine(_today(), t.heure_debut)
            delta_min = (heure_debut_dt - now_dt).total_seconds() / 60
            if 0 <= delta_min <= 30:
                chap_id = (t.chapitre_ids or [None])[0] if t.chapitre_ids else None
                return {
                    "type": "prochaine_imminente",
                    "titre": f"{_icon(t)} **Prépare-toi pour :** {t.titre}",
                    "detail": f"⏰ Dans {int(delta_min)} min "
                              f"({t.heure_debut.strftime('%H:%M')})",
                    "tache": t,
                    "chapitre_id": chap_id,
                }

    # 3. Révision Leitner EN RETARD (priorité haute)
    if semaine:
        en_retard = dette_revision(session, today=_today())
        if en_retard:
            chap = en_retard[0]
            # Défense en profondeur : dette_revision filtre déjà les NULL,
            # mais on évite tout AttributeError si la requête change un jour.
            date_str = chap.date_prochaine.strftime("%d/%m") if chap.date_prochaine else "?"
            return {
                "type": "revision_retard",
                "titre": f"⚠️ **Révision en retard :** {chap.titre}",
                "detail": f"Prévue le {date_str} — ouvre-la pour rattraper le retard.",
                "chapitre_id": chap.id,
            }

    # 4. Révision Leitner due aujourd'hui
    if semaine:
        par_jour = chapitres_par_jour_futur(session, days_ahead=1, today=_today())
        aujourdhui = par_jour.get(_today(), [])
        if aujourdhui:
            chap = aujourdhui[0]
            return {
                "type": "revision_aujourdhui",
                "titre": f"🔥 **À réviser aujourd'hui :** {chap.titre}",
                "detail": f"Niveau Leitner actuel : {chap.niveau_actuel or 0} — "
                          f"si tu valides, prochaine révision plus tard.",
                "chapitre_id": chap.id,
            }

    # 5. Check-in fatigué → suggérer pause
    if checkin and (checkin.get("fatigue_physique", 0) > 7 or checkin.get("charge_mentale", 0) > 7):
        return {
            "type": "pause",
            "titre": "🛋️ **Prends une vraie pause.**",
            "detail": "Ton check-in indique que tu es cramé. "
                      "30 min de vrai repos te rendront plus efficace que "
                      "30 min d'étude forcée.",
        }

    # 6. Tâches restantes mais aucune imminente
    restantes = [t for t in taches_du_jour if t.statut == "a_faire"]
    if restantes:
        prochaine = restantes[0]
        if prochaine.heure_debut:
            return {
                "type": "creneau_libre",
                "titre": "☕ **Tu as un peu de marge.**",
                "detail": f"Prochaine tâche : {_icon(prochaine)} {prochaine.titre} "
                          f"à {prochaine.heure_debut.strftime('%H:%M')}. "
                          "Profites-en pour une mini-révision flashcards "
                          "ou un vrai break.",
            }

    # 7. Journée bouclée
    return {
        "type": "fin_journee",
        "titre": "🎉 **Journée bouclée !**",
        "detail": "Toutes les tâches du jour sont validées. "
                  "Vérifie ce qui t'attend demain dans **📍 Études** ou "
                  "prends un vrai repos.",
    }


# ---------------------------------------------------------------------------
# Sections UI
# ---------------------------------------------------------------------------
def _render_bouton_magique(action: dict[str, Any]) -> None:
    """Affiche la suggestion contextuelle dans un gros bandeau coloré."""
    import html as _html

    couleurs_par_type: dict[str, str] = {
        "tache_en_cours":       "#10b981",  # vert
        "prochaine_imminente":  "#f59e0b",  # orange
        "revision_retard":      "#ef4444",  # rouge
        "revision_aujourdhui":  "#f59e0b",  # orange
        "pause":                "#8b5cf6",  # violet
        "creneau_libre":        "#3b82f6",  # bleu
        "fin_journee":          "#10b981",  # vert
    }
    couleur = couleurs_par_type.get(action["type"], "#6b7280")

    # On échappe le titre et le détail car ils contiennent des chaînes
    # construites depuis chap.titre / Tache.titre — données utilisateur
    # ou Gemini, jamais à injecter telles quelles dans du HTML. Le
    # markdown bold (**...**) n'était de toute façon pas rendu dans ce
    # contexte HTML — on remplace par <strong> pour conserver l'intention
    # visuelle d'avant.
    titre_safe = _html.escape(str(action.get("titre", "")))
    detail_safe = _html.escape(str(action.get("detail", "")))
    titre_html = titre_safe.replace("**", "<strong>", 1).replace("**", "</strong>", 1)
    detail_html = detail_safe.replace("**", "<strong>", 1).replace("**", "</strong>", 1)

    st.markdown(
        f"<div style='background:{couleur}15;border-left:6px solid {couleur};"
        f"padding:18px 22px;border-radius:8px;margin:8px 0;'>"
        f"<div style='font-size:1.15rem;color:{couleur};font-weight:700;'>"
        f"{titre_html}</div>"
        f"<div style='color:#374151;margin-top:6px;font-size:0.95rem;'>"
        f"{detail_html}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # Bouton d'action contextuel.
    chap_id = action.get("chapitre_id")
    if chap_id:
        if st.button(
            "🧠 Ouvrir dans la Salle d'étude",
            type="primary", width="stretch",
            key=f"goto_chap_aujourdhui_{chap_id}",
        ):
            st.session_state.target_chapitre_id = chap_id
            try:
                st.switch_page("pages/session_etude.py")
            except Exception:  # noqa: BLE001
                st.session_state.navigate_to_session = True


def _render_taches_du_jour(taches: list[Tache]) -> None:
    if not taches:
        st.info("📭 Aucune tâche planifiée aujourd'hui. Profites-en !")
        return

    st.markdown("### 🗓️ Aujourd'hui")
    statut_meta = {
        "a_faire":       ("⏳", "#3b82f6"),
        "fait":          ("✅", "#10b981"),
        "partiellement": ("⚠️", "#f59e0b"),
        "non_fait":      ("❌", "#ef4444"),
    }
    now_t = _now().time()

    for t in taches:
        emoji_statut, color = statut_meta.get(t.statut, ("•", "#9ca3af"))
        # Mise en évidence si la tâche est en cours.
        is_en_cours = (
            t.heure_debut and t.heure_fin
            and t.heure_debut <= now_t < t.heure_fin
            and t.statut == "a_faire"
        )
        bg = "#fef3c7" if is_en_cours else "#f9fafb"
        bord = "#f59e0b" if is_en_cours else "#e5e7eb"
        marker = " 📍 EN COURS" if is_en_cours else ""
        lock = "🔒 " if t.obligatoire else ""

        st.markdown(
            f"<div style='background:{bg};border:1px solid {bord};"
            f"border-radius:6px;padding:10px 14px;margin:4px 0;"
            f"display:flex;justify-content:space-between;align-items:center;'>"
            f"<div>"
            f"<span style='color:#6b7280;font-size:0.85rem;'>"
            f"{t.heure_debut.strftime('%H:%M')}→{t.heure_fin.strftime('%H:%M')}"
            f"</span>"
            f"<span style='margin:0 8px;'>{_icon(t)}</span>"
            f"<span style='font-weight:500;'>{lock}{t.titre}</span>"
            f"</div>"
            f"<div style='color:{color};font-weight:600;font-size:0.9rem;'>"
            f"{emoji_statut}{marker}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


def _render_mini_kpis(taches: list[Tache]) -> None:
    """Mini bandeau de KPIs du jour."""
    nb = len(taches)
    nb_fait = sum(1 for t in taches if t.statut == "fait")
    nb_a_faire = sum(1 for t in taches if t.statut == "a_faire")
    pct = (nb_fait / nb * 100) if nb else 0

    cols = st.columns(3)
    cols[0].metric("🗓️ Tâches du jour", nb)
    cols[1].metric("✅ Validées", f"{nb_fait}", delta=f"{pct:.0f}%", delta_color="off")
    cols[2].metric("⏳ Restantes", nb_a_faire)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("📍 Aujourd'hui")
    st.caption(
        f"{_now().strftime('%A %d %B %Y, %H:%M')} — "
        "le hub quotidien : ce qui compte maintenant."
    )

    with get_session() as session:
        semaine = _get_semaine_courante(session)

        if semaine is None:
            st.warning(
                "⚠️ Aucune semaine en cours. Ouvre l'onglet **📖 Études** "
                "pour initialiser ta semaine."
            )
            return

        taches_du_jour = _get_taches_du_jour(session, semaine)

        # Mini check-in en haut — invitation à le remplir si pas fait.
        checkin_row = (
            session.query(CheckInQuotidien)
            .filter(CheckInQuotidien.date == _today())
            .first()
        )
        checkin: dict[str, int] | None = None
        if checkin_row:
            checkin = {
                "fatigue_physique": int(checkin_row.fatigue_physique or 0),
                "charge_mentale": int(checkin_row.charge_mentale or 0),
                "qualite_sommeil": int(checkin_row.qualite_sommeil or 0),
            }

        from modules._widgets_checkin import render_checkin_quotidien_widget
        render_checkin_quotidien_widget(
            session, key_prefix="aujourdhui_checkin",
            titre="📊 Check-in du jour (impacte les suggestions ci-dessous)",
        )

        st.divider()

        # === Le bouton magique : « Et maintenant je fais quoi ? » ===
        st.markdown("### 🧭 Et maintenant, je fais quoi ?")
        action = _proposer_action(session, semaine, taches_du_jour, checkin)
        _render_bouton_magique(action)

        st.divider()

        # === Mini KPIs + tâches du jour ===
        _render_mini_kpis(taches_du_jour)
        _render_taches_du_jour(taches_du_jour)

        st.divider()
        st.caption(
            "💡 Pour voir le planning complet de la semaine, va sur "
            "**📊 Suivi quotidien** ou **✨ Génération IA**."
        )


# Exécution Streamlit
render()
