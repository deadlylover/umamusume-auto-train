import copy
from unittest.mock import patch

import core.bot as bot
import core.config as config
import utils.constants as constants
from core.actions import Action
from core.skeleton import _activate_trackblazer_planner_turn, build_review_snapshot
from core.trackblazer.executor import PlannerExecutorHooks
from core.trackblazer.runtime import PlannerRuntimeHooks, run_trackblazer_planner_turn


def _base_state():
  return {
    "year": "Junior Year Pre-Debut",
    "turn": 11,
    "criteria": "Reach the next fan target",
    "energy_level": 100,
    "max_energy": 100,
    "current_mood": "GOOD",
    "current_stats": {"spd": 240, "sta": 180, "pwr": 210, "guts": 120, "wit": 160, "sp": 80},
    "date_event_available": True,
    "race_mission_available": False,
    "aptitudes": {"track": "A", "distance": "A"},
    "status_effect_names": [],
    "rival_indicator_detected": False,
    "trackblazer_climax": False,
    "trackblazer_climax_locked_race": False,
    "trackblazer_climax_race_day": False,
    "trackblazer_lobby_scheduled_race": False,
    "trackblazer_trainings_remaining_upper_bound": 6,
    "trackblazer_inventory_summary": {"held_quantities": {}, "items_detected": [], "actionable_items": [], "by_category": {}, "total_detected": 0},
    "trackblazer_inventory_flow": {},
    "trackblazer_shop_summary": {"shop_coins": 0, "items_detected": [], "purchasable_items": []},
    "trackblazer_shop_flow": {},
    "skill_purchase_check": {"should_check": False, "current_sp": 120, "threshold_sp": 400, "auto_buy_skill_enabled": False, "reason": "below threshold"},
    "skill_purchase_flow": {"skipped": True, "reason": "below threshold"},
    "skill_purchase_scan": {},
    "skill_purchase_plan": {},
    "training_results": {
      "speed": {
        "name": "speed",
        "score_tuple": (56.0, 0),
        "weighted_stat_score": 56.0,
        "stat_gains": {"speed": 18, "power": 7, "sp": 8},
        "failure": 2,
        "total_supports": 3,
        "total_rainbow_friends": 1,
        "total_friendship_levels": {"blue": 1, "green": 1, "yellow": 0, "max": 0},
      }
    },
    "state_validation": {"valid": True},
  }


def _rest_action():
  action = Action()
  action.func = "do_rest"
  action.available_actions = ["do_training", "do_rest", "do_race"]
  action["training_name"] = "speed"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = {
    "name": "speed",
    "score_tuple": (56.0, 0),
    "weighted_stat_score": 56.0,
    "stat_gains": {"speed": 18, "power": 7, "sp": 8},
    "failure": 2,
    "total_supports": 3,
    "total_rainbow_friends": 1,
  }
  action["available_trainings"] = {"speed": copy.deepcopy(action["training_data"])}
  return action


def _runtime_executor_hooks():
  return PlannerExecutorHooks(
    skill_purchase_plan=lambda action: {},
    review_action_before_execution=lambda *args, **kwargs: True,
    wait_for_execute_intent=lambda *args, **kwargs: "check_only",
    run_skill_purchase_plan=lambda *args, **kwargs: {"status": "skipped"},
    run_trackblazer_shop_purchases=lambda *args, **kwargs: {"status": "skipped"},
    wait_for_lobby_after_shop_purchase=lambda: True,
    refresh_trackblazer_pre_action_inventory=lambda *args, **kwargs: {"status": "skipped"},
    execute_trackblazer_pre_action_items=lambda *args, **kwargs: {"status": "skipped"},
    wait_for_lobby_after_item_use=lambda *args, **kwargs: True,
    enforce_operator_race_gate_before_execute=lambda *args, **kwargs: None,
    run_planner_race_preflight=lambda *args, **kwargs: None,
    resolve_post_action_resolution=lambda *args, **kwargs: True,
    trackblazer_action_failure_should_block_retry=lambda *args, **kwargs: False,
    update_operator_snapshot=lambda *args, **kwargs: None,
  )


def _runtime_hooks():
  return PlannerRuntimeHooks(
    attach_skill_purchase_plan=lambda *args, **kwargs: "attached",
    attach_trackblazer_pre_action_item_plan=lambda state_obj, action: action,
    push_turn_retry_debug=lambda *args, **kwargs: None,
    update_operator_snapshot=lambda *args, **kwargs: None,
    should_retry_training_after_consecutive_warning=lambda action: False,
    prepare_training_fallback_after_consecutive_warning=lambda action: False,
  )


def _assert_snapshot_path(state_obj, action, expected_path, note):
  snapshot = build_review_snapshot(state_obj, action, reasoning_notes=note, ocr_debug=[])
  assert snapshot.get("trackblazer_runtime_path") == expected_path, note
  assert (snapshot.get("state_summary") or {}).get("trackblazer_runtime_path") == expected_path, note
  assert f"Path: {expected_path}" in snapshot.get("turn_discussion_text", ""), note
  return snapshot


def _test_path_provenance_distinguishes_runtime_modes():
  # Display hydration only, legacy runtime path.
  bot.set_trackblazer_use_new_planner_enabled(True)
  state_obj = _base_state()
  action = _rest_action()
  _assert_snapshot_path(state_obj, action, "legacy_runtime", "display_only_should_be_legacy_runtime")

  # Planner runtime active.
  planner_state = _base_state()
  planner_action = _rest_action()
  activation = _activate_trackblazer_planner_turn(planner_state, planner_action)
  assert activation.get("status") == "planner", activation
  _assert_snapshot_path(planner_state, planner_action, "planner_runtime", "planner_runtime_path")

  # Planner activation fallback path.
  fallback_state = _base_state()
  fallback_action = _rest_action()
  with patch("core.trackblazer.planner._build_planner_race_plan", side_effect=RuntimeError("synthetic planner failure")):
    activation = _activate_trackblazer_planner_turn(fallback_state, fallback_action)
  assert activation.get("status") == "fallback", activation
  _assert_snapshot_path(fallback_state, fallback_action, "planner_fallback_legacy", "planner_fallback_path")


def _test_pre_debut_planner_still_prefers_training_over_stale_rest():
  bot.set_trackblazer_use_new_planner_enabled(True)
  state_obj = _base_state()
  action = _rest_action()
  activation = _activate_trackblazer_planner_turn(state_obj, action)
  assert activation.get("status") == "planner"
  snapshot = _assert_snapshot_path(state_obj, action, "planner_runtime", "pre_debut_training_guard")
  assert (snapshot.get("selected_action") or {}).get("func") == "do_training", snapshot.get("selected_action")
  assert (snapshot.get("selected_action") or {}).get("training_name") == "speed"


def _test_planner_snapshot_and_boundary_history_are_copyable():
  bot.set_trackblazer_use_new_planner_enabled(True)
  bot.clear_debug_history()
  state_obj = _base_state()
  action = _rest_action()
  activation = _activate_trackblazer_planner_turn(state_obj, action)
  assert activation.get("status") == "planner"
  snapshot = build_review_snapshot(state_obj, action, reasoning_notes="planner_history", ocr_debug=[])
  planned_clicks = list(snapshot.get("planned_clicks") or [])
  assert planned_clicks, snapshot
  assert (planned_clicks[0] or {}).get("label") == "Open training menu", planned_clicks
  quick_bar = snapshot.get("quick_bar") or {}
  assert "Open training menu" in (quick_bar.get("planned_clicks_text") or ""), quick_bar

  history = bot.get_debug_history()
  planner_boundary = next(
    (entry for entry in history if entry.get("event") == "planner_boundary" and entry.get("result") == "planner_runtime"),
    None,
  )
  assert planner_boundary is not None, history
  planner_snapshot = next(
    (entry for entry in history if entry.get("event") == "planner_snapshot" and entry.get("result") == "planner_runtime"),
    None,
  )
  assert planner_snapshot is not None, history
  assert planner_snapshot.get("decision_path") == "planner", planner_snapshot
  assert "Open training menu" in (planner_snapshot.get("note") or ""), planner_snapshot


def _test_forced_runtime_fallback_sets_explicit_path():
  bot.set_trackblazer_use_new_planner_enabled(True)
  state_obj = _base_state()
  action = _rest_action()
  with patch("core.trackblazer.runtime.set_turn_plan_decision_path", side_effect=RuntimeError("synthetic setup failure")):
    result = run_trackblazer_planner_turn(
      state_obj,
      action,
      0,
      "review",
      executor_hooks=_runtime_executor_hooks(),
      runtime_hooks=_runtime_hooks(),
    )
  assert result.get("status") == "fallback_to_legacy", result
  assert state_obj.get("trackblazer_runtime_path") == "planner_fallback_legacy", state_obj.get("trackblazer_runtime_path")
  snapshot = _assert_snapshot_path(state_obj, action, "planner_fallback_legacy", "runtime_fallback_snapshot")
  planner_runtime = snapshot.get("trackblazer_planner_runtime") or {}
  assert planner_runtime.get("runtime_path") == "planner_fallback_legacy"


def main():
  config.reload_config(print_config=False)
  constants.SCENARIO_NAME = "trackblazer"
  try:
    _test_path_provenance_distinguishes_runtime_modes()
    _test_pre_debut_planner_still_prefers_training_over_stale_rest()
    _test_planner_snapshot_and_boundary_history_are_copyable()
    _test_forced_runtime_fallback_sets_explicit_path()
    print("trackblazer planner milestone 7 checks: ok")
  finally:
    bot.set_trackblazer_use_new_planner_enabled(False)


if __name__ == "__main__":
  main()
