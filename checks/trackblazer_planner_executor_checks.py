import json

from core.actions import Action
from core.trackblazer.executor import PlannerExecutorHooks, run_planner_action_with_review
from core.trackblazer.models import ExecutionStep, TurnPlan


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


def main():
  results = {
    "whistle_replan": _run_whistle_replan_case(),
    "energy_recheck": _run_energy_recheck_case(),
  }
  print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
  main()
