"""
tests/test_hs_log_parser.py
---------------------------
Unit tests for hs_log_parser.py.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import copy
import tempfile
import time
from pathlib import Path

import pytest

from hs_log_parser import LogParser, _parse_entity, _empty_game_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_parser(tmp_path: Path) -> tuple:
    """Return (parser, log_file_path)."""
    log_file = tmp_path / "Power.log"
    log_file.write_text("", encoding="utf-8")
    states = []
    parser = LogParser(log_path=str(log_file), callback=states.append)
    return parser, log_file, states


def _append(log_file: Path, line: str) -> None:
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# _parse_entity
# ---------------------------------------------------------------------------

class TestParseEntity:
    def test_bracket_full(self):
        raw = "[name=Ragnaros id=74 zone=PLAY zonePos=6 cardId=EX1_298 player=1]"
        e = _parse_entity(raw)
        assert e["name"] == "Ragnaros"
        assert e["id"] == 74
        assert e["zone"] == "PLAY"
        assert e["zone_pos"] == 6
        assert e["card_id"] == "EX1_298"
        assert e["player"] == 1

    def test_numeric_id(self):
        e = _parse_entity("74")
        assert e["id"] == 74

    def test_game_entity(self):
        e = _parse_entity("GameEntity")
        assert e.get("name") == "GameEntity"

    def test_unknown_entity(self):
        raw = "[name=UNKNOWN ENTITY [cardType=INVALID] id=12]"
        e = _parse_entity(raw)
        assert e["id"] == 12


# ---------------------------------------------------------------------------
# _empty_game_state
# ---------------------------------------------------------------------------

class TestEmptyGameState:
    def test_structure(self):
        s = _empty_game_state()
        assert "player" in s
        assert "enemy" in s
        assert s["game_active"] is False
        assert s["player"]["mana"] == {"current": 0, "total": 0, "overload": 0}
        assert s["player"]["hand"] == []
        assert s["player"]["board"] == []


# ---------------------------------------------------------------------------
# LogParser – game start (CREATE_GAME)
# ---------------------------------------------------------------------------

class TestCreateGame:
    def test_create_game_resets_state(self, tmp_path):
        parser, log_file, states = _make_parser(tmp_path)
        _append(log_file, "[Power] GameState.DebugPrintPower() - CREATE_GAME")
        # Process a single line directly
        parser._process_line("[Power] GameState.DebugPrintPower() - CREATE_GAME")
        assert parser._state["game_active"] is True

    def test_second_create_game_resets(self, tmp_path):
        parser, log_file, states = _make_parser(tmp_path)
        parser._state["turn"] = 5
        parser._process_line("[Power] GameState.DebugPrintPower() - CREATE_GAME")
        assert parser._state["turn"] == 0


# ---------------------------------------------------------------------------
# LogParser – TAG_CHANGE for game-level tags
# ---------------------------------------------------------------------------

class TestTagChange:
    def test_turn_update(self, tmp_path):
        parser, _, _ = _make_parser(tmp_path)
        parser._state["game_active"] = True
        parser._process_line(
            "[Power] GameState.DebugPrintPower() - "
            "TAG_CHANGE Entity=GameEntity tag=TURN value=3"
        )
        assert parser._state["turn"] == 3

    def test_state_complete(self, tmp_path):
        parser, _, _ = _make_parser(tmp_path)
        parser._state["game_active"] = True
        parser._process_line(
            "[Power] GameState.DebugPrintPower() - "
            "TAG_CHANGE Entity=GameEntity tag=STATE value=COMPLETE"
        )
        assert parser._state["game_active"] is False

    def test_state_running(self, tmp_path):
        parser, _, _ = _make_parser(tmp_path)
        parser._process_line(
            "[Power] GameState.DebugPrintPower() - "
            "TAG_CHANGE Entity=GameEntity tag=STATE value=RUNNING"
        )
        assert parser._state["game_active"] is True


# ---------------------------------------------------------------------------
# LogParser – entity block (FULL_ENTITY)
# ---------------------------------------------------------------------------

class TestFullEntity:
    def test_card_id_registered(self, tmp_path):
        parser, _, _ = _make_parser(tmp_path)
        parser._process_line(
            "[Power] GameState.DebugPrintPower() - "
            "FULL_ENTITY - Creating ID=74 CardID=EX1_298"
        )
        ent = parser._registry.get(74)
        assert ent.get("card_id") == "EX1_298"

    def test_indented_tags_captured(self, tmp_path):
        parser, _, _ = _make_parser(tmp_path)
        parser._process_line(
            "[Power] GameState.DebugPrintPower() - "
            "FULL_ENTITY - Creating ID=74 CardID=EX1_298"
        )
        parser._process_line("    tag=CARDTYPE value=MINION")
        ent = parser._registry.get(74)
        assert ent.get("CARDTYPE") == "MINION"


# ---------------------------------------------------------------------------
# LogParser – zone change (hand → play)
# ---------------------------------------------------------------------------

class TestZoneChange:
    def _setup_minion(self, parser, eid: int, player: int = 1) -> None:
        """Populate the registry with a minion entity."""
        parser._registry.update(
            eid, name="Test Minion", card_id="TEST_001",
            CARDTYPE="MINION", player=player,
            ATK="3", HEALTH="3", MAX_HEALTH="3",
        )
        parser._local_player_id = 1

    def test_zone_play_adds_to_player_board(self, tmp_path):
        parser, _, _ = _make_parser(tmp_path)
        self._setup_minion(parser, 99, player=1)
        parser._process_line(
            "[Power] GameState.DebugPrintPower() - "
            "TAG_CHANGE Entity=[name=Test Minion id=99 zone=PLAY zonePos=1 "
            "cardId=TEST_001 player=1] tag=ZONE value=PLAY"
        )
        assert any(m["id"] == 99 for m in parser._state["player"]["board"])

    def test_zone_hand_adds_to_player_hand(self, tmp_path):
        parser, _, _ = _make_parser(tmp_path)
        self._setup_minion(parser, 100, player=1)
        parser._process_line(
            "[Power] GameState.DebugPrintPower() - "
            "TAG_CHANGE Entity=[name=Test Minion id=100 zone=HAND zonePos=1 "
            "cardId=TEST_001 player=1] tag=ZONE value=HAND"
        )
        assert any(c["id"] == 100 for c in parser._state["player"]["hand"])

    def test_zone_graveyard_removes_from_board(self, tmp_path):
        parser, _, _ = _make_parser(tmp_path)
        self._setup_minion(parser, 101, player=1)
        # Put it on the board first
        parser._state["player"]["board"].append({"id": 101, "name": "Test Minion"})
        parser._process_line(
            "[Power] GameState.DebugPrintPower() - "
            "TAG_CHANGE Entity=[name=Test Minion id=101 zone=GRAVEYARD zonePos=0 "
            "cardId=TEST_001 player=1] tag=ZONE value=GRAVEYARD"
        )
        assert not any(m["id"] == 101 for m in parser._state["player"]["board"])

    def test_enemy_minion_goes_to_enemy_board(self, tmp_path):
        parser, _, _ = _make_parser(tmp_path)
        self._setup_minion(parser, 200, player=2)
        parser._local_player_id = 1
        parser._process_line(
            "[Power] GameState.DebugPrintPower() - "
            "TAG_CHANGE Entity=[name=Enemy Minion id=200 zone=PLAY zonePos=1 "
            "cardId=TEST_002 player=2] tag=ZONE value=PLAY"
        )
        assert any(m["id"] == 200 for m in parser._state["enemy"]["board"])


# ---------------------------------------------------------------------------
# LogParser – mana tracking
# ---------------------------------------------------------------------------

class TestManaTracking:
    def test_total_mana_update(self, tmp_path):
        parser, _, _ = _make_parser(tmp_path)
        parser._local_player_id = 1
        # Register a player entity
        parser._registry.update(2, CARDTYPE="PLAYER", player=1)
        parser._process_line(
            "[Power] GameState.DebugPrintPower() - "
            "TAG_CHANGE Entity=[name=Player id=2 zone=PLAY zonePos=0 "
            "cardId= player=1] tag=RESOURCES value=7"
        )
        assert parser._state["player"]["mana"]["total"] == 7

    def test_overload_update(self, tmp_path):
        parser, _, _ = _make_parser(tmp_path)
        parser._local_player_id = 1
        parser._registry.update(2, CARDTYPE="PLAYER", player=1)
        parser._process_line(
            "[Power] GameState.DebugPrintPower() - "
            "TAG_CHANGE Entity=[name=Player id=2 zone=PLAY zonePos=0 "
            "cardId= player=1] tag=OVERLOADED_CRYSTALS value=2"
        )
        assert parser._state["player"]["mana"]["overload"] == 2


# ---------------------------------------------------------------------------
# LogParser – board stat updates (ATK / HEALTH)
# ---------------------------------------------------------------------------

class TestBoardStatUpdates:
    def test_attack_update_on_board_minion(self, tmp_path):
        parser, _, _ = _make_parser(tmp_path)
        parser._local_player_id = 1
        # Manually place a minion on the board
        parser._state["player"]["board"].append(
            {"id": 55, "name": "Buffed Minion", "attack": 3, "health": 3}
        )
        parser._registry.update(55, name="Buffed Minion", player=1, CARDTYPE="MINION")
        parser._process_line(
            "[Power] GameState.DebugPrintPower() - "
            "TAG_CHANGE Entity=[name=Buffed Minion id=55 zone=PLAY zonePos=1 "
            "cardId=TEST player=1] tag=ATK value=5"
        )
        assert parser._state["player"]["board"][0]["attack"] == 5

    def test_health_update_on_board_minion(self, tmp_path):
        parser, _, _ = _make_parser(tmp_path)
        parser._local_player_id = 1
        parser._state["player"]["board"].append(
            {"id": 56, "name": "Damaged Minion", "attack": 3, "health": 5}
        )
        parser._registry.update(56, name="Damaged Minion", player=1, CARDTYPE="MINION")
        parser._process_line(
            "[Power] GameState.DebugPrintPower() - "
            "TAG_CHANGE Entity=[name=Damaged Minion id=56 zone=PLAY zonePos=1 "
            "cardId=TEST player=1] tag=HEALTH value=2"
        )
        assert parser._state["player"]["board"][0]["health"] == 2


# ---------------------------------------------------------------------------
# LogParser – state property returns a deep copy
# ---------------------------------------------------------------------------

class TestStateProperty:
    def test_state_is_deep_copy(self, tmp_path):
        parser, _, _ = _make_parser(tmp_path)
        s1 = parser.state
        s2 = parser.state
        s1["turn"] = 999
        assert s2["turn"] == 0  # original unchanged
