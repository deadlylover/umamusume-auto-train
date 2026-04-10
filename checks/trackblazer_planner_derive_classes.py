import copy

import core.config as config
import utils.constants as constants
from core.trackblazer.derive import derive_turn_state
from core.trackblazer.observe import hydrate_observed_turn_state


def _state_for_energy(year_label, energy_level, *, climax=False, rival=False):
  return {
    "year": year_label,
    "turn": 8,
    "energy_level": energy_level,
    "max_energy": 100,
    "current_mood": "GOOD",
    "status_effect_names": [],
    "rival_indicator_detected": rival,
    "race_mission_available": False,
    "trackblazer_climax": climax,
    "trackblazer_climax_locked_race": climax,
    "trackblazer_climax_race_day": False,
    "trackblazer_lobby_scheduled_race": False,
    "trackblazer_inventory_summary": {"held_quantities": {}, "items_detected": [], "actionable_items": [], "by_category": {}, "total_detected": 0},
    "trackblazer_inventory_flow": {"opened": True},
    "trackblazer_shop_items": [],
    "trackblazer_shop_summary": {"shop_coins": 0, "items_detected": [], "purchasable_items": []},
    "trackblazer_shop_flow": {"entered": True},
    "skill_purchase_check": {"should_check": False},
    "skill_purchase_flow": {"skipped": True},
    "skill_purchase_scan": {},
    "skill_purchase_plan": {},
    "training_results": {
      "weak": {
        "name": "weak",
        "score_tuple": (20.0, 0),
        "weighted_stat_score": 20.0,
        "stat_gains": {"weak": 8, "sp": 4},
        "failure": 0,
        "total_supports": 1,
        "total_rainbow_friends": 0,
      },
      "adequate": {
        "name": "adequate",
        "score_tuple": (40.0, 0),
        "weighted_stat_score": 40.0,
        "stat_gains": {"adequate": 14, "sp": 4},
        "failure": 0,
        "total_supports": 2,
        "total_rainbow_friends": 0,
      },
      "strong": {
        "name": "strong",
        "score_tuple": (50.0, 0),
        "weighted_stat_score": 50.0,
        "stat_gains": {"strong": 18, "sp": 5},
        "failure": 0,
        "total_supports": 3,
        "total_rainbow_friends": 1,
      },
      "very_strong": {
        "name": "very_strong",
        "score_tuple": (60.0, 0),
        "weighted_stat_score": 60.0,
        "stat_gains": {"very_strong": 22, "sp": 6},
        "failure": 0,
        "total_supports": 4,
        "total_rainbow_friends": 2,
      },
    },
  }


def _derive(state_obj):
  observed = hydrate_observed_turn_state(copy.deepcopy(state_obj), action=None, planner_state={})
  return derive_turn_state(observed, planner_state={}, state_obj=copy.deepcopy(state_obj), action=None).to_dict()


def main():
  config.reload_config(print_config=False)
  constants.SCENARIO_NAME = "trackblazer"

  assert _derive(_state_for_energy("Senior Year Early Sep", 4)).get("energy_class") == "critical"
  assert _derive(_state_for_energy("Senior Year Early Sep", 20)).get("energy_class") == "low"
  assert _derive(_state_for_energy("Senior Year Early Sep", 50)).get("energy_class") == "ok"
  assert _derive(_state_for_energy("Senior Year Early Sep", 90)).get("energy_class") == "high"

  derived = _derive(_state_for_energy("Senior Year Early Jul", 50))
  value_classes = {
    entry.get("name"): entry.get("value_class")
    for entry in (derived.get("training_value") or [])
  }
  assert value_classes == {
    "weak": "weak",
    "adequate": "adequate",
    "strong": "strong",
    "very_strong": "very_strong",
  }
  assert (derived.get("timeline_window") or {}).get("is_summer") is True
  assert (derived.get("timeline_window") or {}).get("summer_window") is True

  non_summer = _derive(_state_for_energy("Senior Year Early Sep", 50))
  assert (non_summer.get("timeline_window") or {}).get("is_summer") is False

  climax = _derive(_state_for_energy("Senior Year Early Sep", 50, climax=True))
  assert (climax.get("timeline_window") or {}).get("is_climax") is True
  assert (climax.get("timeline_window") or {}).get("tsc_active") is True
  assert (climax.get("race_opportunity") or {}).get("climax_locked") is True

  rival = _derive(_state_for_energy("Senior Year Early Sep", 50, rival=True))
  assert (rival.get("race_opportunity") or {}).get("rival_visible") is True
  assert "next_turn_races_count" in (rival.get("lookahead_summary") or {})
  assert "next_n_turns_races_count" in (rival.get("lookahead_summary") or {})


if __name__ == "__main__":
  main()
