import copy
import json
from functools import lru_cache
from pathlib import Path


YEAR_ORDER = ("Junior Year", "Classic Year", "Senior Year")
CALENDAR = (
  "Early Jan",
  "Late Jan",
  "Early Feb",
  "Late Feb",
  "Early Mar",
  "Late Mar",
  "Early Apr",
  "Late Apr",
  "Early May",
  "Late May",
  "Early Jun",
  "Late Jun",
  "Early Jul",
  "Late Jul",
  "Early Aug",
  "Late Aug",
  "Early Sep",
  "Late Sep",
  "Early Oct",
  "Late Oct",
  "Early Nov",
  "Late Nov",
  "Early Dec",
  "Late Dec",
)

_GRADE_ORDER = ("G1", "G2", "G3", "OP", "Pre-OP")
_YEAR_RANK = {name: index for index, name in enumerate(YEAR_ORDER)}
_DATE_RANK = {name: index for index, name in enumerate(CALENDAR)}


def split_turn_label(turn_label):
  text = str(turn_label or "").strip()
  for year in YEAR_ORDER:
    prefix = f"{year} "
    if text == year:
      return year, ""
    if text.startswith(prefix):
      return year, text[len(prefix):].strip()
  return "", text


def combine_turn_label(year, date):
  year_text = str(year or "").strip()
  date_text = str(date or "").strip()
  if year_text and date_text:
    return f"{year_text} {date_text}"
  return year_text or date_text


def _race_data_path():
  return Path(__file__).resolve().parent.parent / "data" / "races.json"


@lru_cache(maxsize=1)
def load_race_catalog():
  with open(_race_data_path(), "r", encoding="utf-8") as handle:
    data = json.load(handle)
  return data if isinstance(data, dict) else {}


def race_grade_rank(grade):
  try:
    return _GRADE_ORDER.index(str(grade or "").strip())
  except ValueError:
    return len(_GRADE_ORDER)


def _safe_int(value, default=0):
  try:
    return int(value)
  except (TypeError, ValueError):
    return default


def get_races_for_date(year, date):
  year_text = str(year or "").strip()
  date_text = str(date or "").strip()
  year_map = load_race_catalog().get(year_text) or {}
  races = []
  for name, race in year_map.items():
    if not isinstance(race, dict):
      continue
    if str(race.get("date") or "").strip() != date_text:
      continue
    payload = copy.deepcopy(race)
    payload["name"] = name
    races.append(payload)
  races.sort(
    key=lambda race: (
      race_grade_rank(race.get("grade")),
      -_safe_int((race.get("fans") or {}).get("gained")),
      str(race.get("name") or ""),
    )
  )
  return races


def _race_names_for_date(year, date):
  return {race.get("name") for race in get_races_for_date(year, date) if race.get("name")}


def _entry_sort_key(entry):
  return (
    _YEAR_RANK.get(entry.get("year"), len(_YEAR_RANK)),
    _DATE_RANK.get(entry.get("date"), len(_DATE_RANK)),
    str(entry.get("name") or ""),
  )


def normalize_selector_entry(raw_entry):
  if not isinstance(raw_entry, dict):
    return None

  year = str(raw_entry.get("year") or "").strip()
  date = str(raw_entry.get("date") or "").strip()

  if year and not date and year not in YEAR_ORDER:
    parsed_year, parsed_date = split_turn_label(year)
    if parsed_year and parsed_date:
      year, date = parsed_year, parsed_date
  elif not year and date:
    parsed_year, parsed_date = split_turn_label(date)
    if parsed_year and parsed_date:
      year, date = parsed_year, parsed_date

  if year not in YEAR_ORDER or date not in CALENDAR:
    return None

  selected_name = str(raw_entry.get("name") or raw_entry.get("selected_race") or "").strip()
  if selected_name and selected_name not in _race_names_for_date(year, date):
    selected_name = ""

  allowed_value = raw_entry.get("race_allowed")
  race_allowed = True if allowed_value is None else bool(allowed_value)

  return {
    "year": year,
    "date": date,
    "name": selected_name,
    "race_allowed": race_allowed,
  }


def _normalize_entry_collection(raw_entries):
  entry_map = {}
  for raw_entry in raw_entries or []:
    entry = normalize_selector_entry(raw_entry)
    if not entry:
      continue
    entry_map[(entry["year"], entry["date"])] = entry
  return entry_map


def normalize_legacy_race_schedule(raw_schedule):
  legacy_entries = []
  entry_map = _normalize_entry_collection(raw_schedule)
  for entry in sorted(entry_map.values(), key=_entry_sort_key):
    payload = dict(entry)
    payload["race_allowed"] = True
    legacy_entries.append(payload)
  return legacy_entries


def normalize_operator_race_selector(raw_selector):
  raw_selector = raw_selector if isinstance(raw_selector, dict) else {}
  raw_entries = raw_selector.get("dates")
  if not isinstance(raw_entries, list):
    raw_entries = raw_selector.get("entries")
  if not isinstance(raw_entries, list):
    raw_entries = []

  entry_map = _normalize_entry_collection(raw_entries)
  enabled_value = raw_selector.get("enabled")
  enabled = bool(entry_map) if enabled_value is None else bool(enabled_value)

  return {
    "version": 1,
    "enabled": enabled,
    "dates": sorted(entry_map.values(), key=_entry_sort_key),
  }


def get_selector_ui_state(selector, legacy_schedule=None):
  normalized = normalize_operator_race_selector(selector)
  if normalized.get("enabled") or normalized.get("dates"):
    return normalized
  return {
    "version": 1,
    "enabled": False,
    "dates": normalize_legacy_race_schedule(legacy_schedule or []),
  }


def serialize_selector_payload(entries, enabled=True):
  if isinstance(entries, dict):
    raw_entries = list(entries.values())
  else:
    raw_entries = list(entries or [])
  entry_map = _normalize_entry_collection(raw_entries)
  serialized_entries = []
  for entry in sorted(entry_map.values(), key=_entry_sort_key):
    name = str(entry.get("name") or "").strip()
    race_allowed = bool(entry.get("race_allowed", True))
    if not name and race_allowed:
      continue
    serialized_entries.append(
      {
        "year": entry["year"],
        "date": entry["date"],
        "name": name,
        "race_allowed": race_allowed,
      }
    )
  return {
    "version": 1,
    "enabled": bool(enabled),
    "dates": serialized_entries,
  }


def get_effective_schedule_entries(selector, legacy_schedule=None):
  normalized = normalize_operator_race_selector(selector)
  source_entries = (
    normalized.get("dates", [])
    if normalized.get("enabled")
    else normalize_legacy_race_schedule(legacy_schedule or [])
  )
  effective = []
  for entry in source_entries:
    name = str(entry.get("name") or "").strip()
    if not name:
      continue
    effective.append(
      {
        "name": name,
        "year": entry["year"],
        "date": entry["date"],
      }
    )
  return effective


def get_race_gate_for_turn_label(turn_label, selector):
  normalized = normalize_operator_race_selector(selector)
  year, date = split_turn_label(turn_label)
  gate = {
    "enabled": bool(normalized.get("enabled")),
    "applies": False,
    "source": "legacy_config" if not normalized.get("enabled") else "operator_selector",
    "turn_label": combine_turn_label(year, date),
    "year": year,
    "date": date,
    "entry_present": False,
    "race_allowed": True,
    "selected_race": None,
  }
  if year not in YEAR_ORDER or date not in CALENDAR:
    return gate

  gate["applies"] = bool(normalized.get("enabled"))
  if not normalized.get("enabled"):
    return gate

  entry_map = {
    (entry["year"], entry["date"]): entry
    for entry in normalized.get("dates", [])
  }
  entry = entry_map.get((year, date))
  gate["entry_present"] = bool(entry)
  if entry:
    gate["race_allowed"] = bool(entry.get("race_allowed", True))
    gate["selected_race"] = str(entry.get("name") or "").strip() or None
  return gate


def get_selected_race_for_turn_label(turn_label, selector, require_allowed=True):
  gate = get_race_gate_for_turn_label(turn_label, selector)
  if not gate.get("enabled"):
    return None
  if require_allowed and not gate.get("race_allowed"):
    return None
  return gate.get("selected_race") or None


def summarize_selector_state(selector, legacy_schedule=None, use_ui_fallback=False):
  if use_ui_fallback:
    normalized = get_selector_ui_state(selector, legacy_schedule=legacy_schedule)
  else:
    normalized = normalize_operator_race_selector(selector)
  dates = normalized.get("dates", [])
  return {
    "enabled": bool(normalized.get("enabled")),
    "configured_dates": len(dates),
    "selected_count": sum(1 for entry in dates if str(entry.get("name") or "").strip()),
    "blocked_count": sum(1 for entry in dates if not bool(entry.get("race_allowed", True))),
  }
