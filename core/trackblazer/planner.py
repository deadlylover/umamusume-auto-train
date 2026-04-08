from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List, Tuple

import core.config as config
from core.trackblazer.candidates import enumerate_candidate_actions
from core.trackblazer.derive import derive_turn_state
from core.trackblazer_item_use import plan_item_usage
from core.trackblazer.observe import hydrate_observed_turn_state
from core.trackblazer_shop import get_dynamic_shop_limits, get_effective_shop_items
from core.trackblazer.models import (
  BackgroundSkillScanState,
  PlannerFreshness,
  PlannerRuntimeState,
  TurnPlan,
)


PLANNER_STATE_KEY = "trackblazer_planner_state"
PLANNER_RUNTIME_KEY = "trackblazer_planner_runtime"
PLANNER_VERSION = 1


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
    "training_name": action.get("training_name"),
    "training_function": action.get("training_function"),
    "race_name": action.get("race_name"),
    "prefer_rival_race": action.get("prefer_rival_race"),
    "scheduled_race": action.get("scheduled_race"),
    "trackblazer_lobby_scheduled_race": action.get("trackblazer_lobby_scheduled_race"),
    "trackblazer_climax_race_day": action.get("trackblazer_climax_race_day"),
    "trackblazer_race_decision": action.get("trackblazer_race_decision"),
    "trackblazer_race_lookahead": action.get("trackblazer_race_lookahead"),
    "is_race_day": action.get("is_race_day"),
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
  if existing and existing_freshness == freshness.to_dict():
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
    "reassess_after_item_use": bool(reassess_after_item_use),
  })
  derived = derive_turn_state(observed, planner_state={
    "inventory_source": inventory_source,
    "shop_buy_plan": shop_buy_plan,
    "pre_action_items": execution_items,
    "reassess_after_item_use": bool(reassess_after_item_use),
    "turn_plan": {"planner_metadata": {"runtime": runtime_state}},
  })
  candidates = enumerate_candidate_actions(observed, derived)

  legacy_shared_plan = {
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
    },
    item_plan={
      "item_use_plan": copy.deepcopy(item_use_plan),
      "pre_action_items": copy.deepcopy(execution_items),
      "deferred_use": copy.deepcopy(deferred_use),
      "reassess_after_item_use": bool(reassess_after_item_use),
      "context": copy.deepcopy(item_context),
    },
    inventory_snapshot={
      "source": inventory_source,
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
      "inventory_source": inventory_source,
    },
    planner_metadata={
      "planner_version": PLANNER_VERSION,
      "decision_path": "legacy",
      "inventory_source": inventory_source,
      "runtime": copy.deepcopy(runtime_state),
    },
    legacy_shared_plan=legacy_shared_plan,
  )

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
    "legacy_shared_plan": legacy_shared_plan,
    "dual_run": {
      "observed": observed.to_dict(),
      "derived": derived.to_dict(),
      "candidates": [candidate.to_dict() for candidate in candidates],
      "turn_plan": turn_plan.to_snapshot(),
      "comparison": {
        "mode": "read_only",
        "match": True,
        "legacy_hash": _hash_payload(legacy_shared_plan),
        "planner_hash": _hash_payload(turn_plan.to_planned_actions()),
        "diverged_keys": [],
        "notes": "Planner dual-run is hydrated from cached state only; no additional inventory/shop/skills traversal.",
      },
    },
  }
  state_obj[PLANNER_STATE_KEY] = plan_payload
  return plan_payload
