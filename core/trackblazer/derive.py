from __future__ import annotations

from typing import Any, Dict

from core.trackblazer.models import DerivedTurnState, ObservedTurnState


_SUMMER_TOKENS = ("early jul", "late jul", "early aug", "late aug")


def _safe_ratio(numerator, denominator):
  try:
    denominator = float(denominator)
    if denominator <= 0:
      return None
    return round(float(numerator) / denominator, 4)
  except (TypeError, ValueError, ZeroDivisionError):
    return None


def derive_turn_state(observed: ObservedTurnState, planner_state=None) -> DerivedTurnState:
  observed_data = observed.to_dict()
  planner_state = planner_state if isinstance(planner_state, dict) else {}
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
  data: Dict[str, Any] = {
    "turn_key": f"{observed_data.get('year') or '?'}|{observed_data.get('turn') or '?'}",
    "timeline_label": observed_data.get("turn"),
    "is_summer": any(token in turn_label for token in _SUMMER_TOKENS),
    "energy_ratio": _safe_ratio(observed_data.get("energy_level"), observed_data.get("max_energy")),
    "race_available_summary": {
      "rival_indicator": bool(observed_data.get("rival_indicator_detected")),
      "race_mission_available": bool(observed_data.get("race_mission_available")),
      "lobby_scheduled_race": bool(observed_data.get("trackblazer_lobby_scheduled_race")),
      "climax_locked_race": bool(observed_data.get("trackblazer_climax_locked_race")),
    },
    "training_value_summary": {
      "best_training_name": (observed_data.get("selected_action") or {}).get("training_name"),
      "best_score": best_score,
      "best_total": training_total,
    },
    "inventory_source": planner_state.get("inventory_source"),
    "shop_buy_count": len(planner_state.get("shop_buy_plan") or []),
    "pre_action_item_count": len(planner_state.get("pre_action_items") or []),
    "reassess_after_item_use": bool(planner_state.get("reassess_after_item_use")),
    "skill_scan_state": (((planner_state.get("turn_plan") or {}).get("planner_metadata") or {}).get("runtime") or {}).get("pending_skill_scan") or {},
  }
  return DerivedTurnState(data=data)
