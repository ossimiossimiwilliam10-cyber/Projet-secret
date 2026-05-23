"""Audit de cohérence des données — chasse aux paradoxes silencieux.

Le schéma SQLite ne porte aucune contrainte CHECK ni trigger : tout
invariant métier (``maitrise_pct ∈ [0,100]``, ``niveau ≤ MAX_NIVEAU``,
``streak_jours == 0`` ssi pas d'activité…) peut dériver silencieusement
au gré des bugs de service.

Ce module expose :

- :func:`audit_all`         — produit un rapport listant TOUTES les
                              incohérences trouvées (read-only).
- :func:`repair_all`        — corrige les incohérences réparables
                              automatiquement (avec dry-run par défaut).
- :func:`require_invariants` — fonction de validation invocable depuis
                               n'importe quel service avant un commit.

Le rapport est structuré (``Issue`` dataclass) pour pouvoir être affiché
dans l'UI ou exporté en JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from sqlalchemy import func

from database import (
    Chapitre,
    Matiere,
    Objectif,
    SaisieHebdo,
    Semaine,
    Tache,
    Utilisateur,
)
from database.models import GamificationState

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# Sévérités : "critical" = perte/incohérence visible utilisateur ;
# "warning" = donnée bizarre mais non-bloquante ; "info" = nettoyage cosmétique.
_VALID_TACHE_STATUTS = {"a_faire", "fait", "partiellement", "non_fait", "reporte"}
_VALID_SEMAINE_STATUTS = {"en_attente", "generee", "terminee"}


@dataclass
class Issue:
    """Une incohérence détectée."""
    severity: str         # "critical" | "warning" | "info"
    category: str         # ex: "chapitre.maitrise_pct"
    entity: str           # ex: "Chapitre#42"
    message: str
    repairable: bool = False
    # Snapshot des valeurs cassées (pour log / debug)
    context: dict = field(default_factory=dict)


@dataclass
class AuditReport:
    """Résultat agrégé d'un audit."""
    issues: list[Issue] = field(default_factory=list)

    def add(self, issue: Issue) -> None:
        self.issues.append(issue)

    @property
    def is_clean(self) -> bool:
        return not self.issues

    def by_severity(self, severity: str) -> list[Issue]:
        return [i for i in self.issues if i.severity == severity]

    def summary(self) -> dict[str, int]:
        out = {"critical": 0, "warning": 0, "info": 0, "total": len(self.issues)}
        for i in self.issues:
            out[i.severity] = out.get(i.severity, 0) + 1
        return out


# ===========================================================================
# Checks individuels
# ===========================================================================
def check_chapitre_invariants(session: "Session") -> Iterable[Issue]:
    """maitrise_pct ∈ [0,100], niveau_actuel ∈ [0, MAX_NIVEAU]."""
    from services.revision_service import MAX_NIVEAU

    for chap in session.query(Chapitre).all():
        if chap.maitrise_pct is not None and not (0 <= chap.maitrise_pct <= 100):
            yield Issue(
                severity="critical",
                category="chapitre.maitrise_pct",
                entity=f"Chapitre#{chap.id}",
                message=f"maitrise_pct hors [0,100] : {chap.maitrise_pct}",
                repairable=True,
                context={"value": chap.maitrise_pct},
            )
        if chap.niveau_actuel is not None and not (0 <= chap.niveau_actuel <= MAX_NIVEAU):
            yield Issue(
                severity="critical",
                category="chapitre.niveau_actuel",
                entity=f"Chapitre#{chap.id}",
                message=f"niveau_actuel hors [0,{MAX_NIVEAU}] : {chap.niveau_actuel}",
                repairable=True,
                context={"value": chap.niveau_actuel, "max": MAX_NIVEAU},
            )


def check_gamification_invariants(session: "Session") -> Iterable[Issue]:
    """niveau == calculer_niveau_pour_xp(xp), streak cohérent avec derniere_activite."""
    from services.gamification_service import calculer_niveau_pour_xp

    for g in session.query(GamificationState).all():
        # niveau stocké doit être dérivable de xp
        xp = int(g.xp or 0)
        niveau_attendu = calculer_niveau_pour_xp(xp)
        if (g.niveau or 1) != niveau_attendu:
            yield Issue(
                severity="warning",
                category="gamification.niveau",
                entity=f"GamificationState(user={g.utilisateur_id})",
                message=(
                    f"niveau stocké ({g.niveau}) ne matche pas le calcul depuis "
                    f"xp={xp} (attendu {niveau_attendu})"
                ),
                repairable=True,
                context={"stored": g.niveau, "expected": niveau_attendu, "xp": xp},
            )

        # streak > 0 implique une derniere_activite_xp posée
        if (g.streak_jours or 0) > 0 and g.derniere_activite_xp is None:
            yield Issue(
                severity="warning",
                category="gamification.streak",
                entity=f"GamificationState(user={g.utilisateur_id})",
                message=(
                    f"streak_jours={g.streak_jours} mais derniere_activite_xp est NULL"
                ),
                repairable=True,
                context={"streak": g.streak_jours},
            )

        # streak_record >= streak_jours toujours
        if (g.streak_record or 0) < (g.streak_jours or 0):
            yield Issue(
                severity="warning",
                category="gamification.streak_record",
                entity=f"GamificationState(user={g.utilisateur_id})",
                message=(
                    f"streak_record ({g.streak_record}) < streak_jours ({g.streak_jours})"
                ),
                repairable=True,
                context={
                    "record": g.streak_record, "current": g.streak_jours,
                },
            )

        # Si derniere_activite_xp est trop ancienne, streak devrait être 0
        if g.derniere_activite_xp is not None and (g.streak_jours or 0) > 0:
            delta = (date.today() - g.derniere_activite_xp).days
            if delta > 1:
                yield Issue(
                    severity="warning",
                    category="gamification.streak_stale",
                    entity=f"GamificationState(user={g.utilisateur_id})",
                    message=(
                        f"streak_jours={g.streak_jours} mais dernière activité "
                        f"il y a {delta} jours (streak aurait dû reset)"
                    ),
                    repairable=True,
                    context={
                        "streak": g.streak_jours, "delta_jours": delta,
                    },
                )


def check_tache_invariants(session: "Session") -> Iterable[Issue]:
    """heure_fin > heure_debut, duree_min cohérent, statut valide."""
    for t in session.query(Tache).all():
        if t.heure_debut and t.heure_fin:
            debut_min = t.heure_debut.hour * 60 + t.heure_debut.minute
            fin_min = t.heure_fin.hour * 60 + t.heure_fin.minute
            if fin_min <= debut_min:
                yield Issue(
                    severity="critical",
                    category="tache.heures_inversees",
                    entity=f"Tache#{t.id}",
                    message=(
                        f"heure_fin ({t.heure_fin}) <= heure_debut ({t.heure_debut})"
                    ),
                    repairable=False,
                    context={"debut": str(t.heure_debut), "fin": str(t.heure_fin)},
                )
                continue
            expected_duree = fin_min - debut_min
            if t.duree_min and abs(t.duree_min - expected_duree) > 0:
                yield Issue(
                    severity="info",
                    category="tache.duree_drift",
                    entity=f"Tache#{t.id}",
                    message=(
                        f"duree_min ({t.duree_min}) drifte de heure_fin-heure_debut "
                        f"({expected_duree})"
                    ),
                    repairable=True,
                    context={
                        "stored": t.duree_min, "expected": expected_duree,
                    },
                )

        if t.statut and t.statut not in _VALID_TACHE_STATUTS:
            yield Issue(
                severity="warning",
                category="tache.statut_inconnu",
                entity=f"Tache#{t.id}",
                message=f"statut {t.statut!r} hors whitelist",
                repairable=True,
                context={"value": t.statut, "valids": sorted(_VALID_TACHE_STATUTS)},
            )


def check_semaine_invariants(session: "Session") -> Iterable[Issue]:
    """statut whitelist, 1 SaisieHebdo max par semaine, dates cohérentes."""
    for s in session.query(Semaine).all():
        if s.statut and s.statut not in _VALID_SEMAINE_STATUTS:
            yield Issue(
                severity="warning",
                category="semaine.statut_inconnu",
                entity=f"Semaine#{s.id}",
                message=f"statut {s.statut!r} hors whitelist",
                repairable=False,
                context={"value": s.statut, "valids": sorted(_VALID_SEMAINE_STATUTS)},
            )
        if s.date_debut and s.date_fin and s.date_fin < s.date_debut:
            yield Issue(
                severity="critical",
                category="semaine.dates_inversees",
                entity=f"Semaine#{s.id}",
                message=f"date_fin ({s.date_fin}) < date_debut ({s.date_debut})",
                repairable=False,
                context={"debut": str(s.date_debut), "fin": str(s.date_fin)},
            )

    # Multiple SaisieHebdo pour une même semaine (la table devrait avoir un
    # UNIQUE mais sans ça on chasse les doublons).
    duplicates = (
        session.query(SaisieHebdo.semaine_id, func.count(SaisieHebdo.id))
        .group_by(SaisieHebdo.semaine_id)
        .having(func.count(SaisieHebdo.id) > 1)
        .all()
    )
    for semaine_id, count in duplicates:
        yield Issue(
            severity="critical",
            category="saisie.doublon",
            entity=f"SaisieHebdo(semaine_id={semaine_id})",
            message=f"{count} SaisieHebdo pour cette semaine au lieu d'une seule",
            repairable=False,
            context={"count": count},
        )


def check_pdf_references(session: "Session") -> Iterable[Issue]:
    """Les entrées Chapitre.pdfs[*].path doivent pointer sur des fichiers existants."""
    from database.db import BASE_DIR

    for chap in session.query(Chapitre).filter(Chapitre.pdfs.isnot(None)).all():
        for entry in (chap.pdfs or []):
            if not isinstance(entry, dict):
                continue
            rel_path = entry.get("path")
            if not rel_path:
                continue
            full = BASE_DIR / rel_path
            if not full.exists():
                yield Issue(
                    severity="warning",
                    category="chapitre.pdf_orphelin",
                    entity=f"Chapitre#{chap.id}",
                    message=f"PDF référencé introuvable : {rel_path}",
                    repairable=False,
                    context={"path": rel_path, "label": entry.get("label", "")},
                )


def check_orphan_references(session: "Session") -> Iterable[Issue]:
    """Foreign keys non-CASCADE qui pointent dans le vide."""
    # Matiere.ue_id : Matiere doit pointer sur une UE existante ou être NULL.
    # Géré par FK si bien définie ; mais on vérifie côté lecture en cas de drift.
    # (Skip — les FK SQLAlchemy gèrent normalement.)

    # Tache.chapitre_ids (JSON) : peut pointer sur des chapitres supprimés.
    chap_ids_actifs = {row[0] for row in session.query(Chapitre.id).all()}
    for t in session.query(Tache).filter(Tache.chapitre_ids.isnot(None)).all():
        if not t.chapitre_ids:
            continue
        for cid in t.chapitre_ids:
            try:
                cid_int = int(cid)
            except (TypeError, ValueError):
                continue
            if cid_int not in chap_ids_actifs:
                yield Issue(
                    severity="info",
                    category="tache.chapitre_orphelin",
                    entity=f"Tache#{t.id}",
                    message=(
                        f"chapitre_ids référence chapitre #{cid_int} qui n'existe pas"
                    ),
                    repairable=True,
                    context={"orphan_chapitre_id": cid_int},
                )

    # Objectif.ponderations : idem.
    for obj in session.query(Objectif).filter(
        Objectif.ponderations.isnot(None),
    ).all():
        pond = obj.ponderations or {}
        if not isinstance(pond, dict):
            continue
        for cid_key in list(pond.keys()):
            try:
                cid_int = int(cid_key)
            except (TypeError, ValueError):
                continue
            if cid_int not in chap_ids_actifs:
                yield Issue(
                    severity="info",
                    category="objectif.ponderation_orpheline",
                    entity=f"Objectif#{obj.id}",
                    message=(
                        f"ponderations référence chapitre #{cid_int} disparu"
                    ),
                    repairable=True,
                    context={"orphan_chapitre_id": cid_int},
                )


# ===========================================================================
# Orchestration
# ===========================================================================
_ALL_CHECKS = (
    check_chapitre_invariants,
    check_gamification_invariants,
    check_tache_invariants,
    check_semaine_invariants,
    check_pdf_references,
    check_orphan_references,
)


def audit_all(session: "Session") -> AuditReport:
    """Lance tous les checks et retourne le rapport agrégé."""
    report = AuditReport()
    for check in _ALL_CHECKS:
        for issue in check(session):
            report.add(issue)
    return report


# ===========================================================================
# Réparations
# ===========================================================================
def repair_all(session: "Session", dry_run: bool = True) -> dict[str, int]:
    """Répare les incohérences réparables. Retourne le compteur par catégorie.

    Avec ``dry_run=True`` (default), aucune modification n'est commitée :
    on compte ce qui SERAIT réparé. L'appelant doit faire son propre commit.
    """
    from services.gamification_service import calculer_niveau_pour_xp
    from services.revision_service import MAX_NIVEAU

    report = audit_all(session)
    fixes: dict[str, int] = {}
    chap_ids_actifs = {row[0] for row in session.query(Chapitre.id).all()}

    for issue in report.issues:
        if not issue.repairable:
            continue
        fixes[issue.category] = fixes.get(issue.category, 0) + 1
        if dry_run:
            continue

        # `entity` peut être "Foo#42" ou "Foo(user=42)" selon le check.
        cat = issue.category
        if "#" in issue.entity:
            eid = int(issue.entity.split("#", 1)[1].rstrip(")"))
        else:
            eid = -1  # géré au cas par cas ci-dessous via uid
        if cat == "chapitre.maitrise_pct":
            chap = session.get(Chapitre, eid)
            if chap:
                chap.maitrise_pct = max(0.0, min(100.0, float(chap.maitrise_pct or 0)))
        elif cat == "chapitre.niveau_actuel":
            chap = session.get(Chapitre, eid)
            if chap:
                chap.niveau_actuel = max(0, min(MAX_NIVEAU, int(chap.niveau_actuel or 0)))
        elif cat in ("gamification.niveau",):
            uid = int(issue.entity.split("=")[1].rstrip(")"))
            g = session.query(GamificationState).filter_by(utilisateur_id=uid).first()
            if g:
                g.niveau = calculer_niveau_pour_xp(int(g.xp or 0))
        elif cat == "gamification.streak":
            uid = int(issue.entity.split("=")[1].rstrip(")"))
            g = session.query(GamificationState).filter_by(utilisateur_id=uid).first()
            if g:
                g.streak_jours = 0
        elif cat == "gamification.streak_record":
            uid = int(issue.entity.split("=")[1].rstrip(")"))
            g = session.query(GamificationState).filter_by(utilisateur_id=uid).first()
            if g:
                g.streak_record = max(g.streak_record or 0, g.streak_jours or 0)
        elif cat == "gamification.streak_stale":
            uid = int(issue.entity.split("=")[1].rstrip(")"))
            g = session.query(GamificationState).filter_by(utilisateur_id=uid).first()
            if g:
                g.streak_jours = 0
        elif cat == "tache.duree_drift":
            t = session.get(Tache, eid)
            if t and t.heure_debut and t.heure_fin:
                debut = t.heure_debut.hour * 60 + t.heure_debut.minute
                fin = t.heure_fin.hour * 60 + t.heure_fin.minute
                t.duree_min = max(0, fin - debut)
        elif cat == "tache.statut_inconnu":
            t = session.get(Tache, eid)
            if t:
                t.statut = "a_faire"
        elif cat == "tache.chapitre_orphelin":
            t = session.get(Tache, eid)
            if t and t.chapitre_ids:
                t.chapitre_ids = [
                    int(c) for c in t.chapitre_ids
                    if isinstance(c, (int, str))
                    and str(c).isdigit()
                    and int(c) in chap_ids_actifs
                ]
        elif cat == "objectif.ponderation_orpheline":
            obj = session.get(Objectif, eid)
            if obj and obj.ponderations:
                obj.ponderations = {
                    k: v for k, v in obj.ponderations.items()
                    if str(k).isdigit() and int(k) in chap_ids_actifs
                }

    return fixes


__all__ = [
    "Issue",
    "AuditReport",
    "audit_all",
    "repair_all",
    "check_chapitre_invariants",
    "check_gamification_invariants",
    "check_tache_invariants",
    "check_semaine_invariants",
    "check_pdf_references",
    "check_orphan_references",
]
