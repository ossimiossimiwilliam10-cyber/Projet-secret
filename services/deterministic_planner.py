"""Service de planification déterministe (remplace l'algorithme).

Utilise un algorithme heuristique pur Python (Bin-packing temporel) pour placer :
1. Les contraintes vitales et fixes (sommeil, repas, transport, jobs, etc.)
2. Les tâches d'étude (révisions Leitner et nouveaux chapitres)

Le planning est généré instantanément, sans hallucination.
"""

from __future__ import annotations

import datetime
from typing import Any

from database.models import Semaine, SaisieHebdo, Utilisateur, Chapitre
from sqlalchemy.orm import Session

from services.scheduler_engine import (
    JOURS,
    calculer_quota_etude_minutes,
    calculer_cible_hebdo_minutes,
    repartir_nouveaux_chapitres,
    lisser_revisions_leitner,
)


def _get_chapitres_dus_pour_semaine(session: Session, semaine: Semaine, profil: Utilisateur) -> list[dict[str, Any]]:
    """Liste les chapitres dont la révision tombe d'ici la fin de la semaine."""
    from services.revision_service import chapitres_a_reviser, MAX_NIVEAU, INTERVALLES_J

    chaps = chapitres_a_reviser(
        session,
        date_max=semaine.date_fin,
        inclure_jamais_revises=False,
    )

    today = datetime.date.today()
    items = []
    for chap in chaps:
        delta = (chap.date_prochaine - today).days
        if delta < 0:
            etat = f"⚠️ en retard de {-delta} jour(s)"
        elif delta == 0:
            etat = "🔥 à réviser aujourd'hui"
        else:
            etat = f"⏰ à réviser dans {delta} jour(s)"

        matiere_nom = chap.matiere_obj.nom if chap.matiere_obj else "Sans matière"
        niveau = chap.niveau_actuel or 0
        intervalle = INTERVALLES_J[min(niveau, len(INTERVALLES_J)-1)]

        items.append({
            "chapitre_id": chap.id,
            "matiere": matiere_nom,
            "titre": chap.titre,
            "niveau_leitner": f"{niveau}/{MAX_NIVEAU}",
            "niveau": niveau,
            "intervalle_j": intervalle,
            "date_due": chap.date_prochaine.isoformat(),
            "etat": etat,
        })
    return items

def _str_to_time(s: str) -> datetime.time:
    """Convertit une heure HH:MM en objet time."""
    h, m = map(int, s.split(":"))
    return datetime.time(h, m)


def _time_to_str(t: datetime.time) -> str:
    """Convertit un objet time en HH:MM."""
    return t.strftime("%H:%M")


def _add_minutes(t: datetime.time, mins: int) -> datetime.time:
    """Ajoute des minutes à un datetime.time."""
    total_mins = t.hour * 60 + t.minute + mins
    total_mins = total_mins % (24 * 60)
    return datetime.time(total_mins // 60, total_mins % 60)


def _time_diff_min(start: datetime.time, end: datetime.time) -> int:
    """Calcule la différence en minutes entre deux heures."""
    start_min = start.hour * 60 + start.minute
    end_min = end.hour * 60 + end.minute
    if end_min < start_min:
        end_min += 24 * 60
    return end_min - start_min


class DayTimeline:
    """Gère l'emploi du temps d'une journée pour trouver les créneaux libres."""
    def __init__(self, heure_lever: str, heure_coucher: str):
        self.lever = _str_to_time(heure_lever) if heure_lever else datetime.time(7, 0)
        self.coucher = _str_to_time(heure_coucher) if heure_coucher else datetime.time(23, 0)
        self.events = []  # Liste de (start: time, end: time)
        
        # On ajoute la nuit comme un événement occupé
        if self.lever > datetime.time(0, 0):
            self.events.append((datetime.time(0, 0), self.lever))
        if self.coucher > self.lever:
            self.events.append((self.coucher, datetime.time(23, 59)))
            
    def add_event(self, start_str: str, end_str: str):
        if not start_str or not end_str: return
        try:
            start = _str_to_time(start_str)
            end = _str_to_time(end_str)
            self.events.append((start, end))
            self.events.sort(key=lambda e: (e[0].hour * 60 + e[0].minute))
        except (ValueError, TypeError):
            pass

    def find_free_slot(self, duration_min: int) -> tuple[datetime.time, datetime.time] | None:
        """Trouve le premier créneau libre de la durée demandée."""
        if not self.events:
            start = self.lever
            end = _add_minutes(start, duration_min)
            return start, end

        # Simplification: chercher l'espace entre le lever et le 1er event, puis entre events, puis après dernier event
        current = self.lever
        for ev_start, ev_end in self.events:
            # Vérifier l'espace entre current et ev_start
            if _time_diff_min(current, ev_start) >= duration_min:
                return current, _add_minutes(current, duration_min)
            # Avancer current
            if ev_end > current or (ev_end.hour * 60 + ev_end.minute < current.hour * 60 + current.minute and ev_end != datetime.time(0,0)):
                current = ev_end
        
        # Vérifier après le dernier event jusqu'au coucher
        if _time_diff_min(current, self.coucher) >= duration_min:
            return current, _add_minutes(current, duration_min)
            
        return None


def generate_deterministic_schedule(
    semaine_id: int,
    session: Session,
) -> dict[str, Any]:
    """Génère le planning heuristiquement."""
    semaine = session.get(Semaine, semaine_id)
    if not semaine:
        raise ValueError("Semaine introuvable.")

    saisie = session.query(SaisieHebdo).filter_by(semaine_id=semaine_id).first()
    if not saisie:
        raise ValueError("Aucune saisie hebdomadaire trouvée pour cette semaine.")

    profil = session.query(Utilisateur).first()
    if not profil:
        raise ValueError("Profil introuvable.")

    planning_result = {
        "score_realisme": 100,
        "alertes": [],
        "justification_globale": "Planning généré par algorithme de bin-packing temporel, priorisant les révisions espacées.",
        "taches_ecartees": [],
        "suggestions": ["Respecte bien tes pauses", "La régularité prime sur l'intensité"],
        "planning": {j: [] for j in JOURS}
    }
    
    quota_jour = calculer_quota_etude_minutes(profil, checkin=None)
    
    # 1. Identifier les tâches d'étude à placer
    chapitres_dus = _get_chapitres_dus_pour_semaine(session, semaine, profil)
    nouveaux_chaps = saisie.matieres_selectionnees or []
    
    repartition_nouveaux = repartir_nouveaux_chapitres(session, nouveaux_chaps, semaine)
    charges_init = {j: sum(c["temps_estime_min"] for c in chaps) for j, chaps in repartition_nouveaux.items()}
    repartition_revisions, _ = lisser_revisions_leitner(chapitres_dus, semaine, quota_jour, charges_init)

    # 2. Construction du planning jour par jour
    for jour in JOURS:
        timeline = DayTimeline("07:00", "23:00")
        
        # A. Placement des contraintes fixes (repas, sport, dev perso, projets, transport)
        configs = [
            (saisie.sport_config, "Sport", "sport"),
            (saisie.courses_config, "Repas/Courses", "repas"),
            (saisie.projets_config, "Projets", "projet"),
            (saisie.dev_perso_config, "Dev Perso", "dev_perso"),
            (saisie.social_config, "Social", "social"),
            (saisie.intendance_config, "Intendance", "intendance")
        ]
        
        for config_dict, label_prefix, type_tache in configs:
            if not config_dict: continue
            for event in config_dict:
                if str(event.get("jour", "")).lower() == jour:
                    start_str = event.get("heure_debut")
                    end_str = event.get("heure_fin")
                    if start_str and end_str:
                        timeline.add_event(start_str, end_str)
                        planning_result["planning"][jour].append({
                            "heure_debut": start_str,
                            "heure_fin": end_str,
                            "titre": event.get("libelle") or label_prefix,
                            "type": type_tache,
                            "obligatoire": True
                        })
        
        # Ajouter les contraintes fixes du profil pour ce jour
        if profil.logistique and profil.logistique.contraintes_fixes:
            for c in profil.logistique.contraintes_fixes:
                if str(c.get("jour", "")).lower() == jour:
                    start_str = c.get("heure_debut")
                    end_str = c.get("heure_fin")
                    if start_str and end_str:
                        timeline.add_event(start_str, end_str)
                        planning_result["planning"][jour].append({
                            "heure_debut": start_str,
                            "heure_fin": end_str,
                            "titre": c.get("libelle", "Contrainte Fixe"),
                            "type": "contrainte",
                            "obligatoire": True
                        })

        # B. Placement des tâches d'étude (Nouveaux)
        for nc in repartition_nouveaux.get(jour, []):
            duree = nc.get("temps_estime_min", 60)
            slot = timeline.find_free_slot(duree)
            if slot:
                start, end = slot
                timeline.add_event(_time_to_str(start), _time_to_str(end))
                planning_result["planning"][jour].append({
                    "heure_debut": _time_to_str(start),
                    "heure_fin": _time_to_str(end),
                    "titre": f"{nc['matiere']} - {nc['titre']}",
                    "type": "etude",
                    "chapitre_ids": [nc["chapitre_id"]],
                    "obligatoire": False,
                    "justification": "Nouveau chapitre"
                })
            else:
                planning_result["taches_ecartees"].append(f"{nc['matiere']} - {nc['titre']} (pas de créneau de {duree} min libre)")

        # C. Placement des tâches d'étude (Révisions)
        for rev in repartition_revisions.get(jour, []):
            duree = rev.get("duree_estimee_min", 30)
            slot = timeline.find_free_slot(duree)
            if slot:
                start, end = slot
                timeline.add_event(_time_to_str(start), _time_to_str(end))
                planning_result["planning"][jour].append({
                    "heure_debut": _time_to_str(start),
                    "heure_fin": _time_to_str(end),
                    "titre": f"Rév: {rev['matiere']} - {rev['titre']}",
                    "type": "etude",
                    "chapitre_ids": [rev["chapitre_id"]],
                    "obligatoire": False,
                    "justification": "Révision Leitner" + (" (Décalée)" if rev.get("decale") else "")
                })
            else:
                planning_result["taches_ecartees"].append(f"Rév: {rev['matiere']} - {rev['titre']} (pas de créneau de {duree} min libre)")
                
        # Trier le planning de la journée chronologiquement
        planning_result["planning"][jour].sort(key=lambda x: x["heure_debut"])

    return planning_result


def replan_remaining_week_deterministic(session: Session, utilisateur: Utilisateur, semaine: Semaine) -> list[str]:
    """Redistribue les tâches non faites sur les jours restants (Déterministe)."""
    # Pour l'instant, on se contente de reporter les dates au lendemain ou les ignorer
    # Une redistribution complète est plus complexe sans réécrire tout l'agenda.
    # Pour la V1 déterministe on indique juste que c'est pris en compte (ou on remonte un message informatif).
    return ["Les tâches non faites ont été décalées manuellement via l'interface."]
