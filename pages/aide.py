"""Page **❓ Aide** — guide d'utilisation et pédagogie.

Pas de logique métier ici, juste de la documentation interactive pour :

- Comprendre la méthode des J (rappel théorique court).
- Naviguer dans l'app (à quoi sert chaque onglet, dans quel ordre).
- FAQ sur les cas d'usage typiques (planifier la semaine prochaine,
  ajouter un cours après génération, sauvegarder, examen approche…).

Conçue pour être utile à TROIS publics :

- Toi dans 6 mois quand tu reviens sur l'app après une longue pause.
- Toi en pleine période de partiels quand tu n'as pas le temps de
  retrouver « comment ça marche déjà ? ».
- Toute personne à qui tu montrerais l'app et qui se demande à quoi
  ça sert.
"""

from __future__ import annotations

import streamlit as st


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
def _render_intro() -> None:
    st.title("❓ Aide & Mode d'emploi")
    st.caption(
        "L'app construit ton planning hebdo en croisant ton objectif "
        "d'études, ta charge réelle (sport, job, cours présentiels), ta "
        "forme du jour, et la méthode de répétition espacée Leitner. Tout "
        "ce que tu valides nourrit l'algorithme — il n'y a rien à "
        "configurer manuellement après ton profil initial."
    )


def _render_methode_des_j() -> None:
    st.header("🧠 La méthode des J en 30 secondes")
    st.markdown(
        """
**Principe** : le cerveau oublie une info nouvelle très vite. Si tu la
revois **juste avant** de l'oublier, tu remets le compteur à zéro et
l'intervalle suivant peut être plus long. C'est la **courbe d'oubli
d'Ebbinghaus** appliquée à l'étude.

**Tes intervalles dans l'app** :

| Niveau Leitner | Intervalle | Quand ? |
|---|---|---|
| **J1** | 1 jour | Première révision le lendemain |
| **J3** | 3 jours | Si J1 réussi |
| **J7** | 7 jours | Si J3 réussi |
| **J14** | 14 jours | Etc. |
| **J30** | 30 jours | |
| **J60** → **J2190** | Jusqu'à ~6 ans | Pour la maîtrise long terme |

**Si tu rates un quiz** : le niveau Leitner du chapitre **redescend**
automatiquement, et la prochaine date est plus proche. Pas de
culpabilisation : c'est par design.

**Ce que l'app fait pour toi** :
- Pose automatiquement les dates de révision après chaque quiz.
- Te montre ce qui tombe dans les 4 prochaines semaines (page Révisions).
- Projette les dates futures (J3 → J7 → J14 → …) sur 30 / 90 / 180 / 365 j.
- Détecte les conflits (plusieurs chapitres d'une matière le même jour,
  dépassement de ton plafond horaire) et propose un lissage automatique
  avec une tolérance ±3 jours.
        """
    )


def _render_navigation() -> None:
    st.header("🗺️ À quoi sert chaque onglet ?")
    st.markdown(
        """
### ⚙️ Configuration
- **👤 Profil** — Ton chronotype, ton plafond d'étude par jour, ton
  objectif hebdomadaire, tes trajets habituels, ta clé Gemini. À
  remplir une fois, à ajuster occasionnellement. La **zone Sauvegarde
  & Restauration** est en bas — pense à télécharger un backup toutes
  les semaines.
- **📚 Bibliothèque** — Tes UE, tes matières, tes chapitres. Importe
  un ou plusieurs PDFs → Gemini détecte les chapitres et les crée.

### 📅 Ma semaine (saisie hebdo)
Tu coches ce que tu veux travailler / faire cette semaine, matière par
matière, sport, courses, projets, etc. Ces saisies nourrissent la
génération du planning.
- **📖 Études** — Sélectionne tes matières et chapitres. Indicateurs
  visuels (🔥 révisions dues, 📑 nouveaux chapitres, 📊 maîtrise),
  pré-sélection automatique le lundi matin, bandeau live qui te dit si
  ta sélection rentre dans ton objectif hebdo.
- **🥊 Sport**, **🛒 Courses & Repas**, **🎯 Projets**, **🌱 Dev perso**,
  **🍹 Social**, **🧹 Intendance**, **⚖️ Ajustements** — Pareil pour
  chaque catégorie de ta vie.
- **💼 Jobs & Travail** — Tes horaires fixes (alternance, job étudiant).
  L'IA bloque ces créneaux automatiquement.
- **📸 Import Photo (IA)** — Photo de ton emploi du temps papier →
  Gemini extrait les créneaux.

### ⚡ Action
- **✨ Génération IA** — Le moment où l'IA construit ton planning.
  Sélecteur « cette semaine / semaine prochaine » en haut. Le check-in
  biomécanique du jour est juste sous le bandeau (impacte le plafond).
  Bouton **🔁 Intégrer mes nouveautés** si tu as ajouté des chapitres
  après une génération — préserve ton avancement.
- **📊 Suivi quotidien** — Valide tes tâches au fil de la journée
  (✅ Terminée / ⚠️ Partielle / ❌ Non faite). Bilan auto du jour +
  de la semaine, avec comparaison à ton objectif hebdo d'étude.
- **🧠 Salle d'étude** — Pour chaque chapitre : fiche IA, QCM auto, quiz
  ouvert avec correction Gemini, **Feynman audio** (tu expliques à voix
  haute, Gemini évalue).
- **🏆 Achievements** — Tous les badges débloqués + ceux à débloquer.
- **🎯 Objectifs** — Objectifs personnalisés à long terme (« 15 au
  partiel d'algèbre du 15 juin »). Gemini propose une stratégie qui
  booste les bons chapitres dans tes futurs plannings.

### 📈 Bilan
- **📈 Tableau de bord** — Vue d'ensemble : XP/niveau/streak, planning
  visuel, KPIs, graphes de maîtrise par matière, donut de répartition
  globale de ton temps validé.
- **🧠 Révisions (Méthode des J)** — La page stratégique. Distribution
  par boîte Leitner, calendrier 4 semaines, projection long terme avec
  détection de conflits et bouton de lissage automatique.
        """
    )


def _render_workflows() -> None:
    st.header("🔁 Cas d'usage typiques")

    with st.expander("📆 Lundi matin — préparer ma semaine", expanded=True):
        st.markdown(
            """
1. Va sur **📖 Études** — les matières avec révisions dues sont déjà
   pré-cochées en vert.
2. Décoche ce que tu ne veux pas, ajoute d'autres chapitres si besoin.
   Vérifie le bandeau live : ta charge doit être ≤ ton objectif hebdo.
3. Va sur **✨ Génération IA**.
4. Fais ton check-in du jour (si fatigué, le plafond baisse de 30 %).
5. Optionnel : ajoute une consigne libre (ex. « jeudi je dois finir
   avant 18h »).
6. Clique sur **🚀 Générer le planning**.
7. C'est tout. Pour ajuster pendant la semaine, valide tes tâches dans
   **📊 Suivi quotidien**.
            """
        )

    with st.expander("📆 Dimanche soir — préparer la semaine prochaine"):
        st.markdown(
            """
1. Va sur **📖 Études**, change le sélecteur en haut sur **📆 Semaine
   prochaine**.
2. Sélectionne tes matières comme d'habitude.
3. Va sur **✨ Génération IA** — le sélecteur est déjà sur « semaine
   prochaine » (le choix est partagé).
4. Génère. Lundi matin, tout est prêt.
            """
        )

    with st.expander("➕ J'ai ajouté un chapitre APRÈS avoir généré"):
        st.markdown(
            """
1. **Bibliothèque** → tu importes le nouveau PDF.
2. **Études** → tu coches le nouveau chapitre dans ta sélection.
3. **Génération IA** → un bandeau bleu apparaît :
   *« ➕ 1 nouveauté détectée »*. Clique **🔁 Intégrer mes nouveautés**.
4. L'IA ajoute le chapitre dans les créneaux libres restants, **sans
   toucher** à tes tâches déjà validées (XP, statuts, etc.).
            """
        )

    with st.expander("⚠️ Plusieurs chapitres tombent le même jour"):
        st.markdown(
            """
Tu vois sur **🧠 Révisions** que plusieurs chapitres d'une même
matière tombent le même jour (par exemple parce que tu les as tous
importés en lot) ?

1. Va dans la section **📈 Projection long terme**.
2. Si l'app détecte des conflits, un bandeau jaune apparaît.
3. Clique **🪄 Lisser automatiquement**.
4. L'algo décale les chapitres en doublon vers les jours libres dans
   [J, J+3] — l'esprit Leitner reste respecté.
            """
        )

    with st.expander("🎯 J'ai un partiel dans 3 semaines"):
        st.markdown(
            """
Crée un **🎯 Objectif** :

1. **Action → Objectifs → ➕ Nouvel objectif**.
2. Renseigne : titre (« 15 au partiel d'algèbre »), matière visée,
   note cible, date du partiel.
3. Clique **🧠 Demander une stratégie à l'IA**.
4. Gemini analyse ton état actuel et propose des pondérations par
   chapitre. Tu peux **adopter** la stratégie — elle sera appliquée
   automatiquement dans tous tes futurs plannings hebdo.
            """
        )

    with st.expander("💾 Comment sécuriser mes données ?"):
        st.markdown(
            """
Streamlit Cloud peut perdre tes données si l'instance est recréée.
**Télécharge une sauvegarde régulièrement** (le dimanche soir par
exemple, en même temps que tu prépares la semaine suivante) :

1. **Profil → 💾 Sauvegarde & Restauration**.
2. Clique **💾 Télécharger** — tu récupères un `.zip` horodaté avec
   ta base + tes PDFs.
3. Range-le sur ton ordi ou ton Drive.

Pour restaurer : dans la même section, upload ton zip et confirme.
Tout est rétabli (DB + PDFs).
            """
        )


def _render_faq() -> None:
    st.header("💬 FAQ rapide")

    with st.expander("Pourquoi le planning ne change pas quand je clique « Régénérer » ?"):
        st.markdown(
            """
Il change ! Mais comme tes contraintes (matières, profil, check-in)
sont identiques, Gemini sort un planning souvent proche du précédent.
Vérifie le **bandeau vert en haut** (« 🔄 Planning régénéré à HH:MM »)
et la **stratégie de l'IA** dans la section 2 — elle est régénérée à
chaque fois.
            """
        )

    with st.expander("Mon plafond d'étude est-il fixe ?"):
        st.markdown(
            """
Non. Il est **modulé automatiquement** par ton check-in biomécanique
du jour : si tu déclares fatigue > 7/10 ou charge mentale > 7/10, le
plafond baisse de **30 %** pour ce jour-là. C'est pour respecter ton
état réel.
            """
        )

    with st.expander("Qu'est-ce que la « tolérance ±3 jours » ?"):
        st.markdown(
            """
Quand Leitner dit qu'un chapitre doit être revu le mardi 25, mais que
ce jour est déjà saturé, l'app s'autorise à le décaler dans la fenêtre
[lundi 24, vendredi 28]. **3 jours de tolérance** est pédagogiquement
acceptable et permet d'éviter les surcharges sans trahir la méthode.
Visible dans la projection long terme + bouton de lissage automatique.
            """
        )

    with st.expander("Pourquoi mes nouveaux chapitres tombent tous le même jour ?"):
        st.markdown(
            """
Quand tu importes plusieurs PDFs en lot, ils sont tous initialisés à
J1 (= +1 jour). C'est l'algo Leitner standard. Pour étaler, utilise
le bouton **🪄 Lisser automatiquement** sur la page Révisions, ou
laisse l'IA répartir lors de la prochaine génération de planning.
            """
        )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def render() -> None:
    _render_intro()
    st.divider()
    _render_methode_des_j()
    st.divider()
    _render_navigation()
    st.divider()
    _render_workflows()
    st.divider()
    _render_faq()


# Exécution Streamlit
render()
