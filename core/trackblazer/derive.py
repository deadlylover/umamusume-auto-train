from __future__ import annotations

import copy
from typing import Any, Dict

import core.config as config
from core.trackblazer_item_use import _usage_context
from core.trackblazer.models import DerivedTurnState, ObservedTurnState
from core.trackblazer_race_logic import (
  get_optional_race_low_energy_override,
  get_race_lookahead_energy_advice,
)
from utils.log import warning


_SUMMER_TOKENS = ("early jul", "late jul", "early aug", "late aug")


def _safe_ratio(numerator, denominator):
  try:
    denominator = float(denominator)
    if denominator <= 0:
      return None
    return round(float(numerator) / denominator, 4)
  except (TypeError, ValueError, ZeroDivisionError):
    return None


def derive_turn_state(observed: ObservedTurnState, planner_state=None, state_obj=None, action=None) -> DerivedTurnState:
  observed_data = observed.to_dict()
  planner_state = planner_state if isinstance(planner_state, dict) else {}
  state_obj = state_obj if isinstance(state_obj, dict) else {}
  turn_label = str(observed_data.get("turn") or "").lower()
  training_data = (observed_data.get("selected_action") or {}).get("training_data") or {}
  score_tuple = training_data.get("score_tuple") or ()
  try:
    best_score = float(score_tuple[0]) if score_tuple else None
  except (TypeError, ValueError, IndexError):
    best_score = None

  stat_gains = training_data.get("stat_gains") or {}
  training_total = sum(
    int(value)
    for value in stat_gains.values()
    if isinstance(value, (int, float))
  )
  usage_context = {}
  derivation_warnings = []
  if hasattr(action, "get") and state_obj:
    try:
      usage_context = _usage_context(
        state_obj,
        action,
        policy=getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None),
      ) or {}
    except Exception as exc:
      warning(f"[TB_PLANNER] derive_turn_state usage_context failed: {exc}")
      derivation_warnings.append({
        "source": "usage_context",
        "error": str(exc),
      })
      usage_context = {}

  race_low_energy_override = {}
  race_lookahead = {}
  if state_obj:
    try:
      race_low_energy_override = get_optional_race_low_energy_override(state_obj) or {}
    except Exception as exc:
      warning(f"[TB_PLANNER] derive_turn_state race_low_energy_override failed: {exc}")
      derivation_warnings.append({
        "source": "race_low_energy_override",
        "error": str(exc),
      })
      race_low_energy_override = {}
    try:
      race_lookahead = get_race_lookahead_energy_advice(
        state_obj,
        getattr(config, "OPERATOR_RACE_SELECTOR", None),
      ) or {}
    except Exception as exc:
      warning(f"[TB_PLANNER] derive_turn_state race_lookahead failed: {exc}")
      derivation_warnings.append({
        "source": "race_lookahead",
        "error": str(exc),
      })
      race_lookahead = {}

  observation_status = copy.deepcopy(observed_data.get("observation_status") or {})
  missing_inputs = sorted(
    key for key, value in observation_status.items()
    if isinstance(value, dict) and value.get("stale")
  )
  selected_action = observed_data.get("selected_action") or {}
  data: Dict[str, Any] = {
    "turn_key": f"{observed_data.get('year') or '?'}|{observed_data.get('turn') or '?'}",
    "timeline_label": observed_data.get("turn"),
    "is_summer": any(token in turn_label for token in _SUMMER_TOKENS),
    "energy_ratio": _safe_ratio(observed_data.get("energy_level"), observed_data.get("max_energy")),
    "observation_status": observation_status,
    "missing_inputs": missing_inputs,
    "race_available_summary": {
      "rival_indicator": bool(observed_data.get("rival_indicator_detected")),
      "race_mission_available": bool(observed_data.get("race_mission_available")),
      "lobby_scheduled_race": bool(observed_data.get("trackblazer_lobby_scheduled_race")),
      "climax_locked_race": bool(observed_data.get("trackblazer_climax_locked_race")),
    },
    "training_value_summary": {
      "best_training_name": selected_action.get("training_name"),
      "best_score": best_score,
      "best_total": training_total,
      "failure_rate": training_data.get("failure"),
      "support_count": training_data.get("total_supports"),
      "rainbow_count": training_data.get("total_rainbow_friends"),
    },
    "action_summary": {
      "func": selected_action.get("func"),
      "race_name": selected_action.get("race_name"),
      "race_grade_target": selected_action.get("race_grade_target"),
      "prefer_rival_race": bool(selected_action.get("prefer_rival_race")),
      "fallback_non_rival_race": bool(selected_action.get("fallback_non_rival_race")),
      "is_race_day": bool(selected_action.get("is_race_day")),
      "scheduled_race": bool(selected_action.get("scheduled_race")),
      "trackblazer_lobby_scheduled_race": bool(selected_action.get("trackblazer_lobby_scheduled_race")),
      "trackblazer_climax_race_day": bool(selected_action.get("trackblazer_climax_race_day")),
      "race_mission_available": bool(selected_action.get("race_mission_available")),
      "rest_promoted_to_training": bool(selected_action.get("rest_promoted_to_training")),
      "consecutive_warning_cancelled": bool(selected_action.get("consecutive_warning_cancelled")),
      "consecutive_warning_force_rest": bool(selected_action.get("consecutive_warning_force_rest")),
      "consecutive_warning_cancel_reason": selected_action.get("consecutive_warning_cancel_reason"),
      "rival_fallback_func": selected_action.get("rival_fallback_func"),
    },
    "inventory_source": planner_state.get("inventory_source"),
    "shop_buy_count": len(planner_state.get("shop_buy_plan") or []),
    "pre_action_item_count": len(planner_state.get("pre_action_items") or []),
    "reassess_after_item_use": bool(planner_state.get("reassess_after_item_use")),
    "skill_scan_state": (((planner_state.get("turn_plan") or {}).get("planner_metadata") or {}).get("runtime") or {}).get("pending_skill_scan") or {},
    "usage_context_summary": {
      key: usage_context.get(key)
      for key in (
        "timeline_label",
        "timeline_index",
        "past_final_summer",
        "climax_window",
        "summer_window",
        "energy_level",
        "max_energy",
        "energy_deficit",
        "safe_energy_target",
        "held_vita_restore_total",
        "spendable_vita_restore_total",
        "held_reset_whistles",
        "held_vita_reaches_safe_energy",
        "spendable_vita_reaches_safe_energy",
        "action_func",
        "training_name",
        "training_score",
        "matching_stat_gain",
        "total_stat_gain",
        "failure_rate",
        "rainbow_count",
        "support_count",
        "failure_bypassed_by_items",
        "high_value_training",
        "very_high_value_training",
        "committed_value_training",
        "strong_burst_training",
        "weak_summer_training",
        "weak_climax_training",
        "energy_rescue",
        "commit_training_after_items",
        "training_survives_race_gate",
        "zero_energy_optional_race",
        "race_low_energy_vita_rescue",
        "scheduled_race_low_energy_vita_item_key",
        "race_lookahead_active",
        "race_lookahead_energy_item_key",
        "race_lookahead_safe_energy_target",
        "race_lookahead_reason",
        "held_energy_available",
        "held_charm_available",
        "affordable_shop_energy_available",
        "affordable_shop_charm_available",
        "has_followup_failsafe",
        "held_recovery_cover_available",
      )
      if key in usage_context
    },
    "race_logic_summary": {
      "low_energy_override": race_low_energy_override,
      "lookahead": race_lookahead,
    },
    "derivation_warnings": derivation_warnings,
  }
  return DerivedTurnState(data=data)
