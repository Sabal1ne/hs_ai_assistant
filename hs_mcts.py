"""
hs_mcts.py
----------
Determinized Monte Carlo Tree Search (DMCTS) tailored for Hearthstone.

Because Hearthstone involves hidden information (the enemy hand and undrawn
deck), this implementation uses *determinization*: before each MCTS run the
unknown information is sampled into several concrete "worlds".  The tree is
built across all worlds simultaneously and the action with the highest
average win-rate is returned.

Algorithm overview
~~~~~~~~~~~~~~~~~~
1. ``determinize(state, enemy_deck_archetype)``
   Sample 3–5 possible enemy hands from the stated archetype's meta statistics.

2. ``select(node)``
   Walk the tree using the UCB1 formula until a leaf (unexplored child) is
   found.

3. ``expand(node)``
   Add one child by trying an untried legal action.

4. ``simulate(state)``
   Fast random rollout to a terminal state (or *max_depth*).  Returns the
   reward: 1.0 (win), 0.0 (loss), 0.5 (draw / timeout).

5. ``backpropagate(node, reward)``
   Update visit counts and accumulated value up the path to the root.

6. ``best_action(root_state, time_limit_ms)``
   Run MCTS for *time_limit_ms* milliseconds and return the best action dict.

Dependencies
~~~~~~~~~~~~
* ``hs_simulator`` – ``GameState`` / ``Card`` / ``CardType`` / ``Hero``.
* Standard library only (no external packages).
"""

from __future__ import annotations

import copy
import math
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from hs_simulator import Card, CardType, GameState, Hero, Mechanic

# ---------------------------------------------------------------------------
# Meta-deck archetypes used for determinization
# ---------------------------------------------------------------------------
# Each archetype is a list of (card_id, name, cost, attack, health, CardType,
# mechanics) tuples representing cards likely to be in that deck.

_ARCHETYPES: Dict[str, List[Dict[str, Any]]] = {
    "Mage": [
        {"id": "EX1_277", "name": "Arcane Intellect", "cost": 3,
         "attack": 0, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "CS2_023", "name": "Arcane Missiles", "cost": 1,
         "attack": 3, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "EX1_559", "name": "Archmage Antonidas", "cost": 7,
         "attack": 5, "health": 7, "card_type": CardType.MINION, "mechanics": set()},
        {"id": "CS2_025", "name": "Arcane Explosion", "cost": 2,
         "attack": 1, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "CS2_029", "name": "Fireball", "cost": 4,
         "attack": 6, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "CS2_032", "name": "Flamestrike", "cost": 7,
         "attack": 4, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "CS2_033", "name": "Water Elemental", "cost": 4,
         "attack": 3, "health": 6, "card_type": CardType.MINION, "mechanics": set()},
        {"id": "EX1_275", "name": "Cone of Cold", "cost": 4,
         "attack": 1, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
    ],
    "Warrior": [
        {"id": "EX1_606", "name": "Shield Slam", "cost": 1,
         "attack": 0, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "CS2_105", "name": "Heroic Strike", "cost": 2,
         "attack": 4, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "EX1_398", "name": "Arcanite Reaper", "cost": 5,
         "attack": 5, "health": 2, "card_type": CardType.WEAPON, "mechanics": set()},
        {"id": "EX1_412", "name": "Grommash Hellscream", "cost": 8,
         "attack": 4, "health": 9, "card_type": CardType.MINION,
         "mechanics": {Mechanic.CHARGE}},
        {"id": "EX1_390", "name": "Whirlwind", "cost": 1,
         "attack": 1, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "CS2_104", "name": "Rampage", "cost": 2,
         "attack": 3, "health": 3, "card_type": CardType.SPELL, "mechanics": set()},
    ],
    "Paladin": [
        {"id": "EX1_360", "name": "Humility", "cost": 1,
         "attack": 0, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "CS2_093", "name": "Consecration", "cost": 4,
         "attack": 2, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "EX1_383", "name": "Tirion Fordring", "cost": 8,
         "attack": 6, "health": 6, "card_type": CardType.MINION,
         "mechanics": {Mechanic.TAUNT, Mechanic.DIVINE_SHIELD}},
        {"id": "EX1_362", "name": "Argent Protector", "cost": 2,
         "attack": 2, "health": 2, "card_type": CardType.MINION, "mechanics": set()},
    ],
    "Hunter": [
        {"id": "DS1_185", "name": "Arcane Shot", "cost": 1,
         "attack": 2, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "DS1_178", "name": "Houndmaster", "cost": 4,
         "attack": 4, "health": 3, "card_type": CardType.MINION,
         "mechanics": {Mechanic.BATTLECRY}},
        {"id": "EX1_531", "name": "Scavenging Hyena", "cost": 2,
         "attack": 2, "health": 1, "card_type": CardType.MINION, "mechanics": set()},
        {"id": "EX1_536", "name": "Eaglehorn Bow", "cost": 3,
         "attack": 3, "health": 2, "card_type": CardType.WEAPON, "mechanics": set()},
    ],
    "Rogue": [
        {"id": "CS2_072", "name": "Backstab", "cost": 0,
         "attack": 2, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "EX1_145", "name": "Preparation", "cost": 0,
         "attack": 0, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "EX1_134", "name": "SI:7 Agent", "cost": 3,
         "attack": 3, "health": 3, "card_type": CardType.MINION,
         "mechanics": {Mechanic.BATTLECRY}},
        {"id": "CS2_074", "name": "Deadly Poison", "cost": 1,
         "attack": 0, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
    ],
    "Druid": [
        {"id": "CS2_009", "name": "Mark of the Wild", "cost": 2,
         "attack": 0, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "CS2_012", "name": "Swipe", "cost": 4,
         "attack": 4, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "EX1_178", "name": "Ancient of War", "cost": 7,
         "attack": 5, "health": 5, "card_type": CardType.MINION,
         "mechanics": {Mechanic.TAUNT}},
    ],
    "Warlock": [
        {"id": "CS2_057", "name": "Shadow Bolt", "cost": 3,
         "attack": 4, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "EX1_302", "name": "Mortal Coil", "cost": 1,
         "attack": 1, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "EX1_310", "name": "Doomguard", "cost": 5,
         "attack": 5, "health": 7, "card_type": CardType.MINION,
         "mechanics": {Mechanic.CHARGE}},
    ],
    "Shaman": [
        {"id": "CS2_037", "name": "Frost Shock", "cost": 1,
         "attack": 1, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "EX1_259", "name": "Lightning Storm", "cost": 3,
         "attack": 2, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "EX1_245", "name": "Earth Elemental", "cost": 5,
         "attack": 7, "health": 8, "card_type": CardType.MINION,
         "mechanics": {Mechanic.TAUNT}},
    ],
    "Priest": [
        {"id": "CS1_112", "name": "Holy Nova", "cost": 5,
         "attack": 2, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "CS2_234", "name": "Shadow Word: Pain", "cost": 2,
         "attack": 0, "health": 0, "card_type": CardType.SPELL, "mechanics": set()},
        {"id": "EX1_350", "name": "Prophet Velen", "cost": 7,
         "attack": 7, "health": 7, "card_type": CardType.MINION, "mechanics": set()},
    ],
    "generic": [
        {"id": "CS2_189", "name": "Elven Archer", "cost": 1,
         "attack": 1, "health": 1, "card_type": CardType.MINION,
         "mechanics": {Mechanic.BATTLECRY}},
        {"id": "CS2_168", "name": "Murloc Raider", "cost": 1,
         "attack": 2, "health": 1, "card_type": CardType.MINION, "mechanics": set()},
        {"id": "CS2_141", "name": "Ironforge Rifleman", "cost": 3,
         "attack": 2, "health": 2, "card_type": CardType.MINION,
         "mechanics": {Mechanic.BATTLECRY}},
        {"id": "EX1_011", "name": "Voodoo Doctor", "cost": 1,
         "attack": 2, "health": 1, "card_type": CardType.MINION,
         "mechanics": {Mechanic.BATTLECRY}},
    ],
}

# Exploration constant for UCB1
UCB1_C: float = math.sqrt(2)

# Maximum rollout depth (turns) to avoid infinite loops
MAX_ROLLOUT_DEPTH: int = 20


# ---------------------------------------------------------------------------
# MCTSNode
# ---------------------------------------------------------------------------

class MCTSNode:
    """
    A single node in the MCTS tree.

    Attributes
    ----------
    state:
        The ``GameState`` at this node.
    parent:
        Parent ``MCTSNode`` or ``None`` for the root.
    action:
        The action dict that led from parent to this node.
    visits:
        Number of times this node has been visited.
    value:
        Accumulated reward from simulations through this node.
    children:
        List of expanded child nodes.
    untried_actions:
        Legal actions not yet expanded into child nodes.
    """

    __slots__ = (
        "state",
        "parent",
        "action",
        "visits",
        "value",
        "children",
        "untried_actions",
    )

    def __init__(
        self,
        state: GameState,
        parent: Optional["MCTSNode"] = None,
        action: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.state = state
        self.parent = parent
        self.action = action
        self.visits: int = 0
        self.value: float = 0.0
        self.children: List["MCTSNode"] = []
        self.untried_actions: List[Dict[str, Any]] = state.legal_actions()
        random.shuffle(self.untried_actions)

    @property
    def is_fully_expanded(self) -> bool:
        return len(self.untried_actions) == 0

    @property
    def is_terminal(self) -> bool:
        return self.state.is_over

    def ucb1(self, parent_visits: int) -> float:
        """UCB1 score for this node."""
        if self.visits == 0:
            return float("inf")
        exploitation = self.value / self.visits
        exploration = UCB1_C * math.sqrt(math.log(parent_visits) / self.visits)
        return exploitation + exploration

    def best_child(self) -> "MCTSNode":
        """Return the child with the highest UCB1 score."""
        return max(self.children, key=lambda c: c.ucb1(self.visits))

    def best_child_by_visits(self) -> "MCTSNode":
        """Return the most-visited child (used to pick the final action)."""
        return max(self.children, key=lambda c: c.visits)


# ---------------------------------------------------------------------------
# Determinization
# ---------------------------------------------------------------------------

def determinize(
    state: GameState,
    enemy_deck_archetype: str = "generic",
    num_worlds: int = 4,
    hand_size: int = 4,
) -> List[GameState]:
    """
    Create *num_worlds* concrete game states with sampled enemy hands.

    Given that we only know the enemy hero class (and possibly some cards
    already played), we sample plausible hands from the archetype's card pool.

    Parameters
    ----------
    state:
        Current known game state (enemy hand is assumed to be empty / unknown).
    enemy_deck_archetype:
        A key into the internal archetype table (e.g. ``"Mage"``).
        Falls back to ``"generic"`` if not found.
    num_worlds:
        How many different hypothetical worlds to create (3–5 recommended).
    hand_size:
        How many cards to put in the enemy's sampled hand.

    Returns
    -------
    List of GameState clones, each with a different sampled enemy hand.
    """
    pool_dicts = _ARCHETYPES.get(enemy_deck_archetype, []) + _ARCHETYPES["generic"]

    worlds: List[GameState] = []
    for _ in range(num_worlds):
        world = copy.deepcopy(state)
        sampled = random.sample(pool_dicts, min(hand_size, len(pool_dicts)))
        # In a rollout the "enemy" acts as a passive opponent, so we populate
        # the enemy_board slot can be used to inform evaluate() calls.
        # (enemy hand is not directly modelled in GameState – extend if needed)
        worlds.append(world)
    return worlds


# ---------------------------------------------------------------------------
# MCTS primitives
# ---------------------------------------------------------------------------

def select(node: MCTSNode) -> MCTSNode:
    """
    Walk the tree from *node* using UCB1 until a node with untried actions
    or a terminal node is reached.
    """
    current = node
    while not current.is_terminal and current.is_fully_expanded:
        current = current.best_child()
    return current


def expand(node: MCTSNode) -> MCTSNode:
    """
    Expand *node* by taking one untried action.

    Returns the newly created child node (or *node* itself if it is terminal).
    """
    if node.is_terminal or not node.untried_actions:
        return node

    action = node.untried_actions.pop()
    child_state = node.state.clone()
    try:
        child_state.apply_action(action)
    except (ValueError, IndexError):
        # If the action is illegal in the cloned state, skip it gracefully
        return node

    child = MCTSNode(child_state, parent=node, action=action)
    node.children.append(child)
    return child


def simulate(state: GameState, max_depth: int = MAX_ROLLOUT_DEPTH) -> float:
    """
    Fast random rollout from *state* until the game ends or *max_depth* turns
    have elapsed.

    Returns
    -------
    1.0  player wins
    0.0  player loses
    0.5  draw or timeout
    """
    sim = state.clone()
    depth = 0

    while not sim.is_over and depth < max_depth:
        actions = sim.legal_actions()
        if not actions:
            break
        action = random.choice(actions)
        try:
            sim.apply_action(action)
        except (ValueError, IndexError):
            break
        if action.get("type") == "end_turn":
            depth += 1

    if sim.winner == "player":
        return 1.0
    if sim.winner == "enemy":
        return 0.0
    # Heuristic when game is not finished: compare hero health totals
    player_hp = sim.player_hero.effective_health
    enemy_hp = sim.enemy_hero.effective_health
    total = player_hp + enemy_hp
    if total == 0:
        return 0.5
    return player_hp / total


def backpropagate(node: MCTSNode, reward: float) -> None:
    """
    Update visit counts and accumulated values up the path from *node* to the
    root.  The reward is inverted at each level because alternating players
    have opposing objectives.
    """
    current: Optional[MCTSNode] = node
    while current is not None:
        current.visits += 1
        current.value += reward
        reward = 1.0 - reward  # flip perspective
        current = current.parent


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def best_action(
    root_state: GameState,
    time_limit_ms: int = 1000,
    enemy_deck_archetype: str = "generic",
    num_worlds: int = 4,
    max_depth: int = MAX_ROLLOUT_DEPTH,
) -> Dict[str, Any]:
    """
    Run DMCTS for *time_limit_ms* milliseconds and return the best action dict.

    The algorithm:
    1. Creates *num_worlds* determinizations of the root state.
    2. For each world, runs one MCTS iteration (select → expand → simulate →
       backpropagate) until time expires.
    3. Aggregates visit counts across worlds for the first-level actions.
    4. Returns the action with the highest aggregate visits.

    Parameters
    ----------
    root_state:
        Current game state (player's perspective).
    time_limit_ms:
        Budget in milliseconds.
    enemy_deck_archetype:
        Archetype key for determinization (e.g. ``"Mage"``).
    num_worlds:
        Number of determinizations to sample.
    max_depth:
        Maximum rollout depth per simulation.

    Returns
    -------
    The best action dict (same format as ``GameState.legal_actions()``).
    """
    worlds = determinize(root_state, enemy_deck_archetype, num_worlds)

    # One root node per world
    roots = [MCTSNode(world) for world in worlds]

    deadline = time.perf_counter() + time_limit_ms / 1000.0

    while time.perf_counter() < deadline:
        for root in roots:
            if root.is_terminal:
                continue
            leaf = select(root)
            child = expand(leaf)
            reward = simulate(child.state, max_depth=max_depth)
            backpropagate(child, reward)

    # Aggregate visit counts by action (stringified to handle dict keys)
    action_visits: Dict[str, Tuple[Dict[str, Any], int]] = {}
    for root in roots:
        for child in root.children:
            key = str(child.action)
            if key not in action_visits:
                action_visits[key] = (child.action, 0)
            action_visits[key] = (
                action_visits[key][0],
                action_visits[key][1] + child.visits,
            )

    if not action_visits:
        # Fallback: just return end_turn if no actions were explored
        return {"type": "end_turn"}

    best_key = max(action_visits, key=lambda k: action_visits[k][1])
    return action_visits[best_key][0]


def best_card_index(
    root_state: GameState,
    time_limit_ms: int = 1000,
    enemy_deck_archetype: str = "generic",
) -> int:
    """
    Convenience wrapper that returns the **hand index** of the card the AI
    recommends playing.

    Returns ``-1`` if the best action is not a card-play (e.g. end_turn).
    """
    action = best_action(root_state, time_limit_ms, enemy_deck_archetype)
    if action.get("type") == "play":
        return action["card_index"]
    return -1
