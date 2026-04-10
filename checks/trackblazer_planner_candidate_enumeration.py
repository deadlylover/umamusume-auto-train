import copy
import inspect

import core.config as config
import utils.constants as constants
from core.trackblazer.candidates import enumerate_candidate_actions
from core.trackblazer.derive import derive_turn_state
from core.trackblazer.observe import hydrate_observed_turn_state


def _base_state():
  return {
    "year": "Senior Year Early Sep",
    "turn": 10,
    "energy_level": 60,
    "max_energy": 100,
    "current_mood": "GOOD",
    "status_effect_names": [],
    "rival_indicator_detected": False,
    "race_mission_available": False,
    "trackblazer_climax": False,
    "trackblazer_climax_locked_race": False,
    "trackblazer_climax_race_day": False,
    "trackblazer_lobby_scheduled_race": False,
    "trackblazer_inventory": {"speed_manual": {"held_quantity": 1, "detected": True}},
    "trackblazer_inventory_summary": {
      "held_quantities": {"speed_manual": 1},
      "items_detected": ["speed_manual"],
      "actionable_items": ["speed_manual"],
      "by_category": {"training_boost": ["speed_manual"]},
      "total_detected": 1,
    },
    "trackblazer_inventory_flow": {"opened": True},
    "trackblazer_shop_items": [],
    "trackblazer_shop_summary": {"shop_coins": 0, "items_detected": [], "purchasable_items": []},
    "trackblazer_shop_flow": {"entered": True},
    "skill_purchase_check": {"should_check": False},
    "skill_purchase_flow": {"skipped": True},
    "skill_purchase_scan": {},
    "skill_purchase_plan": {},
    "training_results": {
      "speed": {
        "name": "speed",
        "score_tuple": (52.0, 0),
        "weighted_stat_score": 52.0,
        "stat_gains": {"speed": 20, "power": 7, "sp": 10},
        "failure": 0,
        "total_supports": 4,
        "total_rainbow_friends": 1,
        "failure_bypassed_by_items": False,
      },
      "stamina": {
        "name": "stamina",
        "score_tuple": (24.0, 0),
        "weighted_stat_score": 24.0,
        "stat_gains": {"stamina": 12, "power": 4, "sp": 6},
        "failure": 3,
        "total_supports": 2,
        "total_rainbow_friends": 0,
        "failure_bypassed_by_items": False,
      },
    },
  }


def _run(state_obj):
  observed = hydrate_observed_turn_state(copy.deepcopy(state_obj), action=None, planner_state={})
  derived = derive_turn_state(observed, planner_state={}, state_obj=copy.deepcopy(state_obj), action=None)
  candidates = enumerate_candidate_actions(observed, derived, getattr(config, "TRACKBLAZER_PLANNER_POLICY", {}))
  return observed.to_dict(), derived.to_dict(), [candidate.to_dict() for candidate in candidates]


def _node_ids(candidates):
  return {entry.get("node_id") for entry in candidates}


def main():
  config.reload_config(print_config=False)
  constants.SCENARIO_NAME = "trackblazer"

  observed, derived, candidates = _run(_base_state())
  node_ids = _node_ids(candidates)
  assert "train:speed" in node_ids
  assert "train:speed+items:speed_manual" in node_ids
  assert "rest" in node_ids
  assert not any(node_id and str(node_id).startswith("race:") for node_id in node_ids if node_id != "rest")

  rival_state = _base_state()
  rival_state["rival_indicator_detected"] = True
  _, _, rival_candidates = _run(rival_state)
  assert "race:rival" in _node_ids(rival_candidates)

  low_energy_rival_state = _base_state()
  low_energy_rival_state["rival_indicator_detected"] = True
  low_energy_rival_state["energy_level"] = 1
  _, _, low_energy_rival_candidates = _run(low_energy_rival_state)
  assert "race:rival" not in _node_ids(low_energy_rival_candidates)

  status_state = _base_state()
  status_state["status_effect_names"] = ["Headache"]
  _, _, status_candidates = _run(status_state)
  assert "infirmary" in _node_ids(status_candidates)

  mood_state = _base_state()
  mood_state["current_mood"] = "BAD"
  _, _, mood_candidates = _run(mood_state)
  assert "recreation" in _node_ids(mood_candidates)

  missing_training_state = _base_state()
  missing_training_state["training_results"] = {}
  observed_missing, _, missing_candidates = _run(missing_training_state)
  assert "training" in set(observed_missing.get("missing_inputs") or [])
  assert not any(str(node_id).startswith("train:") for node_id in _node_ids(missing_candidates))

  signature = inspect.signature(enumerate_candidate_actions)
  assert list(signature.parameters.keys()) == ["observed", "derived", "policy"]


if __name__ == "__main__":
  main()
