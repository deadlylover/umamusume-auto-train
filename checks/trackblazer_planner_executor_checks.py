import copy
import json
from unittest.mock import patch

from core.actions import Action
import core.skeleton as skeleton
from core.trackblazer.executor import PlannerExecutorHooks, run_planner_action_with_review
from core.trackblazer.models import ExecutionStep, TurnPlan
import utils.constants as constants


def _make_action(func_name="do_rest"):
  action = Action()
  action.func = func_name
  action.available_actions = [func_name]
  return action


def _make_reassess_turn_plan(transition_kind):
  return TurnPlan(
    decision_path="planner",
    item_plan={
      "execution_payload": {
        "reassess_transition": {
          "required": True,
          "transition_kind": transition_kind,
          "reason": f"{transition_kind}_required",
          "trigger_items": ["reset_whistle"] if transition_kind == "reset_whistle_reroll" else ["vita_20"],
        },
        "action_mutations": {
          "trackblazer_pre_action_items": [
            {"key": "reset_whistle" if transition_kind == "reset_whistle_reroll" else "vita_20"}
          ],
          "trackblazer_reassess_after_item_use": True,
        },
      }
    },
    step_sequence=[
      ExecutionStep(
        step_id="refresh_inventory_for_items",
        step_type="refresh_inventory_for_items",
      ),
      ExecutionStep(
        step_id="execute_pre_action_items",
        step_type="execute_pre_action_items",
      ),
      ExecutionStep(
        step_id="await_lobby_after_items",
        step_type="await_lobby_after_items",
      ),
      ExecutionStep(
        step_id="transition_after_pre_action_items",
        step_type="transition_reassess_after_items",
      ),
    ],
  )


def _base_hooks(
  *,
  item_execute_result,
  run_post_energy_item_followup,
  run_post_reset_whistle_replan,
  state_log,
):
  def _unexpected(name):
    def _raiser(*_args, **_kwargs):
      raise AssertionError(f"unexpected hook call: {name}")
    return _raiser

  return PlannerExecutorHooks(
    skill_purchase_plan=lambda _action: {},
    review_action_before_execution=_unexpected("review_action_before_execution"),
    wait_for_execute_intent=_unexpected("wait_for_execute_intent"),
    run_skill_purchase_plan=_unexpected("run_skill_purchase_plan"),
    run_trackblazer_shop_purchases=_unexpected("run_trackblazer_shop_purchases"),
    wait_for_lobby_after_shop_purchase=lambda: True,
    refresh_trackblazer_pre_action_inventory=lambda _state_obj, _action: {"status": "ready"},
    execute_trackblazer_pre_action_items=lambda _state_obj, _action, commit_mode="full": dict(item_execute_result),
    recheck_selected_training_after_item_use=_unexpected("recheck_selected_training_after_item_use"),
    run_post_energy_item_followup=run_post_energy_item_followup,
    run_post_reset_whistle_replan=run_post_reset_whistle_replan,
    wait_for_lobby_after_item_use=lambda _state_obj, _action, **_kwargs: True,
    enforce_operator_race_gate_before_execute=_unexpected("enforce_operator_race_gate_before_execute"),
    run_planner_race_preflight=_unexpected("run_planner_race_preflight"),
    resolve_post_action_resolution=lambda _state_obj, _action: True,
    trackblazer_action_failure_should_block_retry=lambda _state_obj, _action: False,
    update_operator_snapshot=lambda *_args, **_kwargs: state_log.append(
      {
        "phase": _kwargs.get("phase"),
        "message": _kwargs.get("message"),
        "error_text": _kwargs.get("error_text"),
      }
    ),
  )


def _run_whistle_replan_case():
  state_obj = {}
  action = _make_action("do_rest")
  turn_plan = _make_reassess_turn_plan("reset_whistle_reroll")
  counters = {
    "run_post_reset_whistle_replan": 0,
    "action_run": 0,
  }
  state_log = []

  resumed_turn_plan = TurnPlan(
    decision_path="planner",
    race_plan={
      "action_payload": {
        "func": "do_training",
        "options": {
          "training_name": "spd",
          "training_function": "stat_weight_training",
        },
        "available_actions": ["do_training"],
      },
      "selected_action": {
        "func": "do_training",
        "training_name": "spd",
      },
    },
  )

  def _run():
    counters["action_run"] += 1
    return True

  action.run = _run

  hooks = _base_hooks(
    item_execute_result={
      "status": "reassess",
      "reason": "reset_whistle_requires_replan",
    },
    run_post_energy_item_followup=lambda *_args, **_kwargs: {
      "status": "ready",
      "reason": "not_used",
    },
    run_post_reset_whistle_replan=lambda *_args, **_kwargs: (
      counters.__setitem__("run_post_reset_whistle_replan", counters["run_post_reset_whistle_replan"] + 1) or {
        "status": "replanned",
        "reason": "reset_whistle_replanned",
        "turn_plan": resumed_turn_plan,
        "planned_clicks": [],
        "selected_action": {"func": "do_training", "training_name": "spd"},
      }
    ),
    state_log=state_log,
  )

  outcome = run_planner_action_with_review(
    state_obj,
    action,
    turn_plan,
    1,
    "Review proposed action before execution.",
    hooks,
    resume_context={
      "skip_review": True,
      "skip_skill_purchases": True,
      "skip_shop_purchases": True,
      "reason": "test_resume",
    },
  )

  assert outcome.get("status") == "replanned", outcome
  assert counters["run_post_reset_whistle_replan"] == 1, counters
  assert counters["action_run"] == 0, counters
  return {
    "status": outcome.get("status"),
    "selected_action": (outcome.get("turn_plan") or resumed_turn_plan).race_plan.get("selected_action"),
    "counters": counters,
  }


def _run_energy_recheck_case():
  state_obj = {}
  action = _make_action("do_training")
  action["training_name"] = "sta"
  action["training_function"] = "stat_weight_training"
  turn_plan = _make_reassess_turn_plan("energy_item_reassess")
  counters = {
    "run_post_energy_item_followup": 0,
    "action_run": 0,
  }
  state_log = []

  def _run():
    counters["action_run"] += 1
    return True

  action.run = _run

  hooks = _base_hooks(
    item_execute_result={
      "status": "reassess",
      "reason": "energy_item_requires_recheck",
    },
    run_post_energy_item_followup=lambda *_args, **_kwargs: (
      counters.__setitem__("run_post_energy_item_followup", counters["run_post_energy_item_followup"] + 1) or {
        "status": "blocked",
        "reason": "energy_followup_blocked",
      }
    ),
    run_post_reset_whistle_replan=lambda *_args, **_kwargs: {
      "status": "blocked",
      "reason": "not_used",
    },
    state_log=state_log,
  )

  outcome = run_planner_action_with_review(
    state_obj,
    action,
    turn_plan,
    1,
    "Review proposed action before execution.",
    hooks,
    resume_context={
      "skip_review": True,
      "skip_skill_purchases": True,
      "skip_shop_purchases": True,
      "reason": "test_resume",
    },
  )

  assert outcome.get("status") == "blocked", outcome
  assert outcome.get("reason") == "energy_followup_blocked", outcome
  assert counters["run_post_energy_item_followup"] == 1, counters
  assert counters["action_run"] == 0, counters
  return {
    "status": outcome.get("status"),
    "reason": outcome.get("reason"),
    "counters": counters,
  }


def _run_refresh_sync_case():
  state_obj = {}
  action = _make_action("do_training")
  action["training_name"] = "pwr"
  action["trackblazer_pre_action_items"] = [
    {"key": "royal_kale_juice", "name": "Royal Kale Juice"}
  ]
  action["_trackblazer_planner_item_execution_override"] = {
    "execution_items": [
      {"key": "royal_kale_juice", "name": "Royal Kale Juice"}
    ],
    "reassess_transition": {
      "required": True,
      "transition_kind": "energy_rescue_reassess",
      "reason": "stale_energy_item_reassess",
      "trigger_items": ["royal_kale_juice"],
    },
  }
  stale_turn_plan = TurnPlan(
    decision_path="planner",
    item_plan={
      "pre_action_items": [
        {"key": "royal_kale_juice", "name": "Royal Kale Juice"}
      ],
      "execution_payload": {
        "execution_items": [
          {"key": "royal_kale_juice", "name": "Royal Kale Juice"}
        ],
        "reassess_transition": {
          "required": True,
          "transition_kind": "energy_rescue_reassess",
          "reason": "stale_energy_item_reassess",
          "trigger_items": ["royal_kale_juice"],
        },
        "action_mutations": {
          "trackblazer_pre_action_items": [
            {"key": "royal_kale_juice", "name": "Royal Kale Juice"}
          ],
          "trackblazer_reassess_after_item_use": True,
        },
      },
      "reassess_after_item_use": True,
    },
    review_context={
      "selected_action": {
        "func": "do_training",
        "training_name": "pwr",
        "pre_action_item_use": [
          {"key": "royal_kale_juice", "name": "Royal Kale Juice"}
        ],
        "reassess_after_item_use": True,
      }
    },
    step_sequence=[
      ExecutionStep(step_id="refresh_inventory_for_items", step_type="refresh_inventory_for_items"),
      ExecutionStep(step_id="replan_pre_action_items", step_type="replan_pre_action_items"),
      ExecutionStep(step_id="execute_pre_action_items", step_type="execute_pre_action_items"),
    ],
  )
  refreshed_items = [
    {"key": "vita_20", "name": "Vita 20"},
    {"key": "vita_20", "name": "Vita 20"},
  ]
  refreshed_turn_plan = TurnPlan(
    decision_path="planner",
    item_plan={
      "pre_action_items": copy.deepcopy(refreshed_items),
      "execution_payload": {
        "execution_items": copy.deepcopy(refreshed_items),
        "reassess_transition": {
          "required": True,
          "transition_kind": "energy_rescue_reassess",
          "reason": "refreshed_energy_item_reassess",
          "trigger_items": ["vita_20", "vita_20"],
        },
        "action_mutations": {
          "trackblazer_pre_action_items": copy.deepcopy(refreshed_items),
          "trackblazer_reassess_after_item_use": True,
        },
      },
      "reassess_after_item_use": True,
    },
    review_context={
      "selected_action": {
        "func": "do_training",
        "training_name": "pwr",
        "pre_action_item_use": copy.deepcopy(refreshed_items),
        "reassess_after_item_use": True,
      }
    },
    step_sequence=[
      ExecutionStep(step_id="refresh_inventory_for_items", step_type="refresh_inventory_for_items"),
      ExecutionStep(step_id="replan_pre_action_items", step_type="replan_pre_action_items"),
      ExecutionStep(step_id="execute_pre_action_items", step_type="execute_pre_action_items"),
    ],
  )
  captured = {}
  state_log = []

  def _capture_item_execute(_state_obj, current_action, commit_mode="full"):
    captured["commit_mode"] = commit_mode
    captured["pre_action_items"] = copy.deepcopy(current_action.get("trackblazer_pre_action_items") or [])
    captured["override"] = copy.deepcopy(current_action.get("_trackblazer_planner_item_execution_override") or {})
    return {
      "status": "failed",
      "reason": "captured_refreshed_items",
    }

  hooks = PlannerExecutorHooks(
    skill_purchase_plan=lambda _action: {},
    review_action_before_execution=lambda *_args, **_kwargs: True,
    wait_for_execute_intent=lambda *_args, **_kwargs: "execute",
    run_skill_purchase_plan=lambda *_args, **_kwargs: {"status": "skipped"},
    run_trackblazer_shop_purchases=lambda *_args, **_kwargs: {"status": "skipped"},
    wait_for_lobby_after_shop_purchase=lambda: True,
    refresh_trackblazer_pre_action_inventory=lambda _state_obj, _action: {
      "status": "ready",
      "turn_plan": refreshed_turn_plan,
    },
    execute_trackblazer_pre_action_items=_capture_item_execute,
    recheck_selected_training_after_item_use=lambda *_args, **_kwargs: {"status": "ready"},
    run_post_energy_item_followup=lambda *_args, **_kwargs: {"status": "ready"},
    run_post_reset_whistle_replan=lambda *_args, **_kwargs: {"status": "blocked", "reason": "not_used"},
    wait_for_lobby_after_item_use=lambda *_args, **_kwargs: True,
    enforce_operator_race_gate_before_execute=lambda *_args, **_kwargs: None,
    run_planner_race_preflight=lambda *_args, **_kwargs: None,
    resolve_post_action_resolution=lambda *_args, **_kwargs: True,
    trackblazer_action_failure_should_block_retry=lambda *_args, **_kwargs: False,
    update_operator_snapshot=lambda *_args, **_kwargs: state_log.append(
      {
        "phase": _kwargs.get("phase"),
        "message": _kwargs.get("message"),
      }
    ),
  )

  outcome = run_planner_action_with_review(
    state_obj,
    action,
    stale_turn_plan,
    1,
    "Review proposed action before execution.",
    hooks,
    resume_context={
      "skip_review": True,
      "skip_skill_purchases": True,
      "skip_shop_purchases": True,
      "reason": "test_refresh_sync",
    },
  )

  refreshed_keys = [entry.get("key") for entry in captured.get("pre_action_items") or []]
  override_keys = [
    entry.get("key")
    for entry in list((captured.get("override") or {}).get("execution_items") or [])
  ]
  assert outcome.get("status") == "failed", outcome
  assert outcome.get("reason") == "captured_refreshed_items", outcome
  assert refreshed_keys == ["vita_20", "vita_20"], captured
  assert override_keys == ["vita_20", "vita_20"], captured
  return {
    "status": outcome.get("status"),
    "captured_items": refreshed_keys,
    "captured_override_items": override_keys,
  }


def _run_whistle_reseed_case():
  state_obj = {
    "year": "Senior Year Late Jul",
    "turn": 11,
    "training_results": {
      "sta": {
        "failure": 9,
        "stat_gains": {"sta": 26, "guts": 11},
        "total_supports": 3,
        "total_friendship_levels": {"gray": 0, "blue": 0, "green": 0, "yellow": 0, "max": 0},
        "total_rainbow_friends": 0,
      }
    },
  }
  action = _make_action("do_training")
  action["training_name"] = "wit"
  action["training_data"] = {
    "score_tuple": (29.0, 0),
    "failure": 0,
    "stat_gains": {"wit": 20},
    "total_supports": 3,
  }
  action["available_trainings"] = {
    "wit": {
      "score_tuple": (29.0, 0),
      "failure": 0,
      "stat_gains": {"wit": 20},
      "total_supports": 3,
    }
  }
  action["planner_race_warning_policy"] = {"accept_warning": False}
  action["trackblazer_planner_race"] = {"branch_kind": "optional_rival_race"}
  action["rival_scout"] = {"executed": True, "rival_found": True}

  captured = {}

  def _fake_collect_training_state(state, training_function_name):
    captured["collect_training_function"] = training_function_name
    state["training_results"] = copy.deepcopy(state_obj["training_results"])
    return state

  def _fake_stat_weight_training(state, training_template, seed_action):
    captured["training_template_function"] = training_template.get("training_function")
    seed_action.func = "do_training"
    seed_action["training_name"] = "sta"
    seed_action["training_data"] = {
      "score_tuple": (43.0, 0),
      "failure": 9,
      "failure_bypassed_by_items": True,
      "trackblazer_failure_bypass_items": ["good_luck_charm", "vita_20"],
      "stat_gains": {"sta": 26, "guts": 11},
      "total_supports": 3,
    }
    seed_action["available_trainings"] = {
      "sta": copy.deepcopy(seed_action["training_data"]),
    }
    return seed_action

  def _fake_set_turn_plan_decision_path(state, replanning_action, decision_path, **_kwargs):
    captured["decision_path"] = decision_path
    captured["action_func"] = replanning_action.func
    captured["training_name"] = replanning_action.get("training_name")
    captured["training_data"] = copy.deepcopy(replanning_action.get("training_data") or {})
    captured["available_trainings"] = copy.deepcopy(replanning_action.get("available_trainings") or {})
    captured["planner_race_warning_policy"] = copy.deepcopy(replanning_action.get("planner_race_warning_policy") or {})
    captured["trackblazer_planner_race"] = copy.deepcopy(replanning_action.get("trackblazer_planner_race") or {})
    captured["rival_scout"] = copy.deepcopy(replanning_action.get("rival_scout") or {})
    turn_plan = TurnPlan(
      decision_path="planner",
      review_context={
        "selected_action": {
          "func": "do_training",
          "training_name": "sta",
        }
      },
      item_plan={"pre_action_items": []},
      race_plan={
        "selected_action": {
          "func": "do_training",
          "training_name": "sta",
        }
      },
    )
    return {}, turn_plan

  with patch.object(constants, "SCENARIO_NAME", "trackblazer"), patch(
    "core.skeleton.collect_training_state",
    side_effect=_fake_collect_training_state,
  ), patch(
    "core.skeleton.flush_gpu_cache",
    lambda: None,
  ), patch(
    "core.skeleton.update_operator_snapshot",
    lambda *_args, **_kwargs: None,
  ), patch(
    "core.skeleton.Strategy",
  ), patch(
    "core.trainings.stat_weight_training",
    side_effect=_fake_stat_weight_training,
  ), patch(
    "core.skeleton.set_turn_plan_decision_path",
    side_effect=_fake_set_turn_plan_decision_path,
  ) as strategy_cls:
    strategy_cls.return_value.get_training_template.return_value = {
      "training_function": "stat_weight_training",
      "stat_weight_set": {},
      "risk_taking_set": {},
    }
    result = skeleton._run_post_reset_whistle_replan(state_obj, action)

  assert result.get("status") == "replanned", result
  assert captured.get("decision_path") == "planner", captured
  assert captured.get("action_func") == "do_training", captured
  assert captured.get("training_name") == "sta", captured
  assert "wit" not in (captured.get("available_trainings") or {}), captured
  assert captured.get("training_data", {}).get("failure_bypassed_by_items") is True, captured
  assert captured.get("training_data", {}).get("trackblazer_failure_bypass_items") == ["good_luck_charm", "vita_20"], captured
  assert captured.get("planner_race_warning_policy") == {}, captured
  assert captured.get("trackblazer_planner_race") == {}, captured
  assert captured.get("rival_scout") == {}, captured
  return {
    "status": result.get("status"),
    "training_name": captured.get("training_name"),
    "failure_bypass_items": captured.get("training_data", {}).get("trackblazer_failure_bypass_items"),
  }


def main():
  results = {
    "whistle_replan": _run_whistle_replan_case(),
    "energy_recheck": _run_energy_recheck_case(),
    "refresh_sync": _run_refresh_sync_case(),
    "whistle_reseed": _run_whistle_reseed_case(),
  }
  print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
  main()
