import copy
from unittest.mock import patch

import core.bot as bot
import core.config as config
import utils.constants as constants
from core.actions import Action
from core.trackblazer.executor import PlannerExecutorHooks
from core.trackblazer.models import ExecutionStep, PlannerFreshness, TurnPlan
from core.trackblazer.planner import (
  PLANNER_STATE_KEY,
  build_turn_plan_execution_action,
)
from core.trackblazer.runtime import PlannerRuntimeHooks, run_trackblazer_planner_turn
import core.skeleton as skeleton


def _base_state():
  return {
    "year": "Senior Year Early Jul",
    "turn": 12,
    "energy_level": 55,
    "max_energy": 100,
    "current_mood": "GOOD",
    "current_stats": {"spd": 800, "sta": 620, "pwr": 700, "guts": 310, "wit": 540, "sp": 240},
    "date_event_available": True,
    "trackblazer_inventory_summary": {"held_quantities": {}, "items_detected": [], "actionable_items": [], "by_category": {}},
    "trackblazer_inventory_flow": {},
    "trackblazer_shop_summary": {"shop_coins": 0, "items_detected": [], "purchasable_items": []},
    "trackblazer_shop_flow": {},
    "skill_purchase_plan": {},
  }


def _race_action():
  action = Action()
  action.func = "do_race"
  action.available_actions = ["do_race", "do_training", "do_rest"]
  action["race_name"] = "any"
  action["prefer_rival_race"] = True
  action["training_name"] = "speed"
  action["training_data"] = {
    "name": "speed",
    "score_tuple": (42.0, 0),
    "weighted_stat_score": 42.0,
    "stat_gains": {"speed": 20, "power": 8, "sp": 10},
    "failure": 0,
    "total_supports": 4,
    "total_rainbow_friends": 1,
  }
  return action


def _turn_plan_for_payload(selected_payload, *, fallback_policy=None, race_scout=None):
  selected_payload = dict(selected_payload or {})
  return TurnPlan(
    version=3,
    decision_path="planner",
    freshness=PlannerFreshness(turn_key="Senior Year Early Jul|12"),
    warning_plan=copy.deepcopy(selected_payload.get("planner_race_warning_policy") or {}),
    fallback_policy=dict(fallback_policy or {}),
    race_plan={
      "planner_owned": True,
      "branch_kind": "ranked_selection",
      "selected_action": copy.deepcopy(selected_payload),
      "action_payload": {
        "planner_owned": True,
        "func": selected_payload.get("func"),
        "options": copy.deepcopy(selected_payload),
        "available_actions": [selected_payload.get("func")] if selected_payload.get("func") else [],
      },
      "warning_plan": copy.deepcopy(selected_payload.get("planner_race_warning_policy") or {}),
      "race_scout": copy.deepcopy(race_scout or {}),
    },
    step_sequence=[
      ExecutionStep(step_id="await_operator_review", step_type="await_operator_review", intent="review_current_turn"),
    ],
  )


def _executor_hooks():
  return PlannerExecutorHooks(
    skill_purchase_plan=lambda action: {},
    review_action_before_execution=lambda *args, **kwargs: True,
    wait_for_execute_intent=lambda *args, **kwargs: "execute",
    run_skill_purchase_plan=lambda state_obj, action, current_action_count: {"status": "skipped"},
    run_trackblazer_shop_purchases=lambda state_obj, action: {"status": "skipped"},
    wait_for_lobby_after_shop_purchase=lambda: True,
    refresh_trackblazer_pre_action_inventory=lambda state_obj, action: {"status": "skipped"},
    execute_trackblazer_pre_action_items=lambda state_obj, action, commit_mode="full": {"status": "skipped"},
    wait_for_lobby_after_item_use=lambda *args, **kwargs: True,
    enforce_operator_race_gate_before_execute=lambda *args, **kwargs: None,
    run_planner_race_preflight=lambda *args, **kwargs: None,
    resolve_consecutive_race_warning=lambda *args, **kwargs: {"status": "completed", "reason": "warning_policy_ready"},
    resolve_post_action_resolution=lambda state_obj, action: True,
    trackblazer_action_failure_should_block_retry=lambda state_obj, action: False,
    update_operator_snapshot=lambda *args, **kwargs: None,
  )


def _runtime_hooks():
  return PlannerRuntimeHooks(
    attach_skill_purchase_plan=lambda state_obj, action, current_action_count, race_check=False: "attached",
    attach_trackblazer_pre_action_item_plan=lambda state_obj, action: action,
    push_turn_retry_debug=lambda *args, **kwargs: None,
    update_operator_snapshot=lambda *args, **kwargs: None,
    should_retry_training_after_consecutive_warning=lambda action: False,
    prepare_training_fallback_after_consecutive_warning=lambda action: False,
  )


def _test_turn_plan_builds_execution_action_without_mutating_caller():
  caller_action = _race_action()
  turn_plan = _turn_plan_for_payload(
    {
      "func": "do_training",
      "training_name": "speed",
      "training_function": "stat_weight_training",
      "training_data": copy.deepcopy(caller_action["training_data"]),
    }
  )

  execution_action = build_turn_plan_execution_action(caller_action, turn_plan)

  assert caller_action.func == "do_race", caller_action
  assert execution_action.func == "do_training", execution_action
  assert execution_action.get("training_name") == "speed", execution_action
  execution_payload = turn_plan.to_execution_payload()
  assert execution_payload.get("selected_action", {}).get("func") == "do_training", execution_payload


def _test_runtime_retry_preserves_caller_action():
  state_obj = _base_state()
  caller_action = _race_action()
  attempt_funcs = []

  def _fake_executor(*args, **kwargs):
    action_obj = args[1]
    attempt_funcs.append(action_obj.func)
    if len(attempt_funcs) == 1:
      return {"status": "failed", "reason": "synthetic_failure", "committed": False}
    return {"status": "executed", "reason": "turn_complete", "committed": True}

  retry_plan = _turn_plan_for_payload(
    {
      "func": "do_training",
      "training_name": "speed",
      "training_function": "stat_weight_training",
      "training_data": copy.deepcopy(caller_action["training_data"]),
    },
    fallback_policy={
      "planner_owned": True,
      "chain": [
        {
          "trigger": "race_gate_blocked",
          "target_func": "do_training",
          "target_payload": {
            "func": "do_training",
            "training_name": "speed",
            "training_function": "stat_weight_training",
            "training_data": copy.deepcopy(caller_action["training_data"]),
          },
          "source_node_id": "train:speed",
          "planner_ranked": True,
        },
      ],
    },
  )

  with patch("core.trackblazer.runtime.set_turn_plan_decision_path", return_value=({}, retry_plan)), patch(
    "core.trackblazer.runtime.run_planner_action_with_review",
    side_effect=_fake_executor,
  ):
    result = run_trackblazer_planner_turn(
      state_obj,
      caller_action,
      0,
      "review",
      executor_hooks=_executor_hooks(),
      runtime_hooks=_runtime_hooks(),
    )

  assert result.get("status") == "executed", result
  assert attempt_funcs == ["do_training", "do_training"], attempt_funcs
  assert caller_action.func == "do_race", caller_action
  assert caller_action.get("race_name") == "any", caller_action


def _test_planner_race_preflight_uses_planner_payload_not_legacy_fallback():
  bot.set_trackblazer_use_new_planner_enabled(True)
  state_obj = _base_state()
  action = _race_action()
  state_obj[PLANNER_STATE_KEY] = {
    "turn_plan": _turn_plan_for_payload(
      {
        "func": "do_race",
        "race_name": "any",
        "prefer_rival_race": True,
      },
      fallback_policy={
        "planner_owned": True,
        "chain": [
          {
            "trigger": "rival_scout_failed",
            "target_func": "do_training",
            "target_payload": {
              "func": "do_training",
              "training_name": "speed",
              "training_function": "stat_weight_training",
              "training_data": copy.deepcopy(action["training_data"]),
            },
          },
        ],
      },
      race_scout={"required": True, "failure_transition": "revert_to_fallback_action"},
    ).to_snapshot(),
  }

  with patch("scenarios.trackblazer.scout_rival_race", return_value={"rival_found": False}), patch(
    "core.skeleton.update_operator_snapshot",
    lambda *args, **kwargs: None,
  ), patch(
    "core.skeleton._apply_legacy_rival_fallback_payload",
    side_effect=AssertionError("legacy fallback applier should not run on planner path"),
  ):
    result = skeleton._run_planner_race_preflight(state_obj, action)

  assert result is None, result
  assert action.func == "do_training", action
  assert action.get("training_name") == "speed", action


def _test_operator_gate_revert_uses_planner_payload_not_legacy_fallback():
  action = _race_action()
  action["trackblazer_planner_race"] = {
    "branch_kind": "ranked_selection",
    "fallback_action": {
      "func": "do_training",
      "training_name": "speed",
      "training_function": "stat_weight_training",
      "training_data": copy.deepcopy(action["training_data"]),
    },
  }
  action["planner_race_warning_policy"] = {"warning_expected": True}
  action["planner_warning_outcome"] = {"cancelled": False, "resolved": True}

  with patch(
    "core.skeleton._apply_legacy_rival_fallback_payload",
    side_effect=AssertionError("legacy fallback applier should not run on planner-owned gate revert"),
  ):
    reverted = skeleton._revert_optional_race_to_fallback(action)

  assert reverted is True, reverted
  assert action.func == "do_training", action
  assert action.get("training_name") == "speed", action


def _test_consecutive_warning_training_retry_uses_planner_payload_not_legacy_fallback():
  action = _race_action()
  action["trackblazer_planner_race"] = {
    "branch_kind": "ranked_selection",
    "fallback_action": {
      "func": "do_training",
      "training_name": "speed",
      "training_function": "stat_weight_training",
      "training_data": copy.deepcopy(action["training_data"]),
    },
  }
  action["planner_race_warning_policy"] = {
    "warning_expected": True,
    "cancel_target": "do_training",
  }
  action["planner_warning_outcome"] = {
    "cancelled": True,
    "force_rest": False,
    "reason": "optional_rival_promoted_from_rest",
  }

  with patch(
    "core.skeleton._apply_legacy_rival_fallback_payload",
    side_effect=AssertionError("legacy fallback applier should not run on planner-owned warning retry"),
  ):
    prepared = skeleton._prepare_training_fallback_after_consecutive_warning(action)

  assert prepared is True, prepared
  assert action.func == "do_training", action
  assert action.get("training_name") == "speed", action
  assert (action.get("trackblazer_planner_race") or {}).get("fallback_action", {}).get("func") == "do_training", action
  assert "planner_warning_outcome" not in action.options, action


def _test_same_turn_handoff_is_the_legacy_hydration_point():
  state_obj = _base_state()
  action = _race_action()
  state_obj[PLANNER_STATE_KEY] = {
    "turn_plan": _turn_plan_for_payload(
      {
        "func": "do_training",
        "training_name": "speed",
        "training_function": "stat_weight_training",
        "training_data": copy.deepcopy(action["training_data"]),
      }
    ).to_snapshot(),
  }

  with patch("core.skeleton.update_operator_snapshot") as update_snapshot_mock, patch(
    "core.skeleton._push_turn_retry_debug"
  ) as retry_debug_mock:
    reason = skeleton._handoff_planner_runtime_fallback_to_legacy_same_turn(
      state_obj,
      action,
      {"status": "fallback_to_legacy", "reason": "synthetic runtime fallback"},
    )

  assert reason == "synthetic runtime fallback", reason
  assert action.func == "do_training", action
  assert action.get("training_name") == "speed", action
  update_snapshot_mock.assert_called_once()
  retry_debug_mock.assert_called_once()


def main():
  config.reload_config(print_config=False)
  constants.SCENARIO_NAME = "trackblazer"
  _test_turn_plan_builds_execution_action_without_mutating_caller()
  _test_runtime_retry_preserves_caller_action()
  _test_planner_race_preflight_uses_planner_payload_not_legacy_fallback()
  _test_operator_gate_revert_uses_planner_payload_not_legacy_fallback()
  _test_consecutive_warning_training_retry_uses_planner_payload_not_legacy_fallback()
  _test_same_turn_handoff_is_the_legacy_hydration_point()
  print("trackblazer planner milestone 8 checks: ok")


if __name__ == "__main__":
  main()
