from __future__ import annotations

import copy
from typing import Any, Dict


OPTIONAL_RACE_ACTION_FIELDS = (
  "race_name",
  "race_image_path",
  "race_mission_available",
  "prioritize_missions_over_g1",
  "scheduled_race",
  "trackblazer_lobby_scheduled_race",
  "hammer_spendable",
  "prefer_rival_race",
  "race_grade_target",
  "planner_race_warning_policy",
  "trackblazer_planner_race",
)

CONSECUTIVE_WARNING_FIELDS = (
  "_consecutive_warning_force_rest",
  "_consecutive_warning_cancelled",
  "_consecutive_warning_cancel_reason",
)


def _action_value(action, key, default=None):
  if hasattr(action, "get"):
    return action.get(key, default)
  if isinstance(action, dict):
    return action.get(key, default)
  return default


def _action_pop(action, key):
  if isinstance(action, dict):
    action.pop(key, None)
  elif hasattr(action, "options"):
    action.options.pop(key, None)


def clear_optional_race_action_fields(action):
  for key in OPTIONAL_RACE_ACTION_FIELDS:
    _action_pop(action, key)


def clear_consecutive_warning_fields(action):
  for key in CONSECUTIVE_WARNING_FIELDS:
    _action_pop(action, key)


def remember_training_fallback(action):
  if not hasattr(action, "__setitem__"):
    return
  training_name = _action_value(action, "training_name")
  training_data = copy.deepcopy(_action_value(action, "training_data") or {})
  if training_name:
    action["_rival_fallback_training_name"] = training_name
  if training_data:
    action["_rival_fallback_training_data"] = training_data


def set_rival_fallback_action(action, *, func=None, training_name=None, training_data=None):
  if not hasattr(action, "__setitem__"):
    return
  if func:
    action["_rival_fallback_func"] = func
  if training_name:
    action["_rival_fallback_training_name"] = training_name
  if isinstance(training_data, dict) and training_data:
    action["_rival_fallback_training_data"] = copy.deepcopy(training_data)


def capture_rival_fallback_payload(action) -> Dict[str, Any]:
  fallback_func = _action_value(action, "_rival_fallback_func")
  training_name = (
    _action_value(action, "_rival_fallback_training_name")
    or _action_value(action, "training_name")
  )
  training_data = copy.deepcopy(
    _action_value(action, "_rival_fallback_training_data")
    or _action_value(action, "training_data")
    or {}
  )
  if not fallback_func or fallback_func == "do_race":
    fallback_func = "do_training" if training_name and training_data else "do_rest"
  return {
    "func": fallback_func,
    "training_name": training_name,
    "training_data": training_data,
  }


def apply_rival_fallback_payload(action, fallback_payload) -> bool:
  fallback_payload = fallback_payload if isinstance(fallback_payload, dict) else {}
  target_func = fallback_payload.get("func")
  if not target_func:
    return False
  if hasattr(action, "func"):
    action.func = target_func
  elif isinstance(action, dict):
    action["func"] = target_func
  if target_func == "do_training":
    training_name = fallback_payload.get("training_name")
    training_data = copy.deepcopy(fallback_payload.get("training_data") or {})
    if training_name and hasattr(action, "__setitem__"):
      action["training_name"] = training_name
    if training_data and hasattr(action, "__setitem__"):
      action["training_data"] = training_data
  clear_optional_race_action_fields(action)
  return True


def has_training_fallback(action) -> bool:
  payload = capture_rival_fallback_payload(action)
  return bool(payload.get("training_name")) and isinstance(payload.get("training_data"), dict) and bool(payload.get("training_data"))


def hydrate_action_from_turn_plan(state_obj, action, *, limit=8):
  from core.trackblazer.planner import apply_turn_plan_action_payload, get_turn_plan, plan_once

  planner_state = plan_once(state_obj, action, limit=limit) if isinstance(state_obj, dict) else {}
  turn_plan = get_turn_plan(state_obj, action, planner_state=planner_state, limit=limit)
  apply_turn_plan_action_payload(action, turn_plan)
  return action
