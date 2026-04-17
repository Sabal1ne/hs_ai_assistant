"""
hs_simulator.py
---------------
A lightweight Hearthstone simulator engine designed for MCTS rollouts.

This module does NOT render graphics.  It only simulates board states and
combat so that the MCTS algorithm can evaluate thousands of positions per
second.

Classes
~~~~~~~
Card      – Represents a single card (minion, spell, weapon).
GameState – The complete board state for one game snapshot.

Key methods
~~~~~~~~~~~
GameState.play_card(card_index, target=None)
    Move a card from the hand to the board or apply its effect.

GameState.attack(attacker_index, defender_index)
    Simulate combat between two minions (or a minion and the hero).

Design choices
~~~~~~~~~~~~~~
* ``__slots__`` on both classes to minimise per-object overhead.
* ``copy.copy`` / ``copy.deepcopy`` safe (deep-copy supported for MCTS
  state cloning).
* No external dependencies – pure Python 3.8+.
"""

from __future__ import annotations

import copy
import random
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class CardType(str, Enum):
    MINION = "MINION"
    SPELL = "SPELL"
    WEAPON = "WEAPON"
    HERO_POWER = "HERO_POWER"


class Mechanic(str, Enum):
    TAUNT = "TAUNT"
    CHARGE = "CHARGE"
    DIVINE_SHIELD = "DIVINE_SHIELD"
    BATTLECRY = "BATTLECRY"
    DEATHRATTLE = "DEATHRATTLE"
    POISONOUS = "POISONOUS"
    WINDFURY = "WINDFURY"
    LIFESTEAL = "LIFESTEAL"


# ---------------------------------------------------------------------------
# Deathrattle registry
# ---------------------------------------------------------------------------
# Maps card ID -> callable(board_index, game_state) that applies the effect.
# Users may register custom deathrattles at runtime.

_DEATHRATTLE_REGISTRY: Dict[str, Callable[["GameState", int, str], None]] = {}


def register_deathrattle(
    card_id: str,
    effect: Callable[["GameState", int, str], None],
) -> None:
    """Register a deathrattle effect for *card_id*.

    Parameters
    ----------
    card_id:
        Hearthstone card identifier (e.g. ``"EX1_116"``).
    effect:
        ``effect(game_state, board_index, side)`` – called when the minion
        dies.  *side* is ``"player"`` or ``"enemy"``.
    """
    _DEATHRATTLE_REGISTRY[card_id] = effect


# ---------------------------------------------------------------------------
# Card
# ---------------------------------------------------------------------------

class Card:
    """Represents a single Hearthstone card.

    Attributes
    ----------
    id       : Hearthstone card identifier string (e.g. ``"EX1_298"``).
    name     : Display name.
    cost     : Mana cost.
    attack   : Base attack (minions / weapons).
    health   : Current health (minions) or durability (weapons).
    max_health: Maximum health for this minion.
    card_type: ``CardType`` enum value.
    mechanics: Set of ``Mechanic`` values.
    exhausted: True if the minion has already attacked this turn.
    damage_taken: Accumulated damage this turn (used for divine-shield
                  tracking before actually reducing health).
    """

    __slots__ = (
        "id",
        "name",
        "cost",
        "attack",
        "health",
        "max_health",
        "card_type",
        "mechanics",
        "exhausted",
        "damage_taken",
        "active_divine_shield",
    )

    def __init__(
        self,
        *,
        id: str = "",
        name: str = "",
        cost: int = 0,
        attack: int = 0,
        health: int = 1,
        max_health: Optional[int] = None,
        card_type: CardType = CardType.MINION,
        mechanics: Optional[set] = None,
    ) -> None:
        self.id = id
        self.name = name
        self.cost = cost
        self.attack = attack
        self.health = health
        self.max_health = max_health if max_health is not None else health
        self.card_type = card_type
        self.mechanics = set(mechanics) if mechanics else set()
        # Runtime state – new minions are exhausted unless they have CHARGE
        self.exhausted = Mechanic.CHARGE not in self.mechanics
        self.damage_taken = 0
        self.active_divine_shield = Mechanic.DIVINE_SHIELD in self.mechanics

    # Convenience predicates
    @property
    def has_taunt(self) -> bool:
        return Mechanic.TAUNT in self.mechanics

    @property
    def has_charge(self) -> bool:
        return Mechanic.CHARGE in self.mechanics

    @property
    def has_deathrattle(self) -> bool:
        return Mechanic.DEATHRATTLE in self.mechanics

    @property
    def has_divine_shield(self) -> bool:
        return self.active_divine_shield

    @property
    def is_alive(self) -> bool:
        return self.health > 0

    def __repr__(self) -> str:
        return (
            f"Card(id={self.id!r}, name={self.name!r}, cost={self.cost}, "
            f"attack={self.attack}, health={self.health})"
        )

    def __deepcopy__(self, memo: Dict) -> "Card":
        cls = self.__class__
        new = cls.__new__(cls)
        memo[id(self)] = new
        for slot in self.__slots__:
            val = getattr(self, slot)
            setattr(new, slot, copy.copy(val) if slot == "mechanics" else val)
        return new


# ---------------------------------------------------------------------------
# Hero
# ---------------------------------------------------------------------------

class Hero:
    """Lightweight hero representation."""

    __slots__ = ("name", "hero_class", "health", "max_health", "armor", "attack")

    def __init__(
        self,
        name: str = "",
        hero_class: str = "",
        health: int = 30,
        armor: int = 0,
        attack: int = 0,
    ) -> None:
        self.name = name
        self.hero_class = hero_class
        self.health = health
        self.max_health = health
        self.armor = armor
        self.attack = attack  # temporary weapon attack

    @property
    def effective_health(self) -> int:
        return self.health + self.armor

    @property
    def is_alive(self) -> bool:
        return self.health > 0

    def take_damage(self, amount: int) -> None:
        """Apply *amount* damage, absorbing armor first."""
        if amount <= 0:
            return
        absorbed = min(self.armor, amount)
        self.armor -= absorbed
        self.health -= amount - absorbed

    def __deepcopy__(self, memo: Dict) -> "Hero":
        cls = self.__class__
        new = cls.__new__(cls)
        memo[id(self)] = new
        for slot in self.__slots__:
            setattr(new, slot, getattr(self, slot))
        return new


# ---------------------------------------------------------------------------
# GameState
# ---------------------------------------------------------------------------

class GameState:
    """
    Complete snapshot of a Hearthstone game turn.

    This is the state object consumed by ``hs_mcts.py`` for tree expansion
    and rollouts.  All mutation methods return ``self`` for chaining, except
    where noted.

    Attributes
    ----------
    player_mana     : Mana available to the player this turn.
    player_max_mana : Maximum mana this turn.
    player_overload : Overload locked next turn.
    player_hand     : Cards in the player's hand (list of Card).
    player_board    : Minions the player controls (list of Card, max 7).
    enemy_board     : Minions the opponent controls (list of Card, max 7).
    player_hero     : Player's Hero.
    enemy_hero      : Opponent's Hero.
    turn            : Current turn number.
    is_over         : True once a hero has died.
    winner          : ``"player"``, ``"enemy"``, or ``None``.
    """

    __slots__ = (
        "player_mana",
        "player_max_mana",
        "player_overload",
        "player_hand",
        "player_board",
        "enemy_board",
        "player_hero",
        "enemy_hero",
        "turn",
        "is_over",
        "winner",
    )

    def __init__(
        self,
        *,
        player_mana: int = 0,
        player_max_mana: int = 0,
        player_overload: int = 0,
        player_hand: Optional[List[Card]] = None,
        player_board: Optional[List[Card]] = None,
        enemy_board: Optional[List[Card]] = None,
        player_hero: Optional[Hero] = None,
        enemy_hero: Optional[Hero] = None,
        turn: int = 1,
    ) -> None:
        self.player_mana = player_mana
        self.player_max_mana = player_max_mana
        self.player_overload = player_overload
        self.player_hand: List[Card] = player_hand if player_hand is not None else []
        self.player_board: List[Card] = player_board if player_board is not None else []
        self.enemy_board: List[Card] = enemy_board if enemy_board is not None else []
        self.player_hero: Hero = player_hero if player_hero is not None else Hero()
        self.enemy_hero: Hero = enemy_hero if enemy_hero is not None else Hero()
        self.turn = turn
        self.is_over = False
        self.winner: Optional[str] = None

    # ------------------------------------------------------------------
    # Deep copy (required for MCTS branching)
    # ------------------------------------------------------------------

    def clone(self) -> "GameState":
        """Return a deep copy of this game state."""
        return copy.deepcopy(self)

    # ------------------------------------------------------------------
    # Turn lifecycle
    # ------------------------------------------------------------------

    def begin_turn(self) -> "GameState":
        """Advance to a new player turn: refill mana, un-exhaust minions."""
        self.turn += 1
        # Mana refill (capped at 10), apply last turn's overload
        new_max = min(10, self.player_max_mana + 1)
        self.player_max_mana = new_max
        self.player_mana = max(0, new_max - self.player_overload)
        self.player_overload = 0

        for minion in self.player_board:
            minion.exhausted = False
            minion.damage_taken = 0
        return self

    # ------------------------------------------------------------------
    # Play a card
    # ------------------------------------------------------------------

    def play_card(
        self,
        card_index: int,
        target: Optional[int] = None,
        target_side: str = "enemy",
    ) -> "GameState":
        """
        Play the card at *card_index* from the player's hand.

        Parameters
        ----------
        card_index:
            Index into ``player_hand``.
        target:
            Board index of the target minion (or ``None`` if not required).
        target_side:
            ``"player"`` or ``"enemy"`` – which board *target* refers to.

        Raises
        ------
        ValueError
            If the card index is out of range or not enough mana.
        """
        if card_index < 0 or card_index >= len(self.player_hand):
            raise ValueError(
                f"card_index {card_index} out of range "
                f"(hand size={len(self.player_hand)})"
            )
        card = self.player_hand[card_index]
        if card.cost > self.player_mana:
            raise ValueError(
                f"Not enough mana: need {card.cost}, have {self.player_mana}"
            )

        self.player_mana -= card.cost
        self.player_hand.pop(card_index)

        if card.card_type == CardType.MINION:
            self._summon_minion(card, target, target_side)
        elif card.card_type == CardType.SPELL:
            self._cast_spell(card, target, target_side)
        elif card.card_type == CardType.WEAPON:
            self._equip_weapon(card)

        self._check_deaths()
        return self

    def _summon_minion(
        self,
        card: Card,
        target: Optional[int],
        target_side: str,
    ) -> None:
        if len(self.player_board) >= 7:
            # Board full – card is wasted (simplified; real HS returns it)
            return
        card.exhausted = Mechanic.CHARGE not in card.mechanics
        self.player_board.append(card)

        if Mechanic.BATTLECRY in card.mechanics and target is not None:
            board = self.enemy_board if target_side == "enemy" else self.player_board
            if 0 <= target < len(board):
                self._deal_damage(board[target], card.attack, attacker=card)

    def _cast_spell(
        self,
        card: Card,
        target: Optional[int],
        target_side: str,
    ) -> None:
        """Apply a simplified spell effect: deal *attack* damage to target."""
        if target is not None and card.attack > 0:
            if target == -1:
                # Hero target
                hero = self.enemy_hero if target_side == "enemy" else self.player_hero
                hero.take_damage(card.attack)
            else:
                board = (
                    self.enemy_board if target_side == "enemy" else self.player_board
                )
                if 0 <= target < len(board):
                    self._deal_damage(board[target], card.attack)

    def _equip_weapon(self, card: Card) -> None:
        self.player_hero.attack = card.attack

    # ------------------------------------------------------------------
    # Attack
    # ------------------------------------------------------------------

    def attack(
        self,
        attacker_index: int,
        defender_index: int,
        defender_is_hero: bool = False,
    ) -> "GameState":
        """
        Simulate an attack from a player minion against an enemy target.

        Parameters
        ----------
        attacker_index:
            Index into ``player_board``.
        defender_index:
            Index into ``enemy_board`` **or** ignored when *defender_is_hero*
            is ``True``.
        defender_is_hero:
            When ``True`` the attacker hits the enemy hero directly.

        Raises
        ------
        ValueError
            When the attacker is exhausted, or a Taunt minion is present
            and a non-Taunt target is chosen.
        """
        if attacker_index < 0 or attacker_index >= len(self.player_board):
            raise ValueError(
                f"attacker_index {attacker_index} out of range "
                f"(board size={len(self.player_board)})"
            )
        attacker = self.player_board[attacker_index]
        if attacker.exhausted:
            raise ValueError(f"{attacker.name!r} is exhausted and cannot attack.")
        if attacker.attack == 0:
            raise ValueError(f"{attacker.name!r} has 0 attack.")

        # Taunt enforcement
        taunt_minions = [m for m in self.enemy_board if m.has_taunt]
        if taunt_minions and not defender_is_hero:
            defender = (
                self.enemy_board[defender_index]
                if 0 <= defender_index < len(self.enemy_board)
                else None
            )
            if defender is not None and not defender.has_taunt:
                raise ValueError(
                    "Must attack a Taunt minion first."
                )
        if taunt_minions and defender_is_hero:
            raise ValueError("Must attack a Taunt minion first.")

        if defender_is_hero:
            self.enemy_hero.take_damage(attacker.attack)
        else:
            if defender_index < 0 or defender_index >= len(self.enemy_board):
                raise ValueError(
                    f"defender_index {defender_index} out of range "
                    f"(enemy board size={len(self.enemy_board)})"
                )
            defender = self.enemy_board[defender_index]
            self._deal_damage(defender, attacker.attack, attacker=attacker)
            self._deal_damage(attacker, defender.attack, attacker=defender)

        attacker.exhausted = True
        self._check_deaths()
        self._check_game_over()
        return self

    # ------------------------------------------------------------------
    # Damage resolution
    # ------------------------------------------------------------------

    def _deal_damage(
        self,
        target: Card,
        amount: int,
        attacker: Optional[Card] = None,
    ) -> None:
        """Apply *amount* damage to *target* (a Card), respecting divine shield.

        If *attacker* has the POISONOUS mechanic, any non-zero damage is lethal
        (the target's health is reduced to 0 after the divine-shield check).
        """
        if amount <= 0:
            return
        if target.active_divine_shield:
            target.active_divine_shield = False
            return
        target.health -= amount
        target.damage_taken += amount
        # Poisonous: damage from a poisonous source is always lethal
        if attacker is not None and Mechanic.POISONOUS in attacker.mechanics:
            target.health = min(target.health, 0)

    # ------------------------------------------------------------------
    # Death checking and deathrattles
    # ------------------------------------------------------------------

    def _check_deaths(self) -> None:
        """Remove dead minions from both boards and trigger deathrattles."""
        self._reap_dead("player")
        self._reap_dead("enemy")

    def _reap_dead(self, side: str) -> None:
        board: List[Card] = (
            self.player_board if side == "player" else self.enemy_board
        )
        dead_indices = [i for i, m in enumerate(board) if not m.is_alive]
        # Process deathrattles in reverse so indices stay valid
        for idx in reversed(dead_indices):
            card = board[idx]
            if card.has_deathrattle and card.id in _DEATHRATTLE_REGISTRY:
                try:
                    _DEATHRATTLE_REGISTRY[card.id](self, idx, side)
                except Exception:
                    pass  # never let a deathrattle crash a rollout
            board.pop(idx)

    # ------------------------------------------------------------------
    # Game-over detection
    # ------------------------------------------------------------------

    def _check_game_over(self) -> None:
        player_dead = not self.player_hero.is_alive
        enemy_dead = not self.enemy_hero.is_alive
        if player_dead and enemy_dead:
            self.is_over = True
            self.winner = "draw"
        elif player_dead:
            self.is_over = True
            self.winner = "enemy"
        elif enemy_dead:
            self.is_over = True
            self.winner = "player"

    # ------------------------------------------------------------------
    # Available-action enumeration (used by MCTS)
    # ------------------------------------------------------------------

    def legal_actions(self) -> List[Dict[str, Any]]:
        """
        Return a list of legal action dicts for the current state.

        Each action dict contains:
        * ``type`` – ``"play"``, ``"attack"``, or ``"end_turn"``
        * Additional keys depending on type.
        """
        actions: List[Dict[str, Any]] = []

        # Play card actions
        taunt_indices = [i for i, m in enumerate(self.enemy_board) if m.has_taunt]
        for i, card in enumerate(self.player_hand):
            if card.cost <= self.player_mana:
                if card.card_type == CardType.MINION:
                    actions.append({"type": "play", "card_index": i, "target": None})
                elif card.card_type == CardType.SPELL:
                    if card.attack > 0:
                        # Targeted spell
                        for j in range(len(self.enemy_board)):
                            actions.append(
                                {"type": "play", "card_index": i, "target": j,
                                 "target_side": "enemy"}
                            )
                        actions.append(
                            {"type": "play", "card_index": i, "target": -1,
                             "target_side": "enemy"}
                        )
                    else:
                        actions.append({"type": "play", "card_index": i, "target": None})

        # Attack actions
        for i, attacker in enumerate(self.player_board):
            if attacker.exhausted or attacker.attack == 0:
                continue
            if taunt_indices:
                for j in taunt_indices:
                    actions.append(
                        {"type": "attack", "attacker": i, "defender": j,
                         "defender_is_hero": False}
                    )
            else:
                for j in range(len(self.enemy_board)):
                    actions.append(
                        {"type": "attack", "attacker": i, "defender": j,
                         "defender_is_hero": False}
                    )
                actions.append(
                    {"type": "attack", "attacker": i, "defender": -1,
                     "defender_is_hero": True}
                )

        actions.append({"type": "end_turn"})
        return actions

    def apply_action(self, action: Dict[str, Any]) -> "GameState":
        """Apply an action dict (from :meth:`legal_actions`) to *self*."""
        if action["type"] == "play":
            self.play_card(
                action["card_index"],
                action.get("target"),
                action.get("target_side", "enemy"),
            )
        elif action["type"] == "attack":
            self.attack(
                action["attacker"],
                action["defender"],
                action.get("defender_is_hero", False),
            )
        elif action["type"] == "end_turn":
            self.begin_turn()
        return self

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"GameState(turn={self.turn}, mana={self.player_mana}/{self.player_max_mana}, "
            f"hand={len(self.player_hand)}, "
            f"player_board={self.player_board}, "
            f"enemy_board={self.enemy_board})"
        )
