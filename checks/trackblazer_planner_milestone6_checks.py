import inspect
from unittest.mock import patch

import core.config as config
import utils.constants as constants
from core.actions import Action
from core.trackblazer.executor import PlannerExecutorHooks, run_planner_action_with_review
from core.trackblazer.models import ExecutionStep, PlannerFreshness, TurnPlan
from core.trackblazer.runtime import PlannerRuntimeHooks, run_trackblazer_planner_turn
import core.skeleton as skeleton


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
    "trackblazer_inventory_summary": {
      "held_quantities": {},
      "items_detected": [],
      "actionable_items": [],
      "by_category": {},
      "total_detected": 0,
    },
    "trackblazer_inventory_flow": {},
    "trackblazer_shop_summary": {"shop_coins": 0, "items_detected": [], "purchasable_items": []},
    "trackblazer_shop_flow": {},
    "skill_purchase_check": {
      "should_check": False,
      "current_sp": 240,
      "threshold_sp": 400,
      "auto_buy_skill_enabled": False,
      "reason": "below threshold",
    },
    "skill_purchase_flow": {"skipped": True, "reason": "below threshold"},
    "skill_purchase_scan": {},
    "skill_purchase_plan": {},
    "training_results": {
      "speed": {
        "name": "speed",
        "score_tuple": (42.0, 0),
        "weighted_stat_score": 42.0,
        "stat_gains": {"speed": 20, "power": 8, "sp": 10},
        "failure": 0,
        "total_supports": 4,
        "total_rainbow_friends": 1,
        "total_friendship_levels": {"blue": 2, "green": 1, "yellow": 0, "max": 0},
      },
    },
    "state_validation": {"valid": True},
  }


def _training_action():
  action = Action()
  action.func = "do_training"
  action.available_actions = ["do_training", "do_rest", "do_race"]
  action["training_name"] = "speed"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = {
    "name": "speed",
    "score_tuple": (42.0, 0),
    "weighted_stat_score": 42.0,
    "stat_gains": {"speed": 20, "power": 8, "sp": 10},
    "failure": 0,
    "total_supports": 4,
    "total_rainbow_friends": 1,
  }
  action["available_trainings"] = {"speed": dict(action["training_data"])}
  return action


def _race_action():
  action = _training_action()
  action.func = "do_race"
  action.available_actions = ["do_race", "do_training", "do_rest"]
  action["race_name"] = "any"
  action["prefer_rival_race"] = True
  return action


def _turn_plan(*step_types, fallback_policy=None):
  return TurnPlan(
    version=3,
    decision_path="planner",
    fallback_policy=dict(fallback_policy or {}),
    freshness=PlannerFreshness(turn_key="Senior Year Early Jul|12"),
    step_sequence=[
      ExecutionStep(
        step_id=step_type,
        step_type=step_type,
        intent=step_type,
      )
      for step_type in step_types
    ],
  )


def _executor_hooks(recorder, *, execute_intent="execute", item_result=None, reset_whistle_replan_result=None):
  item_result = item_result or {"status": "skipped", "reason": "no_pre_action_items"}
  reset_whistle_replan_result = reset_whistle_replan_result or {"status": "blocked", "reason": "unused_in_check"}

  def _review(*args, **kwargs):
    recorder.append(("review", kwargs.get("reasoning_notes")))
    return True

  def _wait(*args, **kwargs):
    recorder.append(("wait", execute_intent))
    return execute_intent

  def _refresh(*args, **kwargs):
    recorder.append(("refresh_items", None))
    return {"status": "ready", "reason": "refreshed"}

  def _execute_items(*args, **kwargs):
    recorder.append(("execute_items", kwargs.get("commit_mode")))
    return dict(item_result)

  hooks = PlannerExecutorHooks(
    skill_purchase_plan=lambda action: {},
    review_action_before_execution=_review,
    wait_for_execute_intent=_wait,
    run_skill_purchase_plan=lambda state_obj, action, current_action_count: recorder.append(("skill", None)) or {"status": "skipped"},
    run_trackblazer_shop_purchases=lambda state_obj, action: recorder.append(("shop", None)) or {"status": "skipped"},
    wait_for_lobby_after_shop_purchase=lambda: recorder.append(("wait_shop", None)) or True,
    refresh_trackblazer_pre_action_inventory=_refresh,
    execute_trackblazer_pre_action_items=_execute_items,
    recheck_selected_training_after_item_use=lambda *args, **kwargs: recorder.append(("recheck_selected_training", None)) or {"status": "reassess", "reason": "unused_in_check"},
    run_post_energy_item_followup=lambda *args, **kwargs: recorder.append(("post_energy_followup", None)) or {"status": "ready", "reason": "unused_in_check"},
    run_post_reset_whistle_replan=lambda *args, **kwargs: recorder.append(("reset_whistle_replan", None)) or dict(reset_whistle_replan_result),
    wait_for_lobby_after_item_use=lambda *args, **kwargs: recorder.append(("wait_items", None)) or True,
    enforce_operator_race_gate_before_execute=lambda *args, **kwargs: recorder.append(("race_gate", None)) or None,
    run_planner_race_preflight=lambda *args, **kwargs: recorder.append(("race_preflight", None)) or None,
    resolve_consecutive_race_warning=lambda *args, **kwargs: recorder.append(("resolve_warning", kwargs.get("turn_plan"))) or {"status": "completed", "reason": "warning_policy_prepared"},
    resolve_post_action_resolution=lambda *args, **kwargs: recorder.append(("resolve_post", None)) or True,
    trackblazer_action_failure_should_block_retry=lambda state_obj, action: recorder.append(("block_retry", action.func)) or False,
    update_operator_snapshot=lambda *args, **kwargs: recorder.append(("snapshot", kwargs.get("phase"))),
  )
  return hooks


def _runtime_hooks(recorder):
  return PlannerRuntimeHooks(
    attach_skill_purchase_plan=lambda state_obj, action, current_action_count, race_check=False: recorder.append(("attach_skill", action.func, race_check)) or "attached",
    attach_trackblazer_pre_action_item_plan=lambda state_obj, action: recorder.append(("attach_items", action.func)) or action,
    push_turn_retry_debug=lambda *args, **kwargs: recorder.append(("retry_debug", kwargs.get("result"))),
    update_operator_snapshot=lambda *args, **kwargs: recorder.append(("runtime_snapshot", kwargs.get("phase"))),
    should_retry_training_after_consecutive_warning=lambda action: False,
    prepare_training_fallback_after_consecutive_warning=lambda action: False,
  )


def _test_executor_check_only_preview():
  state_obj = _base_state()
  action = _training_action()
  recorder = []
  turn_plan = _turn_plan(
    "await_operator_review",
    "refresh_inventory_for_items",
    "replan_pre_action_items",
    "execute_pre_action_items",
  )

  result = run_planner_action_with_review(
    state_obj,
    action,
    turn_plan,
    0,
    "review",
    _executor_hooks(recorder, execute_intent="check_only"),
  )

  assert result.get("status") == "previewed"
  assert ("execute_items", "dry_run") in recorder
  assert not any(name == "resolve_post" for name, _ in recorder)


def _test_executor_reassess_transition():
  state_obj = _base_state()
  action = _training_action()
  recorder = []
  turn_plan = _turn_plan(
    "await_operator_review",
    "refresh_inventory_for_items",
    "replan_pre_action_items",
    "execute_pre_action_items",
    "await_lobby_after_items",
    "transition_after_pre_action_items",
  )

  result = run_planner_action_with_review(
    state_obj,
    action,
    turn_plan,
    0,
    "review",
    _executor_hooks(recorder, item_result={"status": "reassess", "reason": "item_reassess"}),
  )

  assert result.get("status") == "reassess"
  assert ("execute_items", "full") in recorder
  assert ("wait_items", None) in recorder


def _test_executor_reset_whistle_replans_in_place():
  state_obj = _base_state()
  action = _training_action()
  recorder = []
  resumed_turn_plan = _turn_plan("execute_main_action", "resolve_post_action")
  resumed_turn_plan.review_context = {
    "selected_action": {
      "func": "do_training",
      "training_name": "speed",
    }
  }
  resumed_turn_plan.item_plan = {
    "pre_action_items": [{"key": "motivating_megaphone", "name": "Motivating Megaphone"}],
  }
  turn_plan = _turn_plan(
    "await_operator_review",
    "refresh_inventory_for_items",
    "replan_pre_action_items",
    "execute_pre_action_items",
    "await_lobby_after_items",
    "transition_after_pre_action_items",
  )
  turn_plan.item_plan = {
    "execution_payload": {
      "reassess_transition": {
        "required": True,
        "transition_kind": "reset_whistle_reroll",
      }
    }
  }

  result = run_planner_action_with_review(
    state_obj,
    action,
    turn_plan,
    0,
    "review",
    _executor_hooks(
      recorder,
      item_result={"status": "reassess", "reason": "item_reassess"},
      reset_whistle_replan_result={
        "status": "replanned",
        "reason": "replanned_after_whistle",
        "turn_plan": resumed_turn_plan,
        "planned_clicks": [{"label": "Resume training"}],
        "selected_action": {"func": "do_training", "training_name": "speed"},
      },
    ),
  )

  assert result.get("status") == "replanned", result
  assert result.get("resume_context", {}).get("skip_review") is True, result
  assert ("reset_whistle_replan", None) in recorder, recorder


def _test_runtime_replanned_turn_resumes_without_review():
  state_obj = _base_state()
  action = _training_action()
  recorder = []
  resume_contexts = []
  initial_turn_plan = _turn_plan("await_operator_review", "transition_after_pre_action_items")
  resumed_turn_plan = _turn_plan("execute_main_action", "resolve_post_action")

  def _fake_executor(*args, **kwargs):
    resume_contexts.append(dict(kwargs.get("resume_context") or {}))
    if len(resume_contexts) == 1:
      return {
        "status": "replanned",
        "reason": "replanned_after_whistle",
        "committed": True,
        "turn_plan": resumed_turn_plan,
        "planned_clicks": [{"label": "Resume training"}],
        "resume_context": {
          "skip_review": True,
          "skip_skill_purchases": True,
          "skip_shop_purchases": True,
          "reason": "resume_after_reset_whistle_replan",
        },
      }
    return {"status": "executed", "reason": "turn_complete", "committed": True}

  with patch("core.trackblazer.runtime.set_turn_plan_decision_path", return_value=({}, initial_turn_plan)), patch(
    "core.trackblazer.runtime.run_planner_action_with_review",
    side_effect=_fake_executor,
  ):
    result = run_trackblazer_planner_turn(
      state_obj,
      action,
      0,
      "review",
      executor_hooks=_executor_hooks(recorder),
      runtime_hooks=_runtime_hooks(recorder),
    )

  assert result.get("status") == "executed", result
  assert resume_contexts[0] == {}, resume_contexts
  assert resume_contexts[1].get("skip_review") is True, resume_contexts


def _test_executor_skill_emergency_close_matches_legacy():
  state_obj = _base_state()
  action = _training_action()
  action.run = lambda: True
  recorder = []
  turn_plan = _turn_plan("await_operator_review", "execute_skill_purchases")

  hooks = _executor_hooks(recorder)
  hooks.run_skill_purchase_plan = lambda state_obj, action, current_action_count: {
    "status": "failed",
    "reason": "synthetic_skill_failure",
    "result": {
      "skill_purchase_flow": {
        "opened": True,
        "closed": False,
      },
    },
  }

  with patch("core.skill_scanner._close_skills_page", return_value={"closed": True}):
    result = run_planner_action_with_review(
      state_obj,
      action,
      turn_plan,
      0,
      "review",
      hooks,
    )

  assert result.get("status") == "executed", result
  assert ("resolve_post", None) in recorder


def _test_executor_item_failure_can_block_retry():
  state_obj = _base_state()
  action = _training_action()
  recorder = []
  turn_plan = _turn_plan(
    "await_operator_review",
    "refresh_inventory_for_items",
    "execute_pre_action_items",
  )
  action["trackblazer_pre_action_items"] = [{"key": "vita_20"}]
  state_obj["trackblazer_inventory_flow"] = {"reason": "inventory_did_not_close_after_confirm"}

  hooks = _executor_hooks(
    recorder,
    item_result={"status": "failed", "reason": "inventory_did_not_close_after_confirm"},
  )
  hooks.trackblazer_action_failure_should_block_retry = lambda state_obj, action: True

  result = run_planner_action_with_review(
    state_obj,
    action,
    turn_plan,
    0,
    "review",
    hooks,
  )

  assert result.get("status") == "blocked", result


def _test_executor_resolve_consecutive_warning_step_owned():
  state_obj = _base_state()
  action = _race_action()
  action.run = lambda: True
  recorder = []
  turn_plan = _turn_plan(
    "await_operator_review",
    "resolve_consecutive_race_warning",
    "execute_main_action",
    "resolve_post_action",
  )

  result = run_planner_action_with_review(
    state_obj,
    action,
    turn_plan,
    0,
    "review",
    _executor_hooks(recorder),
  )

  assert result.get("status") == "executed", result
  event_names = [entry[0] for entry in recorder]
  assert "resolve_warning" in event_names, event_names
  assert event_names.index("resolve_warning") < event_names.index("resolve_post"), event_names


def _test_runtime_retry_owned_by_planner_runtime():
  state_obj = _base_state()
  action = _race_action()
  action.available_actions = ["do_race", "do_rest"]
  recorder = []
  attempt_funcs = []

  def _fake_executor(*args, **kwargs):
    action_obj = args[1]
    attempt_funcs.append(action_obj.func)
    if len(attempt_funcs) == 1:
      return {"status": "failed", "reason": "synthetic_failure", "committed": False}
    return {"status": "executed", "reason": "turn_complete", "committed": True}

  fallback_policy = {
    "planner_owned": True,
    "chain": [
      {
        "trigger": "race_gate_blocked",
        "target_func": "do_training",
        "target_payload": {
          "func": "do_training",
          "training_name": "speed",
          "training_data": dict(action["training_data"]),
        },
        "source_node_id": "train:speed",
        "planner_ranked": True,
      },
    ],
  }

  with patch("core.trackblazer.runtime.set_turn_plan_decision_path", return_value=({}, _turn_plan("await_operator_review", fallback_policy=fallback_policy))), patch(
    "core.trackblazer.runtime.run_planner_action_with_review",
    side_effect=_fake_executor,
  ):
    result = run_trackblazer_planner_turn(
      state_obj,
      action,
      0,
      "review",
      executor_hooks=_executor_hooks(recorder),
      runtime_hooks=_runtime_hooks(recorder),
    )

  assert result.get("status") == "executed"
  assert attempt_funcs == ["do_race", "do_training"], attempt_funcs


def _test_runtime_empty_planner_retry_chain_falls_back_to_legacy():
  state_obj = _base_state()
  action = _race_action()
  action.available_actions = ["do_race", "do_training", "do_rest"]
  recorder = []

  with patch("core.trackblazer.runtime.set_turn_plan_decision_path", return_value=({}, _turn_plan("await_operator_review"))), patch(
    "core.trackblazer.runtime.run_planner_action_with_review",
    return_value={"status": "failed", "reason": "synthetic_failure", "committed": False},
  ):
    result = run_trackblazer_planner_turn(
      state_obj,
      action,
      0,
      "review",
      executor_hooks=_executor_hooks(recorder),
      runtime_hooks=_runtime_hooks(recorder),
    )

  assert result.get("status") == "fallback_to_legacy", result
  assert state_obj.get("_trackblazer_planner_force_fallback"), state_obj


def _test_runtime_safe_legacy_fallback():
  state_obj = _base_state()
  action = _race_action()
  recorder = []

  with patch("core.trackblazer.runtime.set_turn_plan_decision_path", side_effect=RuntimeError("synthetic setup failure")):
    result = run_trackblazer_planner_turn(
      state_obj,
      action,
      0,
      "review",
      executor_hooks=_executor_hooks(recorder),
      runtime_hooks=_runtime_hooks(recorder),
    )

  assert result.get("status") == "fallback_to_legacy"
  assert state_obj.get("_trackblazer_planner_force_fallback"), result


def _test_same_turn_fallback_handoff_control_flow():
  state_obj = _base_state()
  action = _race_action()
  planner_runtime_result = {
    "status": "fallback_to_legacy",
    "reason": "synthetic runtime fallback",
  }

  with patch("core.skeleton.update_operator_snapshot") as update_snapshot_mock, patch(
    "core.skeleton._push_turn_retry_debug"
  ) as retry_debug_mock:
    reason = skeleton._handoff_planner_runtime_fallback_to_legacy_same_turn(
      state_obj,
      action,
      planner_runtime_result,
    )

  assert reason == "synthetic runtime fallback"
  update_snapshot_mock.assert_called_once()
  retry_debug_mock.assert_called_once()
  retry_kwargs = retry_debug_mock.call_args.kwargs
  assert retry_kwargs.get("result") == "planner_runtime_same_turn_legacy_handoff", retry_kwargs
  assert retry_kwargs.get("same_turn_retry") is True, retry_kwargs


def _test_skeleton_delegates_planner_runtime():
  source = inspect.getsource(skeleton.career_lobby)
  assert "run_trackblazer_planner_turn(" in source
  assert "_handoff_planner_runtime_fallback_to_legacy_same_turn(" in source
  assert "planner_activation = {\"status\": \"fallback\"" in source
  assert 'result="planner_runtime_recollect"' not in source
  helper_source = inspect.getsource(skeleton._handoff_planner_runtime_fallback_to_legacy_same_turn)
  assert 'result="planner_runtime_same_turn_legacy_handoff"' in helper_source


def main():
  config.reload_config(print_config=False)
  constants.SCENARIO_NAME = "trackblazer"
  _test_executor_check_only_preview()
  _test_executor_reassess_transition()
  _test_executor_reset_whistle_replans_in_place()
  _test_executor_skill_emergency_close_matches_legacy()
  _test_executor_item_failure_can_block_retry()
  _test_executor_resolve_consecutive_warning_step_owned()
  _test_runtime_replanned_turn_resumes_without_review()
  _test_runtime_retry_owned_by_planner_runtime()
  _test_runtime_empty_planner_retry_chain_falls_back_to_legacy()
  _test_runtime_safe_legacy_fallback()
  _test_same_turn_fallback_handoff_control_flow()
  _test_skeleton_delegates_planner_runtime()
  print("trackblazer planner milestone 6 checks: ok")


if __name__ == "__main__":
  main()
