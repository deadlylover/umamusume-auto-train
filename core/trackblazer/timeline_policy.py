from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, Optional

import core.bot as bot
import utils.constants as constants


TIMELINE_POLICY_STATE_KEY = "trackblazer_timeline_policy"

_CLIMAX_LABEL = "Finale Underway"
_SUMMER_WINDOWS = {
  "Classic Year Early Jul",
  "Classic Year Late Jul",
  "Classic Year Early Aug",
  "Classic Year Late Aug",
  "Senior Year Early Jul",
  "Senior Year Late Jul",
  "Senior Year Early Aug",
  "Senior Year Late Aug",
}
_FINAL_SUMMER_LABEL = "Senior Year Late Aug"
_TIMELINE_INDEX = {label: index for index, label in enumerate(constants.TIMELINE)}
_FINAL_SUMMER_INDEX = _TIMELINE_INDEX.get(_FINAL_SUMMER_LABEL)
_CLIMAX_TRAINING_TURN_CAP = 3


def _safe_int(value, default=None):
  try:
    return int(value)
  except (TypeError, ValueError):
    return default


def _timeline_label(state_obj: Dict[str, Any]) -> str:
  year_text = str((state_obj or {}).get("year") or "").strip()
  turn_text = str((state_obj or {}).get("turn") or "").strip()
  if year_text in _TIMELINE_INDEX:
    return year_text
  combined = f"{year_text} {turn_text}".strip()
  if combined in _TIMELINE_INDEX:
    return combined
  return year_text or combined or turn_text


def _distance_to_label(current_index: Optional[int], labels: Iterable[str]) -> Optional[int]:
  if current_index is None:
    return None
  label_set = set(labels)
  for index in range(current_index, len(constants.TIMELINE)):
    if constants.TIMELINE[index] in label_set:
      return max(0, index - current_index)
  return None


def _distance_to_predicate(current_index: Optional[int], predicate) -> Optional[int]:
  if current_index is None:
    return None
  for index in range(current_index, len(constants.TIMELINE)):
    if predicate(constants.TIMELINE[index]):
      return max(0, index - current_index)
  return None


def _bond_cutoff_index() -> Optional[int]:
  cutoff_label = str(bot.get_trackblazer_bond_boost_cutoff() or "").strip()
  return _TIMELINE_INDEX.get(cutoff_label)


def _policy_cache_key(state_obj: Dict[str, Any]) -> tuple:
  timeline_label = _timeline_label(state_obj)
  return (
    timeline_label,
    str((state_obj or {}).get("turn") or "").strip(),
    bool((state_obj or {}).get("trackblazer_climax")),
    bool((state_obj or {}).get("trackblazer_climax_locked_race")),
    bool((state_obj or {}).get("trackblazer_climax_race_day")),
    bool((state_obj or {}).get("trackblazer_climax_race_day_banner")),
    bool((state_obj or {}).get("trackblazer_climax_race_day_button")),
    _safe_int((state_obj or {}).get("trackblazer_trainings_remaining_upper_bound")),
  )


def _build_timeline_policy(state_obj: Dict[str, Any]) -> Dict[str, Any]:
  state_obj = state_obj if isinstance(state_obj, dict) else {}
  year_text = str(state_obj.get("year") or "").strip()
  turn_text = str(state_obj.get("turn") or "").strip()
  timeline_label = _timeline_label(state_obj)
  timeline_index = _TIMELINE_INDEX.get(timeline_label)
  is_summer_window = timeline_label in _SUMMER_WINDOWS
  is_climax_window = bool(
    state_obj.get("trackblazer_climax")
    or year_text == _CLIMAX_LABEL
    or timeline_label == _CLIMAX_LABEL
  )

  forced_signal_sources = []
  if bool(state_obj.get("trackblazer_climax_race_day")):
    forced_signal_sources.append("state_flag")
  if bool(state_obj.get("trackblazer_climax_race_day_banner")):
    forced_signal_sources.append("banner")
  if bool(state_obj.get("trackblazer_climax_race_day_button")):
    forced_signal_sources.append("button")
  is_forced_climax_race_day = bool(forced_signal_sources)

  is_generic_race_day = turn_text == "Race Day"
  is_finale_underway_training_turn = bool(
    is_climax_window
    and not is_forced_climax_race_day
    and not is_generic_race_day
  )

  trainings_remaining_upper_bound = _safe_int(
    state_obj.get("trackblazer_trainings_remaining_upper_bound"),
  )
  if trainings_remaining_upper_bound is None and is_finale_underway_training_turn:
    trainings_remaining_upper_bound = _CLIMAX_TRAINING_TURN_CAP

  bond_cutoff_index = _bond_cutoff_index()
  is_pre_bond_cutoff = bool(
    bond_cutoff_index is not None
    and timeline_index is not None
    and timeline_index <= bond_cutoff_index
  )
  is_post_bond_cutoff = bool(
    bond_cutoff_index is not None
    and timeline_index is not None
    and timeline_index > bond_cutoff_index
  )
  is_post_final_summer = bool(
    _FINAL_SUMMER_INDEX is not None
    and timeline_index is not None
    and timeline_index > _FINAL_SUMMER_INDEX
  )

  if is_forced_climax_race_day:
    phase_kind = "forced_climax_race_day"
  elif is_finale_underway_training_turn:
    phase_kind = "finale_underway_training_turn"
  elif is_generic_race_day:
    phase_kind = "forced_race_day"
  elif is_summer_window:
    phase_kind = "summer_training_window"
  else:
    phase_kind = "normal_training_window"

  return {
    "timeline_label": timeline_label,
    "timeline_index": timeline_index,
    "year": year_text,
    "turn": turn_text,
    "phase_kind": phase_kind,
    "is_summer": is_summer_window,
    "is_summer_window": is_summer_window,
    "summer_window": is_summer_window,
    "is_climax": is_climax_window,
    "is_climax_window": is_climax_window,
    "climax_window": is_climax_window,
    "tsc_active": is_climax_window,
    "is_finale_underway_training_turn": is_finale_underway_training_turn,
    "is_forced_climax_race_day": is_forced_climax_race_day,
    "is_race_day": is_generic_race_day,
    "is_pre_bond_cutoff": is_pre_bond_cutoff,
    "is_post_bond_cutoff": is_post_bond_cutoff,
    "past_bond_training_cutoff": is_post_bond_cutoff,
    "is_post_final_summer": is_post_final_summer,
    "past_final_summer": is_post_final_summer,
    "trainings_remaining_upper_bound": trainings_remaining_upper_bound,
    "optional_races_allowed": not (
      is_finale_underway_training_turn
      or is_forced_climax_race_day
      or is_generic_race_day
    ),
    "forced_climax_signal_sources": forced_signal_sources,
    "summer_distance": 0 if is_summer_window else _distance_to_label(timeline_index, _SUMMER_WINDOWS),
    "climax_distance": 0 if is_climax_window else _distance_to_predicate(
      timeline_index,
      lambda label: label == _CLIMAX_LABEL,
    ),
    "bond_cutoff_label": str(bot.get_trackblazer_bond_boost_cutoff() or "").strip(),
    "bond_cutoff_index": bond_cutoff_index,
  }


def get_trackblazer_timeline_policy(state_obj, *, copy_result=True) -> Dict[str, Any]:
  state_obj = state_obj if isinstance(state_obj, dict) else {}
  cache_key = _policy_cache_key(state_obj)
  cached = state_obj.get(TIMELINE_POLICY_STATE_KEY)
  if isinstance(cached, dict) and cached.get("_cache_key") == cache_key:
    payload = dict(cached)
  else:
    payload = _build_timeline_policy(state_obj)
    payload["_cache_key"] = cache_key
    state_obj[TIMELINE_POLICY_STATE_KEY] = payload
  payload.pop("_cache_key", None)
  return copy.deepcopy(payload) if copy_result else payload
