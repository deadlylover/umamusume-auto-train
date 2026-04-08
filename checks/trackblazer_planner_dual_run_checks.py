import json
from contextlib import ExitStack
from unittest.mock import patch

import core.config as config
import utils.constants as constants
from core.actions import Action
from core.skeleton import build_review_snapshot
from core.trackblazer_race_logic import evaluate_trackblazer_race


def _base_state():
  return {
    "year": "Senior Year Early Jul",
    "turn": 12,
    "criteria": "Reach the next fan target",
    "energy_level": 55,
    "max_energy": 100,
    "current_mood": "GOOD",
    "current_stats": {
      "spd": 800,
      "sta": 620,
      "pwr": 700,
      "guts": 310,
      "wit": 540,
      "sp": 240,
    },
    "date_event_available": True,
    "race_mission_available": False,
    "aptitudes": {"track": "A", "distance": "A"},
    "status_effect_names": [],
    "rival_indicator_detected": False,
    "trackblazer_climax": False,
    "trackblazer_climax_locked_race": False,
    "trackblazer_trainings_remaining_upper_bound": 6,
    "trackblazer_inventory": {
      "vita_20": {"detected": True, "held_quantity": 1, "increment_target": (1, 1), "category": "energy"},
      "good_luck_charm": {"detected": True, "held_quantity": 1, "increment_target": (2, 2), "category": "support"},
    },
    "trackblazer_inventory_summary": {
      "held_quantities": {"vita_20": 1, "good_luck_charm": 1},
      "items_detected": ["vita_20", "good_luck_charm"],
      "actionable_items": ["vita_20", "good_luck_charm"],
      "by_category": {"energy": ["vita_20"], "support": ["good_luck_charm"]},
      "total_detected": 2,
    },
    "trackblazer_inventory_controls": {"confirm_use_visible": True},
    "trackblazer_inventory_flow": {
      "opened": True,
      "closed": True,
      "use_training_items_button_visible": True,
      "timing_total": 0.41,
      "timing_open": 0.10,
      "timing_scan": 0.20,
      "timing_close": 0.11,
    },
    "trackblazer_inventory_pre_shop": {
      "vita_20": {"detected": True, "held_quantity": 1, "increment_target": (1, 1), "category": "energy"},
    },
    "trackblazer_inventory_pre_shop_summary": {
      "held_quantities": {"vita_20": 1},
      "items_detected": ["vita_20"],
      "actionable_items": ["vita_20"],
      "by_category": {"energy": ["vita_20"]},
      "total_detected": 1,
    },
    "trackblazer_inventory_pre_shop_flow": {
      "opened": True,
      "closed": True,
      "timing_total": 0.37,
      "timing_open": 0.09,
      "timing_scan": 0.18,
      "timing_close": 0.10,
    },
    "trackblazer_shop_items": ["vita_20", "good_luck_charm"],
    "trackblazer_shop_summary": {
      "shop_coins": 180,
      "items_detected": ["vita_20", "good_luck_charm"],
      "purchasable_items": ["vita_20", "good_luck_charm"],
    },
    "trackblazer_shop_flow": {
      "entered": True,
      "closed": True,
      "timing_total": 0.28,
      "timing_open": 0.08,
      "timing_scan": 0.12,
      "timing_close": 0.08,
    },
    "skill_purchase_check": {
      "should_check": False,
      "current_sp": 240,
      "threshold_sp": 400,
      "auto_buy_skill_enabled": False,
      "reason": "below threshold",
    },
    "skill_purchase_flow": {
      "skipped": True,
      "reason": "below threshold",
      "timing_total": 0.05,
    },
    "skill_purchase_scan": {},
    "skill_purchase_plan": {},
    "state_validation": {"valid": True},
    "trackblazer_shop_priority_preview": [],
  }


def _training_results(selected_name="speed", selected_score=42.0, selected_failure=0):
  return {
    "speed": {
      "name": "speed",
      "score_tuple": (selected_score, 0),
      "weighted_stat_score": selected_score,
      "stat_gains": {"speed": 20, "power": 8, "sp": 10},
      "failure": selected_failure,
      "total_supports": 4,
      "total_rainbow_friends": 1,
      "total_friendship_levels": {"blue": 2, "green": 1, "yellow": 0, "max": 0},
      "failure_bypassed_by_items": False,
    },
    "stamina": {
      "name": "stamina",
      "score_tuple": (28.0, 0),
      "weighted_stat_score": 28.0,
      "stat_gains": {"stamina": 16, "power": 4, "sp": 8},
      "failure": 2,
      "total_supports": 3,
      "total_rainbow_friends": 0,
      "total_friendship_levels": {"blue": 1, "green": 1, "yellow": 0, "max": 0},
      "failure_bypassed_by_items": False,
    },
  }


def _training_action():
  action = Action()
  action.func = "do_training"
  action.available_actions = ["do_training", "do_rest", "do_race"]
  training_results = _training_results()
  action["training_name"] = "speed"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = dict(training_results["speed"])
  action["available_trainings"] = dict(training_results)
  return action, training_results


def _race_action():
  action = Action()
  action.func = "do_race"
  action.available_actions = ["do_training", "do_rest", "do_race"]
  training_results = _training_results(selected_score=14.0, selected_failure=1)
  action["training_name"] = "speed"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = {
    **dict(training_results["speed"]),
    "weighted_stat_score": 14.0,
    "score_tuple": (14.0, 0),
    "stat_gains": {"speed": 9, "power": 4, "sp": 6},
  }
  action["race_name"] = "any"
  action["prefer_rival_race"] = True
  action["available_trainings"] = dict(training_results)
  return action, training_results


def _rest_action():
  action = Action()
  action.func = "do_rest"
  action.available_actions = ["do_training", "do_rest", "do_race"]
  training_results = _training_results(selected_score=8.0, selected_failure=18)
  action["training_name"] = "speed"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = {
    **dict(training_results["speed"]),
    "weighted_stat_score": 8.0,
    "score_tuple": (8.0, 0),
    "stat_gains": {"speed": 6, "power": 2, "sp": 4},
    "failure": 18,
  }
  action["energy_level"] = 5
  action["available_trainings"] = dict(training_results)
  return action, training_results


def _prepare_case(case_name):
  state = _base_state()
  if case_name == "training":
    action, training_results = _training_action()
    state["rival_indicator_detected"] = False
  elif case_name == "race":
    action, training_results = _race_action()
    state["rival_indicator_detected"] = True
  elif case_name == "rest":
    action, training_results = _rest_action()
    state["energy_level"] = 5
    state["rival_indicator_detected"] = False
  else:
    raise ValueError(case_name)
  state["training_results"] = dict(training_results)
  action["trackblazer_race_decision"] = evaluate_trackblazer_race(state, action)
  return state, action


def main():
  config.reload_config(print_config=False)
  constants.SCENARIO_NAME = "trackblazer"
  cases = {}
  traversal_calls = {
    "collect_main_state": 0,
    "collect_training_state": 0,
    "collect_trackblazer_inventory": 0,
    "check_trackblazer_shop_inventory": 0,
    "collect_skill_purchase": 0,
    "check_rival_race_indicator": 0,
  }

  def _forbid(name):
    def _inner(*args, **kwargs):
      traversal_calls[name] += 1
      raise AssertionError(f"unexpected live traversal: {name}")
    return _inner

  with ExitStack() as stack:
    stack.enter_context(patch("core.state.collect_main_state", side_effect=_forbid("collect_main_state")))
    stack.enter_context(patch("core.state.collect_training_state", side_effect=_forbid("collect_training_state")))
    stack.enter_context(patch("core.state.collect_trackblazer_inventory", side_effect=_forbid("collect_trackblazer_inventory")))
    stack.enter_context(patch("scenarios.trackblazer.check_trackblazer_shop_inventory", side_effect=_forbid("check_trackblazer_shop_inventory")))
    stack.enter_context(patch("core.skill_scanner.collect_skill_purchase", side_effect=_forbid("collect_skill_purchase")))
    stack.enter_context(patch("scenarios.trackblazer.check_rival_race_indicator", side_effect=_forbid("check_rival_race_indicator")))

    for case_name in ("training", "race", "rest"):
      state_obj, action = _prepare_case(case_name)
      snapshot = build_review_snapshot(
        state_obj,
        action,
        reasoning_notes=f"synthetic {case_name} case",
        ocr_debug=[],
      )
      comparison = snapshot.get("planner_dual_run_comparison") or {}
      assert comparison, f"{case_name}: missing planner comparison"
      assert comparison.get("match") is True, f"{case_name}: planner/legacy discussion diverged"
      assert "Turn Discussion" in (comparison.get("legacy_turn_discussion") or ""), f"{case_name}: missing legacy discussion text"
      assert "Turn Discussion" in (comparison.get("planner_turn_discussion") or ""), f"{case_name}: missing planner discussion text"
      assert comparison.get("diff_lines") in ([], None), f"{case_name}: expected empty diff lines on match"
      cases[case_name] = {
        "match": comparison.get("match"),
        "legacy_hash": comparison.get("legacy_hash"),
        "planner_hash": comparison.get("planner_hash"),
        "candidate_kinds": [
          entry.get("kind")
          for entry in ((state_obj.get("trackblazer_planner_state") or {}).get("dual_run") or {}).get("candidates", [])
        ],
      }

  assert all(count == 0 for count in traversal_calls.values()), traversal_calls
  print(json.dumps({
    "cases": cases,
    "traversal_calls": traversal_calls,
  }, indent=2, sort_keys=True))


if __name__ == "__main__":
  main()
