"""
🏆 TEST CORPS ENTIER — Exocerveau (v2)
Lancer : .venv/Scripts/python.exe tests/test_corps_entier.py
"""
import os, sys, tempfile, time, random, json
from datetime import date, datetime, timedelta, time as dtime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPORT = []
def log(icon, msg):
    REPORT.append(f"{icon} {msg}")
    print(f"  {icon} {msg}")

print("\n🧪 TEST CORPS ENTIER — Exocerveau")
print("=" * 55)

tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp_path = tmp_db.name; tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}"
log("🔧", f"DB temporaire: ok")

from database.db import init_db, get_session, Base, engine
from database.models import (
    Utilisateur, BiometrieConfig, LogistiqueConfig, SystemeConfig,
    GamificationState, UE, Matiere, Chapitre, Semaine, Tache, SaisieHebdo,
)
Base.metadata.create_all(bind=engine)
log("✅", "DB OK")

# 1. ETUDIANT
with get_session() as s:
    u = Utilisateur(nom="Dupont", prenom="Jean")
    s.add(u); s.flush()
    s.add(BiometrieConfig(utilisateur_id=u.id, duree_max_session_min=50, heures_etude_cible_par_semaine=25))
    s.add(LogistiqueConfig(utilisateur_id=u.id))
    s.add(SystemeConfig(utilisateur_id=u.id, deepseek_api_key="sk-test"))
    s.add(GamificationState(utilisateur_id=u.id, xp=0, niveau=1))
    s.commit()
    UID = u.id
    log("✅", f"Jean Dupont (ID={UID}) 25h/sem")

# 2. MATIERES + CHAPITRES
matieres = [("Physique",["Mecanique","Thermo","Optique"]),("Maths",["Algebre","Analyse","Probas"]),
            ("Chimie",["Organique","Cinetique"]),("Biologie",["Cellulaire","Genetique"]),
            ("Info",["Python","Algo","SQL"]),("Anglais",["Grammaire","Oral"]),
            ("Anatomie",["Osteo","Myologie"]),("Pharmaco",["Cinetique","Dynamie"])]
with get_session() as s:
    total_ch = 0
    for ue_name, mats in matieres:
        ue = UE(nom=ue_name, code=f"UE-{ue_name[:3].upper()}", semestre_code="S3")
        s.add(ue); s.flush()
        for mat_name in mats:
            mat = Matiere(nom=mat_name, ue_id=ue.id, professeur="Dr. Martin")
            s.add(mat); s.flush()
            for c in range(1, 5):
                s.add(Chapitre(matiere_id=mat.id, numero=c, titre=f"{mat_name} Ch.{c}"))
                total_ch += 1
    s.commit()
    log("✅", f"{len(matieres)} UEs, {total_ch} chapitres")

# 3. SEMAINE + TACHES
today = date.today()
iso = today.isocalendar()
debut = today - timedelta(days=today.weekday())
jours = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
with get_session() as s:
    sem = Semaine(annee=iso[0], numero_semaine=iso[1], date_debut=debut, date_fin=debut+timedelta(days=6))
    s.add(sem); s.flush()
    SID = sem.id
    for jour in jours:
        for slot in range(3):
            s.add(Tache(semaine_id=SID, jour=jour, type="etude", titre=f"Session {slot+1}",
                       heure_debut=dtime(8+slot*4,0), heure_fin=dtime(10+slot*4,0), duree_min=50, statut="a faire"))
    # Sport
    s.add(Tache(semaine_id=SID, jour="mercredi", type="sport", titre="Sport", heure_debut=dtime(17,0), heure_fin=dtime(18,30), duree_min=90, statut="a faire"))
    s.commit()
    tc = s.query(Tache).filter_by(semaine_id=SID).count()
    log("✅", f"Semaine {iso[1]}/{iso[0]} — {tc} taches")

# 4. QUIZ LEITNER
with get_session() as s:
    from services.revision_service import appliquer_resultat_quiz
    chapitres = s.query(Chapitre).limit(20).all()
    ok = 0
    for ch in chapitres:
        try:
            appliquer_resultat_quiz(s, ch.id, float(random.choice([0,50,75,100])), "qcm")
            ok += 1
        except Exception as e:
            log("❌", f"Quiz: {e}")
    s.commit()
    log("✅", f"Quiz: {ok}/20 OK")

# 5. RAPPORT
print("\n" + "=" * 55)
with get_session() as s:
    print(f"  👤 {s.query(Utilisateur).count()}  📚 {s.query(UE).count()}UE  📖 {s.query(Matiere).count()}mat")
    print(f"  📝 {s.query(Chapitre).count()}chap  📅 {s.query(Semaine).count()}sem  ✅ {s.query(Tache).count()}taches")
passed = sum(1 for r in REPORT if "✅" in r)
failed = sum(1 for r in REPORT if "❌" in r)
print(f"  ✅ {passed}  ❌ {failed}")
print("\n  🏆 CORPS ENTIER OK" if failed == 0 else f"\n  ⚠️ {failed} echec(s)")
os.unlink(tmp_path)
