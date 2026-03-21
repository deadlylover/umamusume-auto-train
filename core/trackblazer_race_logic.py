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


def evaluate_trackblazer_race(state_obj, action):
  """Return a structured Trackblazer race-vs-training decision payload."""
  year = state_obj.get("year", "")
  turn = state_obj.get("turn", "")
  summer = _is_summer(year)
  training_stats = _total_stat_gain(action)
  race_info = _detect_race_options(state_obj)
  rival_indicator = _detect_rival_available()
  race_available = bool(race_info.get("race_count"))

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
      rival_indicator=rival_indicator,
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
      rival_indicator=rival_indicator,
      race_tier_info=race_info,
    )

  if not race_available:
    return _decision(
      should_race=False,
      reason="No optional race is available on this date after aptitude filtering",
      training_total_stats=training_stats,
      is_summer=summer,
      g1_forced=False,
      prefer_rival_race=False,
      race_tier_target=None,
      race_name=None,
      race_available=False,
      rival_indicator=rival_indicator,
      race_tier_info=race_info,
    )

  if summer:
    if rival_indicator and training_stats is not None and training_stats < _WEAK_TRAINING_THRESHOLD:
      return _decision(
        should_race=True,
        reason=(
          f"Summer usually prefers training, but rival is present and training is weak "
          f"({training_stats} < {_WEAK_TRAINING_THRESHOLD})"
        ),
        training_total_stats=training_stats,
        is_summer=True,
        g1_forced=False,
        prefer_rival_race=True,
        race_tier_target="G2_G3",
        race_name=race_info.get("best_g2_g3_race_name") or race_info.get("best_any_race_name"),
        race_available=True,
        rival_indicator=True,
        race_tier_info=race_info,
      )
    return _decision(
      should_race=False,
      reason="Summer window: prefer training over optional races",
      training_total_stats=training_stats,
      is_summer=True,
      g1_forced=False,
      prefer_rival_race=False,
      race_tier_target=None,
      race_name=None,
      race_available=True,
      rival_indicator=rival_indicator,
      race_tier_info=race_info,
    )

  if training_stats is not None and training_stats < _WEAK_TRAINING_THRESHOLD:
    target_name = race_info.get("best_g2_g3_race_name") or race_info.get("best_any_race_name")
    target_tier = "G2_G3" if race_info.get("best_g2_g3_race_name") else "any"
    return _decision(
      should_race=bool(target_name),
      reason=f"Weak training ({training_stats} < {_WEAK_TRAINING_THRESHOLD}); prefer racing",
      training_total_stats=training_stats,
      is_summer=False,
      g1_forced=False,
      prefer_rival_race=bool(rival_indicator),
      race_tier_target=target_tier,
      race_name=target_name,
      race_available=True,
      rival_indicator=rival_indicator,
      race_tier_info=race_info,
    )

  if rival_indicator:
    return _decision(
      should_race=True,
      reason="Rival race indicator present; bias toward racing for bonus stats",
      training_total_stats=training_stats,
      is_summer=False,
      g1_forced=False,
      prefer_rival_race=True,
      race_tier_target="any",
      race_name=race_info.get("best_any_race_name"),
      race_available=True,
      rival_indicator=True,
      race_tier_info=race_info,
    )

  return _decision(
    should_race=False,
    reason="Training is adequate and there is no forcing race signal",
    training_total_stats=training_stats,
    is_summer=False,
    g1_forced=False,
    prefer_rival_race=False,
    race_tier_target=None,
    race_name=None,
    race_available=True,
    rival_indicator=rival_indicator,
    race_tier_info=race_info,
  )
