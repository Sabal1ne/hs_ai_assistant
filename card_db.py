"""
card_db.py
----------
Real card data from the HearthSim ``hearthstone`` / ``hearthstone_data``
packages (https://github.com/HearthSim/hsdata).

This module provides a thin layer between the HearthSim card database and the
simulator's ``Card`` / ``Mechanic`` / ``CardType`` types.

Public API
~~~~~~~~~~
``is_available() -> bool``
    Return ``True`` when the optional ``hearthstone`` / ``hearthstone_data``
    packages are installed.

``get_cards_for_class(class_name, include_neutral) -> List[Dict]``
    Return all collectible cards for a hero class as plain dicts compatible
    with ``hs_simulator.Card(**d)``.  Neutral cards can optionally be included.
    The result is cached after the first call for each class.

``make_card(card_dict) -> Card``
    Convenience wrapper: build an ``hs_simulator.Card`` from a dict returned
    by :func:`get_cards_for_class`.

``ALL_CLASS_NAMES``
    Tuple of recognised hero-class strings (matches the keys used by
    ``hs_mcts.py``).

If the optional packages are not installed this module degrades gracefully:
``is_available()`` returns ``False`` and ``get_cards_for_class()`` returns an
empty list so callers can fall back to their own data.
"""

from __future__ import annotations

import functools
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Optional dependency probe
# ---------------------------------------------------------------------------

_HS_AVAILABLE: Optional[bool] = None  # None = not yet checked


def is_available() -> bool:
    """Return ``True`` when hearthstone + hearthstone_data are importable."""
    global _HS_AVAILABLE
    if _HS_AVAILABLE is None:
        try:
            from hearthstone import cardxml  # noqa: F401
            from hearthstone_data import get_carddefs_path  # noqa: F401
            _HS_AVAILABLE = True
        except ImportError:
            _HS_AVAILABLE = False
    return _HS_AVAILABLE


# ---------------------------------------------------------------------------
# Class name ↔ CardClass mapping
# ---------------------------------------------------------------------------

ALL_CLASS_NAMES = (
    "Mage",
    "Warrior",
    "Paladin",
    "Hunter",
    "Rogue",
    "Druid",
    "Warlock",
    "Shaman",
    "Priest",
    "Demon Hunter",
    "Death Knight",
    "Neutral",
)

# String name → hearthstone.enums.CardClass member name
_CLASS_NAME_TO_ENUM: Dict[str, str] = {
    "Mage": "MAGE",
    "Warrior": "WARRIOR",
    "Paladin": "PALADIN",
    "Hunter": "HUNTER",
    "Rogue": "ROGUE",
    "Druid": "DRUID",
    "Warlock": "WARLOCK",
    "Shaman": "SHAMAN",
    "Priest": "PRIEST",
    "Demon Hunter": "DEMONHUNTER",
    "Death Knight": "DEATHKNIGHT",
    "Neutral": "NEUTRAL",
}

# ---------------------------------------------------------------------------
# Mechanic tag mapping
# ---------------------------------------------------------------------------

# GameTag attribute name → hs_simulator Mechanic string
_MECHANIC_MAP: Dict[str, str] = {
    "TAUNT": "TAUNT",
    "CHARGE": "CHARGE",
    "DIVINE_SHIELD": "DIVINE_SHIELD",
    "BATTLECRY": "BATTLECRY",
    "DEATHRATTLE": "DEATHRATTLE",
    "POISONOUS": "POISONOUS",
    "WINDFURY": "WINDFURY",
    "LIFESTEAL": "LIFESTEAL",
}

# CardType int value → simulator CardType string
_CARDTYPE_MAP: Dict[int, str] = {
    4: "MINION",
    5: "SPELL",
    7: "WEAPON",
}

# ---------------------------------------------------------------------------
# Internal: lazy-loaded database
# ---------------------------------------------------------------------------

_DB: Optional[Any] = None  # hearthstone CardXML db


def _load_db() -> Any:
    """Load and cache the card database (no-op if already loaded)."""
    global _DB
    if _DB is None:
        from hearthstone import cardxml
        _DB, _ = cardxml.load()
    return _DB


# ---------------------------------------------------------------------------
# Internal: convert a CardXML object to our dict format
# ---------------------------------------------------------------------------

def _card_to_dict(card: Any) -> Dict[str, Any]:
    """
    Convert a hearthstone CardXML card object to a plain dict.

    The returned dict has the same keys used by ``hs_simulator.Card``:
    ``id``, ``name``, ``cost``, ``attack``, ``health``, ``card_type``,
    ``mechanics``.
    """
    from hearthstone.enums import GameTag

    mechanics: set = set()
    for tag_name, sim_name in _MECHANIC_MAP.items():
        tag = GameTag[tag_name]
        if card.tags.get(tag):
            mechanics.add(sim_name)

    card_type_str = _CARDTYPE_MAP.get(int(card.type), "SPELL")

    return {
        "id": card.id,
        "name": card.name,
        "cost": card.cost,
        "attack": card.atk,
        "health": card.health,
        "card_type": card_type_str,
        "mechanics": mechanics,
    }


# ---------------------------------------------------------------------------
# Cache for per-class card lists
# ---------------------------------------------------------------------------

_CLASS_CACHE: Dict[str, List[Dict[str, Any]]] = {}


def get_cards_for_class(
    class_name: str,
    include_neutral: bool = True,
) -> List[Dict[str, Any]]:
    """
    Return all collectible playable cards for *class_name* as dicts.

    Parameters
    ----------
    class_name:
        Hero class name, e.g. ``"Mage"``.  Use ``"Neutral"`` to get only
        neutral cards.  Unknown names return an empty list.
    include_neutral:
        When ``True`` (default), neutral cards are appended to the
        class-specific cards.  Ignored when *class_name* is ``"Neutral"``.

    Returns
    -------
    List of card dicts with keys matching ``hs_simulator.Card`` constructor
    parameters: ``id``, ``name``, ``cost``, ``attack``, ``health``,
    ``card_type``, ``mechanics``.

    Notes
    -----
    Results are cached after the first call per (class_name, include_neutral)
    pair.  Pass ``include_neutral=False`` when you need only class-specific
    cards.
    """
    if not is_available():
        return []

    cache_key = f"{class_name}|{include_neutral}"
    if cache_key in _CLASS_CACHE:
        return _CLASS_CACHE[cache_key]

    from hearthstone.enums import CardClass, CardType, GameTag

    enum_name = _CLASS_NAME_TO_ENUM.get(class_name)
    if enum_name is None:
        return []

    target_class = CardClass[enum_name]
    db = _load_db()

    # Gather class-specific cards
    result: List[Dict[str, Any]] = []
    neutral_result: List[Dict[str, Any]] = []

    for card in db.values():
        # Only collectible, playable card types
        if not card.tags.get(GameTag.COLLECTIBLE):
            continue
        if int(card.type) not in _CARDTYPE_MAP:
            continue

        classes = card.classes
        if target_class in classes:
            result.append(_card_to_dict(card))
        elif (
            include_neutral
            and class_name != "Neutral"
            and CardClass.NEUTRAL in classes
        ):
            neutral_result.append(_card_to_dict(card))

    combined = result + neutral_result
    _CLASS_CACHE[cache_key] = combined
    return combined


def make_card(card_dict: Dict[str, Any]) -> "Card":
    """
    Build an ``hs_simulator.Card`` from a dict produced by
    :func:`get_cards_for_class`.

    Parameters
    ----------
    card_dict:
        Dict with keys ``id``, ``name``, ``cost``, ``attack``, ``health``,
        ``card_type``, ``mechanics``.

    Returns
    -------
    An initialised ``hs_simulator.Card`` instance.
    """
    from hs_simulator import Card, CardType, Mechanic

    card_type = CardType(card_dict["card_type"])
    mechanics = {Mechanic(m) for m in card_dict.get("mechanics", set())}

    return Card(
        id=card_dict.get("id", ""),
        name=card_dict.get("name", ""),
        cost=card_dict.get("cost", 0),
        attack=card_dict.get("attack", 0),
        health=card_dict.get("health", 1) or 1,  # minions need health > 0
        card_type=card_type,
        mechanics=mechanics,
    )
