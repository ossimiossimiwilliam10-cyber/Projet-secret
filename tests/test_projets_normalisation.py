"""Tests de la normalisation des items dans ``modules.hebdo.projets``.

Le module héberge un helper privé ``_normaliser_item`` qui doit accepter à
la fois les items « natifs » (saisis directement dans l'onglet) et les
items « reportés » (poussés par ``services.report_service`` quand des
tâches non terminées débordent de la semaine précédente).

Sans cette normalisation, le ``st.data_editor`` mélange des colonnes
incompatibles avec son ``column_config`` et plante / affiche du vide
pour les valeurs hors enum.
"""

from __future__ import annotations

from modules.hebdo.projets import PRIORITES, TYPES_PROJET, _normaliser_item


def test_item_natif_reste_intact():
    """Un item saisi normalement par l'utilisateur est conservé tel quel."""
    natif = {
        "titre": "Rendu TD physique",
        "type": "Scolaire (Devoir/Projet)",
        "duree_min": 120,
        "priorite": "Haute",
        "echeance": "Avant vendredi 18h",
    }
    out = _normaliser_item(natif)
    assert out == natif


def test_item_reporte_est_traduit_vers_libelles_attendus():
    """Un item produit par report_service (type_original='etude', clé
    reportee_depuis_semaine_id…) est traduit pour rester compatible avec
    le data_editor de projets.py."""
    reporte = {
        "titre": "Algèbre - Chapitre 3",
        "type_original": "etude",
        "duree_min": 90,
        "reportee_depuis_semaine_id": 12,
        "statut_precedent": "non_fait",
        "commentaire_etudiant": "Plus de temps demain",
        "matiere_id": 5,
        "chapitre_ids": [42],
    }
    out = _normaliser_item(reporte)

    assert out["titre"] == "Algèbre - Chapitre 3"
    # 'etude' doit mapper vers le libellé scolaire visible dans le selectbox.
    assert out["type"] == "Scolaire (Devoir/Projet)"
    assert out["type"] in TYPES_PROJET
    assert out["duree_min"] == 90
    assert out["priorite"] in PRIORITES
    # L'échéance doit signaler le report pour que l'utilisateur le voie.
    assert "Report" in out["echeance"] or "report" in out["echeance"].lower()
    # Aucune clé étrangère ne fuit dans la sortie.
    for key in ("type_original", "reportee_depuis_semaine_id", "matiere_id"):
        assert key not in out


def test_type_inconnu_retombe_sur_default():
    """Si l'item a un ``type`` qui n'est pas dans TYPES_PROJET (donnée
    historique cassée), on retombe sur la première option valide."""
    item = {"titre": "Truc", "type": "valeur_inattendue", "duree_min": 30}
    out = _normaliser_item(item)
    assert out["type"] == TYPES_PROJET[0]


def test_priorite_invalide_retombe_sur_moyenne():
    """Pareil pour ``priorite`` : si la valeur n'est pas dans PRIORITES,
    on retombe sur 'Moyenne' au lieu de casser le SelectboxColumn."""
    item = {"titre": "X", "priorite": "Critique++"}
    assert _normaliser_item(item)["priorite"] == "Moyenne"


def test_libelle_compat_old_format():
    """Certains items historiques peuvent avoir ``libelle`` au lieu de
    ``titre``. On accepte les deux."""
    item = {"libelle": "Vieux format"}
    assert _normaliser_item(item)["titre"] == "Vieux format"
