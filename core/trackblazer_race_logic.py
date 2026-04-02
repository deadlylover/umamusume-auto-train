"""Trackblazer-specific race-vs-training decision gate.

This is intentionally a small heuristic layer that can be iterated on during
live testing. It does not open menus or click. It only inspects the current
state and selected action, then returns a structured decision payload that the
main loop can log, preview, and act on.
"""

import utils.constants as constants
import core.bot as bot
import core.config as config
from core.race_selector import get_race_gate_for_turn_label
from core.trackblazer_item_use import (
  get_training_behavior_settings,
  get_training_behavior_strong_training_score_threshold,
)
from utils.log import debug, info, warning


_SUMMER_WINDOWS = (
  "Classic Year Early Jul",
  "Classic Year Late Jul",
  "Classic Year Early Aug",
  "Classic Year Late Aug",
  "Senior Year Early Jul",
  "Senior Year Late Jul",
  "Senior Year Early Aug",
  "Senior Year Late Aug",
)

_WEAK_TRAINING_THRESHOLD = 35
_JUNIOR_THREE_SUPPORT_MIN_SUPPORTS = 3
_MIN_RACE_ENERGY_PCT = 0.05
_ZERO_ENERGY_OPTIONAL_RACE_PCT = 0.02
_GRADE_ORDER = ("G1", "G2", "G3", "OP", "Pre-OP")
_ZERO_ENERGY_OPTIONAL_RACE_VITA_KEYS = (
  "vita_65",
  "vita_40",
  "vita_20",
  "royal_kale_juice",
  "energy_drink_max",
  "energy_drink_max_ex",
)
_ZERO_ENERGY_OPTIONAL_RACE_RECOVERY_KEYS = (
  "miracle_cure",
  "rich_hand_cream",
)


def _safe_int(value):
  try:
    return int(value)
  except (TypeError, ValueError):
    return None


def _safe_float(value):
  try:
    return float(value)
  except (TypeError, ValueError):
    return None


def _is_summer(year):
  return str(year or "") in _SUMMER_WINDOWS


def _optional_race_low_energy_override(state_obj):
  state_obj = state_obj if isinstance(state_obj, dict) else {}
  training_behavior = get_training_behavior_settings(
    getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None)
  )
  energy_level = _safe_float(state_obj.get("energy_level"))
  max_energy = _safe_float(state_obj.get("max_energy"))
  if energy_level is None or max_energy is None or max_energy <= 0:
    return {
      "low_energy": False,
      "energy_pct": 0.0,
      "prefer_rest": False,
      "allow_race": False,
      "reason": "",
    }

  energy_pct = energy_level / max(max_energy, 1.0)
  low_energy = energy_pct <= _ZERO_ENERGY_OPTIONAL_RACE_PCT
  held_quantities = (state_obj.get("trackblazer_inventory_summary") or {}).get("held_quantities") or {}
  has_vita = any(
    (_safe_int(held_quantities.get(item_key)) or 0) > 0
    for item_key in _ZERO_ENERGY_OPTIONAL_RACE_VITA_KEYS
  )
  has_recovery_cover = any(
    (_safe_int(held_quantities.get(item_key)) or 0) > 0
    for item_key in _ZERO_ENERGY_OPTIONAL_RACE_RECOVERY_KEYS
  )
  allow_vita = bool(
    low_energy
    and training_behavior.get("allow_zero_energy_optional_race_with_vita")
    and has_vita
  )
  allow_recovery = bool(
    low_energy
    and training_behavior.get("allow_zero_energy_optional_race_with_recovery_items")
    and has_recovery_cover
  )
  allow_race = allow_vita or allow_recovery
  prefer_rest = bool(
    low_energy
    and training_behavior.get("prefer_rest_on_zero_energy_optional_race")
    and not allow_race
  )
  if allow_vita:
    reason = "zero-energy optional race override: held Vita/energy item can cover the rival race"
  elif allow_recovery:
    reason = "zero-energy optional race override: held Miracle Cure / Rich Hand Cream allows rival race"
  elif prefer_rest:
    reason = "zero-energy optional race safeguard: prefer rest over an unscheduled rival race"
  else:
    reason = ""
  return {
    "low_energy": low_energy,
    "energy_pct": energy_pct,
    "prefer_rest": prefer_rest,
    "allow_race": allow_race,
    "reason": reason,
  }


def _total_stat_gain(action):
  training_data = action.get("training_data") if hasattr(action, "get") else None
  if not isinstance(training_data, dict):
    return None
  stat_gains = training_data.get("stat_gains")
  if not isinstance(stat_gains, dict):
    return None

  total = 0
  found = False
  for stat_name, value in stat_gains.items():
    if stat_name == "sp":
      continue
    normalized = _safe_int(value)
    if normalized is None:
      continue
    total += normalized
    found = True
  return total if found else None


def _training_score(action):
  training_data = action.get("training_data") if hasattr(action, "get") else None
  if not isinstance(training_data, dict):
    return None
  score_tuple = training_data.get("score_tuple")
  if not isinstance(score_tuple, (tuple, list)) or not score_tuple:
    return None
  return _safe_float(score_tuple[0])


def _support_count(action):
  training_data = action.get("training_data") if hasattr(action, "get") else None
  if not isinstance(training_data, dict):
    return None
  return _safe_int(training_data.get("total_supports"))


def _race_grade_rank(grade):
  try:
    return _GRADE_ORDER.index(grade)
  except ValueError:
    return len(_GRADE_ORDER)


def _best_race_by_grade(races, allowed_grades=None):
  if not isinstance(races, list):
    return None
  allowed_set = set(allowed_grades or [])
  best_race = None
  best_fans = -1
  for race in races:
    if not isinstance(race, dict):
      continue
    grade = race.get("grade")
    if allowed_set and grade not in allowed_set:
      continue
    fans_gained = _safe_int(((race.get("fans") or {}).get("gained"))) or 0
    if best_race is None or fans_gained > best_fans:
      best_race = race
      best_fans = fans_gained
  return best_race


def _detect_race_options(state_obj):
  """Best-effort race info from the filtered date-specific race schedule.

  This uses ``constants.RACES`` after aptitude filtering. That gives a useful
  scaffold for date-aware optional race selection, but it is not the same as
  reading the live race-list UI. Template/OCR-based grade detection on the race
  list still needs to be added later.
  """
  date_key = state_obj.get("year", "")
  races_on_date = list(constants.RACES.get(date_key, []) or [])
  grade_buckets = {}
  for race in races_on_date:
    if not isinstance(race, dict):
      continue
    grade = race.get("grade")
    if not grade:
      continue
    grade_buckets.setdefault(grade, []).append(race)

  available_grades = sorted(grade_buckets.keys(), key=_race_grade_rank)
  best_grade = available_grades[0] if available_grades else None
  best_any_race = _best_race_by_grade(races_on_date)
  best_g1_race = _best_race_by_grade(races_on_date, allowed_grades=("G1",))
  best_g2_g3_race = _best_race_by_grade(races_on_date, allowed_grades=("G2", "G3"))

  return {
    "races_on_date": races_on_date,
    "race_count": len(races_on_date),
    "available_grades": available_grades,
    "best_grade": best_grade,
    "g1_available": bool(best_g1_race),
    "g2_g3_available": bool(best_g2_g3_race),
    "best_any_race_name": (best_any_race or {}).get("name"),
    "best_g1_race_name": (best_g1_race or {}).get("name"),
    "best_g2_g3_race_name": (best_g2_g3_race or {}).get("name"),
  }


def _detect_rival_available():
  try:
    from scenarios.trackblazer import check_rival_race_indicator
    return bool(check_rival_race_indicator())
  except Exception as exc:
    warning(f"[TB_RACE] Rival indicator check failed: {exc}")
    return False


def _decision(**kwargs):
  decision = {
    "should_race": bool(kwargs.get("should_race", False)),
    "reason": str(kwargs.get("reason", "")),
    "training_total_stats": kwargs.get("training_total_stats"),
    "training_score": kwargs.get("training_score"),
    "training_supports": kwargs.get("training_supports"),
    "is_summer": bool(kwargs.get("is_summer", False)),
    "g1_forced": bool(kwargs.get("g1_forced", False)),
    "prefer_rival_race": bool(kwargs.get("prefer_rival_race", False)),
    "race_tier_target": kwargs.get("race_tier_target"),
    "race_name": kwargs.get("race_name"),
    "race_available": bool(kwargs.get("race_available", False)),
    "rival_indicator": bool(kwargs.get("rival_indicator", False)),
    "race_tier_info": kwargs.get("race_tier_info") or {},
  }
  log_fn = info if decision["should_race"] else debug
  score_part = ""
  if decision["training_score"] is not None:
    score_part = f", score={decision['training_score']}"
  log_fn(
    f"[TB_RACE] {'RACE' if decision['should_race'] else 'TRAIN'}: "
    f"{decision['reason']} "
    f"(summer={decision['is_summer']}, g1={decision['g1_forced']}, "
    f"rival={decision['rival_indicator']}, stats={decision['training_total_stats']}{score_part}, "
    f"supports={decision['training_supports']}, "
    f"race={decision['race_name'] or '-'})"
  )
  return decision


def _has_race_energy(state_obj):
  """Check if energy is above the minimum threshold to race."""
  energy_level = state_obj.get("energy_level", 0) or 0
  max_energy = state_obj.get("max_energy", 1) or 1
  energy_pct = energy_level / max_energy if max_energy > 0 else 0
  return energy_pct >= _MIN_RACE_ENERGY_PCT, energy_pct


def _strong_training_score_threshold():
  return get_training_behavior_strong_training_score_threshold()


def get_optional_race_low_energy_override(state_obj):
  return _optional_race_low_energy_override(state_obj)


def evaluate_trackblazer_race(state_obj, action):
  """Return a structured Trackblazer race-vs-training decision payload.

  The race schedule (``constants.RACES``) is only used for mandatory checks:
  Race Day and G1 dates.  For all other race decisions the rival indicator
  on the race button (visible on the lobby screen) is the source of truth.

  If ``state_obj["rival_indicator_detected"]`` is already set (pre-collected
  during collecting_race_state), that value is used instead of re-checking
  the screen.  The expensive rival scout is deferred to execution time.
  """
  year = state_obj.get("year", "")
  turn = state_obj.get("turn", "")
  summer = _is_summer(year)
  training_stats = _total_stat_gain(action)
  training_score = _training_score(action)
  training_supports = _support_count(action)
  race_info = _detect_race_options(state_obj)

  if state_obj.get("trackblazer_climax_locked_race"):
    return _decision(
      should_race=False,
      reason="Twinkle Star Climax training turn: race button is locked, so keep the normal inventory/shop/training flow",
      training_total_stats=training_stats,
      training_score=training_score,
      training_supports=training_supports,
      is_summer=summer,
      g1_forced=False,
      prefer_rival_race=False,
      race_tier_target=None,
      race_name=None,
      race_available=False,
      rival_indicator=False,
      race_tier_info=race_info,
    )

  # --- Mandatory races (schedule-driven, no rival check needed) -----------

  if turn == "Race Day":
    return _decision(
      should_race=True,
      reason="Race Day is mandatory",
      training_total_stats=training_stats,
      training_score=training_score,
      training_supports=training_supports,
      is_summer=summer,
      g1_forced=True,
      prefer_rival_race=False,
      race_tier_target="any",
      race_name=None,
      race_available=True,
      rival_indicator=False,
      race_tier_info=race_info,
    )

  race_gate = get_race_gate_for_turn_label(
    state_obj.get("year"),
    getattr(config, "OPERATOR_RACE_SELECTOR", None),
  )
  if race_gate.get("enabled") and not race_gate.get("race_allowed"):
    selected_race = race_gate.get("selected_race")
    blocked_reason = (
      f"Operator race gate disabled racing on {race_gate.get('turn_label') or state_obj.get('year')}"
    )
    if selected_race:
      blocked_reason += f" (selected race: {selected_race})"
    return _decision(
      should_race=False,
      reason=blocked_reason,
      training_total_stats=training_stats,
      training_score=training_score,
      training_supports=training_supports,
      is_summer=summer,
      g1_forced=False,
      prefer_rival_race=False,
      race_tier_target=None,
      race_name=selected_race,
      race_available=False,
      rival_indicator=bool(state_obj.get("rival_indicator_detected")),
      race_tier_info=race_info,
    )

  if race_info.get("g1_available"):
    return _decision(
      should_race=True,
      reason="G1 is available on this date; Trackblazer policy is to always race G1",
      training_total_stats=training_stats,
      training_score=training_score,
      training_supports=training_supports,
      is_summer=summer,
      g1_forced=True,
      prefer_rival_race=False,
      race_tier_target="G1",
      race_name=race_info.get("best_g1_race_name"),
      race_available=True,
      rival_indicator=False,
      race_tier_info=race_info,
    )

  # --- Optional races (rival indicator on screen is the source of truth) --

  # Use pre-collected indicator from collecting_race_state if available,
  # otherwise fall back to a live check.
  if "rival_indicator_detected" in state_obj:
    rival_indicator = bool(state_obj["rival_indicator_detected"])
  else:
    rival_indicator = _detect_rival_available()

  if not rival_indicator:
    return _decision(
      should_race=False,
      reason="No rival race indicator on screen",
      training_total_stats=training_stats,
      training_score=training_score,
      training_supports=training_supports,
      is_summer=summer,
      g1_forced=False,
      prefer_rival_race=False,
      race_tier_target=None,
      race_name=None,
      race_available=False,
      rival_indicator=False,
      race_tier_info=race_info,
    )

  # Rival indicator is present.  Gate on minimum energy.
  low_energy_reason_suffix = ""
  has_energy, energy_pct = _has_race_energy(state_obj)
  if not has_energy:
    low_energy_override = _optional_race_low_energy_override(state_obj)
    if low_energy_override.get("low_energy") and low_energy_override.get("allow_race"):
      low_energy_reason_suffix = f"; {low_energy_override.get('reason')}"
      info(
        f"[TB_RACE] Allowing low-energy optional race at {energy_pct:.0%}: "
        f"{low_energy_override.get('reason')}"
      )
    else:
      reason = (
        f"Rival indicator on screen but energy too low to race "
        f"({energy_pct:.0%} < {_MIN_RACE_ENERGY_PCT:.0%})"
      )
      if low_energy_override.get("prefer_rest"):
        reason = (
          f"{low_energy_override.get('reason')} "
          f"({energy_pct:.0%} energy, no configured recovery cover)"
        )
      return _decision(
        should_race=False,
        reason=reason,
        training_total_stats=training_stats,
        training_score=training_score,
        training_supports=training_supports,
        is_summer=summer,
        g1_forced=False,
        prefer_rival_race=False,
        race_tier_target=None,
        race_name=None,
        race_available=False,
        rival_indicator=True,
        race_tier_info=race_info,
      )

  # If the strategy already chose rest (all trainings blocked by failure
  # chance or energy), racing is strictly better than resting — it gains
  # fans, grade points, and VP at no training cost.
  action_func = getattr(action, "func", None)
  if action_func == "do_rest":
    return _decision(
      should_race=True,
      reason=(
        "Rival present and action is rest — racing is better than resting "
        f"(all trainings likely blocked by failure chance){low_energy_reason_suffix}"
      ),
      training_total_stats=training_stats,
      training_score=_training_score(action),
      training_supports=training_supports,
      is_summer=summer,
      g1_forced=False,
      prefer_rival_race=True,
      race_tier_target="any",
      race_name=None,
      race_available=True,
      rival_indicator=True,
      race_tier_info=race_info,
    )

  scoring_mode = bot.get_trackblazer_scoring_mode()
  strong_training_score_threshold = _strong_training_score_threshold()
  training_score = _training_score(action)
  if (
    scoring_mode == "stat_focused"
    and training_score is not None
    and training_score >= strong_training_score_threshold
  ):
    return _decision(
      should_race=False,
      reason=(
        f"Stat-focused training score is strong ({training_score} >= "
        f"{strong_training_score_threshold}) — keep the training turn instead of taking the optional rival race"
      ),
      training_total_stats=training_stats,
      training_score=training_score,
      training_supports=training_supports,
      is_summer=summer,
      g1_forced=False,
      prefer_rival_race=False,
      race_tier_target=None,
      race_name=None,
      race_available=True,
      rival_indicator=True,
      race_tier_info=race_info,
    )

  # Summer: only race the rival if training is weak.
  if str(year or "").startswith("Junior") and training_supports is not None and training_supports >= _JUNIOR_THREE_SUPPORT_MIN_SUPPORTS:
    return _decision(
      should_race=False,
      reason=(
        f"Junior year with a 3-support reading ({training_supports} >= "
        f"{_JUNIOR_THREE_SUPPORT_MIN_SUPPORTS}) — keep the training turn over an optional rival race"
      ),
      training_total_stats=training_stats,
      training_score=training_score,
      training_supports=training_supports,
      is_summer=summer,
      g1_forced=False,
      prefer_rival_race=False,
      race_tier_target=None,
      race_name=None,
      race_available=True,
      rival_indicator=True,
      race_tier_info=race_info,
    )

  # Summer: only race the rival if training is weak.
  if summer:
    if training_stats is None or training_stats < _WEAK_TRAINING_THRESHOLD:
      return _decision(
        should_race=True,
        reason=(
          f"Summer, but rival present and training is weak "
          f"({training_stats} < {_WEAK_TRAINING_THRESHOLD}) — scout will verify aptitude"
          f"{low_energy_reason_suffix}"
        ),
        training_total_stats=training_stats,
        training_supports=training_supports,
        is_summer=True,
        g1_forced=False,
        prefer_rival_race=True,
        race_tier_target="any",
        race_name=None,
        race_available=True,
        rival_indicator=True,
        race_tier_info=race_info,
      )
    return _decision(
      should_race=False,
      reason="Summer window: prefer training over rival race",
      training_total_stats=training_stats,
      training_score=training_score,
      training_supports=training_supports,
      is_summer=True,
      g1_forced=False,
      prefer_rival_race=False,
      race_tier_target=None,
      race_name=None,
      race_available=True,
      rival_indicator=True,
      race_tier_info=race_info,
    )

  # Non-summer: weak training → rival race is better than a bad turn.
  # Also treat None stats as weak (no usable training data available).
  if training_stats is None or training_stats < _WEAK_TRAINING_THRESHOLD:
    return _decision(
      should_race=True,
      reason=(
        f"Rival present with weak training ({training_stats} < "
        f"{_WEAK_TRAINING_THRESHOLD}) — scout will verify aptitude"
        f"{low_energy_reason_suffix}"
      ),
      training_total_stats=training_stats,
      training_score=training_score,
      training_supports=training_supports,
      is_summer=False,
      g1_forced=False,
      prefer_rival_race=True,
      race_tier_target="any",
      race_name=None,
      race_available=True,
      rival_indicator=True,
      race_tier_info=race_info,
    )

  # Non-summer, adequate training, rival present — prefer training.
  return _decision(
    should_race=False,
    reason=(
      f"Rival present but training is strong enough to skip "
      f"({training_stats} >= {_WEAK_TRAINING_THRESHOLD})"
    ),
    training_total_stats=training_stats,
    training_score=training_score,
    training_supports=training_supports,
    is_summer=False,
    g1_forced=False,
    prefer_rival_race=False,
    race_tier_target=None,
    race_name=None,
    race_available=True,
    rival_indicator=True,
    race_tier_info=race_info,
  )
