"""Verrouillage optimiste — protection contre les écrasements multi-onglets.

Scénario : l'utilisateur ouvre le même chapitre dans deux onglets, modifie
les notes dans l'onglet A et sauvegarde, puis modifie les notes dans
l'onglet B (qui a une version stale) et sauvegarde. Sans protection,
les modifications de l'onglet A sont **silencieusement écrasées**.

Le pattern utilisé ici est un *ETag* léger via une colonne ``version`` :

1. À la lecture, on capture la version courante.
2. À l'écriture, on vérifie que la version en base est toujours celle
   qu'on a lue. Sinon → :class:`ConflictError`.
3. En cas de succès, on incrémente la version.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from database import Chapitre

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class ConflictError(RuntimeError):
    """Levée quand une mise à jour optimiste détecte une version stale.

    L'appelant doit recharger les données et proposer à l'utilisateur de
    refaire sa saisie (ou de merger manuellement).
    """

    def __init__(
        self, expected_version: int, actual_version: int, entity: str = "Chapitre"
    ) -> None:
        super().__init__(
            f"{entity} modifié dans un autre onglet "
            f"(version attendue : {expected_version}, actuelle : {actual_version}). "
            f"Recharge la page pour voir les dernières modifications."
        )
        self.expected_version = expected_version
        self.actual_version = actual_version


def update_chapitre_safe(
    session: "Session",
    chapitre_id: int,
    expected_version: int,
    mutate: Callable[[Chapitre], None],
) -> int:
    """Applique ``mutate(chap)`` sur le chapitre, après vérification de version.

    Args:
        session: session SQLAlchemy ouverte.
        chapitre_id: id du chapitre à muter.
        expected_version: version que l'UI avait lue.
        mutate: callback qui applique les mutations sur l'objet Chapitre.

    Returns:
        La nouvelle version (= ancienne + 1) après mutation.

    Raises:
        ConflictError: si la version en base ne correspond pas.
        ValueError: si le chapitre n'existe pas.
    """
    chap = session.get(Chapitre, chapitre_id)
    if chap is None:
        raise ValueError(f"Chapitre #{chapitre_id} introuvable.")

    current = int(chap.version or 1)
    if current != expected_version:
        raise ConflictError(expected_version, current)

    mutate(chap)
    chap.version = current + 1
    return chap.version


__all__ = ["ConflictError", "update_chapitre_safe"]
