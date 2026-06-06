#!/usr/bin/env python3
"""apply_fixes.py — Patch automatique du projet Planning étudiant IA.

À lancer depuis la racine de ton projet (le dossier qui contient ``app.py``).

Utilisation :
    python apply_fixes.py              # dry-run (n'écrit RIEN, affiche ce qui changerait)
    python apply_fixes.py --apply      # applique pour de vrai (backup automatique)
    python apply_fixes.py --apply -y   # idem sans confirmation interactive

Correctifs appliqués :
  🔴 Bugs runtime
    #1  Query.get() déprécié (SQLAlchemy 2.x) → session.get(Model, id)
        Dans : modules/generation.py, modules/suivi.py, modules/travail.py,
        modules/import_externe.py, modules/hebdo/etudes.py, .../sport.py,
        .../courses.py, .../dev_perso.py, .../social.py, .../intendance.py,
        .../ajustements.py, .../projets.py.
    #2  modules/suivi.py — protection None sur maitrise_pct (None < 100 ⇒ TypeError).
    #3  Cohérence casse des jours (modules/hebdo/social.py + intendance.py) :
        constantes JOURS passées en minuscules pour matcher ai_planner/suivi/etudes.
        Inclut une normalisation rétro-compatible des données déjà sauvegardées.
  🟡 Fragilités
    #7  requirements.txt — ajout explicite de Pillow (utilisé par import_externe).
    #8  modules/profil.py — retrait du faux modèle "gemini-3-flash-preview".
  🟢 Cosmétique
    #14 app.py — retrait des emojis dupliqués dans les titres de page.
    #15 modules/bibliotheque.py — f-string sans placeholder.

Non appliqué (volontairement) :
    • modules/generation.py:65 (delete destructive) — décision de design, pas un bug.
    • Imports inutiles divers — bruit cosmétique.
    • database/db.py BASE_DIR — fonctionne dans l'usage actuel.

Le script est IDEMPOTENT : tu peux le relancer sans risque.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Catalogue des correctifs (remplacement de chaîne exacte)
# ─────────────────────────────────────────────────────────────────────────────
FIXES_REPLACE: list[dict] = [

    # ━━━ 🔴 BUG #1 — Query.get() déprécié (SQLAlchemy 2.x) ━━━━━━━━━━━━━━━━━
    {
        "file": "modules/generation.py",
        "name": "[#1.1] Query.get() → session.get() (generation.py)",
        "replacements": [(
            "semaine = session.query(Semaine).get(semaine_id)",
            "semaine = session.get(Semaine, semaine_id)",
        )],
    },
    {
        "file": "modules/suivi.py",
        "name": "[#1.2 + #2] Query.get() + None-safety maitrise_pct (suivi.py)",
        "replacements": [
            ("t = s.query(Tache).get(task_id)", "t = s.get(Tache, task_id)"),
            ("ch = s.query(Chapitre).get(ch_id)", "ch = s.get(Chapitre, ch_id)"),
            ("sem = s.query(Semaine).get(semaine_id)", "sem = s.get(Semaine, semaine_id)"),
            (
                "if ch and ch.maitrise_pct < 100:",
                "if ch and (ch.maitrise_pct or 0) < 100:",
            ),
        ],
    },
    {
        "file": "modules/travail.py",
        "name": "[#1.3] Query.get() (travail.py)",
        "replacements": [(
            "job_to_del = delete_session.query(Job).get(j.id)",
            "job_to_del = delete_session.get(Job, j.id)",
        )],
    },
    {
        "file": "modules/import_externe.py",
        "name": "[#1.4] Query.get() (import_externe.py)",
        "replacements": [(
            "profil_to_update = write_session.query(Utilisateur).get(profil.id)",
            "profil_to_update = write_session.get(Utilisateur, profil.id)",
        )],
    },
    {
        "file": "modules/hebdo/etudes.py",
        "name": "[#1.5] Query.get() (etudes.py)",
        "replacements": [(
            "saisie_to_update = write_session.query(SaisieHebdo).get(saisie.id)",
            "saisie_to_update = write_session.get(SaisieHebdo, saisie.id)",
        )],
    },
    {
        "file": "modules/hebdo/sport.py",
        "name": "[#1.6] Query.get() (sport.py)",
        "replacements": [(
            "saisie_to_update = write_session.query(SaisieHebdo).get(saisie.id)",
            "saisie_to_update = write_session.get(SaisieHebdo, saisie.id)",
        )],
    },
    {
        "file": "modules/hebdo/courses.py",
        "name": "[#1.7] Query.get() (courses.py)",
        "replacements": [(
            "saisie_to_update = write_session.query(SaisieHebdo).get(saisie.id)",
            "saisie_to_update = write_session.get(SaisieHebdo, saisie.id)",
        )],
    },
    {
        "file": "modules/hebdo/dev_perso.py",
        "name": "[#1.8] Query.get() (dev_perso.py)",
        "replacements": [(
            "saisie_to_update = write_session.query(SaisieHebdo).get(saisie.id)",
            "saisie_to_update = write_session.get(SaisieHebdo, saisie.id)",
        )],
    },
    {
        "file": "modules/hebdo/ajustements.py",
        "name": "[#1.9] Query.get() (ajustements.py)",
        "replacements": [(
            "saisie_to_update = write_session.query(SaisieHebdo).get(saisie.id)",
            "saisie_to_update = write_session.get(SaisieHebdo, saisie.id)",
        )],
    },
    {
        "file": "modules/hebdo/projets.py",
        "name": "[#1.10] Query.get() (projets.py)",
        "replacements": [(
            "saisie_to_update = write_session.query(SaisieHebdo).get(saisie.id)",
            "saisie_to_update = write_session.get(SaisieHebdo, saisie.id)",
        )],
    },
    {
        "file": "modules/hebdo/intendance.py",
        "name": "[#1.11] Query.get() (intendance.py)",
        "replacements": [(
            "saisie_to_update = write_session.query(SaisieHebdo).get(saisie.id)",
            "saisie_to_update = write_session.get(SaisieHebdo, saisie.id)",
        )],
    },
    {
        "file": "modules/hebdo/social.py",
        "name": "[#1.12] Query.get() (social.py)",
        "replacements": [(
            "saisie_to_update = write_session.query(SaisieHebdo).get(saisie.id)",
            "saisie_to_update = write_session.get(SaisieHebdo, saisie.id)",
        )],
    },
    {
        "file": "services/report_service.py",
        "name": "[#1.13] Query.get() (report_service.py)",
        "replacements": [(
            "sem_origine = session.query(Semaine).get(previous_week_id)",
            "sem_origine = session.get(Semaine, previous_week_id)",
        )],
    },

    # ━━━ 🔴 BUG #3 — Cohérence de casse des jours ━━━━━━━━━━━━━━━━━━━━━━━━━
    {
        "file": "modules/hebdo/social.py",
        "name": "[#3a] JOURS minuscules + normalisation rétro-compat (social.py)",
        "replacements": [
            (
                'JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]',
                'JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]',
            ),
            # Exemples par défaut
            ('"jour": "Dimanche"', '"jour": "dimanche"'),
            ('"jour": "Vendredi"', '"jour": "vendredi"'),
            # Fallback à la sauvegarde
            (
                '"jour": str(row.get("jour", "Lundi"))',
                '"jour": str(row.get("jour", "lundi")).lower()',
            ),
            # Normalisation rétro-compatible des données déjà en base
            (
                "        config_db: dict[str, Any] = saisie.social_config or {}\n",
                "        config_db: dict[str, Any] = saisie.social_config or {}\n"
                "        # Rétrocompat : minuscule les jours capitalisés des anciennes sauvegardes\n"
                "        # (sinon le SelectboxColumn rejette \"Vendredi\" qui ne fait plus partie des options)\n"
                "        for _ev in config_db.get(\"evenements\", []) or []:\n"
                "            if isinstance(_ev, dict) and _ev.get(\"jour\"):\n"
                "                _ev[\"jour\"] = str(_ev[\"jour\"]).lower()\n",
                # Sentinelle pour idempotence
                "# Rétrocompat : minuscule les jours capitalisés des anciennes sauvegardes",
            ),
        ],
    },
    {
        "file": "modules/hebdo/intendance.py",
        "name": "[#3b] JOURS minuscules + normalisation rétro-compat (intendance.py)",
        "replacements": [
            (
                'JOURS = ["Peu importe", "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]',
                'JOURS = ["peu importe", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]',
            ),
            # Ligne d'exemple par défaut
            ('"jour_prefere": "Peu importe"', '"jour_prefere": "peu importe"'),
            # Fallback à la sauvegarde
            (
                '"jour_prefere": str(row.get("jour_prefere", "Peu importe"))',
                '"jour_prefere": str(row.get("jour_prefere", "peu importe")).lower()',
            ),
            # Normalisation rétro-compatible
            (
                "        config_db: list[dict[str, Any]] = saisie.intendance_config or []\n",
                "        config_db: list[dict[str, Any]] = saisie.intendance_config or []\n"
                "        # Rétrocompat : minuscule les jour_prefere capitalisés des anciennes sauvegardes\n"
                "        for _it in config_db:\n"
                "            if isinstance(_it, dict) and _it.get(\"jour_prefere\"):\n"
                "                _it[\"jour_prefere\"] = str(_it[\"jour_prefere\"]).lower()\n",
                # Sentinelle pour idempotence
                "# Rétrocompat : minuscule les jour_prefere capitalisés des anciennes sauvegardes",
            ),
        ],
    },

    # ━━━ 🟡 BUG #8 — modèle LLM inexistant ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    {
        "file": "modules/profil.py",
        "name": "[#8] Retrait du modèle gemini-3-flash-preview (inexistant)",
        "replacements": [(
            'MODELES_GEMINI: list[str] = [\n'
            '    "gemini-2.5-flash",\n'
            '    "gemini-2.5-pro",\n'
            '    "gemini-3-flash-preview",\n'
            '    "gemini-2.0-flash",\n'
            ']',
            'MODELES_GEMINI: list[str] = [\n'
            '    "gemini-2.5-flash",\n'
            '    "gemini-2.5-pro",\n'
            '    "gemini-2.0-flash",\n'
            ']',
        )],
    },

    # ━━━ 🟢 #14 — Double emoji dans les titres de page (app.py) ━━━━━━━━━━━━
    {
        "file": "app.py",
        "name": "[#14] Retrait des emojis dupliqués dans titres+icon (app.py)",
        "replacements": [
            (
                'st.Page("pages/achievements.py",  title="🏆 Achievements", icon="🏆", url_path="achievements"),',
                'st.Page("pages/achievements.py",  title="Achievements", icon="🏆", url_path="achievements"),',
            ),
            (
                'st.Page("pages/objectifs.py", title="🎯 Objectifs", icon="🎯", url_path="objectifs")',
                'st.Page("pages/objectifs.py", title="Objectifs", icon="🎯", url_path="objectifs")',
            ),
        ],
    },

    # ━━━ 🟢 #15 — f-string sans placeholder ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    {
        "file": "modules/bibliotheque.py",
        "name": "[#15] Retrait du f inutile (bibliotheque.py)",
        "replacements": [(
            "st.error(f\"❌ Aucun PDF n'a pu être importé.\")",
            "st.error(\"❌ Aucun PDF n'a pu être importé.\")",
        )],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Cas particulier : requirements.txt (append-line-if-missing)
# ─────────────────────────────────────────────────────────────────────────────
def fix_requirements(root: Path, dry_run: bool, log: list[str]) -> bool:
    """Ajoute Pillow à requirements.txt s'il n'y figure pas déjà."""
    req = root / "requirements.txt"
    if not req.exists():
        log.append("  ⚠️  requirements.txt introuvable, sauté")
        return False
    content = req.read_text(encoding="utf-8")
    if "pillow" in content.lower():
        log.append("  ⏭️  requirements.txt : Pillow déjà présent")
        return False
    new_content = (
        content.rstrip()
        + "\n\n# --- Image (utilisé par modules/import_externe.py via PIL.Image) ---\n"
        + "Pillow>=10.0\n"
    )
    log.append("  ✅ requirements.txt : ajout de Pillow>=10.0")
    if not dry_run:
        req.write_text(new_content, encoding="utf-8")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Moteur d'application
# ─────────────────────────────────────────────────────────────────────────────
def apply_one_fix(root: Path, fix: dict, dry_run: bool, log: list[str]) -> bool:
    """Applique un fix. Retourne True si le fichier a réellement changé.

    Chaque entrée de fix["replacements"] peut être :
      - (old, new)            → remplacement simple
      - (old, new, sentinel)  → si ``sentinel`` est déjà dans le fichier,
                                ce remplacement est sauté (idempotence pour
                                les insertions multi-lignes où ``old`` reste
                                visible dans le résultat).
    """
    fpath = root / fix["file"]
    if not fpath.exists():
        log.append(f"  ⚠️  Fichier introuvable, sauté : {fix['file']}")
        return False

    content = fpath.read_text(encoding="utf-8")
    original = content
    n_applied = 0
    n_notfound = 0
    n_already = 0

    for repl in fix["replacements"]:
        if len(repl) == 3:
            old, new, sentinel = repl
            if sentinel and sentinel in content:
                n_already += 1
                continue
        else:
            old, new = repl

        if old in content:
            content = content.replace(old, new)
            n_applied += 1
        else:
            n_notfound += 1

    if content == original:
        if n_already and not n_notfound:
            log.append(f"  ⏭️  {fix['file']} : déjà appliqué")
        else:
            log.append(f"  ⏭️  {fix['file']} : déjà à jour ou patron(s) absent(s)")
        return False

    msg = f"  ✅ {fix['file']} : {n_applied} remplacement(s)"
    if n_already:
        msg += f" ({n_already} déjà appliqué(s))"
    if n_notfound:
        msg += f" ({n_notfound} patron(s) absent(s))"
    log.append(msg)

    if not dry_run:
        fpath.write_text(content, encoding="utf-8")
    return True


def make_backup(root: Path, files_rel: set[str]) -> Path:
    """Copie chaque fichier listé dans _backup_apply_fixes/<timestamp>/."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = root / "_backup_apply_fixes" / ts
    backup_dir.mkdir(parents=True, exist_ok=True)
    for rel in sorted(files_rel):
        src = root / rel
        if src.exists():
            dst = backup_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return backup_dir


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Patch automatique du projet Planning étudiant IA.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Applique réellement les changements (par défaut : dry-run)",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip la confirmation interactive",
    )
    parser.add_argument(
        "--root", default=".",
        help="Racine du projet (par défaut : dossier courant)",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not (root / "app.py").exists():
        print(f"❌ Pas de app.py dans {root}.")
        print("   Lance ce script depuis la racine du projet (le dossier qui contient app.py).")
        return 1

    print("─" * 64)
    print(f"📁 Racine du projet : {root}")
    print(f"🔧 Mode : {'APPLIQUER (écriture)' if args.apply else 'DRY-RUN (lecture seule)'}")
    print("─" * 64)

    if args.apply and not args.yes:
        rep = input(
            "\n⚠️  Confirme l'application des correctifs ?\n"
            "   Un backup automatique sera créé dans _backup_apply_fixes/.\n"
            "   (oui/non) : "
        ).strip().lower()
        if rep not in ("oui", "o", "yes", "y"):
            print("Abandonné. Aucun fichier modifié.")
            return 0

    # Backup
    backup_dir = None
    if args.apply:
        files_to_backup = {f["file"] for f in FIXES_REPLACE}
        files_to_backup.add("requirements.txt")
        backup_dir = make_backup(root, files_to_backup)
        print(f"\n💾 Backup créé : {backup_dir.relative_to(root)}/")

    # Application
    log: list[str] = []
    n_files_changed = 0

    log.append("")
    log.append("═" * 64)
    log.append("APPLICATION DES CORRECTIFS")
    log.append("═" * 64)

    for fix in FIXES_REPLACE:
        log.append("")
        log.append(f"→ {fix['name']}")
        if apply_one_fix(root, fix, dry_run=not args.apply, log=log):
            n_files_changed += 1

    log.append("")
    log.append("→ [#7] requirements.txt : ajout explicite de Pillow")
    if fix_requirements(root, dry_run=not args.apply, log=log):
        n_files_changed += 1

    print("\n".join(log))

    # Résumé
    print()
    print("═" * 64)
    if args.apply:
        print(f"✅ Terminé : {n_files_changed} fichier(s) modifié(s).")
        if backup_dir:
            print(f"💾 Backup conservé dans : {backup_dir.relative_to(root)}/")
            print(f"   (Pour rollback : cp -r {backup_dir.relative_to(root)}/* .)")
    else:
        print(f"🔍 Dry-run : {n_files_changed} fichier(s) seraient modifié(s).")
        print("   Relance avec --apply pour appliquer.")
    print("═" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
