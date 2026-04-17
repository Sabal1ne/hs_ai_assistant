"""
tests/test_card_db.py
---------------------
Unit tests for the card_db module.

When hearthstone/hearthstone_data are installed the "live" code-paths are
tested.  When they are absent every test falls back to checking the graceful
fallback behaviour.
"""
from __future__ import annotations

import pytest

import card_db
from card_db import (
    ALL_CLASS_NAMES,
    get_cards_for_class,
    is_available,
    make_card,
)


# ---------------------------------------------------------------------------
# Availability probe
# ---------------------------------------------------------------------------

class TestIsAvailable:
    def test_returns_bool(self):
        result = is_available()
        assert isinstance(result, bool)

    def test_idempotent(self):
        # Calling twice must return the same value (cached).
        assert is_available() == is_available()


# ---------------------------------------------------------------------------
# ALL_CLASS_NAMES constant
# ---------------------------------------------------------------------------

class TestAllClassNames:
    def test_is_tuple(self):
        assert isinstance(ALL_CLASS_NAMES, tuple)

    def test_contains_expected_classes(self):
        expected = {
            "Mage", "Warrior", "Paladin", "Hunter", "Rogue",
            "Druid", "Warlock", "Shaman", "Priest",
            "Demon Hunter", "Death Knight", "Neutral",
        }
        assert expected.issubset(set(ALL_CLASS_NAMES))


# ---------------------------------------------------------------------------
# get_cards_for_class
# ---------------------------------------------------------------------------

class TestGetCardsForClass:
    def test_unknown_class_returns_empty(self):
        result = get_cards_for_class("NotAClass")
        assert result == []

    def test_returns_list(self):
        result = get_cards_for_class("Mage")
        assert isinstance(result, list)

    def test_result_cached(self):
        # Second call returns same object (cached).
        first = get_cards_for_class("Priest")
        second = get_cards_for_class("Priest")
        assert first is second

    @pytest.mark.skipif(not is_available(), reason="hearthstone_data not installed")
    def test_mage_cards_nonempty_when_db_available(self):
        cards = get_cards_for_class("Mage")
        assert len(cards) > 0

    @pytest.mark.skipif(not is_available(), reason="hearthstone_data not installed")
    def test_card_dict_has_required_keys(self):
        cards = get_cards_for_class("Mage")
        required = {"id", "name", "cost", "attack", "health", "card_type", "mechanics"}
        for card in cards[:10]:
            assert required.issubset(card.keys()), f"Missing keys in {card}"

    @pytest.mark.skipif(not is_available(), reason="hearthstone_data not installed")
    def test_card_type_strings_valid(self):
        valid_types = {"MINION", "SPELL", "WEAPON"}
        cards = get_cards_for_class("Warrior")
        for card in cards:
            assert card["card_type"] in valid_types, (
                f"Unexpected card_type {card['card_type']!r} for {card['name']!r}"
            )

    @pytest.mark.skipif(not is_available(), reason="hearthstone_data not installed")
    def test_mechanics_is_set(self):
        cards = get_cards_for_class("Paladin")
        for card in cards[:20]:
            assert isinstance(card["mechanics"], set), (
                f"mechanics should be a set for {card['name']!r}"
            )

    @pytest.mark.skipif(not is_available(), reason="hearthstone_data not installed")
    def test_mechanics_values_are_known_strings(self):
        known = {
            "TAUNT", "CHARGE", "DIVINE_SHIELD", "BATTLECRY",
            "DEATHRATTLE", "POISONOUS", "WINDFURY", "LIFESTEAL",
        }
        cards = get_cards_for_class("Hunter")
        for card in cards:
            for mech in card["mechanics"]:
                assert mech in known, (
                    f"Unknown mechanic {mech!r} on {card['name']!r}"
                )

    @pytest.mark.skipif(not is_available(), reason="hearthstone_data not installed")
    def test_no_duplicate_card_ids(self):
        cards = get_cards_for_class("Shaman")
        ids = [c["id"] for c in cards]
        assert len(ids) == len(set(ids)), "Duplicate card IDs found"

    @pytest.mark.skipif(not is_available(), reason="hearthstone_data not installed")
    def test_include_neutral_true_returns_more_cards(self):
        with_neutral = get_cards_for_class("Druid", include_neutral=True)
        without_neutral = get_cards_for_class("Druid", include_neutral=False)
        assert len(with_neutral) >= len(without_neutral)

    @pytest.mark.skipif(not is_available(), reason="hearthstone_data not installed")
    def test_all_known_classes_return_cards(self):
        for class_name in ALL_CLASS_NAMES:
            cards = get_cards_for_class(class_name)
            assert len(cards) > 0, f"No cards returned for class {class_name!r}"

    @pytest.mark.skipif(not is_available(), reason="hearthstone_data not installed")
    def test_specific_card_fireball_present_in_mage(self):
        cards = get_cards_for_class("Mage")
        ids = {c["id"] for c in cards}
        assert "CS2_029" in ids, "Fireball (CS2_029) not found in Mage card pool"

    @pytest.mark.skipif(not is_available(), reason="hearthstone_data not installed")
    def test_taunt_mechanic_on_known_taunt_card(self):
        """Annoy-o-Tron (GVG_085) has TAUNT and DIVINE_SHIELD."""
        cards = get_cards_for_class("Neutral")
        card_map = {c["id"]: c for c in cards}
        if "GVG_085" in card_map:
            card = card_map["GVG_085"]
            assert "TAUNT" in card["mechanics"]
            assert "DIVINE_SHIELD" in card["mechanics"]

    @pytest.mark.skipif(not is_available(), reason="hearthstone_data not installed")
    def test_cost_and_stats_are_ints(self):
        cards = get_cards_for_class("Warlock")
        for card in cards[:20]:
            assert isinstance(card["cost"], int), f"cost not int for {card['name']!r}"
            assert isinstance(card["attack"], int), f"attack not int for {card['name']!r}"
            assert isinstance(card["health"], int), f"health not int for {card['name']!r}"

    def test_fallback_when_unavailable(self, monkeypatch):
        """get_cards_for_class returns [] when DB is unavailable."""
        monkeypatch.setattr(card_db, "_HS_AVAILABLE", False)
        # Clear cache entry if present
        card_db._CLASS_CACHE.pop("Mage|True", None)
        result = get_cards_for_class("Mage")
        assert result == []
        # Restore
        monkeypatch.setattr(card_db, "_HS_AVAILABLE", None)


# ---------------------------------------------------------------------------
# make_card
# ---------------------------------------------------------------------------

class TestMakeCard:
    def test_make_card_from_minion_dict(self):
        from hs_simulator import Card, CardType, Mechanic
        d = {
            "id": "TEST_001",
            "name": "Test Minion",
            "cost": 3,
            "attack": 3,
            "health": 3,
            "card_type": "MINION",
            "mechanics": {"TAUNT"},
        }
        card = make_card(d)
        assert isinstance(card, Card)
        assert card.name == "Test Minion"
        assert card.cost == 3
        assert card.attack == 3
        assert card.health == 3
        assert card.card_type == CardType.MINION
        assert Mechanic.TAUNT in card.mechanics

    def test_make_card_from_spell_dict(self):
        from hs_simulator import Card, CardType
        d = {
            "id": "TEST_002",
            "name": "Test Spell",
            "cost": 2,
            "attack": 0,
            "health": 0,
            "card_type": "SPELL",
            "mechanics": set(),
        }
        card = make_card(d)
        assert card.card_type == CardType.SPELL
        # Spells with health=0 should get health=1 from the safeguard in make_card
        assert card.health == 1

    def test_make_card_all_mechanics(self):
        from hs_simulator import Mechanic
        all_mechs = {
            "TAUNT", "CHARGE", "DIVINE_SHIELD", "BATTLECRY",
            "DEATHRATTLE", "POISONOUS", "WINDFURY", "LIFESTEAL",
        }
        d = {
            "id": "TEST_003",
            "name": "Everything Card",
            "cost": 10,
            "attack": 5,
            "health": 5,
            "card_type": "MINION",
            "mechanics": all_mechs,
        }
        card = make_card(d)
        expected = {Mechanic(m) for m in all_mechs}
        assert card.mechanics == expected

    @pytest.mark.skipif(not is_available(), reason="hearthstone_data not installed")
    def test_make_card_roundtrip_from_db(self):
        """Cards from the DB can be converted to simulator Card objects."""
        from hs_simulator import Card
        cards = get_cards_for_class("Mage", include_neutral=False)
        for d in cards[:10]:
            card = make_card(d)
            assert isinstance(card, Card)
            assert card.name == d["name"]
            assert card.cost == d["cost"]
