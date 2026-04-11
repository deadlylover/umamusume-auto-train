import copy
from contextlib import ExitStack
from unittest.mock import patch

import core.bot as bot
import core.config as config
import utils.constants as constants
from core.actions import Action
from core.skeleton import (
  _activate_trackblazer_planner_turn,
  _attach_trackblazer_pre_action_item_plan,
  build_review_snapshot,
  career_lobby,
)
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


def _training_action(*, score=22.0, supports=1):
  action = Action()
  action.func = "do_training"
  action.available_actions = ["do_training", "do_rest", "do_race"]
  action["training_name"] = "speed"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = {
    "name": "speed",
    "score_tuple": (score, 0),
    "weighted_stat_score": score,
    "stat_gains": {"speed": 17, "power": 5, "sp": 2},
    "failure": 0,
    "total_supports": supports,
    "total_rainbow_friends": 0,
  }
  action["available_trainings"] = {"speed": copy.deepcopy(action["training_data"])}
  return action


def _legacy_runtime_state():
  state_obj = _base_state()
  state_obj["year"] = "Senior Year"
  state_obj["turn"] = "Early Oct"
  return state_obj


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
    recheck_selected_training_after_item_use=lambda *args, **kwargs: {"status": "reassess", "reason": "unused_in_check"},
    run_post_energy_item_followup=lambda *args, **kwargs: {"status": "ready", "reason": "unused_in_check"},
    run_post_reset_whistle_replan=lambda *args, **kwargs: {"status": "blocked", "reason": "unused_in_check"},
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
    should_force_rest_after_consecutive_warning=lambda action: False,
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


def _test_planner_prefers_training_over_race_gate_rest_fallback():
  bot.set_trackblazer_use_new_planner_enabled(True)
  state_obj = _base_state()
  state_obj["year"] = "Junior Year Late Nov"
  state_obj["turn"] = 3
  action = _rest_action()

  planner_race_decision = {
    "should_race": False,
    "reason": "Operator race gate disabled racing on Junior Year Late Nov",
    "training_total_stats": 18,
    "training_score": 16.0,
    "training_supports": 2,
    "prefer_rest_over_weak_training": False,
    "race_available": False,
    "rival_indicator": True,
  }

  with patch("core.trackblazer.planner.evaluate_trackblazer_race", lambda *_args, **_kwargs: dict(planner_race_decision)):
    activation = _activate_trackblazer_planner_turn(state_obj, action)

  assert activation.get("status") == "planner", activation
  snapshot = _assert_snapshot_path(state_obj, action, "planner_runtime", "race_gate_rest_fallback")
  selected_action = snapshot.get("selected_action") or {}
  assert selected_action.get("func") == "do_training", selected_action
  assert selected_action.get("training_name") == "speed", selected_action
  assert (selected_action.get("trackblazer_race_decision") or {}).get("reason") == planner_race_decision["reason"], selected_action


def _test_planner_does_not_reselect_optional_race_when_operator_gate_blocks_date():
  bot.set_trackblazer_use_new_planner_enabled(True)
  state_obj = _base_state()
  state_obj["year"] = "Classic Year Early Nov"
  state_obj["turn"] = 4
  state_obj["current_mood"] = "GREAT"
  state_obj["energy_level"] = 109
  state_obj["max_energy"] = 131
  state_obj["rival_indicator_detected"] = True
  state_obj["training_results"] = {
    "speed": {
      "name": "speed",
      "score_tuple": (22.0, 0),
      "weighted_stat_score": 22.0,
      "stat_gains": {"speed": 17, "power": 5, "sp": 2},
      "failure": 0,
      "total_supports": 1,
      "total_rainbow_friends": 0,
      "total_friendship_levels": {"blue": 0, "green": 1, "yellow": 0, "max": 1},
    }
  }
  action = _training_action(score=22.0, supports=1)

  blocked_selector = {
    "enabled": True,
    "dates": [
      {
        "year": "Classic Year",
        "date": "Early Nov",
        "name": "Queen Elizabeth II Cup",
        "race_allowed": False,
      }
    ],
  }

  with patch.object(config, "OPERATOR_RACE_SELECTOR", blocked_selector), patch.object(config, "RACE_SCHEDULE", {}):
    activation = _activate_trackblazer_planner_turn(state_obj, action)
    assert activation.get("status") == "planner", activation
    snapshot = _assert_snapshot_path(state_obj, action, "planner_runtime", "blocked_optional_race_keeps_training")
    selected_action = snapshot.get("selected_action") or {}
    planned_clicks = list(snapshot.get("planned_clicks") or [])
    assert selected_action.get("func") == "do_training", selected_action
    assert selected_action.get("training_name") == "speed", selected_action
    assert (selected_action.get("trackblazer_race_decision") or {}).get("should_race") is False, selected_action
    assert "Open training menu" in ((planned_clicks[0] or {}).get("label") if planned_clicks else ""), planned_clicks


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


def _test_activation_failure_restores_action_payload():
  bot.set_trackblazer_use_new_planner_enabled(True)
  state_obj = _base_state()
  action = _rest_action()
  original_payload = {
    "func": action.func,
    "options": copy.deepcopy(action.options),
    "available_actions": list(action.available_actions),
  }

  def _mutating_failure(*args, **kwargs):
    target_action = args[1]
    target_action.func = "do_race"
    target_action["race_name"] = "any"
    target_action["trackblazer_climax_race_day"] = True
    raise RuntimeError("synthetic planner activation failure")

  with patch("core.skeleton.set_turn_plan_decision_path", side_effect=_mutating_failure):
    activation = _activate_trackblazer_planner_turn(state_obj, action)

  assert activation.get("status") == "fallback", activation
  assert action.func == original_payload["func"], action.func
  assert action.options == original_payload["options"], action.options
  assert list(action.available_actions) == original_payload["available_actions"], action.available_actions


def _test_legacy_hydration_failure_keeps_action_unchanged():
  bot.set_trackblazer_use_new_planner_enabled(False)
  state_obj = _base_state()
  action = _rest_action()
  original_payload = {
    "func": action.func,
    "options": copy.deepcopy(action.options),
    "available_actions": list(action.available_actions),
  }

  with patch("core.skeleton._hydrate_legacy_action_from_turn_plan", side_effect=RuntimeError("synthetic hydration failure")):
    hydrated_action = _attach_trackblazer_pre_action_item_plan(state_obj, action)

  assert hydrated_action is action
  assert action.func == original_payload["func"], action.func
  assert action.options == original_payload["options"], action.options
  assert list(action.available_actions) == original_payload["available_actions"], action.available_actions
  hydration_failure = state_obj.get("_trackblazer_planner_hydration_failure") or {}
  assert "planner_hydration_failed" in (hydration_failure.get("reason") or ""), hydration_failure
  snapshot = build_review_snapshot(state_obj, action, reasoning_notes="legacy_hydration_failure", ocr_debug=[])
  assert snapshot.get("trackblazer_runtime_path") == "legacy_runtime", snapshot.get("trackblazer_runtime_path")
  assert (snapshot.get("selected_action") or {}).get("func") == "do_rest", snapshot.get("selected_action")


def _test_career_lobby_legacy_hydration_failure_still_completes_turn():
  bot.set_trackblazer_use_new_planner_enabled(False)
  original_running = bot.is_bot_running
  bot.is_bot_running = True
  state_template = _legacy_runtime_state()
  recorded = {}

  class _FakeStrategy:
    def validate_state_details(self, state_obj):
      return {"valid": True}

    def get_training_template(self, state_obj):
      return {"training_function": "stat_weight_training"}

    def check_scheduled_races(self, state_obj, action):
      return action

    def decide_race_for_goal(self, state_obj, action):
      return action

    def decide(self, state_obj, action):
      return _rest_action()

  def _record_and_finalize_turn(state_obj, action):
    recorded["state"] = copy.deepcopy(state_obj)
    recorded["action"] = {
      "func": action.func,
      "options": copy.deepcopy(action.options),
      "available_actions": list(action.available_actions),
    }
    bot.is_bot_running = False

  try:
    with ExitStack() as stack:
      stack.enter_context(patch("core.skeleton.sleep", lambda *_args, **_kwargs: None))
      stack.enter_context(patch("core.skeleton.init_skill_py", lambda: None))
      stack.enter_context(patch("core.skeleton.clear_aptitudes_cache", lambda: None))
      stack.enter_context(patch("core.skeleton.update_startup_scan_snapshot", lambda *args, **kwargs: None))
      stack.enter_context(patch("core.skeleton.update_operator_snapshot", lambda *args, **kwargs: None))
      stack.enter_context(patch("core.skeleton.update_pre_action_phase", lambda *args, **kwargs: None))
      stack.enter_context(patch("core.skeleton.log_encoded", lambda *args, **kwargs: None))
      stack.enter_context(patch("core.skeleton.ensure_operator_console", lambda: None))
      stack.enter_context(patch("core.skeleton.detect_scenario", lambda: "trackblazer"))
      stack.enter_context(patch("core.skeleton.Strategy", _FakeStrategy))
      stack.enter_context(patch("core.skeleton.collect_main_state", lambda: copy.deepcopy(state_template)))
      stack.enter_context(patch("core.skeleton.collect_training_state", lambda state_obj, *_args, **_kwargs: state_obj))
      stack.enter_context(patch("core.skeleton.collect_trackblazer_inventory", lambda state_obj, **_kwargs: state_obj))
      stack.enter_context(patch("core.skeleton.maybe_review_skill_purchase", lambda *args, **kwargs: "attached"))
      stack.enter_context(patch("core.skeleton.run_action_with_review", lambda *args, **kwargs: "executed"))
      stack.enter_context(patch("core.skeleton.record_and_finalize_turn", _record_and_finalize_turn))
      stack.enter_context(patch("core.skeleton._restore_turn_from_last_state", lambda *args, **kwargs: None))
      stack.enter_context(patch("core.skeleton._restore_cached_trackblazer_inventory", lambda *args, **kwargs: True))
      stack.enter_context(patch("core.skeleton._detect_trackblazer_complete_career_banner", lambda **kwargs: {"detected": False}))
      stack.enter_context(patch("core.skeleton._detect_stable_career_screen_anchors", lambda *_args, **_kwargs: {"training_button": 1, "rest_button": 1}))
      stack.enter_context(patch("core.skeleton._has_stable_career_screen", lambda *_args, **_kwargs: True))
      stack.enter_context(patch("core.skeleton._hydrate_legacy_action_from_turn_plan", side_effect=RuntimeError("synthetic hydration failure")))
      stack.enter_context(patch("scenarios.trackblazer.check_trackblazer_shop_inventory", lambda **kwargs: {
        "trackblazer_shop_items": [],
        "trackblazer_shop_summary": {"shop_coins": 0, "items_detected": [], "purchasable_items": []},
        "trackblazer_shop_flow": {"entered": False, "closed": False, "reason": "synthetic_no_shop_scan"},
      }))
      stack.enter_context(patch("scenarios.trackblazer.inspect_climax_race_day_detection", lambda log_result=True: {"detected": False, "banner": {}, "button": {}}))
      stack.enter_context(patch("scenarios.trackblazer.check_rival_race_indicator", lambda state_obj: False))
      stack.enter_context(patch.object(bot, "get_execution_intent", lambda: "execute"))
      stack.enter_context(patch.object(bot, "has_pending_trackblazer_shop_check", lambda: False))
      stack.enter_context(patch.object(bot, "clear_trackblazer_shop_check_request", lambda: None))
      stack.enter_context(patch.object(bot, "get_trackblazer_scoring_mode", lambda: "balanced"))
      stack.enter_context(patch.object(bot, "get_trackblazer_allow_buff_override", lambda: False))
      stack.enter_context(patch.object(bot, "push_debug_history", lambda *args, **kwargs: None))
      stack.enter_context(patch("core.skeleton.device_action.flush_screenshot_cache", lambda: None))
      stack.enter_context(patch("core.skeleton.device_action.screenshot", lambda *args, **kwargs: object()))
      stack.enter_context(patch("core.skeleton.device_action.match_cached_templates", lambda *args, **kwargs: {}))
      stack.enter_context(patch("core.skeleton.device_action.match_template", lambda *args, **kwargs: False))
      stack.enter_context(patch("core.skeleton.device_action.locate", lambda *args, **kwargs: False))
      stack.enter_context(patch("core.skeleton.device_action.locate_and_click", lambda *args, **kwargs: False))
      career_lobby(dry_run_turn=False)
  finally:
    bot.is_bot_running = original_running

  assert recorded.get("action", {}).get("func") == "do_rest", recorded
  assert recorded.get("state", {}).get("_trackblazer_planner_hydration_failure"), recorded
  assert "planner_hydration_failed" in (
    (recorded.get("state", {}).get("_trackblazer_planner_hydration_failure") or {}).get("reason") or ""
  ), recorded


def main():
  config.reload_config(print_config=False)
  constants.SCENARIO_NAME = "trackblazer"
  try:
    _test_path_provenance_distinguishes_runtime_modes()
    _test_pre_debut_planner_still_prefers_training_over_stale_rest()
    _test_planner_prefers_training_over_race_gate_rest_fallback()
    _test_planner_does_not_reselect_optional_race_when_operator_gate_blocks_date()
    _test_planner_snapshot_and_boundary_history_are_copyable()
    _test_forced_runtime_fallback_sets_explicit_path()
    _test_activation_failure_restores_action_payload()
    _test_legacy_hydration_failure_keeps_action_unchanged()
    _test_career_lobby_legacy_hydration_failure_still_completes_turn()
    print("trackblazer planner milestone 7 checks: ok")
  finally:
    bot.set_trackblazer_use_new_planner_enabled(False)


if __name__ == "__main__":
  main()
