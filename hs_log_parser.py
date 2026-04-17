"""
hs_log_parser.py
----------------
A robust, real-time parser for Hearthstone Power.log files.

The parser monitors the log file continuously (like 'tail -f') and maintains a
structured game-state dictionary that is updated incrementally as new log lines
are appended.

Extracted events
~~~~~~~~~~~~~~~~
* Player hand cards (drawn / mulliganed)
* Board minions (summoned, died, attack, health, stats)
* Mana crystals (current, total, overload)
* Enemy hero class and hero power
* Game start and end events

Typical usage
~~~~~~~~~~~~~
    from hs_log_parser import LogParser

    def on_state_change(state: dict) -> None:
        print(state)

    parser = LogParser(callback=on_state_change)
    parser.start()          # blocking – run in a thread for real usage
"""

from __future__ import annotations

import copy
import re
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any

# ---------------------------------------------------------------------------
# Regex patterns for Power.log lines
# ---------------------------------------------------------------------------

# Prefix produced by every actionable line
_PREFIX = re.compile(r"\[Power\] GameState\.DebugPrint\w+\(\) - ")

# TAG_CHANGE  – the most common event
_TAG_CHANGE = re.compile(
    r"TAG_CHANGE\s+Entity=(?P<entity>.+?)\s+tag=(?P<tag>\w+)\s+value=(?P<value>\S+)"
)

# FULL_ENTITY / SHOW_ENTITY headers (card reveal)
_FULL_ENTITY = re.compile(
    r"FULL_ENTITY\s+-\s+(?:Creating|Updating)\s+ID=(?P<entity_id>\d+)"
    r"(?:\s+CardID=(?P<card_id>\S+))?"
)
_SHOW_ENTITY = re.compile(
    r"SHOW_ENTITY\s+-\s+Updating\s+Entity=(?P<entity>.+?)"
    r"\s+CardID=(?P<card_id>\S+)"
)

# Indented tag=value pairs following a FULL_ENTITY / SHOW_ENTITY block
_TAG_VALUE = re.compile(r"^\s+tag=(?P<tag>\w+)\s+value=(?P<value>\S+)")

# BLOCK_START to detect attack / trigger / play sequences
_BLOCK_START = re.compile(
    r"BLOCK_START\s+BlockType=(?P<block_type>\w+)\s+Entity=(?P<entity>.+?)"
    r"\s+EffectCardId=\S*\s+EffectIndex=\S+\s+Target=(?P<target>.*)"
)

# Entity string formats:
#   [name=Ragnaros id=74 zone=PLAY zonePos=1 cardId=EX1_298 player=1]
#   GameEntity
#   <number>
_ENTITY_BRACKET = re.compile(
    # Use .+? (instead of [^\]]+?) so names containing nested brackets
    # like "UNKNOWN ENTITY [cardType=INVALID]" are matched correctly.
    r"\[name=(?P<name>.+?)\s+id=(?P<id>\d+)"
    r"(?:\s+zone=(?P<zone>\w+))?"
    r"(?:\s+zonePos=(?P<zone_pos>\d+))?"
    # \S* (not \S+) allows an empty cardId= value that appears for unknown entities
    r"(?:\s+cardId=(?P<card_id>\S*))?"
    r"(?:\s+player=(?P<player>\d+))?"
    r"\]"
)
_ENTITY_ID = re.compile(r"^(\d+)$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_entity(raw: str) -> Dict[str, Any]:
    """Return a dict with whatever fields are present in the entity string."""
    m = _ENTITY_BRACKET.search(raw)
    if m:
        result: Dict[str, Any] = {
            "name": m.group("name"),
            "id": int(m.group("id")),
        }
        if m.group("zone"):
            result["zone"] = m.group("zone")
        if m.group("zone_pos") is not None:
            result["zone_pos"] = int(m.group("zone_pos"))
        if m.group("card_id"):
            result["card_id"] = m.group("card_id")
        if m.group("player"):
            result["player"] = int(m.group("player"))
        return result
    m2 = _ENTITY_ID.match(raw.strip())
    if m2:
        return {"id": int(m2.group(1))}
    return {"name": raw.strip()}


def _empty_game_state() -> Dict[str, Any]:
    return {
        "game_active": False,
        "turn": 0,
        "player": {
            "id": None,          # player index in the log (1 or 2)
            "hand": [],          # list of entity-dicts
            "board": [],         # list of entity-dicts
            "mana": {"current": 0, "total": 0, "overload": 0},
            "hero": {"name": "", "card_id": "", "health": 30, "armor": 0},
            "hero_power": {"card_id": "", "used": False},
        },
        "enemy": {
            "id": None,
            "hand_count": 0,
            "board": [],
            "hero": {"name": "", "class": "", "card_id": "", "health": 30, "armor": 0},
            "hero_power": {"card_id": "", "used": False},
        },
    }


# ---------------------------------------------------------------------------
# Entity registry
# ---------------------------------------------------------------------------

class _EntityRegistry:
    """Tracks every entity seen in the log keyed by its numeric ID."""

    def __init__(self) -> None:
        self._entities: Dict[int, Dict[str, Any]] = {}

    def get(self, entity_id: int) -> Dict[str, Any]:
        return self._entities.setdefault(entity_id, {"id": entity_id})

    def update(self, entity_id: int, **kwargs: Any) -> None:
        self._entities.setdefault(entity_id, {"id": entity_id}).update(kwargs)

    def find_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        for e in self._entities.values():
            if e.get("name") == name:
                return e
        return None

    def reset(self) -> None:
        self._entities.clear()


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------

class LogParser:
    """
    Monitors a Hearthstone Power.log file and maintains an up-to-date
    game-state dictionary.

    Parameters
    ----------
    log_path:
        Path to Power.log.  If *None*, :func:`utils.find_hs_log_path` is used.
    callback:
        Optional callable invoked with a *copy* of the game state whenever a
        meaningful change is detected.
    poll_interval:
        Seconds between file-read attempts when no new data is available.
    """

    def __init__(
        self,
        log_path: Optional[str | Path] = None,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        poll_interval: float = 0.2,
    ) -> None:
        if log_path is None:
            try:
                from utils import find_hs_log_path
                log_path = find_hs_log_path()
            except Exception:
                log_path = "Power.log"
        self.log_path = Path(log_path)
        self.callback = callback
        self.poll_interval = poll_interval

        self._state: Dict[str, Any] = _empty_game_state()
        self._registry = _EntityRegistry()
        self._stop_event = threading.Event()

        # Parser state for tracking multi-line entity blocks
        self._current_entity_id: Optional[int] = None
        self._in_entity_block: bool = False

        # Track which player-number is "us" (the local player)
        self._local_player_id: Optional[int] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Block and tail the log file until :meth:`stop` is called."""
        self._stop_event.clear()
        self._tail()

    def start_async(self) -> threading.Thread:
        """Start the parser in a background thread and return it."""
        self._stop_event.clear()
        t = threading.Thread(target=self._tail, daemon=True, name="hs-log-parser")
        t.start()
        return t

    def stop(self) -> None:
        """Signal the background loop to terminate."""
        self._stop_event.set()

    @property
    def state(self) -> Dict[str, Any]:
        """Return a deep-copy of the current game state."""
        return copy.deepcopy(self._state)

    # ------------------------------------------------------------------
    # Tail loop
    # ------------------------------------------------------------------

    def _tail(self) -> None:
        file_obj = None
        file_pos = 0

        while not self._stop_event.is_set():
            try:
                if file_obj is None:
                    if self.log_path.exists():
                        file_obj = open(self.log_path, "r", encoding="utf-8", errors="replace")
                        file_pos = 0
                    else:
                        time.sleep(self.poll_interval)
                        continue

                # Detect log rotation / file truncation
                current_size = self.log_path.stat().st_size
                if current_size < file_pos:
                    file_obj.close()
                    file_obj = None
                    self._on_new_game()
                    continue

                lines = file_obj.readlines()
                if lines:
                    file_pos = file_obj.tell()
                    changed = False
                    for line in lines:
                        changed = self._process_line(line.rstrip("\n")) or changed
                    if changed and self.callback:
                        self.callback(self.state)
                else:
                    time.sleep(self.poll_interval)

            except OSError:
                if file_obj:
                    file_obj.close()
                    file_obj = None
                time.sleep(self.poll_interval)

        if file_obj:
            file_obj.close()

    # ------------------------------------------------------------------
    # Line dispatch
    # ------------------------------------------------------------------

    def _process_line(self, line: str) -> bool:
        """Parse a single log line; return True if game state was modified."""
        # Handle indented tag=value lines inside an entity block
        m_tv = _TAG_VALUE.match(line)
        if m_tv and self._in_entity_block and self._current_entity_id is not None:
            self._registry.update(
                self._current_entity_id,
                **{m_tv.group("tag"): m_tv.group("value")},
            )
            self._apply_entity_tag(
                self._current_entity_id,
                m_tv.group("tag"),
                m_tv.group("value"),
            )
            return True

        # Only act on Power lines
        if not _PREFIX.search(line):
            self._in_entity_block = False
            return False

        # Strip prefix for easier matching
        content = _PREFIX.sub("", line).strip()

        # TAG_CHANGE
        m = _TAG_CHANGE.match(content)
        if m:
            self._in_entity_block = False
            return self._handle_tag_change(
                m.group("entity"), m.group("tag"), m.group("value")
            )

        # FULL_ENTITY header
        m = _FULL_ENTITY.match(content)
        if m:
            eid = int(m.group("entity_id"))
            card_id = m.group("card_id") or ""
            if card_id and card_id != "0":
                self._registry.update(eid, card_id=card_id)
            self._current_entity_id = eid
            self._in_entity_block = True
            return False  # tags will arrive on subsequent lines

        # SHOW_ENTITY header
        m = _SHOW_ENTITY.match(content)
        if m:
            ep = _parse_entity(m.group("entity"))
            eid = ep.get("id")
            card_id = m.group("card_id") or ""
            if eid is not None:
                if card_id and card_id != "0":
                    self._registry.update(eid, card_id=card_id)
                self._registry.update(eid, **{k: v for k, v in ep.items() if k != "id"})
                self._current_entity_id = eid
                self._in_entity_block = True
            return False

        # BLOCK_START
        m = _BLOCK_START.match(content)
        if m:
            self._in_entity_block = False
            return self._handle_block_start(
                m.group("block_type"), m.group("entity"), m.group("target")
            )

        # Game start / end signals
        if "CREATE_GAME" in content:
            self._on_new_game()
            return True

        self._in_entity_block = False
        return False

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_new_game(self) -> None:
        self._state = _empty_game_state()
        self._registry.reset()
        self._current_entity_id = None
        self._in_entity_block = False
        self._local_player_id = None
        self._state["game_active"] = True

    def _handle_tag_change(self, entity_raw: str, tag: str, value: str) -> bool:
        ep = _parse_entity(entity_raw)
        eid = ep.get("id")
        name = ep.get("name", "")

        # Detect game-level tags
        if name == "GameEntity" or eid == 1:
            return self._handle_game_tag(tag, value)

        if eid is None:
            return False

        # Update registry
        self._registry.update(eid, **{k: v for k, v in ep.items() if k != "id"})
        self._registry.update(eid, **{tag: value})

        return self._apply_entity_tag(eid, tag, value)

    def _handle_game_tag(self, tag: str, value: str) -> bool:
        if tag == "TURN":
            self._state["turn"] = int(value)
            return True
        if tag == "STATE" and value == "COMPLETE":
            self._state["game_active"] = False
            return True
        if tag == "STATE" and value == "RUNNING":
            self._state["game_active"] = True
            return True
        return False

    def _apply_entity_tag(self, eid: int, tag: str, value: str) -> bool:
        """Translate a raw tag/value on entity *eid* into game-state mutations."""
        ent = self._registry.get(eid)
        player_id = ent.get("player") or ent.get("CONTROLLER")
        zone = ent.get("zone") or ent.get("ZONE")
        card_type = ent.get("CARDTYPE", "")

        changed = False

        # Determine if this entity is the local player or enemy
        # Heuristic: first HERO we discover whose player=1 is "us"
        if card_type == "HERO" and tag == "HEALTH":
            self._maybe_register_hero(eid, player_id, value)
            changed = True

        if tag == "ZONE":
            changed = self._handle_zone_change(eid, value) or changed

        if tag in ("ATK", "ATTACK"):
            changed = self._update_board_entity(eid, "attack", int(value)) or changed

        if tag == "HEALTH":
            changed = self._update_board_entity(eid, "health", int(value)) or changed

        if tag == "MAX_HEALTH":
            changed = self._update_board_entity(eid, "max_health", int(value)) or changed

        if tag == "TAUNT":
            changed = self._update_board_entity(eid, "taunt", value == "1") or changed

        if tag == "DIVINE_SHIELD":
            changed = self._update_board_entity(eid, "divine_shield", value == "1") or changed

        if tag == "CHARGE":
            changed = self._update_board_entity(eid, "charge", value == "1") or changed

        if tag == "EXHAUSTED" and card_type == "HERO_POWER":
            changed = self._update_hero_power_used(eid, value == "1") or changed

        if tag == "RESOURCES":
            changed = self._update_mana(eid, "total", int(value)) or changed

        if tag == "RESOURCES_USED":
            total = self._get_mana_total(eid)
            current = max(0, total - int(value))
            changed = self._update_mana(eid, "current", current) or changed

        if tag == "OVERLOADED_CRYSTALS":
            changed = self._update_mana(eid, "overload", int(value)) or changed

        if tag == "ARMOR":
            changed = self._update_hero_armor(eid, int(value)) or changed

        return changed

    # ------------------------------------------------------------------
    # Zone-change handling (cards moving between hand/play/graveyard)
    # ------------------------------------------------------------------

    def _handle_zone_change(self, eid: int, new_zone: str) -> bool:
        ent = self._registry.get(eid)
        player_id = ent.get("player") or ent.get("CONTROLLER")
        card_type = ent.get("CARDTYPE", "")

        if card_type not in ("MINION", "SPELL", "WEAPON", ""):
            return False

        side = self._side_for_player(player_id)
        if side is None:
            return False

        # Remove from all zone lists first
        self._remove_from_zones(eid)

        if new_zone == "HAND" and side == "player":
            self._state["player"]["hand"].append(self._entity_snapshot(eid))
            return True

        if new_zone == "PLAY" and card_type == "MINION":
            target = self._state[side]["board"]
            target.append(self._entity_snapshot(eid))
            return True

        # Card left the board (GRAVEYARD / DECK / REMOVED_FROM_GAME)
        return True

    # ------------------------------------------------------------------
    # Hero & hero-power tracking
    # ------------------------------------------------------------------

    def _maybe_register_hero(
        self, eid: int, player_id: Optional[int], health_str: str
    ) -> None:
        ent = self._registry.get(eid)
        card_id = ent.get("card_id", "")
        hero_name = ent.get("name", "")
        health = int(health_str)

        if self._local_player_id is None and player_id == 1:
            self._local_player_id = 1

        side = self._side_for_player(player_id)
        if side is None:
            return

        self._state[side]["hero"]["health"] = health
        if card_id:
            self._state[side]["hero"]["card_id"] = card_id
        if hero_name:
            self._state[side]["hero"]["name"] = hero_name
            if side == "enemy":
                # Derive class from hero name / card_id
                self._state["enemy"]["hero"]["class"] = self._class_from_card_id(card_id)

    def _class_from_card_id(self, card_id: str) -> str:
        """Best-effort hero-class lookup from a card ID prefix."""
        _HERO_MAP = {
            "HERO_01": "Warrior",
            "HERO_02": "Shaman",
            "HERO_03": "Rogue",
            "HERO_04": "Paladin",
            "HERO_05": "Hunter",
            "HERO_06": "Druid",
            "HERO_07": "Warlock",
            "HERO_08": "Mage",
            "HERO_09": "Priest",
            "HERO_10": "Demon Hunter",
            "HERO_11": "Death Knight",
        }
        prefix = card_id[:7] if len(card_id) >= 7 else card_id
        return _HERO_MAP.get(prefix, "")

    def _update_hero_power_used(self, eid: int, used: bool) -> bool:
        ent = self._registry.get(eid)
        player_id = ent.get("player") or ent.get("CONTROLLER")
        side = self._side_for_player(player_id)
        if side is None:
            return False
        self._state[side]["hero_power"]["used"] = used
        card_id = ent.get("card_id", "")
        if card_id:
            self._state[side]["hero_power"]["card_id"] = card_id
        return True

    def _update_hero_armor(self, eid: int, armor: int) -> bool:
        ent = self._registry.get(eid)
        card_type = ent.get("CARDTYPE", "")
        if card_type != "HERO":
            return False
        player_id = ent.get("player") or ent.get("CONTROLLER")
        side = self._side_for_player(player_id)
        if side is None:
            return False
        self._state[side]["hero"]["armor"] = armor
        return True

    # ------------------------------------------------------------------
    # Mana
    # ------------------------------------------------------------------

    def _get_mana_total(self, player_eid: int) -> int:
        ent = self._registry.get(player_eid)
        player_id = ent.get("player")
        side = self._side_for_player(player_id)
        if side == "player":
            return self._state["player"]["mana"]["total"]
        return 0

    def _update_mana(self, player_eid: int, field: str, value: int) -> bool:
        ent = self._registry.get(player_eid)
        # RESOURCES tags appear on the player entity (CARDTYPE=PLAYER)
        player_id = ent.get("player") or ent.get("CONTROLLER")
        side = self._side_for_player(player_id)
        if side != "player":
            return False
        self._state["player"]["mana"][field] = value
        return True

    # ------------------------------------------------------------------
    # Board helpers
    # ------------------------------------------------------------------

    def _update_board_entity(self, eid: int, field: str, value: Any) -> bool:
        for side in ("player", "enemy"):
            for minion in self._state[side]["board"]:
                if minion.get("id") == eid:
                    minion[field] = value
                    return True
        return False

    def _remove_from_zones(self, eid: int) -> None:
        for side in ("player", "enemy"):
            self._state[side]["board"] = [
                m for m in self._state[side]["board"] if m.get("id") != eid
            ]
        self._state["player"]["hand"] = [
            c for c in self._state["player"]["hand"] if c.get("id") != eid
        ]

    def _entity_snapshot(self, eid: int) -> Dict[str, Any]:
        ent = self._registry.get(eid)
        return {
            "id": eid,
            "name": ent.get("name", ""),
            "card_id": ent.get("card_id", ""),
            "attack": int(ent.get("ATK", 0)),
            "health": int(ent.get("HEALTH", 0)),
            "max_health": int(ent.get("MAX_HEALTH", 0)),
            "taunt": ent.get("TAUNT", "0") == "1",
            "divine_shield": ent.get("DIVINE_SHIELD", "0") == "1",
            "charge": ent.get("CHARGE", "0") == "1",
            "zone_pos": int(ent.get("zone_pos") or ent.get("ZONE_POSITION") or 0),
        }

    # ------------------------------------------------------------------
    # BLOCK_START
    # ------------------------------------------------------------------

    def _handle_block_start(
        self, block_type: str, entity_raw: str, target_raw: str
    ) -> bool:
        if block_type == "PLAY":
            ep = _parse_entity(entity_raw)
            eid = ep.get("id")
            if eid:
                self._registry.update(eid, **{k: v for k, v in ep.items() if k != "id"})
            return False  # zone change will follow
        return False

    # ------------------------------------------------------------------
    # Side resolution
    # ------------------------------------------------------------------

    def _side_for_player(self, player_id: Optional[int]) -> Optional[str]:
        """Return 'player' or 'enemy' for a given player index."""
        if player_id is None:
            return None

        # First PLAYER entity seen with player=1 is the local player
        if self._local_player_id is None:
            self._local_player_id = player_id

        if player_id == self._local_player_id:
            return "player"
        return "enemy"
