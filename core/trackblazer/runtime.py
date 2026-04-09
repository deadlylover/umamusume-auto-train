from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict

import core.config as config
import utils.constants as constants
from core.race_selector import get_race_gate_for_turn_label
from core.trackblazer.executor import PlannerExecutorHooks, run_planner_action_with_review
from core.trackblazer.compat import set_rival_fallback_action
from core.trackblazer.planner import (
  RUNTIME_PATH_PLANNER_FALLBACK_LEGACY,
  RUNTIME_PATH_PLANNER_RUNTIME,
  append_planner_runtime_transition,
  get_turn_plan,
  mark_planner_fallback,
  set_trackblazer_runtime_path,
  set_turn_plan_decision_path,
)
from utils.log import info, warning


@dataclass
class PlannerRuntimeHooks:
  attach_skill_purchase_plan: Callable[[Dict[str, Any], Any, int, bool], str]
  attach_trackblazer_pre_action_item_plan: Callable[[Dict[str, Any], Any], Any]
  push_turn_retry_debug: Callable[..., Any]
  update_operator_snapshot: Callable[..., Any]
  should_retry_training_after_consecutive_warning: Callable[[Any], bool]
  prepare_training_fallback_after_consecutive_warning: Callable[[Any], bool]


def _transition(state_obj, step_id, step_type, status, note="", details=None):
  append_planner_runtime_transition(
    state_obj,
    step_id=step_id,
    step_type=step_type,
    status=status,
    note=note,
    details=details or {},
  )


def _set_disable_skip_turn_fallback(action, enabled):
  if not hasattr(action, "options"):
    return
  if enabled:
    action["disable_skip_turn_fallback"] = True
  else:
    action.options.pop("disable_skip_turn_fallback", None)


def _selected_race_still_pending(state_obj, action):
  fallback_gate = get_race_gate_for_turn_label(
    state_obj.get("year"),
    getattr(config, "OPERATOR_RACE_SELECTOR", None),
  ) if (constants.SCENARIO_NAME or "default") in ("mant", "trackblazer") else {}
  has_selected_race = bool(
    (action.get("trackblazer_race_decision") or {}).get("should_race")
    or action.get("scheduled_race")
    or action.get("is_race_day")
    or action.get("trackblazer_lobby_scheduled_race")
    or (fallback_gate.get("race_allowed") and fallback_gate.get("selected_race"))
  )
  return has_selected_race


def _prepare_retry_order(action):
  available_actions = list(getattr(action, "available_actions", []) or [])
  if available_actions:
    available_actions.pop(0)
  consecutive_warning_retry_training = False
  if bool(action.get("_consecutive_warning_force_rest")):
    info(
      "[FALLBACK] Consecutive-race warning blocked optional weak-training race. "
      "Prioritizing rest fallback."
    )
    set_rival_fallback_action(action, func="do_rest")
    if "do_rest" in available_actions:
      available_actions = ["do_rest"] + [name for name in available_actions if name != "do_rest"]
    else:
      available_actions.insert(0, "do_rest")
  return available_actions, consecutive_warning_retry_training


def _prepare_retry_candidate(state_obj, action, function_name, hooks: PlannerRuntimeHooks):
  action.func = function_name
  _set_disable_skip_turn_fallback(action, enabled=(function_name == "do_rest"))
  if (constants.SCENARIO_NAME or "default") in ("mant", "trackblazer"):
    hooks.attach_trackblazer_pre_action_item_plan(state_obj, action)
  return action


def _attach_skill_plan_for_attempt(state_obj, action, current_action_count, hooks: PlannerRuntimeHooks):
  return hooks.attach_skill_purchase_plan(
    state_obj,
    action,
    current_action_count,
    race_check=bool(action.func == "do_race"),
  )


def _refresh_planner_turn(state_obj, action):
  planner_state, turn_plan = set_turn_plan_decision_path(state_obj, action, "planner")
  return planner_state, turn_plan


def _planner_runtime_fallback_to_legacy(state_obj, action, reason):
  warning(f"[TB_PLANNER] Falling back to legacy for this turn: {reason}")
  mark_planner_fallback(state_obj, reason)
  try:
    set_turn_plan_decision_path(state_obj, action, "planner→legacy (fallback)", reason=reason)
  except Exception:
    pass
  set_trackblazer_runtime_path(
    state_obj,
    RUNTIME_PATH_PLANNER_FALLBACK_LEGACY,
    reason=reason,
    source="planner_runtime_fallback",
  )
  _transition(state_obj, "planner_runtime", "planner_runtime", "fallback_to_legacy", reason)
  return {"status": "fallback_to_legacy", "reason": reason}


def run_trackblazer_planner_turn(
  state_obj,
  action,
  action_count,
  review_message,
  *,
  executor_hooks: PlannerExecutorHooks,
  runtime_hooks: PlannerRuntimeHooks,
  sub_phase=None,
  ocr_debug=None,
  planned_clicks=None,
):
  attempted_planner_execution = False

  try:
    _transition(state_obj, "planner_runtime", "planner_runtime", "started", "planner_turn_attempt")
    set_trackblazer_runtime_path(
      state_obj,
      RUNTIME_PATH_PLANNER_RUNTIME,
      source="planner_runtime",
    )
    skill_result = _attach_skill_plan_for_attempt(state_obj, action, action_count, runtime_hooks)
    if skill_result == "failed":
      _transition(state_obj, "planner_runtime", "planner_runtime", "failed", "skill_plan_attach_failed")
      return {"status": "failed", "reason": "skill_plan_attach_failed"}
    _, turn_plan = _refresh_planner_turn(state_obj, action)
  except Exception as exc:
    return _planner_runtime_fallback_to_legacy(state_obj, action, f"planner_runtime_setup_failed: {exc}")

  attempt_action_names = []
  exhausted_reason = "planner_runtime_exhausted"
  while True:
    attempted_planner_execution = True
    outcome = run_planner_action_with_review(
      state_obj,
      action,
      turn_plan,
      action_count,
      review_message,
      executor_hooks,
      sub_phase=sub_phase,
      ocr_debug=ocr_debug,
      planned_clicks=planned_clicks,
    )
    status = outcome.get("status")
    if status in {"executed", "previewed", "reassess", "blocked"}:
      _transition(state_obj, "planner_runtime", "planner_runtime", status, outcome.get("reason") or "")
      return outcome

    exhausted_reason = outcome.get("reason") or exhausted_reason
    if status != "failed":
      return outcome

    runtime_hooks.push_turn_retry_debug(
      state_obj,
      reason="Planner-mode action attempt failed; trying fallback actions inside planner runtime.",
      reasons=[action.func or "unknown_action"],
      before_phase="evaluating_strategy",
      context="planner_runtime",
      event="turn_retry",
      result="planner_retry",
      sub_phase="evaluate_training_action",
      phase="evaluating_strategy",
    )

    consecutive_warning_retry_training = runtime_hooks.should_retry_training_after_consecutive_warning(action)
    if consecutive_warning_retry_training:
      if runtime_hooks.prepare_training_fallback_after_consecutive_warning(action):
        info(
          "[FALLBACK] Consecutive-race warning blocked optional rival race after energy rescue. "
          "Retrying the rescued training fallback."
        )
      else:
        warning(
          "[FALLBACK] Consecutive-race warning suggested a rescued training retry, "
          "but no valid training fallback was available."
        )

    available_actions, _ = _prepare_retry_order(action)
    if consecutive_warning_retry_training:
      available_actions = ["do_training"] + [name for name in available_actions if name != "do_training"]

    if action.get("race_mission_available") and action.func == "do_race":
      info("Couldn't match race mission to aptitudes, trying next action.")
    else:
      info(f"Action {action.func} failed in planner runtime, trying other actions.")
    info(f"Available actions: {available_actions}")

    has_selected_race = _selected_race_still_pending(state_obj, action)
    if bool(action.get("_consecutive_warning_force_rest")) and not consecutive_warning_retry_training:
      has_selected_race = False
    allow_rest_fallback_for_optional_rival = bool(
      action.get("prefer_rival_race")
      and action.get("_rival_fallback_func") == "do_rest"
      and not action.get("scheduled_race")
      and not action.get("trackblazer_lobby_scheduled_race")
      and not action.get("is_race_day")
    )

    retried = False
    for function_name in available_actions:
      if function_name in attempt_action_names:
        continue
      if function_name == "do_rest" and has_selected_race and not allow_rest_fallback_for_optional_rival:
        info("[FALLBACK] Skipping do_rest fallback — selected race is still pending.")
        continue

      attempt_action_names.append(function_name)
      info(f"[TB_PLANNER] Retrying via planner runtime candidate: {function_name}")
      _prepare_retry_candidate(state_obj, action, function_name, runtime_hooks)
      skill_result = _attach_skill_plan_for_attempt(state_obj, action, action_count, runtime_hooks)
      if skill_result == "failed":
        return {"status": "failed", "reason": "skill_plan_attach_failed_after_retry"}
      try:
        _, turn_plan = _refresh_planner_turn(state_obj, action)
      except Exception as exc:
        if outcome.get("committed"):
          warning(f"[TB_PLANNER] Retry replan failed after committed planner steps: {exc}")
          return {"status": "failed", "reason": f"planner_retry_replan_failed: {exc}", "committed": True}
        return _planner_runtime_fallback_to_legacy(state_obj, action, f"planner_retry_replan_failed: {exc}")
      retried = True
      break

    if retried:
      continue

    if outcome.get("committed"):
      _transition(state_obj, "planner_runtime", "planner_runtime", "failed", exhausted_reason, {"committed": True})
      return {"status": "failed", "reason": exhausted_reason, "committed": True}
    return _planner_runtime_fallback_to_legacy(state_obj, action, exhausted_reason)
