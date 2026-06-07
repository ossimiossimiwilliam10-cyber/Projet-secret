# README — Architecture & Guide de Présentation Détaillée

> Ce document de présentation fait office de guide de référence, de documentation architecturale et de manuel d'intégration pour les assistants IA et les développeurs intervenant sur ce projet. À lire impérativement avant toute modification du code pour respecter les patterns établis et éviter les pièges conceptuels majeurs.

## 1. Vue d'Ensemble & Stack Technique

L'application est une plateforme de planification d'études personnelle (mono-utilisateur) développée pour les étudiants, intégrant fortement l'IA pour l'analyse de documents et l'aide à l'apprentissage.

### Stack Technique
- **Interface Utilisateur :** Streamlit (architecture multi-pages via `app.py` et dossiers `pages/`, `modules/`).
- **Base de Données :** SQLAlchemy ORM couplé à SQLite (stockage local mono-fichier dans `data/planning.db`).
- **Intelligence Artificielle :** Gemini AI via le SDK `google-genai` (pour la génération de plannings, QCM, quiz, fiches de révisions, extraction sémantique des PDFs).
- **Extraction PDF :** `pdfplumber` (pour le texte brut) et `PyMuPDF / fitz` (pour les métadonnées et la table des matières).
- **Sécurité :** Cryptographie via `cryptography.fernet` pour le stockage chiffré des clés API.

## 2. Modèle de Données & Architecture DDD (Domain-Driven Design)

L'application suit une structure DDD stricte où certaines entités sont des Agrégats Racines (*Aggregate Roots*). Le cas d'école est l'entité `Utilisateur`.

### L'Agrégat `Utilisateur` (⚠️ Piège Majeur)
L'entité `Utilisateur` ne contient **aucun champ métier direct**. Elle délègue toutes ses données métier à 4 sous-configurations gérées via des relations One-to-One.
**Règle d'or :** Toujours accéder aux champs via la relation (ex: `profil.biometrie.heure_lever`), **JAMAIS** directement via l'utilisateur (`profil.heure_lever`).

#### Schéma de la relation Utilisateur
```text
Utilisateur (id, nom, created_at, updated_at)
├── biometrie   : BiometrieConfig
│                 (heure_lever, chronotype, heures_etude_*, duree_max_session_min, …)
├── logistique  : LogistiqueConfig
│                 (contraintes_fixes, trajets_habituels, nb_repas_par_jour, …)
├── systeme     : SystemeConfig
│                 (gemini_api_key [chiffrée], gemini_model, replanning_auto_actif)
└── gamification: GamificationState
                  (xp, niveau, streak_jours, streak_record, nb_quiz_total, …)
```

## 3. Sous-systèmes & Intégration IA (Gemini)

### Appels LLM Centralisés et Résilients
Ne faites **jamais** d'appel à l'API LLM sans utiliser les wrappers dédiés de `services.llm_utils`.
- **`call_llm`** : Encapsule les spécificités du SDK `google-genai` et retourne directement une chaîne de texte (`str`). Gère l'interface avec `deepseek` ou `gemini`.
- **`llm_call_with_retry`** : Applique un backoff exponentiel (2s, 4s, 8s) pour gérer automatiquement les erreurs réseau transitoires (429, 503, 504, Timeouts) sans bloquer l'UI. Les erreurs permanentes (ex: API key invalide, 401) ne sont pas relancées.

### Accès Sécurisé à la Clé API
La clé API n'est **jamais stockée en clair**. Pour l'utiliser :
```python
from services.profil_service import get_gemini_credentials
api_key, model = get_gemini_credentials(session)  # Déchiffrement transparent
```

### Validation des Sorties LLM
Le LLM peut générer des réponses mal formatées. Tous les appels exigeant du JSON doivent valider strictement le format :
- Plannings: `services.planner_validator.validate_planning`
- Exercices: `services.qcm_validator.validate_qcm_questions`
- Documents: `services.pdf_analyzer._validate_and_normalize`

### Traitement des PDFs
Le pipeline d'import des documents (`services.pdf_analyzer.py`) est un pilier de l'app :
1. Extraction du texte via `pdfplumber` + TOC via `PyMuPDF`.
2. Troncature intelligente (si > 200 000 caractères) gardant le début, milieu et la fin.
3. Appel Gemini pour scinder le document en un JSON structuré de chapitres avec densité, temps de traitement estimé et mots-clés.

## 4. Gestion de la Révision : "Méthode des J" & Lissage

La logique d'étude est centrée sur le **Système de Leitner (Méthode des J)**. L'objectif est la rétention à long terme et la planification.
- **Intervalles de révision (`INTERVALLES_J`) :** La séquence de répétition espacée suit les intervalles : `1, 3, 5, 7, 14, 30, 60, 90, 180, 365, 730, 1095, 1460, 1825, 2190`.
- **Lissage Automatique :** La fonction `lisser_automatiquement_dates_leitner` permet de décaler dynamiquement les dates de révision avec une tolérance de **±4 jours** pour empêcher l'utilisateur d'être submergé par une accumulation de révisions le même jour, tout en respectant son plafond horaire (`heures_etude_plafond_par_jour`).

## 5. Gestion de l'État & Concurrence

### Sessions SQLAlchemy
Gérez toujours soigneusement l'ouverture des sessions :
- **Lecture rapide :** `with get_session() as session:` (Lecture seule).
- **Écriture transactionnelle :** `with session_scope() as session:` (Commit auto si succès, Rollback auto si exception).
- **Anti-Pattern :** Ne **jamais** maintenir une session de base de données ouverte pendant un appel API Gemini (qui peut bloquer 15 à 30 secondes). Il faut charger l'objet, fermer/quitter la session, lancer l'appel réseau, puis rouvrir une session d'écriture.

### Concurrence & Optimistic Locking
Puisque Streamlit peut être ouvert sur plusieurs onglets, nous appliquons un **Verrou Optimiste** (Optimistic Locking) pour les champs fréquemment mis à jour simultanément (comme les prises de `notes` d'un chapitre).
Exemple : utiliser la fonction `update_chapitre_safe` qui lève une exception `ConflictError` si la version locale de l'onglet (`expected_version`) est obsolète par rapport à la base.

### Caches Versionnés de l'IA
Les sorties de Gemini coûteuses en temps et requêtes (`fiche_ia`, `qcm_cache`, `quiz_cache`) sont gardées en cache. Le système (`services.cache_versioning`) enregistre toujours conjointement :
1. Le hash du texte source (`_texte_sha`).
2. Le modèle utilisé (`_model`).
3. La version du prompt (`_prompt_version`).
Si tu modifies un prompt dans le code source, tu **dois impérativement** incrémenter sa constante version (ex: `FICHE_PROMPT_VERSION`) pour forcer l'invalidation automatique des caches obsolètes.

### Idempotence de l'XP
Pour empêcher l'exploitation et la fraude d'expérience (farming de XP) dans la gamification, chaque tâche achevée possède un flag `xp_attribue`. L'XP n'est attribué qu'une fois. **Ne jamais** ré-octroyer de XP si ce flag est à `True`.

## 6. Bonnes Pratiques Complémentaires

- **Fuseaux Horaires (Timezones) :** Utilisez exclusivement **UTC aware** (`datetime.now(timezone.utc)`). Fuyez les objets datetime naïfs.
- **Gestion des Erreurs UI :** Dans Streamlit, toujours encapsuler les appels métier par un bloc `try-except` clair. Consignez l'exception (`logger.exception`) et n'exposez à l'UI qu'un `st.error()` formaté. Fini les exceptions avalées silencieusement par un simple `except Exception: pass`.
- **Migration Douce (Soft Migrations) :** La fonction `database.db.migrate_schema()` tourne à l'amorçage. Pour ajouter une nouvelle colonne, ajoutez-la à SQLAlchemy (`models.py`) puis à la dictionnaire `_EXPECTED_COLUMNS` dans `db.py` pour un backfill automatique de la colonne via requête SQL (sans nécessiter de destruction des tables).
- **Tests Automatisés :** La suite repose sur un backend SQLite en mémoire, isolé pour `pytest`. Un script d'audit complet (`services.data_integrity.audit_all(session)`) maintient la solidité de la base. Toujours lancer `python -m pytest tests/ -q` avant un grand refactoring.
