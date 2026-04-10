from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict

import utils.constants as constants
from core.trackblazer.executor import PlannerExecutorHooks, run_planner_action_with_review
from core.trackblazer.planner import (
  RUNTIME_PATH_PLANNER_FALLBACK_LEGACY,
  RUNTIME_PATH_PLANNER_RUNTIME,
  apply_selected_action_payload,
  append_planner_runtime_transition,
  build_turn_plan_execution_action,
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


def _apply_retry_payload(state_obj, action, retry_payload, hooks: PlannerRuntimeHooks):
  retry_payload = retry_payload if isinstance(retry_payload, dict) else {}
  apply_selected_action_payload(action, retry_payload)
  _set_disable_skip_turn_fallback(action, enabled=(action.func == "do_rest"))
  if (constants.SCENARIO_NAME or "default") in ("mant", "trackblazer"):
    hooks.attach_trackblazer_pre_action_item_plan(state_obj, action)
  return action


def _planner_retry_entries(turn_plan):
  fallback_policy = dict((turn_plan.fallback_policy if turn_plan else {}) or {})
  return [
    dict(entry)
    for entry in list(fallback_policy.get("chain") or [])
    if isinstance(entry, dict) and isinstance(entry.get("target_payload"), dict) and entry.get("target_payload")
  ]


def _retry_entry_key(entry):
  entry = entry if isinstance(entry, dict) else {}
  return "|".join(
    str(part or "")
    for part in (
      entry.get("trigger"),
      entry.get("source_node_id"),
      entry.get("target_func"),
      ((entry.get("target_payload") or {}).get("training_name") if isinstance(entry.get("target_payload"), dict) else ""),
      ((entry.get("target_payload") or {}).get("race_name") if isinstance(entry.get("target_payload"), dict) else ""),
    )
  )


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
    execution_action = build_turn_plan_execution_action(action, turn_plan)
  except Exception as exc:
    return _planner_runtime_fallback_to_legacy(state_obj, action, f"planner_runtime_setup_failed: {exc}")

  attempted_retry_keys = []
  exhausted_reason = "planner_runtime_exhausted"
  while True:
    attempted_planner_execution = True
    outcome = run_planner_action_with_review(
      state_obj,
      execution_action,
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
      reasons=[execution_action.func or "unknown_action"],
      before_phase="evaluating_strategy",
      context="planner_runtime",
      event="turn_retry",
      result="planner_retry",
      sub_phase="evaluate_training_action",
      phase="evaluating_strategy",
    )

    consecutive_warning_retry_training = runtime_hooks.should_retry_training_after_consecutive_warning(execution_action)
    if consecutive_warning_retry_training:
      if runtime_hooks.prepare_training_fallback_after_consecutive_warning(execution_action):
        info(
          "[FALLBACK] Consecutive-race warning blocked optional rival race after energy rescue. "
          "Retrying the rescued training fallback."
        )
      else:
        warning(
          "[FALLBACK] Consecutive-race warning suggested a rescued training retry, "
          "but no valid training fallback was available."
        )

    retry_entries = _planner_retry_entries(turn_plan)
    if consecutive_warning_retry_training:
      retry_entries = [
        {
          "trigger": "consecutive_warning_retry_training",
          "target_func": "do_training",
          "target_payload": {
            "func": "do_training",
            "training_name": execution_action.get("training_name"),
            "training_data": execution_action.get("training_data"),
          },
          "target_label": execution_action.get("training_name") or "training",
          "source_node_id": "warning_retry_training",
          "planner_ranked": False,
        },
      ] + retry_entries

    if execution_action.get("race_mission_available") and execution_action.func == "do_race":
      info("Couldn't match race mission to aptitudes, trying next action.")
    else:
      info(f"Action {execution_action.func} failed in planner runtime, trying other actions.")
    info(f"Planner fallback entries: {[entry.get('target_func') for entry in retry_entries]}")

    retried = False
    for retry_entry in retry_entries:
      retry_key = _retry_entry_key(retry_entry)
      retry_payload = dict(retry_entry.get("target_payload") or {})
      if retry_key in attempted_retry_keys:
        continue
      if not retry_payload.get("func"):
        continue

      attempted_retry_keys.append(retry_key)
      info(
        "[TB_PLANNER] Retrying via planner runtime fallback: "
        f"{retry_entry.get('trigger') or 'retry_next'} -> {retry_payload.get('func')}"
      )
      _apply_retry_payload(state_obj, execution_action, retry_payload, runtime_hooks)
      skill_result = _attach_skill_plan_for_attempt(state_obj, execution_action, action_count, runtime_hooks)
      if skill_result == "failed":
        return {"status": "failed", "reason": "skill_plan_attach_failed_after_retry"}
      try:
        _, turn_plan = _refresh_planner_turn(state_obj, execution_action)
        execution_action = build_turn_plan_execution_action(execution_action, turn_plan)
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
