"""Page **🏆 Achievements** — galerie complète des badges.

Affiche tous les achievements catalogués (de ``services/gamification_service.py``),
en distinguant ceux débloqués (en couleur, avec date) de ceux verrouillés
(en gris, avec ??? pour les rares/légendaires pour préserver le mystère).
"""

from __future__ import annotations

import streamlit as st

from database import Achievement, Utilisateur, get_session
from services.gamification_service import (
    ACHIEVEMENTS,
    NIVEAU_MAX,
    RARETE_COULEURS,
    progression_niveau,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_unlocked_codes() -> dict[str, str]:
    """Retourne {code: date_obtention} pour tous les achievements débloqués."""
    with get_session() as session:
        rows = session.query(Achievement).all()
        return {
            r.code: r.date_obtention.strftime("%d/%m/%Y") if r.date_obtention else ""
            for r in rows
        }


def _get_profil_snapshot() -> dict:
    """Snapshot du profil pour les KPIs gamification."""
    with get_session() as session:
        p = session.query(Utilisateur).first()
        if p is None:
            return {
                "xp": 0, "niveau": 1, "streak": 0, "streak_record": 0,
                "nb_quiz": 0, "nb_maitrise": 0,
            }
        return {
            "xp": p.gamification.xp or 0,
            "niveau": p.gamification.niveau or 1,
            "streak": p.gamification.streak_jours or 0,
            "streak_record": p.gamification.streak_record or 0,
            "nb_quiz": p.gamification.nb_quiz_total or 0,
            "nb_maitrise": p.gamification.nb_chapitres_maitrise or 0,
        }


# ---------------------------------------------------------------------------
# Rendu de la page
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("🏆 Achievements")
    st.caption(
        "Tous les badges que tu peux débloquer en utilisant l'app : "
        "quiz réussis, chapitres maîtrisés, séries de jours d'affilée…"
    )

    profil = _get_profil_snapshot()
    unlocked = _get_unlocked_codes()

    # === Bandeau récap stats ===
    _render_stats_panel(profil, unlocked)

    st.divider()

    # === Galerie ===
    st.subheader(f"Galerie ({len(unlocked)} / {len(ACHIEVEMENTS)} débloqués)")

    # Grouper par rareté
    raretes_ordre = ["commun", "peu_commun", "rare", "epique", "legendaire"]
    par_rarete: dict[str, list] = {r: [] for r in raretes_ordre}
    for ach in ACHIEVEMENTS:
        par_rarete.setdefault(ach.rarete, []).append(ach)

    rarete_labels = {
        "commun": "🥉 Communs",
        "peu_commun": "🥈 Peu communs",
        "rare": "🥇 Rares",
        "epique": "💎 Épiques",
        "legendaire": "👑 Légendaires",
    }

    for rarete in raretes_ordre:
        achs = par_rarete[rarete]
        if not achs:
            continue
        st.markdown(f"### {rarete_labels.get(rarete, rarete)}")
        cols_par_ligne = 3
        for i in range(0, len(achs), cols_par_ligne):
            cols = st.columns(cols_par_ligne)
            for j, ach in enumerate(achs[i:i + cols_par_ligne]):
                with cols[j]:
                    _render_achievement_card(ach, unlocked)


def _render_stats_panel(profil: dict, unlocked: dict[str, str]) -> None:
    """Bandeau du haut avec les statistiques gamification."""
    prog = progression_niveau(profil["xp"])

    cols = st.columns(4)
    with cols[0]:
        st.markdown(
            f"<div style='text-align:center;padding:12px;background:#1e293b;"
            f"color:white;border-radius:8px;'>"
            f"<div style='font-size:0.75rem;opacity:0.7;'>NIVEAU</div>"
            f"<div style='font-size:2.5rem;font-weight:800;line-height:1;'>{profil['niveau']}</div>"
            f"<div style='font-size:0.7rem;opacity:0.5;'>/{NIVEAU_MAX}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with cols[1]:
        if prog["max_atteint"]:
            xp_str = f"{profil['xp']:,}"
            sub = "Niveau MAX !"
        else:
            xp_str = f"{profil['xp']:,}"
            sub = f"{prog['xp_palier_taille'] - prog['xp_dans_palier']:,} XP vers niv {profil['niveau']+1}"
        st.markdown(
            f"<div style='text-align:center;padding:12px;"
            f"background:linear-gradient(135deg,#fbbf24,#f59e0b);"
            f"color:white;border-radius:8px;'>"
            f"<div style='font-size:0.75rem;opacity:0.85;'>⭐ XP TOTAL</div>"
            f"<div style='font-size:2.2rem;font-weight:800;line-height:1;'>{xp_str}</div>"
            f"<div style='font-size:0.7rem;opacity:0.8;'>{sub}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with cols[2]:
        st.markdown(
            f"<div style='text-align:center;padding:12px;background:#ef4444;"
            f"color:white;border-radius:8px;'>"
            f"<div style='font-size:0.75rem;opacity:0.85;'>🔥 STREAK</div>"
            f"<div style='font-size:2.2rem;font-weight:800;line-height:1;'>{profil['streak']}j</div>"
            f"<div style='font-size:0.7rem;opacity:0.8;'>Record : {profil['streak_record']}j</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with cols[3]:
        st.markdown(
            f"<div style='text-align:center;padding:12px;background:#3b82f6;"
            f"color:white;border-radius:8px;'>"
            f"<div style='font-size:0.75rem;opacity:0.85;'>🏆 BADGES</div>"
            f"<div style='font-size:2.2rem;font-weight:800;line-height:1;'>{len(unlocked)}</div>"
            f"<div style='font-size:0.7rem;opacity:0.8;'>/ {len(ACHIEVEMENTS)} disponibles</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


def _render_achievement_card(ach, unlocked: dict[str, str]) -> None:
    """Affiche une carte pour un achievement (débloqué ou verrouillé)."""
    is_unlocked = ach.code in unlocked
    couleur = RARETE_COULEURS.get(ach.rarete, "#9ca3af")

    if is_unlocked:
        # Débloqué : pleine couleur + date
        date_str = unlocked[ach.code]
        st.markdown(
            f"<div style='background:{couleur}15;border:2px solid {couleur};"
            f"border-radius:10px;padding:16px;height:160px;display:flex;"
            f"flex-direction:column;justify-content:space-between;'>"
            f"<div>"
            f"<div style='font-size:2.5rem;text-align:center;line-height:1;'>{ach.icone}</div>"
            f"<div style='text-align:center;font-weight:700;color:{couleur};"
            f"font-size:1rem;margin-top:6px;'>{ach.nom}</div>"
            f"<div style='text-align:center;font-size:0.78rem;color:#4b5563;"
            f"margin-top:4px;line-height:1.3;'>{ach.description}</div>"
            f"</div>"
            f"<div style='text-align:center;font-size:0.7rem;color:#6b7280;"
            f"margin-top:6px;'>Débloqué le {date_str}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    else:
        # Verrouillé : grisé, et description masquée pour rares/légendaires
        cache_description = ach.rarete in ("rare", "epique", "legendaire")
        nom_display = ach.nom if not cache_description else "???"
        desc_display = ach.description if not cache_description else "Continue à progresser pour découvrir ce badge…"
        icone_display = "🔒" if cache_description else f"<span style='opacity:0.3;'>{ach.icone}</span>"

        st.markdown(
            f"<div style='background:#f3f4f6;border:2px dashed #d1d5db;"
            f"border-radius:10px;padding:16px;height:160px;display:flex;"
            f"flex-direction:column;justify-content:space-between;opacity:0.7;'>"
            f"<div>"
            f"<div style='font-size:2.5rem;text-align:center;line-height:1;filter:grayscale(80%);'>{icone_display}</div>"
            f"<div style='text-align:center;font-weight:600;color:#6b7280;"
            f"font-size:1rem;margin-top:6px;'>{nom_display}</div>"
            f"<div style='text-align:center;font-size:0.78rem;color:#9ca3af;"
            f"margin-top:4px;line-height:1.3;font-style:italic;'>{desc_display}</div>"
            f"</div>"
            f"<div style='text-align:center;font-size:0.65rem;color:#9ca3af;"
            f"margin-top:6px;text-transform:uppercase;letter-spacing:0.05em;'>{ach.rarete}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


# Exécution Streamlit
render()
