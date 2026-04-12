import json
import copy
from contextlib import ExitStack
from unittest.mock import patch

import core.config as config
import core.trackblazer.planner as planner_module
import core.trackblazer_item_use as item_use_module
import utils.constants as constants
from core.actions import Action
from core.skeleton import build_review_snapshot
from core.trackblazer.models import TurnPlan
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


def _rest_with_failure_blocked_training_action(blocked_score=36.0):
  action = Action()
  action.func = "do_rest"
  action.available_actions = ["do_training", "do_rest", "do_race"]
  training_results = {
    "speed": {
      "name": "speed",
      "weighted_stat_score": blocked_score - 10.0,
      "bond_boost": 10.0,
      "score_tuple": (blocked_score, 0),
      "stat_gains": {"speed": 9, "power": 7, "sp": 3},
      "failure": 25,
      "total_supports": 2,
      "total_rainbow_friends": 0,
      "total_friendship_levels": {"blue": 1, "green": 0, "yellow": 0, "max": 0},
      "failure_bypassed_by_items": False,
    },
    "wit": {
      "name": "wit",
      "weighted_stat_score": 24.0,
      "bond_boost": 0.0,
      "score_tuple": (24.0, 0),
      "stat_gains": {"wit": 7, "speed": 2, "sp": 3},
      "failure": 0,
      "total_supports": 1,
      "total_rainbow_friends": 0,
      "total_friendship_levels": {"blue": 0, "green": 1, "yellow": 0, "max": 0},
      "failure_bypassed_by_items": False,
    },
  }
  action["training_name"] = "wit"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = dict(training_results["wit"])
  action["available_trainings"] = {"wit": dict(training_results["wit"])}
  action["trackblazer_race_lookahead"] = {
    "enabled": True,
    "conserve": False,
    "can_train_and_race": True,
    "reason": "test",
  }
  return action, training_results


def _scheduled_race_action():
  action = Action()
  action.func = "do_race"
  action.available_actions = ["do_training", "do_rest", "do_race"]
  training_results = _training_results(selected_score=18.0, selected_failure=0)
  action["training_name"] = "speed"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = dict(training_results["speed"])
  action["race_name"] = "tokyo_yushun"
  action["scheduled_race"] = True
  action["available_trainings"] = dict(training_results)
  return action, training_results


def _forced_race_day_action():
  action = Action()
  action.func = "do_race"
  action.available_actions = ["do_training", "do_rest", "do_race"]
  training_results = _training_results(selected_score=12.0, selected_failure=0)
  action["training_name"] = "speed"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = dict(training_results["speed"])
  action["race_name"] = "any"
  action["is_race_day"] = True
  action["year"] = "Senior Year"
  action["available_trainings"] = dict(training_results)
  return action, training_results


def _fallback_non_rival_race_action():
  action = Action()
  action.func = "do_race"
  action.available_actions = ["do_race", "do_training", "do_rest"]
  training_results = _training_results(selected_score=16.0, selected_failure=4)
  action["training_name"] = "speed"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = dict(training_results["speed"])
  action["race_name"] = "any"
  action["fallback_non_rival_race"] = True
  action["available_trainings"] = dict(training_results)
  action["trackblazer_race_decision"] = {
    "should_race": True,
    "reason": "Weak board with rival gate fallback allowed a non-rival race attempt",
    "training_total_stats": 16,
    "training_score": 16.0,
    "fallback_non_rival_race": True,
    "prefer_rival_race": False,
    "race_tier_target": "g3",
    "race_name": "any",
    "race_available": True,
    "rival_indicator": False,
  }
  return action, training_results


def _goal_race_action():
  action = Action()
  action.func = "do_race"
  action.available_actions = ["do_race", "do_training", "do_rest"]
  training_results = _training_results(selected_score=21.0, selected_failure=1)
  action["training_name"] = "speed"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = dict(training_results["speed"])
  action["race_name"] = "satsuki_sho"
  action["available_trainings"] = dict(training_results)
  return action, training_results


def _weak_training_rest_action():
  action = Action()
  action.func = "do_rest"
  action.available_actions = ["do_rest", "do_training", "do_race"]
  training_results = _training_results(selected_score=7.0, selected_failure=12)
  action["training_name"] = "speed"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = {
    **dict(training_results["speed"]),
    "weighted_stat_score": 7.0,
    "score_tuple": (7.0, 0),
    "stat_gains": {"speed": 5, "power": 2, "sp": 4},
    "failure": 12,
  }
  action["energy_level"] = 14
  action["available_trainings"] = dict(training_results)
  action["trackblazer_race_decision"] = {
    "should_race": False,
    "reason": "Weak low-value board with upcoming cadence pressure; rest preferred over training",
    "training_total_stats": 7,
    "training_score": 7.0,
    "prefer_rest_over_weak_training": True,
    "race_available": False,
    "rival_indicator": False,
  }
  return action, training_results


def _stat_focused_training_override_action():
  action = Action()
  action.func = "do_training"
  action.available_actions = ["do_training", "do_rest", "do_race"]
  training_results = _training_results(selected_score=41.0, selected_failure=0)
  action["training_name"] = "speed"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = dict(training_results["speed"])
  action["_trackblazer_rest_promoted_to_training"] = True
  action["available_trainings"] = dict(training_results)
  return action, training_results


def _forced_climax_race_action():
  action = Action()
  action.func = "do_race"
  action.available_actions = ["do_race"]
  training_results = _training_results(selected_score=10.0, selected_failure=0)
  action["training_name"] = "speed"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = dict(training_results["speed"])
  action["race_name"] = "any"
  action["trackblazer_climax_race_day"] = True
  action["available_trainings"] = dict(training_results)
  return action, training_results


def _mission_race_action():
  action = Action()
  action.func = "do_race"
  action.available_actions = ["do_race", "do_training", "do_rest"]
  training_results = _training_results(selected_score=15.0, selected_failure=2)
  action["training_name"] = "speed"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = dict(training_results["speed"])
  action["race_name"] = "any"
  action["race_mission_available"] = True
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
  elif case_name == "scheduled_race":
    action, training_results = _scheduled_race_action()
    state["rival_indicator_detected"] = False
  elif case_name == "forced_race_day":
    action, training_results = _forced_race_day_action()
    state["turn"] = "Race Day"
    state["rival_indicator_detected"] = False
  elif case_name == "fallback_non_rival_race":
    action, training_results = _fallback_non_rival_race_action()
    state["rival_indicator_detected"] = False
  elif case_name == "goal_race":
    action, training_results = _goal_race_action()
    state["rival_indicator_detected"] = False
  elif case_name == "weak_training_rest":
    action, training_results = _weak_training_rest_action()
    state["energy_level"] = 14
    state["rival_indicator_detected"] = False
  elif case_name == "stat_focused_training_override":
    action, training_results = _stat_focused_training_override_action()
    state["rival_indicator_detected"] = False
  elif case_name == "forced_climax_race":
    action, training_results = _forced_climax_race_action()
    state["trackblazer_climax"] = True
    state["trackblazer_climax_locked_race"] = True
    state["trackblazer_climax_race_day"] = True
    state["rival_indicator_detected"] = False
  elif case_name == "mission_race":
    action, training_results = _mission_race_action()
    state["race_mission_available"] = True
    state["rival_indicator_detected"] = False
  else:
    raise ValueError(case_name)
  state["training_results"] = dict(training_results)
  if not action.get("trackblazer_race_decision"):
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

    expected_candidate_kinds = {
      "training": {"training", "race_gate"},
      "race": {"race", "race_gate"},
      "rest": {"rest", "race_gate"},
      "scheduled_race": {"race", "scheduled_race", "race_gate"},
      "forced_race_day": {"race", "forced_race_day", "race_gate"},
      "fallback_non_rival_race": {"race", "fallback_non_rival_race", "race_gate"},
      "goal_race": {"race", "goal_race", "race_gate"},
      "weak_training_rest": {"rest", "weak_training_rest", "race_gate"},
      "stat_focused_training_override": {"training", "stat_focused_training_override", "race_gate"},
      "forced_climax_race": {"race", "forced_climax_race", "race_gate"},
      "mission_race": {"race", "mission_race", "race_gate"},
    }

    for case_name in (
      "training",
      "race",
      "rest",
      "scheduled_race",
      "forced_race_day",
      "fallback_non_rival_race",
      "goal_race",
      "weak_training_rest",
      "stat_focused_training_override",
      "forced_climax_race",
      "mission_race",
    ):
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
      candidate_kinds = [
        entry.get("kind")
        for entry in ((state_obj.get("trackblazer_planner_state") or {}).get("dual_run") or {}).get("candidates", [])
      ]
      missing_kinds = sorted(expected_candidate_kinds[case_name] - set(candidate_kinds))
      assert not missing_kinds, f"{case_name}: missing candidate kinds {missing_kinds}"
      turn_plan = (state_obj.get("trackblazer_planner_state") or {}).get("turn_plan") or {}
      review_context = dict(turn_plan.get("review_context") or {})
      step_types = [entry.get("step_type") for entry in list(turn_plan.get("step_sequence") or []) if isinstance(entry, dict)]
      assert "await_operator_review" in step_types, f"{case_name}: missing await_operator_review step"
      assert "execute_main_action" in step_types, f"{case_name}: missing execute_main_action step"
      assert "resolve_post_action" in step_types, f"{case_name}: missing resolve_post_action step"
      assert review_context.get("selected_action"), f"{case_name}: missing planner-owned selected_action review context"
      assert review_context.get("ranked_trainings"), f"{case_name}: missing planner-owned ranked trainings"
      assert review_context.get("ranked_trainings") == snapshot.get("ranked_trainings"), f"{case_name}: planner-owned ranked trainings should match review snapshot"
      assert TurnPlan.from_snapshot(turn_plan).to_planned_clicks() == snapshot.get("planned_clicks"), f"{case_name}: planner-owned planned clicks should come from TurnPlan step payloads"

      planner_only_turn_plan = copy.deepcopy(turn_plan)
      planner_only_turn_plan.pop("legacy_shared_plan", None)
      planner_only_planned_actions = TurnPlan.from_snapshot(planner_only_turn_plan).to_planned_actions()
      original_planned_actions = snapshot.get("planned_actions") or {}
      assert planner_only_planned_actions.get("inventory_scan") == original_planned_actions.get("inventory_scan"), f"{case_name}: inventory scan should be planner-native"
      assert planner_only_planned_actions.get("would_use") == original_planned_actions.get("would_use"), f"{case_name}: would_use should be planner-native"
      assert planner_only_planned_actions.get("deferred_use") == original_planned_actions.get("deferred_use"), f"{case_name}: deferred_use should be planner-native"
      assert planner_only_planned_actions.get("shop_scan") == original_planned_actions.get("shop_scan"), f"{case_name}: shop scan should be planner-native"
      assert planner_only_planned_actions.get("would_buy") == original_planned_actions.get("would_buy"), f"{case_name}: would_buy should be planner-native"

      cases[case_name] = {
        "match": comparison.get("match"),
        "legacy_hash": comparison.get("legacy_hash"),
        "planner_hash": comparison.get("planner_hash"),
        "candidate_kinds": candidate_kinds,
        "step_types": step_types,
      }

    planner_fallback_state, planner_fallback_action = _prepare_case("training")
    initial_snapshot = build_review_snapshot(
      planner_fallback_state,
      planner_fallback_action,
      reasoning_notes="synthetic planner fallback case",
      ocr_debug=[],
    )
    planner_state = planner_fallback_state.get("trackblazer_planner_state") or {}
    fallback_entry = {
      "key": "vita_20",
      "name": "Vita 20",
      "usage_group": "energy",
      "reason": "synthetic planner fallback check",
    }
    planner_state["pre_action_items"] = [fallback_entry]
    planner_state["reassess_after_item_use"] = True
    planner_state["item_use_context"] = {"training_name": "speed"}
    turn_plan = planner_state.get("turn_plan") or {}
    turn_plan["item_plan"] = {
      **dict(turn_plan.get("item_plan") or {}),
      "pre_action_items": [fallback_entry],
      "deferred_use": [],
      "reassess_after_item_use": True,
      "context": {"training_name": "speed"},
    }
    planner_state["turn_plan"] = turn_plan
    planner_fallback_state["trackblazer_planner_state"] = planner_state
    planner_fallback_action.options.pop("trackblazer_pre_action_items", None)
    planner_fallback_action.options.pop("trackblazer_reassess_after_item_use", None)
    fallback_snapshot = build_review_snapshot(
      planner_fallback_state,
      planner_fallback_action,
      reasoning_notes="synthetic planner fallback case",
      ocr_debug=[],
    )
    selected_action = fallback_snapshot.get("selected_action") or {}
    assert selected_action.get("pre_action_item_use") == [fallback_entry], "selected_action should fall back to planner pre-action items"
    assert selected_action.get("reassess_after_item_use") is True, "selected_action should fall back to planner reassess flag"
    assert initial_snapshot.get("planner_dual_run_comparison", {}).get("match") is True, "planner fallback setup should still produce a valid initial comparison"

    review_context_state, review_context_action = _prepare_case("training")
    review_context_snapshot = build_review_snapshot(
      review_context_state,
      review_context_action,
      reasoning_notes="synthetic planner review context case",
      ocr_debug=[],
    )
    review_context_planner_state = review_context_state.get("trackblazer_planner_state") or {}
    review_context_turn_plan = review_context_planner_state.get("turn_plan") or {}
    review_context_payload = dict(review_context_turn_plan.get("review_context") or {})
    review_context_payload["selected_action"] = {
      **dict(review_context_payload.get("selected_action") or {}),
      "training_name": "stamina",
      "score_tuple": (99.0, 0),
      "stat_gains": {"stamina": 33, "power": 7, "sp": 11},
      "failure": 1,
      "total_supports": 5,
      "total_rainbow_friends": 2,
      "trackblazer_race_decision": {
        "should_race": False,
        "reason": "synthetic planner-owned selected_action view",
        "training_total_stats": 40,
        "training_score": 99.0,
        "race_available": False,
      },
    }
    review_context_payload["ranked_trainings"] = [
      {
        "name": "stamina",
        "score_tuple": (99.0, 0),
        "failure": 1,
        "total_supports": 5,
        "total_rainbow_friends": 2,
        "stat_gains": {"stamina": 33, "power": 7, "sp": 11},
        "filtered_out": False,
        "excluded_reason": None,
      }
    ]
    review_context_turn_plan["review_context"] = review_context_payload
    review_context_planner_state["turn_plan"] = review_context_turn_plan
    review_context_state["trackblazer_planner_state"] = review_context_planner_state
    review_context_followup = build_review_snapshot(
      review_context_state,
      review_context_action,
      reasoning_notes="synthetic planner review context case",
      ocr_debug=[],
    )
    followup_selected_action = review_context_followup.get("selected_action") or {}
    assert followup_selected_action.get("training_name") == "stamina", "review snapshot should prefer planner-owned selected_action view"
    assert followup_selected_action.get("score_tuple") == (99.0, 0), "review snapshot should preserve planner-owned selected_action score tuple"
    assert followup_selected_action.get("trackblazer_race_decision", {}).get("reason") == "synthetic planner-owned selected_action view", "review snapshot should prefer planner-owned race decision summary"
    followup_ranked_trainings = review_context_followup.get("ranked_trainings") or []
    assert len(followup_ranked_trainings) == 1 and followup_ranked_trainings[0].get("name") == "stamina", "review snapshot should prefer planner-owned ranked trainings"
    followup_turn_plan_snapshot = (review_context_state.get("trackblazer_planner_state") or {}).get("turn_plan") or {}
    followup_review_context_payload = followup_turn_plan_snapshot.get("review_context") or {}
    assert followup_review_context_payload.get("reasoning_notes") == "synthetic planner review context case", "stored TurnPlan review context should carry reasoning notes for console rendering"
    assert followup_review_context_payload.get("planned_clicks") == review_context_followup.get("planned_clicks"), "stored TurnPlan review context should carry planned clicks for console rendering"
    assert review_context_followup.get("turn_discussion_text") == review_context_followup.get("planner_dual_run_comparison", {}).get("planner_turn_discussion"), "review snapshot should expose planner-owned turn discussion text"
    mutated_turn_plan = TurnPlan.from_snapshot((review_context_state.get("trackblazer_planner_state") or {}).get("turn_plan") or {})
    compact_summary_text = mutated_turn_plan.to_compact_summary({
      "scenario_name": review_context_followup.get("scenario_name"),
      "turn_label": review_context_followup.get("turn_label"),
      "execution_intent": review_context_followup.get("execution_intent"),
      "state_summary": review_context_followup.get("state_summary"),
      "selected_action": {},
      "ranked_trainings": [],
      "reasoning_notes": review_context_followup.get("reasoning_notes"),
      "planned_clicks": review_context_followup.get("planned_clicks"),
    }, include_prompt=False)
    assert compact_summary_text == review_context_followup.get("compact_summary_text"), "review snapshot should expose planner-owned compact summary text"
    quick_bar_payload = mutated_turn_plan.to_quick_bar({
      "planned_clicks": review_context_followup.get("planned_clicks"),
    })
    assert quick_bar_payload == review_context_followup.get("quick_bar"), "review snapshot should expose planner-owned quick-bar payload"
    planner_text = mutated_turn_plan.to_turn_discussion({
      "scenario_name": review_context_followup.get("scenario_name"),
      "turn_label": review_context_followup.get("turn_label"),
      "execution_intent": review_context_followup.get("execution_intent"),
      "state_summary": review_context_followup.get("state_summary"),
      "selected_action": {},
      "ranked_trainings": [],
      "reasoning_notes": review_context_followup.get("reasoning_notes"),
      "planned_clicks": review_context_followup.get("planned_clicks"),
    })
    assert "Action: train stamina" in planner_text, "TurnPlan discussion should render from planner-owned selected_action context"
    assert "Race Gate Reason: synthetic planner-owned selected_action view" in planner_text, "TurnPlan discussion should render planner-owned race summary"

    stale_item_state, stale_item_action = _prepare_case("training")
    stale_item_action["trackblazer_pre_action_items"] = [
      {
        "key": "grilled_carrots",
        "name": "Grilled Carrots",
        "usage_group": "energy",
        "reason": "synthetic stale action payload",
      }
    ]
    stale_item_action["trackblazer_reassess_after_item_use"] = True
    with patch(
      "core.trackblazer.planner.plan_item_usage",
      return_value={
        "context": {
          "training_name": "speed",
          "training_score": 42.0,
          "energy_rescue": False,
          "commit_training_after_items": True,
        },
        "candidates": [
          {
            "key": "motivating_megaphone",
            "name": "Motivating Megaphone",
            "usage_group": "training_burst",
            "reason": "synthetic planner-owned burst item",
          }
        ],
        "deferred": [],
      },
    ):
      stale_item_snapshot = build_review_snapshot(
        stale_item_state,
        stale_item_action,
        reasoning_notes="synthetic stale action payload case",
        ocr_debug=[],
      )
    stale_selected_action = stale_item_snapshot.get("selected_action") or {}
    assert [entry.get("key") for entry in stale_selected_action.get("pre_action_item_use") or []] == ["motivating_megaphone"], "review snapshot should ignore stale live action items when planner mode owns the turn"
    stale_turn_discussion = stale_item_snapshot.get("turn_discussion_text") or ""
    assert "Action: use Motivating Megaphone -> " in stale_turn_discussion, "turn discussion should render planner-owned pre-action items"
    assert "Action: use Grilled Carrots" not in stale_turn_discussion, "turn discussion should not leak stale live action items into planner-owned review text"

    freshness_state, freshness_action = _prepare_case("training")
    freshness_initial = build_review_snapshot(
      freshness_state,
      freshness_action,
      reasoning_notes="synthetic planner freshness case",
      ocr_debug=[],
    )
    initial_turn_plan = (freshness_state.get("trackblazer_planner_state") or {}).get("turn_plan") or {}
    initial_freshness = dict(initial_turn_plan.get("freshness") or {})
    freshness_action["available_trainings"] = copy.deepcopy(freshness_action.get("available_trainings") or {})
    freshness_action["available_trainings"]["stamina"] = {
      **dict(freshness_action["available_trainings"].get("stamina") or {}),
      "score_tuple": (88.0, 0),
      "stat_gains": {"stamina": 30, "power": 6, "sp": 9},
      "failure": 0,
    }
    freshness_followup = build_review_snapshot(
      freshness_state,
      freshness_action,
      reasoning_notes="synthetic planner freshness case",
      ocr_debug=[],
    )
    refreshed_turn_plan = (freshness_state.get("trackblazer_planner_state") or {}).get("turn_plan") or {}
    refreshed_freshness = dict(refreshed_turn_plan.get("freshness") or {})
    assert initial_freshness.get("action_key") != refreshed_freshness.get("action_key"), "planner freshness should invalidate when available trainings change"
    refreshed_ranked_trainings = refreshed_turn_plan.get("review_context", {}).get("ranked_trainings") or []
    stamina_entry = next((entry for entry in refreshed_ranked_trainings if entry.get("name") == "stamina"), {})
    assert stamina_entry.get("score_tuple") == (88.0, 0), "planner-owned ranked trainings should refresh from updated available trainings"
    assert freshness_followup.get("ranked_trainings") == refreshed_ranked_trainings, "review snapshot should reuse refreshed planner-owned ranked trainings"
    assert freshness_initial.get("planner_dual_run_comparison", {}).get("match") is True, "initial freshness case should produce a valid planner comparison"
    assert freshness_followup.get("planner_dual_run_comparison", {}).get("match") is True, "refreshed freshness case should preserve planner comparison parity"

    compute_once_state, compute_once_action = _prepare_case("training")
    shop_compute_calls = 0
    item_compute_calls = 0
    original_shop_builder = planner_module._candidate_shop_buys
    original_item_planner = planner_module.plan_item_usage

    def _count_shop_builds(*args, **kwargs):
      nonlocal shop_compute_calls
      shop_compute_calls += 1
      return original_shop_builder(*args, **kwargs)

    def _count_item_builds(*args, **kwargs):
      nonlocal item_compute_calls
      item_compute_calls += 1
      return original_item_planner(*args, **kwargs)

    with patch("core.trackblazer.planner._candidate_shop_buys", side_effect=_count_shop_builds), patch(
      "core.trackblazer.planner.plan_item_usage",
      side_effect=_count_item_builds,
    ):
      planner_module.plan_once(compute_once_state, compute_once_action, limit=8)
      planner_module.plan_once(compute_once_state, compute_once_action, limit=8)

    assert shop_compute_calls == 1, "plan_once should compute shop candidates once per unchanged planner snapshot"
    assert item_compute_calls == 1, "plan_once should compute item plan once per unchanged planner snapshot"

    for field_name in (
      "rival_indicator_detected",
      "race_mission_available",
      "trackblazer_lobby_scheduled_race",
      "trackblazer_climax_race_day",
    ):
      race_freshness_state, race_freshness_action = _prepare_case("training")
      baseline_snapshot = build_review_snapshot(
        race_freshness_state,
        race_freshness_action,
        reasoning_notes=f"synthetic race freshness baseline: {field_name}",
        ocr_debug=[],
      )
      baseline_turn_plan = (race_freshness_state.get("trackblazer_planner_state") or {}).get("turn_plan") or {}
      baseline_freshness = dict(baseline_turn_plan.get("freshness") or {})

      race_freshness_state[field_name] = not bool(race_freshness_state.get(field_name))
      followup_snapshot = build_review_snapshot(
        race_freshness_state,
        race_freshness_action,
        reasoning_notes=f"synthetic race freshness followup: {field_name}",
        ocr_debug=[],
      )
      followup_turn_plan = (race_freshness_state.get("trackblazer_planner_state") or {}).get("turn_plan") or {}
      followup_freshness = dict(followup_turn_plan.get("freshness") or {})
      assert baseline_freshness.get("state_key") != followup_freshness.get("state_key"), (
        f"planner freshness should invalidate when race-state field changes: {field_name}"
      )
      assert baseline_snapshot.get("planner_dual_run_comparison", {}).get("match") is True, (
        f"race freshness baseline should keep planner comparison parity: {field_name}"
      )
      assert followup_snapshot.get("planner_dual_run_comparison", {}).get("match") is True, (
        f"race freshness followup should keep planner comparison parity: {field_name}"
      )

    cached_only_state, cached_only_action = _prepare_case("training")
    cached_only_state.pop("rival_indicator_detected", None)
    with patch(
      "core.trackblazer.planner.evaluate_trackblazer_race",
      side_effect=AssertionError("read-only planner should not re-run race gate without cached rival indicator"),
    ):
      cached_only_snapshot = build_review_snapshot(
        cached_only_state,
        cached_only_action,
        reasoning_notes="synthetic cached-only race gate case",
        ocr_debug=[],
      )
    cached_only_turn_plan = TurnPlan.from_snapshot(
      (cached_only_state.get("trackblazer_planner_state") or {}).get("turn_plan") or {}
    )
    cached_only_race_plan = dict(cached_only_turn_plan.race_plan or {})
    cached_only_decision = dict(cached_only_race_plan.get("race_decision") or {})
    cached_only_race_check = dict(cached_only_race_plan.get("race_check") or {})
    assert cached_only_decision.get("cached_only") is True, "read-only race gate should emit cached_only decision metadata when rival cache is missing"
    assert "cached rival-indicator context is missing" in (cached_only_decision.get("reason") or ""), "cached_only race decision should expose explicit missing-cache reason"
    assert cached_only_race_check.get("cached_only") is True, "read-only race check should flag cached-only mode when rival cache is missing"
    assert cached_only_snapshot.get("planner_dual_run_comparison", {}).get("match") is True, "cached-only race guard should preserve planner comparison parity"

    whistle_state, whistle_action = _prepare_case("training")
    whistle_state["trackblazer_shop_summary"] = {
      "shop_coins": 180,
      "items_detected": ["vita_20"],
      "purchasable_items": ["vita_20"],
    }
    whistle_state["trackblazer_shop_items"] = ["vita_20"]
    with patch(
      "core.trackblazer.planner._candidate_shop_buys",
      return_value=[
        {
          "key": "vita_20",
          "name": "Vita 20",
          "priority": "HIGH",
          "cost": 30,
          "held_quantity": 1,
          "max_quantity": 4,
          "reason": "synthetic shop refresh before whistle",
        }
      ],
    ), patch(
      "core.trackblazer.planner.plan_item_usage",
      return_value={
        "context": {
          "training_name": "speed",
          "training_score": 42.0,
          "energy_rescue": False,
          "commit_training_after_items": False,
        },
        "candidates": [
          {
            "key": "reset_whistle",
            "name": "Reset Whistle",
            "usage_group": "burst_setup",
            "reason": "synthetic weak board reroll",
          },
          {
            "key": "motivating_megaphone",
            "name": "Motivating Megaphone",
            "usage_group": "training_burst",
            "reason": "synthetic burst after reroll",
          },
        ],
        "deferred": [],
      },
    ):
      whistle_snapshot = build_review_snapshot(
        whistle_state,
        whistle_action,
        reasoning_notes="synthetic whistle reassess case",
        ocr_debug=[],
      )
    whistle_turn_plan = TurnPlan.from_snapshot((whistle_state.get("trackblazer_planner_state") or {}).get("turn_plan") or {})
    whistle_item_plan = dict(whistle_turn_plan.item_plan or {})
    whistle_execution_payload = dict((whistle_snapshot.get("planner_execution_payload") or {}).get("item_execution") or {})
    whistle_subgraph = dict(whistle_item_plan.get("subgraph") or {})
    whistle_reobserve_boundaries = list(whistle_turn_plan.reobserve_boundaries or [])
    whistle_selected_action = whistle_snapshot.get("selected_action") or {}
    whistle_planned_actions = whistle_snapshot.get("planned_actions") or {}
    assert [entry.get("key") for entry in whistle_selected_action.get("pre_action_item_use") or []] == ["reset_whistle"], "whistle case should expose planner-owned first-pass item execution"
    assert whistle_selected_action.get("reassess_after_item_use") is True, "whistle case should expose planner-owned reassess flag"
    assert whistle_execution_payload.get("inventory_refresh", {}).get("trigger") == "post_shop_purchase_refresh", "shop purchases should force planner-owned inventory refresh before item execution"
    assert whistle_execution_payload.get("reassess_transition", {}).get("transition_kind") == "reset_whistle_reroll", "whistle case should model planner-first Reset Whistle reassess transitions"
    assert "Reset Whistle" in (whistle_execution_payload.get("reassess_transition", {}).get("reason") or ""), "whistle case should explain why the planner invalidated the selected action"
    assert whistle_subgraph.get("path") == [
      "inventory_refresh",
      "replan_items",
      "execute_pre_action_items",
      "await_lobby_after_items",
      "reassess",
    ], "whistle item plan should expose a planner-owned reassess subgraph"
    assert any(
      transition.get("to") == "reassess" and transition.get("reobserve")
      for transition in whistle_subgraph.get("transitions") or []
    ), "whistle item subgraph should model reassess as an explicit transition"
    assert whistle_planned_actions.get("item_plan_subgraph", {}).get("path") == whistle_subgraph.get("path"), "planned actions should surface the planner-owned item subgraph"
    assert whistle_planned_actions.get("reassess_boundary", {}).get("required") is True, "planned actions should surface reassess boundary metadata"
    deferred_keys = [entry.get("key") for entry in whistle_planned_actions.get("deferred_use") or []]
    assert "motivating_megaphone" in deferred_keys, "whistle case should defer follow-up burst items into the planner reassess pass"
    deferred_entry = next(entry for entry in whistle_planned_actions.get("deferred_use") or [] if entry.get("key") == "motivating_megaphone")
    assert "post-whistle reassess" in (deferred_entry.get("reason") or ""), "whistle case should explain why the burst item was deferred"
    assert whistle_reobserve_boundaries and whistle_reobserve_boundaries[0].get("trigger_items") == ["reset_whistle"], "whistle case should register a planner-owned reobserve boundary"
    whistle_step_sequence = [step.to_dict() for step in whistle_turn_plan.step_sequence]
    whistle_refresh_step = next(step for step in whistle_step_sequence if step.get("step_type") == "refresh_inventory_for_items")
    whistle_transition_step = next(step for step in whistle_step_sequence if step.get("step_type") == "transition_reassess_after_items")
    whistle_item_step = next(step for step in whistle_step_sequence if step.get("step_type") == "execute_pre_action_items")
    assert any(click.get("label") == "Open use-items inventory" for click in whistle_refresh_step.get("planned_clicks") or []), "planner-owned refresh step should surface the inventory re-entry clicks"
    assert any(click.get("label") == "Increment Reset Whistle" for click in whistle_item_step.get("planned_clicks") or []), "whistle execution step should carry planner-owned item-use clicks"
    assert whistle_transition_step.get("success_transition") == "reassess", "planner transition step should hand off whistle turns to reassess"

    energy_state, energy_action = _prepare_case("training")
    energy_state["trackblazer_shop_summary"] = {
      "shop_coins": 0,
      "items_detected": [],
      "purchasable_items": [],
    }
    energy_state["trackblazer_shop_items"] = []
    with patch(
      "core.trackblazer.planner.plan_item_usage",
      return_value={
        "context": {
          "training_name": "speed",
          "training_score": 42.0,
          "energy_rescue": True,
          "commit_training_after_items": False,
        },
        "candidates": [
          {
            "key": "vita_20",
            "name": "Vita 20",
            "usage_group": "energy",
            "reason": "synthetic energy rescue",
          },
          {
            "key": "good_luck_charm",
            "name": "Good-Luck Charm",
            "usage_group": "support",
            "reason": "synthetic fail-safe pairing",
          },
        ],
        "deferred": [],
      },
    ):
      energy_snapshot = build_review_snapshot(
        energy_state,
        energy_action,
        reasoning_notes="synthetic energy reassess case",
        ocr_debug=[],
      )
    energy_turn_plan = TurnPlan.from_snapshot((energy_state.get("trackblazer_planner_state") or {}).get("turn_plan") or {})
    energy_item_plan = dict(energy_turn_plan.item_plan or {})
    energy_execution_payload = dict((energy_snapshot.get("planner_execution_payload") or {}).get("item_execution") or {})
    energy_subgraph = dict(energy_item_plan.get("subgraph") or {})
    energy_selected_action = energy_snapshot.get("selected_action") or {}
    assert [entry.get("key") for entry in energy_selected_action.get("pre_action_item_use") or []] == ["vita_20", "good_luck_charm"], "energy rescue case should keep the planner-owned energy/failsafe item set"
    assert energy_selected_action.get("reassess_after_item_use") is True, "energy rescue should remain a planner-owned reassess transition"
    assert energy_execution_payload.get("inventory_refresh", {}).get("trigger") == "pre_action_refresh", "energy rescue without shop buys should refresh inventory immediately before item use"
    assert energy_execution_payload.get("reassess_transition", {}).get("transition_kind") == "energy_rescue_reassess", "energy rescue should model planner-first reassess transitions"
    assert "Energy items change" in (energy_execution_payload.get("reassess_transition", {}).get("reason") or ""), "energy rescue should explain why the planner invalidated the selected action"
    assert energy_subgraph.get("path", [])[-1:] == ["reassess"], "energy rescue subgraph should end in reassess"
    assert any(
      transition.get("to") == "reassess" and "vita_20" in list(transition.get("trigger_items") or [])
      for transition in energy_subgraph.get("transitions") or []
    ), "energy rescue subgraph should tie the reassess transition to the energy item"
    energy_reobserve = list(energy_snapshot.get("planner_execution_payload", {}).get("reobserve_boundaries") or [])
    assert energy_reobserve and "Energy items change" in (energy_reobserve[0].get("reason") or ""), "planner execution payload should expose energy-item reassess boundaries before execute"
    energy_step_sequence = [step.to_dict() for step in energy_turn_plan.step_sequence]
    energy_transition_step = next(step for step in energy_step_sequence if step.get("step_type") == "transition_reassess_after_items")
    assert energy_transition_step.get("metadata", {}).get("transition_kind") == "energy_rescue_reassess", "energy case should expose the explicit reassess transition on the planner step"

    healthy_training_state = _base_state()
    healthy_training_state["year"] = "Senior Year Early Feb"
    healthy_training_state["turn"] = 122
    healthy_training_state["current_mood"] = "GREAT"
    healthy_training_state["energy_level"] = 67
    healthy_training_state["max_energy"] = 126
    healthy_training_state["trackblazer_inventory"] = {
      "vita_40": {"detected": True, "held_quantity": 1, "increment_target": (1, 1), "category": "energy"},
      "speed_ankle_weights": {"detected": True, "held_quantity": 1, "increment_target": (2, 2), "category": "training_boost"},
      "motivating_megaphone": {"detected": True, "held_quantity": 3, "increment_target": (3, 3), "category": "mood"},
      "good_luck_charm": {"detected": True, "held_quantity": 2, "increment_target": (4, 4), "category": "support"},
      "coaching_megaphone": {"detected": True, "held_quantity": 1, "increment_target": (5, 5), "category": "mood"},
      "plain_cupcake": {"detected": True, "held_quantity": 1, "increment_target": (6, 6), "category": "mood"},
    }
    healthy_training_state["trackblazer_inventory_summary"] = {
      "held_quantities": {
        "vita_40": 1,
        "speed_ankle_weights": 1,
        "motivating_megaphone": 3,
        "good_luck_charm": 2,
        "coaching_megaphone": 1,
        "plain_cupcake": 1,
      },
      "items_detected": [
        "vita_40",
        "speed_ankle_weights",
        "motivating_megaphone",
        "good_luck_charm",
        "coaching_megaphone",
        "plain_cupcake",
      ],
      "actionable_items": [
        "vita_40",
        "speed_ankle_weights",
        "motivating_megaphone",
        "good_luck_charm",
        "coaching_megaphone",
        "plain_cupcake",
      ],
      "by_category": {
        "energy": ["vita_40"],
        "training_boost": ["speed_ankle_weights"],
        "mood": ["motivating_megaphone", "coaching_megaphone", "plain_cupcake"],
        "support": ["good_luck_charm"],
      },
      "total_detected": 6,
    }
    healthy_training_action = Action()
    healthy_training_action.func = "do_training"
    healthy_training_action.available_actions = ["do_training", "do_rest", "do_race"]
    healthy_training_action["training_name"] = "spd"
    healthy_training_action["training_function"] = "stat_weight_training"
    healthy_training_action["training_data"] = {
      "name": "spd",
      "weighted_stat_score": 58.0,
      "score_tuple": (58.0, 0),
      "stat_gains": {"speed": 39, "power": 19, "sp": 6},
      "failure": 1,
      "total_supports": 3,
      "total_rainbow_friends": 2,
      "total_friendship_levels": {"blue": 0, "green": 0, "yellow": 0, "max": 3},
      "failure_bypassed_by_items": False,
    }
    healthy_training_action["available_trainings"] = {
      "spd": dict(healthy_training_action["training_data"]),
    }
    healthy_training_plan = item_use_module.plan_item_usage(
      policy=getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None),
      state_obj=healthy_training_state,
      action=healthy_training_action,
      limit=8,
    )
    healthy_candidate_keys = [entry.get("key") for entry in healthy_training_plan.get("candidates") or []]
    healthy_deferred = {
      entry.get("key"): entry
      for entry in healthy_training_plan.get("deferred") or []
      if entry.get("key")
    }
    assert "vita_40" not in healthy_candidate_keys, "healthy >50% energy training should not stage Vita just because failure is nonzero"
    assert set(healthy_candidate_keys[:2]) == {"motivating_megaphone", "speed_ankle_weights"}, healthy_training_plan
    assert "vita_40" in healthy_deferred, "healthy >50% energy training should explicitly defer the held Vita"
    assert "energy healthy" in (healthy_deferred["vita_40"].get("reason") or ""), healthy_deferred["vita_40"]

    rest_race_state = _base_state()
    rest_race_state["year"] = "Junior Year Late Sep"
    rest_race_state["turn"] = 7
    rest_race_state["energy_level"] = 46
    rest_race_state["max_energy"] = 126
    rest_race_state["rival_indicator_detected"] = False
    rest_race_action, rest_race_training_results = _rest_with_failure_blocked_training_action(blocked_score=36.0)
    rest_race_state["training_results"] = dict(rest_race_training_results)
    rest_race_decision = evaluate_trackblazer_race(rest_race_state, rest_race_action)
    assert rest_race_decision.get("should_race") is True, rest_race_decision
    assert rest_race_decision.get("fallback_non_rival_race") is True, rest_race_decision
    assert rest_race_decision.get("training_score") == 36.0, rest_race_decision

    edge_blocked_state = _base_state()
    edge_blocked_state["year"] = "Junior Year Late Sep"
    edge_blocked_state["turn"] = 7
    edge_blocked_state["energy_level"] = 46
    edge_blocked_state["max_energy"] = 126
    edge_blocked_state["rival_indicator_detected"] = False
    edge_blocked_action, edge_blocked_training_results = _rest_with_failure_blocked_training_action(blocked_score=40.0)
    edge_blocked_state["training_results"] = dict(edge_blocked_training_results)
    edge_blocked_decision = evaluate_trackblazer_race(edge_blocked_state, edge_blocked_action)
    assert edge_blocked_decision.get("should_race") is True, edge_blocked_decision
    assert edge_blocked_decision.get("fallback_non_rival_race") is True, edge_blocked_decision

    strong_blocked_state = _base_state()
    strong_blocked_state["year"] = "Junior Year Late Sep"
    strong_blocked_state["turn"] = 7
    strong_blocked_state["energy_level"] = 46
    strong_blocked_state["max_energy"] = 126
    strong_blocked_state["rival_indicator_detected"] = False
    strong_blocked_action, strong_blocked_training_results = _rest_with_failure_blocked_training_action(blocked_score=41.0)
    strong_blocked_state["training_results"] = dict(strong_blocked_training_results)
    strong_blocked_decision = evaluate_trackblazer_race(strong_blocked_state, strong_blocked_action)
    assert strong_blocked_decision.get("should_race") is False, strong_blocked_decision
    assert strong_blocked_decision.get("fallback_non_rival_race") is False, strong_blocked_decision

  assert all(count == 0 for count in traversal_calls.values()), traversal_calls
  print(json.dumps({
    "cases": cases,
    "traversal_calls": traversal_calls,
  }, indent=2, sort_keys=True))


if __name__ == "__main__":
  main()
