# hs_ai_assistant

A Python-based AI assistant for Hearthstone that reads the game log in
real-time, simulates board states, runs a Monte Carlo Tree Search to
recommend plays, and displays the suggestion in a transparent overlay window.

---

## Components

| File | Description |
|------|-------------|
| `hs_log_parser.py` | Real-time `Power.log` parser (tail-based) |
| `hs_simulator.py` | Lightweight game-state / combat engine |
| `hs_mcts.py` | Determinized Monte Carlo Tree Search AI |
| `overlay.py` | Transparent, click-through GUI overlay |
| `utils.py` | Cross-platform Hearthstone log-path finder |
| `card_db.py` | Real card data from [HearthSim/hsdata](https://github.com/HearthSim/hsdata) via the `hearthstone` package |

---

## Quick start

### 1. Find the log file automatically

```python
from utils import find_hs_log_path
log_path = find_hs_log_path()   # Windows / macOS / Linux + GUI fallback
print(log_path)
```

### 2. Parse the live game state

```python
from hs_log_parser import LogParser

def on_update(state):
    print("Turn:", state["turn"])
    print("Hand:", state["player"]["hand"])
    print("Board:", state["player"]["board"])

parser = LogParser(callback=on_update)
parser.start_async()   # non-blocking
```

### 3. Simulate board states

```python
from hs_simulator import Card, CardType, GameState, Hero, Mechanic

fireball = Card(id="CS2_029", name="Fireball", cost=4, attack=6,
                card_type=CardType.SPELL)
target   = Card(name="Scary Minion", cost=5, attack=4, health=5)

gs = GameState(
    player_mana=4, player_max_mana=4,
    player_hand=[fireball],
    enemy_board=[target],
    player_hero=Hero(),
    enemy_hero=Hero(),
)
gs.play_card(0, target=0, target_side="enemy")
print(gs.enemy_board)   # [] - target is dead
```

### 4. Run the MCTS AI

```python
from hs_mcts import best_action

action = best_action(gs, time_limit_ms=1000, enemy_deck_archetype="Mage")
print(action)   # e.g. {'type': 'play', 'card_index': 0, 'target': None}
```

### 5. Show the overlay

```python
from overlay import AIOverlay

overlay = AIOverlay()
overlay.update_suggestion("Fireball", win_rate_delta=12.5)
overlay.run()   # blocking; use overlay.start_async() in production
```

---

## Architecture

```
Power.log  -->  LogParser  -->  game_state dict
                                      |
                               hs_simulator
                               (GameState / Card)
                                      |
                                  hs_mcts
                              (best_action)
                                      |
                                  overlay
                              (AIOverlay.update_suggestion)

card_db  ─────────────────────────> hs_mcts
(hearthstone/hsdata)          (determinize card pool)
```

---

## Requirements

* Python 3.8+
* `hearthstone` + `hearthstone-data` (**optional**, for real card data):
  ```bash
  pip install -r requirements.txt
  ```
  Without these packages `card_db.is_available()` returns `False` and the
  MCTS falls back to built-in minimal card lists.
* Standard library only otherwise (`tkinter` for the overlay and path-finder GUI)
* `pytest` for running the test suite

---

## Running tests

```bash
pip install pytest
python -m pytest tests/ -v
```

---

## Card database (`card_db.py`)

`card_db.py` integrates the [HearthSim/hsdata](https://github.com/HearthSim/hsdata)
dataset via the `hearthstone` Python package:

```python
from card_db import get_cards_for_class, make_card, is_available

if is_available():
    mage_cards = get_cards_for_class("Mage")      # list of card dicts
    fireball   = make_card(mage_cards[0])          # hs_simulator.Card
```

The database contains **34 000+ cards** (version 35.2).  Only collectible
Minions, Spells, and Weapons are returned.  Neutral cards are automatically
included unless `include_neutral=False` is passed.

---

## Notes

* The overlay uses `tkinter` (included with CPython). On Windows it also
  applies `WS_EX_LAYERED | WS_EX_TRANSPARENT` via `ctypes` so mouse
  clicks pass through to the game.
* MCTS determinization now uses the full real card pool from `card_db` when
  the optional packages are installed; otherwise it falls back to the built-in
  `_FALLBACK_ARCHETYPES` in `hs_mcts.py`.
* Set the `HS_LOG_PATH` environment variable to skip auto-detection:
  ```
  HS_LOG_PATH=C:\path\to\Hearthstone\Logs\Power.log
  ```
