"""Trackblazer-specific race-vs-training decision gate.

This is intentionally a small heuristic layer that can be iterated on during
live testing. It does not open menus or click. It only inspects the current
state and selected action, then returns a structured decision payload that the
main loop can log, preview, and act on.
"""

import utils.constants as constants
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
_MIN_RACE_ENERGY_PCT = 0.05
_GRADE_ORDER = ("G1", "G2", "G3", "OP", "Pre-OP")


def _safe_int(value):
  try:
    return int(value)
  except (TypeError, ValueError):
    return None


def _is_summer(year):
  return str(year or "") in _SUMMER_WINDOWS


def _total_stat_gain(action):
  training_data = action.get("training_data") if hasattr(action, "get") else None
  if not isinstance(training_data, dict):
    return None
  stat_gains = training_data.get("stat_gains")
  if not isinstance(stat_gains, dict):
    return None

  total = 0
  found = False
  for value in stat_gains.values():
    normalized = _safe_int(value)
    if normalized is None:
      continue
    total += normalized
    found = True
  return total if found else None


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
  log_fn(
    f"[TB_RACE] {'RACE' if decision['should_race'] else 'TRAIN'}: "
    f"{decision['reason']} "
    f"(summer={decision['is_summer']}, g1={decision['g1_forced']}, "
    f"rival={decision['rival_indicator']}, stats={decision['training_total_stats']}, "
    f"race={decision['race_name'] or '-'})"
  )
  return decision


def _has_race_energy(state_obj):
  """Check if energy is above the minimum threshold to race."""
  energy_level = state_obj.get("energy_level", 0) or 0
  max_energy = state_obj.get("max_energy", 1) or 1
  energy_pct = energy_level / max_energy if max_energy > 0 else 0
  return energy_pct >= _MIN_RACE_ENERGY_PCT, energy_pct


def evaluate_trackblazer_race(state_obj, action):
  """Return a structured Trackblazer race-vs-training decision payload.

  The race schedule (``constants.RACES``) is only used for mandatory checks:
  Race Day and G1 dates.  For all other race decisions the rival indicator
  on the race button (visible on the lobby screen) is the source of truth.
  If the rival button is present, the scout will open the race list and
  verify aptitude inside.
  """
  year = state_obj.get("year", "")
  turn = state_obj.get("turn", "")
  summer = _is_summer(year)
  training_stats = _total_stat_gain(action)
  race_info = _detect_race_options(state_obj)

  # --- Mandatory races (schedule-driven, no rival check needed) -----------

  if turn == "Race Day":
    return _decision(
      should_race=True,
      reason="Race Day is mandatory",
      training_total_stats=training_stats,
      is_summer=summer,
      g1_forced=True,
      prefer_rival_race=False,
      race_tier_target="any",
      race_name=None,
      race_available=True,
      rival_indicator=False,
      race_tier_info=race_info,
    )

  if race_info.get("g1_available"):
    return _decision(
      should_race=True,
      reason="G1 is available on this date; Trackblazer policy is to always race G1",
      training_total_stats=training_stats,
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

  rival_indicator = _detect_rival_available()

  if not rival_indicator:
    return _decision(
      should_race=False,
      reason="No rival race indicator on screen",
      training_total_stats=training_stats,
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
  has_energy, energy_pct = _has_race_energy(state_obj)
  if not has_energy:
    return _decision(
      should_race=False,
      reason=(
        f"Rival indicator on screen but energy too low to race "
        f"({energy_pct:.0%} < {_MIN_RACE_ENERGY_PCT:.0%})"
      ),
      training_total_stats=training_stats,
      is_summer=summer,
      g1_forced=False,
      prefer_rival_race=False,
      race_tier_target=None,
      race_name=None,
      race_available=False,
      rival_indicator=True,
      race_tier_info=race_info,
    )

  # Summer: only race the rival if training is weak.
  if summer:
    if training_stats is not None and training_stats < _WEAK_TRAINING_THRESHOLD:
      return _decision(
        should_race=True,
        reason=(
          f"Summer, but rival present and training is weak "
          f"({training_stats} < {_WEAK_TRAINING_THRESHOLD}) — scout will verify aptitude"
        ),
        training_total_stats=training_stats,
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
  if training_stats is not None and training_stats < _WEAK_TRAINING_THRESHOLD:
    return _decision(
      should_race=True,
      reason=(
        f"Rival present with weak training ({training_stats} < "
        f"{_WEAK_TRAINING_THRESHOLD}) — scout will verify aptitude"
      ),
      training_total_stats=training_stats,
      is_summer=False,
      g1_forced=False,
      prefer_rival_race=True,
      race_tier_target="any",
      race_name=None,
      race_available=True,
      rival_indicator=True,
      race_tier_info=race_info,
    )

  # Non-summer, adequate training, rival present — still worth racing.
  return _decision(
    should_race=True,
    reason="Rival race indicator present; bias toward racing for bonus stats",
    training_total_stats=training_stats,
    is_summer=False,
    g1_forced=False,
    prefer_rival_race=True,
    race_tier_target="any",
    race_name=None,
    race_available=True,
    rival_indicator=True,
    race_tier_info=race_info,
  )
