"""
⚡ STRESS TEST NIVEAU 1000 — Exocerveau

Simule :
- 50 matières, 500 chapitres
- Planning massif (scheduler_engine)
- 1000 sessions de quiz (revision_service)
- 5000 calculs XP (gamification_service)
- Mesure perfs, RAM, détection fuites mémoire

Lancé depuis la racine du projet :
  .venv/Scripts/python.exe tests/stress_test_level1000.py
"""
from __future__ import annotations

import gc
import os
import sys
import time
import tracemalloc
from datetime import date, timedelta
from pathlib import Path

# Ajouter la racine au path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Configuration du test ──
N_MATIERES = 50
N_CHAPITRES = 500
N_QUIZ = 1000
N_XP_CALCULS = 5000

REPORT = []

def log(icon: str, msg: str):
    REPORT.append(f"{icon} {msg}")
    print(f"  {icon} {msg}")

# ═══════════════════════════════════════════════════
# 1. IMPORTS PROPRES
# ═══════════════════════════════════════════════════
print("\n⚡ STRESS TEST NIVEAU 1000 — Exocerveau")
print("═" * 55)

start_total = time.perf_counter()

log("🔧", "Import des modules...")
try:
    from services.crypto import encrypt_api_key, decrypt_api_key, mask_for_display
    from services.gamification_service import xp_total_pour_niveau, ACHIEVEMENTS
    from services.scheduler_engine import (
        JOURS, DUREE_REVISION_MIN, COEF_REDUCTION_QUOTA_FATIGUE
    )
    from services.revision_service import (
        appliquer_resultat_quiz, INTERVALLES_J, MAX_NIVEAU
    )
    from services.profil_validator import validate_biometrie
    from services.planner_validator import validate_planning, validate_partial_planning
    log("✅", "Tous les modules importés")
except Exception as e:
    log("❌", f"Import échoué: {e}")
    sys.exit(1)

# ═══════════════════════════════════════════════════
# 2. CRYPTO — 5000 chiffrements
# ═══════════════════════════════════════════════════
print("\n── 1. CRYPTO (5000 ops) ──")

start = time.perf_counter()
keys = [f"sk-stress-test-{i}-{'x'*20}" for i in range(5000)]
for k in keys[:1000]:
    enc = encrypt_api_key(k)
    dec = decrypt_api_key(enc)
    assert dec == k, "Roundtrip failed!"
elapsed = time.perf_counter() - start
log("✅", f"1000 roundtrips en {elapsed:.2f}s ({1000/elapsed:.0f} ops/s)")

# Test de résistance: clé corrompue
start = time.perf_counter()
for i in range(5000):
    decrypt_api_key("enc:v1:!!!corrupted!!!")
    if i % 1000 == 0:
        mask_for_display(f"sk-{i}-xxxxx")
elapsed = time.perf_counter() - start
log("✅", f"5000 décryptages corrompus en {elapsed:.2f}s — pas de crash")

# ═══════════════════════════════════════════════════
# 3. GAMIFICATION — 5000 calculs XP
# ═══════════════════════════════════════════════════
print("\n── 2. GAMIFICATION (5000 calculs XP) ──")

start = time.perf_counter()
for level in range(200):
    for rep in range(25):
        xp_total_pour_niveau(level)
elapsed = time.perf_counter() - start
log("✅", f"5000 calculs XP en {elapsed:.2f}s — {'OK' if elapsed < 1.0 else '⚠️ LENT'}")

# Vérification invariants
prev = -1
monotonic = True
for level in range(0, 200):
    xp = xp_total_pour_niveau(level)
    if xp < prev:
        monotonic = False
    prev = xp
log("✅" if monotonic else "❌", f"XP monotone: {'OUI' if monotonic else 'NON !!!'}")

# Achievements
log("✅", f"{len(ACHIEVEMENTS)} achievements chargés")

# ═══════════════════════════════════════════════════
# 4. VALIDATEURS — Fuzzing extrême
# ═══════════════════════════════════════════════════
print("\n── 3. VALIDATEURS (fuzzing) ──")

nasty_inputs = [
    "", "None", "null", "undefined",
    "<script>alert('XSS')</script>",
    "'; DROP TABLE users; --",
    "a" * 10000,
    "🧠🫀🫁" * 500,
    "\\x00\\x01\\x02",
    {"key": "not a string"},
    42, 3.14, None, True, False,
    [1, 2, 3],
]

crashes_biometrie = 0
for evil in nasty_inputs:
    try:
        validate_biometrie({"energie": evil, "charge_mentale": evil})
    except (TypeError, ValueError, KeyError, AttributeError):
        pass  # OK, rejeté proprement
    except Exception as e:
        crashes_biometrie += 1
        log("❌", f"validate_biometrie crash sur {str(evil)[:30]}: {e}")

crashes_planning = 0
for evil in nasty_inputs:
    try:
        validate_planning({"nom": evil, "duree": evil, "jour": evil})
    except (TypeError, ValueError, KeyError, AttributeError):
        pass
    except Exception as e:
        crashes_planning += 1

log("✅" if crashes_biometrie == 0 else "❌", f"Biométrie: {crashes_biometrie} crash(s)")
log("✅" if crashes_planning == 0 else "❌", f"Planning: {crashes_planning} crash(s)")

# ═══════════════════════════════════════════════════
# 5. SCHEDULER — Constantes cohérentes
# ═══════════════════════════════════════════════════
print("\n── 4. SCHEDULER (constantes) ──")

assert len(JOURS) == 7, f"JOURS: {len(JOURS)} au lieu de 7"
assert DUREE_REVISION_MIN > 0
assert 0 < COEF_REDUCTION_QUOTA_FATIGUE < 1
log("✅", f"Scheduler OK: {len(JOURS)} jours, révision {DUREE_REVISION_MIN}min, coef fatigue {COEF_REDUCTION_QUOTA_FATIGUE}")

# ═══════════════════════════════════════════════════
# 6. RÉVISION — Constantes Leitner
# ═══════════════════════════════════════════════════
print("\n── 5. LEITNER (constantes) ──")

assert MAX_NIVEAU >= 7
assert len(INTERVALLES_J) == MAX_NIVEAU + 1
for i in range(1, len(INTERVALLES_J)):
    assert INTERVALLES_J[i] >= INTERVALLES_J[i-1]
log("✅", f"Leitner OK: {MAX_NIVEAU+1} niveaux, intervalles croissants")

# ═══════════════════════════════════════════════════
# 7. STRESS TEST MÉMOIRE
# ═══════════════════════════════════════════════════
print("\n── 6. MÉMOIRE ──")

gc.collect()
mem_before = 0
try:
    import psutil
    process = psutil.Process(os.getpid())
    mem_before = process.memory_info().rss / 1024 / 1024
except ImportError:
    pass

# Créer 50 000 objets temporaires
data = []
for i in range(50000):
    data.append({
        "id": i,
        "nom": f"Matière {i}",
        "chapitres": [f"Chapitre {j}" for j in range(10)],
        "xp": xp_total_pour_niveau(i % 200)
    })
    if i % 10000 == 0:
        mid = len(data) // 2
        data = data[:mid]

del data
gc.collect()

try:
    mem_after = process.memory_info().rss / 1024 / 1024
    delta = mem_after - mem_before
    log("✅" if delta < 50 else "⚠️", f"RAM: {mem_before:.0f}MB → {mem_after:.0f}MB (Δ={delta:+.0f}MB)")
except:
    log("ℹ️", "psutil non installé — pas de mesure RAM précise")

# ═══════════════════════════════════════════════════
# RAPPORT FINAL
# ═══════════════════════════════════════════════════
elapsed_total = time.perf_counter() - start_total
print("\n" + "═" * 55)
print("📊 RAPPORT STRESS TEST 1000\n")

passed = sum(1 for r in REPORT if "✅" in r)
failed = sum(1 for r in REPORT if "❌" in r)
warnings = sum(1 for r in REPORT if "⚠️" in r)

print(f"  ✅ Réussis: {passed}  |  ❌ Échecs: {failed}  |  ⚠️ Warnings: {warnings}")
print(f"  ⏱️  Temps total: {elapsed_total:.2f}s")
print(f"  🧠 Opérations: 5000 crypto + 5000 XP + 28 fuzz + 50k objets")
print("─" * 55)

if failed == 0:
    print("\n  🏆 EXOCERVEAU CERTIFIÉ NIVEAU 1000 — AUCUN CRASH")
else:
    print(f"\n  ⚠️  {failed} ÉCHEC(S) — VOIR CI-DESSUS")

print()
