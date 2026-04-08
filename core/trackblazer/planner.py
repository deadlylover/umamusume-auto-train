from __future__ import annotations

import copy
import difflib
import hashlib
import json
from typing import Any, Dict, List, Tuple

import core.bot as bot
import core.config as config
import utils.constants as constants
from core.trackblazer.candidates import enumerate_candidate_actions
from core.trackblazer.derive import derive_turn_state
from core.trackblazer_item_use import plan_item_usage
from core.trackblazer.observe import hydrate_observed_turn_state
from core.trackblazer.review import build_ranked_training_snapshot
from core.trackblazer_shop import get_dynamic_shop_limits, get_effective_shop_items
from core.trackblazer.models import (
  BackgroundSkillScanState,
  ExecutionStep,
  PlannerFreshness,
  PlannerRuntimeState,
  TurnPlan,
  render_turn_discussion,
)


PLANNER_STATE_KEY = "trackblazer_planner_state"
PLANNER_RUNTIME_KEY = "trackblazer_planner_runtime"
PLANNER_VERSION = 2


def _normalize_for_hash(value):
  if isinstance(value, dict):
    return {str(key): _normalize_for_hash(val) for key, val in sorted(value.items(), key=lambda item: str(item[0]))}
  if isinstance(value, (list, tuple)):
    return [_normalize_for_hash(item) for item in value]
  if isinstance(value, set):
    return sorted(_normalize_for_hash(item) for item in value)
  if isinstance(value, (str, int, float, bool)) or value is None:
    return value
  return str(value)


def _hash_payload(value) -> str:
  normalized = _normalize_for_hash(value)
  payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
  return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _turn_key(state_obj) -> str:
  year = (state_obj or {}).get("year") or "?"
  turn = (state_obj or {}).get("turn") or "?"
  return f"{year}|{turn}"


def _state_signature(state_obj) -> Dict[str, Any]:
  return {
    "year": (state_obj or {}).get("year"),
    "turn": (state_obj or {}).get("turn"),
    "energy_level": (state_obj or {}).get("energy_level"),
    "max_energy": (state_obj or {}).get("max_energy"),
    "current_mood": (state_obj or {}).get("current_mood"),
    "current_stats": (state_obj or {}).get("current_stats"),
    "trackblazer_inventory_summary": (state_obj or {}).get("trackblazer_inventory_summary"),
    "trackblazer_inventory_pre_shop_summary": (state_obj or {}).get("trackblazer_inventory_pre_shop_summary"),
    "trackblazer_shop_summary": (state_obj or {}).get("trackblazer_shop_summary"),
    "trackblazer_shop_items": list((state_obj or {}).get("trackblazer_shop_items") or []),
    "trackblazer_shop_flow": (state_obj or {}).get("trackblazer_shop_flow"),
    "trackblazer_inventory_flow": (state_obj or {}).get("trackblazer_inventory_flow"),
    "trackblazer_inventory_pre_shop_flow": (state_obj or {}).get("trackblazer_inventory_pre_shop_flow"),
    "training_results": (state_obj or {}).get("training_results"),
    "trackblazer_climax": (state_obj or {}).get("trackblazer_climax"),
    "trackblazer_climax_locked_race": (state_obj or {}).get("trackblazer_climax_locked_race"),
    "trackblazer_trainings_remaining_upper_bound": (state_obj or {}).get("trackblazer_trainings_remaining_upper_bound"),
  }


def _action_signature(action) -> Dict[str, Any]:
  if not hasattr(action, "get"):
    return {}
  training_data = action.get("training_data") or {}
  return {
    "func": getattr(action, "func", None),
    "available_trainings": action.get("available_trainings"),
    "training_name": action.get("training_name"),
    "training_function": action.get("training_function"),
    "race_name": action.get("race_name"),
    "race_image_path": action.get("race_image_path"),
    "race_grade_target": action.get("race_grade_target"),
    "prefer_rival_race": action.get("prefer_rival_race"),
    "fallback_non_rival_race": action.get("fallback_non_rival_race"),
    "scheduled_race": action.get("scheduled_race"),
    "trackblazer_lobby_scheduled_race": action.get("trackblazer_lobby_scheduled_race"),
    "trackblazer_climax_race_day": action.get("trackblazer_climax_race_day"),
    "race_mission_available": action.get("race_mission_available"),
    "_trackblazer_rest_promoted_to_training": action.get("_trackblazer_rest_promoted_to_training"),
    "trackblazer_race_decision": action.get("trackblazer_race_decision"),
    "trackblazer_race_lookahead": action.get("trackblazer_race_lookahead"),
    "rival_scout": action.get("rival_scout"),
    "is_race_day": action.get("is_race_day"),
    "_rival_fallback_func": action.get("_rival_fallback_func"),
    "_consecutive_warning_force_rest": action.get("_consecutive_warning_force_rest"),
    "_consecutive_warning_cancelled": action.get("_consecutive_warning_cancelled"),
    "_consecutive_warning_cancel_reason": action.get("_consecutive_warning_cancel_reason"),
    "date_event_available": action.get("date_event_available"),
    "training_data": {
      "score_tuple": training_data.get("score_tuple"),
      "stat_gains": training_data.get("stat_gains"),
      "failure": training_data.get("failure"),
      "total_supports": training_data.get("total_supports"),
      "total_rainbow_friends": training_data.get("total_rainbow_friends"),
    },
  }


def _skill_context_signature(state_obj) -> Dict[str, Any]:
  context = (state_obj or {}).get("skill_purchase_check") or {}
  shopping_list = list(context.get("shopping_list") or [])
  return {
    "shopping_list": shopping_list,
    "scheduled_g1_race": bool(context.get("scheduled_g1_race")),
    "should_check": bool(context.get("should_check")),
    "current_sp": context.get("current_sp"),
    "threshold": context.get("threshold"),
  }


def _build_selected_action_review_context(action, pre_action_items=None, reassess_after_item_use=None) -> Dict[str, Any]:
  if not hasattr(action, "get"):
    return {
      "func": getattr(action, "func", None),
      "pre_action_item_use": list(pre_action_items or []),
      "reassess_after_item_use": bool(reassess_after_item_use),
    }
  training_data = action.get("training_data") or {}
  return {
    "func": getattr(action, "func", None),
    "training_name": action.get("training_name"),
    "training_function": action.get("training_function"),
    "race_name": action.get("race_name"),
    "race_image_path": action.get("race_image_path"),
    "race_grade_target": action.get("race_grade_target"),
    "score_tuple": copy.deepcopy(training_data.get("score_tuple")),
    "stat_gains": copy.deepcopy(training_data.get("stat_gains")),
    "failure": training_data.get("failure"),
    "total_supports": training_data.get("total_supports"),
    "total_rainbow_friends": training_data.get("total_rainbow_friends"),
    "prefer_rival_race": action.get("prefer_rival_race"),
    "rival_scout": copy.deepcopy(action.get("rival_scout") or {}),
    "pre_action_item_use": copy.deepcopy(pre_action_items or []),
    "reassess_after_item_use": bool(reassess_after_item_use),
    "trackblazer_race_decision": copy.deepcopy(action.get("trackblazer_race_decision") or {}),
    "trackblazer_race_lookahead": copy.deepcopy(action.get("trackblazer_race_lookahead") or {}),
  }

def _skill_shortlist_hash(skill_context_key: str) -> str:
  return _hash_payload({"skill_context_key": skill_context_key})


def ensure_planner_runtime_state(state_obj) -> Dict[str, Any]:
  if not isinstance(state_obj, dict):
    return PlannerRuntimeState().to_dict()

  existing = copy.deepcopy(state_obj.get(PLANNER_RUNTIME_KEY) or {})
  pending_skill_scan = BackgroundSkillScanState(**dict(existing.get("pending_skill_scan") or {}))
  runtime = PlannerRuntimeState(
    turn_key=str(existing.get("turn_key") or _turn_key(state_obj)),
    latest_observation_id=str(existing.get("latest_observation_id") or ""),
    scan_cadence=dict(existing.get("scan_cadence") or {}),
    pending_skill_scan=pending_skill_scan,
    fallback_count=int(existing.get("fallback_count") or 0),
    last_fallback_reason=str(existing.get("last_fallback_reason") or ""),
    transition_breadcrumbs=list(existing.get("transition_breadcrumbs") or []),
  )
  runtime_payload = runtime.to_dict()
  state_obj[PLANNER_RUNTIME_KEY] = runtime_payload
  return runtime_payload


def _candidate_shop_buys(effective_shop_items, shop_items=None, shop_summary=None, held_quantities=None, limit=8):
  detected_shop_keys = set(shop_items or (shop_summary or {}).get("items_detected") or [])
  held_quantities = held_quantities or {}
  shop_coins = int((shop_summary or {}).get("shop_coins") or 0)
  if shop_coins == 0:
    return []

  dynamic_limits = get_dynamic_shop_limits(
    held_quantities=held_quantities,
    year=(shop_summary or {}).get("year"),
    turn=(shop_summary or {}).get("turn"),
  )
  remaining_coins = shop_coins
  would_buy = []
  planned_counts = {}
  planned_family_counts = {}

  for item in effective_shop_items:
    item_key = item.get("key")
    if item_key not in detected_shop_keys:
      continue
    if item.get("effective_priority") == "NEVER":
      continue
    held_quantity = int(held_quantities.get(item_key) or 0)
    max_quantity = int(item.get("max_quantity") or 0)
    dynamic_limit = dynamic_limits.get(item_key) or {}
    if dynamic_limit.get("block_purchase"):
      continue
    planned_for_item = int(planned_counts.get(item_key) or 0)
    dynamic_max_total = dynamic_limit.get("max_total")
    if dynamic_max_total is not None and held_quantity + planned_for_item >= int(dynamic_max_total):
      continue
    family_key = dynamic_limit.get("family_key")
    family_max_total = dynamic_limit.get("family_max_total")
    if family_key and family_max_total is not None:
      family_planned = int(planned_family_counts.get(family_key) or 0)
      family_total_held = int(dynamic_limit.get("family_total_held") or 0)
      if family_total_held + family_planned >= int(family_max_total):
        continue
    remaining_capacity = max(0, max_quantity - held_quantity)
    if max_quantity > 0 and remaining_capacity <= 0:
      continue
    cost = int(item.get("cost") or 0)
    if cost > remaining_coins:
      continue

    reason_parts = [f"policy={item.get('effective_priority')}"]
    timing_rules = item.get("active_timing_rules") or []
    if timing_rules:
      rule = timing_rules[0]
      reason_parts.append(rule.get("label") or "timing override")
      if rule.get("note"):
        reason_parts.append(rule["note"])
    if dynamic_limit.get("reason"):
      reason_parts.append(dynamic_limit["reason"])
    elif item.get("policy_notes"):
      reason_parts.append(item["policy_notes"])
    if max_quantity > 0:
      reason_parts.append(f"hold {held_quantity}/{max_quantity}")
    reason_parts.append(f"cost {cost}")

    would_buy.append(
      {
        "key": item_key,
        "name": item.get("display_name") or str(item_key or "").replace("_", " ").title(),
        "priority": item.get("effective_priority"),
        "cost": cost,
        "held_quantity": held_quantity,
        "max_quantity": max_quantity,
        "reason": "; ".join(part for part in reason_parts if part),
      }
    )
    remaining_coins -= cost
    planned_counts[item_key] = planned_for_item + 1
    if family_key:
      planned_family_counts[family_key] = int(planned_family_counts.get(family_key) or 0) + 1

  return would_buy[: max(0, int(limit))]


def _project_inventory(state_obj, planned_buys):
  projected_state = dict(state_obj or {})
  inventory = copy.deepcopy(projected_state.get("trackblazer_inventory") or {})
  summary = copy.deepcopy(projected_state.get("trackblazer_inventory_summary") or {})
  held_quantities = dict(summary.get("held_quantities") or {})
  items_detected = list(summary.get("items_detected") or [])
  actionable_items = list(summary.get("actionable_items") or [])

  for buy_entry in list(planned_buys or []):
    item_key = buy_entry.get("key") if isinstance(buy_entry, dict) else None
    if not item_key:
      continue
    next_quantity = int(held_quantities.get(item_key) or 0) + 1
    held_quantities[item_key] = next_quantity
    if item_key not in items_detected:
      items_detected.append(item_key)
    if item_key not in actionable_items:
      actionable_items.append(item_key)
    item_entry = dict(inventory.get(item_key) or {})
    item_entry["detected"] = True
    item_entry["held_quantity"] = next_quantity
    inventory[item_key] = item_entry

  summary["held_quantities"] = held_quantities
  summary["items_detected"] = items_detected
  summary["actionable_items"] = actionable_items
  summary["total_detected"] = len(items_detected)
  projected_state["trackblazer_inventory"] = inventory
  projected_state["trackblazer_inventory_summary"] = summary
  return projected_state


def _requires_reassess(items):
  for entry in (items or []):
    if not isinstance(entry, dict):
      continue
    if entry.get("key") == "reset_whistle":
      return True
    if entry.get("usage_group") == "energy":
      return True
  return False


def _order_pre_action_items(items):
  ordered_items = list(items or [])
  if not ordered_items:
    return []

  kale_indexes = [index for index, entry in enumerate(ordered_items) if entry.get("key") == "royal_kale_juice"]
  mood_indexes = [index for index, entry in enumerate(ordered_items) if entry.get("usage_group") == "mood"]
  if not kale_indexes or not mood_indexes:
    return ordered_items

  kale_index = kale_indexes[0]
  first_mood_index = mood_indexes[0]
  if kale_index < first_mood_index:
    return ordered_items

  kale_entry = ordered_items.pop(kale_index)
  ordered_items.insert(first_mood_index, kale_entry)
  return ordered_items


def _resolve_inventory_source(state_obj) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], str]:
  current_inventory = copy.deepcopy((state_obj or {}).get("trackblazer_inventory") or {})
  current_summary = copy.deepcopy((state_obj or {}).get("trackblazer_inventory_summary") or {})
  current_flow = copy.deepcopy((state_obj or {}).get("trackblazer_inventory_flow") or {})
  current_detected = list(current_summary.get("items_detected") or [])
  if current_inventory or current_detected:
    return current_inventory, current_summary, current_flow, "current"

  pre_shop_inventory = copy.deepcopy((state_obj or {}).get("trackblazer_inventory_pre_shop") or {})
  pre_shop_summary = copy.deepcopy((state_obj or {}).get("trackblazer_inventory_pre_shop_summary") or {})
  pre_shop_flow = copy.deepcopy((state_obj or {}).get("trackblazer_inventory_pre_shop_flow") or {})
  pre_shop_detected = list(pre_shop_summary.get("items_detected") or [])
  if pre_shop_inventory or pre_shop_detected:
    return pre_shop_inventory, pre_shop_summary, pre_shop_flow, "pre_shop_fallback"

  return current_inventory, current_summary, current_flow, "current"


def _build_step_sequence(state_obj, action, shop_buy_plan, item_execution_payload) -> List[ExecutionStep]:
  action_func = _action_func(action)
  prefer_rival_race = bool(_action_value(action, "prefer_rival_race"))
  skill_purchase_plan = dict((state_obj or {}).get("skill_purchase_plan") or {})
  execution_items = list((item_execution_payload or {}).get("execution_items") or [])
  reassess_transition = dict((item_execution_payload or {}).get("reassess_transition") or {})
  reassess_after_item_use = bool(reassess_transition.get("required"))
  step_sequence = [
    ExecutionStep(
      step_id="await_operator_review",
      step_type="await_operator_review",
      intent="review_current_turn",
      screen_preconditions=["lobby_snapshot_ready"],
      success_transition="operator_confirmed",
      failure_transition="review_cancelled",
    ),
  ]

  if skill_purchase_plan:
    step_sequence.append(
      ExecutionStep(
        step_id="execute_skill_purchases",
        step_type="execute_skill_purchases",
        intent="commit_skill_purchase_plan",
        screen_preconditions=["skills_menu_accessible"],
        success_transition="skill_purchase_complete",
        failure_transition="skill_purchase_failed",
        planned_clicks=list(skill_purchase_plan.get("planned_clicks") or []),
      )
    )

  if shop_buy_plan:
    step_sequence.append(
      ExecutionStep(
        step_id="execute_shop_purchases",
        step_type="execute_shop_purchases",
        intent="buy_planned_trackblazer_items",
        screen_preconditions=["shop_entry_available"],
        success_transition="shop_purchase_complete",
        failure_transition="shop_purchase_failed",
        planned_clicks=_build_shop_step_planned_clicks(shop_buy_plan),
      )
    )
    step_sequence.append(
      ExecutionStep(
        step_id="await_lobby_after_shop",
        step_type="await_lobby_after_shop",
        intent="return_to_lobby_after_shop",
        screen_preconditions=["shop_overlay_open"],
        success_transition="lobby_restored",
        failure_transition="lobby_return_failed",
      )
    )

  if execution_items:
    step_sequence.append(
      ExecutionStep(
        step_id="refresh_inventory_for_items",
        step_type="refresh_inventory_for_items",
        intent="refresh_inventory_before_item_use",
        screen_preconditions=["inventory_entry_available"],
        success_transition="inventory_refreshed_for_item_use",
        failure_transition="inventory_refresh_failed",
        notes="Planner-owned inventory refresh before item execution.",
        planned_clicks=_build_item_refresh_step_planned_clicks(),
      )
    )
    step_sequence.append(
      ExecutionStep(
        step_id="replan_pre_action_items",
        step_type="replan_pre_action_items",
        intent="recompute_pre_action_item_plan",
        screen_preconditions=["refreshed_inventory_snapshot_ready"],
        success_transition="pre_action_item_plan_ready",
        failure_transition="pre_action_item_plan_failed",
        notes="Planner-owned replan against the refreshed inventory snapshot.",
        metadata={
          "execution_items": copy.deepcopy(execution_items),
          "reassess_transition": copy.deepcopy(reassess_transition),
        },
      )
    )
    step_sequence.append(
      ExecutionStep(
        step_id="execute_pre_action_items",
        step_type="execute_pre_action_items",
        intent="use_planned_pre_action_items",
        screen_preconditions=["inventory_entry_available"],
        success_transition="reassess_required" if reassess_after_item_use else "item_use_complete",
        failure_transition="item_use_failed",
        metadata={
          "execution_items": copy.deepcopy(execution_items),
        },
        planned_clicks=_build_item_execute_step_planned_clicks(item_execution_payload),
      )
    )
    step_sequence.append(
      ExecutionStep(
        step_id="await_lobby_after_items",
        step_type="await_lobby_after_items",
        intent="return_to_lobby_after_item_use",
        screen_preconditions=["inventory_overlay_open"],
        success_transition="lobby_restored",
        failure_transition="lobby_return_failed",
      )
    )
    step_sequence.append(
      ExecutionStep(
        step_id="transition_after_pre_action_items",
        step_type="transition_reassess_after_items" if reassess_after_item_use else "transition_continue_after_items",
        intent="reassess_after_item_use" if reassess_after_item_use else "continue_selected_action",
        screen_preconditions=["lobby_restored"],
        success_transition="reassess" if reassess_after_item_use else "main_action_ready",
        failure_transition="transition_resolution_failed",
        notes=reassess_transition.get("reason") or "",
        metadata=copy.deepcopy(reassess_transition),
        planned_clicks=_build_item_transition_step_planned_clicks(item_execution_payload),
      )
    )

  if action_func == "do_race":
    step_sequence.append(
      ExecutionStep(
        step_id="enforce_race_gate",
        step_type="enforce_race_gate",
        intent="apply_operator_race_gate",
        screen_preconditions=["race_action_selected"],
        success_transition="race_gate_cleared",
        failure_transition="race_gate_blocked",
      )
    )
    if prefer_rival_race:
      step_sequence.append(
        ExecutionStep(
          step_id="execute_rival_scout",
          step_type="execute_rival_scout",
          intent="verify_rival_race_before_commit",
          screen_preconditions=["race_list_accessible"],
          success_transition="rival_race_confirmed",
          failure_transition="revert_to_fallback_action",
        )
      )

  if action_func:
    step_sequence.append(
      ExecutionStep(
        step_id="execute_main_action",
        step_type="execute_main_action",
        intent=action_func,
        screen_preconditions=["main_action_ready"],
        success_transition="action_click_complete",
        failure_transition="action_click_failed",
        planned_clicks=_build_main_action_step_planned_clicks(action),
      )
    )
    step_sequence.append(
      ExecutionStep(
        step_id="resolve_post_action",
        step_type="resolve_post_action",
        intent="stabilize_after_action",
        screen_preconditions=["post_action_transition_expected"],
        success_transition="turn_complete",
        failure_transition="post_action_resolution_failed",
      )
    )

  return step_sequence


def _attach_execution_item_plan(item_use_plan):
  candidates = list((item_use_plan or {}).get("candidates") or [])
  has_whistle = any(entry.get("key") == "reset_whistle" for entry in candidates)
  if has_whistle:
    candidates = [entry for entry in candidates if entry.get("key") == "reset_whistle"]
  elif any(entry.get("usage_group") == "energy" for entry in candidates):
    burst_groups = ("training_burst", "training_burst_specific")
    candidates = [entry for entry in candidates if entry.get("usage_group") not in burst_groups]
  candidates = _order_pre_action_items(candidates)

  deferred_use = list((item_use_plan or {}).get("deferred") or [])
  reassess_after_item_use = _requires_reassess(candidates)
  if reassess_after_item_use:
    planned_item_keys = {entry.get("key") for entry in candidates if entry.get("key")}
    deferred_keys = {entry.get("key") for entry in deferred_use if isinstance(entry, dict)}
    for entry in list((item_use_plan or {}).get("candidates") or []):
      item_key = entry.get("key")
      if not item_key or item_key in planned_item_keys or item_key in deferred_keys:
        continue
      deferred_entry = dict(entry)
      existing_reason = deferred_entry.get("reason") or ""
      deferred_entry["reason"] = (
        f"{existing_reason}; deferred until post-whistle reassess"
        if existing_reason else
        "deferred until post-whistle reassess"
      )
      deferred_use.append(deferred_entry)
  return candidates, deferred_use, reassess_after_item_use


def _inventory_scan_status(inventory_flow):
  inventory_status = "unknown"
  if (inventory_flow or {}).get("opened") or (inventory_flow or {}).get("already_open"):
    inventory_status = "scanned"
  elif (inventory_flow or {}).get("skipped"):
    inventory_status = "skipped"
  return inventory_status


def _shop_scan_status(shop_flow):
  shop_status = "unknown"
  if (shop_flow or {}).get("entered"):
    shop_status = "scanned" if (shop_flow or {}).get("closed") else "open_failed_to_close"
  elif shop_flow:
    shop_status = "skipped" if (shop_flow or {}).get("reason") else "failed"
  return shop_status


def _action_func(action):
  return getattr(action, "func", None) if action is not None else None


def _action_value(action, key, default=None):
  if hasattr(action, "get"):
    return action.get(key, default)
  return default


def _planned_click(label, template=None, *, target=None, region_key=None, note=None):
  payload = {
    "label": label,
    "input_backend": bot.get_active_control_backend(),
    "screenshot_backend": bot.get_screenshot_backend(),
  }
  if template:
    payload["template"] = template
  if target is not None:
    payload["target"] = target
  if region_key:
    payload["region_key"] = region_key
  if note:
    payload["note"] = note
  return payload


def _item_binding_label(action) -> str:
  action_func = _action_func(action) or "unknown"
  training_name = _action_value(action, "training_name")
  race_name = _action_value(action, "race_name")
  if action_func == "do_training" and training_name:
    return f"{action_func}:{training_name}"
  if action_func == "do_race" and race_name:
    return f"{action_func}:{race_name}"
  return action_func


def _item_reassess_reason(execution_items):
  trigger_items = [entry.get("key") for entry in list(execution_items or []) if isinstance(entry, dict) and entry.get("key")]
  if "reset_whistle" in trigger_items:
    return "Reset Whistle rerolls the board, so the selected action must be rebuilt from a fresh training scan"
  if any(entry.get("usage_group") == "energy" for entry in list(execution_items or []) if isinstance(entry, dict)):
    return "Energy items change post-item energy and failure state before the selected action can be committed"
  return "Selected pre-action items change board state before the selected action is committed"


def _build_shop_step_planned_clicks(shop_buy_plan):
  clicks = []
  if not shop_buy_plan:
    return clicks
  clicks.append(_planned_click("Open shop for purchases", note="Trackblazer shop buy step before the main action"))
  for entry in list(shop_buy_plan or []):
    item_name = entry.get("display_name") or entry.get("name") or str(entry.get("key", "item")).replace("_", " ").title()
    cost = entry.get("cost")
    cost_label = f" ({cost} coins)" if cost else ""
    clicks.append(
      _planned_click(
        f"Buy {item_name}{cost_label}",
        note=f"policy={entry.get('priority', '?')}; hold {entry.get('held_quantity', '?')}/{entry.get('max_quantity', '?')}",
      )
    )
  clicks.append(_planned_click("Confirm shop purchase", note="Press confirm to finalize all selected shop items"))
  clicks.append(_planned_click("Close shop", note="Return to lobby after purchase"))
  return clicks


def _build_item_execution_payload(action, shop_buy_plan, execution_items, deferred_use, reassess_after_item_use, item_context):
  trigger_items = [entry.get("key") for entry in list(execution_items or []) if isinstance(entry, dict) and entry.get("key")]
  reassess_reason = _item_reassess_reason(execution_items) if reassess_after_item_use else "Selected pre-action items can flow directly into the already selected action"
  reassess_kind = "continue_selected_action"
  if "reset_whistle" in trigger_items:
    reassess_kind = "reset_whistle_reroll"
  elif any(entry.get("usage_group") == "energy" for entry in list(execution_items or []) if isinstance(entry, dict)):
    reassess_kind = "energy_rescue_reassess" if item_context.get("energy_rescue") else "energy_item_reassess"
  inventory_refresh = {
    "trigger": "post_shop_purchase_refresh" if shop_buy_plan else "pre_action_refresh",
    "reason": (
      "refresh inventory against purchased items before item-use planning"
      if shop_buy_plan else
      "refresh inventory immediately before item-use planning"
    ),
  }
  path = [
    "inventory_refresh" if execution_items else "selected_action_ready",
    "replan_items" if execution_items else "selected_action_ready",
    "execute_pre_action_items" if execution_items else "skip_pre_action_items",
    "await_lobby_after_items" if execution_items else "selected_action_ready",
    "reassess" if reassess_after_item_use else "selected_action_ready",
  ]
  transitions = [
    {
      "from": "inventory_refresh",
      "to": "replan_items",
      "reason": inventory_refresh["reason"],
      "reobserve": False,
      "trigger_items": [],
    },
  ]
  if execution_items:
    transitions.append(
      {
        "from": "replan_items",
        "to": "execute_pre_action_items",
        "reason": "planner-owned item execution payload is now authoritative",
        "reobserve": False,
        "trigger_items": trigger_items,
      }
    )
    transitions.append(
      {
        "from": "execute_pre_action_items",
        "to": "await_lobby_after_items",
        "reason": "inventory overlay must close before the turn can continue",
        "reobserve": False,
        "trigger_items": trigger_items,
      }
    )
    transitions.append(
      {
        "from": "await_lobby_after_items",
        "to": "reassess" if reassess_after_item_use else "selected_action_ready",
        "reason": reassess_reason or "selected action remains valid after item use",
        "reobserve": bool(reassess_after_item_use),
        "trigger_items": trigger_items,
      }
    )

  action_mutations = {
    "trackblazer_shop_buy_plan": copy.deepcopy(list(shop_buy_plan or [])),
    "trackblazer_pre_action_items": copy.deepcopy(list(execution_items or [])),
    "trackblazer_item_use_context": copy.deepcopy(dict(item_context or {})),
    "trackblazer_reassess_after_item_use": bool(reassess_after_item_use),
  }
  return {
    "planner_owned": True,
    "binding_label": _item_binding_label(action),
    "inventory_refresh": inventory_refresh,
    "execution_items": copy.deepcopy(list(execution_items or [])),
    "deferred_items": copy.deepcopy(list(deferred_use or [])),
    "reassess_transition": {
      "required": bool(reassess_after_item_use),
      "transition_kind": reassess_kind,
      "reason": reassess_reason,
      "trigger_items": trigger_items,
      "selected_action_invalidated": bool(reassess_after_item_use),
      "requires_reobserve": bool(reassess_after_item_use),
      "requires_training_rescan": bool(reassess_after_item_use),
      "target_phase": "collecting_main_state" if reassess_after_item_use else "execute_main_action",
    },
    "path": path,
    "transitions": transitions,
    "compatibility_action_fields": action_mutations,
    "action_mutations": action_mutations,
  }


def _build_item_refresh_step_planned_clicks():
  return [
    _planned_click(
      "Open use-items inventory",
      region_key="SCREEN_BOTTOM_BBOX",
      note="Trackblazer pre-action item step before the main action",
    ),
    _planned_click(
      "Scan inventory item rows",
      region_key="MANT_INVENTORY_ITEMS_REGION",
      note="Pair the planned pre-action items to increment controls",
    ),
  ]


def _build_item_execute_step_planned_clicks(item_execution_payload):
  payload = item_execution_payload if isinstance(item_execution_payload, dict) else {}
  execution_items = list(payload.get("execution_items") or [])
  if not execution_items:
    return []
  clicks = []
  for item in execution_items:
    clicks.append(
      _planned_click(
        f"Increment {item.get('name') or item.get('key') or 'item'}",
        note=item.get("reason") or "Select this item once before the main action",
      )
    )
  clicks.append(
    _planned_click(
      "Confirm planned item use",
      note=(
        "In execute mode the bot should commit the planned item use before the main action. "
        "In check-only/preview modes this remains a simulation step."
      ),
    )
  )
  return clicks


def _build_item_transition_step_planned_clicks(item_execution_payload):
  payload = item_execution_payload if isinstance(item_execution_payload, dict) else {}
  if (payload.get("reassess_transition") or {}).get("required"):
    return [
      _planned_click(
        "Rescan trainings after item use",
        region_key="GAME_WINDOW_BBOX",
        note="Item use changes board state (whistle reroll or energy reducing failure), so the follow-up action must be re-evaluated",
      )
    ]
  return []


def _build_reobserve_boundaries(item_execution_payload):
  payload = item_execution_payload if isinstance(item_execution_payload, dict) else {}
  reassess_transition = dict(payload.get("reassess_transition") or {})
  if not reassess_transition.get("required"):
    return []
  return [
    {
      "boundary_id": "post_item_use_reobserve",
      "from_step": "transition_after_pre_action_items",
      "to_state": "collect_main_state",
      "reason": reassess_transition.get("reason"),
      "trigger_items": list(reassess_transition.get("trigger_items") or []),
      "selected_action_invalidated": bool(reassess_transition.get("selected_action_invalidated")),
      "transition_kind": reassess_transition.get("transition_kind"),
    }
  ]


def _build_item_plan_subgraph(item_execution_payload):
  payload = item_execution_payload if isinstance(item_execution_payload, dict) else {}
  execution_items = copy.deepcopy(list(payload.get("execution_items") or []))
  deferred_items = copy.deepcopy(list(payload.get("deferred_items") or []))
  reassess_transition = copy.deepcopy(dict(payload.get("reassess_transition") or {}))
  nodes = [
    {
      "node_id": "preview_item_plan",
      "node_type": "item_plan_preview",
      "binding_label": payload.get("binding_label"),
      "execution_items": execution_items,
      "deferred_items": deferred_items,
    }
  ]
  transitions = []
  if execution_items:
    nodes.extend([
      {
        "node_id": "refresh_inventory_for_items",
        "node_type": "inventory_refresh",
        "reason": (payload.get("inventory_refresh") or {}).get("reason"),
      },
      {
        "node_id": "replan_pre_action_items",
        "node_type": "item_replan",
        "reason": "Recompute item usage against the refreshed inventory snapshot",
      },
      {
        "node_id": "execute_pre_action_items",
        "node_type": "item_execute",
        "items": execution_items,
      },
      {
        "node_id": "transition_after_pre_action_items",
        "node_type": "item_transition",
        "transition": reassess_transition,
      },
    ])
    transitions.extend([
      {
        "source_node_id": "preview_item_plan",
        "target_node_id": "refresh_inventory_for_items",
        "transition_type": "refresh_before_item_use",
      },
      {
        "source_node_id": "refresh_inventory_for_items",
        "target_node_id": "replan_pre_action_items",
        "transition_type": "replan_from_refreshed_inventory",
      },
      {
        "source_node_id": "replan_pre_action_items",
        "target_node_id": "execute_pre_action_items",
        "transition_type": "commit_pre_action_items",
      },
      {
        "source_node_id": "execute_pre_action_items",
        "target_node_id": "transition_after_pre_action_items",
        "transition_type": reassess_transition.get("transition_kind") or "continue_selected_action",
      },
    ])
  return {
    "graph_type": "trackblazer_pre_action_items",
    "binding_label": payload.get("binding_label"),
    "inventory_refresh": copy.deepcopy(payload.get("inventory_refresh") or {}),
    "execution_items": execution_items,
    "deferred_items": deferred_items,
    "path": copy.deepcopy(payload.get("path") or []),
    "nodes": nodes,
    "node_transitions": transitions,
    "transitions": copy.deepcopy(payload.get("transitions") or []),
  }


def _build_main_action_step_planned_clicks(action):
  action_func = _action_func(action)
  if not action_func:
    return []
  if action_func == "do_training":
    training_name = _action_value(action, "training_name")
    return [
      _planned_click("Open training menu", "assets/buttons/training_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
      _planned_click(
        f"Select training: {training_name or 'unknown'}",
        target=constants.TRAINING_BUTTON_POSITIONS.get(training_name),
        note="Double-click training slot",
      ),
    ]
  if action_func == "do_rest":
    return [
      _planned_click("Click rest button", "assets/buttons/rest_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
      _planned_click("Fallback summer rest button", "assets/buttons/rest_summer_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
    ]
  if action_func == "do_recreation":
    return [
      _planned_click("Open recreation menu", "assets/buttons/recreation_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
      _planned_click("Fallback summer recreation button", "assets/buttons/rest_summer_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
    ]
  if action_func == "do_infirmary":
    return [_planned_click("Click infirmary button", "assets/buttons/infirmary_btn.png", region_key="SCREEN_BOTTOM_BBOX")]
  if action_func == "do_race":
    race_name = _action_value(action, "race_name")
    race_grade_target = _action_value(action, "race_grade_target")
    prefer_rival_race = bool(_action_value(action, "prefer_rival_race"))
    fallback_non_rival_race = bool(_action_value(action, "fallback_non_rival_race"))
    is_race_day = bool(_action_value(action, "is_race_day"))
    is_trackblazer_climax_race_day = bool(_action_value(action, "trackblazer_climax_race_day"))
    scheduled_race = bool(_action_value(action, "scheduled_race") or _action_value(action, "trackblazer_lobby_scheduled_race"))
    race_template = f"assets/races/{race_name}.png" if race_name and race_name not in ("", "any") else _action_value(action, "race_image_path") or "assets/ui/match_track.png"
    if is_race_day and is_trackblazer_climax_race_day:
      return [
        _planned_click(
          "Click forced Climax race button",
          constants.TRACKBLAZER_RACE_TEMPLATES.get("climax_race_button"),
          note="Race-day screen replaces the normal training/rest/races buttons.",
        ),
        _planned_click(
          "Confirm race-day prompt",
          "assets/buttons/ok_btn.png",
          region_key="GAME_WINDOW_BBOX",
          note="Advance from the race-day prompt after entering the forced race.",
        ),
        _planned_click("Confirm race", "assets/buttons/race_btn.png"),
        _planned_click("Fallback BlueStacks confirm", "assets/buttons/bluestacks/race_btn.png"),
      ]
    return [
      _planned_click("Open race menu", "assets/buttons/races_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
      _planned_click(
        "Check consecutive-race warning",
        constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive"),
        region_key="GAME_WINDOW_BBOX",
        note=(
          "If this warning appears after clicking Races, continue with OK for scheduled races; "
          "otherwise follow the race gate before opening the race list."
          if scheduled_race else
          "Fallback non-rival race: cancel and revert to training if consecutive-race warning appears."
          if fallback_non_rival_race else
          "If this warning appears after clicking Races, decide whether to continue with OK "
          "or back out with Cancel before opening the race list."
        ),
      ),
      _planned_click(
        "Continue through warning (OK)",
        constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive_ok") or "assets/buttons/ok_btn.png",
        region_key="GAME_WINDOW_BBOX",
        note=(
          "Scheduled race override: click the warning dialog OK and continue into the race list."
          if scheduled_race else
          "Use the warning-dialog OK when the race gate accepts a third consecutive race."
        ),
      ),
      _planned_click(
        "Fallback warning OK",
        "assets/buttons/ok_btn.png",
        region_key="GAME_WINDOW_BBOX",
        note="Generic fallback if the warning-specific OK template is not matched.",
      ),
      _planned_click(
        "Back out from warning (Cancel)",
        "assets/buttons/cancel_btn.png",
        region_key="GAME_WINDOW_BBOX",
        note=(
          "Not expected for scheduled races; only use if the dialog must be dismissed back to lobby."
          if scheduled_race else
          "Use this when the race gate rejects a third consecutive race and returns to lobby."
        ),
      ),
      _planned_click(
        "Scan/select race entry",
        race_template,
        region_key="RACE_LIST_BOX_BBOX",
        note=(
          f"target={race_grade_target or 'any'}"
          + ("; prefer rival row when present" if prefer_rival_race else "")
        ),
      ),
      _planned_click("Confirm race", "assets/buttons/race_btn.png"),
      _planned_click("Fallback BlueStacks confirm", "assets/buttons/bluestacks/race_btn.png"),
    ]
  if action_func == "buy_skill":
    return [
      _planned_click("Open skills menu", "assets/buttons/skills_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
      _planned_click("Scan skill rows", region_key="SCROLLING_SKILL_SCREEN_BBOX", note="OCR and template scan only"),
      _planned_click("Confirm selected skills", "assets/buttons/confirm_btn.png"),
      _planned_click("Learn selected skills", "assets/buttons/learn_btn.png"),
      _planned_click("Exit skill screen", "assets/buttons/back_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
    ]
  if action_func == "check_inventory":
    return [
      _planned_click("Open use-items inventory", region_key="SCREEN_BOTTOM_BBOX", note="Locate the Trackblazer use-items entry button"),
      _planned_click("Scan inventory item rows", region_key="MANT_INVENTORY_ITEMS_REGION", note="OCR and native-scale template scan only"),
      _planned_click("Verify inventory controls", region_key="GAME_WINDOW_BBOX", note="Check use/close button visibility"),
      _planned_click("Close inventory", note="Dismiss the inventory overlay after scan"),
    ]
  if action_func == "execute_training_items":
    commit_mode = _action_value(action, "commit_mode") or "dry_run"
    clicks = [
      _planned_click("Open use-items inventory", region_key="SCREEN_BOTTOM_BBOX", note="Locate the Trackblazer use-items entry button"),
      _planned_click("Scan inventory item rows", region_key="MANT_INVENTORY_ITEMS_REGION", note="Pair items to increment controls"),
    ]
    if commit_mode == "dry_run":
      clicks.append(_planned_click("Detect controls (no increment clicks)", note="Simulated — no destructive clicks in dry_run"))
      clicks.append(_planned_click("Close inventory", note=f"commit_mode={commit_mode}"))
    else:
      clicks.append(_planned_click("Increment Vita 65", note="Select one Vita 65"))
      clicks.append(_planned_click("Increment Reset Whistle", note="Select one Reset Whistle"))
      clicks.append(_planned_click("Verify confirm-use controls", note="Ensure confirm/cancel controls are available"))
      clicks.append(_planned_click("Press confirm-use", note=f"commit_mode={commit_mode}"))
    return clicks
  if action_func == "check_shop":
    return [
      _planned_click("Open Trackblazer shop", region_key="GAME_WINDOW_BBOX", note="Locate the shop entry button"),
      _planned_click("Scan shop coin display", region_key="MANT_SHOP_COIN_REGION", note="OCR coin count"),
      _planned_click("Scan shop item rows", region_key="GAME_WINDOW_BBOX", note="Template scan for visible shop stock"),
      _planned_click("Close shop", note="Dismiss the shop overlay after scan"),
    ]
  return []


def build_review_planned_actions(state_obj, action, planner_state=None) -> Dict[str, Any]:
  planner_state = planner_state if isinstance(planner_state, dict) else {}
  legacy_shared_plan = planner_state.get("legacy_shared_plan") or {}
  inventory_plan = legacy_shared_plan.get("inventory_scan") or {}
  shop_plan = legacy_shared_plan.get("shop_scan") or {}
  would_buy = list(planner_state.get("shop_buy_plan") or legacy_shared_plan.get("would_buy") or [])
  would_use = list(planner_state.get("pre_action_items") or legacy_shared_plan.get("would_use") or [])
  deferred_use = list(planner_state.get("deferred_use") or legacy_shared_plan.get("deferred_use") or [])

  race_decision = _action_value(action, "trackblazer_race_decision", {}) or {}
  rival_scout = _action_value(action, "rival_scout", {}) or {}
  race_planned = {}
  race_check = {}
  race_entry_gate = {}
  race_scout_planned = {}
  rival_indicator_detected = (state_obj or {}).get("rival_indicator_detected")
  forced_climax_race_day = bool((state_obj or {}).get("trackblazer_climax_race_day"))
  scheduled_race = bool(_action_value(action, "scheduled_race") or _action_value(action, "trackblazer_lobby_scheduled_race"))
  lobby_scheduled_race = bool((state_obj or {}).get("trackblazer_lobby_scheduled_race") or _action_value(action, "trackblazer_lobby_scheduled_race"))
  scheduled_race_source = None
  if lobby_scheduled_race:
    scheduled_race_source = "lobby_button"
  elif scheduled_race:
    scheduled_race_source = "config_schedule"
  if rival_indicator_detected is not None or forced_climax_race_day or scheduled_race:
    race_check = {
      "phase": "collecting_race_state",
      "sub_phase": (
        "check_scheduled_race"
        if scheduled_race else
        ("check_rival_indicator" if not forced_climax_race_day else "check_forced_race_day")
      ),
      "method": (
        "scheduled_race_signal"
        if scheduled_race else
        ("lobby_race_button_indicator" if not forced_climax_race_day else "climax_race_day_banner_or_button")
      ),
      "rival_indicator_detected": bool(rival_indicator_detected),
      "forced_climax_race_day": forced_climax_race_day,
      "forced_climax_race_day_banner": bool((state_obj or {}).get("trackblazer_climax_race_day_banner")),
      "forced_climax_race_day_button": bool((state_obj or {}).get("trackblazer_climax_race_day_button")),
      "scheduled_race": scheduled_race,
      "scheduled_race_source": scheduled_race_source,
      "lobby_scheduled_race_detected": lobby_scheduled_race,
      "scout_required": bool(_action_func(action) == "do_race" and _action_value(action, "prefer_rival_race")),
    }
  if isinstance(race_decision, dict) and race_decision:
    race_info = race_decision.get("race_tier_info") or {}
    race_planned = {
      "should_race": race_decision.get("should_race"),
      "reason": race_decision.get("reason"),
      "training_total_stats": race_decision.get("training_total_stats"),
      "training_score": race_decision.get("training_score"),
      "is_summer": race_decision.get("is_summer"),
      "g1_forced": race_decision.get("g1_forced"),
      "prefer_rival_race": race_decision.get("prefer_rival_race"),
      "fallback_non_rival_race": race_decision.get("fallback_non_rival_race"),
      "prefer_rest_over_weak_training": race_decision.get("prefer_rest_over_weak_training"),
      "forced_race_day": race_decision.get("forced_race_day"),
      "race_tier_target": race_decision.get("race_tier_target"),
      "race_name": race_decision.get("race_name"),
      "race_available": race_decision.get("race_available"),
      "rival_indicator": race_decision.get("rival_indicator"),
      "available_grades": race_info.get("available_grades"),
      "best_grade": race_info.get("best_grade"),
      "race_count": race_info.get("race_count"),
      "scheduled_race": scheduled_race,
      "scheduled_race_source": scheduled_race_source,
    }
  if scheduled_race:
    race_entry_gate = {
      "opens_from_lobby": True,
      "consecutive_warning_template": constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive"),
      "consecutive_warning_ok_template": constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive_ok"),
      "warning_meaning": "This scheduled race would become the third consecutive race",
      "ok_action": "continue_to_race_list",
      "cancel_action": "return_to_lobby",
      "expected_branch": "continue_to_race_list",
      "scheduled_race": True,
      "scheduled_race_source": scheduled_race_source,
      "force_accept_warning": True,
    }
  elif isinstance(race_decision, dict) and race_decision and (_action_func(action) == "do_race" or race_decision.get("should_race")):
    rest_promoted_optional_race = bool(
      race_decision.get("prefer_rival_race")
      and _action_value(action, "_rival_fallback_func") == "do_rest"
      and not _action_value(action, "scheduled_race")
      and not _action_value(action, "trackblazer_lobby_scheduled_race")
      and not _action_value(action, "is_race_day")
    )
    if race_decision.get("fallback_non_rival_race") or rest_promoted_optional_race:
      expected_branch = "return_to_lobby"
    elif race_decision.get("g1_forced") or race_decision.get("should_race"):
      expected_branch = "continue_to_race_list"
    else:
      expected_branch = "return_to_lobby"
    race_entry_gate = {
      "opens_from_lobby": True,
      "consecutive_warning_template": constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive"),
      "consecutive_warning_ok_template": constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive_ok"),
      "warning_meaning": "This race would become the third consecutive race",
      "ok_action": "continue_to_race_list",
      "cancel_action": "return_to_lobby",
      "expected_branch": expected_branch,
    }
  if isinstance(rival_scout, dict) and rival_scout:
    race_scout_planned = {
      "phase": "scouting_rival_race",
      "executed": True,
      "rival_found": rival_scout.get("rival_found"),
      "selected_race_name": rival_scout.get("race_name"),
      "selected_match_count": rival_scout.get("match_count"),
      "selected_grade": rival_scout.get("grade"),
      "reverted_to_training": bool(rival_scout.get("rival_found") is False),
    }
  elif _action_value(action, "prefer_rival_race"):
    race_scout_planned = {
      "phase": "scouting_rival_race",
      "executed": False,
      "status": "pending_execute_commit",
      "reason": "Full rival scout only runs after commit.",
    }

  return {
    "race_check": race_check,
    "race_decision": race_planned,
    "race_entry_gate": race_entry_gate,
    "race_scout": race_scout_planned,
    "inventory_scan": inventory_plan,
    "would_use": would_use,
    "would_use_context": planner_state.get("item_use_context") or legacy_shared_plan.get("would_use_context") or {},
    "deferred_use": deferred_use,
    "shop_scan": shop_plan,
    "would_buy": would_buy,
  }


def _turn_discussion_diff_lines(legacy_text: str, planner_text: str, limit: int = 80) -> List[str]:
  diff_lines = list(
    difflib.unified_diff(
      legacy_text.splitlines(),
      planner_text.splitlines(),
      fromfile="legacy",
      tofile="planner",
      lineterm="",
    )
  )
  return diff_lines[: max(0, int(limit))]


def get_turn_plan(state_obj, action, planner_state=None, limit=8) -> TurnPlan:
  planner_state = (
    planner_state if isinstance(planner_state, dict) else
    (plan_once(state_obj, action, limit=limit) if isinstance(state_obj, dict) else {})
  )
  return TurnPlan.from_snapshot(dict((planner_state or {}).get("turn_plan") or {}))


def apply_turn_plan_action_payload(action, turn_plan: TurnPlan):
  if not hasattr(action, "__setitem__"):
    return action

  item_plan = dict((turn_plan.item_plan if turn_plan else {}) or {})
  shop_plan = dict((turn_plan.shop_plan if turn_plan else {}) or {})
  execution_payload = dict(item_plan.get("execution_payload") or {})
  action_mutations = dict(execution_payload.get("action_mutations") or {})
  for key, value in action_mutations.items():
    action[key] = copy.deepcopy(value)
  if "trackblazer_shop_buy_plan" not in action_mutations:
    action["trackblazer_shop_buy_plan"] = copy.deepcopy(list(shop_plan.get("would_buy") or []))
  return action


def update_turn_discussion_dual_run(state_obj, snapshot_context, legacy_planned_actions=None) -> Dict[str, Any]:
  if not isinstance(state_obj, dict):
    return {}
  planner_state = state_obj.get(PLANNER_STATE_KEY) or {}
  if not planner_state:
    return {}

  dual_run = copy.deepcopy(planner_state.get("dual_run") or {})
  turn_plan_snapshot = planner_state.get("turn_plan") or {}
  turn_plan = TurnPlan.from_snapshot(turn_plan_snapshot)
  legacy_planned_actions = legacy_planned_actions if isinstance(legacy_planned_actions, dict) else dict(planner_state.get("legacy_shared_plan") or {})

  legacy_text = render_turn_discussion(snapshot_context or {}, legacy_planned_actions)
  planner_text = turn_plan.to_turn_discussion(snapshot_context or {})
  match = legacy_text == planner_text
  diff_lines = [] if match else _turn_discussion_diff_lines(legacy_text, planner_text)
  comparison = {
    "mode": "read_only",
    "match": match,
    "legacy_hash": _hash_payload(legacy_text),
    "planner_hash": _hash_payload(planner_text),
    "diverged_keys": [] if match else ["turn_discussion_text"],
    "notes": (
      "Planner dual-run is hydrated from cached state only; no additional inventory/shop/skills traversal."
      if match else
      "Planner dual-run discussion diverged from legacy text. Review diff_lines for the exact mismatch."
    ),
    "legacy_turn_discussion": legacy_text,
    "planner_turn_discussion": planner_text,
    "diff_lines": diff_lines,
  }
  dual_run["comparison"] = comparison
  planner_state["dual_run"] = dual_run
  state_obj[PLANNER_STATE_KEY] = planner_state
  return comparison


def plan_once(state_obj, action, limit=8) -> Dict[str, Any]:
  if not isinstance(state_obj, dict):
    return {}

  runtime_state = ensure_planner_runtime_state(state_obj)
  turn_key = _turn_key(state_obj)
  state_key = _hash_payload(_state_signature(state_obj))
  observation_id = state_key
  action_key = _hash_payload(_action_signature(action))
  skill_context_key = _hash_payload(_skill_context_signature(state_obj))
  freshness = PlannerFreshness(
    turn_key=turn_key,
    observation_id=observation_id,
    state_key=state_key,
    action_key=action_key,
    skill_context_key=skill_context_key,
  )

  existing = state_obj.get(PLANNER_STATE_KEY) or {}
  existing_freshness = dict(existing.get("freshness") or {})
  if (
    existing
    and int(existing.get("version") or 0) == PLANNER_VERSION
    and existing_freshness == freshness.to_dict()
  ):
    return existing

  inventory, inventory_summary, inventory_flow, inventory_source = _resolve_inventory_source(state_obj)
  plan_state = dict(state_obj)
  plan_state["trackblazer_inventory"] = inventory
  plan_state["trackblazer_inventory_summary"] = inventory_summary

  held_quantities = dict((inventory_summary or {}).get("held_quantities") or {})
  shop_items = list(state_obj.get("trackblazer_shop_items") or [])
  shop_summary = {
    **(state_obj.get("trackblazer_shop_summary") or {}),
    "year": state_obj.get("year"),
    "turn": state_obj.get("turn"),
  }
  effective_shop_items = get_effective_shop_items(
    policy=getattr(config, "TRACKBLAZER_SHOP_POLICY", None),
    year=state_obj.get("year"),
    turn=state_obj.get("turn"),
  )
  shop_buy_plan = _candidate_shop_buys(
    effective_shop_items,
    shop_items=shop_items,
    shop_summary=shop_summary,
    held_quantities=held_quantities,
    limit=limit,
  )

  projected_state = _project_inventory(plan_state, shop_buy_plan) if shop_buy_plan else plan_state
  item_use_plan = plan_item_usage(
    policy=getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None),
    state_obj=projected_state,
    action=action,
    limit=limit,
  )
  execution_items, deferred_use, reassess_after_item_use = _attach_execution_item_plan(item_use_plan)
  item_execution_payload = _build_item_execution_payload(
    action,
    shop_buy_plan,
    execution_items,
    deferred_use,
    reassess_after_item_use,
    item_use_plan.get("context") or {},
  )
  reobserve_boundaries = _build_reobserve_boundaries(item_execution_payload)

  items_detected = list((inventory_summary or {}).get("items_detected") or [])
  if not items_detected and isinstance(inventory, dict):
    for item_key, item_entry in inventory.items():
      if not isinstance(item_entry, dict):
        continue
      held_quantity = item_entry.get("held_quantity")
      detected = bool(item_entry.get("detected"))
      try:
        held_quantity = int(held_quantity)
      except (TypeError, ValueError):
        held_quantity = 0
      if detected or held_quantity > 0:
        items_detected.append(item_key)

  projected_summary = copy.deepcopy(projected_state.get("trackblazer_inventory_summary") or {})
  item_context = dict(item_use_plan.get("context") or {})
  shop_flow = state_obj.get("trackblazer_shop_flow") or {}

  pending_skill_scan = BackgroundSkillScanState(
    **dict((runtime_state.get("pending_skill_scan") or {}))
  )
  pending_skill_scan.turn_key = pending_skill_scan.turn_key or turn_key
  pending_skill_scan.observation_id = pending_skill_scan.observation_id or observation_id
  pending_skill_scan.skill_context_key = pending_skill_scan.skill_context_key or skill_context_key
  if pending_skill_scan.status == "stale":
    pending_skill_scan.captured_shortlist_hash = pending_skill_scan.captured_shortlist_hash or _skill_shortlist_hash(skill_context_key)

  runtime_state["turn_key"] = turn_key
  runtime_state["latest_observation_id"] = observation_id
  runtime_state["pending_skill_scan"] = pending_skill_scan.to_dict()
  state_obj[PLANNER_RUNTIME_KEY] = runtime_state

  observed = hydrate_observed_turn_state(state_obj, action=action, planner_state={
    "freshness": freshness.to_dict(),
    "inventory_source": inventory_source,
    "shop_buy_plan": shop_buy_plan,
    "pre_action_items": execution_items,
    "deferred_use": deferred_use,
    "reassess_after_item_use": bool(reassess_after_item_use),
    "runtime": runtime_state,
  })
  derived = derive_turn_state(observed, planner_state={
    "inventory_source": inventory_source,
    "shop_buy_plan": shop_buy_plan,
    "pre_action_items": execution_items,
    "reassess_after_item_use": bool(reassess_after_item_use),
    "turn_plan": {"planner_metadata": {"runtime": runtime_state}},
  }, state_obj=state_obj, action=action)
  candidates = enumerate_candidate_actions(observed, derived, state_obj=state_obj, action=action)

  legacy_base_plan = {
    "race_check": {},
    "race_decision": {},
    "race_entry_gate": {},
    "race_scout": {},
    "inventory_scan": {
      "status": _inventory_scan_status(inventory_flow),
      "reason": (inventory_flow or {}).get("reason") or "",
      "button_visible": (inventory_flow or {}).get("use_training_items_button_visible"),
      "items_detected": items_detected,
      "held_quantities": held_quantities,
      "actionable_items": list((inventory_summary or {}).get("actionable_items") or []),
    },
    "would_use": execution_items,
    "would_use_context": item_context,
    "deferred_use": deferred_use,
    "shop_scan": {
      "status": _shop_scan_status(shop_flow),
      "reason": (shop_flow or {}).get("reason") or "",
      "shop_coins": shop_summary.get("shop_coins", state_obj.get("shop_coins")),
      "items_detected": (state_obj.get("trackblazer_shop_summary") or {}).get("items_detected") or shop_items,
      "not_purchasable": sorted(
        set((state_obj.get("trackblazer_shop_summary") or {}).get("items_detected") or shop_items or [])
        - set((state_obj.get("trackblazer_shop_summary") or {}).get("purchasable_items") or shop_items or [])
      ),
    },
    "would_buy": shop_buy_plan,
  }
  review_planned_actions = build_review_planned_actions(
    state_obj,
    action,
    planner_state={
      "legacy_shared_plan": legacy_base_plan,
      "item_use_context": item_context,
    },
  )
  ranked_trainings = build_ranked_training_snapshot(
    state_obj=state_obj,
    available_trainings=copy.deepcopy(action.get("available_trainings") or {}) if hasattr(action, "get") else {},
    training_function=action.get("training_function") if hasattr(action, "get") else None,
  )

  turn_plan = TurnPlan(
    version=PLANNER_VERSION,
    decision_path="legacy",
    freshness=freshness,
    selected_candidate=candidates[0].to_dict() if candidates else {},
    candidate_ranking=[candidate.to_dict() for candidate in candidates],
    shop_plan={
      "would_buy": shop_buy_plan,
      "shop_summary": copy.deepcopy(shop_summary),
      "effective_shop_items": copy.deepcopy(effective_shop_items),
      "scan": {
        "status": _shop_scan_status(shop_flow),
        "reason": (shop_flow or {}).get("reason") or "",
        "shop_coins": shop_summary.get("shop_coins", state_obj.get("shop_coins")),
        "items_detected": (state_obj.get("trackblazer_shop_summary") or {}).get("items_detected") or shop_items,
        "not_purchasable": sorted(
          set((state_obj.get("trackblazer_shop_summary") or {}).get("items_detected") or shop_items or [])
          - set((state_obj.get("trackblazer_shop_summary") or {}).get("purchasable_items") or shop_items or [])
        ),
      },
    },
    item_plan={
      "item_use_plan": copy.deepcopy(item_use_plan),
      "pre_action_items": copy.deepcopy(execution_items),
      "deferred_use": copy.deepcopy(deferred_use),
      "reassess_after_item_use": bool(reassess_after_item_use),
      "context": copy.deepcopy(item_context),
      "execution_payload": copy.deepcopy(item_execution_payload),
      "subgraph": _build_item_plan_subgraph(item_execution_payload),
      "selected_action_binding": {
        "func": getattr(action, "func", None),
        "training_name": action.get("training_name") if hasattr(action, "get") else None,
        "race_name": action.get("race_name") if hasattr(action, "get") else None,
      },
      "reassess_boundary": {
        "required": bool(reassess_after_item_use),
        "trigger_items": [entry.get("key") for entry in execution_items if isinstance(entry, dict) and entry.get("key")],
        "reason": (item_execution_payload.get("reassess_transition") or {}).get("reason"),
        "transition_kind": (item_execution_payload.get("reassess_transition") or {}).get("transition_kind"),
      },
    },
    reobserve_boundaries=copy.deepcopy(reobserve_boundaries),
    inventory_snapshot={
      "source": inventory_source,
      "scan": {
        "status": _inventory_scan_status(inventory_flow),
        "reason": (inventory_flow or {}).get("reason") or "",
        "button_visible": (inventory_flow or {}).get("use_training_items_button_visible"),
        "items_detected": items_detected,
        "held_quantities": held_quantities,
        "actionable_items": list((inventory_summary or {}).get("actionable_items") or []),
      },
      "pre_plan_summary": copy.deepcopy(inventory_summary),
      "projected_post_buy_summary": projected_summary,
    },
    timing={
      "inventory": copy.deepcopy((inventory_flow or {}).get("timing") or {}),
      "shop": copy.deepcopy((shop_flow or {}).get("timing") or {}),
      "skill": copy.deepcopy(((state_obj.get("skill_purchase_flow") or {}).get("timing")) or {}),
    },
    debug_summary={
      "shop_item_count": len(shop_buy_plan),
      "item_candidate_count": len(list(item_use_plan.get("candidates") or [])),
      "execution_item_count": len(execution_items),
      "ranked_training_count": len(ranked_trainings),
      "inventory_source": inventory_source,
    },
    planner_metadata={
      "planner_version": PLANNER_VERSION,
      "decision_path": "legacy",
      "inventory_source": inventory_source,
      "runtime": copy.deepcopy(runtime_state),
    },
    review_context={
      "selected_action": _build_selected_action_review_context(
        action,
        pre_action_items=execution_items,
        reassess_after_item_use=reassess_after_item_use,
      ),
      "ranked_trainings": copy.deepcopy(ranked_trainings),
    },
    legacy_shared_plan=review_planned_actions,
    step_sequence=_build_step_sequence(
      state_obj,
      action,
      shop_buy_plan,
      item_execution_payload,
    ),
  )
  turn_plan.review_context = {
    **dict(turn_plan.review_context or {}),
    "planned_clicks": turn_plan.to_planned_clicks(),
  }

  plan_payload = {
    "version": PLANNER_VERSION,
    "freshness": freshness.to_dict(),
    "turn_plan": turn_plan.to_snapshot(),
    "shop_buy_plan": shop_buy_plan,
    "item_use_plan": copy.deepcopy(item_use_plan),
    "pre_action_items": execution_items,
    "deferred_use": deferred_use,
    "item_use_context": item_context,
    "reassess_after_item_use": bool(reassess_after_item_use),
    "inventory_source": inventory_source,
    "inventory_source_summary": copy.deepcopy(inventory_summary),
    "projected_inventory_summary": projected_summary,
    "legacy_shared_plan": review_planned_actions,
    "dual_run": {
      "observed": observed.to_dict(),
      "derived": derived.to_dict(),
      "candidates": [candidate.to_dict() for candidate in candidates],
      "turn_plan": turn_plan.to_snapshot(),
      "comparison": {
        "mode": "read_only",
        "match": None,
        "legacy_hash": "",
        "planner_hash": "",
        "diverged_keys": [],
        "notes": "Awaiting Turn Discussion serialization context from build_review_snapshot().",
      },
    },
  }
  state_obj[PLANNER_STATE_KEY] = plan_payload
  return plan_payload
