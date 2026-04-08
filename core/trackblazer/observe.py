from __future__ import annotations

import copy
from typing import Any, Dict

from core.trackblazer.models import ObservedTurnState


def hydrate_observed_turn_state(state_obj, action=None, planner_state=None) -> ObservedTurnState:
  state_obj = state_obj if isinstance(state_obj, dict) else {}
  planner_state = planner_state if isinstance(planner_state, dict) else {}
  inventory_snapshot = {
    "pre_shop": copy.deepcopy(state_obj.get("trackblazer_inventory_pre_shop") or state_obj.get("trackblazer_inventory") or {}),
    "pre_shop_summary": copy.deepcopy(state_obj.get("trackblazer_inventory_pre_shop_summary") or state_obj.get("trackblazer_inventory_summary") or {}),
    "current": copy.deepcopy(state_obj.get("trackblazer_inventory") or {}),
    "current_summary": copy.deepcopy(state_obj.get("trackblazer_inventory_summary") or {}),
    "projected_post_buy_summary": copy.deepcopy(planner_state.get("projected_inventory_summary") or {}),
  }
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
    "rival_indicator_detected": state_obj.get("rival_indicator_detected"),
    "trackblazer_climax": bool(state_obj.get("trackblazer_climax")),
    "trackblazer_climax_locked_race": bool(state_obj.get("trackblazer_climax_locked_race")),
    "trackblazer_trainings_remaining_upper_bound": state_obj.get("trackblazer_trainings_remaining_upper_bound"),
    "trackblazer_lobby_scheduled_race": bool(
      state_obj.get("trackblazer_lobby_scheduled_race")
      or (action.get("trackblazer_lobby_scheduled_race") if hasattr(action, "get") else False)
    ),
    "shop_items": list(state_obj.get("trackblazer_shop_items") or []),
    "shop_summary": copy.deepcopy(state_obj.get("trackblazer_shop_summary") or {}),
    "shop_flow": copy.deepcopy(state_obj.get("trackblazer_shop_flow") or {}),
    "inventory": inventory_snapshot,
    "inventory_flow": copy.deepcopy(state_obj.get("trackblazer_inventory_flow") or {}),
    "inventory_pre_shop_flow": copy.deepcopy(state_obj.get("trackblazer_inventory_pre_shop_flow") or {}),
    "skill_purchase_check": copy.deepcopy(state_obj.get("skill_purchase_check") or {}),
    "skill_purchase_flow": copy.deepcopy(state_obj.get("skill_purchase_flow") or {}),
    "skill_purchase_scan": copy.deepcopy(state_obj.get("skill_purchase_scan") or {}),
    "skill_purchase_plan": copy.deepcopy(state_obj.get("skill_purchase_plan") or {}),
    "selected_action": {
      "func": getattr(action, "func", None),
      "training_name": action.get("training_name") if hasattr(action, "get") else None,
      "race_name": action.get("race_name") if hasattr(action, "get") else None,
      "prefer_rival_race": action.get("prefer_rival_race") if hasattr(action, "get") else None,
      "training_data": copy.deepcopy(action.get("training_data") or {}) if hasattr(action, "get") else {},
      "trackblazer_race_decision": copy.deepcopy(action.get("trackblazer_race_decision") or {}) if hasattr(action, "get") else {},
    },
    "planner_state": {
      "freshness": copy.deepcopy(planner_state.get("freshness") or {}),
      "inventory_source": planner_state.get("inventory_source"),
      "shop_buy_plan": copy.deepcopy(planner_state.get("shop_buy_plan") or []),
      "pre_action_items": copy.deepcopy(planner_state.get("pre_action_items") or []),
      "reassess_after_item_use": bool(planner_state.get("reassess_after_item_use")),
    },
    "dual_run_source": "state_obj_cached_only",
  }
  return ObservedTurnState(data=data)
