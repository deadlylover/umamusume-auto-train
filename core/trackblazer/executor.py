from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from core.trackblazer.models import TurnPlan
from core.trackblazer.planner import append_planner_runtime_transition
from utils.log import info, warning


_ITEM_FAILURE_REASSESS_REASONS = {
  "failed_to_open_inventory",
  "failed_to_close_inventory",
  "required_items_not_actionable",
}


@dataclass
class PlannerExecutorHooks:
  skill_purchase_plan: Callable[[Any], Dict[str, Any]]
  review_action_before_execution: Callable[..., bool]
  wait_for_execute_intent: Callable[..., str]
  run_skill_purchase_plan: Callable[[Dict[str, Any], Any, int], Dict[str, Any]]
  run_trackblazer_shop_purchases: Callable[[Dict[str, Any], Any], Dict[str, Any]]
  wait_for_lobby_after_shop_purchase: Callable[[], bool]
  refresh_trackblazer_pre_action_inventory: Callable[[Dict[str, Any], Any], Dict[str, Any]]
  execute_trackblazer_pre_action_items: Callable[[Dict[str, Any], Any, str], Dict[str, Any]]
  wait_for_lobby_after_item_use: Callable[..., bool]
  enforce_operator_race_gate_before_execute: Callable[..., Optional[str]]
  run_planner_race_preflight: Callable[..., Optional[str]]
  resolve_post_action_resolution: Callable[[Dict[str, Any], Any], bool]
  trackblazer_action_failure_should_block_retry: Callable[[Dict[str, Any], Any], bool]
  update_operator_snapshot: Callable[..., Any]
  resolve_consecutive_race_warning: Optional[Callable[..., Dict[str, Any]]] = None


def _step_present(turn_plan: TurnPlan, step_type: str) -> bool:
  for step in list(turn_plan.step_sequence or []):
    if getattr(step, "step_type", "") == step_type:
      return True
  return False


def _review_reasoning_notes(skill_plan):
  reasoning_notes = "Use execute mode to commit clicks. Current view shows OCR/debug and planned click targets only."
  if skill_plan:
    reasoning_notes = (
      "Skill scan is complete. Continue will execute the skill purchase sub-routine first, "
      "then continue with the rest of the turn. "
      + reasoning_notes
    )
  return reasoning_notes


def _transition(state_obj, step_id, step_type, status, note="", details=None):
  append_planner_runtime_transition(
    state_obj,
    step_id=step_id,
    step_type=step_type,
    status=status,
    note=note,
    details=details or {},
  )


def _preview_trackblazer_items(state_obj, action, hooks: PlannerExecutorHooks):
  refresh_result = hooks.refresh_trackblazer_pre_action_inventory(state_obj, action)
  if refresh_result.get("status") == "failed":
    return refresh_result
  if refresh_result.get("status") == "skipped":
    return {"status": "skipped"}
  return hooks.execute_trackblazer_pre_action_items(state_obj, action, commit_mode="dry_run")


def run_planner_action_with_review(
  state_obj,
  action,
  turn_plan: TurnPlan,
  action_count: int,
  review_message: str,
  hooks: PlannerExecutorHooks,
  *,
  sub_phase=None,
  ocr_debug=None,
  planned_clicks=None,
):
  skill_plan = hooks.skill_purchase_plan(action)
  reasoning_notes = _review_reasoning_notes(skill_plan)
  committed = False

  _transition(state_obj, "await_operator_review", "await_operator_review", "started")
  if not hooks.review_action_before_execution(
    state_obj,
    action,
    review_message,
    sub_phase=sub_phase,
    ocr_debug=ocr_debug,
    planned_clicks=planned_clicks,
    reasoning_notes=reasoning_notes,
  ):
    _transition(state_obj, "await_operator_review", "await_operator_review", "failed", "review_cancelled")
    return {"status": "failed", "step_id": "await_operator_review", "reason": "review_cancelled", "committed": committed}

  execution_intent = hooks.wait_for_execute_intent(
    state_obj,
    action,
    message_prefix="Action review ready",
    reasoning_notes=reasoning_notes,
    sub_phase=sub_phase,
    ocr_debug=ocr_debug,
    planned_clicks=planned_clicks,
  )
  if execution_intent == "failed":
    _transition(state_obj, "await_operator_review", "await_operator_review", "failed", "execute_intent_failed")
    return {"status": "failed", "step_id": "await_operator_review", "reason": "execute_intent_failed", "committed": committed}
  if execution_intent != "execute":
    preview_result = _preview_trackblazer_items(state_obj, action, hooks)
    _transition(
      state_obj,
      "await_operator_review",
      "await_operator_review",
      "previewed",
      "check_only_preview",
      {"item_preview_status": preview_result.get("status")},
    )
    return {"status": "previewed", "step_id": "await_operator_review", "reason": "check_only_preview", "committed": committed}
  _transition(state_obj, "await_operator_review", "await_operator_review", "completed", "operator_confirmed")

  if _step_present(turn_plan, "execute_skill_purchases"):
    _transition(state_obj, "execute_skill_purchases", "execute_skill_purchases", "started")
    skill_purchase_result = hooks.run_skill_purchase_plan(state_obj, action, action_count)
    if skill_purchase_result.get("status") == "failed":
      skill_result = skill_purchase_result.get("result") or {}
      skill_flow = skill_result.get("skill_purchase_flow") or {}
      if skill_flow.get("opened") and not skill_flow.get("closed"):
        from core.skill_scanner import _close_skills_page

        warning("[SKILL] Skill purchase left page open, attempting emergency close.")
        emergency_close = _close_skills_page()
        if emergency_close.get("closed"):
          skill_flow["closed"] = True
          skill_flow["emergency_close"] = True
        else:
          hooks.update_operator_snapshot(
            state_obj,
            action,
            phase="recovering",
            status="error",
            error_text=f"Skill purchase left skills page open: {skill_purchase_result.get('reason')}",
            sub_phase=sub_phase,
            ocr_debug=ocr_debug,
            planned_clicks=planned_clicks,
          )
          _transition(state_obj, "execute_skill_purchases", "execute_skill_purchases", "blocked", skill_purchase_result.get("reason") or "skill_purchase_left_page_open")
          return {
            "status": "blocked",
            "step_id": "execute_skill_purchases",
            "reason": skill_purchase_result.get("reason") or "skill_purchase_left_page_open",
            "committed": committed,
          }
      if skill_flow.get("opened") and not skill_flow.get("closed"):
        hooks.update_operator_snapshot(
          state_obj,
          action,
          phase="recovering",
          status="error",
          error_text=f"Skill purchase left skills page open: {skill_purchase_result.get('reason')}",
          sub_phase=sub_phase,
          ocr_debug=ocr_debug,
          planned_clicks=planned_clicks,
        )
        _transition(state_obj, "execute_skill_purchases", "execute_skill_purchases", "blocked", skill_purchase_result.get("reason") or "skill_purchase_left_page_open")
        return {
          "status": "blocked",
          "step_id": "execute_skill_purchases",
          "reason": skill_purchase_result.get("reason") or "skill_purchase_left_page_open",
          "committed": committed,
        }
      warning(
        f"[SKILL] Skill purchase failed: {skill_purchase_result.get('reason')}. "
        "Continuing with the rest of the turn."
      )
      hooks.update_operator_snapshot(
        state_obj,
        action,
        phase="executing_action",
        message=f"Skill purchase failed ({skill_purchase_result.get('reason')}); proceeding with {action.func}.",
        sub_phase=sub_phase,
        ocr_debug=ocr_debug,
        planned_clicks=planned_clicks,
      )
      _transition(state_obj, "execute_skill_purchases", "execute_skill_purchases", "failed", skill_purchase_result.get("reason") or "skill_purchase_failed")
    elif skill_purchase_result.get("status") == "executed":
      committed = True
      _transition(state_obj, "execute_skill_purchases", "execute_skill_purchases", "completed", skill_purchase_result.get("reason") or "skill_purchase_complete")
    else:
      _transition(state_obj, "execute_skill_purchases", "execute_skill_purchases", "skipped")

  if _step_present(turn_plan, "execute_shop_purchases"):
    _transition(state_obj, "execute_shop_purchases", "execute_shop_purchases", "started")
    shop_purchase_result = hooks.run_trackblazer_shop_purchases(state_obj, action)
    if shop_purchase_result.get("status") == "failed":
      shop_result = shop_purchase_result.get("result") or {}
      shop_flow = shop_result.get("trackblazer_shop_flow") or {}
      if shop_flow.get("entered") and not shop_flow.get("closed"):
        hooks.update_operator_snapshot(
          state_obj,
          action,
          phase="recovering",
          status="error",
          error_text=f"Shop purchase left shop open: {shop_purchase_result.get('reason')}",
          sub_phase=sub_phase,
          ocr_debug=ocr_debug,
          planned_clicks=planned_clicks,
        )
        _transition(state_obj, "execute_shop_purchases", "execute_shop_purchases", "blocked", shop_purchase_result.get("reason") or "trackblazer_shop_purchase_failed")
        return {
          "status": "blocked",
          "step_id": "execute_shop_purchases",
          "reason": shop_purchase_result.get("reason") or "trackblazer_shop_purchase_failed",
          "committed": committed,
        }
      warning(
        f"[TB_SHOP] Shop purchase failed: {shop_purchase_result.get('reason')}. "
        "Continuing with main action."
      )
      hooks.update_operator_snapshot(
        state_obj,
        action,
        phase="executing_action",
        message=f"Shop purchase failed ({shop_purchase_result.get('reason')}); proceeding with {action.func}.",
        sub_phase=sub_phase,
        ocr_debug=ocr_debug,
        planned_clicks=planned_clicks,
      )
      _transition(state_obj, "execute_shop_purchases", "execute_shop_purchases", "failed", shop_purchase_result.get("reason") or "trackblazer_shop_purchase_failed")
    elif shop_purchase_result.get("status") == "executed":
      committed = True
      _transition(state_obj, "execute_shop_purchases", "execute_shop_purchases", "completed", shop_purchase_result.get("reason") or "trackblazer_shop_purchase_applied")
      if _step_present(turn_plan, "await_lobby_after_shop"):
        _transition(state_obj, "await_lobby_after_shop", "await_lobby_after_shop", "started")
        if not hooks.wait_for_lobby_after_shop_purchase():
          hooks.update_operator_snapshot(
            state_obj,
            action,
            phase="recovering",
            status="error",
            error_text="Lobby not visible after shop purchase; shop/inventory overlay may still be up.",
            sub_phase=sub_phase,
            ocr_debug=ocr_debug,
            planned_clicks=planned_clicks,
          )
          _transition(state_obj, "await_lobby_after_shop", "await_lobby_after_shop", "blocked", "lobby_return_failed")
          return {
            "status": "blocked",
            "step_id": "await_lobby_after_shop",
            "reason": "lobby_return_failed",
            "committed": committed,
          }
        _transition(state_obj, "await_lobby_after_shop", "await_lobby_after_shop", "completed", "lobby_restored")
    else:
      _transition(state_obj, "execute_shop_purchases", "execute_shop_purchases", "skipped")

  item_refresh_result = {"status": "skipped"}
  if _step_present(turn_plan, "refresh_inventory_for_items"):
    _transition(state_obj, "refresh_inventory_for_items", "refresh_inventory_for_items", "started")
    item_refresh_result = hooks.refresh_trackblazer_pre_action_inventory(state_obj, action)
    if item_refresh_result.get("status") == "failed":
      _transition(
        state_obj,
        "refresh_inventory_for_items",
        "refresh_inventory_for_items",
        "failed",
        item_refresh_result.get("reason") or "inventory_refresh_failed",
      )
      return {
        "status": "failed",
        "step_id": "refresh_inventory_for_items",
        "reason": item_refresh_result.get("reason") or "inventory_refresh_failed",
        "committed": committed,
      }
    _transition(
      state_obj,
      "refresh_inventory_for_items",
      "refresh_inventory_for_items",
      "completed" if item_refresh_result.get("status") != "skipped" else "skipped",
      item_refresh_result.get("reason") or "",
    )

  if _step_present(turn_plan, "replan_pre_action_items"):
    _transition(
      state_obj,
      "replan_pre_action_items",
      "replan_pre_action_items",
      "completed" if item_refresh_result.get("status") != "skipped" else "skipped",
      item_refresh_result.get("reason") or "pre_action_item_plan_ready",
    )

  item_execute_result = {"status": "skipped"}
  if _step_present(turn_plan, "execute_pre_action_items") and item_refresh_result.get("status") != "skipped":
    _transition(state_obj, "execute_pre_action_items", "execute_pre_action_items", "started")
    item_execute_result = hooks.execute_trackblazer_pre_action_items(state_obj, action, commit_mode="full")
    if item_execute_result.get("status") == "failed":
      failure_reason = item_execute_result.get("reason") or "trackblazer_pre_action_items_failed"
      if failure_reason in _ITEM_FAILURE_REASSESS_REASONS:
        hooks.update_operator_snapshot(
          state_obj,
          action,
          phase="collecting_main_state",
          message="Pre-action item flow failed; retrying the turn before choosing a fallback action.",
          sub_phase="reassess_after_item_use",
          ocr_debug=ocr_debug,
          planned_clicks=planned_clicks,
        )
        _transition(state_obj, "execute_pre_action_items", "execute_pre_action_items", "reassess", failure_reason)
        return {
          "status": "reassess",
          "step_id": "execute_pre_action_items",
          "reason": failure_reason,
          "committed": committed,
        }
      hooks.update_operator_snapshot(
        state_obj,
        action,
        phase="recovering",
        status="error",
        error_text=f"Pre-action item use failed: {failure_reason}",
        sub_phase=sub_phase,
        ocr_debug=ocr_debug,
        planned_clicks=planned_clicks,
      )
      if hooks.trackblazer_action_failure_should_block_retry(state_obj, action):
        _transition(state_obj, "execute_pre_action_items", "execute_pre_action_items", "blocked", failure_reason)
        return {
          "status": "blocked",
          "step_id": "execute_pre_action_items",
          "reason": failure_reason,
          "committed": committed,
        }
      _transition(state_obj, "execute_pre_action_items", "execute_pre_action_items", "failed", failure_reason)
      return {
        "status": "failed",
        "step_id": "execute_pre_action_items",
        "reason": failure_reason,
        "committed": committed,
      }
    if item_execute_result.get("status") in {"executed", "reassess"}:
      committed = True
    _transition(
      state_obj,
      "execute_pre_action_items",
      "execute_pre_action_items",
      item_execute_result.get("status") or "completed",
      item_execute_result.get("reason") or "",
    )

  if _step_present(turn_plan, "await_lobby_after_items") and item_execute_result.get("status") in {"executed", "reassess"}:
    _transition(state_obj, "await_lobby_after_items", "await_lobby_after_items", "started")
    lobby_confirmed = hooks.wait_for_lobby_after_item_use(
      state_obj,
      action,
      sub_phase=sub_phase,
      ocr_debug=ocr_debug,
      planned_clicks=planned_clicks,
    )
    if not lobby_confirmed:
      hooks.update_operator_snapshot(
        state_obj,
        action,
        phase="recovering",
        status="error",
        error_text="Lobby not visible after item use; inventory overlay may still be up.",
        sub_phase=sub_phase,
        ocr_debug=ocr_debug,
        planned_clicks=planned_clicks,
      )
      _transition(state_obj, "await_lobby_after_items", "await_lobby_after_items", "blocked", "lobby_return_failed")
      return {
        "status": "blocked",
        "step_id": "await_lobby_after_items",
        "reason": "lobby_return_failed",
        "committed": committed,
      }
    _transition(state_obj, "await_lobby_after_items", "await_lobby_after_items", "completed", "lobby_restored")

  if _step_present(turn_plan, "transition_after_pre_action_items") and item_execute_result.get("status") == "reassess":
    hooks.update_operator_snapshot(
      state_obj,
      action,
      phase="collecting_main_state",
      message="Trackblazer items applied. Rechecking turn state before committing an action.",
      sub_phase="reassess_after_item_use",
      ocr_debug=ocr_debug,
      planned_clicks=planned_clicks,
    )
    _transition(
      state_obj,
      "transition_after_pre_action_items",
      "transition_reassess_after_items",
      "reassess",
      item_execute_result.get("reason") or "trackblazer_item_use_reassess",
    )
    return {
      "status": "reassess",
      "step_id": "transition_after_pre_action_items",
      "reason": item_execute_result.get("reason") or "trackblazer_item_use_reassess",
      "committed": committed,
    }

  if _step_present(turn_plan, "enforce_race_gate"):
    _transition(state_obj, "enforce_race_gate", "enforce_race_gate", "started")
    gate_result = hooks.enforce_operator_race_gate_before_execute(
      state_obj,
      action,
      sub_phase=sub_phase,
      ocr_debug=ocr_debug,
      planned_clicks=planned_clicks,
    )
    if gate_result == "blocked":
      _transition(state_obj, "enforce_race_gate", "enforce_race_gate", "blocked", "race_gate_blocked")
      return {"status": "blocked", "step_id": "enforce_race_gate", "reason": "race_gate_blocked", "committed": committed}
    if gate_result == "reverted":
      _transition(state_obj, "enforce_race_gate", "enforce_race_gate", "completed", "race_gate_reverted_to_fallback")
    else:
      _transition(state_obj, "enforce_race_gate", "enforce_race_gate", "completed", "race_gate_cleared")

  if _step_present(turn_plan, "execute_rival_scout"):
    _transition(state_obj, "execute_rival_scout", "execute_rival_scout", "started")
    preflight_result = hooks.run_planner_race_preflight(
      state_obj,
      action,
      sub_phase=sub_phase,
      ocr_debug=ocr_debug,
      planned_clicks=planned_clicks,
    )
    if preflight_result in {"failed", "blocked", "reassess", "previewed", "executed"}:
      _transition(state_obj, "execute_rival_scout", "execute_rival_scout", preflight_result, "planner_race_preflight_terminal")
      return {
        "status": preflight_result,
        "step_id": "execute_rival_scout",
        "reason": "planner_race_preflight_terminal",
        "committed": committed,
      }
    _transition(state_obj, "execute_rival_scout", "execute_rival_scout", "completed", "rival_scout_resolved")

  if _step_present(turn_plan, "resolve_consecutive_race_warning"):
    _transition(state_obj, "resolve_consecutive_race_warning", "resolve_consecutive_race_warning", "started")
    resolver = getattr(hooks, "resolve_consecutive_race_warning", None)
    warning_result = (
      resolver(
        state_obj,
        action,
        turn_plan=turn_plan,
        sub_phase=sub_phase,
        ocr_debug=ocr_debug,
        planned_clicks=planned_clicks,
      )
      if callable(resolver) else
      {"status": "completed", "reason": "warning_step_noop"}
    )
    warning_status = str((warning_result or {}).get("status") or "completed")
    warning_reason = str((warning_result or {}).get("reason") or "")
    if warning_status in {"failed", "blocked", "reassess", "previewed", "executed"}:
      _transition(
        state_obj,
        "resolve_consecutive_race_warning",
        "resolve_consecutive_race_warning",
        warning_status,
        warning_reason or "warning_policy_terminal",
      )
      return {
        "status": warning_status,
        "step_id": "resolve_consecutive_race_warning",
        "reason": warning_reason or "warning_policy_terminal",
        "committed": committed,
      }
    _transition(
      state_obj,
      "resolve_consecutive_race_warning",
      "resolve_consecutive_race_warning",
      "completed",
      warning_reason or "warning_policy_ready",
    )

  hooks.update_operator_snapshot(
    state_obj,
    action,
    phase="executing_action",
    message=f"Executing {action.func}.",
    sub_phase="action_run",
    ocr_debug=ocr_debug,
    planned_clicks=planned_clicks,
  )
  _transition(state_obj, "execute_main_action", "execute_main_action", "started", getattr(action, "func", ""))
  result = action.run()
  if not result:
    _transition(
      state_obj,
      "execute_main_action",
      "execute_main_action",
      "failed",
      f"action_failed:{getattr(action, 'func', '')}",
      {
        "warning_outcome": dict(getattr(action, "get", lambda _k, _d=None: _d)("planner_warning_outcome") or {}),
        "warning_cancelled": bool(getattr(action, "get", lambda _k, _d=None: _d)("_consecutive_warning_cancelled")),
        "warning_reason": (
          (dict(getattr(action, "get", lambda _k, _d=None: _d)("planner_warning_outcome") or {}).get("reason"))
          or getattr(action, "get", lambda _k, _d=None: _d)("_consecutive_warning_cancel_reason")
        ),
      },
    )
    hooks.update_operator_snapshot(
      state_obj,
      action,
      phase="recovering",
      status="error",
      error_text=f"Action failed: {action.func}",
      sub_phase=sub_phase,
      ocr_debug=ocr_debug,
      planned_clicks=planned_clicks,
    )
    return {
      "status": "failed",
      "step_id": "execute_main_action",
      "reason": f"action_failed:{action.func}",
      "committed": committed,
    }
  committed = True
  _transition(state_obj, "execute_main_action", "execute_main_action", "completed", getattr(action, "func", ""))

  _transition(state_obj, "resolve_post_action", "resolve_post_action", "started")
  post_action_result = hooks.resolve_post_action_resolution(state_obj, action)
  if not post_action_result:
    hooks.update_operator_snapshot(
      state_obj,
      action,
      phase="recovering",
      status="error",
      error_text=f"Post-action resolution failed after {action.func}",
      sub_phase="resolve_post_action",
      ocr_debug=ocr_debug,
      planned_clicks=planned_clicks,
    )
    _transition(state_obj, "resolve_post_action", "resolve_post_action", "failed", "post_action_resolution_failed")
    return {
      "status": "failed",
      "step_id": "resolve_post_action",
      "reason": "post_action_resolution_failed",
      "committed": committed,
    }
  _transition(state_obj, "resolve_post_action", "resolve_post_action", "completed", "turn_complete")
  info(f"[TB_PLANNER] Planner executor completed {action.func}.")
  return {"status": "executed", "step_id": "resolve_post_action", "reason": "turn_complete", "committed": committed}
