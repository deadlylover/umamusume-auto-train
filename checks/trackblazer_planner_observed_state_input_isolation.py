import copy

import core.config as config
import utils.constants as constants
from core.actions import Action
from core.trackblazer.candidates import enumerate_candidate_actions
from core.trackblazer.derive import derive_turn_state
from core.trackblazer.observe import hydrate_observed_turn_state


def _base_state():
  return {
    "year": "Senior Year Early Jul",
    "turn": 12,
    "energy_level": 72,
    "max_energy": 100,
    "current_mood": "GOOD",
    "status_effect_names": [],
    "rival_indicator_detected": False,
    "race_mission_available": False,
    "trackblazer_climax": False,
    "trackblazer_climax_locked_race": False,
    "trackblazer_climax_race_day": False,
    "trackblazer_lobby_scheduled_race": False,
    "trackblazer_inventory": {
      "speed_manual": {"held_quantity": 1, "detected": True},
    },
    "trackblazer_inventory_summary": {
      "held_quantities": {"speed_manual": 1},
      "items_detected": ["speed_manual"],
      "actionable_items": ["speed_manual"],
      "by_category": {"training_boost": ["speed_manual"]},
      "total_detected": 1,
    },
    "trackblazer_inventory_flow": {"opened": True, "closed": True},
    "trackblazer_shop_items": [],
    "trackblazer_shop_summary": {"shop_coins": 0, "items_detected": [], "purchasable_items": []},
    "trackblazer_shop_flow": {"entered": True, "closed": True},
    "skill_purchase_check": {"should_check": False},
    "skill_purchase_flow": {"skipped": True},
    "skill_purchase_scan": {},
    "skill_purchase_plan": {},
    "training_results": {
      "speed": {
        "name": "speed",
        "score_tuple": (54.0, 0),
        "weighted_stat_score": 54.0,
        "stat_gains": {"speed": 24, "power": 8, "sp": 12},
        "failure": 0,
        "total_supports": 4,
        "total_rainbow_friends": 1,
        "failure_bypassed_by_items": False,
      },
      "stamina": {
        "name": "stamina",
        "score_tuple": (18.0, 0),
        "weighted_stat_score": 18.0,
        "stat_gains": {"stamina": 10, "power": 3, "sp": 6},
        "failure": 3,
        "total_supports": 2,
        "total_rainbow_friends": 0,
        "failure_bypassed_by_items": False,
      },
    },
  }


def _legacy_action():
  action = Action()
  action.func = "do_rest"
  action.available_actions = ["do_rest", "do_training", "do_race"]
  action["training_name"] = "wit"
  action["scheduled_race"] = True
  action["trackblazer_lobby_scheduled_race"] = True
  action["available_trainings"] = {
    "wit": {"name": "wit", "score_tuple": (999.0, 0)},
  }
  action["training_data"] = {
    "name": "wit",
    "score_tuple": (999.0, 0),
    "weighted_stat_score": 999.0,
    "stat_gains": {"wit": 99, "sp": 30},
    "failure": 0,
    "total_supports": 5,
    "total_rainbow_friends": 2,
  }
  return action


def _planner_node_ids(candidates):
  return sorted(
    candidate.node_id
    for candidate in candidates
    if not str(candidate.node_id).startswith("compat:")
  )


def main():
  config.reload_config(print_config=False)
  constants.SCENARIO_NAME = "trackblazer"
  policy = getattr(config, "TRACKBLAZER_PLANNER_POLICY", {})

  state_obj = _base_state()
  action = _legacy_action()
  observed = hydrate_observed_turn_state(state_obj, action=action, planner_state={})
  observed_data = observed.to_dict()

  assert observed_data.get("available_trainings") == state_obj["training_results"]
  assert observed_data.get("trackblazer_lobby_scheduled_race") is False
  assert observed_data.get("missing_inputs") == []
  assert (observed_data.get("legacy_seed_metadata") or {}).get("func") == "do_rest"
  assert (observed_data.get("legacy_seed_metadata") or {}).get("available_trainings") is None

  derived = derive_turn_state(observed, planner_state={}, state_obj=state_obj, action=action)
  derived_data = derived.to_dict()
  assert (derived_data.get("training_value_summary") or {}).get("best_training_name") == "speed"
  assert derived_data.get("energy_class") == "high"

  candidates = enumerate_candidate_actions(observed, derived, policy)
  planner_nodes = _planner_node_ids(candidates)
  assert "train:speed" in planner_nodes
  assert "train:speed+items:speed_manual" in planner_nodes
  assert "rest" in planner_nodes

  mutated_observed_data = copy.deepcopy(observed_data)
  mutated_observed_data["legacy_seed_metadata"] = {
    **dict(mutated_observed_data.get("legacy_seed_metadata") or {}),
    "func": "do_race",
    "training_name": "guts",
    "race_name": "synthetic_race",
    "scheduled_race": False,
  }
  mutated_observed = type(observed)(data=mutated_observed_data)
  mutated_derived = derive_turn_state(mutated_observed, planner_state={}, state_obj=state_obj, action=action)
  mutated_candidates = enumerate_candidate_actions(mutated_observed, mutated_derived, policy)
  assert mutated_derived.to_dict().get("training_value_summary") == derived_data.get("training_value_summary")
  assert _planner_node_ids(mutated_candidates) == planner_nodes


if __name__ == "__main__":
  main()
