import copy
from contextlib import ExitStack
from unittest.mock import patch

import core.config as config
import utils.constants as constants
from core.actions import Action
from core.trackblazer.candidates import enumerate_candidate_actions
from core.trackblazer.derive import derive_turn_state
from core.trackblazer.models import TurnPlan
from core.trackblazer.observe import hydrate_observed_turn_state
from core.trackblazer.planner import _apply_shop_deviation_rules, plan_once


def _base_state():
  return {
    "year": "Senior Year Early Jul",
    "turn": 12,
    "criteria": "Reach the next fan target",
    "energy_level": 60,
    "max_energy": 100,
    "current_mood": "GOOD",
    "current_stats": {"spd": 700, "sta": 500, "pwr": 520, "guts": 300, "wit": 460, "sp": 240},
    "date_event_available": True,
    "race_mission_available": False,
    "aptitudes": {"track": "A", "distance": "A"},
    "status_effect_names": [],
    "rival_indicator_detected": False,
    "trackblazer_climax": False,
    "trackblazer_climax_locked_race": False,
    "trackblazer_climax_race_day": False,
    "trackblazer_lobby_scheduled_race": False,
    "trackblazer_inventory": {},
    "trackblazer_inventory_summary": {
      "held_quantities": {},
      "items_detected": [],
      "actionable_items": [],
      "by_category": {},
      "total_detected": 0,
    },
    "trackblazer_inventory_flow": {"opened": True, "closed": True},
    "trackblazer_shop_items": [],
    "trackblazer_shop_summary": {"shop_coins": 0, "items_detected": [], "purchasable_items": []},
    "trackblazer_shop_flow": {"entered": True, "closed": True},
    "skill_purchase_check": {"should_check": False, "reason": "cooldown closed", "threshold_sp": 400},
    "skill_purchase_flow": {"skipped": True},
    "skill_purchase_scan": {},
    "skill_purchase_plan": {},
    "training_results": {
      "speed": {
        "name": "speed",
        "score_tuple": (42.0, 0),
        "weighted_stat_score": 42.0,
        "stat_gains": {"speed": 20, "power": 5, "sp": 9},
        "failure": 2,
        "total_supports": 3,
        "total_rainbow_friends": 1,
        "total_friendship_levels": {"blue": 1, "green": 1, "yellow": 0, "max": 0},
      },
      "stamina": {
        "name": "stamina",
        "score_tuple": (18.0, 0),
        "weighted_stat_score": 18.0,
        "stat_gains": {"stamina": 10, "power": 3, "sp": 6},
        "failure": 1,
        "total_supports": 2,
        "total_rainbow_friends": 0,
        "total_friendship_levels": {"blue": 1, "green": 0, "yellow": 0, "max": 0},
      },
    },
    "state_validation": {"valid": True},
  }


def _base_action(state_obj):
  action = Action()
  action.func = "do_training"
  action.available_actions = ["do_training", "do_rest", "do_race"]
  action["training_name"] = "speed"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = copy.deepcopy((state_obj.get("training_results") or {}).get("speed") or {})
  action["available_trainings"] = copy.deepcopy(state_obj.get("training_results") or {})
  return action


def _stub_planner_race_plan():
  return {
    "planner_owned": True,
    "branch_kind": "non_race",
    "selection_rationale": "",
    "selected_action": {
      "func": "do_rest",
      "trackblazer_race_decision": {},
      "trackblazer_race_lookahead": {},
    },
    "action_payload": {
      "planner_owned": True,
      "branch_kind": "non_race",
      "func": "do_rest",
      "options": {"func": "do_rest"},
      "fallback_action": {"func": "do_rest"},
      "available_actions": ["do_training", "do_rest", "do_race"],
    },
    "race_check": {"planner_owned": True, "branch_kind": "non_race"},
    "race_decision": {},
    "race_entry_gate": {},
    "race_scout": {"planner_owned": True, "required": False, "executed": False},
    "warning_plan": {},
    "fallback_policy": {"planner_owned": True, "chain": []},
  }


def _plan_once_with_stubs(state_obj, action):
  with ExitStack() as stack:
    stack.enter_context(patch("core.trackblazer.planner._build_planner_race_plan", return_value=_stub_planner_race_plan()))
    stack.enter_context(
      patch(
        "core.trackblazer.derive._lookahead_summary",
        return_value={
          "source": {},
          "next_turn_races_count": 0,
          "next_n_turns_races_count": 0,
          "projected_energy_deficit": False,
          "next_g1_distance": None,
          "next_race_day_distance": None,
        },
      )
    )
    return plan_once(state_obj, action, limit=8)


def _derive_and_enumerate(state_obj):
  observed = hydrate_observed_turn_state(copy.deepcopy(state_obj), action=None, planner_state={})
  derived = derive_turn_state(observed, planner_state={}, state_obj=copy.deepcopy(state_obj), action=None)
  candidates = enumerate_candidate_actions(observed, derived, getattr(config, "TRACKBLAZER_PLANNER_POLICY", {}))
  return observed.to_dict(), derived.to_dict(), [candidate.to_dict() for candidate in candidates]


def _test_skill_cadence_candidate_gate():
  closed_state = _base_state()
  _, closed_derived, closed_candidates = _derive_and_enumerate(closed_state)
  assert closed_derived.get("skill_cadence_open") is False, closed_derived
  assert "skill_purchase" not in {entry.get("node_id") for entry in closed_candidates}, closed_candidates

  open_state = _base_state()
  open_state["skill_purchase_check"] = {
    "should_check": True,
    "reason": "Initial skill purchase check is due.",
    "threshold_sp": 400,
    "scheduled_g1_race": False,
  }
  _, open_derived, open_candidates = _derive_and_enumerate(open_state)
  assert open_derived.get("skill_cadence_open") is True, open_derived
  skill_candidate = next(entry for entry in open_candidates if entry.get("node_id") == "skill_purchase")
  assert skill_candidate["source_facts"]["reason"] == "Initial skill purchase check is due.", skill_candidate


def _test_skill_cadence_candidate_is_ranked_but_not_selected_main_action():
  state_obj = _base_state()
  state_obj["skill_purchase_check"] = {
    "should_check": True,
    "reason": "Skill purchase recheck is due.",
    "threshold_sp": 400,
    "scheduled_g1_race": False,
  }
  action = _base_action(state_obj)
  planner_state = _plan_once_with_stubs(state_obj, action)
  turn_plan = TurnPlan.from_snapshot(planner_state.get("turn_plan") or {})
  ranked_ids = [entry.get("node_id") for entry in list(turn_plan.candidate_ranking or [])]
  assert "skill_purchase" in ranked_ids, ranked_ids
  assert (turn_plan.selected_candidate or {}).get("node_id") != "skill_purchase", turn_plan.selected_candidate


def _test_shop_deviation_rules_record_rationale():
  effective_shop_items = [
    {"key": "speed_manual", "display_name": "Speed Manual", "cost": 20, "effective_priority": "HIGH", "max_quantity": 4, "policy_notes": "manual"},
    {"key": "vita_40", "display_name": "Vita 40", "cost": 55, "effective_priority": "HIGH", "max_quantity": 5, "policy_notes": "energy"},
    {"key": "vita_65", "display_name": "Vita 65", "cost": 75, "effective_priority": "HIGH", "max_quantity": 6, "policy_notes": "energy"},
  ]
  shop_summary = {"shop_coins": 80, "year": "Senior Year Early Jul", "turn": 12, "items_detected": ["speed_manual", "vita_40", "vita_65"]}

  adjusted, deviations = _apply_shop_deviation_rules(
    [{"key": "speed_manual", "name": "Speed Manual", "cost": 20}],
    selected_candidate={"node_id": "train:speed+items:vita_40"},
    derived_data={
      "lookahead_summary": {"projected_energy_deficit": True},
      "timeline_window": {"summer_distance": 1},
    },
    effective_shop_items=effective_shop_items,
    shop_items=["speed_manual", "vita_40", "vita_65"],
    shop_summary=shop_summary,
    held_quantities={},
  )

  assert adjusted[0]["key"] in {"vita_40", "vita_65"}, adjusted
  triggers = {entry.get("trigger") for entry in deviations}
  assert "item_assist_requirement" in triggers, deviations
  assert "energy_deficit" in triggers or "summer_reservation" in triggers, deviations
  assert all(entry.get("reason") for entry in deviations), deviations


def main():
  config.reload_config(print_config=False)
  constants.SCENARIO_NAME = "trackblazer"
  _test_skill_cadence_candidate_gate()
  _test_skill_cadence_candidate_is_ranked_but_not_selected_main_action()
  _test_shop_deviation_rules_record_rationale()
  print("trackblazer planner milestone 9 checks: ok")


if __name__ == "__main__":
  main()
