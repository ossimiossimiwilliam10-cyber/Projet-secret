"""Onglet **Études** de la saisie hebdomadaire.

L'étudiant sélectionne ses matières à travailler cette semaine et leurs
chapitres. L'app l'aide activement avec :

- **Indicateurs cognitifs** par matière (révisions Leitner dues,
  nouveaux chapitres, % maîtrise).
- **Pré-sélection automatique** des matières où Leitner a quelque
  chose à faire cette semaine, lors d'une saisie neuve.
- **Estimation live du volume horaire** comparée au quota du
  scheduler (anti-surcharge).
- **Pré-remplissage intelligent** du type de travail selon le niveau
  Leitner des chapitres choisis.
- **Lien direct vers la salle d'étude** pour chaque chapitre coché.

La création de la semaine + le transfert des tâches reportées de la
semaine précédente sont délégués à ``utils.helpers.get_or_create_current_week``.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import pandas as pd
import streamlit as st

from database.db import get_session, session_scope
from database.models import Chapitre, Matiere, Profil, SaisieHebdo
from services.matiere_stats import (
    estimer_charge_minutes,
    format_label_matiere,
    matieres_avec_revisions_dues,
    stats_matiere,
)
from services.scheduler_engine import (
    calculer_cible_hebdo_minutes,
    calculer_quota_etude_minutes,
)
from utils.helpers import get_or_create_current_week

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
    """Devine le type de travail pertinent selon le niveau Leitner des
    chapitres choisis.

    Règle simple : si la majorité des chapitres sont au niveau 0
    (jamais commencés) → « Première lecture ». Sinon → « Révision /
    Compréhension ». L'utilisateur peut toujours override.
    """
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
    except Exception:  # noqa: BLE001
        # Fallback si switch_page indisponible : on lève un drapeau.
        st.session_state.navigate_to_session = True


# ---------------------------------------------------------------------------
# Rendu UI
# ---------------------------------------------------------------------------
def render() -> None:
    st.title("📚 Études de la semaine")
    st.caption("Sélectionne tes matières et tes chapitres. L'IA s'occupera ensuite de la répartition.")

    with get_session() as session:
        semaine, saisie, nb_reportees = get_or_create_current_week(session)

        st.info(
            f"📅 **Semaine {semaine.numero_semaine}** : "
            f"du {semaine.date_debut.strftime('%d/%m')} au {semaine.date_fin.strftime('%d/%m/%Y')}"
        )

        if nb_reportees > 0:
            st.warning(
                f"🔁 **{nb_reportees} tâche(s) ont été reportées** de la semaine précédente. "
                "Va sur l'onglet **Projets & Tâches** pour les voir et les ajuster."
            )

        matieres_selectionnees_db: list[dict[str, Any]] = saisie.matieres_selectionnees or []
        travaux_ponctuels_db: list[dict[str, Any]] = saisie.travaux_ponctuels or []

        toutes_matieres = (
            session.query(Matiere).filter_by(actif=True).order_by(Matiere.nom).all()
        )
        if not toutes_matieres:
            st.warning(
                "Ta bibliothèque est vide. Crée des matières et importe des PDFs "
                "avant de planifier ta semaine."
            )
            return

        # --- Stats par matière (1 query par matière, OK pour <50 matières) ---
        stats_par_id: dict[int, dict[str, Any]] = {
            m.id: stats_matiere(session, m) for m in toutes_matieres
        }

        # --- Pré-sélection automatique (idée C) ---
        # Si la saisie est encore vide cette semaine, on pré-coche les
        # matières qui ont une révision Leitner due aujourd'hui ou en
        # retard. L'étudiant n'a plus à se demander quoi cocher le lundi.
        auto_preselection = False
        if not matieres_selectionnees_db:
            ids_dus = set(matieres_avec_revisions_dues(session))
            if ids_dus:
                auto_preselection = True
                matiere_ids_deja_selectionnees = list(ids_dus)
            else:
                matiere_ids_deja_selectionnees = []
        else:
            matiere_ids_deja_selectionnees = [
                m["matiere_id"] for m in matieres_selectionnees_db
            ]

        matieres_pre_selectionnees = [
            m for m in toutes_matieres if m.id in matiere_ids_deja_selectionnees
        ]

        if auto_preselection:
            st.success(
                f"🤖 J'ai pré-sélectionné **{len(matieres_pre_selectionnees)} matière(s)** "
                "où Leitner a des révisions dues cette semaine. Décoche celles que "
                "tu ne veux pas, ajoute les autres."
            )

        # --- 1. Sélection des matières avec libellés enrichis (idée A) ---
        st.subheader("1. Matières à travailler")
        matieres_choisies = st.multiselect(
            "Quelles matières souhaites-tu aborder cette semaine ?",
            options=toutes_matieres,
            default=matieres_pre_selectionnees,
            format_func=lambda m: format_label_matiere(m, stats_par_id[m.id]),
            help="Légende : 🔥 révisions dues — ⚠️ retard — 📑 nouveaux chapitres — 📊 maîtrise.",
        )

        nouvelles_matieres_selectionnees: list[dict[str, Any]] = []

        # --- 2. Détail par matière choisie ---
        # Dédoublonnage défensif : Streamlit ne devrait pas retourner deux fois
        # la même matière, mais un état corrompu (saisie historique avec doublon)
        # rendrait toutes les clés `ch_{matiere.id}` / `type_{matiere.id}` etc.
        # en collision. Mieux vaut blinder.
        matieres_choisies_uniques: list[Matiere] = []
        _seen_ids: set[int] = set()
        for _m in matieres_choisies:
            if _m.id not in _seen_ids:
                _seen_ids.add(_m.id)
                matieres_choisies_uniques.append(_m)

        if matieres_choisies_uniques:
            for matiere in matieres_choisies_uniques:
                config_existante = next(
                    (m for m in matieres_selectionnees_db
                     if m.get("matiere_id") == matiere.id),
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
                        st.caption(
                            "⚠️ Cette matière n'a pas encore de chapitre. "
                            "Importe un PDF depuis la **Bibliothèque**."
                        )
                        continue

                    chap_by_id = {ch.id: ch for ch in chapitres_matiere}
                    chapitres_options = {
                        ch.id: f"Chap. {ch.numero} - {ch.titre}"
                        for ch in chapitres_matiere
                    }

                    ch_ids_def = [
                        ch_id for ch_id in config_existante.get("chapitre_ids", [])
                        if ch_id in chapitres_options
                    ]

                    chapitres_choisis = st.multiselect(
                        "Chapitres spécifiques (laisse vide pour une révision globale)",
                        options=list(chapitres_options.keys()),
                        default=ch_ids_def,
                        format_func=lambda x: chapitres_options[x],
                        key=f"ch_{matiere.id}",
                    )

                    # Lien direct vers la salle d'étude pour chaque chapitre coché.
                    # On dédoublonne défensivement et on préfixe la clé par
                    # matiere.id pour éviter toute collision Streamlit si un
                    # chapitre est cité deux fois (état corrompu, rerun, etc.).
                    if chapitres_choisis:
                        st.caption("🧠 Ouvrir un chapitre dans la salle d'étude :")
                        ids_uniques = list(dict.fromkeys(chapitres_choisis))
                        cols = st.columns(min(len(ids_uniques), 4) or 1)
                        for idx, ch_id in enumerate(ids_uniques):
                            with cols[idx % len(cols)]:
                                ch = chap_by_id[ch_id]
                                if st.button(
                                    f"🧠 Ch. {ch.numero}",
                                    key=f"goto_chap_m{matiere.id}_c{ch.id}",
                                    help=ch.titre,
                                ):
                                    _open_chapitre_in_session_etude(ch.id)

                    # Suggestion intelligente du type de travail.
                    chapitres_choisis_db = [chap_by_id[i] for i in chapitres_choisis]
                    type_suggere = _type_travail_suggere(chapitres_choisis_db)
                    idx_type = TYPES_TRAVAIL.index(
                        config_existante.get("type_travail") or type_suggere
                    )

                    col1, col2 = st.columns(2)
                    with col1:
                        type_travail = st.selectbox(
                            "Type de travail",
                            options=TYPES_TRAVAIL,
                            index=idx_type,
                            key=f"type_{matiere.id}",
                            help=f"Suggéré selon le niveau Leitner des chapitres choisis : « {type_suggere} ».",
                        )
                    with col2:
                        idx_urg = 0
                        if config_existante.get("urgence") in URGENCES:
                            idx_urg = URGENCES.index(config_existante["urgence"])
                        urgence = st.select_slider(
                            "Urgence", options=URGENCES, value=URGENCES[idx_urg],
                            key=f"urg_{matiere.id}",
                        )

                    nouvelles_matieres_selectionnees.append({
                        "matiere_id": matiere.id,
                        "chapitre_ids": chapitres_choisis,
                        "type_travail": type_travail,
                        "urgence": urgence,
                    })

        # --- Indicateur live de charge horaire ---
        # Compare la somme estimée à l'OBJECTIF HEBDOMADAIRE du profil
        # (pas au plafond × 7 — c'est l'objectif réel que l'étudiant vise).
        charge_min = estimer_charge_minutes(session, nouvelles_matieres_selectionnees)
        if charge_min > 0:
            profil = session.query(Profil).first()
            cible_hebdo_min = calculer_cible_hebdo_minutes(profil)
            pct = charge_min / cible_hebdo_min * 100 if cible_hebdo_min else 0

            heures_str = f"{charge_min / 60:.1f} h"
            cible_str = f"{cible_hebdo_min / 60:.1f} h"
            plafond_h = float(getattr(profil, "heures_etude_plafond_par_jour", 0) or 0)
            plafond_str = f"{plafond_h:.1f} h/jour" if plafond_h > 0 else "non défini"

            if charge_min < cible_hebdo_min * 0.6:
                st.info(
                    f"📊 **Volume estimé : {heures_str}** sur ton objectif de "
                    f"{cible_str} ({pct:.0f} %). Tu peux cocher davantage de "
                    f"chapitres pour atteindre ta cible hebdo."
                )
            elif charge_min <= cible_hebdo_min * 1.05:
                st.success(
                    f"🎯 **Volume estimé : {heures_str}** — bien aligné sur ton "
                    f"objectif hebdo de {cible_str} ({pct:.0f} %). "
                    f"Plafond journalier : {plafond_str}."
                )
            elif charge_min <= cible_hebdo_min * 1.2:
                st.warning(
                    f"⚠️ **Volume estimé : {heures_str}** — légèrement au-dessus "
                    f"de ton objectif hebdo de {cible_str} ({pct:.0f} %). "
                    f"L'IA répartira en respectant ton plafond ({plafond_str})."
                )
            else:
                st.error(
                    f"🚨 **Surcharge : {heures_str}** pour un objectif de "
                    f"{cible_str} ({pct:.0f} %). L'IA déclassera des chapitres "
                    f"dans `taches_ecartees`. Allège ta sélection ou augmente "
                    f"ton objectif dans le **Profil**."
                )

        # --- 3. Travaux ponctuels (devoirs, exposés) ---
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
                "deadline": st.column_config.DatetimeColumn(
                    "Deadline (Date & Heure)", format="DD/MM/YYYY HH:mm",
                ),
                "duree_min": st.column_config.NumberColumn(
                    "Durée estimée (min)", min_value=15, step=15, default=60,
                ),
                "priorite": st.column_config.SelectboxColumn(
                    "Priorité", options=PRIORITES, default="Normale",
                ),
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
                st.error(f"Erreur lors de la sauvegarde : {e}")


# ---------------------------------------------------------------------------
# Helpers privés — check-in du jour + travaux ponctuels
# ---------------------------------------------------------------------------
def _get_checkin_today_dict(session) -> dict[str, Any] | None:
    """Retourne le check-in biomécanique du jour au format dict, ou None
    s'il n'y en a pas eu."""
    from database.models import CheckInQuotidien

    row = (
        session.query(CheckInQuotidien)
        .filter(CheckInQuotidien.date == _dt.date.today())
        .first()
    )
    if row is None:
        return None
    return {
        "fatigue_physique": int(row.fatigue_physique or 0),
        "charge_mentale": int(row.charge_mentale or 0),
        "qualite_sommeil": int(row.qualite_sommeil or 0),
    }


def _build_travaux_df(travaux_ponctuels_db: list[dict[str, Any]]) -> pd.DataFrame:
    """Construit le DataFrame des travaux ponctuels, trié par deadline."""
    df = pd.DataFrame(travaux_ponctuels_db) if travaux_ponctuels_db else pd.DataFrame(
        columns=["libelle", "deadline", "duree_min", "priorite"]
    )
    if "deadline" in df.columns:
        df["deadline"] = pd.to_datetime(df["deadline"], errors="coerce")
        # Tri par deadline ASC (les plus urgents en haut), NaT à la fin.
        df = df.sort_values(by="deadline", ascending=True, na_position="last")
    return df.reset_index(drop=True)


def _render_alertes_deadlines(edited_travaux: pd.DataFrame) -> None:
    """Affiche un badge 🚨 pour chaque travail dont la deadline est < 3 jours
    (et un 📅 « passée » pour les retards)."""
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
            alertes.append(
                f"🚨 **{libelle}** : deadline dans {jours} jour(s) "
                f"({deadline.strftime('%d/%m %H:%M')})"
            )

    if alertes:
        st.warning("**Deadlines proches ou dépassées :**\n\n" + "\n\n".join(alertes))


def _clean_travaux(edited_travaux: pd.DataFrame) -> list[dict[str, Any]]:
    """Normalise les travaux saisis pour stockage JSON."""
    travaux_propres: list[dict[str, Any]] = []
    for _, row in edited_travaux.iterrows():
        if pd.isna(row.get("libelle")) or str(row.get("libelle")).strip() == "":
            continue
        deadline_str = ""
        if pd.notna(row.get("deadline")):
            try:
                deadline_str = row["deadline"].strftime("%Y-%m-%d %H:%M")
            except Exception:  # noqa: BLE001
                pass
        travaux_propres.append({
            "libelle": str(row["libelle"]).strip(),
            "deadline": deadline_str,
            "duree_min": int(row.get("duree_min", 60)),
            "priorite": str(row.get("priorite", "Normale")),
        })
    return travaux_propres
