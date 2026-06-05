# CLAUDE.md — guide d'architecture pour assistants IA

> Document de transmission pour les futurs assistants IA (Claude, Gemini,
> DeepSeek, …) qui interviennent sur ce projet. Lis ce fichier en premier
> avant toute modification non triviale. Il capture l'architecture, les
> patterns établis, et **les pièges à éviter** identifiés à la dure.

## Vue d'ensemble

App Streamlit personnelle de planification d'études pour étudiant. Stack :

- **Streamlit** (UI multi-pages via `app.py` + `pages/`)
- **SQLAlchemy** + **SQLite** (mono-fichier local : `data/planning.db`)
- **Gemini AI** (SDK `google-genai`) pour planning, fiches IA, QCM, quiz,
  évaluation de réponses ouvertes, extraction de PDFs
- **pdfplumber** + **PyMuPDF** pour l'extraction texte/TOC des PDFs
- **cryptography (Fernet)** pour chiffrer la clé API Gemini en base

Mono-utilisateur. Aucune authentification : l'app suppose un user singleton
en DB (`Utilisateur` avec 4 sous-configs).

## Architecture DDD — ⚠️ piège n°1

Le modèle `Utilisateur` est **un agrégat racine** avec 4 sous-configs en
relation one-to-one (refonte DDD historique). **Tous les champs métier
sont sur les sous-configs**, jamais directement sur `Utilisateur`.

```
Utilisateur (id, nom, created_at, updated_at)
├── biometrie   : BiometrieConfig   (heure_lever, chronotype,
│                                    heures_etude_*, duree_max_session_min, …)
├── logistique  : LogistiqueConfig  (contraintes_fixes, trajets_habituels,
│                                    nb_repas_par_jour, temps_transport_min, …)
├── systeme     : SystemeConfig     (gemini_api_key (chiffrée!), gemini_model,
│                                    replanning_auto_actif)
└── gamification: GamificationState (xp, niveau, streak_jours, streak_record,
                                     derniere_activite_xp, nb_quiz_total, …)
```

**Toujours** accéder via la relation : `profil.biometrie.heure_lever`,
**jamais** `profil.heure_lever`.

J'ai trouvé 3 bugs DDD résiduels (gamification, scheduler, ai_planner) où
des fonctions lisaient `profil.X` directement → `AttributeError` silencieux
masqué par les `except Exception`, OU `getattr` qui retournait `None` et
faisait tomber le code en fallback inutilement. **Vérifie chaque accès
`profil.X` quand tu touches du code.**

### Liste exhaustive des attrs migrés (anti-DDD-trap)

Si tu vois ces noms à droite d'un `profil.`, c'est un BUG (ou une
double-écriture intentionnelle à vérifier) :

- `xp`, `niveau`, `streak_jours`, `streak_record`, `derniere_activite_xp`,
  `nb_quiz_total`, `nb_chapitres_maitrise`, `nb_seances_sport_total` → **gamification**
- `heure_lever`, `heure_coucher`, `chronotype`, `pic_concentration`,
  `duree_max_session_min`, `pause_entre_sessions_min`, `methode_travail`,
  `capacite_weekend`, `tolerance_fatigue`, `besoin_sieste`, `duree_sieste_min`,
  `heures_etude_cible_par_semaine`, `heures_etude_plafond_par_jour` → **biometrie**
- `contraintes_fixes`, `trajets_habituels`, `nb_repas_par_jour`,
  `duree_repas_min`, `duree_prep_repas_min`, `temps_transport_min` → **logistique**
- `gemini_api_key`, `gemini_model`, `replanning_auto_actif` → **systeme**

### Helper centralisé pour Gemini

**Ne jamais** lire `profil.systeme.gemini_api_key` directement : c'est
chiffré (Fernet) en base. Utilise :

```python
from services.profil_service import get_gemini_credentials
api_key, model = get_gemini_credentials(session)  # déchiffre transparent
```

### Helper centralisé pour params scheduler

Le scheduler utilise un `_profil_attr(profil, attr, default)` qui sait
chercher dans `profil.biometrie.X` puis `profil.logistique.X` puis
`profil.X` direct (pour compat tests avec `SimpleNamespace`).

## Patterns établis (à respecter)

### Sessions SQLAlchemy

- **Read-only court** : `with get_session() as session:` (pas de commit).
- **Mutation** : `with session_scope() as session:` (commit-on-success,
  rollback-on-exception).
- **Jamais de session ouverte pendant un appel Gemini** (15-30 s d'I/O
  réseau). Voir `services.ai_planner.generate_schedule_from_ai` pour le
  pattern à 3 phases : load (session) → call Gemini (sans session) →
  validate + return.

### Appels Gemini

**Toujours** via `services.gemini_utils.gemini_call_with_retry(call_fn,
context="...")` :

- Retry exponentiel auto (2s, 4s, 8s) sur erreurs transitoires
  (429, 503, 504, ConnectionError, TimeoutError).
- Pas de retry sur 400/401/403/"api key invalid".
- Logging structuré dans le logger `"gemini"`.

### Validation des sorties Gemini

Gemini **ment** régulièrement. Toujours valider stricte :

- **Planning hebdo** : `services.planner_validator.validate_planning(raw)`
  ou `validate_partial_planning(raw, key, allowed_jours)`.
- **QCM/quiz** : `services.qcm_validator.validate_qcm_questions(raw_list)`
  ou `validate_quiz_questions(raw_text, max_questions)`.
- **PDF analysis** : `services.pdf_analyzer._validate_and_normalize`.

### Cache versionné

Toute mise en cache d'une sortie Gemini coûteuse (`fiche_ia`, `qcm_cache`,
`quiz_cache`) **doit** stocker `_model`, `_prompt_version`, `_texte_sha`
en parallèle, et **vérifier** la validité via `services.cache_versioning.
cache_is_valid(...)` avant lecture. Sinon le cache ne s'invalide jamais
quand le modèle change ou le PDF est re-uploadé.

Quand tu modifies un prompt système majeur, **bump** la constante
`FICHE_PROMPT_VERSION` / `QCM_PROMPT_VERSION` / `QUIZ_PROMPT_VERSION`.

### Verrou optimiste (multi-onglets)

Pour les mutations sensibles à la concurrence (notes, etc.), utiliser
`services.optimistic_lock.update_chapitre_safe(session, id,
expected_version, mutate)`. Lève `ConflictError` si l'onglet a une
version stale.

### Idempotence XP

`Tache.xp_attribue` (Boolean) est un **flag persistant** posé à la 1re
attribution. **Ne jamais** re-attribuer d'XP ou re-bumper `maitrise_pct`
si ce flag est True. Sinon → exploit farming par toggle statut.

### Validation PDF upload

Tout upload passe par `services.pdf_storage.validate_pdf_upload(bytes,
filename)` : taille ≤ 25 Mio, signature magique `%PDF-`, pas de path
traversal. Puis `compute_sha256` + `find_existing_upload` pour
l'idempotence (re-upload du même fichier sur la même matière → skip
analyse Gemini).

### Datetime

Toujours **timezone-aware UTC** : `datetime.now(timezone.utc)`. Jamais
`datetime.utcnow()` (déprécié 3.12+) ni `datetime.now()` naïf. Les colonnes
DateTime du modèle ont des défaults aware — un mix naïf/aware déclenche
des `TypeError` en comparaison.

### Gestion d'erreurs UI

Pattern standard dans les pages :

```python
try:
    resultat = appel_metier(...)
except (ValueError, RuntimeError) as exc:
    st.error(f"❌ Erreur : {exc}")          # message direct utilisateur
except Exception as exc:  # noqa: BLE001
    import logging
    logging.getLogger("gemini").exception("opération X")
    st.error(f"❌ Erreur inattendue : {exc}. Consulte les logs.")
```

**Plus jamais** d'`except Exception` nu sans logger derrière — c'est
comme ça que les 3 bugs DDD majeurs ont vécu en prod silencieusement.

## Migration douce

`database.db.migrate_schema()` est appelé au boot. Pour ajouter une
colonne sans casser une DB existante :

1. Ajouter la `Column` dans `database/models.py`.
2. Ajouter une entrée dans `_EXPECTED_COLUMNS[table_name]` dans
   `database/db.py` : `"col_name": "SQL_TYPE_AND_DEFAULT"`.
3. La migration est exécutée à chaque boot, idempotente.

Pour un **backfill de données** (recalcul de valeurs existantes), suivre
le pattern de `_backfill_duree_min` (fonction dédiée appelée à la fin
de `migrate_schema`, idempotente, SQL UPDATE conditionnel).

## Tests

`pytest tests/` — 250+ tests, **doivent rester verts**. Pattern :

- Engine SQLite in-memory + `sessionmaker` local.
- `monkeypatch.setattr(database.db, "SessionLocal", ...)` pour les fonctions
  qui ouvrent leur propre session.
- Fixtures isolées (pas de DB partagée entre tests).

**Avant de commit, toujours** : `python -m pytest tests/ -q
--ignore=tests/test_ical_exporter.py`.

(Le `test_ical_exporter.py` est ignoré car il a des dépendances optionnelles
non installées dans l'env CI.)

## Audit cohérence

`services.data_integrity.audit_all(session)` scanne 20+ invariants
(maitrise_pct ∈ [0,100], niveau ≤ MAX_NIVEAU, streak ↔ derniere_activite,
chapitres orphelins, doublons SaisieHebdo, …). À lancer périodiquement
en debug pour détecter les drifts de données silencieux.

`repair_all(session, dry_run=False)` corrige ~50% des issues automatiquement.

## Sécurité — ce qui est en place

- Clé Gemini chiffrée Fernet en base (`services.crypto`). La clé du chiffrement
  est dans `data/.vault_key` (gitignore).
- Validation magique-bytes sur restore backup (refuse les .db cassés).
- Validation upload PDF (taille, signature, path traversal).
- Backup défensif pré-restauration (`planning.db.bak`).
- Écriture atomique de la DB restaurée (rename via `.tmp`).
- Échappement HTML dans le rendu dashboard (`html.escape` + `_attr`).

## Anti-patterns à NE PAS introduire

- ❌ `print(...)` pour du logging — utiliser `logging.getLogger(...)`.
- ❌ `except Exception:` nu sans log derrière.
- ❌ `profil.<champ_migré>` direct.
- ❌ `getattr(profil, "X", None)` qui retourne `None` silencieusement.
- ❌ Appel Gemini sans `gemini_call_with_retry`.
- ❌ Cache IA sans bump de prompt_version après refonte du prompt.
- ❌ Mutation Chapitre/Tache sans passer par les helpers (`update_chapitre_safe`,
  `_update_task_status`).
- ❌ Modifier l'historique git avec `filter-repo` ou `--force` sans backup.
- ❌ Commit du fichier `data/.vault_key` (gitignore le bloque normalement).

## Bugs historiques connus (résolus mais à surveiller)

| Bug | Symptôme | Fix |
|---|---|---|
| Exploit farming XP | Toggle statut donnait XP infini | `Tache.xp_attribue` flag |
| DDD residual gamification | `AttributeError` à chaque XP | Accès via `.gamification.X` |
| DDD residual scheduler | Plafond utilisateur ignoré | Helper `_profil_attr` |
| Clé Gemini chiffrée non déchiffrée | Tous appels Gemini en erreur | `get_gemini_credentials()` |
| Session ouverte pendant Gemini | DB lock potentiel | Refactor 3-phases |
| N+1 `chap.matiere_obj.nom` | Lenteur Révisions | `selectinload` |
| `Tache.duree_min` jamais setté | KPI Dashboard à 0 | Calcul à l'insert + backfill |
| Cache IA non versionné | Fiches stales après changement de modèle | 4 colonnes `_model/_prompt_version/_texte_sha` |
| UE.semestre shadowing | Column `semestre` et relationship `semestre` se masquaient mutuellement | Colonne renommée `semestre_code` (garde `name="semestre"` en DB) |
| `print()` dans services | Erreurs masquées, pas de trace en prod | Migration vers `logging.getLogger(...)` dans db.py, ai_planner, pdf_analyzer |
| Appel Gemini direct dans ai_planner | `integrer_nouveautes` + `replan` contournaient `call_llm` → pas de multi-LLM | Refactor via `call_llm` (supporte DeepSeek + Gemini) |
| `except Exception:` nu sans log | Gamification, achievements : bugs silencieux | Ajout `logger.exception(...)` |

## Conseils pour Gemini/DeepSeek

- Lis ce fichier en entier avant toute modification non triviale.
- Avant de modifier un service, fais `grep -rn "<nom_fonction>"` pour voir
  tous les callers — la refactor a déjà cassé 3 fois des callers oubliés.
- Streamlit re-run la page à chaque interaction : ne pas faire d'opérations
  coûteuses dans le scope global d'un module.
- `st.session_state` survit aux reruns mais pas aux changements de page —
  utiliser `st.query_params` pour le state cross-page.
- Pour toute fonctionnalité utilisateur visible : tester manuellement avec
  `streamlit run app.py` (pas seulement `pytest`) avant de déclarer done.

🤖 Document rédigé par Claude le 23/05/2026 en passation pour Gemini/DeepSeek.
