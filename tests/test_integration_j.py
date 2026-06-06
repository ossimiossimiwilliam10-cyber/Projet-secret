"""TEST INTEGRATION : Centre d'Etudes + Methode des J + Scheduler"""
import os, sys, tempfile, json
from datetime import date, timedelta, time as dtime
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp_path = tmp.name; tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}"

from database.db import Base, engine, get_session
from database.models import (Utilisateur, BiometrieConfig, LogistiqueConfig, SystemeConfig,
    GamificationState, UE, Matiere, Chapitre, Semaine, Tache, SaisieHebdo)
Base.metadata.create_all(bind=engine)

print("="*55)
print("TEST INTEGRATION — Centre d'Etudes + Methode des J")
print("="*55)

# ---- 1. Creer etudiant + 3 matieres avec chapitres a divers niveaux ----
with get_session() as s:
    u = Utilisateur(nom="Test", prenom="Leitner")
    s.add(u); s.flush()
    s.add(BiometrieConfig(utilisateur_id=u.id, duree_max_session_min=50,
         heures_etude_cible_par_semaine=25, heures_etude_plafond_par_jour=6))
    s.add(LogistiqueConfig(utilisateur_id=u.id))
    s.add(SystemeConfig(utilisateur_id=u.id))
    s.add(GamificationState(utilisateur_id=u.id, xp=0, niveau=1))
    
    ue = UE(nom="Sciences", code="SCI", semestre_code="S3"); s.add(ue); s.flush()
    today = date.today()
    from services.revision_service import INTERVALLES_J
    for mat_name, ch_data in [
        ("Physique", [("Mecanique", 0), ("Optique", 2), ("Thermo", 5), ("Quantique", 0)]),
        ("Maths", [("Algebre", 0), ("Analyse", 3), ("Probas", 1), ("Stats", 0)]),
        ("Chimie", [("Organique", 4), ("Cinetique", 0), ("Solutions", 2), ("Atomistique", 0)]),
    ]:
        mat = Matiere(nom=mat_name, ue_id=ue.id)
        s.add(mat); s.flush()
        for titre, niveau in ch_data:
            ch = Chapitre(matiere_id=mat.id, numero=niveau+1, titre=titre,
                         niveau_actuel=niveau,
                         date_prochaine=today + timedelta(days=INTERVALLES_J[min(niveau, len(INTERVALLES_J)-1)]))
            s.add(ch)
    s.commit()
    
    n_ch = s.query(Chapitre).count()
    niveaux = [c.niveau_actuel for c in s.query(Chapitre).all()]
    print(f"\n1. ETAT INITIAL")
    print(f"   {n_ch} chapitres, niveaux: {niveaux}")
    print(f"   Niveaux 0: {niveaux.count(0)} (nouveaux) | Niveau >=1: {sum(1 for n in niveaux if n>=1)} (a reviser)")

# ---- 2. Semaine + scheduler ----
with get_session() as s:
    today = date.today()
    iso = today.isocalendar()
    debut = today - timedelta(days=today.weekday())
    sem = Semaine(annee=iso[0], numero_semaine=iso[1], date_debut=debut, date_fin=debut+timedelta(days=6))
    s.add(sem); s.flush()
    
    saisie_data = {"ajustements": {}, "courses": {}, "etudes": {"matieres_prioritaires": ["Physique", "Maths", "Chimie"]}}
    saisie = SaisieHebdo(semaine_id=sem.id)
    s.add(saisie)
    s.commit()
    
    from services.scheduler_engine import (repartir_nouveaux_chapitres, lisser_revisions_leitner,
        calculer_quota_etude_minutes, JOURS)
    from services.ai_planner import _get_chapitres_dus_pour_semaine
    
    chapitres_dus = _get_chapitres_dus_pour_semaine(s, sem)
    utilisateur = s.query(Utilisateur).first(); quota = calculer_quota_etude_minutes(utilisateur, None)
    
    chaps_db = s.query(Chapitre).all()
    matieres_sel = [{"chapitre_ids": [ch.id for ch in chaps_db], "matiere_nom": "Toutes"}]
    repartition = repartir_nouveaux_chapitres(s, matieres_sel, sem)
    charges_init = {j: sum(ch["temps_estime_min"] for ch in repartition[j]) for j in JOURS}
    repart_rev, charges_fin = lisser_revisions_leitner(chapitres_dus, sem, quota, charges_init)
    
    print(f"\n2. PLANNING GENERE")
    print(f"   Chapitres dus (revision): {len(chapitres_dus)}")
    print(f"   Quota/jour: {quota} min")
    for jour in JOURS:
        n_nouveaux = len(repartition.get(jour, []))
        n_revisions = len(repart_rev.get(jour, []))
        charge = charges_fin.get(jour, 0)
        bar = "#" * min(20, charge//10)
        print(f"   {jour:<9} | nouveaux:{n_nouveaux} | revisions:{n_revisions} | charge:{charge:>3}min {bar}")

# ---- 3. Simuler session d'etude (quiz) ----
with get_session() as s:
    from services.revision_service import appliquer_resultat_quiz
    chapitres = s.query(Chapitre).order_by(Chapitre.id).limit(6).all()
    print(f"\n3. SESSION D'ETUDE (6 quiz)")
    for ch in chapitres:
        niveau_avant = ch.niveau_actuel
        score = {0: 100, 1: 75, 2: 25, 3: 100, 4: 50, 5: 100}[ch.id % 6]
        result = appliquer_resultat_quiz(s, ch.id, float(score), "qcm")
        delta = result["niveau_apres"] - result["niveau_avant"]
        fleche = "/\\" if delta > 0 else ("\\/" if delta < 0 else "->")
        print(f"   {ch.titre:<15} score:{score:>3}% | nv {niveau_avant}->{result['niveau_apres']} {fleche} | prochaine: {result['date_prochaine']}")
    s.commit()

# ---- 4. Verifier nouvel etat ----
with get_session() as s:
    chapitres_post = s.query(Chapitre).all()
    niveaux_post = [c.niveau_actuel for c in chapitres_post]
    dist = Counter(niveaux_post)
    print(f"\n4. DISTRIBUTION FINALE")
    for niv in sorted(dist):
        bar = "#" * dist[niv]
        print(f"   Niveau {niv}: {dist[niv]} chapitres {bar}")

print(f"\n{'='*55}")
print("TEST D'INTEGRATION REUSSI")
os.unlink(tmp_path)
