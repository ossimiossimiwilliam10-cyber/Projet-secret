"""Outil CLI pour auditer la cohérence de la DB.

Usage :
    python tools/audit_db.py              # rapport seul (read-only)
    python tools/audit_db.py --repair     # rapport + dry-run réparation
    python tools/audit_db.py --apply      # rapport + APPLIQUE les réparations
    python tools/audit_db.py --json       # sortie JSON pour scripts

Lancé périodiquement (manuellement ou via cron) pour détecter les
drifts silencieux : maitrise > 100%, streak qui ne reflète pas la
dernière activité, chapitre_ids référençant des chapitres supprimés,
PDFs orphelins, etc.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ajoute la racine du repo au PYTHONPATH pour pouvoir exécuter le script
# depuis n'importe quel répertoire de travail.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db import init_db, migrate_schema, session_scope  # noqa: E402
from services.data_integrity import audit_all, repair_all  # noqa: E402


_SEVERITY_ICONS = {
    "critical": "🚨",
    "warning":  "⚠️ ",
    "info":     "ℹ️ ",
}


def _print_report(report, *, fmt: str = "text") -> None:
    if fmt == "json":
        out = {
            "summary": report.summary(),
            "issues": [
                {
                    "severity": i.severity,
                    "category": i.category,
                    "entity": i.entity,
                    "message": i.message,
                    "repairable": i.repairable,
                    "context": i.context,
                }
                for i in report.issues
            ],
        }
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        return

    if report.is_clean:
        print("✅ DB cohérente — aucune issue détectée.")
        return

    summary = report.summary()
    print(
        f"📊 Total : {summary['total']} issue(s) "
        f"({summary['critical']} critical, {summary['warning']} warning, "
        f"{summary['info']} info)\n"
    )

    by_cat: dict[str, list] = {}
    for issue in report.issues:
        by_cat.setdefault(issue.category, []).append(issue)

    for cat, issues in sorted(by_cat.items()):
        icon = _SEVERITY_ICONS.get(issues[0].severity, "?")
        repairable_n = sum(1 for i in issues if i.repairable)
        suffix = f" ({repairable_n} réparable)" if repairable_n else ""
        print(f"{icon} [{cat}] × {len(issues)}{suffix}")
        for i in issues[:5]:
            print(f"    {i.entity} — {i.message}")
        if len(issues) > 5:
            print(f"    … et {len(issues) - 5} autre(s)")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--repair", action="store_true",
        help="Affiche ce qui SERAIT réparé (dry-run, ne touche pas la DB)",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="APPLIQUE les réparations (modifie la DB — irréversible)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Sortie JSON pour usage scripté",
    )
    args = parser.parse_args()

    fmt = "json" if args.json else "text"

    # Garantit que les tables existent (no-op si DB déjà initialisée).
    init_db()
    migrate_schema(verbose=False)

    with session_scope() as session:
        report = audit_all(session)
        _print_report(report, fmt=fmt)

        if args.repair or args.apply:
            fixes = repair_all(session, dry_run=not args.apply)
            if fmt == "json":
                print(json.dumps({"fixes": fixes}, indent=2, ensure_ascii=False))
            else:
                label = "APPLIQUÉ" if args.apply else "DRY-RUN"
                print(f"\n🛠️  Réparations [{label}] :")
                if not fixes:
                    print("    (rien à réparer)")
                for cat, n in sorted(fixes.items()):
                    print(f"    • {cat} × {n}")
                if args.apply:
                    print("\n✅ Modifications commitées.")

    # Exit code utile pour les pipelines : 1 si critical detected.
    return 1 if report.by_severity("critical") else 0


if __name__ == "__main__":
    sys.exit(main())
