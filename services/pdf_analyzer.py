"""Service d'**analyse de PDF** : extraction du texte et appel à Gemini pour
obtenir un découpage en chapitres + métadonnées pédagogiques.

Pipeline complet :

1. :func:`extract_text`        — extrait le texte du PDF (pdfplumber pour le
                                 texte, PyMuPDF pour les métadonnées et la
                                 table des matières), tronque si nécessaire.
2. :func:`build_analysis_prompt` — construit le prompt structuré pour Gemini.
3. :func:`analyze_pdf`         — orchestre l'appel Gemini, parse le JSON.
4. :func:`apply_analysis_to_course` — persiste l'analyse en base et crée
                                       les enregistrements ``Chapitre``.

Tous les appels Gemini utilisent le nouveau SDK ``google-genai`` (l'ancien
``google-generativeai`` est déprécié depuis Gemini 2.0).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from datetime import datetime

from sqlalchemy.orm import Session

from database import Chapitre, Matiere
from services.gemini_utils import gemini_call_with_retry

# ---------------------------------------------------------------------------
# Imports optionnels — l'app reste importable même si ces paquets manquent.
# Les fonctions concernées lèveront une erreur claire au moment de leur appel.
# ---------------------------------------------------------------------------
try:
    import pdfplumber  # type: ignore
except ImportError:  # pragma: no cover
    pdfplumber = None  # type: ignore

try:
    import fitz  # PyMuPDF  # type: ignore
except ImportError:  # pragma: no cover
    fitz = None  # type: ignore


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
# Budget de caractères envoyés à Gemini. ~50 k tokens, bien sous la limite
# de 1 M tokens de Gemini 2.5 Flash, et économique en facturation.
MAX_PROMPT_CHARS = 200_000

# Types de contenu autorisés dans le JSON renvoyé par Gemini.
TYPES_CONTENU_VALIDES = {"theorie", "exercices", "exemples", "mixte"}


# ---------------------------------------------------------------------------
# Structure de données : résultat d'extraction
# ---------------------------------------------------------------------------
@dataclass
class PdfExtraction:
    """Résultat de l'extraction d'un PDF."""

    num_pages: int
    pages_text: list[str]   # texte par page (indexé à partir de 0)
    title: str              # titre extrait des métadonnées (souvent vide)
    toc: list[dict[str, Any]] = field(default_factory=list)
    # toc : [{niveau: int, titre: str, page: int}, ...]
    char_count: int = 0
    truncated: bool = False
    text_for_prompt: str = ""

    @property
    def is_likely_scanned(self) -> bool:
        """Heuristique : si la moyenne par page < 50 caractères, le PDF
        est probablement un scan (OCR non géré ici)."""
        if self.num_pages == 0:
            return False
        avg = self.char_count / self.num_pages
        return avg < 50


# ---------------------------------------------------------------------------
# 1. Extraction du texte
# ---------------------------------------------------------------------------
def extract_text(pdf_path: str | Path) -> PdfExtraction:
    """Extrait le texte et les métadonnées d'un PDF.

    Args:
        pdf_path: chemin vers le fichier PDF.

    Raises:
        FileNotFoundError: si le fichier n'existe pas.
        RuntimeError: si les paquets requis ne sont pas installés.
        ValueError: si le PDF est illisible.
    """
    if pdfplumber is None or fitz is None:
        raise RuntimeError(
            "Les paquets `pdfplumber` et `PyMuPDF` sont requis. "
            "Lance : `pip install pdfplumber PyMuPDF`"
        )

    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF introuvable : {path}")

    # --- Métadonnées + table des matières via PyMuPDF -------------------
    title = ""
    toc: list[dict[str, Any]] = []
    try:
        doc = fitz.open(str(path))
        try:
            meta = doc.metadata or {}
            title = (meta.get("title") or "").strip()
            # get_toc() retourne [[niveau, titre, page], ...]
            toc_raw = doc.get_toc() or []
            toc = [
                {
                    "niveau": int(item[0]),
                    "titre": (item[1] or "").strip(),
                    "page": int(item[2]),
                }
                for item in toc_raw
                if len(item) >= 3
            ]
        finally:
            doc.close()
    except Exception as exc:  # noqa: BLE001
        # On poursuit même si les métadonnées sont inaccessibles.
        print(f"[pdf_analyzer] Avertissement : métadonnées illisibles ({exc})")

    # --- Texte par page via pdfplumber ----------------------------------
    pages_text: list[str] = []
    try:
        with pdfplumber.open(str(path)) as plumber_doc:
            for page in plumber_doc.pages:
                try:
                    txt = page.extract_text() or ""
                except Exception:  # noqa: BLE001
                    txt = ""
                pages_text.append(txt)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Impossible de lire le PDF : {exc}") from exc

    num_pages = len(pages_text)
    char_count = sum(len(t) for t in pages_text)

    text_for_prompt, truncated = _build_text_for_prompt(pages_text)

    return PdfExtraction(
        num_pages=num_pages,
        pages_text=pages_text,
        title=title,
        toc=toc,
        char_count=char_count,
        truncated=truncated,
        text_for_prompt=text_for_prompt,
    )


def _build_text_for_prompt(pages_text: list[str]) -> tuple[str, bool]:
    """Construit le texte à envoyer à Gemini, avec marqueurs de page.

    Si le total dépasse :data:`MAX_PROMPT_CHARS`, garde le début, le milieu et
    la fin (les chapitres de plus de 200 k caractères sont rares — par
    sécurité on ne coupe jamais brutalement la fin).
    """
    parts = [
        f"--- PAGE {i + 1} ---\n{txt}"
        for i, txt in enumerate(pages_text)
        if txt.strip()
    ]
    full = "\n\n".join(parts)
    total = len(full)

    if total <= MAX_PROMPT_CHARS:
        return full, False

    # Tronqué : 40 % début + 20 % milieu + 40 % fin.
    n_start = int(MAX_PROMPT_CHARS * 0.4)
    n_middle = int(MAX_PROMPT_CHARS * 0.2)
    n_end = MAX_PROMPT_CHARS - n_start - n_middle

    middle_center = total // 2
    middle_start = middle_center - n_middle // 2

    sep = "\n\n[…contenu omis pour respecter le budget de tokens…]\n\n"
    truncated_text = (
        full[:n_start]
        + sep
        + full[middle_start : middle_start + n_middle]
        + sep
        + full[total - n_end :]
    )
    return truncated_text, True


# ---------------------------------------------------------------------------
# 2. Construction du prompt
# ---------------------------------------------------------------------------
def build_analysis_prompt(
    extraction: PdfExtraction,
    cours_nom: str,
    matiere: str = "",
) -> str:
    """Construit le prompt structuré envoyé à Gemini."""
    toc_section = ""
    if extraction.toc:
        # On limite à 80 entrées pour ne pas inonder le prompt.
        toc_lines = []
        for entry in extraction.toc[:80]:
            indent = "  " * max(0, entry["niveau"] - 1)
            toc_lines.append(f"{indent}- p.{entry['page']} {entry['titre']}")
        toc_section = (
            "\n\nTABLE DES MATIÈRES (extraite directement du PDF) :\n"
            + "\n".join(toc_lines)
        )

    truncated_warning = ""
    if extraction.truncated:
        truncated_warning = (
            "\n\n⚠️ Le document a été tronqué pour respecter le budget de tokens. "
            "Tu vois le début, un extrait du milieu et la fin uniquement. "
            "Tiens compte de cette troncature dans ton estimation du temps total."
        )

    scanned_warning = ""
    if extraction.is_likely_scanned and extraction.char_count > 0:
        scanned_warning = (
            "\n\n⚠️ Le PDF semble être un document scanné (très peu de texte extractible). "
            "Ton analyse sera nécessairement limitée."
        )

    matiere_line = f"MATIÈRE : {matiere}\n" if matiere else ""

    return f"""Tu es un expert pédagogique en sciences cognitives et en méthodologie d'apprentissage.
Analyse ce cours universitaire et produis un JSON structuré.

COURS : {cours_nom}
{matiere_line}NOMBRE DE PAGES : {extraction.num_pages}{toc_section}{truncated_warning}{scanned_warning}

CONTENU DU PDF :
\"\"\"
{extraction.text_for_prompt}
\"\"\"

RETOURNE UNIQUEMENT un JSON valide au format suivant (pas de markdown, pas de préambule, pas de commentaires) :

{{
  "chapitres": [
    {{
      "numero": 1,
      "titre": "Titre exact du chapitre",
      "pages": "1-15",
      "densite": 3,
      "type_contenu": "theorie"
    }}
  ],
  "mots_cles": ["concept1", "concept2", "concept3", "..."],
  "temps_total_estime_h": 12.5,
  "resume_100_mots": "Résumé synthétique du cours en environ 100 mots.",
  "conseils_methode": "Méthode de révision recommandée pour ce type de cours, en 2 ou 3 phrases."
}}

CONSIGNES IMPÉRATIVES :
- Si une table des matières est fournie ci-dessus, utilise-la EN PRIORITÉ pour identifier les chapitres ; ne réinvente pas un découpage.
- Sinon, identifie les chapitres par leurs titres dans le texte (titres en majuscules, numérotation, ou changements de section).
- "densite" : entier de 1 à 5. 1 = très accessible (exemples, illustrations, peu de théorie). 5 = très dense (théorie pure, formules, démonstrations).
- "type_contenu" doit être EXACTEMENT l'un de : "theorie", "exercices", "exemples", "mixte".
- "mots_cles" : exactement 10 concepts clés représentatifs du cours.
- "temps_total_estime_h" : estimation HONNÊTE et RÉALISTE pour MAÎTRISER tout le cours (lecture + compréhension + fiches + exercices). Un cours de 100 pages denses prend typiquement 25 à 45 h, un cours de 30 pages d'exemples 6 à 12 h.
- "resume_100_mots" : entre 80 et 120 mots.
- "conseils_methode" : adapté à la nature du cours (cours théorique → privilégier fiches + relecture espacée ; cours appliqué → privilégier exercices).
- AUCUN TEXTE en dehors du JSON. Le JSON doit être parsable directement.
"""


# ---------------------------------------------------------------------------
# 3. Appel à Gemini + parsing robuste
# ---------------------------------------------------------------------------
def analyze_pdf(
    pdf_path: str | Path,
    cours_nom: str,
    matiere: str,
    api_key: str,
    model: str = "gemini-2.5-flash",
) -> dict[str, Any]:
    """Pipeline complet : extraction + analyse Gemini.

    Returns:
        Le dict JSON validé renvoyé par Gemini, augmenté de quelques
        métadonnées d'extraction (``_meta``).

    Raises:
        RuntimeError: paquets manquants.
        ValueError: JSON malformé ou réponse vide.
        Exception: toute erreur réseau / auth de Gemini est propagée.
    """
    if not api_key or not api_key.strip():
        raise ValueError("Clé API Gemini manquante.")

    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Package `google-genai` non installé. Lance `pip install google-genai`."
        ) from exc

    extraction = extract_text(pdf_path)
    prompt = build_analysis_prompt(extraction, cours_nom, matiere)

    client = genai.Client(api_key=api_key.strip())
    response = gemini_call_with_retry(
        lambda: client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3,
            ),
        )
    )

    text = getattr(response, "text", "") or ""
    if not text.strip():
        # Tente de remonter la raison (safety filter, etc.).
        feedback = getattr(response, "prompt_feedback", None)
        raise ValueError(
            f"Réponse vide de Gemini. prompt_feedback = {feedback!r}"
        )

    analysis = parse_gemini_json(text)
    analysis = _validate_and_normalize(analysis)

    # Métadonnées utiles pour le module Bibliothèque (affichage UI).
    analysis["_meta"] = {
        "num_pages": extraction.num_pages,
        "char_count": extraction.char_count,
        "truncated": extraction.truncated,
        "toc_detected": len(extraction.toc) > 0,
    }
    return analysis


def parse_gemini_json(text: str) -> dict[str, Any]:
    """Parse robuste de la réponse Gemini.

    Tente, dans l'ordre :
      1. Parse direct
      2. Strip des blocs markdown ``` ```json ``` ```
      3. Extraction du premier objet ``{...}`` via regex
    """
    if not text or not text.strip():
        raise ValueError("Réponse vide.")

    s = text.strip()

    # 1. Parse direct (cas response_mime_type="application/json" qui marche)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # 2. Strip des blocs ``` ```json ... ``` ```
    if s.startswith("```"):
        lines = s.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            s = stripped  # on continue avec ce qu'on a

    # 3. Extraction du premier objet JSON via regex (greedy)
    match = re.search(r"\{.*\}", s, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"JSON malformé : {exc}. Début de la réponse : {s[:200]!r}"
            ) from exc

    raise ValueError(
        f"Aucun JSON trouvé dans la réponse. Reçu : {s[:200]!r}"
    )


def _validate_and_normalize(analysis: dict[str, Any]) -> dict[str, Any]:
    """Vérifie la présence des clés attendues, normalise les types et corrige
    les écarts mineurs (numéros manquants, types_contenu inconnus, etc.)."""
    if not isinstance(analysis, dict):
        raise ValueError(f"Le JSON racine doit être un objet, reçu : {type(analysis).__name__}")

    # --- chapitres ---
    chapitres = analysis.get("chapitres") or []
    if not isinstance(chapitres, list):
        raise ValueError("`chapitres` doit être une liste.")

    normalized_chapitres = []
    for i, ch in enumerate(chapitres, start=1):
        if not isinstance(ch, dict):
            continue
        numero = ch.get("numero")
        try:
            numero = int(numero) if numero is not None else i
        except (TypeError, ValueError):
            numero = i
        titre = str(ch.get("titre") or f"Chapitre {numero}").strip()
        pages = str(ch.get("pages") or "").strip()
        try:
            densite = int(ch.get("densite", 3))
        except (TypeError, ValueError):
            densite = 3
        densite = max(1, min(5, densite))
        type_contenu = str(ch.get("type_contenu") or "mixte").strip().lower()
        if type_contenu not in TYPES_CONTENU_VALIDES:
            type_contenu = "mixte"

        normalized_chapitres.append({
            "numero": numero,
            "titre": titre,
            "pages": pages,
            "densite": densite,
            "type_contenu": type_contenu,
        })
    analysis["chapitres"] = normalized_chapitres

    # --- mots_cles ---
    mots = analysis.get("mots_cles") or []
    if not isinstance(mots, list):
        mots = []
    analysis["mots_cles"] = [str(m).strip() for m in mots if str(m).strip()][:20]

    # --- temps_total_estime_h ---
    try:
        analysis["temps_total_estime_h"] = float(analysis.get("temps_total_estime_h", 0.0))
    except (TypeError, ValueError):
        analysis["temps_total_estime_h"] = 0.0

    # --- resume_100_mots ---
    analysis["resume_100_mots"] = str(analysis.get("resume_100_mots") or "").strip()

    # --- conseils_methode ---
    analysis["conseils_methode"] = str(analysis.get("conseils_methode") or "").strip()

    return analysis


# ---------------------------------------------------------------------------
# 4. Persistance en base
# ---------------------------------------------------------------------------
def apply_analysis_to_matiere(
    session: Session,
    matiere_id: int,
    analysis: dict[str, Any],
    pdf_path: str,
    pdf_label: str = "",
) -> list[int]:
    """Crée les chapitres détectés par Gemini, rattachés à une Matière.

    Refonte bibliothèque : remplace ``apply_analysis_to_course``. Le PDF
    qui a généré l'analyse est référencé dans le champ ``pdfs`` de chaque
    chapitre créé.

    Args:
        session: session SQLAlchemy ouverte.
        matiere_id: id de la matière de rattachement (obligatoire).
        analysis: dict normalisé renvoyé par ``analyze_pdf``.
        pdf_path: chemin (relatif au repo) du PDF analysé.
        pdf_label: libellé optionnel du document (ex. "Cours magistral",
            "Polycopié"). Vide si non précisé.

    Returns:
        Liste des IDs des chapitres créés.
    """
    matiere = session.query(Matiere).filter_by(id=matiere_id).one()
    chapitres_data = analysis.get("chapitres", [])

    pdf_entry = {
        "path": pdf_path,
        "label": pdf_label or "Document",
        "uploaded_at": datetime.utcnow().isoformat(timespec="seconds"),
    }

    new_ids: list[int] = []
    for ch_data in chapitres_data:
        chap = Chapitre(
            matiere_id=matiere.id,
            numero=int(ch_data["numero"]),
            titre=ch_data["titre"],
            maitrise_pct=0,
            type_travail_restant="premiere_lecture",
            temps_estime_h=_estimate_chapter_time(ch_data, analysis),
            pdfs=[pdf_entry],
        )
        session.add(chap)
        session.flush()
        new_ids.append(chap.id)

    return new_ids


def _estimate_chapter_time(
    ch_data: dict[str, Any],
    analysis: dict[str, Any],
) -> float:
    """Répartit le temps total proportionnellement à la densité du chapitre.

    Un chapitre de densité 5 prend ~1.67× le temps d'un chapitre de densité 3
    (à nombre de chapitres égal). Si le total est manquant, retombe sur 0.
    """
    total = float(analysis.get("temps_total_estime_h", 0.0))
    chapitres = analysis.get("chapitres", [])
    if total <= 0 or not chapitres:
        return 0.0

    densites = [max(1, min(5, int(c.get("densite", 3)))) for c in chapitres]
    total_weight = sum(densites)
    if total_weight == 0:
        return round(total / len(chapitres), 1)

    weight = max(1, min(5, int(ch_data.get("densite", 3))))
    return round(total * weight / total_weight, 1)


__all__ = [
    "PdfExtraction",
    "extract_text",
    "build_analysis_prompt",
    "analyze_pdf",
    "parse_gemini_json",
    "apply_analysis_to_matiere",
    "MAX_PROMPT_CHARS",
    "TYPES_CONTENU_VALIDES",
]
