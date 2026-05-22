import os
import re

files = [
    "app.py",
    "apply_fixes.py",
    "modules/bibliotheque.py",
    "modules/dashboard.py",
    "modules/generation.py",
    "modules/hebdo/ajustements.py",
    "modules/hebdo/etudes.py",
    "modules/import_externe.py",
    "modules/profil.py",
    "modules/suivi.py",
    "modules/travail.py",
    "pages/achievements.py",
    "pages/aide.py",
    "pages/revisions.py",
    "pages/session_etude.py",
    "services/ai_planner.py",
    "services/objectif_service.py",
    "tests/test_lieu_job_et_contraintes_ignorees.py",
    "tests/test_planner.py",
    "services/gamification_service.py",
    "tests/test_revision_supervision.py",
    "tests/test_lissage_auto_leitner.py",
    "services/revision_service.py"
]

gamification_attrs = ["xp", "niveau", "streak_jours", "streak_record", "derniere_activite_xp", "nb_quiz_total", "nb_chapitres_maitrise", "nb_seances_sport_total"]
systeme_attrs = ["gemini_api_key", "gemini_model", "replanning_auto_actif"]
biometrie_attrs = ["heure_lever", "heure_coucher", "heures_sommeil_cible", "chronotype", "pic_concentration", "duree_max_session_min", "pause_entre_sessions_min", "methode_travail", "capacite_weekend", "tolerance_fatigue", "heures_etude_cible_par_semaine", "heures_etude_plafond_par_jour", "besoin_sieste", "duree_sieste_min"]
logistique_attrs = ["temps_transport_min", "trajets_habituels", "nb_repas_par_jour", "duree_repas_min", "duree_prep_repas_min", "contraintes_fixes"]

replacements = {
    r"\bProfil\b": "Utilisateur",
}

for attr in gamification_attrs: replacements[rf"\bprofil\.{attr}\b"] = f"profil.gamification.{attr}"
for attr in systeme_attrs: replacements[rf"\bprofil\.{attr}\b"] = f"profil.systeme.{attr}"
for attr in biometrie_attrs: replacements[rf"\bprofil\.{attr}\b"] = f"profil.biometrie.{attr}"
for attr in logistique_attrs: replacements[rf"\bprofil\.{attr}\b"] = f"profil.logistique.{attr}"

for attr in gamification_attrs: replacements[rf"\bp\.{attr}\b"] = f"p.gamification.{attr}"
for attr in systeme_attrs: replacements[rf"\bp\.{attr}\b"] = f"p.systeme.{attr}"
for attr in biometrie_attrs: replacements[rf"\bp\.{attr}\b"] = f"p.biometrie.{attr}"
for attr in logistique_attrs: replacements[rf"\bp\.{attr}\b"] = f"p.logistique.{attr}"

for file_path in files:
    if not os.path.exists(file_path):
        continue
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    original = content
    for pattern, rep in replacements.items():
        content = re.sub(pattern, rep, content)
        
    content = re.sub(r'getattr\(profil, "(heures_etude_plafond_par_jour)"', r'getattr(profil.biometrie, "\1"', content)
    content = re.sub(r'hasattr\(profil, "(heures_etude_plafond_par_jour)"', r'hasattr(profil.biometrie, "\1"', content)
    
    if original != content:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
