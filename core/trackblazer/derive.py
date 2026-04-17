from __future__ import annotations

import copy
from collections import OrderedDict
from typing import Any, Dict

import core.config as config
import utils.constants as constants
from core.actions import Action
from core.trackblazer_item_use import _hammer_usage_state, _usage_context
from core.trackblazer.models import DerivedTurnState, ObservedTurnState
from core.trackblazer.timeline_policy import get_trackblazer_timeline_policy
from core.trackblazer_race_logic import (
  get_optional_race_low_energy_override,
  get_race_lookahead_energy_advice,
)
from utils.log import warning


_SUMMER_TOKENS = ("early jul", "late jul", "early aug", "late aug")
_MEGAPHONE_KEYS = ("motivating_megaphone", "empowering_megaphone", "coaching_megaphone")
_STAT_ITEM_PRIORITY = ("manual", "scroll", "notepad")
_LOOKAHEAD_CACHE_MAX = 128
_LOOKAHEAD_CACHE = OrderedDict()


def _lookahead_cache_get(cache_key):
  cached = _LOOKAHEAD_CACHE.get(cache_key)
  if cached is not None:
    _LOOKAHEAD_CACHE.move_to_end(cache_key)
  return cached


def _lookahead_cache_store(cache_key, value):
  _LOOKAHEAD_CACHE[cache_key] = value
  _LOOKAHEAD_CACHE.move_to_end(cache_key)
  while len(_LOOKAHEAD_CACHE) > _LOOKAHEAD_CACHE_MAX:
    _LOOKAHEAD_CACHE.popitem(last=False)


def _safe_ratio(numerator, denominator):
  try:
    denominator = float(denominator)
    if denominator <= 0:
      return None
    return round(float(numerator) / denominator, 4)
  except (TypeError, ValueError, ZeroDivisionError):
    return None


def _safe_float(value, default=None):
  try:
    return float(value)
  except (TypeError, ValueError):
    return default


def _training_value_class(score, cutoffs):
  score = _safe_float(score, 0.0) or 0.0
  if score >= _safe_float(cutoffs.get("very_strong"), 60.0):
    return "very_strong"
  if score >= _safe_float(cutoffs.get("strong"), 50.0):
    return "strong"
  if score >= _safe_float(cutoffs.get("adequate"), 40.0):
    return "adequate"
  return "weak"


def _merge_training_inputs(training_results, available_trainings):
  merged = {}
  raw_training_results = training_results if isinstance(training_results, dict) else {}
  scored_available_trainings = available_trainings if isinstance(available_trainings, dict) else {}

  for training_name in sorted(set(raw_training_results) | set(scored_available_trainings)):
    raw_entry = raw_training_results.get(training_name)
    scored_entry = scored_available_trainings.get(training_name)
    merged_entry = {}
    if isinstance(raw_entry, dict):
      merged_entry.update(copy.deepcopy(raw_entry))
    if isinstance(scored_entry, dict):
      merged_entry.update(copy.deepcopy(scored_entry))
    if merged_entry:
      merged[training_name] = merged_entry

  return merged


def _energy_class(energy_ratio, cutoffs):
  if energy_ratio is None:
    return "ok"
  if energy_ratio < _safe_float(cutoffs.get("critical"), 0.05):
    return "critical"
  if energy_ratio < _safe_float(cutoffs.get("low"), 0.30):
    return "low"
  if energy_ratio < _safe_float(cutoffs.get("ok"), 0.70):
    return "ok"
  return "high"


def _make_training_action(training_name, training_data):
  action = Action()
  action.func = "do_training"
  action["training_name"] = training_name
  action["training_data"] = copy.deepcopy(training_data or {})
  return action


def _timeline_index(turn_label):
  if turn_label in constants.TIMELINE:
    return constants.TIMELINE.index(turn_label)
  return None


def _distance_to_labels(current_index, predicate):
  if current_index is None:
    return None
  for index in range(current_index, len(constants.TIMELINE)):
    if predicate(constants.TIMELINE[index]):
      return max(0, index - current_index)
  return None


def _selector_entry_map(selector):
  selector = selector if isinstance(selector, dict) else {}
  return {
    f"{entry.get('year')} {entry.get('date')}".strip(): entry
    for entry in (selector.get("dates") or [])
    if isinstance(entry, dict)
  }


def _lookahead_summary(state_obj, observed_data, policy):
  turn_key = f"{observed_data.get('year') or '?'}|{observed_data.get('turn') or '?'}"
  selector_hash = str(hash(repr(getattr(config, "OPERATOR_RACE_SELECTOR", None))))
  cache_key = (turn_key, selector_hash)
  cached = _lookahead_cache_get(cache_key)
  if cached is not None:
    return copy.deepcopy(cached)

  lookahead = get_race_lookahead_energy_advice(
    state_obj,
    getattr(config, "OPERATOR_RACE_SELECTOR", None),
  ) or {}
  selector = getattr(config, "OPERATOR_RACE_SELECTOR", None) or {}
  horizon = max(1, int(policy.get("lookahead_horizon_turns", 3)))
  current_label = str(observed_data.get("year") or "").strip()
  current_index = _timeline_index(current_label)
  entry_map = _selector_entry_map(selector)
  next_n_races = []
  if current_index is not None:
    for offset in range(1, horizon + 1):
      index = current_index + offset
      if index >= len(constants.TIMELINE):
        break
      entry = entry_map.get(constants.TIMELINE[index]) or {}
      if entry.get("enabled") is False:
        continue
      if entry.get("race_allowed") and entry.get("selected_race"):
        next_n_races.append(
          {
            "distance": offset,
            "turn_label": constants.TIMELINE[index],
            "race_name": entry.get("name") or entry.get("selected_race"),
          }
        )

  g1_distance = None
  races_today = list(constants.RACES.get(observed_data.get("year"), []) or [])
  if any((race or {}).get("grade") == "G1" for race in races_today):
    g1_distance = 0
  elif current_index is not None:
    for offset in range(1, len(constants.TIMELINE) - current_index):
      label = constants.TIMELINE[current_index + offset]
      races = list(constants.RACES.get(label, []) or [])
      if any((race or {}).get("grade") == "G1" for race in races):
        g1_distance = offset
        break

  next_race_day_distance = next_n_races[0]["distance"] if next_n_races else None
  summary = {
    "source": copy.deepcopy(lookahead),
    "next_turn_races_count": 1 if next_n_races and next_n_races[0]["distance"] == 1 else 0,
    "next_n_turns_races_count": len(next_n_races),
    "projected_energy_deficit": bool(lookahead.get("conserve") and not lookahead.get("can_train_and_race")),
    "next_g1_distance": g1_distance,
    "next_race_day_distance": next_race_day_distance,
  }
  _lookahead_cache_store(cache_key, copy.deepcopy(summary))
  return summary


def _race_opportunity(observed_data, lookahead_summary, timeline_policy):
  races_today = list(constants.RACES.get(observed_data.get("year"), []) or [])
  g1_today = any((race or {}).get("grade") == "G1" for race in races_today)
  race_scout = copy.deepcopy(observed_data.get("race_scout") or observed_data.get("rival_scout") or {})
  race_found = race_scout.get("race_found", race_scout.get("rival_found"))
  race_scout_rejected = bool(
    race_scout.get("executed")
    and race_found is False
    and race_scout.get("blocks_optional_race", race_scout.get("blocks_optional_rival_race", True))
  )
  mandatory_today = bool(
    bool((timeline_policy or {}).get("is_race_day"))
    or g1_today
    or bool((timeline_policy or {}).get("is_forced_climax_race_day"))
    or observed_data.get("trackblazer_climax_locked_race")
  )
  return {
    "rival_visible": bool(observed_data.get("rival_indicator_detected")),
    "maiden_available": bool(observed_data.get("trackblazer_maiden_available")),
    "race_scout": race_scout,
    "race_scout_rejected": race_scout_rejected,
    "race_scout_rejection_reason": str(race_scout.get("reason") or "") if race_scout_rejected else "",
    "race_scout_no_double_aptitude_match": bool(race_scout_rejected and race_scout.get("no_double_aptitude_match")),
    "rival_scout": race_scout,
    "rival_scout_rejected": race_scout_rejected,
    "rival_scout_rejection_reason": str(race_scout.get("reason") or "") if race_scout_rejected else "",
    "rival_scout_no_double_aptitude_match": bool(race_scout_rejected and race_scout.get("no_double_aptitude_match")),
    "lobby_scheduled": bool(observed_data.get("trackblazer_lobby_scheduled_race")),
    "climax_locked": bool(
      observed_data.get("trackblazer_climax_locked_race")
      or bool((timeline_policy or {}).get("is_forced_climax_race_day"))
    ),
    "mandatory_today": mandatory_today,
    "optional_safe_under_lookahead": not bool(lookahead_summary.get("projected_energy_deficit")),
  }


def _item_availability(state_obj):
  summary = copy.deepcopy((state_obj or {}).get("trackblazer_inventory_summary") or {})
  held_quantities = dict(summary.get("held_quantities") or {})
  shop_items = set((state_obj or {}).get("trackblazer_shop_items") or [])
  _, hammer_spendable = _hammer_usage_state(held_quantities)
  return {
    "held_vita_total": sum(int(held_quantities.get(key, 0) or 0) for key in ("vita_65", "vita_40", "vita_20", "royal_kale_juice")),
    "held_reset_whistles": int(held_quantities.get("reset_whistle", 0) or 0),
    "held_megaphones": {
      key: int(held_quantities.get(key, 0) or 0)
      for key in _MEGAPHONE_KEYS
    },
    "held_hammers": {
      "artisan": int(held_quantities.get("artisan_cleat_hammer", 0) or 0),
      "master": int(held_quantities.get("master_cleat_hammer", 0) or 0),
      "spendable": copy.deepcopy(hammer_spendable),
    },
    "held_matching_stat_books": {
      key: int(value or 0)
      for key, value in held_quantities.items()
      if any(token in str(key) for token in _STAT_ITEM_PRIORITY)
    },
    "affordable_shop_equivalents": {
      "vita": [key for key in ("vita_65", "vita_40", "vita_20", "royal_kale_juice") if key in shop_items],
      "megaphones": [key for key in _MEGAPHONE_KEYS if key in shop_items],
      "matching_stat_books": [key for key in shop_items if any(token in str(key) for token in _STAT_ITEM_PRIORITY)],
    },
  }


def _timeline_window(observed_data, policy):
  del policy
  return get_trackblazer_timeline_policy(observed_data)


def _training_value(training_results, state_obj, policy):
  results = []
  for training_name, training_data in (training_results or {}).items():
    training_data = training_data if isinstance(training_data, dict) else {}
    action = _make_training_action(training_name, training_data)
    usage_context = _usage_context(
      state_obj,
      action,
      policy=getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None),
    ) or {}
    held_quantities = dict(((state_obj or {}).get("trackblazer_inventory_summary") or {}).get("held_quantities") or {})
    shop_items = set((state_obj or {}).get("trackblazer_shop_items") or [])
    best_item_key = None
    if int(held_quantities.get("reset_whistle", 0) or 0) > 0 and usage_context.get("weak_summer_training"):
      best_item_key = "reset_whistle"
    elif any(int(held_quantities.get(key, 0) or 0) > 0 for key in _MEGAPHONE_KEYS) and usage_context.get("commit_training_after_items"):
      best_item_key = next((key for key in _MEGAPHONE_KEYS if int(held_quantities.get(key, 0) or 0) > 0), None)
    elif usage_context.get("failure_bypassed_by_items"):
      planned_failure_bypass_items = [
        key
        for key in list(training_data.get("trackblazer_failure_bypass_items") or [])
        if key
      ]
      for key in planned_failure_bypass_items + ["rich_hand_cream", "miracle_cure", "vita_20", "vita_40", "vita_65", "royal_kale_juice", "good_luck_charm"]:
        if int(held_quantities.get(key, 0) or 0) > 0 or key in shop_items:
          best_item_key = key
          break
    elif usage_context.get("training_name"):
      for suffix in _STAT_ITEM_PRIORITY:
        match_key = f"{training_name}_{suffix}"
        if int(held_quantities.get(match_key, 0) or 0) > 0 or match_key in shop_items:
          best_item_key = match_key
          break
    score_tuple = training_data.get("score_tuple") or ()
    score = _safe_float(score_tuple[0], None) if score_tuple else None
    if score is None:
      weighted_score = _safe_float(training_data.get("weighted_stat_score"), None)
      if weighted_score is None:
        score = 0.0
      else:
        score = weighted_score + (_safe_float(training_data.get("bond_boost"), 0.0) or 0.0)
    total_stat_gain = sum(
      int(value)
      for stat_name, value in (training_data.get("stat_gains") or {}).items()
      if stat_name != "sp" and isinstance(value, (int, float))
    )
    matching_stat_gain = int(((training_data.get("stat_gains") or {}).get(training_name)) or 0)
    item_assist_available = bool(best_item_key)
    item_assist_score_delta = 0.0
    if item_assist_available:
      assisted_score = _safe_float(usage_context.get("training_score"), 0.0) or 0.0
      # usage_context["training_score"] is the absolute score snapshot for the
      # selected training, not an additive bonus from the item itself.
      item_assist_score_delta = max(0.0, assisted_score - float(score or 0.0))
    results.append(
      {
        "name": training_name,
        "score": float(score or 0.0),
        "total_stat_gain": total_stat_gain,
        "matching_stat_gain": matching_stat_gain,
        "failure": int(training_data.get("failure") or 0),
        "support_count": int(training_data.get("total_supports") or 0),
        "rainbow_count": int(training_data.get("total_rainbow_friends") or 0),
        "value_class": _training_value_class(score, policy.get("training_value_class_cutoffs") or {}),
        "item_assist_available": item_assist_available,
        "item_assist_score_delta": float(item_assist_score_delta or 0.0),
        "best_item_key": best_item_key,
        "usage_context": {
          "commit_training_after_items": bool(usage_context.get("commit_training_after_items")),
          "weak_summer_training": bool(usage_context.get("weak_summer_training")),
          "failure_bypassed_by_items": bool(usage_context.get("failure_bypassed_by_items")),
        },
      }
    )
  return results


def _best_training_summary(training_value):
  if not training_value:
    return {
      "best_training_name": None,
      "best_score": None,
      "best_total": None,
    }
  best_entry = max(training_value, key=lambda entry: entry.get("score", 0.0))
  return {
    "best_training_name": best_entry.get("name"),
    "best_score": best_entry.get("score"),
    "best_total": best_entry.get("total_stat_gain"),
  }


def derive_turn_state(observed: ObservedTurnState, planner_state=None, state_obj=None, action=None) -> DerivedTurnState:
  observed_data = observed.to_dict()
  planner_state = planner_state if isinstance(planner_state, dict) else {}
  state_obj = state_obj if isinstance(state_obj, dict) else {}
  policy = getattr(config, "TRACKBLAZER_PLANNER_POLICY", {}) or {}
  turn_label = str(observed_data.get("turn") or "").lower()
  derivation_warnings = []

  race_low_energy_override = {}
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

  observation_status = copy.deepcopy(observed_data.get("observation_status") or {})
  missing_inputs = list(observed_data.get("missing_inputs") or [])
  energy_ratio = _safe_ratio(observed_data.get("energy_level"), observed_data.get("max_energy"))
  training_inputs = _merge_training_inputs(
    observed_data.get("training_results") or {},
    observed_data.get("available_trainings") or {},
  )
  training_value = _training_value(training_inputs, state_obj, policy)
  lookahead_summary = {}
  try:
    lookahead_summary = _lookahead_summary(state_obj, observed_data, policy)
  except Exception as exc:
    warning(f"[TB_PLANNER] derive_turn_state race_lookahead failed: {exc}")
    derivation_warnings.append({
      "source": "race_lookahead",
      "error": str(exc),
    })
    lookahead_summary = {
      "source": {},
      "next_turn_races_count": 0,
      "next_n_turns_races_count": 0,
      "projected_energy_deficit": False,
      "next_g1_distance": None,
      "next_race_day_distance": None,
    }
  timeline_policy = _timeline_window(observed_data, policy)
  skill_purchase_check = copy.deepcopy(observed_data.get("skill_purchase_check") or {})
  skill_cadence_open = bool(skill_purchase_check.get("should_check"))
  skill_cadence_reason = str(skill_purchase_check.get("reason") or "")
  data: Dict[str, Any] = {
    "turn_key": f"{observed_data.get('year') or '?'}|{observed_data.get('turn') or '?'}",
    "timeline_label": observed_data.get("turn"),
    "is_summer": bool(timeline_policy.get("is_summer_window")),
    "energy_ratio": energy_ratio,
    "energy_class": _energy_class(energy_ratio, policy.get("energy_class_cutoffs") or {}),
    "observation_status": observation_status,
    "missing_inputs": missing_inputs,
    "training_value": training_value,
    "training_value_summary": _best_training_summary(training_value),
    "race_opportunity": _race_opportunity(observed_data, lookahead_summary, timeline_policy),
    "race_available_summary": {
      "rival_indicator": bool(observed_data.get("rival_indicator_detected")),
      "race_mission_available": bool(observed_data.get("race_mission_available")),
      "lobby_scheduled_race": bool(observed_data.get("trackblazer_lobby_scheduled_race")),
      "climax_locked_race": bool(observed_data.get("trackblazer_climax_locked_race")),
    },
    "inventory_source": planner_state.get("inventory_source"),
    "shop_buy_count": len(planner_state.get("shop_buy_plan") or []),
    "pre_action_item_count": len(planner_state.get("pre_action_items") or []),
    "reassess_after_item_use": bool(planner_state.get("reassess_after_item_use")),
    "skill_scan_state": (((planner_state.get("turn_plan") or {}).get("planner_metadata") or {}).get("runtime") or {}).get("pending_skill_scan") or {},
    "item_availability": _item_availability(state_obj),
    "timeline_window": copy.deepcopy(timeline_policy),
    "timeline_policy": copy.deepcopy(timeline_policy),
    "lookahead_summary": lookahead_summary,
    "skill_cadence_open": skill_cadence_open,
    "skill_cadence_reason": skill_cadence_reason,
    "race_logic_summary": {
      "low_energy_override": race_low_energy_override,
      "lookahead": copy.deepcopy(lookahead_summary.get("source") or {}),
    },
    "derivation_warnings": derivation_warnings,
  }
  return DerivedTurnState(data=data)
