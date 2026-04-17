"""
tests/test_hs_simulator.py
--------------------------
Unit tests for hs_simulator.py (Card, Hero, GameState).
"""

import copy
import pytest

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hs_simulator import (
    Card,
    CardType,
    GameState,
    Hero,
    Mechanic,
    register_deathrattle,
)


# ---------------------------------------------------------------------------
# Card
# ---------------------------------------------------------------------------

class TestCard:
    def test_basic_attributes(self):
        c = Card(id="EX1_298", name="Ragnaros", cost=8, attack=8, health=8,
                 card_type=CardType.MINION)
        assert c.id == "EX1_298"
        assert c.name == "Ragnaros"
        assert c.cost == 8
        assert c.attack == 8
        assert c.health == 8
        assert c.card_type == CardType.MINION
        assert c.is_alive

    def test_charge_not_exhausted(self):
        c = Card(name="Wisp", cost=0, attack=1, health=1,
                 mechanics={Mechanic.CHARGE})
        assert not c.exhausted

    def test_no_charge_is_exhausted(self):
        c = Card(name="Wisp", cost=0, attack=1, health=1)
        assert c.exhausted

    def test_taunt_predicate(self):
        c = Card(name="Defender", cost=1, attack=1, health=3,
                 mechanics={Mechanic.TAUNT})
        assert c.has_taunt

    def test_divine_shield(self):
        c = Card(name="Silver Hand Recruit", cost=1, attack=1, health=1,
                 mechanics={Mechanic.DIVINE_SHIELD})
        assert c.has_divine_shield
        assert c.active_divine_shield

    def test_deepcopy(self):
        c = Card(name="Foo", cost=2, attack=2, health=2)
        c2 = copy.deepcopy(c)
        c2.health = 0
        assert c.health == 2


# ---------------------------------------------------------------------------
# Hero
# ---------------------------------------------------------------------------

class TestHero:
    def test_take_damage_no_armor(self):
        h = Hero(health=30)
        h.take_damage(5)
        assert h.health == 25
        assert h.armor == 0

    def test_take_damage_with_armor(self):
        h = Hero(health=30, armor=5)
        h.take_damage(8)
        assert h.armor == 0
        assert h.health == 27

    def test_take_damage_zero(self):
        h = Hero(health=30)
        h.take_damage(0)
        assert h.health == 30

    def test_effective_health(self):
        h = Hero(health=25, armor=7)
        assert h.effective_health == 32


# ---------------------------------------------------------------------------
# GameState – play_card
# ---------------------------------------------------------------------------

class TestPlayCard:
    def _state_with_card(self, card: Card, mana: int = 10) -> GameState:
        gs = GameState(
            player_mana=mana,
            player_max_mana=mana,
            player_hand=[card],
        )
        return gs

    def test_play_minion_moves_to_board(self):
        c = Card(name="Wisp", cost=0, attack=1, health=1)
        gs = self._state_with_card(c)
        gs.play_card(0)
        assert len(gs.player_hand) == 0
        assert len(gs.player_board) == 1
        assert gs.player_board[0].name == "Wisp"

    def test_mana_deducted(self):
        c = Card(name="Fireball", cost=4, attack=6, health=0,
                 card_type=CardType.SPELL)
        gs = GameState(
            player_mana=10,
            player_max_mana=10,
            player_hand=[c],
            enemy_board=[Card(name="Target", cost=0, attack=1, health=10)],
        )
        gs.play_card(0, target=0, target_side="enemy")
        assert gs.player_mana == 6

    def test_insufficient_mana_raises(self):
        c = Card(name="Expensive", cost=8)
        gs = self._state_with_card(c, mana=5)
        with pytest.raises(ValueError, match="mana"):
            gs.play_card(0)

    def test_invalid_index_raises(self):
        gs = GameState(player_mana=10, player_max_mana=10)
        with pytest.raises(ValueError):
            gs.play_card(0)

    def test_spell_deals_damage(self):
        spell = Card(name="Bolt", cost=1, attack=3, health=0,
                     card_type=CardType.SPELL)
        target = Card(name="Minion", cost=2, attack=2, health=5)
        gs = GameState(
            player_mana=10,
            player_max_mana=10,
            player_hand=[spell],
            enemy_board=[target],
        )
        gs.play_card(0, target=0, target_side="enemy")
        assert gs.enemy_board[0].health == 2

    def test_spell_kills_minion(self):
        spell = Card(name="Bolt", cost=1, attack=5, health=0,
                     card_type=CardType.SPELL)
        target = Card(name="Minion", cost=2, attack=2, health=3)
        gs = GameState(
            player_mana=10,
            player_max_mana=10,
            player_hand=[spell],
            enemy_board=[target],
        )
        gs.play_card(0, target=0, target_side="enemy")
        assert len(gs.enemy_board) == 0

    def test_board_full(self):
        board = [Card(name=f"M{i}", cost=1, attack=1, health=1) for i in range(7)]
        c = Card(name="Extra", cost=0, attack=1, health=1)
        gs = GameState(
            player_mana=10, player_max_mana=10,
            player_hand=[c], player_board=board,
        )
        gs.play_card(0)
        assert len(gs.player_board) == 7


# ---------------------------------------------------------------------------
# GameState – attack
# ---------------------------------------------------------------------------

class TestAttack:
    def _two_minion_state(
        self,
        attacker_atk: int = 3,
        attacker_hp: int = 3,
        defender_atk: int = 2,
        defender_hp: int = 4,
        attacker_charge: bool = True,
    ) -> GameState:
        mechanics = {Mechanic.CHARGE} if attacker_charge else set()
        att = Card(name="Attacker", cost=3, attack=attacker_atk,
                   health=attacker_hp, mechanics=mechanics)
        defn = Card(name="Defender", cost=2, attack=defender_atk,
                    health=defender_hp)
        return GameState(
            player_mana=10, player_max_mana=10,
            player_board=[att],
            enemy_board=[defn],
            player_hero=Hero(),
            enemy_hero=Hero(),
        )

    def test_basic_combat(self):
        gs = self._two_minion_state()
        gs.attack(0, 0)
        assert gs.player_board[0].health == 1
        assert gs.enemy_board[0].health == 1

    def test_attacker_exhausted_after_attack(self):
        gs = self._two_minion_state()
        gs.attack(0, 0)
        assert gs.player_board[0].exhausted

    def test_exhausted_cannot_attack(self):
        gs = self._two_minion_state()
        gs.attack(0, 0)
        with pytest.raises(ValueError, match="exhausted"):
            gs.attack(0, 0)

    def test_lethal_removes_minion(self):
        gs = self._two_minion_state(attacker_atk=5, defender_hp=3)
        gs.attack(0, 0)
        assert len(gs.enemy_board) == 0

    def test_mutual_trade(self):
        gs = self._two_minion_state(attacker_atk=3, attacker_hp=2,
                                     defender_atk=3, defender_hp=3)
        gs.attack(0, 0)
        assert len(gs.player_board) == 0
        assert len(gs.enemy_board) == 0

    def test_attack_hero(self):
        att = Card(name="Charge Guy", cost=3, attack=5, health=5,
                   mechanics={Mechanic.CHARGE})
        gs = GameState(
            player_mana=10, player_max_mana=10,
            player_board=[att],
            enemy_board=[],
            player_hero=Hero(),
            enemy_hero=Hero(health=30),
        )
        gs.attack(0, -1, defender_is_hero=True)
        assert gs.enemy_hero.health == 25

    def test_taunt_enforcement(self):
        att = Card(name="Attacker", cost=1, attack=3, health=3,
                   mechanics={Mechanic.CHARGE})
        taunt = Card(name="Taunt", cost=2, attack=1, health=5,
                     mechanics={Mechanic.TAUNT})
        other = Card(name="Other", cost=1, attack=1, health=1)
        gs = GameState(
            player_mana=10, player_max_mana=10,
            player_board=[att],
            enemy_board=[taunt, other],
            player_hero=Hero(),
            enemy_hero=Hero(),
        )
        with pytest.raises(ValueError, match="Taunt"):
            gs.attack(0, 1)
        gs.attack(0, 0)

    def test_taunt_blocks_hero_attack(self):
        att = Card(name="Charge", cost=1, attack=3, health=3,
                   mechanics={Mechanic.CHARGE})
        taunt = Card(name="Taunt", cost=2, attack=1, health=5,
                     mechanics={Mechanic.TAUNT})
        gs = GameState(
            player_mana=10, player_max_mana=10,
            player_board=[att],
            enemy_board=[taunt],
            player_hero=Hero(),
            enemy_hero=Hero(),
        )
        with pytest.raises(ValueError, match="Taunt"):
            gs.attack(0, -1, defender_is_hero=True)

    def test_poisonous_attacker_kills_any_minion(self):
        att = Card(name="Toxic Blob", cost=3, attack=1, health=5,
                   mechanics={Mechanic.CHARGE, Mechanic.POISONOUS})
        target = Card(name="Giant", cost=6, attack=5, health=100)
        gs = GameState(
            player_mana=10, player_max_mana=10,
            player_board=[att],
            enemy_board=[target],
            player_hero=Hero(),
            enemy_hero=Hero(),
        )
        gs.attack(0, 0)
        # Poisonous attacker: target must die regardless of its health
        assert len(gs.enemy_board) == 0

    def test_poisonous_defender_kills_attacker(self):
        att = Card(name="Attacker", cost=3, attack=5, health=100,
                   mechanics={Mechanic.CHARGE})
        defender = Card(name="Toxic", cost=1, attack=1, health=5,
                        mechanics={Mechanic.POISONOUS})
        gs = GameState(
            player_mana=10, player_max_mana=10,
            player_board=[att],
            enemy_board=[defender],
            player_hero=Hero(),
            enemy_hero=Hero(),
        )
        gs.attack(0, 0)
        # The defender is poisonous, so the attacker should die from counterattack
        assert len(gs.player_board) == 0

    def test_divine_shield_blocks_damage(self):
        att = Card(name="Attacker", cost=3, attack=5, health=5,
                   mechanics={Mechanic.CHARGE})
        shield = Card(name="Shielded", cost=1, attack=1, health=1,
                      mechanics={Mechanic.DIVINE_SHIELD})
        gs = GameState(
            player_mana=10, player_max_mana=10,
            player_board=[att],
            enemy_board=[shield],
            player_hero=Hero(),
            enemy_hero=Hero(),
        )
        gs.attack(0, 0)
        assert len(gs.enemy_board) == 1
        assert not gs.enemy_board[0].active_divine_shield

    def test_game_over_on_hero_death(self):
        att = Card(name="Lethal", cost=1, attack=31, health=1,
                   mechanics={Mechanic.CHARGE})
        gs = GameState(
            player_mana=10, player_max_mana=10,
            player_board=[att],
            enemy_board=[],
            player_hero=Hero(),
            enemy_hero=Hero(health=30),
        )
        gs.attack(0, -1, defender_is_hero=True)
        assert gs.is_over
        assert gs.winner == "player"


# ---------------------------------------------------------------------------
# GameState – clone and begin_turn
# ---------------------------------------------------------------------------

class TestCloneAndTurn:
    def test_clone_is_independent(self):
        gs = GameState(player_mana=5, player_max_mana=10)
        gs2 = gs.clone()
        gs2.player_mana = 0
        assert gs.player_mana == 5

    def test_begin_turn_refills_mana(self):
        gs = GameState(player_mana=0, player_max_mana=3)
        gs.begin_turn()
        assert gs.player_max_mana == 4
        assert gs.player_mana == 4

    def test_begin_turn_unexhausts_minions(self):
        m = Card(name="Veteran", cost=2, attack=2, health=2)
        m.exhausted = True
        gs = GameState(player_mana=5, player_max_mana=5, player_board=[m])
        gs.begin_turn()
        assert not gs.player_board[0].exhausted

    def test_overload_applied_next_turn(self):
        gs = GameState(player_mana=5, player_max_mana=5, player_overload=2)
        gs.begin_turn()
        assert gs.player_mana == 4

    def test_mana_capped_at_10(self):
        gs = GameState(player_mana=10, player_max_mana=10)
        gs.begin_turn()
        assert gs.player_max_mana == 10


# ---------------------------------------------------------------------------
# GameState – legal_actions
# ---------------------------------------------------------------------------

class TestLegalActions:
    def test_end_turn_always_present(self):
        gs = GameState(player_mana=0, player_max_mana=0)
        actions = gs.legal_actions()
        types = [a["type"] for a in actions]
        assert "end_turn" in types

    def test_play_actions_respect_mana(self):
        cheap = Card(name="Cheap", cost=1, attack=1, health=1)
        expensive = Card(name="Expensive", cost=5, attack=5, health=5)
        gs = GameState(
            player_mana=3, player_max_mana=3,
            player_hand=[cheap, expensive],
        )
        play_actions = [a for a in gs.legal_actions() if a["type"] == "play"]
        played_indices = {a["card_index"] for a in play_actions}
        assert 0 in played_indices
        assert 1 not in played_indices

    def test_exhausted_minion_cannot_attack(self):
        m = Card(name="Tired", cost=1, attack=3, health=3)
        m.exhausted = True
        gs = GameState(
            player_mana=5, player_max_mana=5,
            player_board=[m],
            enemy_board=[Card(name="Enemy", cost=1, attack=1, health=1)],
        )
        attack_actions = [a for a in gs.legal_actions() if a["type"] == "attack"]
        assert attack_actions == []


# ---------------------------------------------------------------------------
# Deathrattle
# ---------------------------------------------------------------------------

class TestDeathrattle:
    def test_deathrattle_triggered_on_death(self):
        triggered = []

        def my_dr(gs, board_idx, side):
            triggered.append((board_idx, side))

        register_deathrattle("CUSTOM_DR_TEST", my_dr)

        minion = Card(name="DR Minion", cost=3, attack=3, health=1,
                      id="CUSTOM_DR_TEST",
                      mechanics={Mechanic.DEATHRATTLE, Mechanic.CHARGE})
        killer = Card(name="Killer", cost=1, attack=3, health=5,
                      mechanics={Mechanic.CHARGE})
        gs = GameState(
            player_mana=10, player_max_mana=10,
            player_board=[killer],
            enemy_board=[minion],
            player_hero=Hero(),
            enemy_hero=Hero(),
        )
        gs.attack(0, 0)
        assert len(triggered) == 1
        assert triggered[0][1] == "enemy"
