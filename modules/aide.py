"""Page **❓ Aide** — guide d'utilisation et pédagogie.

Pas de logique métier ici, juste de la documentation interactive pour :
- Comprendre la méthode des J (rappel théorique).
- Naviguer dans l'app (à quoi sert chaque onglet).
- Cas d'usage typiques et FAQ.

Conçue pour TROIS publics :
- Toi dans 6 mois quand tu reviens après une longue pause.
- Toi en période de partiels, pressé.
- Toute personne découvrant l'app.
"""

from __future__ import annotations

import streamlit as st


# ---------------------------------------------------------------------------
# Section 0 — Intro
# ---------------------------------------------------------------------------
def _render_intro() -> None:
    st.title("❓ Aide & Mode d'emploi")
    st.caption(
        "L'app construit ton planning hebdo en croisant ton objectif "
        "d'études, ta charge réelle (sport, job, cours), ta forme du jour, "
        "et la répétition espacée Leitner. "
        "Tout ce que tu valides nourrit l'algorithme."
    )

    # Mini KPIs pour donner une vue d'ensemble
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown("**📅 Semestre**  \n<small>Regroupe tes UE</small>", unsafe_allow_html=True)
    with col2:
        st.markdown("**🎓 UE**  \n<small>Unité d'Enseignement</small>", unsafe_allow_html=True)
    with col3:
        st.markdown("**📘 Matière**  \n<small>Sous-discipline</small>", unsafe_allow_html=True)
    with col4:
        st.markdown("**📑 Chapitre**  \n<small>Granularité de révision</small>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 1 — Méthode des J
# ---------------------------------------------------------------------------
def _render_methode_des_j() -> None:
    st.header("🧠 La méthode des J en 30 secondes")

    col1, col2 = st.columns([1, 1])
    with col1:
        st.markdown(
            """
**Principe** : le cerveau oublie une info très vite. Si tu la revois
**juste avant** de l'oublier, tu remets le compteur à zéro et
l'intervalle suivant peut être plus long. C'est la **courbe d'oubli
d'Ebbinghaus**.

**Si tu rates un quiz** : le niveau Leitner **redescend**
automatiquement. Pas de culpabilisation — c'est par design.

**Ce que l'app fait pour toi** :
- Pose automatiquement les dates de révision après chaque quiz
- Te montre ce qui arrive dans les 4 prochaines semaines
- Projette les dates futures (J3 → J7 → J14 → …) sur 30/90/180/365 j
- Détecte les conflits et propose un lissage ±3 jours
"""
        )
    with col2:
        st.markdown(
            """
| Niveau | Intervalle | Quand ? |
|---|---|---|
| **J1** | 1 jour | Lendemain |
| **J3** | 3 jours | Si J1 réussi |
| **J7** | 7 jours | Si J3 réussi |
| **J14** | 14 jours | Etc. |
| **J30** | 30 jours | |
| **J60** → **J2190** | Jusqu'à ~6 ans | Maîtrise long terme |
"""
        )


# ---------------------------------------------------------------------------
# Section 2 — Navigation (mise à jour avec Semestres + DeepSeek)
# ---------------------------------------------------------------------------
def _render_navigation() -> None:
    st.header("🗺️ À quoi sert chaque onglet ?")

    with st.expander("⚙️ Configuration", expanded=True):
        st.markdown(
            """
**👤 Utilisateur** — Ton chronotype, plafond d'étude, objectif hebdo,
trajets, clé API. À remplir une fois. La section 🤖 supporte
**DeepSeek** (modèle deepseek-v4-pro pour l'analyse de PDF et la
génération de planning). Pense à télécharger un backup (section
💾 Sauvegarde & Restauration en bas de la page).

**📚 Bibliothèque** — Hiérarchie **📅 Semestre ▸ 🎓 UE ▸ 📘 Matière ▸ 📑 Chapitre**.
Importe un ou plusieurs PDFs → DeepSeek détecte les chapitres.
Dans l'onglet ⚙️ Gérer, crée tes Semestres, UE et Matières.
"""
        )

    with st.expander("📅 Ma semaine (saisie hebdo)", expanded=False):
        st.markdown(
            """
**📖 Études** — Sélectionne matières et chapitres. Indicateurs visuels
(🔥 révisions dues, 📑 nouveaux, 📊 maîtrise). Pré-sélection auto le
lundi. Bandeau live : ta charge vs objectif hebdo.

**🥊 Sport · 🛒 Courses · 🎯 Projets · 🌱 Dev perso · 🍹 Social · 🧹 Intendance · ⚖️ Ajustements**
— Une section par catégorie de vie.

**💼 Jobs & Travail** — Horaires fixes (alternance, job). L'IA bloque
ces créneaux.

**📸 Import Photo (IA)** — Photo de ton emploi du temps papier →
l'IA extrait les créneaux.
"""
        )

    with st.expander("⚡ Action", expanded=False):
        st.markdown(
            """
**✨ Génération du planning** — L'IA construit ton planning. Sélecteur
« cette semaine / semaine prochaine ». Check-in biomécanique (impacte
le plafond). Bouton **🔁 Intégrer mes nouveautés** si ajouts après
génération.

**📊 Suivi quotidien** — Valide tes tâches (✅ / ⚠️ / ❌). Bilan
auto du jour + semaine, comparaison à ton objectif.

**🧠 Salle d'étude** — Fiche IA, QCM auto, quiz corrigé, **Feynman
audio** (tu expliques à voix haute, l'IA évalue).

**🏆 Achievements** — Badges débloqués et à venir.

**🎯 Objectifs** — Objectifs long terme (« 15 au partiel d'algèbre »).
DeepSeek propose une stratégie qui booste les bons chapitres.
"""
        )

    with st.expander("📈 Bilan", expanded=False):
        st.markdown(
            """
**📈 Tableau de bord** — XP, niveau, streak, planning visuel, KPIs,
graphes de maîtrise par matière, donut de répartition du temps.

**🧠 Révisions (Méthode des J)** — Distribution par boîte Leitner,
calendrier 4 semaines, projection long terme, détection de conflits
et lissage automatique.
"""
        )


# ---------------------------------------------------------------------------
# Section 3 — Cas d'usage (avec expand/collapse all)
# ---------------------------------------------------------------------------
def _render_workflows() -> None:
    st.header("🔁 Cas d'usage typiques")

    # Boutons expand/collapse
    c1, c2 = st.columns(2)
    with c1:
        if st.button("📂 Tout déplier", width="stretch", key="wf_expand"):
            st.session_state["wf_all"] = True
            st.rerun()
    with c2:
        if st.button("📁 Tout replier", width="stretch", key="wf_collapse"):
            st.session_state["wf_all"] = False
            st.rerun()

    expanded = st.session_state.get("wf_all", False)

    with st.expander("📆 Lundi matin — préparer ma semaine", expanded=expanded):
        st.markdown(
            """
1. **📖 Études** — les matières avec révisions dues sont pré-cochées.
2. Décoche/ajoute des chapitres. Vérifie le bandeau live (charge ≤ objectif).
3. **✨ Génération du planning** → fais ton check-in du jour.
4. Optionnel : consigne libre (« jeudi finir avant 18h »).
5. Clique **🚀 Générer le planning**.
6. Valide tes tâches dans **📊 Suivi quotidien** pendant la semaine.
"""
        )

    with st.expander("📆 Dimanche soir — préparer la semaine prochaine", expanded=expanded):
        st.markdown(
            """
1. **📖 Études** → sélecteur en haut sur **📆 Semaine prochaine**.
2. Sélectionne tes matières.
3. **✨ Génération du planning** → le sélecteur est déjà synchronisé.
4. Génère. Lundi matin, tout est prêt.
"""
        )

    with st.expander("➕ J'ai ajouté un chapitre APRÈS avoir généré", expanded=expanded):
        st.markdown(
            """
1. **📚 Bibliothèque** → importe le PDF.
2. **📖 Études** → coche le nouveau chapitre.
3. **✨ Génération du planning** → un bandeau bleu apparaît :
   *« ➕ 1 nouveauté détectée »*. Clique **🔁 Intégrer mes nouveautés**.
4. L'IA ajoute le chapitre dans les créneaux libres, **sans toucher**
   aux tâches déjà validées.
"""
        )

    with st.expander("⚠️ Plusieurs chapitres tombent le même jour", expanded=expanded):
        st.markdown(
            """
Sur **🧠 Révisions**, plusieurs chapitres d'une même matière tombent
le même jour (import en lot) ?

1. **📈 Projection long terme**.
2. Si conflits détectés → bandeau jaune.
3. Clique **🪄 Lisser automatiquement**.
4. L'algo décale vers les jours libres dans [J, J+3].
"""
        )

    with st.expander("🎯 J'ai un partiel dans 3 semaines", expanded=expanded):
        st.markdown(
            """
1. **Action → 🎯 Objectifs → ➕ Nouvel objectif**.
2. Renseigne : titre, matière, note cible, date.
3. Clique **🧠 Demander une stratégie à l'IA**.
4. L'IA analyse ton état et propose des pondérations par chapitre.
   **Adopte** la stratégie → appliquée dans tous tes futurs plannings.
"""
        )

    with st.expander("💾 Comment sécuriser mes données ?", expanded=expanded):
        st.markdown(
            """
Streamlit Cloud peut perdre tes données si l'instance est recréée.
**Télécharge une sauvegarde chaque semaine** (le dimanche soir par ex.) :

1. **👤 Utilisateur → 💾 Sauvegarde & Restauration**.
2. Clique **💾 Télécharger** → tu reçois un `.zip` horodaté
   (DB + PDFs).
3. Range-le sur ton ordi ou Drive.

Pour restaurer : upload le zip et confirme. Tout est rétabli.
"""
        )


# ---------------------------------------------------------------------------
# Section 4 — FAQ (avec expand/collapse all)
# ---------------------------------------------------------------------------
def _render_faq() -> None:
    st.header("💬 FAQ rapide")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("📂 Tout déplier", width="stretch", key="faq_expand"):
            st.session_state["faq_all"] = True
            st.rerun()
    with c2:
        if st.button("📁 Tout replier", width="stretch", key="faq_collapse"):
            st.session_state["faq_all"] = False
            st.rerun()

    expanded = st.session_state.get("faq_all", False)

    with st.expander("Pourquoi le planning ne change pas quand je clique « Régénérer » ?", expanded=expanded):
        st.markdown(
            """
Il change ! Mais comme tes contraintes sont identiques, DeepSeek
sort un planning souvent proche. Vérifie le **bandeau vert** (« 🔄 Planning
régénéré à HH:MM ») et la **stratégie de l'IA** — elle est régénérée à
chaque fois.
"""
        )

    with st.expander("Mon plafond d'étude est-il fixe ?", expanded=expanded):
        st.markdown(
            """
Non. Il est **modulé automatiquement** par ton check-in biomécanique :
fatigue > 7/10 ou charge mentale > 7/10 → plafond réduit de **30 %**
pour ce jour-là. L'app respecte ton état réel.
"""
        )

    with st.expander("Qu'est-ce que la « tolérance ±3 jours » ?", expanded=expanded):
        st.markdown(
            """
Quand Leitner dit qu'un chapitre doit être revu mardi 25, mais que ce
jour est saturé, l'app peut décaler dans [lundi 24, vendredi 28].
**3 jours de tolérance** est pédagogiquement acceptable et évite les
surcharges.
"""
        )

    with st.expander("Pourquoi mes nouveaux chapitres tombent tous le même jour ?", expanded=expanded):
        st.markdown(
            """
Import en lot → tous initialisés à J1 (= +1 jour). C'est l'algo Leitner
standard. Pour étaler : **🪄 Lisser automatiquement** sur la page
Révisions, ou laisse l'IA répartir à la prochaine génération.
"""
        )

    with st.expander("Quel modèle d'IA est utilisé ?", expanded=expanded):
        st.markdown(
            """
L'app utilise **DeepSeek-V4-Pro**, un modèle de raisonnement très
profond, sélectionné pour sa qualité d'analyse et la précision de ses
plannings. Il excelle pour :

- L'**analyse de PDF** (détection des chapitres dans tes cours).
- La **génération de planning** (répartition intelligente de ta charge).
- Les **stratégies d'objectifs** (plan d'action pour tes partiels).
- Les **corrections détaillées** (QCM, fiches, méthode Feynman audio).

Tu configures ta clé API une seule fois dans **👤 Utilisateur → 🤖 Paramètres IA**.
Elle reste chiffrée (Fernet AES-128) dans la base de données.
"""
        )

    with st.expander("C'est quoi la différence entre UE et Matière ?", expanded=expanded):
        st.markdown(
            """
- **📅 Semestre** : regroupe tes UE (ex: « Semestre 5 »).
- **🎓 UE** (Unité d'Enseignement) : porte les crédits ECTS
  (ex: « Mathématiques », 6 ECTS).
- **📘 Matière** : sous-discipline d'une UE (ex: « Algèbre »,
  « Analyse »).
- **📑 Chapitre** : granularité de travail et de révision.

Une UE peut exister sans matière, une matière sans UE. Les Semestres
sont optionnels — tu peux organiser comme tu veux.
"""
        )


# ---------------------------------------------------------------------------
# Section 5 — Raccourcis & astuces
# ---------------------------------------------------------------------------
def _render_tips() -> None:
    st.header("💡 Astuces & raccourcis")

    tips = [
        ("🎯", "Objectif hebdo", "Définis un objectif réaliste dans ton profil. L'IA répartit "
         "tes heures sur 7 jours sans dépasser ton plafond journalier."),
        ("🔄", "Intégrer les nouveautés", "Ajouté un PDF après génération ? Pas besoin de tout "
         "regénérer — clique « 🔁 Intégrer mes nouveautés » dans Génération du planning."),
        ("💾", "Backup = tranquillité", "Télécharge un backup chaque dimanche. 30 secondes qui "
         "peuvent sauver des semaines de progression Leitner et d'XP."),
        ("🧠", "Salle d'étude", "Accessible depuis la Bibliothèque (bouton 🧠 sur chaque chapitre) "
         "OU depuis la sidebar. Idéal pour réviser activement un chapitre."),
        ("📸", "Import photo", "Si ton prof donne un EDT papier, prends-le en photo → "
         "l'IA extrait les créneaux automatiquement."),
        ("📊", "Check-in quotidien", "Plus tu es honnête sur ta fatigue/charge mentale, "
         "plus l'IA ajuste intelligemment ton planning. 10 secondes par jour."),
    ]

    for emoji, titre, desc in tips:
        st.markdown(
            f"<div style='display:flex; gap:12px; align-items:flex-start; "
            f"padding:8px 0;'>"
            f"<div style='font-size:1.5rem; min-width:32px; text-align:center;'>{emoji}</div>"
            f"<div><b>{titre}</b><br><span style='color:#6b7280; font-size:0.9rem;'>{desc}</span></div>"
            f"</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Entrypoint — organisé en tabs
# ---------------------------------------------------------------------------
def render() -> None:
    _render_intro()
    st.divider()

    tab_guide, tab_workflows, tab_faq, tab_tips = st.tabs([
        "📖 Guide", "🔁 Cas d'usage", "💬 FAQ", "💡 Astuces"
    ])

    with tab_guide:
        _render_methode_des_j()
        st.divider()
        _render_navigation()

    with tab_workflows:
        _render_workflows()

    with tab_faq:
        _render_faq()

    with tab_tips:
        _render_tips()




if __name__ == "__main__":
    render()
