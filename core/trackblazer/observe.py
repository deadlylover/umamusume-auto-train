from __future__ import annotations

import copy
from typing import Any, Dict

from core.trackblazer.models import ObservedTurnState


def _status_entry(value, *, missing_reason="missing_from_state_obj"):
  present = bool(value) if not isinstance(value, dict) else bool(value)
  return {
    "present": present,
    "stale": not present,
    "reason": "" if present else missing_reason,
  }


def _key_status(source, *keys, missing_reason="missing_from_state_obj", require_all=False):
  present = all(key in source for key in keys) if require_all else any(key in source for key in keys)
  return {
    "present": present,
    "stale": not present,
    "reason": "" if present else missing_reason,
  }


def _derive_missing_inputs(observation_status):
  return sorted(
    key for key, value in (observation_status or {}).items()
    if isinstance(value, dict) and value.get("stale")
  )


def hydrate_observed_turn_state(state_obj, action=None, planner_state=None) -> ObservedTurnState:
  state_obj = state_obj if isinstance(state_obj, dict) else {}
  planner_state = planner_state if isinstance(planner_state, dict) else {}
  selected_action = action if hasattr(action, "get") else None
  inventory_current = copy.deepcopy(state_obj.get("trackblazer_inventory") or {})
  inventory_current_summary = copy.deepcopy(state_obj.get("trackblazer_inventory_summary") or {})
  inventory_pre_shop = copy.deepcopy(state_obj.get("trackblazer_inventory_pre_shop") or state_obj.get("trackblazer_inventory") or {})
  inventory_pre_shop_summary = copy.deepcopy(state_obj.get("trackblazer_inventory_pre_shop_summary") or state_obj.get("trackblazer_inventory_summary") or {})
  inventory_flow = copy.deepcopy(state_obj.get("trackblazer_inventory_flow") or {})
  inventory_pre_shop_flow = copy.deepcopy(state_obj.get("trackblazer_inventory_pre_shop_flow") or {})
  shop_items = list(state_obj.get("trackblazer_shop_items") or [])
  shop_summary = copy.deepcopy(state_obj.get("trackblazer_shop_summary") or {})
  shop_flow = copy.deepcopy(state_obj.get("trackblazer_shop_flow") or {})
  skill_purchase_check = copy.deepcopy(state_obj.get("skill_purchase_check") or {})
  skill_purchase_flow = copy.deepcopy(state_obj.get("skill_purchase_flow") or {})
  skill_purchase_scan = copy.deepcopy(state_obj.get("skill_purchase_scan") or {})
  skill_purchase_plan = copy.deepcopy(state_obj.get("skill_purchase_plan") or {})
  training_results = copy.deepcopy(state_obj.get("training_results") or {})
  available_trainings = copy.deepcopy(training_results)
  planner_pre_action_items = copy.deepcopy(planner_state.get("pre_action_items") or [])
  planner_reassess_after_item_use = bool(planner_state.get("reassess_after_item_use"))
  inventory_snapshot = {
    "pre_shop": inventory_pre_shop,
    "pre_shop_summary": inventory_pre_shop_summary,
    "current": inventory_current,
    "current_summary": inventory_current_summary,
    "projected_post_buy_summary": copy.deepcopy(planner_state.get("projected_inventory_summary") or {}),
  }
  observation_status = {
    "energy": _key_status(state_obj, "energy_level", "max_energy", require_all=True),
    "mood": _key_status(state_obj, "current_mood"),
    "status": _key_status(state_obj, "status_effect_names"),
    "training": _status_entry(training_results),
    "items": _key_status(
      state_obj,
      "trackblazer_inventory",
      "trackblazer_inventory_summary",
      "trackblazer_inventory_flow",
    ),
    "shop": _key_status(
      state_obj,
      "trackblazer_shop_items",
      "trackblazer_shop_summary",
      "trackblazer_shop_flow",
    ),
    "skills": _key_status(
      state_obj,
      "skill_purchase_check",
      "skill_purchase_flow",
      "skill_purchase_scan",
      "skill_purchase_plan",
    ),
    "race_opportunity": _key_status(
      state_obj,
      "rival_indicator_detected",
      "race_mission_available",
      "trackblazer_lobby_scheduled_race",
      "trackblazer_climax_locked_race",
      "trackblazer_climax_race_day",
    ),
    "lookahead": _key_status(state_obj, "year", "turn", require_all=True),
  }
  missing_inputs = _derive_missing_inputs(observation_status)
  planner_race_payload = (selected_action or {}).get("trackblazer_planner_race") or {}
  planner_race_payload = planner_race_payload if isinstance(planner_race_payload, dict) else {}
  planner_warning_outcome = (selected_action or {}).get("planner_warning_outcome") or {}
  planner_warning_outcome = planner_warning_outcome if isinstance(planner_warning_outcome, dict) else {}
  planner_warning_owned = bool(planner_race_payload or ((selected_action or {}).get("planner_race_warning_policy") or {}))
  if planner_warning_outcome.get("cancelled"):
    consecutive_warning_cancelled = True
    consecutive_warning_force_rest = bool(planner_warning_outcome.get("force_rest"))
    consecutive_warning_cancel_reason = planner_warning_outcome.get("reason")
  elif planner_warning_owned:
    consecutive_warning_cancelled = False
    consecutive_warning_force_rest = False
    consecutive_warning_cancel_reason = None
  else:
    consecutive_warning_cancelled = bool((selected_action or {}).get("_consecutive_warning_cancelled"))
    consecutive_warning_force_rest = bool((selected_action or {}).get("_consecutive_warning_force_rest"))
    consecutive_warning_cancel_reason = (selected_action or {}).get("_consecutive_warning_cancel_reason")
  rival_fallback_action = planner_race_payload.get("fallback_action") or {}
  rival_fallback_action = rival_fallback_action if isinstance(rival_fallback_action, dict) else {}
  rival_fallback_func = (
    rival_fallback_action.get("func")
    if planner_race_payload else
    (selected_action or {}).get("_rival_fallback_func")
  )
  data: Dict[str, Any] = {
    "year": state_obj.get("year"),
    "turn": state_obj.get("turn"),
    "energy_level": state_obj.get("energy_level"),
    "max_energy": state_obj.get("max_energy"),
    "current_mood": state_obj.get("current_mood"),
    "current_stats": copy.deepcopy(state_obj.get("current_stats") or {}),
    "criteria": state_obj.get("criteria"),
    "date_event_available": state_obj.get("date_event_available"),
    "race_mission_available": state_obj.get("race_mission_available"),
    "aptitudes": copy.deepcopy(state_obj.get("aptitudes") or {}),
    "status_effect_names": list(state_obj.get("status_effect_names") or []),
    "state_validation": copy.deepcopy(state_obj.get("state_validation") or {}),
    "rival_indicator_detected": state_obj.get("rival_indicator_detected"),
    "trackblazer_climax": bool(state_obj.get("trackblazer_climax")),
    "trackblazer_climax_locked_race": bool(state_obj.get("trackblazer_climax_locked_race")),
    "trackblazer_climax_race_day": bool(state_obj.get("trackblazer_climax_race_day")),
    "trackblazer_climax_race_day_banner": bool(state_obj.get("trackblazer_climax_race_day_banner")),
    "trackblazer_climax_race_day_button": bool(state_obj.get("trackblazer_climax_race_day_button")),
    "trackblazer_trainings_remaining_upper_bound": state_obj.get("trackblazer_trainings_remaining_upper_bound"),
    "trackblazer_buff_active": bool(state_obj.get("trackblazer_buff_active")),
    "trackblazer_allow_buff_override": bool(state_obj.get("trackblazer_allow_buff_override")),
    "trackblazer_lobby_scheduled_race": bool(state_obj.get("trackblazer_lobby_scheduled_race")),
    "shop_items": shop_items,
    "shop_summary": shop_summary,
    "shop_flow": shop_flow,
    "inventory": inventory_snapshot,
    "inventory_controls": copy.deepcopy(state_obj.get("trackblazer_inventory_controls") or {}),
    "inventory_flow": inventory_flow,
    "inventory_pre_shop_flow": inventory_pre_shop_flow,
    "skill_purchase_check": skill_purchase_check,
    "skill_purchase_flow": skill_purchase_flow,
    "skill_purchase_scan": skill_purchase_scan,
    "skill_purchase_plan": skill_purchase_plan,
    "training_results": training_results,
    "available_trainings": available_trainings,
    "observation_status": observation_status,
    "missing_inputs": missing_inputs,
    "legacy_seed_metadata": {
      "func": getattr(action, "func", None),
      "training_name": selected_action.get("training_name") if selected_action else None,
      "training_function": selected_action.get("training_function") if selected_action else None,
      "race_name": selected_action.get("race_name") if selected_action else None,
      "race_image_path": selected_action.get("race_image_path") if selected_action else None,
      "race_grade_target": selected_action.get("race_grade_target") if selected_action else None,
      "prefer_rival_race": selected_action.get("prefer_rival_race") if selected_action else None,
      "fallback_non_rival_race": bool(selected_action.get("fallback_non_rival_race")) if selected_action else False,
      "is_race_day": bool(selected_action.get("is_race_day")) if selected_action else False,
      "scheduled_race": bool(selected_action.get("scheduled_race")) if selected_action else False,
      "trackblazer_lobby_scheduled_race": bool(selected_action.get("trackblazer_lobby_scheduled_race")) if selected_action else False,
      "trackblazer_climax_race_day": bool(selected_action.get("trackblazer_climax_race_day")) if selected_action else False,
      "race_mission_available": bool(selected_action.get("race_mission_available")) if selected_action else False,
      "rest_promoted_to_training": bool(selected_action.get("_trackblazer_rest_promoted_to_training")) if selected_action else False,
      "consecutive_warning_cancelled": consecutive_warning_cancelled,
      "consecutive_warning_force_rest": consecutive_warning_force_rest,
      "consecutive_warning_cancel_reason": consecutive_warning_cancel_reason,
      "rival_fallback_func": rival_fallback_func,
      "training_data": copy.deepcopy(selected_action.get("training_data") or {}) if selected_action else {},
      "trackblazer_pre_action_items": (
        copy.deepcopy(selected_action.get("trackblazer_pre_action_items") or [])
        if selected_action and selected_action.get("trackblazer_pre_action_items") is not None else
        planner_pre_action_items
      ),
      "trackblazer_reassess_after_item_use": (
        bool(selected_action.get("trackblazer_reassess_after_item_use"))
        if selected_action and selected_action.get("trackblazer_reassess_after_item_use") is not None else
        planner_reassess_after_item_use
      ),
      "trackblazer_race_decision": copy.deepcopy(selected_action.get("trackblazer_race_decision") or {}) if selected_action else {},
      "trackblazer_race_lookahead": copy.deepcopy(selected_action.get("trackblazer_race_lookahead") or {}) if selected_action else {},
      "rival_scout": copy.deepcopy(selected_action.get("rival_scout") or {}) if selected_action else {},
      "available_actions": list(getattr(action, "available_actions", []) or []),
    },
    "planner_state": {
      "freshness": copy.deepcopy(planner_state.get("freshness") or {}),
      "inventory_source": planner_state.get("inventory_source"),
      "shop_buy_plan": copy.deepcopy(planner_state.get("shop_buy_plan") or []),
      "pre_action_items": copy.deepcopy(planner_state.get("pre_action_items") or []),
      "deferred_use": copy.deepcopy(planner_state.get("deferred_use") or []),
      "reassess_after_item_use": bool(planner_state.get("reassess_after_item_use")),
      "runtime": copy.deepcopy(planner_state.get("runtime") or {}),
    },
    "dual_run_source": "state_obj_cached_only",
  }
  return ObservedTurnState(data=data)
