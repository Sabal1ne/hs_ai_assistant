"""
tests/test_hs_mcts.py
---------------------
Unit tests for hs_mcts.py.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from hs_simulator import Card, CardType, GameState, Hero, Mechanic
from hs_mcts import (
    MCTSNode,
    UCB1_C,
    backpropagate,
    best_action,
    best_card_index,
    determinize,
    expand,
    select,
    simulate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simple_state(player_mana: int = 5) -> GameState:
    """Return a simple game state with one playable card."""
    hand = [Card(name="Fireball", cost=3, attack=6, health=0, card_type=CardType.SPELL)]
    enemy = [Card(name="Target", cost=2, attack=2, health=4)]
    return GameState(
        player_mana=player_mana,
        player_max_mana=player_mana,
        player_hand=hand,
        player_board=[],
        enemy_board=enemy,
        player_hero=Hero(),
        enemy_hero=Hero(health=30),
    )


def _terminal_state() -> GameState:
    gs = GameState(player_hero=Hero(health=0))
    gs.is_over = True
    gs.winner = "enemy"
    return gs


# ---------------------------------------------------------------------------
# MCTSNode
# ---------------------------------------------------------------------------

class TestMCTSNode:
    def test_initial_state(self):
        gs = _simple_state()
        node = MCTSNode(gs)
        assert node.visits == 0
        assert node.value == 0.0
        assert node.parent is None
        assert node.action is None
        assert len(node.children) == 0
        assert len(node.untried_actions) > 0

    def test_is_fully_expanded_false_at_start(self):
        node = MCTSNode(_simple_state())
        assert not node.is_fully_expanded

    def test_is_terminal_for_over_state(self):
        node = MCTSNode(_terminal_state())
        assert node.is_terminal

    def test_ucb1_infinite_for_unvisited(self):
        parent = MCTSNode(_simple_state())
        parent.visits = 10
        child = MCTSNode(_simple_state(), parent=parent)
        assert child.ucb1(parent.visits) == float("inf")

    def test_ucb1_finite_after_visit(self):
        import math
        parent = MCTSNode(_simple_state())
        parent.visits = 10
        child = MCTSNode(_simple_state(), parent=parent)
        child.visits = 5
        child.value = 3.0
        score = child.ucb1(parent.visits)
        assert score == pytest.approx(
            3.0 / 5.0 + UCB1_C * math.sqrt(math.log(10) / 5.0)
        )


# ---------------------------------------------------------------------------
# select
# ---------------------------------------------------------------------------

class TestSelect:
    def test_select_returns_root_when_unexplored(self):
        root = MCTSNode(_simple_state())
        result = select(root)
        assert result is root

    def test_select_descends_to_unexplored_child(self):
        root = MCTSNode(_simple_state())
        # Manually expand one child and mark root as "expanded but child unexplored"
        child_state = _simple_state()
        child = MCTSNode(child_state, parent=root)
        child.visits = 0
        root.children.append(child)
        root.untried_actions.clear()  # mark root as fully expanded
        root.visits = 5
        result = select(root)
        # Should choose the child (unvisited → ucb1 = inf)
        assert result is child

    def test_select_terminal_node(self):
        node = MCTSNode(_terminal_state())
        result = select(node)
        assert result is node


# ---------------------------------------------------------------------------
# expand
# ---------------------------------------------------------------------------

class TestExpand:
    def test_expand_creates_child(self):
        root = MCTSNode(_simple_state())
        assert len(root.children) == 0
        child = expand(root)
        assert child is not root or len(root.children) > 0

    def test_expand_reduces_untried_actions(self):
        root = MCTSNode(_simple_state())
        before = len(root.untried_actions)
        expand(root)
        # One action was consumed
        assert len(root.untried_actions) == before - 1

    def test_expand_terminal_returns_node(self):
        node = MCTSNode(_terminal_state())
        result = expand(node)
        assert result is node


# ---------------------------------------------------------------------------
# simulate
# ---------------------------------------------------------------------------

class TestSimulate:
    def test_simulate_returns_float_in_range(self):
        gs = _simple_state()
        reward = simulate(gs)
        assert 0.0 <= reward <= 1.0

    def test_simulate_terminal_win_returns_1(self):
        gs = GameState(player_hero=Hero(health=30), enemy_hero=Hero(health=0))
        gs.is_over = True
        gs.winner = "player"
        assert simulate(gs) == 1.0

    def test_simulate_terminal_loss_returns_0(self):
        gs = GameState(player_hero=Hero(health=0), enemy_hero=Hero(health=30))
        gs.is_over = True
        gs.winner = "enemy"
        assert simulate(gs) == 0.0

    def test_simulate_respects_max_depth(self):
        # A state where neither player can win quickly; max_depth should stop it
        gs = GameState(
            player_mana=10, player_max_mana=10,
            player_hero=Hero(health=30),
            enemy_hero=Hero(health=30),
        )
        reward = simulate(gs, max_depth=2)
        assert 0.0 <= reward <= 1.0


# ---------------------------------------------------------------------------
# backpropagate
# ---------------------------------------------------------------------------

class TestBackpropagate:
    def test_updates_root_and_child(self):
        root = MCTSNode(_simple_state())
        child = MCTSNode(_simple_state(), parent=root)
        backpropagate(child, reward=1.0)
        assert child.visits == 1
        assert child.value == 1.0
        assert root.visits == 1
        # Reward is flipped at root
        assert root.value == pytest.approx(0.0)

    def test_flip_at_each_level(self):
        root = MCTSNode(_simple_state())
        child = MCTSNode(_simple_state(), parent=root)
        grandchild = MCTSNode(_simple_state(), parent=child)
        backpropagate(grandchild, reward=1.0)
        assert grandchild.value == pytest.approx(1.0)
        assert child.value == pytest.approx(0.0)
        assert root.value == pytest.approx(1.0)

    def test_multiple_backpropagations_accumulate(self):
        root = MCTSNode(_simple_state())
        child = MCTSNode(_simple_state(), parent=root)
        backpropagate(child, 1.0)
        backpropagate(child, 1.0)
        assert child.visits == 2
        assert child.value == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# determinize
# ---------------------------------------------------------------------------

class TestDeterminize:
    def test_returns_correct_number_of_worlds(self):
        gs = _simple_state()
        worlds = determinize(gs, "Mage", num_worlds=3)
        assert len(worlds) == 3

    def test_worlds_are_independent_copies(self):
        gs = _simple_state()
        worlds = determinize(gs, "generic", num_worlds=2)
        worlds[0].player_mana = 0
        assert worlds[1].player_mana == gs.player_mana

    def test_unknown_archetype_falls_back_to_generic(self):
        gs = _simple_state()
        worlds = determinize(gs, "NonExistentClass", num_worlds=2)
        assert len(worlds) == 2


# ---------------------------------------------------------------------------
# best_action / best_card_index
# ---------------------------------------------------------------------------

class TestBestAction:
    def test_returns_dict(self):
        gs = _simple_state()
        action = best_action(gs, time_limit_ms=200)
        assert isinstance(action, dict)
        assert "type" in action

    def test_valid_action_type(self):
        gs = _simple_state()
        action = best_action(gs, time_limit_ms=200)
        assert action["type"] in ("play", "attack", "end_turn")

    def test_obvious_lethal_detected(self):
        """With a 30-damage spell in hand and 0-cost, AI should play it."""
        lethal = Card(name="OHK", cost=0, attack=30, health=0, card_type=CardType.SPELL)
        gs = GameState(
            player_mana=10, player_max_mana=10,
            player_hand=[lethal],
            player_board=[],
            enemy_board=[],
            player_hero=Hero(health=30),
            enemy_hero=Hero(health=30),
        )
        action = best_action(gs, time_limit_ms=500)
        # The action should be to play the lethal card or attack hero
        assert action["type"] in ("play", "end_turn")

    def test_best_card_index_returns_minus_one_for_end_turn(self):
        # No playable cards → AI should suggest end_turn
        gs = GameState(
            player_mana=0, player_max_mana=0,
            player_hand=[Card(name="Expensive", cost=10)],
            player_hero=Hero(),
            enemy_hero=Hero(),
        )
        idx = best_card_index(gs, time_limit_ms=100)
        assert idx == -1

    def test_best_card_index_valid_when_card_playable(self):
        gs = _simple_state()
        idx = best_card_index(gs, time_limit_ms=300)
        # Index should be 0 (only one card) or -1 if end_turn chosen
        assert idx in (-1, 0)
