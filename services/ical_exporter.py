"""Export d'un planning hebdomadaire au format iCalendar (.ics).

Permet d'importer le planning dans Google Calendar / Apple Calendar /
Samsung Calendar / Outlook → tu retrouves tes tâches sur ton téléphone
avec **notifications natives** (rappel 10 min avant chaque session
d'étude par exemple).

L'UID de chaque VEVENT est stable (`tache-{id}@exocerveau`) pour que :
- Si tu re-imports le .ics après une régénération, les events sont
  **mis à jour** plutôt que dupliqués (comportement standard iCal).
- Tu peux supprimer un planning d'un coup en sélectionnant les events
  d'une semaine.
"""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from database.models import Semaine, Tache

# Catégories iCal par type de tâche — utiles pour les filtres dans les
# applications calendrier qui supportent CATEGORIES.
_TYPE_CATEGORIES: dict[str, str] = {
    "etude":            "Études",
    "sport":            "Sport",
    "courses":          "Courses",
    "projet":           "Projets",
    "dev_perso":        "Dev perso",
    "social":           "Social",
    "intendance":       "Intendance",
    "repas":            "Repas",
    "sommeil":          "Sommeil",
    "transport":        "Transport",
    "cours_presentiel": "Cours",
    "travail":          "Travail",
    "meal_prep":        "Meal prep",
}

# Icônes / emojis par type, pour préfixer le titre.
_TYPE_ICONS: dict[str, str] = {
    "etude":            "📚",
    "sport":            "🏃",
    "courses":          "🛒",
    "projet":           "🎯",
    "dev_perso":        "🌱",
    "social":           "🍹",
    "intendance":       "🧹",
    "repas":            "🍽️",
    "sommeil":          "😴",
    "transport":        "🚇",
    "cours_presentiel": "🎓",
    "travail":          "💼",
    "meal_prep":        "🍱",
}

_JOURS_FR_TO_OFFSET: dict[str, int] = {
    "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3,
    "vendredi": 4, "samedi": 5, "dimanche": 6,
}


def _date_for_jour(date_debut: datetime.date, jour_fr: str) -> datetime.date | None:
    """Convertit un libellé jour (« lundi », « mardi », …) + la date du
    lundi de la semaine en date concrète. None si jour inconnu."""
    offset = _JOURS_FR_TO_OFFSET.get((jour_fr or "").lower())
    if offset is None:
        return None
    return date_debut + datetime.timedelta(days=offset)


def build_ics_for_semaine(
    session: Session,
    semaine_id: int,
    *,
    inclure_obligatoires: bool = True,
) -> bytes:
    """Construit un fichier .ics (bytes UTF-8) pour la semaine donnée.

    Args:
        session: session SQLAlchemy active.
        semaine_id: ID de la semaine à exporter.
        inclure_obligatoires: si False, n'exporte que les tâches libres
            (utile si l'utilisateur veut juste ses sessions d'étude sans
            saturer son calendrier avec sommeil/repas).

    Returns:
        Contenu du fichier .ics prêt à servir à ``st.download_button``.

    Raises:
        ValueError: si la semaine n'existe pas ou si ``icalendar`` n'est
            pas installé.
    """
    try:
        from icalendar import Calendar, Event
    except ImportError as exc:
        raise ValueError(
            "Le package `icalendar` n'est pas installé. "
            "Ajoute-le à requirements.txt."
        ) from exc

    semaine = session.get(Semaine, semaine_id)
    if semaine is None:
        raise ValueError(f"Semaine #{semaine_id} introuvable.")

    cal = Calendar()
    cal.add("prodid", "-//Exocerveau//Planning étudiant IA//FR")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add(
        "x-wr-calname",
        f"Exocerveau S{semaine.numero_semaine:02d}-{semaine.annee}",
    )
    cal.add(
        "x-wr-caldesc",
        f"Planning généré par l'app Exocerveau pour la semaine du "
        f"{semaine.date_debut.strftime('%d/%m/%Y')} au "
        f"{semaine.date_fin.strftime('%d/%m/%Y')}.",
    )

    taches_query = session.query(Tache).filter_by(semaine_id=semaine_id)
    if not inclure_obligatoires:
        taches_query = taches_query.filter(Tache.obligatoire.is_(False))
    taches = taches_query.order_by(Tache.jour, Tache.heure_debut).all()

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    nb_events = 0

    for t in taches:
        d_jour = _date_for_jour(semaine.date_debut, t.jour)
        if d_jour is None or t.heure_debut is None or t.heure_fin is None:
            continue

        debut = datetime.datetime.combine(d_jour, t.heure_debut)
        fin = datetime.datetime.combine(d_jour, t.heure_fin)
        if fin <= debut:
            continue

        evt = Event()
        # UID stable → re-import = update (pas duplication).
        evt.add("uid", f"tache-{t.id}@exocerveau")
        icone = _TYPE_ICONS.get((t.type or "").lower(), "•")
        lock = "🔒 " if t.obligatoire else ""
        evt.add("summary", f"{icone} {lock}{t.titre or 'Tâche'}")

        # Description : justification IA + statut + commentaire étudiant.
        desc_lines: list[str] = []
        if t.justification_ia:
            desc_lines.append(f"💭 {t.justification_ia}")
        if t.commentaire_etudiant:
            desc_lines.append(f"📝 {t.commentaire_etudiant}")
        desc_lines.append(f"Type : {t.type or 'autre'}")
        if t.statut and t.statut != "a_faire":
            desc_lines.append(f"Statut : {t.statut}")
        desc_lines.append("\nGénéré par Exocerveau.")
        evt.add("description", "\n".join(desc_lines))

        evt.add("dtstart", debut)
        evt.add("dtend", fin)
        evt.add("dtstamp", now_utc)
        evt.add("categories", [_TYPE_CATEGORIES.get((t.type or "").lower(), "Autre")])

        # Rappel 10 min avant pour les sessions d'étude (utile sur
        # téléphone). Les obligatoires (sommeil, repas) restent silencieux.
        if (t.type or "").lower() == "etude" and not t.obligatoire:
            from icalendar import Alarm
            alarm = Alarm()
            alarm.add("action", "DISPLAY")
            titre_alarme = t.titre or "session d'étude"
            alarm.add("description", f"Bientôt : {titre_alarme}")
            alarm.add("trigger", datetime.timedelta(minutes=-10))
            evt.add_component(alarm)

        cal.add_component(evt)
        nb_events += 1

    return cal.to_ical()


def make_ics_filename(semaine: Semaine) -> str:
    """Nom de fichier suggéré pour le téléchargement.

    Format : ``exocerveau_S<numero>_<annee>.ics`` — court, lisible,
    utilisable comme nom d'agenda dans la plupart des applis calendrier.
    """
    return f"exocerveau_S{semaine.numero_semaine:02d}_{semaine.annee}.ics"


__all__ = [
    "build_ics_for_semaine",
    "make_ics_filename",
]
