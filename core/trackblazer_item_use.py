from copy import deepcopy

import utils.constants as constants
from utils.log import info
from core.trackblazer_shop import PRIORITY_LEVELS, get_shop_catalog, normalize_priority, policy_context


_PRIORITY_INDEX = {name: index for index, name in enumerate(PRIORITY_LEVELS)}
_PRIORITY_SORT_BASE = {
  "NEVER": 0,
  "LOW": 1000,
  "MED": 2000,
  "HIGH": 3000,
}
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
_FINAL_SUMMER_LABEL = "Senior Year Late Aug"
_FINAL_SUMMER_INDEX = (
  constants.TIMELINE.index(_FINAL_SUMMER_LABEL)
  if _FINAL_SUMMER_LABEL in constants.TIMELINE else None
)
_TRAINING_LABELS = {
  "spd": "speed",
  "sta": "stamina",
  "pwr": "power",
  "guts": "guts",
  "wit": "wit",
}
_MOOD_LEVEL_INDEX = {
  "AWFUL": 0,
  "BAD": 1,
  "NORMAL": 2,
  "GOOD": 3,
  "GREAT": 4,
}
_MOOD_ITEM_BOOST = {
  "plain_cupcake": 1,
  "berry_sweet_cupcake": 2,
}
_HAMMER_TIERS = (
  "master_cleat_hammer",
  "artisan_cleat_hammer",
)
_HAMMER_TIER_PRIORITY = {
  item_key: len(_HAMMER_TIERS) - index
  for index, item_key in enumerate(_HAMMER_TIERS)
}
_ENERGY_ITEM_KEYS = (
  "vita_65",
  "vita_40",
  "vita_20",
  "royal_kale_juice",
  "energy_drink_max",
  "energy_drink_max_ex",
)
# Absolute energy points restored by each item.  Fail rate starts climbing
# around the 50 % energy mark; even a small top-up (Vita 20 → +20) can clear
# fails when energy is near that threshold.  Items whose restoration would
# push total energy above max_energy are skipped to avoid waste (overcapping).
_ENERGY_RESTORE_VALUES = {
  "vita_65": 65,
  "vita_40": 40,
  "vita_20": 20,
  "royal_kale_juice": 100,
  "energy_drink_max": 5,       # mainly raises max energy (+4), only +5 direct
  "energy_drink_max_ex": 0,    # raises max energy (+8), no direct restore
}
_VITA_ITEM_KEYS = (
  "vita_65",
  "vita_40",
  "vita_20",
)
_FAILSAFE_ITEM_KEYS = _ENERGY_ITEM_KEYS + ("good_luck_charm",)
ITEM_USE_BEHAVIOR_MODES = (
  "blast_now",
  "conserve_for_summer",
  "custom",
)
_DEFAULT_TRAINING_BEHAVIOR_SETTINGS = {
  "burst_commit_mode": "blast_now",
  "promote_charm_training_to_burst": True,
  "enforce_future_summer_good_luck_charm_reserve": False,
  "future_summer_good_luck_charm_min_reserve": 0,
  "wit_failure_gate_min_supports": 2,
  "wit_failure_gate_min_rainbows": 1,
  "wit_failure_gate_high_energy_pct": 80,
}
_ITEM_USE_OVERRIDES = {
  "empowering_megaphone": {
    "usage_group": "training_burst",
    "default_priority": "HIGH",
    "notes": "Use on summer or rainbow burst trainings.",
    "timing_overrides": [
      {
        "label": "Classic Summer burst",
        "start": "Classic Year Early Jul",
        "end": "Classic Year Late Aug",
        "priority_delta": 1,
        "sort_bonus": 220,
        "note": "Peak usage window for burst training.",
      },
      {
        "label": "Senior Summer burst",
        "start": "Senior Year Early Jul",
        "end": "Senior Year Late Aug",
        "priority_delta": 1,
        "sort_bonus": 220,
        "note": "Peak usage window for burst training.",
      },
    ],
  },
  "coaching_megaphone": {
    "usage_group": "training_burst",
    "default_priority": "MED",
    "notes": "Fallback burst megaphone on strong trainings.",
    "timing_overrides": [
      {
        "label": "Classic Summer burst",
        "start": "Classic Year Early Jul",
        "end": "Classic Year Late Aug",
        "priority_delta": 1,
        "sort_bonus": 180,
        "note": "Summer burst setup item.",
      },
      {
        "label": "Senior Summer burst",
        "start": "Senior Year Early Jul",
        "end": "Senior Year Late Aug",
        "priority_delta": 1,
        "sort_bonus": 180,
        "note": "Summer burst setup item.",
      },
    ],
  },
  "motivating_megaphone": {
    "usage_group": "training_burst",
    "default_priority": "HIGH",
    "notes": "Core burst megaphone; use on committed high-value trainings.",
    "timing_overrides": [
      {
        "label": "Classic Summer burst",
        "start": "Classic Year Early Jul",
        "end": "Classic Year Late Aug",
        "priority_delta": 1,
        "sort_bonus": 200,
        "note": "Primary training burst megaphone during summer windows.",
      },
      {
        "label": "Senior Summer burst",
        "start": "Senior Year Early Jul",
        "end": "Senior Year Late Aug",
        "priority_delta": 1,
        "sort_bonus": 200,
        "note": "Primary training burst megaphone during summer windows.",
      },
    ],
  },
  "reset_whistle": {
    "usage_group": "burst_setup",
    "default_priority": "HIGH",
    "notes": "Use to force a summer burst training turn.",
    "timing_overrides": [
      {
        "label": "Classic Summer burst",
        "start": "Classic Year Early Jul",
        "end": "Classic Year Late Aug",
        "priority_delta": 1,
        "sort_bonus": 240,
        "note": "Whistles are highest value during summer burst windows.",
      },
      {
        "label": "Senior Summer burst",
        "start": "Senior Year Early Jul",
        "end": "Senior Year Late Aug",
        "priority_delta": 1,
        "sort_bonus": 240,
        "note": "Whistles are highest value during summer burst windows.",
      },
    ],
  },
  "vita_65": {
    "usage_group": "energy",
    "default_priority": "MED",
    "reserve_quantity": 1,
    "notes": "Save premium Vita for summer unless a training spike demands it.",
  },
  "vita_20": {
    "usage_group": "energy",
    "default_priority": "LOW",
    "notes": "Cheaper energy top-up; still better in summer burst windows.",
  },
  "royal_kale_juice": {
    "usage_group": "energy",
    "default_priority": "MED",
    "notes": "Use on premium burst turns when energy is the limiter.",
  },
  "master_cleat_hammer": {
    "usage_group": "race_boost",
    "default_priority": "MED",
    "notes": "Reserve the best hammers for TSC unless surplus exists.",
  },
  "artisan_cleat_hammer": {
    "usage_group": "race_boost",
    "default_priority": "MED",
    "notes": "Use surplus artisan hammers before master hammers outside TSC.",
  },
}


def _humanize_item_key(item_key):
  return str(item_key or "").replace("_", " ").title()


def _rule_matches(rule, timeline_index):
  if timeline_index is None or not isinstance(rule, dict):
    return False
  start_index = constants.TIMELINE.index(rule["start"]) if rule.get("start") in constants.TIMELINE else None
  end_index = constants.TIMELINE.index(rule["end"]) if rule.get("end") in constants.TIMELINE else None
  if start_index is not None and timeline_index < start_index:
    return False
  if end_index is not None and timeline_index > end_index:
    return False
  return True


def _clamp_priority_index(index):
  return max(0, min(index, len(PRIORITY_LEVELS) - 1))


def _normalize_quantity(value, default_value):
  try:
    normalized = int(value)
  except (TypeError, ValueError):
    normalized = int(default_value)
  return max(0, normalized)


def _safe_int(value, default=0):
  try:
    return int(value)
  except (TypeError, ValueError):
    return default


def _safe_float(value, default=0.0):
  try:
    return float(value)
  except (TypeError, ValueError):
    return default


def _safe_bool(value, default=False):
  if isinstance(value, bool):
    return value
  if isinstance(value, str):
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
      return True
    if normalized in ("0", "false", "no", "off"):
      return False
  return bool(default)


def _past_final_summer(timeline_index):
  if _FINAL_SUMMER_INDEX is None or timeline_index is None:
    return False
  return timeline_index > _FINAL_SUMMER_INDEX


def _normalize_training_behavior_settings(raw_settings=None):
  raw_settings = raw_settings if isinstance(raw_settings, dict) else {}
  mode = str(raw_settings.get("burst_commit_mode", _DEFAULT_TRAINING_BEHAVIOR_SETTINGS["burst_commit_mode"]) or "").strip()
  if mode not in ITEM_USE_BEHAVIOR_MODES:
    mode = _DEFAULT_TRAINING_BEHAVIOR_SETTINGS["burst_commit_mode"]
  return {
    "burst_commit_mode": mode,
    "promote_charm_training_to_burst": _safe_bool(
      raw_settings.get("promote_charm_training_to_burst"),
      _DEFAULT_TRAINING_BEHAVIOR_SETTINGS["promote_charm_training_to_burst"],
    ),
    "enforce_future_summer_good_luck_charm_reserve": _safe_bool(
      raw_settings.get("enforce_future_summer_good_luck_charm_reserve"),
      _DEFAULT_TRAINING_BEHAVIOR_SETTINGS["enforce_future_summer_good_luck_charm_reserve"],
    ),
    "future_summer_good_luck_charm_min_reserve": _normalize_quantity(
      raw_settings.get("future_summer_good_luck_charm_min_reserve"),
      _DEFAULT_TRAINING_BEHAVIOR_SETTINGS["future_summer_good_luck_charm_min_reserve"],
    ),
    "wit_failure_gate_min_supports": min(
      2,
      _normalize_quantity(
        raw_settings.get("wit_failure_gate_min_supports"),
        _DEFAULT_TRAINING_BEHAVIOR_SETTINGS["wit_failure_gate_min_supports"],
      ),
    ),
    "wit_failure_gate_min_rainbows": min(
      2,
      _normalize_quantity(
        raw_settings.get("wit_failure_gate_min_rainbows"),
        _DEFAULT_TRAINING_BEHAVIOR_SETTINGS["wit_failure_gate_min_rainbows"],
      ),
    ),
    "wit_failure_gate_high_energy_pct": min(
      100,
      _normalize_quantity(
        raw_settings.get("wit_failure_gate_high_energy_pct"),
        _DEFAULT_TRAINING_BEHAVIOR_SETTINGS["wit_failure_gate_high_energy_pct"],
      ),
    ),
  }


def get_default_training_behavior_settings():
  return deepcopy(_DEFAULT_TRAINING_BEHAVIOR_SETTINGS)


def get_training_behavior_settings(policy=None):
  policy = normalize_item_use_policy(policy)
  return deepcopy(
    (policy.get("settings") or {}).get("training_behavior")
    or _DEFAULT_TRAINING_BEHAVIOR_SETTINGS
  )


def should_allow_wit_training(state, training_data, policy=None):
  training_behavior = get_training_behavior_settings(policy)
  max_energy = _safe_float(state.get("max_energy"), 0.0)
  current_energy = _safe_float(state.get("energy_level"), 0.0)

  if max_energy <= 0:
    return True, "max energy unknown"

  energy_pct = (current_energy / max_energy) * 100.0
  high_energy_pct = _safe_int(
    training_behavior.get("wit_failure_gate_high_energy_pct"),
    _DEFAULT_TRAINING_BEHAVIOR_SETTINGS["wit_failure_gate_high_energy_pct"],
  )
  if energy_pct > high_energy_pct:
    return True, f"energy {energy_pct:.0f}% > {high_energy_pct}% override"

  min_supports = _safe_int(
    training_behavior.get("wit_failure_gate_min_supports"),
    _DEFAULT_TRAINING_BEHAVIOR_SETTINGS["wit_failure_gate_min_supports"],
  )
  min_rainbows = _safe_int(
    training_behavior.get("wit_failure_gate_min_rainbows"),
    _DEFAULT_TRAINING_BEHAVIOR_SETTINGS["wit_failure_gate_min_rainbows"],
  )
  support_count = _safe_int(training_data.get("total_supports"), 0)
  rainbow_count = _safe_int(training_data.get("total_rainbow_friends"), 0)
  if rainbow_count <= 0 and isinstance(training_data.get("total_friendship_levels"), dict):
    friendship_levels = training_data.get("total_friendship_levels") or {}
    rainbow_count = _safe_int(friendship_levels.get("yellow"), 0) + _safe_int(friendship_levels.get("max"), 0)

  if support_count >= min_supports or rainbow_count >= min_rainbows:
    return True, (
      f"wit gate satisfied (supports {support_count}/{min_supports}, "
      f"rainbows {rainbow_count}/{min_rainbows})"
    )

  return False, (
    f"wit gate blocked (supports {support_count}/{min_supports}, "
    f"rainbows {rainbow_count}/{min_rainbows}, energy {energy_pct:.0f}% <= {high_energy_pct}% override)"
  )


def _infer_target_training(item_key):
  if item_key.startswith("speed_"):
    return "spd"
  if item_key.startswith("stamina_"):
    return "sta"
  if item_key.startswith("power_"):
    return "pwr"
  if item_key.startswith("guts_"):
    return "guts"
  if item_key.startswith("wit_"):
    return "wit"
  return None


def _infer_usage_group(item_key, entry):
  override = _ITEM_USE_OVERRIDES.get(item_key, {})
  if override.get("usage_group"):
    return override["usage_group"]
  category = entry.get("category") or constants.TRACKBLAZER_ITEM_CATEGORIES.get(item_key, "unknown")
  target_training = _infer_target_training(item_key)
  if item_key in _HAMMER_TIERS or category == "race":
    return "race_boost"
  if item_key == "good_luck_charm":
    return "condition"
  if item_key == "reset_whistle":
    return "burst_setup"
  if target_training and any(
    token in item_key
    for token in ("scroll", "manual", "notepad", "training_application")
  ):
    return "stat_auto"
  if target_training and "ankle_weights" in item_key:
    return "training_burst_specific"
  if category in ("mood", "motivation"):
    return "mood"
  if category == "energy":
    return "energy"
  if category == "condition":
    return "condition"
  if category == "training_boost":
    return "training_burst"
  return "utility"


def _default_priority(item_key, usage_group, target_training):
  override = _ITEM_USE_OVERRIDES.get(item_key, {})
  if override.get("default_priority"):
    return normalize_priority(override["default_priority"])
  if usage_group == "stat_auto":
    if target_training in ("spd", "sta", "pwr"):
      return "HIGH"
    if target_training == "wit":
      return "MED"
    return "LOW"
  if usage_group == "training_burst_specific":
    if target_training in ("spd", "sta", "pwr"):
      return "HIGH"
    if target_training == "wit":
      return "MED"
    return "LOW"
  if usage_group in ("training_burst", "burst_setup"):
    return "MED"
  if usage_group in ("mood", "condition", "energy", "race_boost"):
    return "MED"
  return "LOW"


def _default_reserve_quantity(item_key, usage_group):
  override = _ITEM_USE_OVERRIDES.get(item_key, {})
  if "reserve_quantity" in override:
    return _normalize_quantity(override["reserve_quantity"], 0)
  if usage_group == "race_boost":
    return 0
  return 0


def get_item_use_catalog():
  shop_catalog = {entry["key"]: entry for entry in get_shop_catalog()}
  known_keys = sorted(
    set(shop_catalog.keys())
    | set(constants.TRACKBLAZER_ITEM_TEMPLATES.keys())
    | set(_ITEM_USE_OVERRIDES.keys())
  )

  catalog = []
  for item_key in known_keys:
    base_entry = dict(shop_catalog.get(item_key) or {})
    override = _ITEM_USE_OVERRIDES.get(item_key, {})
    target_training = override.get("target_training") or _infer_target_training(item_key)
    usage_group = _infer_usage_group(item_key, base_entry)
    category = base_entry.get("category") or constants.TRACKBLAZER_ITEM_CATEGORIES.get(item_key, "unknown")
    catalog.append(
      {
        "key": item_key,
        "display_name": base_entry.get("display_name") or _humanize_item_key(item_key),
        "effect": base_entry.get("effect") or "",
        "category": category,
        "usage_group": usage_group,
        "target_training": target_training,
        "sort_rank": int(base_entry.get("sort_rank", 999)),
        "default_priority": _default_priority(item_key, usage_group, target_training),
        "default_reserve_quantity": _default_reserve_quantity(item_key, usage_group),
        "notes": str(override.get("notes", base_entry.get("notes", "")) or ""),
        "timing_overrides": deepcopy(override.get("timing_overrides", [])),
        "template_path": constants.TRACKBLAZER_ITEM_TEMPLATES.get(item_key),
        "asset_collected": bool(constants.TRACKBLAZER_ITEM_TEMPLATES.get(item_key)),
      }
    )

  catalog.sort(key=lambda entry: (entry["sort_rank"], entry["display_name"]))
  return catalog


def get_default_item_use_policy():
  items = {}
  for entry in get_item_use_catalog():
    items[entry["key"]] = {
      "priority": normalize_priority(entry["default_priority"]),
      "reserve_quantity": _normalize_quantity(entry["default_reserve_quantity"], 0),
      "notes": str(entry.get("notes") or ""),
      "timing_overrides": deepcopy(entry.get("timing_overrides") or []),
    }
  return {
    "version": 1,
    "settings": {
      "training_behavior": get_default_training_behavior_settings(),
    },
    "items": items,
  }


def normalize_item_use_policy(raw_policy=None):
  base_policy = get_default_item_use_policy()
  raw_policy = raw_policy if isinstance(raw_policy, dict) else {}
  raw_items = raw_policy.get("items") if isinstance(raw_policy.get("items"), dict) else {}
  raw_settings = raw_policy.get("settings") if isinstance(raw_policy.get("settings"), dict) else {}

  normalized_items = {}
  for entry in get_item_use_catalog():
    key = entry["key"]
    default_item = base_policy["items"][key]
    override_item = raw_items.get(key) if isinstance(raw_items.get(key), dict) else {}
    normalized_items[key] = {
      "priority": normalize_priority(override_item.get("priority", default_item["priority"])),
      "reserve_quantity": _normalize_quantity(
        override_item.get("reserve_quantity"),
        default_item["reserve_quantity"],
      ),
      "notes": str(override_item.get("notes", default_item["notes"]) or ""),
      "timing_overrides": deepcopy(
        override_item.get("timing_overrides", default_item["timing_overrides"]) or []
      ),
    }

  return {
    "version": int(raw_policy.get("version", base_policy["version"])),
    "settings": {
      "training_behavior": _normalize_training_behavior_settings(raw_settings.get("training_behavior")),
    },
    "items": normalized_items,
  }


def get_effective_item_use_items(policy=None, year=None, turn=None):
  normalized_policy = normalize_item_use_policy(policy)
  context = policy_context(year=year, turn=turn)
  effective_items = []

  for entry in get_item_use_catalog():
    item_policy = normalized_policy["items"].get(entry["key"], {})
    base_priority = normalize_priority(item_policy.get("priority", entry.get("default_priority")))
    priority_index = _PRIORITY_INDEX[base_priority]
    sort_score = _PRIORITY_SORT_BASE[base_priority] - int(entry.get("sort_rank", 0))
    reserve_quantity = _normalize_quantity(
      item_policy.get("reserve_quantity"),
      entry.get("default_reserve_quantity", 0),
    )
    active_rules = []

    for rule in item_policy.get("timing_overrides") or []:
      if not _rule_matches(rule, context.get("timeline_index")):
        continue
      delta = int(rule.get("priority_delta", 0) or 0)
      priority_index = _clamp_priority_index(priority_index + delta)
      sort_score += int(rule.get("sort_bonus", delta * 100) or 0)
      if "reserve_quantity" in rule:
        reserve_quantity = _normalize_quantity(rule.get("reserve_quantity"), reserve_quantity)
      active_rules.append(
        {
          "label": str(rule.get("label") or "timing_override"),
          "note": str(rule.get("note") or ""),
          "priority_delta": delta,
          "sort_bonus": int(rule.get("sort_bonus", delta * 100) or 0),
          "reserve_quantity": reserve_quantity,
        }
      )

    effective_priority = PRIORITY_LEVELS[priority_index]
    effective_items.append(
      {
        **entry,
        "priority": base_priority,
        "effective_priority": effective_priority,
        "reserve_quantity": reserve_quantity,
        "policy_notes": str(item_policy.get("notes", entry.get("notes", "")) or ""),
        "active_timing_rules": active_rules,
        "effective_sort_score": sort_score,
        "timeline_label": context.get("timeline_label"),
        "timeline_known": context.get("known_timeline"),
      }
    )

  effective_items.sort(
    key=lambda item: (
      item.get("effective_sort_score", 0),
      item.get("asset_collected", False),
      item.get("display_name", ""),
    ),
    reverse=True,
  )
  return effective_items


def _current_held_quantity(item_key, inventory, held_quantities):
  if item_key in held_quantities:
    return _safe_int(held_quantities.get(item_key), 0)
  item_data = inventory.get(item_key) or {}
  if item_data.get("held_quantity") is not None:
    return _safe_int(item_data.get("held_quantity"), 0)
  if item_data.get("detected"):
    return 1
  return 0


def _training_snapshot(training_name, training_data):
  training_data = training_data if isinstance(training_data, dict) else {}
  stat_gains = training_data.get("stat_gains") or {}
  total_stat_gain = sum(_safe_int(value, 0) for value in stat_gains.values())
  return {
    "training_name": training_name,
    "failure": _safe_int(training_data.get("failure"), 0),
    "score": _safe_float((training_data.get("score_tuple") or (0.0, 0))[0], 0.0),
    "supports": _safe_int(training_data.get("total_supports"), 0),
    "matching_stat_gain": _safe_int(stat_gains.get(training_name), 0),
    "total_stat_gain": total_stat_gain,
  }


def _affordable_shop_support_items(state_obj):
  state_obj = state_obj if isinstance(state_obj, dict) else {}
  shop_summary = state_obj.get("trackblazer_shop_summary") or {}
  detected_shop_keys = set(state_obj.get("trackblazer_shop_items") or shop_summary.get("items_detected") or [])
  shop_coins = _safe_int(shop_summary.get("shop_coins", state_obj.get("shop_coins")), -1)
  if shop_coins < 0 or not detected_shop_keys:
    return []

  affordable = []
  for item in get_shop_catalog():
    item_key = item.get("key")
    if item_key not in _FAILSAFE_ITEM_KEYS:
      continue
    if item_key not in detected_shop_keys:
      continue
    cost = _safe_int(item.get("cost"), 0)
    if cost > shop_coins:
      continue
    affordable.append(
      {
        "key": item_key,
        "name": item.get("display_name") or _humanize_item_key(item_key),
        "cost": cost,
      }
    )
  return affordable


def _summer_reroll_signal(state_obj, current_context):
  state_obj = state_obj if isinstance(state_obj, dict) else {}
  if not current_context.get("summer_window"):
    return {}

  try:
    import core.config as config
    max_failure = _safe_int(getattr(config, "MAX_FAILURE", 5), 5)
  except Exception:
    max_failure = 5

  training_results = state_obj.get("training_results") or {}
  if not isinstance(training_results, dict):
    return {}

  risky_candidates = []
  safe_non_wit_exists = False
  for training_name, training_data in training_results.items():
    if training_name == "wit":
      continue
    snapshot = _training_snapshot(training_name, training_data)
    if snapshot["failure"] <= max_failure:
      safe_non_wit_exists = True
    if snapshot["failure"] <= max_failure:
      continue
    if (
      snapshot["supports"] < 2
      and snapshot["matching_stat_gain"] < 16
      and snapshot["total_stat_gain"] < 24
      and snapshot["score"] < 4.0
    ):
      continue
    risky_candidates.append(snapshot)

  if safe_non_wit_exists or not risky_candidates:
    return {
      "needs_reroll": False,
      "risky_training_name": None,
      "risky_training_failure": None,
    }

  risky_candidates.sort(
    key=lambda entry: (
      entry["supports"],
      entry["score"],
      entry["matching_stat_gain"],
      entry["total_stat_gain"],
      -entry["failure"],
    ),
    reverse=True,
  )
  top_candidate = risky_candidates[0]
  return {
    "needs_reroll": True,
    "risky_training_name": top_candidate["training_name"],
    "risky_training_failure": top_candidate["failure"],
  }


def _hammer_usage_state(held_quantities):
  tiers = []
  for item_key in _HAMMER_TIERS:
    tiers.extend([item_key] * max(0, _safe_int(held_quantities.get(item_key), 0)))
  reserved = tiers[:3]
  spendable = tiers[3:]
  reserved_counts = {item_key: reserved.count(item_key) for item_key in _HAMMER_TIERS}
  spendable_counts = {item_key: spendable.count(item_key) for item_key in _HAMMER_TIERS}
  return reserved_counts, spendable_counts


def _usage_context(state_obj, action):
  state_obj = state_obj if isinstance(state_obj, dict) else {}
  inventory = state_obj.get("trackblazer_inventory") or {}
  inventory_summary = state_obj.get("trackblazer_inventory_summary") or {}
  held_quantities = dict(inventory_summary.get("held_quantities") or {})
  training_data = action.get("training_data") if hasattr(action, "get") else {}
  training_data = training_data if isinstance(training_data, dict) else {}
  training_name = action.get("training_name") if hasattr(action, "get") else None
  stat_gains = training_data.get("stat_gains") or {}
  total_stat_gain = sum(_safe_int(value, 0) for value in stat_gains.values())
  matching_stat_gain = _safe_int(stat_gains.get(training_name), 0)
  score_tuple = training_data.get("score_tuple") or (0.0, 0)
  timeline = policy_context(year=state_obj.get("year"), turn=state_obj.get("turn"))
  timeline_label = timeline.get("timeline_label") or ""
  timeline_index = timeline.get("timeline_index")
  climax_window = bool(timeline.get("is_climax"))
  score_value = _safe_float(score_tuple[0], 0.0)
  past_final_summer = _past_final_summer(timeline_index)
  score_over_50 = score_value > 50.0
  # Stop hoarding "save for summer" items when no summer windows remain, or
  # when the current board is already a high-value (>50) commitment.
  summer_conservation_bypass = past_final_summer or score_over_50
  rainbow_count = _safe_int(training_data.get("total_rainbow_friends"), 0)
  support_count = _safe_int(training_data.get("total_supports"), 0)
  failure_rate = _safe_int(training_data.get("failure"), 0)
  energy_level = _safe_int(state_obj.get("energy_level"), 0)
  max_energy = _safe_int(state_obj.get("max_energy"), energy_level)
  safe_energy_target = max_energy * 0.60
  total_held_vita_restore = 0
  for item_key in _VITA_ITEM_KEYS:
    held_quantity = _current_held_quantity(item_key, inventory, held_quantities)
    if held_quantity <= 0:
      continue
    total_held_vita_restore += _ENERGY_RESTORE_VALUES.get(item_key, 0) * held_quantity
  strong_burst_training = bool(
    getattr(action, "func", None) == "do_training"
    and (
      matching_stat_gain >= 30
      or total_stat_gain >= 30
      or (rainbow_count > 0 and score_value >= 6.0)
    )
  )
  weak_summer_training = bool(
    getattr(action, "func", None) == "do_training"
    and timeline_label in _SUMMER_WINDOWS
    and rainbow_count <= 0
    and matching_stat_gain < 20
    and total_stat_gain < 20
    and score_value < 5.0
  )
  weak_climax_training = bool(
    getattr(action, "func", None) == "do_training"
    and climax_window
    and rainbow_count <= 0
    and score_value < 20.0
    and matching_stat_gain < 10
    and total_stat_gain < 18
  )
  held_support_items = []
  for item_key in _FAILSAFE_ITEM_KEYS:
    held_quantity = _current_held_quantity(item_key, inventory, held_quantities)
    if held_quantity <= 0:
      continue
    held_support_items.append(
      {
        "key": item_key,
        "name": _humanize_item_key(item_key),
        "held_quantity": held_quantity,
      }
    )
  affordable_shop_support_items = _affordable_shop_support_items(state_obj)
  reroll_signal = _summer_reroll_signal(
    state_obj,
    {
      "summer_window": timeline_label in _SUMMER_WINDOWS,
    },
  )
  held_support_keys = {entry["key"] for entry in held_support_items}
  affordable_shop_support_keys = {entry["key"] for entry in affordable_shop_support_items}
  high_value_training = bool(
    getattr(action, "func", None) == "do_training"
    and (
      score_value >= 20.0
      or matching_stat_gain >= 10
      or total_stat_gain >= 18
      or rainbow_count > 0
    )
  )
  very_high_value_training = bool(
    getattr(action, "func", None) == "do_training"
    and (
      score_value >= 30.0
      or matching_stat_gain >= 14
      or total_stat_gain >= 24
      or rainbow_count >= 2
    )
  )
  committed_value_training = bool(
    getattr(action, "func", None) == "do_training"
    and (
      score_value >= 35.0
      or matching_stat_gain >= 25
      or total_stat_gain >= 35
    )
  )
  failure_bypassed_by_items = bool(training_data.get("failure_bypassed_by_items"))
  info(f"[ITEM_USE_CTX] failure_bypassed={failure_bypassed_by_items} failure_rate={failure_rate} committed_value={committed_value_training} score={score_value} matching={matching_stat_gain} total={total_stat_gain} training_data_keys={list(training_data.keys())[:10]}")
  commit_training_after_items = bool(
    strong_burst_training
    or committed_value_training
    or (
      getattr(action, "func", None) == "do_training"
      and (failure_rate <= 0 or failure_bypassed_by_items)
      and very_high_value_training
    )
  )
  return {
    "timeline_label": timeline_label,
    "timeline_index": timeline_index,
    "past_final_summer": past_final_summer,
    "climax_window": climax_window,
    "summer_conservation_bypass": summer_conservation_bypass,
    "score_over_50": score_over_50,
    "summer_window": timeline_label in _SUMMER_WINDOWS,
    "current_mood": str(state_obj.get("current_mood") or "").upper(),
    "status_effect_names": list(state_obj.get("status_effect_names") or []),
    "energy_level": energy_level,
    "max_energy": max_energy,
    "energy_deficit": max(0, max_energy - energy_level),
    "safe_energy_target": safe_energy_target,
    "held_vita_restore_total": total_held_vita_restore,
    "held_vita_reaches_safe_energy": (energy_level + total_held_vita_restore) >= safe_energy_target,
    "action_func": getattr(action, "func", None),
    "training_name": training_name,
    "training_score": score_value,
    "stat_gains": stat_gains,
    "matching_stat_gain": matching_stat_gain,
    "total_stat_gain": total_stat_gain,
    "failure_rate": failure_rate,
    "rainbow_count": rainbow_count,
    "support_count": support_count,
    "failure_bypassed_by_items": bool(training_data.get("failure_bypassed_by_items")),
    "held_support_item_names": [entry["name"] for entry in held_support_items],
    "affordable_shop_support_item_names": [entry["name"] for entry in affordable_shop_support_items],
    "held_energy_available": any(item_key in _ENERGY_ITEM_KEYS for item_key in held_support_keys),
    "held_charm_available": "good_luck_charm" in held_support_keys,
    "affordable_shop_energy_available": any(item_key in _ENERGY_ITEM_KEYS for item_key in affordable_shop_support_keys),
    "affordable_shop_charm_available": "good_luck_charm" in affordable_shop_support_keys,
    "has_followup_failsafe": bool(held_support_keys or affordable_shop_support_keys),
    "high_value_training": high_value_training,
    "very_high_value_training": very_high_value_training,
    "committed_value_training": committed_value_training,
    "strong_burst_training": strong_burst_training,
    "weak_summer_training": weak_summer_training or weak_climax_training or bool(reroll_signal.get("needs_reroll")),
    "weak_climax_training": weak_climax_training,
    "summer_reroll_target_name": reroll_signal.get("risky_training_name"),
    "summer_reroll_target_failure": reroll_signal.get("risky_training_failure"),
    "commit_training_after_items": commit_training_after_items,
    "is_tsc": climax_window,
    "trackblazer_buff_active": bool(state_obj.get("trackblazer_buff_active")),
    "allow_buff_override": bool(state_obj.get("trackblazer_allow_buff_override")),
  }


def _evaluate_item_candidates(effective_items, context, inventory, held_quantities, hammer_spendable):
  candidates = []
  deferred = []
  for item in effective_items:
    item_key = item["key"]
    held_quantity = _current_held_quantity(item_key, inventory, held_quantities)
    if held_quantity <= 0:
      continue
    evaluation = _evaluate_item_candidate(item, context, held_quantity, hammer_spendable)
    if not evaluation:
      continue
    base_entry = {
      "key": item_key,
      "name": item.get("display_name") or _humanize_item_key(item_key),
      "priority": item.get("effective_priority"),
      "effective_sort_score": item.get("effective_sort_score", 0),
      "usage_group": item.get("usage_group"),
      "target_training": item.get("target_training"),
      "held_quantity": held_quantity,
      "reserve_quantity": evaluation.get("reserved_quantity", item.get("reserve_quantity", 0)),
      "reason": evaluation.get("reason") or evaluation.get("defer_reason") or "",
    }
    if evaluation.get("use_now"):
      base_entry["candidate_score"] = evaluation.get("candidate_score", 0)
      candidates.append(base_entry)
    elif evaluation.get("defer_reason"):
      deferred.append(base_entry)

  candidates.sort(
    key=lambda entry: (
      _safe_int(entry.get("candidate_score"), 0),
      _safe_int(entry.get("effective_sort_score"), 0),
      _PRIORITY_INDEX.get(str(entry.get("priority") or "MED"), 1),
      _safe_int(entry.get("held_quantity"), 0),
      entry.get("name", ""),
    ),
    reverse=True,
  )
  return candidates, deferred


def _apply_energy_candidate_stacking(candidates, deferred, context):
  candidates = list(candidates or [])
  deferred = list(deferred or [])

  # Prevent energy item stacking. Prefer the smallest restore value that still
  # helps; mild overcapping from a single item is acceptable, but once the
  # deficit is covered don't pile on more items.
  #
  # Good Luck Charm sets failure to 0% for the turn, making energy items
  # redundant — the only reason to use energy at low HP is to reduce fail
  # chance, and charm already handles that.
  charm_planned = any(
    entry.get("key") == "good_luck_charm"
    for entry in candidates
  )
  energy_level = _safe_int(context.get("energy_level"), 0)
  max_energy = _safe_int(context.get("max_energy"), energy_level)
  energy_candidates = [entry for entry in candidates if entry.get("usage_group") == "energy"]
  kept_energy = []
  if energy_candidates and charm_planned:
    non_energy = [entry for entry in candidates if entry.get("usage_group") != "energy"]
    if context.get("held_vita_reaches_safe_energy"):
      kept_non_energy = []
      for entry in non_energy:
        if entry.get("key") == "good_luck_charm":
          entry.pop("candidate_score", None)
          entry["reason"] = (
            "held Vita can lift energy to the 60% safe zone; prefer energy over charm"
          )
          deferred.append(entry)
          continue
        kept_non_energy.append(entry)
      candidates = kept_non_energy + energy_candidates
    else:
      for entry in energy_candidates:
        entry.pop("candidate_score", None)
        entry["reason"] = "charm zeroes failure; energy item not needed this turn"
        deferred.append(entry)
      candidates = non_energy
  elif energy_candidates:
    non_energy = [entry for entry in candidates if entry.get("usage_group") != "energy"]
    energy_candidates.sort(key=lambda entry: _ENERGY_RESTORE_VALUES.get(entry["key"], 0))
    planned_energy_restored = 0
    for entry in energy_candidates:
      restore = _ENERGY_RESTORE_VALUES.get(entry["key"], 0)
      remaining_deficit = max(0, max_energy - (energy_level + planned_energy_restored))
      if planned_energy_restored > 0 and remaining_deficit <= 0:
        entry.pop("candidate_score", None)
        entry["reason"] = (
          f"energy already sufficient ({energy_level}+{planned_energy_restored}"
          f" >= max {max_energy})"
        )
        deferred.append(entry)
        continue
      if planned_energy_restored > 0 and restore > remaining_deficit:
        entry.pop("candidate_score", None)
        entry["reason"] = (
          f"would overcap on top of earlier items ({energy_level}"
          f"+{planned_energy_restored}+{restore} > max {max_energy})"
        )
        deferred.append(entry)
        continue
      planned_energy_restored += restore
      kept_energy.append(entry)

    # Permit a second Vita 20 on the same pass when one copy is already
    # planned, another copy is held, and total energy would still be at or
    # below 60 % after the first restore. This is intentionally a narrow rule:
    # no mid-flow reassess, just stage a second increment for the same item.
    target_energy_floor = max_energy * 0.60
    planned_counts = {}
    for entry in kept_energy:
      item_key = entry.get("key")
      planned_counts[item_key] = planned_counts.get(item_key, 0) + 1
    for entry in kept_energy:
      if entry.get("key") != "vita_20":
        continue
      remaining_vita_20 = _safe_int(entry.get("held_quantity"), 0) - planned_counts.get("vita_20", 0)
      projected_energy = energy_level + planned_energy_restored
      if remaining_vita_20 <= 0:
        break
      if projected_energy > target_energy_floor:
        break
      second_restore = _ENERGY_RESTORE_VALUES.get("vita_20", 20)
      if projected_energy + second_restore > max_energy:
        break
      duplicate_entry = dict(entry)
      duplicate_entry["reason"] = (
        f"{entry.get('reason')}; second copy to push energy above 60% "
        f"({projected_energy}->{projected_energy + second_restore}/{max_energy})"
      )
      kept_energy.append(duplicate_entry)
      planned_energy_restored += second_restore
      planned_counts["vita_20"] = planned_counts.get("vita_20", 0) + 1
      break
    candidates = non_energy + kept_energy

  return candidates, deferred, kept_energy


def _mood_steps_to_great(current_mood):
  current_index = _MOOD_LEVEL_INDEX.get(str(current_mood or "").upper())
  target_index = _MOOD_LEVEL_INDEX["GREAT"]
  if current_index is None:
    return 0
  return max(0, target_index - current_index)


def _apply_mood_candidate_selection(candidates, deferred, context):
  """Keep a single mood item per turn and prefer the smallest boost that
  reaches GREAT."""
  candidates = list(candidates or [])
  deferred = list(deferred or [])
  context = context if isinstance(context, dict) else {}
  mood_candidates = [entry for entry in candidates if entry.get("usage_group") == "mood"]
  if len(mood_candidates) <= 1:
    return candidates, deferred

  steps_needed = _mood_steps_to_great(context.get("current_mood"))
  selected = None
  known_boost_entries = []
  for entry in mood_candidates:
    boost = _MOOD_ITEM_BOOST.get(entry.get("key"), 0)
    if boost > 0:
      known_boost_entries.append((entry, boost))

  if known_boost_entries and steps_needed > 0:
    sufficient = [(entry, boost) for entry, boost in known_boost_entries if boost >= steps_needed]
    if sufficient:
      selected = min(
        sufficient,
        key=lambda item: (
          item[1],
          -_safe_int(item[0].get("candidate_score"), 0),
          -_safe_int(item[0].get("effective_sort_score"), 0),
        ),
      )[0]
    else:
      selected = max(
        known_boost_entries,
        key=lambda item: (
          item[1],
          _safe_int(item[0].get("candidate_score"), 0),
          _safe_int(item[0].get("effective_sort_score"), 0),
        ),
      )[0]

  if selected is None:
    selected = max(
      mood_candidates,
      key=lambda entry: (
        _safe_int(entry.get("candidate_score"), 0),
        _safe_int(entry.get("effective_sort_score"), 0),
      ),
    )

  selected_name = selected.get("name", selected.get("key", "mood item"))
  non_mood = [entry for entry in candidates if entry.get("usage_group") != "mood"]
  for entry in mood_candidates:
    if entry is selected:
      continue
    entry.pop("candidate_score", None)
    entry["reason"] = f"single mood item per turn; {selected_name} preferred"
    deferred.append(entry)
  candidates = non_mood + [selected]
  return candidates, deferred


def _promote_kale_mood_followup(candidates, deferred, context):
  """If Kale is planned, also plan one mood item to offset the mood loss."""
  candidates = list(candidates or [])
  deferred = list(deferred or [])
  context = context if isinstance(context, dict) else {}

  if not any(entry.get("key") == "royal_kale_juice" for entry in candidates):
    return candidates, deferred
  if any(entry.get("usage_group") == "mood" for entry in candidates):
    return candidates, deferred

  mood_deferred = [entry for entry in deferred if entry.get("usage_group") == "mood"]
  if not mood_deferred:
    return candidates, deferred

  projected_mood = context.get("current_mood")
  if str(projected_mood or "").upper() == "GREAT":
    projected_mood = "GOOD"
  steps_needed = _mood_steps_to_great(projected_mood)

  selected = None
  known_boost_entries = []
  for entry in mood_deferred:
    boost = _MOOD_ITEM_BOOST.get(entry.get("key"), 0)
    if boost > 0:
      known_boost_entries.append((entry, boost))

  if known_boost_entries and steps_needed > 0:
    sufficient = [(entry, boost) for entry, boost in known_boost_entries if boost >= steps_needed]
    if sufficient:
      selected = min(
        sufficient,
        key=lambda item: (
          item[1],
          -_safe_int(item[0].get("effective_sort_score"), 0),
          item[0].get("name", ""),
        ),
      )[0]
    else:
      selected = max(
        known_boost_entries,
        key=lambda item: (
          item[1],
          _safe_int(item[0].get("effective_sort_score"), 0),
          item[0].get("name", ""),
        ),
      )[0]

  if selected is None:
    selected = max(
      mood_deferred,
      key=lambda entry: (
        _safe_int(entry.get("effective_sort_score"), 0),
        entry.get("name", ""),
      ),
    )

  deferred = [entry for entry in deferred if entry is not selected]
  selected = dict(selected)
  selected["candidate_score"] = max(_safe_int(selected.get("candidate_score"), 0), 295)
  selected["reason"] = "offset Royal Kale Juice mood loss before confirming"
  candidates.append(selected)
  return candidates, deferred


_MEGAPHONE_KEYS = frozenset({"motivating_megaphone", "empowering_megaphone", "coaching_megaphone"})
_MEGAPHONE_STRENGTH_ORDER = {
  "empowering_megaphone": 3,
  "motivating_megaphone": 2,
  "coaching_megaphone": 1,
}
_RACE_BOOST_KEYS = frozenset(_HAMMER_TIERS)
_RACE_BOOST_STRENGTH_ORDER = {
  item_key: len(_HAMMER_TIERS) - index
  for index, item_key in enumerate(_HAMMER_TIERS)
}


def _apply_megaphone_mutual_exclusion(candidates, deferred, context=None):
  """Only one megaphone buff can be active per turn. Keep the highest-scored
  megaphone candidate and defer the rest."""
  candidates = list(candidates or [])
  deferred = list(deferred or [])
  context = context if isinstance(context, dict) else {}
  megaphone_candidates = [e for e in candidates if e.get("key") in _MEGAPHONE_KEYS]
  if len(megaphone_candidates) <= 1:
    return candidates, deferred

  non_summer = not bool(context.get("summer_window"))
  if non_summer:
    # Outside summer, spend the strongest available megaphone first.
    kept = max(
      megaphone_candidates,
      key=lambda entry: (
        _MEGAPHONE_STRENGTH_ORDER.get(entry.get("key"), 0),
        _safe_int(entry.get("candidate_score"), 0),
        _safe_int(entry.get("effective_sort_score"), 0),
      ),
    )
  else:
    # Already sorted by candidate_score descending from _evaluate_item_candidates
    kept = megaphone_candidates[0]

  kept_name = kept.get("name", kept.get("key", "megaphone"))
  non_megaphone = [e for e in candidates if e.get("key") not in _MEGAPHONE_KEYS]
  for entry in megaphone_candidates:
    if entry is kept:
      continue
    entry.pop("candidate_score", None)
    entry["reason"] = f"only one megaphone buff active per turn; {kept_name} preferred"
    deferred.append(entry)
  candidates = non_megaphone + [kept]
  return candidates, deferred


def _apply_race_boost_mutual_exclusion(candidates, deferred):
  """Cleat hammers do not stack. Keep only the strongest planned race boost."""
  candidates = list(candidates or [])
  deferred = list(deferred or [])
  race_boost_candidates = [entry for entry in candidates if entry.get("key") in _RACE_BOOST_KEYS]
  if len(race_boost_candidates) <= 1:
    return candidates, deferred

  kept = max(
    race_boost_candidates,
    key=lambda entry: (
      _RACE_BOOST_STRENGTH_ORDER.get(entry.get("key"), 0),
      _safe_int(entry.get("candidate_score"), 0),
      _safe_int(entry.get("effective_sort_score"), 0),
    ),
  )
  kept_name = kept.get("name", kept.get("key", "race boost"))
  non_race_boost = [entry for entry in candidates if entry.get("key") not in _RACE_BOOST_KEYS]
  for entry in race_boost_candidates:
    if entry is kept:
      continue
    entry.pop("candidate_score", None)
    entry["reason"] = f"race boosts do not stack; {kept_name} preferred"
    deferred.append(entry)
  candidates = non_race_boost + [kept]
  return candidates, deferred


def _should_commit_after_energy(context, kept_energy):
  if context.get("commit_training_after_items"):
    return False
  if context.get("action_func") != "do_training":
    return False
  if not kept_energy:
    return False
  if not context.get("summer_window"):
    return False
  if not context.get("high_value_training"):
    return False
  if context.get("failure_bypassed_by_items"):
    return True
  return False


def _should_commit_after_charm(policy, context, candidates):
  if context.get("commit_training_after_items"):
    return False
  if context.get("action_func") != "do_training":
    return False
  training_behavior = get_training_behavior_settings(policy)
  if training_behavior.get("burst_commit_mode") != "blast_now":
    return False
  if not training_behavior.get("promote_charm_training_to_burst"):
    return False
  return any(entry.get("key") == "good_luck_charm" for entry in (candidates or []))


def _evaluate_item_candidate(item, context, held_quantity, hammer_spendable):
  item_key = item["key"]
  usage_group = item.get("usage_group")
  target_training = item.get("target_training")
  effective_priority = item.get("effective_priority", "MED")
  priority_score = _PRIORITY_INDEX.get(effective_priority, 1) * 100
  reserve_quantity = _safe_int(item.get("reserve_quantity"), 0)

  if held_quantity <= 0:
    return None

  if usage_group == "race_boost":
    if context["action_func"] != "do_race":
      return None
    spendable = hammer_spendable.get(item_key, 0)
    if spendable <= 0 and not context["is_tsc"]:
      return {
        "defer_reason": "reserved for TSC or no surplus hammers available",
      }
    tier_bonus = _HAMMER_TIER_PRIORITY.get(item_key, 0) * 25
    reason = "TSC race boost" if context["is_tsc"] else "surplus race hammer outside TSC"
    return {
      "candidate_score": 200 + priority_score + spendable * 10 + tier_bonus,
      "reason": reason,
      "reserved_quantity": reserve_quantity,
      "use_now": True,
    }

  if held_quantity <= reserve_quantity:
    return {
      "defer_reason": f"holding reserve {reserve_quantity}",
    }

  if usage_group == "stat_auto":
    if context["action_func"] != "do_training" or target_training != context["training_name"]:
      return None
    reason = f"auto-use on selected {_TRAINING_LABELS.get(target_training, target_training)} training"
    if context["matching_stat_gain"] > 0:
      reason = f"{reason}; matching stat gain +{context['matching_stat_gain']}"
    return {
      "candidate_score": 500 + priority_score + context["matching_stat_gain"] * 10,
      "reason": reason,
      "reserved_quantity": reserve_quantity,
      "use_now": True,
    }

  if usage_group == "training_burst_specific":
    if context["action_func"] != "do_training" or target_training != context["training_name"]:
      return None
    if not context["commit_training_after_items"]:
      return {
        "defer_reason": "waiting for a committed burst training on this stat",
      }
    reason_parts = [f"matches selected {_TRAINING_LABELS.get(target_training, target_training)} training"]
    if context["rainbow_count"] > 0:
      reason_parts.append("rainbow training")
    if context["summer_window"]:
      reason_parts.append("summer burst window")
    if context["matching_stat_gain"] > 0:
      reason_parts.append(f"+{context['matching_stat_gain']} matching stat gain")
    reason_parts.append("commit to current training after item use")
    return {
      "candidate_score": 460 + priority_score + context["matching_stat_gain"] * 10 + context["rainbow_count"] * 25,
      "reason": "; ".join(reason_parts),
      "reserved_quantity": reserve_quantity,
      "use_now": True,
    }

  if usage_group == "training_burst":
    if context["action_func"] != "do_training":
      return None
    # A megaphone buff is already active on the lobby screen. Only the
    # Empowering Megaphone (60%) can override a weaker active buff; the
    # Motivating (40%) and Coaching megaphones cannot override and their
    # increment buttons will be greyed out in-game.
    if context.get("trackblazer_buff_active"):
      if not (context.get("allow_buff_override") and item_key == "empowering_megaphone"):
        return {
          "defer_reason": "megaphone buff already active; increment would be greyed out",
        }
    if not context["commit_training_after_items"]:
      return {
        "defer_reason": "waiting for a committed burst training",
      }
    reason_parts = ["committed training burst turn"]
    if context["summer_window"]:
      reason_parts.append("summer burst window")
    if context["rainbow_count"] > 0:
      reason_parts.append("rainbow support present")
    if context["matching_stat_gain"] > 0:
      reason_parts.append(f"+{context['matching_stat_gain']} matching stat gain")
    if context["total_stat_gain"] > 0:
      reason_parts.append(f"total stat gain {context['total_stat_gain']}")
    return {
      "candidate_score": 400 + priority_score + context["total_stat_gain"] * 5 + context["rainbow_count"] * 20,
      "reason": "; ".join(reason_parts),
      "reserved_quantity": reserve_quantity,
      "use_now": True,
    }

  if usage_group == "burst_setup":
    if context["action_func"] != "do_training":
      if not context["weak_summer_training"]:
        return None
    if (
      not context["summer_window"]
      and not context.get("climax_window")
      and not context.get("summer_conservation_bypass")
    ):
      return {
        "defer_reason": "save for summer burst windows",
      }
    if not context["has_followup_failsafe"]:
      return {
        "defer_reason": "save whistle until energy or a Good-Luck Charm is available",
      }
    if context["commit_training_after_items"]:
      return {
        "defer_reason": "current training already worth committing burst items",
      }
    if not context["weak_summer_training"]:
      return {
        "defer_reason": "current training is acceptable without a reroll",
      }
    support_reasons = []
    if context["held_support_item_names"]:
      support_reasons.append(f"held follow-up support: {', '.join(context['held_support_item_names'])}")
    if context["affordable_shop_support_item_names"]:
      support_reasons.append(
        f"affordable shop support: {', '.join(context['affordable_shop_support_item_names'])}"
      )
    target_hint = ""
    if context.get("summer_reroll_target_name"):
      target_hint = (
        f"unsafe board led by "
        f"{_TRAINING_LABELS.get(context['summer_reroll_target_name'], context['summer_reroll_target_name'])} "
        f"at {context.get('summer_reroll_target_failure', 0)}% fail"
      )
    return {
      "candidate_score": 480 + priority_score,
      # Reset Whistle only rerolls the board. After using it, the bot must
      # rescan trainings and re-evaluate failure/energy before committing.
      "reason": "; ".join(
        part for part in [
          "reroll the board, then recheck trainings before committing",
          "climax training turn" if context.get("climax_window") else "summer reroll",
          target_hint or "current board is too weak or too unsafe to commit",
          "shuffle support cards while follow-up recovery/fail-safe coverage exists",
          *support_reasons,
        ] if part
      ),
      "reserved_quantity": reserve_quantity,
      "use_now": True,
    }

  if usage_group == "mood":
    if context["current_mood"] == "GREAT":
      return {
        "defer_reason": "mood already GREAT",
      }
    return {
      "candidate_score": 300 + priority_score,
      "reason": f"mood is {context['current_mood'] or 'unknown'}; use mood item before confirming",
      "reserved_quantity": reserve_quantity,
      "use_now": True,
    }

  if usage_group == "energy":
    if context["action_func"] != "do_training":
      return None
    if (
      item_key.startswith("vita_")
      and not context["summer_window"]
      and not context.get("summer_conservation_bypass")
    ):
      return {
        "defer_reason": "save Vita for summer burst windows",
      }
    if context["energy_deficit"] < 20:
      return None
    if not (context["summer_window"] or context["high_value_training"]):
      return None
    # When fail is already 0% and energy is above half, the energy item won't
    # change this turn's outcome — no fail to reduce, no training gain boost.
    # Only spend energy items when they actually matter: high fail risk, or
    # energy is genuinely low enough that the deficit threatens upcoming turns.
    energy_ratio = context["energy_level"] / max(context["max_energy"], 1)
    if context["failure_rate"] <= 0 and energy_ratio > 0.5:
      return {
        "defer_reason": (
          f"fail already 0% and energy healthy ({context['energy_level']}"
          f"/{context['max_energy']}); item would not affect this turn"
        ),
      }
    reason_parts = [f"energy deficit {context['energy_deficit']}"]
    if context["summer_window"]:
      reason_parts.append("summer burst window")
    if context["high_value_training"]:
      reason_parts.append("strong training turn")
    return {
      "candidate_score": 260 + priority_score + context["energy_deficit"],
      "reason": "; ".join(reason_parts),
      "reserved_quantity": reserve_quantity,
      "use_now": True,
    }

  if usage_group == "condition":
    if item_key == "good_luck_charm":
      if context["action_func"] != "do_training":
        return None
      if context["failure_rate"] <= 5:
        return {
          "defer_reason": f"no fail risk (fail {context['failure_rate']}% <= 5%); charm would be greyed out",
        }
      if context.get("held_vita_reaches_safe_energy"):
        return {
          "defer_reason": (
            f"held Vita can raise energy to at least 60% "
            f"({context['energy_level']}+{context.get('held_vita_restore_total', 0)}"
            f" >= {int(context.get('safe_energy_target', 0))}); prefer energy over charm"
          ),
        }
      return {
        "candidate_score": 220 + priority_score + context["rainbow_count"] * 10,
        "reason": f"insurance for training at {context['failure_rate']}% fail",
        "reserved_quantity": reserve_quantity,
        "use_now": True,
      } if context["high_value_training"] else None
    if context["status_effect_names"]:
      return {
        "candidate_score": 280 + priority_score,
        "reason": "active condition detected",
        "reserved_quantity": reserve_quantity,
        "use_now": True,
      }
    return None

  return None


def plan_item_usage(policy=None, state_obj=None, action=None, limit=8):
  state_obj = state_obj if isinstance(state_obj, dict) else {}
  if action is None:
    return {"context": {}, "candidates": [], "deferred": []}

  inventory = state_obj.get("trackblazer_inventory") or {}
  inventory_summary = state_obj.get("trackblazer_inventory_summary") or {}
  held_quantities = dict(inventory_summary.get("held_quantities") or {})
  normalized_policy = normalize_item_use_policy(policy)
  context = _usage_context(state_obj, action)
  effective_items = get_effective_item_use_items(
    policy=normalized_policy,
    year=state_obj.get("year"),
    turn=state_obj.get("turn"),
  )
  for item_key in _HAMMER_TIERS:
    if item_key not in held_quantities:
      held_quantities[item_key] = _current_held_quantity(item_key, inventory, held_quantities)
  _, hammer_spendable = _hammer_usage_state(held_quantities)

  candidates, deferred = _evaluate_item_candidates(
    effective_items,
    context,
    inventory,
    held_quantities,
    hammer_spendable,
  )
  candidates, deferred, kept_energy = _apply_energy_candidate_stacking(candidates, deferred, context)
  candidates, deferred = _promote_kale_mood_followup(candidates, deferred, context)
  candidates, deferred = _apply_mood_candidate_selection(candidates, deferred, context)
  candidates, deferred = _apply_megaphone_mutual_exclusion(candidates, deferred, context=context)
  candidates, deferred = _apply_race_boost_mutual_exclusion(candidates, deferred)

  if _should_commit_after_energy(context, kept_energy) or _should_commit_after_charm(normalized_policy, context, candidates):
    context = {
      **context,
      "commit_training_after_items": True,
    }
    candidates, deferred = _evaluate_item_candidates(
      effective_items,
      context,
      inventory,
      held_quantities,
      hammer_spendable,
    )
    candidates, deferred, kept_energy = _apply_energy_candidate_stacking(candidates, deferred, context)
    candidates, deferred = _promote_kale_mood_followup(candidates, deferred, context)
    candidates, deferred = _apply_mood_candidate_selection(candidates, deferred, context)
    candidates, deferred = _apply_megaphone_mutual_exclusion(candidates, deferred, context=context)
    candidates, deferred = _apply_race_boost_mutual_exclusion(candidates, deferred)

  return {
    "context": {
      "timeline_label": context.get("timeline_label"),
      "timeline_index": context.get("timeline_index"),
      "past_final_summer": context.get("past_final_summer"),
      "climax_window": context.get("climax_window"),
      "summer_conservation_bypass": context.get("summer_conservation_bypass"),
      "score_over_50": context.get("score_over_50"),
      "summer_window": context.get("summer_window"),
      "current_mood": context.get("current_mood"),
      "energy_level": context.get("energy_level"),
      "max_energy": context.get("max_energy"),
      "training_name": context.get("training_name"),
      "training_score": context.get("training_score"),
      "matching_stat_gain": context.get("matching_stat_gain"),
      "total_stat_gain": context.get("total_stat_gain"),
      "failure_rate": context.get("failure_rate"),
      "rainbow_count": context.get("rainbow_count"),
      "support_count": context.get("support_count"),
      "strong_burst_training": context.get("strong_burst_training"),
      "weak_summer_training": context.get("weak_summer_training"),
      "weak_climax_training": context.get("weak_climax_training"),
      "failure_bypassed_by_items": context.get("failure_bypassed_by_items"),
      "held_support_item_names": context.get("held_support_item_names"),
      "affordable_shop_support_item_names": context.get("affordable_shop_support_item_names"),
      "summer_reroll_target_name": context.get("summer_reroll_target_name"),
      "summer_reroll_target_failure": context.get("summer_reroll_target_failure"),
      "commit_training_after_items": context.get("commit_training_after_items"),
      "trackblazer_buff_active": context.get("trackblazer_buff_active"),
    },
    "candidates": candidates[: max(0, int(limit))],
    "deferred": deferred[: max(0, int(limit))],
  }
