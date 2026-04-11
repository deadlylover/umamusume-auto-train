from __future__ import annotations

import copy
import difflib
import hashlib
import json
from typing import Any, Dict, List, Tuple

import core.bot as bot
import core.config as config
import utils.constants as constants
from core.actions import Action
from core.trackblazer.compat import (
  capture_rival_fallback_payload as _capture_legacy_rival_fallback_payload,
)
from core.trackblazer.candidates import (
  enumerate_candidate_actions,
  planner_native_goal_race_name,
  planner_native_scheduled_race_name,
)
from core.trackblazer.derive import derive_turn_state
from core.trackblazer_item_use import plan_item_usage
from core.trackblazer.observe import hydrate_observed_turn_state
from core.trackblazer.review import build_ranked_training_snapshot
from core.trackblazer_race_logic import (
  evaluate_trackblazer_race,
  get_race_lookahead_energy_advice,
)
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
PLANNER_FORCE_FALLBACK_KEY = "_trackblazer_planner_force_fallback"
TRACKBLAZER_RUNTIME_PATH_KEY = "trackblazer_runtime_path"
TRACKBLAZER_RUNTIME_PATH_META_KEY = "trackblazer_runtime_path_meta"
RUNTIME_PATH_PLANNER_RUNTIME = "planner_runtime"
RUNTIME_PATH_PLANNER_FALLBACK_LEGACY = "planner_fallback_legacy"
RUNTIME_PATH_LEGACY_RUNTIME = "legacy_runtime"
PLANNER_VERSION = 3


def _is_empty_payload_value(value: Any) -> bool:
  if value is None:
    return True
  if isinstance(value, str):
    return value == ""
  if isinstance(value, (list, dict, tuple, set)):
    return len(value) == 0
  return False


def _normalize_runtime_path(runtime_path: str) -> str:
  normalized = str(runtime_path or "").strip()
  if normalized in {"planner_runtime", "planner"}:
    return RUNTIME_PATH_PLANNER_RUNTIME
  if normalized in {"planner_fallback_legacy", "planner→legacy (fallback)", "planner->legacy (fallback)"}:
    return RUNTIME_PATH_PLANNER_FALLBACK_LEGACY
  if normalized in {"legacy_runtime", "legacy"}:
    return RUNTIME_PATH_LEGACY_RUNTIME
  return RUNTIME_PATH_LEGACY_RUNTIME


def decision_path_for_runtime_path(runtime_path: str) -> str:
  normalized = _normalize_runtime_path(runtime_path)
  if normalized == RUNTIME_PATH_PLANNER_RUNTIME:
    return "planner"
  if normalized == RUNTIME_PATH_PLANNER_FALLBACK_LEGACY:
    return "planner→legacy (fallback)"
  return "legacy"


def runtime_path_for_decision_path(decision_path: str) -> str:
  return _normalize_runtime_path(decision_path)


def get_trackblazer_runtime_path(state_obj, default=RUNTIME_PATH_LEGACY_RUNTIME) -> str:
  if not isinstance(state_obj, dict):
    return _normalize_runtime_path(default)
  runtime_path = state_obj.get(TRACKBLAZER_RUNTIME_PATH_KEY)
  if runtime_path:
    return _normalize_runtime_path(runtime_path)
  runtime_state = state_obj.get(PLANNER_RUNTIME_KEY) or {}
  runtime_path = runtime_state.get("runtime_path")
  if runtime_path:
    return _normalize_runtime_path(runtime_path)
  return _normalize_runtime_path(default)


def set_trackblazer_runtime_path(state_obj, runtime_path, *, reason="", source=""):
  if not isinstance(state_obj, dict):
    return {}
  previous_runtime_path = _normalize_runtime_path(
    state_obj.get(TRACKBLAZER_RUNTIME_PATH_KEY)
    or ((state_obj.get(PLANNER_RUNTIME_KEY) or {}).get("runtime_path"))
    or RUNTIME_PATH_LEGACY_RUNTIME
  )
  previous_meta = copy.deepcopy(state_obj.get(TRACKBLAZER_RUNTIME_PATH_META_KEY) or {})
  normalized_path = _normalize_runtime_path(runtime_path)
  runtime_state = ensure_planner_runtime_state(state_obj)
  runtime_state["runtime_path"] = normalized_path
  runtime_state["runtime_path_reason"] = str(reason or "")
  runtime_state["runtime_path_source"] = str(source or "")
  state_obj[PLANNER_RUNTIME_KEY] = runtime_state
  state_obj[TRACKBLAZER_RUNTIME_PATH_KEY] = normalized_path
  state_obj[TRACKBLAZER_RUNTIME_PATH_META_KEY] = {
    "runtime_path": normalized_path,
    "reason": str(reason or ""),
    "source": str(source or ""),
    "turn_key": _turn_key(state_obj),
  }
  if (
    previous_runtime_path != normalized_path
    or str(previous_meta.get("reason") or "") != str(reason or "")
    or str(previous_meta.get("source") or "") != str(source or "")
  ):
    bot.push_debug_history({
      "event": "planner_boundary",
      "asset": "runtime_path",
      "result": normalized_path,
      "context": "trackblazer_planner",
      "runtime_path": normalized_path,
      "previous_runtime_path": previous_runtime_path,
      "source": str(source or ""),
      "reason": str(reason or ""),
      "note": (
        f"{previous_runtime_path} -> {normalized_path}"
        if previous_runtime_path != normalized_path else
        f"{normalized_path} ({source or 'runtime_path_update'})"
      ),
    })
  return state_obj[TRACKBLAZER_RUNTIME_PATH_META_KEY]


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
    "trackblazer_climax_race_day": (state_obj or {}).get("trackblazer_climax_race_day"),
    "trackblazer_climax_locked_race": (state_obj or {}).get("trackblazer_climax_locked_race"),
    "trackblazer_lobby_scheduled_race": (state_obj or {}).get("trackblazer_lobby_scheduled_race"),
    "trackblazer_trainings_remaining_upper_bound": (state_obj or {}).get("trackblazer_trainings_remaining_upper_bound"),
    "race_mission_available": (state_obj or {}).get("race_mission_available"),
    "rival_indicator_detected": (state_obj or {}).get("rival_indicator_detected"),
  }


def _action_signature(action) -> Dict[str, Any]:
  if not hasattr(action, "get"):
    return {}
  training_data = action.get("training_data") or {}
  planner_race = _planner_race_payload(action)
  warning_outcome = _warning_outcome_payload(action)
  fallback_payload = _capture_effective_rival_fallback_payload(action)
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
    "planner_race_warning_policy": action.get("planner_race_warning_policy"),
    "trackblazer_planner_race": planner_race,
    "planner_warning_outcome": warning_outcome,
    "rival_fallback": fallback_payload,
    "rival_scout": action.get("rival_scout"),
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


def _planner_race_payload(action) -> Dict[str, Any]:
  payload = _action_value(action, "trackblazer_planner_race") or {}
  return dict(payload or {}) if isinstance(payload, dict) else {}


def _planner_runtime_owns_race_payload(action) -> bool:
  return bool(
    _planner_race_payload(action)
    or _action_value(action, "planner_race_warning_policy")
    or _action_value(action, "planner_warning_outcome")
  )


def _capture_effective_rival_fallback_payload(action) -> Dict[str, Any]:
  planner_race = _planner_race_payload(action)
  if planner_race:
    fallback_action = planner_race.get("fallback_action") or {}
    return dict(fallback_action or {}) if isinstance(fallback_action, dict) else {}
  return _capture_legacy_rival_fallback_payload(action)


def _effective_rival_fallback_func(action) -> str:
  return str((_capture_effective_rival_fallback_payload(action) or {}).get("func") or "")


def _warning_outcome_payload(action) -> Dict[str, Any]:
  planner_outcome = _action_value(action, "planner_warning_outcome") or {}
  if isinstance(planner_outcome, dict) and planner_outcome.get("cancelled"):
    return {
      "cancelled": True,
      "force_rest": bool(planner_outcome.get("force_rest")),
      "reason": str(planner_outcome.get("reason") or ""),
    }
  if _planner_runtime_owns_race_payload(action):
    return {}
  if bool(_action_value(action, "_consecutive_warning_cancelled")):
    return {
      "cancelled": True,
      "force_rest": bool(_action_value(action, "_consecutive_warning_force_rest")),
      "reason": str(_action_value(action, "_consecutive_warning_cancel_reason") or ""),
    }
  return {}


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
    "scheduled_race": bool(action.get("scheduled_race")),
    "trackblazer_lobby_scheduled_race": bool(action.get("trackblazer_lobby_scheduled_race")),
    "race_mission_available": bool(action.get("race_mission_available")),
    "is_race_day": bool(action.get("is_race_day")),
    "trackblazer_climax_race_day": bool(action.get("trackblazer_climax_race_day")),
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
  runtime_path_meta = copy.deepcopy(state_obj.get(TRACKBLAZER_RUNTIME_PATH_META_KEY) or {})
  runtime_path = (
    runtime_path_meta.get("runtime_path")
    or state_obj.get(TRACKBLAZER_RUNTIME_PATH_KEY)
    or existing.get("runtime_path")
    or RUNTIME_PATH_LEGACY_RUNTIME
  )
  pending_skill_scan = BackgroundSkillScanState(**dict(existing.get("pending_skill_scan") or {}))
  runtime = PlannerRuntimeState(
    turn_key=str(existing.get("turn_key") or _turn_key(state_obj)),
    latest_observation_id=str(existing.get("latest_observation_id") or ""),
    scan_cadence=dict(existing.get("scan_cadence") or {}),
    pending_skill_scan=pending_skill_scan,
    fallback_count=int(existing.get("fallback_count") or 0),
    last_fallback_reason=str(existing.get("last_fallback_reason") or ""),
    runtime_path=_normalize_runtime_path(runtime_path),
    runtime_path_reason=str(
      runtime_path_meta.get("reason")
      or existing.get("runtime_path_reason")
      or ""
    ),
    runtime_path_source=str(
      runtime_path_meta.get("source")
      or existing.get("runtime_path_source")
      or ""
    ),
    transition_breadcrumbs=list(existing.get("transition_breadcrumbs") or []),
  )
  runtime_payload = runtime.to_dict()
  state_obj[PLANNER_RUNTIME_KEY] = runtime_payload
  state_obj[TRACKBLAZER_RUNTIME_PATH_KEY] = runtime_payload.get("runtime_path") or RUNTIME_PATH_LEGACY_RUNTIME
  state_obj[TRACKBLAZER_RUNTIME_PATH_META_KEY] = {
    "runtime_path": runtime_payload.get("runtime_path") or RUNTIME_PATH_LEGACY_RUNTIME,
    "reason": runtime_payload.get("runtime_path_reason") or "",
    "source": runtime_payload.get("runtime_path_source") or "",
    "turn_key": _turn_key(state_obj),
  }
  return runtime_payload


def _clone_action(action):
  cloned = Action(**copy.deepcopy(getattr(action, "options", {}) or {}))
  cloned.func = _action_func(action)
  cloned.available_actions = list(getattr(action, "available_actions", []) or [])
  return cloned


def _clear_planner_selected_action_fields(action):
  if not hasattr(action, "options"):
    return
  for key in (
    "race_name",
    "race_image_path",
    "race_grade_target",
    "prefer_rival_race",
    "fallback_non_rival_race",
    "scheduled_race",
    "trackblazer_lobby_scheduled_race",
    "race_mission_available",
    "is_race_day",
    "trackblazer_climax_race_day",
    "training_name",
    "training_function",
    "training_data",
    "planner_race_warning_policy",
    "planner_warning_outcome",
    "trackblazer_planner_race",
    "_rival_fallback_func",
    "_rival_fallback_training_name",
    "_rival_fallback_training_data",
    "_consecutive_warning_force_rest",
    "_consecutive_warning_cancelled",
    "_consecutive_warning_cancel_reason",
  ):
    action.options.pop(key, None)


def apply_selected_action_payload(action, selected_payload, *, available_actions=None):
  if not hasattr(action, "__setitem__"):
    return action

  selected_payload = dict(selected_payload or {})
  target_func = selected_payload.get("func") or _action_func(action)
  _clear_planner_selected_action_fields(action)
  action.func = target_func

  for key, value in selected_payload.items():
    if key == "func":
      continue
    if _is_empty_payload_value(value) and key not in {
      "trackblazer_race_decision",
      "trackblazer_race_lookahead",
      "planner_race_warning_policy",
      "trackblazer_planner_race",
    }:
      continue
    action[key] = copy.deepcopy(value)

  if hasattr(action, "available_actions") and target_func:
    available_actions = list(available_actions or getattr(action, "available_actions", []) or [])
    if target_func in available_actions:
      available_actions = [target_func] + [name for name in available_actions if name != target_func]
    else:
      available_actions = [target_func] + available_actions
    action.available_actions = available_actions
  return action


def build_turn_plan_execution_action(action, turn_plan: TurnPlan):
  execution_action = _clone_action(action)
  apply_turn_plan_action_payload(execution_action, turn_plan)
  return execution_action


def _planner_bound_action(action, planner_race_plan):
  planner_race_plan = planner_race_plan if isinstance(planner_race_plan, dict) else {}
  selected_action = dict(planner_race_plan.get("selected_action") or {})
  if not selected_action:
    return _clone_action(action)

  bound_action = _clone_action(action)
  apply_selected_action_payload(
    bound_action,
    selected_action,
    available_actions=list((planner_race_plan.get("action_payload") or {}).get("available_actions") or []),
  )
  return bound_action


def _fallback_target_label(payload):
  if not isinstance(payload, dict):
    return "unknown"
  target_func = payload.get("func") or "unknown"
  if target_func == "do_training":
    training_name = payload.get("training_name") or "unknown"
    return f"train {training_name}"
  if target_func == "do_race":
    race_name = payload.get("race_name") or "any"
    return f"race {race_name}"
  if target_func == "do_rest":
    return "rest"
  if target_func == "do_recreation":
    return "recreation"
  return str(target_func)


def _capture_action_payload(action, state_obj=None) -> Dict[str, Any]:
  payload = {
    "func": _action_func(action),
    "training_name": _action_value(action, "training_name"),
    "training_data": copy.deepcopy(_action_value(action, "training_data") or {}),
    "training_function": _action_value(action, "training_function"),
    "race_name": _action_value(action, "race_name"),
    "race_image_path": _action_value(action, "race_image_path"),
    "race_grade_target": _action_value(action, "race_grade_target"),
    "prefer_rival_race": bool(_action_value(action, "prefer_rival_race")),
    "fallback_non_rival_race": bool(_action_value(action, "fallback_non_rival_race")),
    "scheduled_race": bool(_action_value(action, "scheduled_race")),
    "trackblazer_lobby_scheduled_race": bool(_action_value(action, "trackblazer_lobby_scheduled_race")),
    "trackblazer_climax_race_day": bool(_action_value(action, "trackblazer_climax_race_day")),
    "race_mission_available": bool(_action_value(action, "race_mission_available")),
    "is_race_day": bool(_action_value(action, "is_race_day")),
    "available_actions": list(getattr(action, "available_actions", []) or []),
  }
  if isinstance(state_obj, dict):
    payload["energy_level"] = state_obj.get("energy_level")
    payload["year"] = state_obj.get("year")
  return payload


def _best_training_payload_from_state(action, state_obj=None) -> Dict[str, Any]:
  state_obj = state_obj if isinstance(state_obj, dict) else {}
  def _best_from_trainings(trainings):
    best_name = None
    best_payload = {}
    best_score = float("-inf")

    for training_name, payload in (trainings or {}).items():
      if not isinstance(payload, dict):
        continue
      score_tuple = payload.get("score_tuple") or ()
      score = payload.get("weighted_stat_score")
      if score is None and score_tuple:
        score = score_tuple[0]
      try:
        numeric_score = float(score)
      except (TypeError, ValueError):
        numeric_score = float("-inf")
      if numeric_score > best_score:
        best_score = numeric_score
        best_name = str(payload.get("name") or training_name or "")
        best_payload = copy.deepcopy(payload)

    if best_name and best_payload:
      return {
        "func": "do_training",
        "training_name": best_name,
        "training_function": (
          _action_value(action, "training_function")
          or "stat_weight_training"
        ),
        "training_data": best_payload,
      }
    return {}

  best_training = _best_from_trainings(copy.deepcopy(_action_value(action, "available_trainings") or {}))
  if not best_training:
    best_training = _best_from_trainings(copy.deepcopy(state_obj.get("training_results") or {}))

  if best_training:
    return best_training

  if bool(state_obj.get("date_event_available")):
    return {"func": "do_recreation"}
  return {"func": "do_rest"}


def _resolve_pre_debut_non_race_payload(action, state_obj=None, fallback_payload=None) -> Dict[str, Any]:
  fallback_payload = fallback_payload if isinstance(fallback_payload, dict) else {}
  best_payload = _best_training_payload_from_state(action, state_obj=state_obj)

  # Pre-debut turns should still use the strongest scanned training when one is
  # available, even if the legacy action arrived as a provisional rest fallback.
  if best_payload.get("func") == "do_training":
    return best_payload

  fallback_func = fallback_payload.get("func")
  if fallback_func and fallback_func != "do_race" and fallback_func != "do_rest":
    return copy.deepcopy(fallback_payload)

  if best_payload.get("func"):
    return best_payload

  if fallback_func and fallback_func != "do_race":
    return copy.deepcopy(fallback_payload)

  return {"func": "do_rest"}


def _resolve_planner_non_race_payload(action, state_obj=None, race_decision=None, fallback_payload=None) -> Dict[str, Any]:
  state_obj = state_obj if isinstance(state_obj, dict) else {}
  race_decision = race_decision if isinstance(race_decision, dict) else {}
  fallback_payload = fallback_payload if isinstance(fallback_payload, dict) else {}

  action_payload = _capture_action_payload(action, state_obj=state_obj)
  action_func = action_payload.get("func")
  best_payload = _best_training_payload_from_state(action, state_obj=state_obj)
  best_training_available = best_payload.get("func") == "do_training"
  race_reason = str(race_decision.get("reason") or "").strip().lower()

  # Planner mode should not inherit a provisional legacy rest when the rest
  # only came from race gating. Prefer the best scanned training instead.
  if (
    action_func == "do_rest"
    and best_training_available
    and (
      race_decision.get("prefer_rest_over_weak_training")
      or "operator race gate disabled" in race_reason
    )
  ):
    return best_payload

  if action_func == "do_training":
    training_name = action_payload.get("training_name")
    training_data = action_payload.get("training_data") or {}
    if training_name and isinstance(training_data, dict) and training_data:
      return action_payload
    if best_training_available:
      return best_payload

  if action_func and action_func != "do_race":
    return action_payload

  if best_payload.get("func"):
    return best_payload

  fallback_func = fallback_payload.get("func")
  if fallback_func and fallback_func != "do_race":
    return copy.deepcopy(fallback_payload)

  return {"func": "do_rest"}


def _planner_native_goal_race_active(state_obj) -> Tuple[bool, str]:
  state_obj = state_obj if isinstance(state_obj, dict) else {}
  race_name = planner_native_goal_race_name(state_obj)
  return bool(race_name), race_name


def _planner_force_fallback(state_obj) -> Dict[str, Any]:
  if not isinstance(state_obj, dict):
    return {}
  fallback = dict(state_obj.get(PLANNER_FORCE_FALLBACK_KEY) or {})
  if fallback.get("turn_key") != _turn_key(state_obj):
    return {}
  return fallback


def clear_planner_fallback(state_obj):
  if isinstance(state_obj, dict):
    state_obj.pop(PLANNER_FORCE_FALLBACK_KEY, None)


def mark_planner_fallback(state_obj, reason):
  if not isinstance(state_obj, dict):
    return {}
  runtime_state = ensure_planner_runtime_state(state_obj)
  runtime_state["fallback_count"] = int(runtime_state.get("fallback_count") or 0) + 1
  runtime_state["last_fallback_reason"] = str(reason or "planner_error")
  state_obj[PLANNER_RUNTIME_KEY] = runtime_state
  set_trackblazer_runtime_path(
    state_obj,
    RUNTIME_PATH_PLANNER_FALLBACK_LEGACY,
    reason=reason,
    source="mark_planner_fallback",
  )
  payload = {
    "turn_key": _turn_key(state_obj),
    "reason": str(reason or "planner_error"),
  }
  state_obj[PLANNER_FORCE_FALLBACK_KEY] = payload
  return payload


def append_planner_runtime_transition(state_obj, *, step_id="", step_type="", status="", note="", details=None):
  if not isinstance(state_obj, dict):
    return {}
  runtime_state = ensure_planner_runtime_state(state_obj)
  breadcrumbs = list(runtime_state.get("transition_breadcrumbs") or [])
  breadcrumbs.append(
    {
      "turn_key": _turn_key(state_obj),
      "step_id": str(step_id or ""),
      "step_type": str(step_type or ""),
      "status": str(status or ""),
      "note": str(note or ""),
      "details": copy.deepcopy(details or {}),
    }
  )
  runtime_state["transition_breadcrumbs"] = breadcrumbs[-24:]
  state_obj[PLANNER_RUNTIME_KEY] = runtime_state
  return runtime_state


def _candidate_shop_buys(effective_shop_items, shop_items=None, shop_summary=None, held_quantities=None, limit=8):
  purchasable_shop_keys = (shop_summary or {}).get("purchasable_items")
  if purchasable_shop_keys is not None:
    detected_shop_keys = set(purchasable_shop_keys or [])
  else:
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


_ENERGY_SHOP_ITEM_KEYS = ("vita_65", "royal_kale_juice", "vita_40", "vita_20")
_SUMMER_RESERVE_ITEM_KEYS = ("vita_65", "royal_kale_juice", "vita_40")


def _shop_buy_plan_total_cost(entries):
  total_cost = 0
  for entry in list(entries or []):
    if not isinstance(entry, dict):
      continue
    total_cost += int(entry.get("cost") or 0)
  return total_cost


def _shop_effective_item_lookup(effective_shop_items):
  lookup = {}
  for entry in list(effective_shop_items or []):
    if not isinstance(entry, dict):
      continue
    item_key = str(entry.get("key") or "")
    if item_key:
      lookup[item_key] = entry
  return lookup


def _shop_detected_item_keys(shop_items=None, shop_summary=None):
  return set(shop_items or (shop_summary or {}).get("items_detected") or [])


def _shop_promotion_candidate(
  item_key,
  *,
  effective_lookup=None,
  detected_keys=None,
  held_quantities=None,
  shop_summary=None,
):
  effective_lookup = effective_lookup if isinstance(effective_lookup, dict) else {}
  detected_keys = set(detected_keys or [])
  held_quantities = held_quantities if isinstance(held_quantities, dict) else {}
  shop_summary = shop_summary if isinstance(shop_summary, dict) else {}
  item_entry = dict(effective_lookup.get(item_key) or {})
  if not item_entry:
    return {}
  if item_key not in detected_keys:
    return {}
  if item_entry.get("effective_priority") == "NEVER":
    return {}

  held_quantity = int(held_quantities.get(item_key) or 0)
  max_quantity = int(item_entry.get("max_quantity") or 0)
  dynamic_limit = get_dynamic_shop_limits(
    held_quantities=held_quantities,
    year=shop_summary.get("year"),
    turn=shop_summary.get("turn"),
  ).get(item_key) or {}
  if dynamic_limit.get("block_purchase"):
    return {}
  dynamic_max_total = dynamic_limit.get("max_total")
  if dynamic_max_total is not None and held_quantity >= int(dynamic_max_total):
    return {}
  if max_quantity > 0 and held_quantity >= max_quantity:
    return {}
  return {
    "key": item_key,
    "name": item_entry.get("display_name") or str(item_key).replace("_", " ").title(),
    "priority": item_entry.get("effective_priority"),
    "cost": int(item_entry.get("cost") or 0),
    "held_quantity": held_quantity,
    "max_quantity": max_quantity,
    "reason": str(item_entry.get("policy_notes") or item_entry.get("notes") or ""),
  }


def _promote_shop_buy_entry(would_buy, promoted_entry, *, shop_coins, reason, trigger):
  promoted_entry = dict(promoted_entry or {})
  if not promoted_entry.get("key"):
    return list(would_buy or []), {}

  adjusted = [
    copy.deepcopy(entry)
    for entry in list(would_buy or [])
    if isinstance(entry, dict)
  ]
  existing_index = next(
    (
      index
      for index, entry in enumerate(adjusted)
      if str(entry.get("key") or "") == str(promoted_entry.get("key") or "")
    ),
    None,
  )
  displaced = []
  if existing_index is not None:
    promoted = adjusted.pop(existing_index)
    adjusted.insert(0, promoted)
    if existing_index == 0:
      return adjusted, {}
  else:
    remaining_coins = int(shop_coins or 0) - _shop_buy_plan_total_cost(adjusted)
    while adjusted and remaining_coins < int(promoted_entry.get("cost") or 0):
      removed = adjusted.pop()
      displaced.append(removed.get("name") or removed.get("key") or "unknown")
      remaining_coins += int(removed.get("cost") or 0)
    if remaining_coins < int(promoted_entry.get("cost") or 0):
      return list(would_buy or []), {}
    adjusted.insert(0, copy.deepcopy(promoted_entry))

  deviation = {
    "trigger": str(trigger or "shop_deviation"),
    "item_key": promoted_entry.get("key"),
    "item_name": promoted_entry.get("name") or promoted_entry.get("key"),
    "reason": str(reason or ""),
    "displaced_items": displaced,
  }
  return adjusted, deviation


def _apply_shop_deviation_rules(
  would_buy,
  *,
  selected_candidate=None,
  derived_data=None,
  effective_shop_items=None,
  shop_items=None,
  shop_summary=None,
  held_quantities=None,
):
  adjusted = [
    copy.deepcopy(entry)
    for entry in list(would_buy or [])
    if isinstance(entry, dict)
  ]
  deviations = []
  selected_candidate = selected_candidate if isinstance(selected_candidate, dict) else {}
  derived_data = derived_data if isinstance(derived_data, dict) else {}
  shop_summary = shop_summary if isinstance(shop_summary, dict) else {}
  held_quantities = held_quantities if isinstance(held_quantities, dict) else {}
  effective_lookup = _shop_effective_item_lookup(effective_shop_items)
  detected_keys = _shop_detected_item_keys(shop_items=shop_items, shop_summary=shop_summary)
  shop_coins = int(shop_summary.get("shop_coins") or 0)

  required_item_key = _candidate_item_key(selected_candidate.get("node_id"))
  if required_item_key and not int(held_quantities.get(required_item_key) or 0):
    required_item = _shop_promotion_candidate(
      required_item_key,
      effective_lookup=effective_lookup,
      detected_keys=detected_keys,
      held_quantities=held_quantities,
      shop_summary=shop_summary,
    )
    adjusted, deviation = _promote_shop_buy_entry(
      adjusted,
      required_item,
      shop_coins=shop_coins,
      trigger="item_assist_requirement",
      reason=(
        f"selected {selected_candidate.get('node_id') or 'item-assisted training'} "
        f"requires {required_item.get('name') or required_item_key} and it is affordable in the current shop."
      ),
    )
    if deviation:
      deviations.append(deviation)

  lookahead = dict(derived_data.get("lookahead_summary") or {})
  if lookahead.get("projected_energy_deficit"):
    energy_candidate = next(
      (
        _shop_promotion_candidate(
          item_key,
          effective_lookup=effective_lookup,
          detected_keys=detected_keys,
          held_quantities=held_quantities,
          shop_summary=shop_summary,
        )
        for item_key in _ENERGY_SHOP_ITEM_KEYS
        if _shop_promotion_candidate(
          item_key,
          effective_lookup=effective_lookup,
          detected_keys=detected_keys,
          held_quantities=held_quantities,
          shop_summary=shop_summary,
        )
      ),
      {},
    )
    adjusted, deviation = _promote_shop_buy_entry(
      adjusted,
      energy_candidate,
      shop_coins=shop_coins,
      trigger="energy_deficit",
      reason="lookahead projects an energy deficit before the upcoming race cadence.",
    )
    if deviation:
      deviations.append(deviation)

  timeline_window = dict(derived_data.get("timeline_window") or {})
  summer_distance = _safe_float(timeline_window.get("summer_distance"), 99.0)
  summer_reserve = sum(int(held_quantities.get(item_key) or 0) for item_key in _SUMMER_RESERVE_ITEM_KEYS)
  if summer_distance <= 1.0 and summer_reserve < 1:
    summer_candidate = next(
      (
        _shop_promotion_candidate(
          item_key,
          effective_lookup=effective_lookup,
          detected_keys=detected_keys,
          held_quantities=held_quantities,
          shop_summary=shop_summary,
        )
        for item_key in _SUMMER_RESERVE_ITEM_KEYS
        if _shop_promotion_candidate(
          item_key,
          effective_lookup=effective_lookup,
          detected_keys=detected_keys,
          held_quantities=held_quantities,
          shop_summary=shop_summary,
        )
      ),
      {},
    )
    adjusted, deviation = _promote_shop_buy_entry(
      adjusted,
      summer_candidate,
      shop_coins=shop_coins,
      trigger="summer_reservation",
      reason="summer is within one turn and the Vita reserve is below the planner reservation threshold.",
    )
    if deviation:
      deviations.append(deviation)

  return adjusted, deviations


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


def _planner_selected_action_payload(
  action,
  *,
  func=None,
  training_name=None,
  training_function=None,
  training_data=None,
  race_name=None,
  race_image_path=None,
  race_grade_target=None,
  prefer_rival_race=False,
  fallback_non_rival_race=False,
  scheduled_race=False,
  lobby_scheduled_race=False,
  race_mission_available=False,
  is_race_day=False,
  trackblazer_climax_race_day=False,
  race_decision=None,
  race_lookahead=None,
  warning_policy=None,
  fallback_action=None,
  branch_kind="",
):
  fallback_action = fallback_action if isinstance(fallback_action, dict) else {}
  selected_func = func or _action_func(action)
  payload = {
    "func": selected_func,
    "training_name": training_name if training_name is not None else _action_value(action, "training_name"),
    "training_function": training_function if training_function is not None else _action_value(action, "training_function"),
    "training_data": copy.deepcopy(training_data if training_data is not None else (_action_value(action, "training_data") or {})),
    "race_name": race_name,
    "race_image_path": race_image_path,
    "race_grade_target": race_grade_target,
    "prefer_rival_race": bool(prefer_rival_race),
    "fallback_non_rival_race": bool(fallback_non_rival_race),
    "scheduled_race": bool(scheduled_race),
    "trackblazer_lobby_scheduled_race": bool(lobby_scheduled_race),
    "race_mission_available": bool(race_mission_available),
    "is_race_day": bool(is_race_day),
    "trackblazer_climax_race_day": bool(trackblazer_climax_race_day),
    "trackblazer_race_decision": copy.deepcopy(race_decision or {}),
    "trackblazer_race_lookahead": copy.deepcopy(race_lookahead or {}),
    "planner_race_warning_policy": copy.deepcopy(warning_policy or {}),
    "trackblazer_planner_race": {
      "branch_kind": branch_kind,
      "warning_plan": copy.deepcopy(warning_policy or {}),
      "fallback_action": copy.deepcopy(fallback_action),
    },
  }
  if selected_func != "do_race":
    payload["race_name"] = None
    payload["race_image_path"] = None
    payload["race_grade_target"] = None
    payload["prefer_rival_race"] = False
    payload["fallback_non_rival_race"] = False
    payload["scheduled_race"] = False
    payload["trackblazer_lobby_scheduled_race"] = False
    payload["race_mission_available"] = False
    payload["is_race_day"] = False
    payload["trackblazer_climax_race_day"] = False
  return payload


def _build_planner_race_plan(state_obj, action, *, allow_live_rival_indicator_check=False):
  state_obj = state_obj if isinstance(state_obj, dict) else {}
  base_action = _capture_action_payload(action, state_obj=state_obj)
  # Transitional race-node probes should not pull planner intent from legacy
  # fallback payloads; ranked selection rebuilds planner-owned fallback state.
  base_fallback = {}
  pre_debut_fallback = _resolve_pre_debut_non_race_payload(action, state_obj=state_obj, fallback_payload=base_fallback)
  pre_debut = state_obj.get("year") == "Junior Year Pre-Debut"
  lobby_scheduled_race = bool(state_obj.get("trackblazer_lobby_scheduled_race"))
  forced_climax_race_day = bool(state_obj.get("trackblazer_climax_race_day") or _action_value(action, "trackblazer_climax_race_day"))
  forced_race_day = bool(state_obj.get("turn") == "Race Day" or _action_value(action, "is_race_day"))
  mission_race_enabled = bool(getattr(config, "DO_MISSION_RACES_IF_POSSIBLE", False) and state_obj.get("race_mission_available"))
  prioritize_missions = bool(getattr(config, "PRIORITIZE_MISSIONS_OVER_G1", False))
  race_lookahead = get_race_lookahead_energy_advice(
    state_obj,
    getattr(config, "OPERATOR_RACE_SELECTOR", None),
  ) or {}
  scheduled_race_name = planner_native_scheduled_race_name(state_obj)
  scheduled_race_active = bool(scheduled_race_name) or lobby_scheduled_race
  goal_race_active, goal_race_name = _planner_native_goal_race_active(state_obj)

  existing_rival_scout = copy.deepcopy(_action_value(action, "rival_scout", {}) or {})
  warning_outcome = _warning_outcome_payload(action)
  warning_cancelled = bool(warning_outcome.get("cancelled"))
  warning_cancel_reason = str(warning_outcome.get("reason") or "")
  existing_planner_race = copy.deepcopy(_planner_race_payload(action))
  existing_planner_branch = str(existing_planner_race.get("branch_kind") or "")

  race_decision = {}
  race_decision_cached_only = False
  optional_race_eval_requires_rival_indicator = not (
    forced_climax_race_day
    or forced_race_day
    or scheduled_race_active
    or mission_race_enabled
    or goal_race_active
  )
  if not pre_debut:
    missing_cached_rival_indicator = "rival_indicator_detected" not in state_obj
    if (
      optional_race_eval_requires_rival_indicator
      and missing_cached_rival_indicator
      and not allow_live_rival_indicator_check
    ):
      race_decision_cached_only = True
      race_decision = {
        "should_race": False,
        "reason": "race gate not re-run in read-only planner mode because cached rival-indicator context is missing",
        "cached_only": True,
      }
    else:
      try:
        race_decision = evaluate_trackblazer_race(state_obj, action) or {}
      except Exception as exc:
        race_decision = {
          "should_race": False,
          "reason": f"planner_race_logic_error: {exc}",
          "planner_error": True,
        }

  branch_kind = "non_race"
  selected_action_payload = copy.deepcopy(base_action)
  race_check = {
    "planner_owned": True,
    "branch_kind": branch_kind,
    "rival_indicator_detected": bool(state_obj.get("rival_indicator_detected")),
    "scheduled_race": scheduled_race_active,
    "scheduled_race_source": "lobby_button" if lobby_scheduled_race else ("config_schedule" if scheduled_race_name else ""),
    "forced_climax_race_day": forced_climax_race_day,
    "forced_race_day": forced_race_day,
    "pre_debut": pre_debut,
    "cached_only": bool(race_decision_cached_only or race_decision.get("cached_only")),
    "cached_only_reason": str(race_decision.get("reason") or "") if (race_decision_cached_only or race_decision.get("cached_only")) else "",
  }
  race_entry_gate = {}
  race_scout = {
    "planner_owned": True,
    "required": False,
    "executed": bool(existing_rival_scout),
  }
  warning_plan = {}

  if (
    existing_planner_branch
    and not warning_cancelled
    and existing_rival_scout.get("rival_found") is not False
  ):
    branch_kind = existing_planner_branch
    warning_plan = copy.deepcopy(
      _action_value(action, "planner_race_warning_policy") or
      existing_planner_race.get("warning_plan") or {}
    )
    selected_action_payload = _planner_selected_action_payload(
      action,
      func=_action_func(action),
      race_name=_action_value(action, "race_name"),
      race_image_path=_action_value(action, "race_image_path"),
      race_grade_target=_action_value(action, "race_grade_target"),
      prefer_rival_race=bool(_action_value(action, "prefer_rival_race")),
      fallback_non_rival_race=bool(_action_value(action, "fallback_non_rival_race")),
      scheduled_race=bool(_action_value(action, "scheduled_race")),
      lobby_scheduled_race=bool(_action_value(action, "trackblazer_lobby_scheduled_race")),
      race_mission_available=bool(_action_value(action, "race_mission_available")),
      is_race_day=bool(_action_value(action, "is_race_day")),
      trackblazer_climax_race_day=bool(_action_value(action, "trackblazer_climax_race_day")),
      race_decision=copy.deepcopy(_action_value(action, "trackblazer_race_decision") or {}),
      race_lookahead=copy.deepcopy(_action_value(action, "trackblazer_race_lookahead") or {}),
      warning_policy=warning_plan,
      fallback_action=base_fallback,
      branch_kind=branch_kind,
    )
    race_scout = {
      "planner_owned": True,
      "required": bool(branch_kind == "optional_rival_race"),
      "executed": bool(existing_rival_scout),
      "rival_found": existing_rival_scout.get("rival_found"),
      "selected_race_name": existing_rival_scout.get("race_name"),
      "selected_match_count": existing_rival_scout.get("match_count"),
      "selected_grade": existing_rival_scout.get("grade"),
    }
  elif warning_cancelled and _action_func(action) != "do_race":
    branch_kind = "warning_cancel_fallback"
    warning_plan = {
      "planner_owned": False,
      "provisional": True,
      "outcome": "cancelled",
      "cancel_reason_key": warning_cancel_reason,
    }
    selected_action_payload = _planner_selected_action_payload(
      action,
      func=_action_func(action),
      race_decision={
        "should_race": False,
        "branch_kind": branch_kind,
        "reason": warning_cancel_reason or "Consecutive-race warning cancelled the race branch.",
      },
      warning_policy=warning_plan,
      fallback_action=base_fallback,
      branch_kind=branch_kind,
    )
    race_scout.update({
      "executed": bool(existing_rival_scout),
      "rival_found": existing_rival_scout.get("rival_found"),
      "status": "warning_cancelled",
    })
  elif existing_rival_scout.get("rival_found") is False and _action_func(action) != "do_race":
    branch_kind = "rival_scout_fallback"
    warning_plan = {
      "planner_owned": False,
      "provisional": True,
    }
    selected_action_payload = _planner_selected_action_payload(
      action,
      func=_action_func(action),
      race_decision={
        "should_race": False,
        "branch_kind": branch_kind,
        "reason": "Planner rival scout failed; reverting to the stored fallback action.",
      },
      warning_policy=warning_plan,
      fallback_action=base_fallback,
      branch_kind=branch_kind,
    )
    race_scout.update({
      "required": True,
      "executed": True,
      "rival_found": False,
      "status": "fallback_applied",
      "selected_match_count": existing_rival_scout.get("match_count"),
      "selected_race_name": existing_rival_scout.get("race_name"),
      "selected_grade": existing_rival_scout.get("grade"),
    })
  else:
    if forced_climax_race_day:
      branch_kind = "forced_climax_race"
      selected_action_payload = _planner_selected_action_payload(
        action,
        func="do_race",
        race_name=_action_value(action, "race_name") or "any",
        race_grade_target=_action_value(action, "race_grade_target") or "any",
        is_race_day=True,
        trackblazer_climax_race_day=True,
        race_decision={
          "should_race": True,
          "forced_race_day": True,
          "g1_forced": True,
          "branch_kind": branch_kind,
          "reason": "Forced Climax race-day UI replaces the normal lobby buttons.",
          "race_name": _action_value(action, "race_name") or "any",
          "rival_indicator": False,
        },
        warning_policy=warning_plan,
        fallback_action=base_fallback,
        branch_kind=branch_kind,
      )
    elif forced_race_day:
      branch_kind = "forced_race_day"
      selected_action_payload = _planner_selected_action_payload(
        action,
        func="do_race",
        race_name=_action_value(action, "race_name") or "any",
        race_grade_target=_action_value(action, "race_grade_target") or "any",
        is_race_day=True,
        race_decision={
          "should_race": True,
          "forced_race_day": True,
          "g1_forced": True,
          "branch_kind": branch_kind,
          "reason": "Forced race-day turn bypasses optional race scouting and fallback logic.",
          "race_name": _action_value(action, "race_name") or "any",
          "rival_indicator": bool(state_obj.get("rival_indicator_detected")),
        },
        warning_policy=warning_plan,
        fallback_action=base_fallback,
        branch_kind=branch_kind,
      )
    elif pre_debut:
      branch_kind = "training"
      selected_action_payload = _planner_selected_action_payload(
        action,
        func=pre_debut_fallback.get("func") or "do_training",
        training_name=pre_debut_fallback.get("training_name"),
        training_function=pre_debut_fallback.get("training_function"),
        training_data=copy.deepcopy(pre_debut_fallback.get("training_data") or {}),
        race_decision={
          "should_race": False,
          "branch_kind": branch_kind,
          "reason": "Junior Year Pre-Debut keeps the race branch locked; stay on the normal non-race action until the debut race UI is actually available.",
          "race_name": _action_value(action, "race_name"),
          "rival_indicator": False,
        },
        branch_kind=branch_kind,
      )
    elif prioritize_missions and mission_race_enabled:
      branch_kind = "mission_race"
      selected_action_payload = _planner_selected_action_payload(
        action,
        func="do_race",
        race_name="any",
        race_image_path="assets/ui/match_track.png",
        race_mission_available=True,
        race_decision={
          "should_race": True,
          "branch_kind": branch_kind,
          "reason": "Mission race branch is active and prioritized ahead of the normal Trackblazer race gate.",
          "race_name": "any",
          "rival_indicator": False,
        },
        warning_policy=warning_plan,
        fallback_action=base_fallback,
        branch_kind=branch_kind,
      )
    elif scheduled_race_active:
      branch_kind = "lobby_scheduled_race" if lobby_scheduled_race and not scheduled_race_name else "scheduled_race"
      selected_action_payload = _planner_selected_action_payload(
        action,
        func="do_race",
        race_name=scheduled_race_name or _action_value(action, "race_name") or "any",
        race_image_path=(f"assets/races/{scheduled_race_name}.png" if scheduled_race_name else _action_value(action, "race_image_path") or "assets/ui/match_track.png"),
        scheduled_race=bool(scheduled_race_name),
        lobby_scheduled_race=lobby_scheduled_race,
        race_decision={
          "should_race": True,
          "branch_kind": branch_kind,
          "scheduled_race": True,
          "reason": "Scheduled race branch is planner-owned and bypasses optional rival-scout logic.",
          "race_name": scheduled_race_name or "any",
          "rival_indicator": bool(state_obj.get("rival_indicator_detected")),
        },
        race_lookahead=race_lookahead,
        warning_policy=warning_plan,
        fallback_action=base_fallback,
        branch_kind=branch_kind,
      )
    elif (not prioritize_missions) and mission_race_enabled:
      branch_kind = "mission_race"
      selected_action_payload = _planner_selected_action_payload(
        action,
        func="do_race",
        race_name="any",
        race_image_path="assets/ui/match_track.png",
        race_mission_available=True,
        race_decision={
          "should_race": True,
          "branch_kind": branch_kind,
          "reason": "Mission race branch remains available after scheduled-race checks.",
          "race_name": "any",
          "rival_indicator": False,
        },
        warning_policy=warning_plan,
        fallback_action=base_fallback,
        branch_kind=branch_kind,
      )
    elif goal_race_active:
      branch_kind = "goal_race"
      selected_action_payload = _planner_selected_action_payload(
        action,
        func="do_race",
        race_name=goal_race_name,
        race_image_path=f"assets/races/{goal_race_name}.png" if goal_race_name and goal_race_name != "any" else _action_value(action, "race_image_path"),
        race_decision={
          "should_race": True,
          "branch_kind": branch_kind,
          "reason": "Goal race branch selected from the configured turn criteria.",
          "race_name": goal_race_name,
          "rival_indicator": False,
        },
        warning_policy=warning_plan,
        fallback_action=base_fallback,
        branch_kind=branch_kind,
      )
    else:
      if race_decision.get("should_race"):
        branch_kind = "optional_fallback_race" if race_decision.get("fallback_non_rival_race") else "optional_rival_race"
        race_scout = {
          "planner_owned": True,
          "required": bool(branch_kind == "optional_rival_race"),
          "executed": False,
          "status": "pending",
          "failure_transition": "",
        }
        selected_action_payload = _planner_selected_action_payload(
          action,
          func="do_race",
          race_name=race_decision.get("race_name") or "any",
          race_image_path=_action_value(action, "race_image_path") or "assets/ui/match_track.png",
          race_grade_target=race_decision.get("race_tier_target"),
          prefer_rival_race=bool(race_decision.get("prefer_rival_race")),
          fallback_non_rival_race=bool(race_decision.get("fallback_non_rival_race")),
          race_decision={**dict(race_decision), "branch_kind": branch_kind},
          race_lookahead=race_lookahead,
          warning_policy=warning_plan,
          fallback_action=base_fallback,
          branch_kind=branch_kind,
        )
      else:
        non_race_payload = _resolve_planner_non_race_payload(
          action,
          state_obj=state_obj,
          race_decision=race_decision,
          fallback_payload=base_fallback,
        )
        branch_kind = "training" if non_race_payload.get("func") == "do_training" else "non_race"
        selected_action_payload = _planner_selected_action_payload(
          action,
          func=non_race_payload.get("func"),
          training_name=non_race_payload.get("training_name"),
          training_function=non_race_payload.get("training_function"),
          training_data=copy.deepcopy(non_race_payload.get("training_data") or {}),
          race_decision={**dict(race_decision), "branch_kind": branch_kind},
          race_lookahead=race_lookahead,
          branch_kind=branch_kind,
        )

  race_check["branch_kind"] = branch_kind
  race_check["scheduled_race"] = bool(selected_action_payload.get("scheduled_race") or selected_action_payload.get("trackblazer_lobby_scheduled_race"))
  race_check["scheduled_race_source"] = (
    "lobby_button"
    if selected_action_payload.get("trackblazer_lobby_scheduled_race") else
    ("config_schedule" if selected_action_payload.get("scheduled_race") else "")
  )
  race_check["scout_required"] = bool(race_scout.get("required"))

  if (
    selected_action_payload.get("func") == "do_race"
    and warning_plan
    and branch_kind not in {"forced_race_day", "forced_climax_race", "pre_debut_debut_race"}
  ):
    race_entry_gate = {
      "planner_owned": True,
      "opens_from_lobby": True,
      "expected_branch": "continue_to_race_list" if warning_plan.get("accept_warning") else "return_to_lobby",
      "warning_meaning": "This race would become the third consecutive race" if warning_plan.get("warning_expected") else "",
      "consecutive_warning_template": constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive"),
      "consecutive_warning_ok_template": constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive_ok"),
      "ok_action": "continue_to_race_list",
      "cancel_action": "return_to_lobby",
      "force_accept_warning": bool(warning_plan.get("force_accept_warning")),
    }

  action_payload = {
    "planner_owned": True,
    "branch_kind": branch_kind,
    "func": selected_action_payload.get("func"),
    "options": copy.deepcopy(selected_action_payload),
    "fallback_action": copy.deepcopy(base_fallback),
    "available_actions": list(base_action.get("available_actions") or []),
  }

  if existing_rival_scout:
    race_scout = {
      **dict(race_scout or {}),
      "executed": True,
      "rival_found": existing_rival_scout.get("rival_found"),
      "selected_race_name": existing_rival_scout.get("race_name"),
      "selected_match_count": existing_rival_scout.get("match_count"),
      "selected_grade": existing_rival_scout.get("grade"),
    }

  return {
    "planner_owned": True,
    "branch_kind": branch_kind,
    "selection_rationale": (selected_action_payload.get("trackblazer_race_decision") or {}).get("reason") or "",
    "selected_action": copy.deepcopy(selected_action_payload),
    "action_payload": action_payload,
    "race_check": race_check,
    "race_decision": copy.deepcopy(selected_action_payload.get("trackblazer_race_decision") or {}),
    "race_entry_gate": race_entry_gate,
    "race_scout": race_scout,
    "warning_plan": warning_plan,
    "fallback_policy": {
      "planner_owned": False,
      "provisional": True,
      "chain": [],
    },
  }


def _candidate_is_compat(node_id: str) -> bool:
  return str(node_id or "").startswith("compat:")


def _candidate_training_name(node_id: str) -> str:
  node_id = str(node_id or "")
  if not node_id.startswith("train:"):
    return ""
  body = node_id.split("train:", 1)[1]
  return body.split("+items:", 1)[0].strip()


def _candidate_item_key(node_id: str) -> str:
  node_id = str(node_id or "")
  if "+items:" not in node_id:
    return ""
  return node_id.split("+items:", 1)[1].strip()


def _candidate_is_forced_race(node_id: str) -> bool:
  node_id = str(node_id or "")
  return node_id.startswith("race:scheduled:") or node_id.startswith("race:goal:") or node_id.startswith("race:g1_today:") or node_id in {
    "race:mission",
    "race:race_day",
    "race:climax_locked",
  }


def _candidate_order_rank(node_id: str) -> int:
  node_id = str(node_id or "")
  if node_id == "race:climax_locked":
    return 0
  if node_id == "race:race_day":
    return 1
  if node_id.startswith("race:scheduled:"):
    return 2
  if node_id.startswith("race:goal:"):
    return 3
  if node_id.startswith("race:g1_today:"):
    return 4
  if node_id == "race:mission":
    return 5
  if node_id.startswith("train:") and "+items:" in node_id:
    return 6
  if node_id.startswith("train:"):
    return 7
  if node_id == "race:rival":
    return 8
  if node_id == "race:fallback":
    return 9
  if node_id == "rest":
    return 10
  if node_id == "recreation":
    return 11
  if node_id == "infirmary":
    return 12
  return 20


def _safe_float(value, default=0.0):
  try:
    return float(value)
  except (TypeError, ValueError):
    return default


def _ranked_non_race_candidates(ranked_native, exclude_node_id=None) -> List[Dict[str, Any]]:
  """Return ranked planner-native non-race candidates with viable scores."""
  result = []
  exclude = str(exclude_node_id or "")
  for entry in list(ranked_native or []):
    if not isinstance(entry, dict):
      continue
    node_id = str(entry.get("node_id") or "")
    if not node_id or node_id.startswith("race:"):
      continue
    if exclude and node_id == exclude:
      continue
    score = _safe_float(entry.get("priority_score"), float("-inf"))
    if score == float("-inf"):
      continue
    result.append(entry)
  return result


def _candidate_to_fallback_payload(candidate, state_obj=None, action=None) -> Dict[str, Any]:
  """Materialize an action payload for a non-race ranked candidate."""
  candidate = candidate if isinstance(candidate, dict) else {}
  state_obj = state_obj if isinstance(state_obj, dict) else {}
  node_id = str(candidate.get("node_id") or "")
  if node_id.startswith("train:"):
    training_name = _candidate_training_name(node_id)
    training_data = copy.deepcopy(
      (state_obj.get("training_results") or {}).get(training_name) or {}
    )
    if not training_data and hasattr(action, "get"):
      training_data = copy.deepcopy(
        (action.get("available_trainings") or {}).get(training_name) or {}
      )
    return {
      "func": "do_training",
      "training_name": training_name,
      "training_data": training_data,
    }
  if node_id == "rest":
    return {"func": "do_rest"}
  if node_id == "recreation":
    return {"func": "do_recreation"}
  if node_id == "infirmary":
    return {"func": "do_infirmary"}
  return {}


def _planner_owned_primary_fallback_payload(
  selected_node_id,
  ranked_native,
  state_obj=None,
  action=None,
) -> Dict[str, Any]:
  """Return the top-ranked non-race fallback payload for the selected node."""
  non_race = _ranked_non_race_candidates(ranked_native, exclude_node_id=selected_node_id)
  top_non_race = non_race[0] if non_race else {}
  if not top_non_race:
    return {}
  return _candidate_to_fallback_payload(top_non_race, state_obj=state_obj, action=action)


def _planner_owned_warning_policy_for_node(
  branch_kind,
  selected_node_id,
  ranked_native,
  state_obj=None,
  action=None,
) -> Dict[str, Any]:
  """Build warning policy for the selected race node from ranked planner candidates.

  Cancel targets are sourced from the highest-scoring viable non-race candidate at
  rank time. The legacy `_rival_fallback_*` payload is no longer consulted.
  """
  selected_node_id = str(selected_node_id or "")
  branch_kind = str(branch_kind or "")
  non_race = _ranked_non_race_candidates(ranked_native, exclude_node_id=selected_node_id)
  top_non_race = non_race[0] if non_race else {}
  top_payload = _candidate_to_fallback_payload(top_non_race, state_obj=state_obj, action=action) if top_non_race else {}
  top_label = _fallback_target_label(top_payload) if top_payload else ""
  top_func = top_payload.get("func") if top_payload else None
  top_node_id = top_non_race.get("node_id") if top_non_race else None

  rest_candidate = next((entry for entry in non_race if str(entry.get("node_id") or "") == "rest"), None)
  rest_present = bool(rest_candidate)

  if branch_kind in {"forced_race_day", "forced_climax_race", "pre_debut_debut_race"}:
    return {
      "planner_owned": True,
      "warning_expected": False,
      "accept_warning": True,
      "accept_reason": "Forced race branch bypasses the optional consecutive-race policy.",
      "cancel_target": top_func,
      "cancel_target_label": top_label,
      "cancel_target_node_id": top_node_id,
      "force_rest_on_cancel": False,
    }

  if (
    selected_node_id == "race:race_day"
    or selected_node_id.startswith("race:g1_today:")
    or branch_kind in {"forced_race_day", "scheduled_race", "lobby_scheduled_race"}
  ):
    return {
      "planner_owned": True,
      "warning_expected": True,
      "accept_warning": True,
      "accept_reason": (
        "Forced/required race branch keeps continuing through the consecutive-race warning."
        if (
          branch_kind == "forced_race_day"
          or selected_node_id == "race:race_day"
          or selected_node_id.startswith("race:g1_today:")
        ) else
        "Scheduled race branch forces continue through the consecutive-race warning."
      ),
      "cancel_target": top_func,
      "cancel_target_label": top_label,
      "cancel_target_node_id": top_node_id,
      "force_rest_on_cancel": False,
      "force_accept_warning": True,
    }

  if branch_kind == "optional_fallback_race":
    # Weak-training fallback race: when blocked by the consecutive-race warning
    # the planner prefers rest over the weak training that triggered the
    # fallback in the first place. We still source rest from the ranked list.
    cancel_candidate = rest_candidate or top_non_race
    cancel_payload = _candidate_to_fallback_payload(cancel_candidate, state_obj=state_obj, action=action) if cancel_candidate else {"func": "do_rest"}
    if not cancel_payload:
      cancel_payload = {"func": "do_rest"}
    return {
      "planner_owned": True,
      "warning_expected": True,
      "accept_warning": False,
      "cancel_reason": "Weak-training fallback race is not worth a third consecutive race.",
      "cancel_target": cancel_payload.get("func") or "do_rest",
      "cancel_target_label": _fallback_target_label(cancel_payload) or "rest",
      "cancel_target_node_id": (cancel_candidate.get("node_id") if cancel_candidate else None) or "rest",
      "force_rest_on_cancel": True,
      "cancel_reason_key": "optional_fallback_non_rival_race",
    }

  if branch_kind == "optional_rival_race":
    # The promoted-from-rest case is detected by checking whether the highest
    # ranked non-race candidate is rest itself. The planner ranking captures
    # exactly this signal without needing to consult the legacy action.func.
    is_rest_promoted = bool(top_non_race and str(top_non_race.get("node_id") or "") == "rest")
    if is_rest_promoted:
      return {
        "planner_owned": True,
        "warning_expected": True,
        "accept_warning": False,
        "cancel_reason": "Optional rival race promoted from rest should preserve the rest fallback when blocked.",
        "cancel_target": "do_rest",
        "cancel_target_label": "rest",
        "cancel_target_node_id": "rest",
        "force_rest_on_cancel": True,
        "cancel_reason_key": "optional_rival_promoted_from_rest",
      }
    accept_warning = not bool(getattr(config, "CANCEL_CONSECUTIVE_RACE", False))
    if accept_warning:
      return {
        "planner_owned": True,
        "warning_expected": True,
        "accept_warning": True,
        "accept_reason": "Config allows optional rival races through the consecutive-race warning.",
        "cancel_reason": "",
        "cancel_target": top_func,
        "cancel_target_label": top_label,
        "cancel_target_node_id": top_node_id,
        "force_rest_on_cancel": False,
        "cancel_reason_key": "",
      }
    return {
      "planner_owned": True,
      "warning_expected": True,
      "accept_warning": False,
      "accept_reason": "",
      "cancel_reason": "Config cancels optional rival races at the consecutive-race warning.",
      "cancel_target": "do_rest",
      "cancel_target_label": "rest" if rest_present else (top_label or "rest"),
      "cancel_target_node_id": "rest" if rest_present else top_node_id,
      "force_rest_on_cancel": True,
      "cancel_reason_key": "cancel_consecutive_race_setting",
    }

  if branch_kind in {"goal_race", "mission_race"}:
    return {
      "planner_owned": True,
      "warning_expected": True,
      "accept_warning": True,
      "accept_reason": "Forced/required race branch keeps continuing through the consecutive-race warning.",
      "cancel_target": top_func,
      "cancel_target_label": top_label,
      "cancel_target_node_id": top_node_id,
      "force_rest_on_cancel": False,
    }

  return {
    "planner_owned": True,
    "warning_expected": True,
    "accept_warning": not bool(getattr(config, "CANCEL_CONSECUTIVE_RACE", False)),
    "accept_reason": "Race branch keeps the current consecutive-race policy.",
    "cancel_target": top_func,
    "cancel_target_label": top_label,
    "cancel_target_node_id": top_node_id,
    "force_rest_on_cancel": False,
  }


def _planner_owned_fallback_chain_from_ranked(
  selected_node_id,
  branch_kind,
  ranked_native,
  warning_policy,
  policy,
  state_obj=None,
  action=None,
) -> Dict[str, Any]:
  """Build a planner-owned fallback chain bounded by `policy.max_fallback_depth`.

  Chain entries are sourced from the ranked planner-native candidate list and
  filtered for feasibility per failure mode. When more entries would be needed
  than the bound allows, the chain is truncated and `replan_required` is set.
  """
  selected_node_id = str(selected_node_id or "")
  branch_kind = str(branch_kind or "")
  warning_policy = warning_policy if isinstance(warning_policy, dict) else {}
  policy_dict = policy if isinstance(policy, dict) else {}
  try:
    max_depth = int(policy_dict.get("max_fallback_depth") or 3)
  except (TypeError, ValueError):
    max_depth = 3
  if max_depth < 0:
    max_depth = 0

  non_race = _ranked_non_race_candidates(ranked_native, exclude_node_id=selected_node_id)

  def _entry_from_candidate(trigger, candidate):
    payload = _candidate_to_fallback_payload(candidate, state_obj=state_obj, action=action) if candidate else {}
    return {
      "trigger": trigger,
      "target_func": payload.get("func"),
      "target_payload": payload,
      "target_label": _fallback_target_label(payload),
      "source_node_id": candidate.get("node_id") if isinstance(candidate, dict) else None,
      "planner_ranked": True,
    }

  def _entry_from_warning_policy(trigger):
    cancel_node_id = warning_policy.get("cancel_target_node_id")
    candidate = None
    if cancel_node_id:
      candidate = next(
        (entry for entry in non_race if str(entry.get("node_id") or "") == str(cancel_node_id)),
        None,
      )
    if candidate:
      return _entry_from_candidate(trigger, candidate)
    cancel_func = warning_policy.get("cancel_target") or "do_rest"
    payload = {"func": cancel_func}
    return {
      "trigger": trigger,
      "target_func": cancel_func,
      "target_payload": payload,
      "target_label": warning_policy.get("cancel_target_label") or _fallback_target_label(payload),
      "source_node_id": cancel_node_id,
      "planner_ranked": False,
    }

  raw_chain: List[Dict[str, Any]] = []

  if not selected_node_id or not selected_node_id.startswith("race:"):
    for candidate in non_race:
      raw_chain.append(_entry_from_candidate("retry_next", candidate))
  else:
    if selected_node_id == "race:rival":
      if non_race:
        raw_chain.append(_entry_from_candidate("rival_scout_failed", non_race[0]))
        raw_chain.append(_entry_from_candidate("race_gate_blocked", non_race[0]))
    elif selected_node_id == "race:fallback":
      if non_race:
        raw_chain.append(_entry_from_candidate("race_gate_blocked", non_race[0]))
    elif branch_kind in {"goal_race", "mission_race"}:
      if non_race:
        raw_chain.append(_entry_from_candidate("race_gate_blocked", non_race[0]))
    # Forced races (race:race_day / race:climax_locked / race:scheduled:* /
    # race:goal:* / race:g1_today:* / race:mission) have no inline fallback —
    # they require a full replan rather than chaining to a non-race candidate.

    if warning_policy.get("warning_expected") and not warning_policy.get("accept_warning"):
      raw_chain.append(_entry_from_warning_policy("consecutive_warning_cancel"))

  bounded = raw_chain[:max_depth] if max_depth > 0 else []
  replan_required = len(raw_chain) > max_depth
  return {
    "planner_owned": True,
    "max_fallback_depth": max_depth,
    "chain": bounded,
    "replan_required": replan_required,
  }


def _transitional_planner_native_candidates_from_race_plan(planner_race_plan) -> List[Dict[str, Any]]:
  planner_race_plan = planner_race_plan if isinstance(planner_race_plan, dict) else {}
  branch_kind = str(planner_race_plan.get("branch_kind") or "")
  selected_action = dict(planner_race_plan.get("selected_action") or {})
  if selected_action.get("func") != "do_race":
    return []

  race_name = str(selected_action.get("race_name") or "any")
  candidates = []

  def _append(node_id, rationale, requirements, expected_warnings=None):
    candidates.append(
      {
        "node_id": node_id,
        "kind": "race",
        "priority_score": 0.0,
        "rationale": rationale,
        "requirements": list(requirements or []),
        "expected_warnings": list(expected_warnings or []),
        "expected_followup_state": {},
        "source_facts": {
          "transitional_source": "planner_race_plan",
          "branch_kind": branch_kind,
          "race_name": race_name,
        },
      }
    )

  if branch_kind in {"scheduled_race", "lobby_scheduled_race"}:
    _append(
      f"race:scheduled:{race_name or 'scheduled'}",
      "transitional planner-native scheduled race candidate (remove when M6 race evaluators are planner-native)",
      ["race_opportunity"],
      expected_warnings=["consecutive_race_warning"],
    )
  elif branch_kind == "goal_race":
    _append(
      f"race:goal:{race_name or 'goal'}",
      "transitional planner-native goal race candidate (remove when M6 race evaluators are planner-native)",
      ["race_opportunity"],
      expected_warnings=["consecutive_race_warning"],
    )
  elif branch_kind == "mission_race":
    _append(
      "race:mission",
      "transitional planner-native mission race candidate (remove when M6 race evaluators are planner-native)",
      ["race_opportunity"],
      expected_warnings=["consecutive_race_warning"],
    )
  elif branch_kind == "optional_rival_race":
    _append(
      "race:rival",
      "transitional planner-native rival race candidate sourced from existing race branch",
      ["race_opportunity", "energy"],
      expected_warnings=["consecutive_race_warning"],
    )
  elif branch_kind == "optional_fallback_race":
    _append(
      "race:fallback",
      "transitional planner-native fallback race candidate sourced from existing race branch",
      ["race_opportunity", "training", "lookahead"],
      expected_warnings=["consecutive_race_warning"],
    )
  elif branch_kind == "forced_race_day":
    _append(
      "race:race_day",
      "transitional planner-native forced race-day candidate sourced from existing race branch",
      ["turn_identity"],
      expected_warnings=["consecutive_race_warning"],
    )
  elif branch_kind == "forced_climax_race":
    _append(
      "race:climax_locked",
      "transitional planner-native climax-locked race candidate sourced from existing race branch",
      ["race_opportunity"],
    )

  return candidates


def _score_planner_native_candidates(observed_data, derived_data, policy, planner_native_candidates):
  observed_data = observed_data if isinstance(observed_data, dict) else {}
  derived_data = derived_data if isinstance(derived_data, dict) else {}
  policy = policy if isinstance(policy, dict) else {}
  planner_native_candidates = list(planner_native_candidates or [])
  missing_inputs = set(observed_data.get("missing_inputs") or [])

  training_entries = {
    str(entry.get("name") or ""): dict(entry or {})
    for entry in list(derived_data.get("training_value") or [])
    if isinstance(entry, dict) and entry.get("name")
  }
  training_threshold = _safe_float(policy.get("training_overrides_race_threshold"), 40.0)
  rival_min_energy = _safe_float(policy.get("rival_race_min_energy_ratio"), 0.02)

  lookahead = dict(derived_data.get("lookahead_summary") or {})
  race_opportunity = dict(derived_data.get("race_opportunity") or {})
  timeline_window = dict(derived_data.get("timeline_window") or {})
  energy_ratio = _safe_float(derived_data.get("energy_ratio"), None)
  if energy_ratio is None:
    energy_ratio = 0.5

  scored = []
  best_training_score = float("-inf")
  best_training_base_score = float("-inf")
  best_training_class = "weak"

  for raw_candidate in planner_native_candidates:
    candidate = copy.deepcopy(raw_candidate if isinstance(raw_candidate, dict) else {})
    node_id = str(candidate.get("node_id") or "")
    missing_requirements = sorted(
      requirement
      for requirement in list(candidate.get("requirements") or [])
      if str(requirement) in missing_inputs
    )
    source_facts = dict(candidate.get("source_facts") or {})
    if missing_requirements:
      candidate["priority_score"] = float("-inf")
      candidate["rationale"] = (
        f"{candidate.get('rationale') or ''}; dropped due to missing inputs: {', '.join(missing_requirements)}"
      ).strip("; ")
      source_facts["dropped_missing_inputs"] = missing_requirements
      candidate["source_facts"] = source_facts
      scored.append(candidate)
      continue

    training_name = _candidate_training_name(node_id)
    item_key = _candidate_item_key(node_id)
    score = 0.0
    score_components = {}
    viable = True

    if node_id.startswith("train:"):
      training = dict(training_entries.get(training_name) or {})
      base_score = _safe_float(training.get("score"), 0.0)
      failure_pct = max(0.0, min(100.0, _safe_float(training.get("failure"), 0.0)))
      failure_bypassed = bool(dict(training.get("usage_context") or {}).get("failure_bypassed_by_items"))
      max_failure = max(0.0, _safe_float(getattr(config, "MAX_FAILURE", 5), 5.0))
      if failure_pct > max_failure and not (item_key and failure_bypassed):
        viable = False
        candidate["priority_score"] = float("-inf")
        candidate["rationale"] = (
          f"{candidate.get('rationale') or ''}; dropped because fail {failure_pct:.0f}% "
          f"exceeds hard limit {max_failure:.0f}% without an explicit bypass item"
        ).strip("; ")
        source_facts["hard_failure_gate"] = {
          "failure": failure_pct,
          "max_allowed_failure": max_failure,
          "requires_item_bypass": True,
          "item_key": item_key,
          "failure_bypassed_by_items": failure_bypassed,
        }
        candidate["source_facts"] = source_facts
        scored.append(candidate)
        continue
      failure_penalty = max(0.0, 1.0 - (failure_pct / 100.0))
      if item_key and failure_bypassed:
        failure_penalty = 1.0
      rainbow_multiplier = 1.0 + (0.06 * min(3.0, _safe_float(training.get("rainbow_count"), 0.0)))
      support_count = _safe_float(training.get("support_count"), 0.0)
      bond_multiplier = 1.0 if timeline_window.get("past_bond_training_cutoff") else 1.0 + (0.02 * min(5.0, support_count))
      score = base_score * failure_penalty * rainbow_multiplier * bond_multiplier
      score_components = {
        "base_score": base_score,
        "failure_penalty": failure_penalty,
        "rainbow_multiplier": rainbow_multiplier,
        "bond_multiplier": bond_multiplier,
      }
      if item_key:
        item_delta = _safe_float(training.get("item_assist_score_delta"), 0.0)
        opportunity_cost = 0.0
        if item_key == "reset_whistle" and not timeline_window.get("summer_window"):
          opportunity_cost += 100.0
        if item_key in {"vita_65", "royal_kale_juice"} and (
          timeline_window.get("summer_window")
          or _safe_float(timeline_window.get("summer_distance"), 99.0) <= 1.0
        ):
          opportunity_cost += 8.0
        if "hammer" in item_key:
          if timeline_window.get("tsc_active"):
            opportunity_cost += 100.0
          if _safe_float(timeline_window.get("climax_distance"), 99.0) <= 1.0:
            opportunity_cost += 35.0
        if "megaphone" in item_key and not dict(training.get("usage_context") or {}).get("commit_training_after_items"):
          opportunity_cost += 6.0
        score += item_delta - opportunity_cost
        score_components["item_delta"] = item_delta
        score_components["item_opportunity_cost"] = opportunity_cost
      if score > best_training_score:
        best_training_score = score
        best_training_class = str(training.get("value_class") or "weak")
      if base_score > best_training_base_score:
        best_training_base_score = base_score
    elif node_id == "rest":
      score = (1.0 - energy_ratio) * 100.0
      if lookahead.get("projected_energy_deficit"):
        score += 18.0
      if _safe_float(lookahead.get("next_n_turns_races_count"), 0.0) >= 2.0:
        score += 8.0
      score_components = {"energy_component": (1.0 - energy_ratio) * 100.0}
    elif node_id == "recreation":
      mood = str(observed_data.get("current_mood") or "").upper()
      if mood not in {"BAD", "AWFUL"}:
        viable = False
      score = 52.0 if mood == "AWFUL" else (38.0 if mood == "BAD" else float("-inf"))
      score_components = {"mood": mood}
    elif node_id == "infirmary":
      status_count = float(len(list(observed_data.get("status_effect_names") or [])))
      if status_count <= 0:
        viable = False
      score = 22.0 * status_count
      score_components = {"status_count": int(status_count)}
    elif node_id == "race:rival":
      rival_visible = bool(race_opportunity.get("rival_visible"))
      optional_safe = bool(race_opportunity.get("optional_safe_under_lookahead"))
      if (not rival_visible) or (energy_ratio <= rival_min_energy):
        viable = False
      score = 46.0 + (4.0 if optional_safe else -6.0)
      if _safe_float(lookahead.get("next_n_turns_races_count"), 0.0) >= 2.0:
        score -= 20.0
      score_components = {
        "rival_visible": rival_visible,
        "optional_safe_under_lookahead": optional_safe,
      }
    elif node_id == "race:fallback":
      rival_visible = bool(race_opportunity.get("rival_visible"))
      if rival_visible:
        viable = False
      score = 38.0
      if _safe_float(lookahead.get("next_n_turns_races_count"), 0.0) >= 2.0:
        score -= 12.0
      score_components = {"rival_visible": rival_visible}
    elif _candidate_is_forced_race(node_id):
      forced_bias = {
        "race:climax_locked": 120.0,
        "race:race_day": 110.0,
        "race:mission": 100.0,
      }
      score = 10000.0 + forced_bias.get(node_id, 90.0)
      score_components = {"forced_race": True}
    elif node_id == "skill_purchase":
      score = 12.0
      score_components = {"skill_cadence_open": bool(derived_data.get("skill_cadence_open"))}
    elif node_id.startswith("use_reset_whistle"):
      score = 40.0 if timeline_window.get("summer_window") else float("-inf")
      viable = bool(timeline_window.get("summer_window"))
      score_components = {"summer_window": bool(timeline_window.get("summer_window"))}
    elif node_id.startswith("use_hammer:"):
      score = 20.0
      if timeline_window.get("tsc_active"):
        score -= 100.0
      score_components = {"tsc_active": bool(timeline_window.get("tsc_active"))}
    else:
      score = 0.0
      score_components = {"unclassified": True}

    if not viable:
      score = float("-inf")

    source_facts["scoring"] = {
      "score_components": score_components,
      "policy": {
        "training_overrides_race_threshold": training_threshold,
        "rival_race_min_energy_ratio": rival_min_energy,
      },
    }
    candidate["source_facts"] = source_facts
    candidate["priority_score"] = float(score)
    scored.append(candidate)

  forced_viable = [
    entry
    for entry in scored
    if _candidate_is_forced_race(entry.get("node_id"))
    and _safe_float(entry.get("priority_score"), float("-inf")) != float("-inf")
  ]

  rival_entry = next((entry for entry in scored if entry.get("node_id") == "race:rival"), None)
  fallback_entry = next((entry for entry in scored if entry.get("node_id") == "race:fallback"), None)
  non_forced_best = max(
    (
      _safe_float(entry.get("priority_score"), float("-inf"))
      for entry in scored
      if not _candidate_is_forced_race(entry.get("node_id"))
      and _safe_float(entry.get("priority_score"), float("-inf")) != float("-inf")
    ),
    default=float("-inf"),
  )
  non_race_best = max(
    (
      _safe_float(entry.get("priority_score"), float("-inf"))
      for entry in scored
      if not str(entry.get("node_id") or "").startswith("race:")
      and _safe_float(entry.get("priority_score"), float("-inf")) != float("-inf")
    ),
    default=float("-inf"),
  )

  if not forced_viable:
    if best_training_base_score >= training_threshold:
      for entry in (rival_entry, fallback_entry):
        if not entry:
          continue
        if _safe_float(entry.get("priority_score"), float("-inf")) == float("-inf"):
          continue
        entry["priority_score"] = _safe_float(entry.get("priority_score"), 0.0) - 500.0
        entry["rationale"] = (
          f"{entry.get('rationale') or ''}; penalized because training score "
          f"{best_training_base_score:.2f} >= override threshold {training_threshold:.2f}"
        ).strip("; ")
    else:
      rival_score = _safe_float(rival_entry.get("priority_score"), float("-inf")) if rival_entry else float("-inf")
      fallback_score = _safe_float(fallback_entry.get("priority_score"), float("-inf")) if fallback_entry else float("-inf")
      if rival_entry and rival_score != float("-inf"):
        rival_entry["priority_score"] = max(rival_score, non_forced_best + 1.0)
      elif fallback_entry and fallback_score != float("-inf") and best_training_class == "weak":
        fallback_entry["priority_score"] = max(fallback_score, non_race_best + 1.0)

  if (
    str(observed_data.get("year") or "") == "Junior Year Pre-Debut"
    and best_training_score != float("-inf")
  ):
    for entry in scored:
      if entry.get("node_id") != "rest":
        continue
      rest_score = _safe_float(entry.get("priority_score"), float("-inf"))
      if rest_score == float("-inf"):
        continue
      if rest_score >= best_training_score:
        entry["priority_score"] = best_training_score - 1.0
        entry["rationale"] = (
          f"{entry.get('rationale') or ''}; pre-debut guard keeps training ahead of provisional rest fallback"
        ).strip("; ")

  if (
    _safe_float(lookahead.get("next_turn_races_count"), 0.0) >= 1.0
    and not bool(race_opportunity.get("rival_visible"))
    and energy_ratio > rival_min_energy
  ):
    if fallback_entry and _safe_float(fallback_entry.get("priority_score"), float("-inf")) != float("-inf"):
      fallback_entry["priority_score"] = _safe_float(fallback_entry.get("priority_score"), 0.0) + 8.0
    for entry in scored:
      if entry.get("node_id") == "rest" and _safe_float(entry.get("priority_score"), float("-inf")) != float("-inf"):
        entry["priority_score"] = _safe_float(entry.get("priority_score"), 0.0) - 4.0

  if _safe_float(lookahead.get("next_n_turns_races_count"), 0.0) >= 2.0:
    for entry in scored:
      if entry.get("node_id") in {"race:rival", "race:fallback"} and _safe_float(entry.get("priority_score"), float("-inf")) != float("-inf"):
        entry["priority_score"] = _safe_float(entry.get("priority_score"), 0.0) - 8.0

  if lookahead.get("projected_energy_deficit"):
    for entry in scored:
      if entry.get("node_id") == "rest" and _safe_float(entry.get("priority_score"), float("-inf")) != float("-inf"):
        entry["priority_score"] = _safe_float(entry.get("priority_score"), 0.0) + 6.0

  ranked = sorted(
    scored,
    key=lambda entry: (
      -_safe_float(entry.get("priority_score"), float("-inf")),
      _candidate_order_rank(entry.get("node_id")),
      str(entry.get("node_id") or ""),
    ),
  )
  return ranked


def _rank_candidates_for_selection(candidates, observed_data, derived_data, policy, planner_race_plan):
  candidates = [candidate.to_dict() for candidate in list(candidates or [])]
  planner_native_candidates = [
    copy.deepcopy(candidate)
    for candidate in candidates
    if not _candidate_is_compat(candidate.get("node_id"))
  ]
  existing_nodes = {str(candidate.get("node_id") or "") for candidate in planner_native_candidates}
  for injected in _transitional_planner_native_candidates_from_race_plan(planner_race_plan):
    node_id = str(injected.get("node_id") or "")
    if node_id and node_id not in existing_nodes:
      planner_native_candidates.append(copy.deepcopy(injected))
      existing_nodes.add(node_id)

  ranked_native = _score_planner_native_candidates(
    observed_data,
    derived_data,
    policy,
    planner_native_candidates,
  )
  top_parallel_candidate = next(
    (
      dict(candidate)
      for candidate in ranked_native
      if str(candidate.get("node_id") or "") == "skill_purchase"
      and _safe_float(candidate.get("priority_score"), float("-inf")) != float("-inf")
    ),
    {},
  )
  selected_candidate = next(
    (
      dict(candidate)
      for candidate in ranked_native
      if str(candidate.get("node_id") or "") != "skill_purchase"
      if _safe_float(candidate.get("priority_score"), float("-inf")) != float("-inf")
    ),
    {},
  )
  if selected_candidate:
    selection_rationale = (
      f"selected {selected_candidate.get('node_id')} via planner scoring "
      f"(score={_safe_float(selected_candidate.get('priority_score'), 0.0):.3f})"
    )
    if top_parallel_candidate:
      selection_rationale += (
        f"; kept skill_purchase as a parallel gate candidate "
        f"(score={_safe_float(top_parallel_candidate.get('priority_score'), 0.0):.3f})"
      )
  else:
    selection_rationale = "no viable planner-native candidate after scoring; retaining compatibility payload"

  compat_candidates = [
    copy.deepcopy(candidate)
    for candidate in candidates
    if _candidate_is_compat(candidate.get("node_id"))
  ]
  ranked_all = ranked_native + compat_candidates
  return ranked_all, ranked_native, selected_candidate, selection_rationale


def _candidate_branch_kind(node_id: str, selected_func: str) -> str:
  node_id = str(node_id or "")
  selected_func = str(selected_func or "")
  if node_id == "race:race_day":
    return "forced_race_day"
  if node_id == "race:climax_locked":
    return "forced_climax_race"
  if node_id.startswith("race:scheduled:"):
    return "scheduled_race"
  if node_id.startswith("race:goal:"):
    return "goal_race"
  if node_id.startswith("race:g1_today:"):
    return "forced_race_day"
  if node_id == "race:mission":
    return "mission_race"
  if node_id == "race:rival":
    return "optional_rival_race"
  if node_id == "race:fallback":
    return "optional_fallback_race"
  if selected_func == "do_training":
    return "training"
  return "non_race"


def _selected_payload_from_candidate(selected_candidate, state_obj, action, planner_race_plan, ranked_native_candidates):
  selected_candidate = selected_candidate if isinstance(selected_candidate, dict) else {}
  state_obj = state_obj if isinstance(state_obj, dict) else {}
  planner_race_plan = planner_race_plan if isinstance(planner_race_plan, dict) else {}
  ranked_native_candidates = list(ranked_native_candidates or [])
  selected_action = dict(planner_race_plan.get("selected_action") or {})
  race_decision = dict(selected_action.get("trackblazer_race_decision") or planner_race_plan.get("race_decision") or {})
  race_lookahead = copy.deepcopy(selected_action.get("trackblazer_race_lookahead") or {})
  node_id = str(selected_candidate.get("node_id") or "")
  item_key = _candidate_item_key(node_id)
  selected_score = _safe_float(selected_candidate.get("priority_score"), float("-inf"))
  rationale = str(selected_candidate.get("rationale") or "")
  if selected_score != float("-inf"):
    rationale = f"{rationale} [planner_score={selected_score:.3f}]".strip()
  if not race_decision.get("reason") and rationale:
    race_decision["reason"] = rationale

  selected_func = "do_rest"
  training_name = None
  training_data = {}
  training_function = _action_value(action, "training_function") or "stat_weight_training"
  race_name = selected_action.get("race_name") or _action_value(action, "race_name") or "any"
  race_image_path = selected_action.get("race_image_path") or _action_value(action, "race_image_path")
  race_grade_target = selected_action.get("race_grade_target") or _action_value(action, "race_grade_target")
  prefer_rival_race = bool(selected_action.get("prefer_rival_race"))
  fallback_non_rival_race = bool(selected_action.get("fallback_non_rival_race"))
  scheduled_race = bool(selected_action.get("scheduled_race"))
  lobby_scheduled_race = bool(selected_action.get("trackblazer_lobby_scheduled_race"))
  race_mission_available = bool(selected_action.get("race_mission_available"))
  is_race_day = bool(selected_action.get("is_race_day"))
  trackblazer_climax_race_day = bool(selected_action.get("trackblazer_climax_race_day"))

  if node_id.startswith("train:"):
    selected_func = "do_training"
    training_name = _candidate_training_name(node_id)
    training_data = copy.deepcopy((state_obj.get("training_results") or {}).get(training_name) or {})
    if not training_data:
      training_data = copy.deepcopy((_action_value(action, "available_trainings") or {}).get(training_name) or {})
    if item_key and training_data:
      training_data["planner_selected_item_key"] = item_key
    race_decision = {
      **race_decision,
      "should_race": False,
      "branch_kind": "training",
    }
  elif node_id == "rest":
    selected_func = "do_rest"
    race_decision = {**race_decision, "should_race": False, "branch_kind": "non_race"}
  elif node_id == "recreation":
    selected_func = "do_recreation"
    race_decision = {**race_decision, "should_race": False, "branch_kind": "non_race"}
  elif node_id == "infirmary":
    selected_func = "do_infirmary"
    race_decision = {**race_decision, "should_race": False, "branch_kind": "non_race"}
  elif node_id.startswith("race:"):
    selected_func = "do_race"
    if node_id.startswith("race:scheduled:"):
      race_name = node_id.split("race:scheduled:", 1)[1] or race_name
      scheduled_race = True
      if race_name and race_name != "any" and not race_image_path:
        race_image_path = f"assets/races/{race_name}.png"
    if node_id == "race:race_day":
      is_race_day = True
    if node_id == "race:climax_locked":
      trackblazer_climax_race_day = True
      is_race_day = True
    if node_id == "race:mission":
      race_mission_available = True
    if node_id == "race:rival":
      prefer_rival_race = True
      fallback_non_rival_race = False
    if node_id == "race:fallback":
      prefer_rival_race = False
      fallback_non_rival_race = True
    race_decision = {
      **race_decision,
      "should_race": True,
    }

  branch_kind = _candidate_branch_kind(node_id, selected_func)
  race_decision = {
    **race_decision,
    "branch_kind": branch_kind,
  }

  fallback_action = {}
  warning_plan = {}
  if selected_func == "do_race":
    fallback_action = _planner_owned_primary_fallback_payload(
      node_id,
      ranked_native_candidates,
      state_obj=state_obj,
      action=action,
    )
    warning_plan = _planner_owned_warning_policy_for_node(
      branch_kind,
      node_id,
      ranked_native_candidates,
      state_obj=state_obj,
      action=action,
    )

  selected_payload = _planner_selected_action_payload(
    action,
    func=selected_func,
    training_name=training_name,
    training_function=training_function,
    training_data=training_data,
    race_name=race_name,
    race_image_path=race_image_path,
    race_grade_target=race_grade_target,
    prefer_rival_race=prefer_rival_race,
    fallback_non_rival_race=fallback_non_rival_race,
    scheduled_race=scheduled_race,
    lobby_scheduled_race=lobby_scheduled_race,
    race_mission_available=race_mission_available,
    is_race_day=is_race_day,
    trackblazer_climax_race_day=trackblazer_climax_race_day,
    race_decision=race_decision,
    race_lookahead=race_lookahead,
    warning_policy=warning_plan,
    fallback_action=fallback_action,
    branch_kind=branch_kind,
  )
  return selected_payload, branch_kind, warning_plan, fallback_action


def _apply_ranked_selection_to_race_plan(
  state_obj,
  action,
  planner_race_plan,
  selected_candidate,
  selection_rationale,
  ranked_native_candidates,
  policy,
):
  planner_race_plan = copy.deepcopy(planner_race_plan if isinstance(planner_race_plan, dict) else {})
  selected_candidate = selected_candidate if isinstance(selected_candidate, dict) else {}
  if not selected_candidate:
    return planner_race_plan

  selected_payload, branch_kind, warning_plan, fallback_action = _selected_payload_from_candidate(
    selected_candidate,
    state_obj,
    action,
    planner_race_plan,
    ranked_native_candidates,
  )
  fallback_policy = _planner_owned_fallback_chain_from_ranked(
    str(selected_candidate.get("node_id") or ""),
    branch_kind,
    ranked_native_candidates,
    warning_plan,
    policy,
    state_obj=state_obj,
    action=action,
  )
  if selected_payload.get("func") != "do_race":
    warning_plan = {}

  action_payload = {
    "planner_owned": True,
    "branch_kind": branch_kind,
    "func": selected_payload.get("func"),
    "options": copy.deepcopy(selected_payload),
    "fallback_action": copy.deepcopy(fallback_action),
    "available_actions": list((_capture_action_payload(action, state_obj=state_obj).get("available_actions") or [])),
  }
  race_check = copy.deepcopy(planner_race_plan.get("race_check") or {})
  race_check["branch_kind"] = branch_kind
  race_check["scheduled_race"] = bool(selected_payload.get("scheduled_race") or selected_payload.get("trackblazer_lobby_scheduled_race"))
  race_check["scheduled_race_source"] = (
    "lobby_button"
    if selected_payload.get("trackblazer_lobby_scheduled_race") else
    ("config_schedule" if selected_payload.get("scheduled_race") else "")
  )
  race_check["scout_required"] = bool(branch_kind == "optional_rival_race")

  race_scout = copy.deepcopy(planner_race_plan.get("race_scout") or {})
  race_scout["planner_owned"] = True
  race_scout["required"] = bool(branch_kind == "optional_rival_race")
  if selected_payload.get("func") != "do_race":
    race_scout["required"] = False

  race_entry_gate = copy.deepcopy(planner_race_plan.get("race_entry_gate") or {})
  if selected_payload.get("func") == "do_race":
    if not race_entry_gate:
      race_entry_gate = {
        "planner_owned": True,
        "opens_from_lobby": True,
        "expected_branch": "continue_to_race_list" if warning_plan.get("accept_warning") else "return_to_lobby",
        "warning_meaning": "This race would become the third consecutive race" if warning_plan.get("warning_expected") else "",
        "consecutive_warning_template": constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive"),
        "consecutive_warning_ok_template": constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive_ok"),
        "ok_action": "continue_to_race_list",
        "cancel_action": "return_to_lobby",
        "force_accept_warning": bool(warning_plan.get("force_accept_warning")),
      }
  else:
    race_entry_gate = {}

  planner_race_plan["branch_kind"] = branch_kind
  planner_race_plan["selection_rationale"] = str(selection_rationale or "")
  planner_race_plan["selected_action"] = copy.deepcopy(selected_payload)
  planner_race_plan["action_payload"] = action_payload
  planner_race_plan["race_check"] = race_check
  planner_race_plan["race_decision"] = copy.deepcopy(selected_payload.get("trackblazer_race_decision") or {})
  planner_race_plan["race_entry_gate"] = race_entry_gate
  planner_race_plan["race_scout"] = race_scout
  planner_race_plan["warning_plan"] = copy.deepcopy(warning_plan or {})
  planner_race_plan["fallback_policy"] = copy.deepcopy(fallback_policy or {})
  return planner_race_plan


def _build_step_sequence(state_obj, action, shop_buy_plan, item_execution_payload, race_plan=None) -> List[ExecutionStep]:
  race_plan = race_plan if isinstance(race_plan, dict) else {}
  selected_race_action = dict(race_plan.get("selected_action") or {})
  warning_plan = dict(race_plan.get("warning_plan") or {})
  race_scout_plan = dict(race_plan.get("race_scout") or {})
  branch_kind = str(race_plan.get("branch_kind") or "")
  planned_action = action
  if selected_race_action:
    planned_action = _clone_action(action)
    planned_action.func = selected_race_action.get("func") or _action_func(action)
    for key, value in selected_race_action.items():
      if key == "func":
        continue
      planned_action[key] = copy.deepcopy(value)
  action_func = _action_func(planned_action)
  prefer_rival_race = bool(selected_race_action.get("prefer_rival_race"))
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
    if branch_kind not in {"forced_race_day", "forced_climax_race", "pre_debut_debut_race"}:
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
    if race_scout_plan.get("required") or prefer_rival_race:
      step_sequence.append(
        ExecutionStep(
          step_id="execute_rival_scout",
          step_type="execute_rival_scout",
          intent="verify_rival_race_before_commit",
          screen_preconditions=["race_list_accessible"],
          success_transition="rival_race_confirmed",
          failure_transition=(race_scout_plan.get("failure_transition") or "revert_to_fallback_action"),
          notes=race_scout_plan.get("reason") or "",
        )
      )
    if warning_plan.get("warning_expected"):
      step_sequence.append(
        ExecutionStep(
          step_id="resolve_consecutive_race_warning",
          step_type="resolve_consecutive_race_warning",
          intent="follow_planner_warning_policy",
          screen_preconditions=["consecutive_warning_dialog_possible"],
          success_transition="warning_policy_resolved",
          failure_transition="warning_policy_failed",
          notes=warning_plan.get("cancel_reason") or warning_plan.get("accept_reason") or "",
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
        planned_clicks=_build_main_action_step_planned_clicks(planned_action),
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
      reassess_label = "post-whistle reassess" if has_whistle else "post-energy reassess"
      deferred_entry["reason"] = (
        f"{existing_reason}; deferred until {reassess_label}"
        if existing_reason else
        f"deferred until {reassess_label}"
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


def _item_binding_label_from_payload(payload) -> str:
  payload = payload if isinstance(payload, dict) else {}
  action_func = str(payload.get("func") or "unknown")
  training_name = payload.get("training_name")
  race_name = payload.get("race_name")
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
  effective_reassess = bool(reassess_after_item_use and execution_items)
  reassess_reason = _item_reassess_reason(execution_items) if effective_reassess else "Selected pre-action items can flow directly into the already selected action"
  reassess_kind = "continue_selected_action"
  if "reset_whistle" in trigger_items:
    reassess_kind = "reset_whistle_reroll"
  elif any(entry.get("usage_group") == "energy" for entry in list(execution_items or []) if isinstance(entry, dict)):
    reassess_kind = "energy_rescue_reassess" if item_context.get("energy_rescue") else "energy_item_reassess"
  has_execution_items = bool(execution_items)
  if has_execution_items:
    inventory_refresh = {
      "trigger": "post_shop_purchase_refresh" if shop_buy_plan else "pre_action_refresh",
      "required": True,
      "reason": (
        "refresh inventory against purchased items before item-use planning"
        if shop_buy_plan else
        "refresh inventory immediately before item-use planning"
      ),
    }
    path = [
      "inventory_refresh",
      "replan_items",
      "execute_pre_action_items",
      "await_lobby_after_items",
      "reassess" if effective_reassess else "selected_action_ready",
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
        "to": "reassess" if effective_reassess else "selected_action_ready",
        "reason": reassess_reason or "selected action remains valid after item use",
        "reobserve": bool(effective_reassess),
        "trigger_items": trigger_items,
      }
    )
  else:
    inventory_refresh = {
      "trigger": "none",
      "required": False,
      "reason": "No pre-action items selected; inventory refresh is skipped.",
    }
    path = ["selected_action_ready"]
    transitions = []

  action_mutations = {
    "trackblazer_shop_buy_plan": copy.deepcopy(list(shop_buy_plan or [])),
    "trackblazer_pre_action_items": copy.deepcopy(list(execution_items or [])),
    "trackblazer_item_use_context": copy.deepcopy(dict(item_context or {})),
    "trackblazer_reassess_after_item_use": bool(effective_reassess),
  }
  return {
    "planner_owned": True,
    "binding_label": _item_binding_label(action),
    "inventory_refresh": inventory_refresh,
    "execution_items": copy.deepcopy(list(execution_items or [])),
    "deferred_items": copy.deepcopy(list(deferred_use or [])),
    "reassess_transition": {
      "required": bool(effective_reassess),
      "transition_kind": reassess_kind,
      "reason": reassess_reason,
      "trigger_items": trigger_items,
      "selected_action_invalidated": bool(effective_reassess),
      "requires_reobserve": bool(effective_reassess),
      "requires_training_rescan": bool(effective_reassess),
      "target_phase": "collecting_main_state" if effective_reassess else "execute_main_action",
    },
    "path": path,
    "transitions": transitions,
    "compatibility_action_fields": action_mutations,
    "action_mutations": action_mutations,
  }


def _flow_timing_snapshot(flow):
  flow = flow if isinstance(flow, dict) else {}
  snapshot = {}
  for key in (
    "timing_open",
    "timing_scan",
    "timing_controls",
    "timing_close",
    "timing_total",
    "timing_confirm",
  ):
    if flow.get(key) is not None:
      snapshot[key] = copy.deepcopy(flow.get(key))
  if flow.get("scan_timing"):
    snapshot["scan_timing"] = copy.deepcopy(flow.get("scan_timing"))
  open_result = flow.get("open_result") or flow.get("entry_result")
  close_result = flow.get("close_result")
  if open_result:
    snapshot["open_result"] = copy.deepcopy(open_result)
  if close_result:
    snapshot["close_result"] = copy.deepcopy(close_result)
  return snapshot


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
  reassess_transition = dict(payload.get("reassess_transition") or {})
  if reassess_transition.get("required"):
    transition_kind = str(reassess_transition.get("transition_kind") or "")
    if transition_kind in {"energy_item_reassess", "energy_rescue_reassess"}:
      return [
        _planned_click(
          "Refresh selected training after item use",
          region_key="GAME_WINDOW_BBOX",
          note="Re-open training and rescan the selected training's fail chance before committing the planned training.",
        )
      ]
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
  turn_plan_snapshot = dict(planner_state.get("turn_plan") or {})
  turn_plan = TurnPlan.from_snapshot(turn_plan_snapshot) if turn_plan_snapshot else TurnPlan()
  inventory_plan = copy.deepcopy(dict((turn_plan.inventory_snapshot or {}).get("scan") or {}))
  shop_plan = copy.deepcopy(dict((turn_plan.shop_plan or {}).get("scan") or {}))
  would_buy = list(planner_state.get("shop_buy_plan") or (turn_plan.shop_plan or {}).get("would_buy") or [])
  would_use = list(planner_state.get("pre_action_items") or (turn_plan.item_plan or {}).get("pre_action_items") or [])
  deferred_use = list(planner_state.get("deferred_use") or (turn_plan.item_plan or {}).get("deferred_use") or [])
  planner_race_plan = dict(planner_state.get("race_plan") or turn_plan.race_plan or {})
  item_use_context = planner_state.get("item_use_context") or dict((turn_plan.item_plan or {}).get("context") or {})

  if planner_state.get("use_planner_race_sections") and planner_race_plan.get("planner_owned"):
    return {
      "race_check": copy.deepcopy(planner_race_plan.get("race_check") or {}),
      "race_decision": copy.deepcopy(planner_race_plan.get("race_decision") or {}),
      "race_entry_gate": copy.deepcopy(planner_race_plan.get("race_entry_gate") or {}),
      "race_scout": copy.deepcopy(planner_race_plan.get("race_scout") or {}),
      "race_warning_policy": copy.deepcopy(planner_state.get("warning_plan") or {}),
      "race_fallback_policy": copy.deepcopy(planner_state.get("fallback_policy") or {}),
      "inventory_scan": inventory_plan,
      "would_use": would_use,
      "would_use_context": item_use_context,
      "deferred_use": deferred_use,
      "shop_scan": shop_plan,
      "would_buy": would_buy,
      "shop_deviations": copy.deepcopy((turn_plan.shop_plan or {}).get("deviations") or []),
    }

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
      and _effective_rival_fallback_func(action) == "do_rest"
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
    "would_use_context": item_use_context,
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


def sync_turn_plan_execution_contract(state_obj, action, turn_plan: TurnPlan) -> TurnPlan:
  if not turn_plan:
    return turn_plan

  item_plan = dict(turn_plan.item_plan or {})
  race_plan = dict(turn_plan.race_plan or {})
  selected_action = dict(race_plan.get("selected_action") or {})

  if turn_plan.decision_path == "planner" and selected_action:
    binding_payload = {
      "func": selected_action.get("func"),
      "training_name": selected_action.get("training_name"),
      "race_name": selected_action.get("race_name"),
    }
  else:
    binding_payload = {
      "func": _action_func(action),
      "training_name": _action_value(action, "training_name"),
      "race_name": _action_value(action, "race_name"),
    }

  item_plan["selected_action_binding"] = copy.deepcopy(binding_payload)

  execution_payload = dict(item_plan.get("execution_payload") or {})
  if execution_payload:
    execution_payload["binding_label"] = _item_binding_label_from_payload(binding_payload)
    item_plan["execution_payload"] = execution_payload

  subgraph = dict(item_plan.get("subgraph") or {})
  if subgraph:
    subgraph["binding_label"] = _item_binding_label_from_payload(binding_payload)
    item_plan["subgraph"] = subgraph

  turn_plan.item_plan = item_plan
  turn_plan.step_sequence = _build_step_sequence(
    state_obj,
    action,
    list((turn_plan.shop_plan or {}).get("would_buy") or []),
    dict((turn_plan.item_plan or {}).get("execution_payload") or {}),
    race_plan if turn_plan.decision_path == "planner" else {},
  )
  return turn_plan


def set_turn_plan_decision_path(state_obj, action, decision_path, *, reason=""):
  planner_state = plan_once(state_obj, action, limit=8)
  turn_plan = TurnPlan.from_snapshot(dict((planner_state or {}).get("turn_plan") or {}))
  runtime_path = runtime_path_for_decision_path(str(decision_path or "legacy"))
  turn_plan.decision_path = decision_path_for_runtime_path(runtime_path)
  turn_plan.planner_metadata = {
    **dict(turn_plan.planner_metadata or {}),
    "decision_path": turn_plan.decision_path,
    "runtime_path": runtime_path,
    "fallback_reason": str(reason or ""),
  }
  turn_plan = sync_turn_plan_execution_contract(state_obj, action, turn_plan)
  planner_state["decision_path"] = turn_plan.decision_path
  planner_state["turn_plan"] = turn_plan.to_snapshot()
  if isinstance(state_obj, dict):
    state_obj[PLANNER_STATE_KEY] = planner_state
    set_trackblazer_runtime_path(
      state_obj,
      runtime_path,
      reason=reason,
      source="set_turn_plan_decision_path",
    )
  if turn_plan.decision_path == "planner":
    apply_turn_plan_action_payload(action, turn_plan)
    planner_state["turn_plan"] = turn_plan.to_snapshot()
    if isinstance(state_obj, dict):
      state_obj[PLANNER_STATE_KEY] = planner_state
  return planner_state, turn_plan


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

  if turn_plan and turn_plan.decision_path == "planner":
    race_plan = dict(turn_plan.race_plan or {})
    race_payload = dict(race_plan.get("action_payload") or {})
    selected_payload = dict(race_payload.get("options") or {})
    target_func = race_payload.get("func") or selected_payload.get("func") or _action_func(action)
    available_actions = list(race_payload.get("available_actions") or [])
    apply_selected_action_payload(
      action,
      {
        **selected_payload,
        "func": target_func,
      },
      available_actions=available_actions,
    )

    turn_plan.review_context = {
      **dict(turn_plan.review_context or {}),
      "selected_action": _build_selected_action_review_context(
        action,
        pre_action_items=list(item_plan.get("pre_action_items") or []),
        reassess_after_item_use=item_plan.get("reassess_after_item_use"),
      ),
    }
  return action


def update_turn_discussion_dual_run(state_obj, action, snapshot_context, legacy_planned_actions=None) -> Dict[str, Any]:
  if not isinstance(state_obj, dict):
    return {}
  planner_state = state_obj.get(PLANNER_STATE_KEY) or {}
  if not planner_state:
    return {}

  dual_run = copy.deepcopy(planner_state.get("dual_run") or {})
  turn_plan_snapshot = planner_state.get("turn_plan") or {}
  turn_plan = TurnPlan.from_snapshot(turn_plan_snapshot)
  legacy_planned_actions = (
    legacy_planned_actions
    if isinstance(legacy_planned_actions, dict) else
    build_review_planned_actions(state_obj, action, planner_state=planner_state)
  )

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
  forced_fallback = _planner_force_fallback(state_obj)
  runtime_path = get_trackblazer_runtime_path(state_obj, default=RUNTIME_PATH_LEGACY_RUNTIME)
  if forced_fallback:
    runtime_path = RUNTIME_PATH_PLANNER_FALLBACK_LEGACY
    set_trackblazer_runtime_path(
      state_obj,
      runtime_path,
      reason=forced_fallback.get("reason") or "",
      source="planner_force_fallback",
    )
  decision_path = decision_path_for_runtime_path(runtime_path)
  planner_race_plan = _build_planner_race_plan(
    state_obj,
    action,
    allow_live_rival_indicator_check=(runtime_path == RUNTIME_PATH_PLANNER_RUNTIME),
  )

  observed = hydrate_observed_turn_state(state_obj, action=action, planner_state={
    "freshness": freshness.to_dict(),
    "inventory_source": inventory_source,
    "shop_buy_plan": shop_buy_plan,
    "pre_action_items": execution_items,
    "deferred_use": deferred_use,
    "reassess_after_item_use": bool(reassess_after_item_use),
    "runtime": runtime_state,
    "planner_race_plan": planner_race_plan,
  })
  derived = derive_turn_state(observed, planner_state={
    "inventory_source": inventory_source,
    "shop_buy_plan": shop_buy_plan,
    "pre_action_items": execution_items,
    "reassess_after_item_use": bool(reassess_after_item_use),
    "planner_race_plan": planner_race_plan,
    "turn_plan": {"planner_metadata": {"runtime": runtime_state}},
  }, state_obj=state_obj, action=action)
  policy = getattr(config, "TRACKBLAZER_PLANNER_POLICY", {})
  candidates = enumerate_candidate_actions(
    observed,
    derived,
    policy,
  )
  candidate_ranking, ranked_native_candidates, selected_candidate, selection_rationale = _rank_candidates_for_selection(
    candidates,
    observed.to_dict(),
    derived.to_dict(),
    policy,
    planner_race_plan,
  )
  baseline_shop_buy_plan = copy.deepcopy(shop_buy_plan)
  shop_buy_plan, shop_deviations = _apply_shop_deviation_rules(
    shop_buy_plan,
    selected_candidate=selected_candidate,
    derived_data=derived.to_dict(),
    effective_shop_items=effective_shop_items,
    shop_items=shop_items,
    shop_summary=shop_summary,
    held_quantities=held_quantities,
  )
  if shop_buy_plan != baseline_shop_buy_plan:
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
  selected_race_plan = _apply_ranked_selection_to_race_plan(
    state_obj,
    action,
    planner_race_plan,
    selected_candidate,
    selection_rationale,
    ranked_native_candidates,
    policy,
  )
  planner_action = _planner_bound_action(action, selected_race_plan)
  projected_state = _project_inventory(plan_state, shop_buy_plan) if shop_buy_plan else plan_state
  item_use_plan = plan_item_usage(
    policy=getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None),
    state_obj=projected_state,
    action=planner_action,
    limit=limit,
  )
  execution_items, deferred_use, reassess_after_item_use = _attach_execution_item_plan(item_use_plan)
  item_execution_payload = _build_item_execution_payload(
    planner_action,
    shop_buy_plan,
    execution_items,
    deferred_use,
    reassess_after_item_use,
    item_use_plan.get("context") or {},
  )
  reobserve_boundaries = _build_reobserve_boundaries(item_execution_payload)
  projected_summary = copy.deepcopy(projected_state.get("trackblazer_inventory_summary") or {})
  item_context = dict(item_use_plan.get("context") or {})
  ranked_trainings = build_ranked_training_snapshot(
    state_obj=state_obj,
    available_trainings=copy.deepcopy(planner_action.get("available_trainings") or {}) if hasattr(planner_action, "get") else {},
    training_function=planner_action.get("training_function") if hasattr(planner_action, "get") else None,
  )

  turn_plan = TurnPlan(
    version=PLANNER_VERSION,
    decision_path=decision_path,
    freshness=freshness,
    selected_candidate=copy.deepcopy(selected_candidate),
    selection_rationale=str(selection_rationale or ""),
    candidate_ranking=copy.deepcopy(candidate_ranking),
    shop_plan={
      "would_buy": shop_buy_plan,
      "deviations": copy.deepcopy(shop_deviations),
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
        "func": ((selected_race_plan.get("selected_action") or {}).get("func")) if decision_path == "planner" else getattr(action, "func", None),
        "training_name": ((selected_race_plan.get("selected_action") or {}).get("training_name")) if decision_path == "planner" else (action.get("training_name") if hasattr(action, "get") else None),
        "race_name": ((selected_race_plan.get("selected_action") or {}).get("race_name")) if decision_path == "planner" else (action.get("race_name") if hasattr(action, "get") else None),
      },
      "reassess_boundary": {
        "required": bool(reassess_after_item_use),
        "trigger_items": [entry.get("key") for entry in execution_items if isinstance(entry, dict) and entry.get("key")],
        "reason": (item_execution_payload.get("reassess_transition") or {}).get("reason"),
        "transition_kind": (item_execution_payload.get("reassess_transition") or {}).get("transition_kind"),
      },
    },
    race_plan=copy.deepcopy(selected_race_plan),
    warning_plan=copy.deepcopy(selected_race_plan.get("warning_plan") or {}),
    fallback_policy=copy.deepcopy(selected_race_plan.get("fallback_policy") or {}),
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
      "inventory": _flow_timing_snapshot(inventory_flow),
      "shop": _flow_timing_snapshot(shop_flow),
      "skill": _flow_timing_snapshot(state_obj.get("skill_purchase_flow") or {}),
    },
    debug_summary={
      "shop_item_count": len(shop_buy_plan),
      "shop_deviation_count": len(shop_deviations),
      "item_candidate_count": len(list(item_use_plan.get("candidates") or [])),
      "execution_item_count": len(execution_items),
      "ranked_training_count": len(ranked_trainings),
      "inventory_source": inventory_source,
      "race_branch_kind": selected_race_plan.get("branch_kind"),
      "planner_native_candidate_count": len(ranked_native_candidates),
    },
    planner_metadata={
      "planner_version": PLANNER_VERSION,
      "decision_path": decision_path,
      "runtime_path": runtime_path,
      "inventory_source": inventory_source,
      "runtime": copy.deepcopy(runtime_state),
      "fallback_reason": forced_fallback.get("reason") if forced_fallback else "",
    },
    review_context={
      "selected_action": _build_selected_action_review_context(
        planner_action,
        pre_action_items=execution_items,
        reassess_after_item_use=reassess_after_item_use,
      ),
      "ranked_trainings": copy.deepcopy(ranked_trainings),
      "shop_deviations": copy.deepcopy(shop_deviations),
    },
    step_sequence=_build_step_sequence(
      state_obj,
      action,
      shop_buy_plan,
      item_execution_payload,
      dict(selected_race_plan or {}) if decision_path == "planner" else {},
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
