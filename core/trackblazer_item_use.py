from copy import deepcopy

import utils.constants as constants
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
_TRAINING_LABELS = {
  "spd": "speed",
  "sta": "stamina",
  "pwr": "power",
  "guts": "guts",
  "wit": "wit",
}
_HAMMER_TIERS = (
  "master_cleat_hammer",
  "artisan_cleat_hammer",
)
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
    "usage_group": "mood",
    "default_priority": "HIGH",
    "notes": "Use when mood is below GREAT.",
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
  if category == "mood":
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
    "items": items,
  }


def normalize_item_use_policy(raw_policy=None):
  base_policy = get_default_item_use_policy()
  raw_policy = raw_policy if isinstance(raw_policy, dict) else {}
  raw_items = raw_policy.get("items") if isinstance(raw_policy.get("items"), dict) else {}

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
  training_data = action.get("training_data") if hasattr(action, "get") else {}
  training_data = training_data if isinstance(training_data, dict) else {}
  training_name = action.get("training_name") if hasattr(action, "get") else None
  stat_gains = training_data.get("stat_gains") or {}
  total_stat_gain = sum(_safe_int(value, 0) for value in stat_gains.values())
  matching_stat_gain = _safe_int(stat_gains.get(training_name), 0)
  score_tuple = training_data.get("score_tuple") or (0.0, 0)
  timeline = policy_context(year=state_obj.get("year"), turn=state_obj.get("turn"))
  timeline_label = timeline.get("timeline_label") or ""
  score_value = _safe_float(score_tuple[0], 0.0)
  rainbow_count = _safe_int(training_data.get("total_rainbow_friends"), 0)
  support_count = _safe_int(training_data.get("total_supports"), 0)
  energy_level = _safe_int(state_obj.get("energy_level"), 0)
  max_energy = _safe_int(state_obj.get("max_energy"), energy_level)
  return {
    "timeline_label": timeline_label,
    "summer_window": timeline_label in _SUMMER_WINDOWS,
    "current_mood": str(state_obj.get("current_mood") or "").upper(),
    "status_effect_names": list(state_obj.get("status_effect_names") or []),
    "energy_level": energy_level,
    "max_energy": max_energy,
    "energy_deficit": max(0, max_energy - energy_level),
    "action_func": getattr(action, "func", None),
    "training_name": training_name,
    "training_score": score_value,
    "stat_gains": stat_gains,
    "matching_stat_gain": matching_stat_gain,
    "total_stat_gain": total_stat_gain,
    "rainbow_count": rainbow_count,
    "support_count": support_count,
    "high_value_training": bool(
      getattr(action, "func", None) == "do_training"
      and (
        score_value >= 4.0
        or matching_stat_gain >= 10
        or total_stat_gain >= 18
        or rainbow_count > 0
        or support_count >= 3
      )
    ),
    "very_high_value_training": bool(
      getattr(action, "func", None) == "do_training"
      and (
        score_value >= 6.0
        or matching_stat_gain >= 14
        or total_stat_gain >= 24
        or rainbow_count >= 2
      )
    ),
    "is_tsc": timeline_label == "Finale Underway",
  }


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
    reason = "TSC race boost" if context["is_tsc"] else "surplus race hammer outside TSC"
    return {
      "candidate_score": 200 + priority_score + spendable * 10,
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
    if not (context["high_value_training"] or context["summer_window"] or context["rainbow_count"] > 0):
      return None
    reason_parts = [f"matches selected {_TRAINING_LABELS.get(target_training, target_training)} training"]
    if context["summer_window"]:
      reason_parts.append("summer burst window")
    if context["rainbow_count"] > 0:
      reason_parts.append("rainbow training")
    if context["matching_stat_gain"] > 0:
      reason_parts.append(f"+{context['matching_stat_gain']} matching stat gain")
    return {
      "candidate_score": 420 + priority_score + context["matching_stat_gain"] * 10 + context["rainbow_count"] * 25,
      "reason": "; ".join(reason_parts),
      "reserved_quantity": reserve_quantity,
      "use_now": True,
    }

  if usage_group == "training_burst":
    if context["action_func"] != "do_training":
      return None
    if not (context["high_value_training"] or context["summer_window"] or context["rainbow_count"] > 0):
      return None
    reason_parts = ["strong training turn"]
    if context["summer_window"]:
      reason_parts.append("summer burst window")
    if context["rainbow_count"] > 0:
      reason_parts.append("rainbow support present")
    if context["total_stat_gain"] > 0:
      reason_parts.append(f"total stat gain {context['total_stat_gain']}")
    return {
      "candidate_score": 360 + priority_score + context["total_stat_gain"] * 5 + context["rainbow_count"] * 20,
      "reason": "; ".join(reason_parts),
      "reserved_quantity": reserve_quantity,
      "use_now": True,
    }

  if usage_group == "burst_setup":
    if context["action_func"] != "do_training":
      return None
    if not context["summer_window"]:
      return {
        "defer_reason": "save for summer burst windows",
      }
    if not (context["rainbow_count"] > 0 or context["very_high_value_training"]):
      return None
    return {
      "candidate_score": 380 + priority_score + context["rainbow_count"] * 25,
      "reason": "summer burst setup on a rainbow or spike training turn",
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
    if item_key.startswith("vita_") and not context["summer_window"]:
      return {
        "defer_reason": "save Vita for summer burst windows",
      }
    if context["energy_deficit"] < 20:
      return None
    if not (context["summer_window"] or context["high_value_training"]):
      return None
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
      return {
        "candidate_score": 220 + priority_score + context["rainbow_count"] * 10,
        "reason": "insurance item for a reviewed training turn",
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
  context = _usage_context(state_obj, action)
  effective_items = get_effective_item_use_items(
    policy=policy,
    year=state_obj.get("year"),
    turn=state_obj.get("turn"),
  )
  for item_key in _HAMMER_TIERS:
    if item_key not in held_quantities:
      held_quantities[item_key] = _current_held_quantity(item_key, inventory, held_quantities)
  _, hammer_spendable = _hammer_usage_state(held_quantities)

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
      _PRIORITY_INDEX.get(str(entry.get("priority") or "MED"), 1),
      _safe_int(entry.get("held_quantity"), 0),
      entry.get("name", ""),
    ),
    reverse=True,
  )

  return {
    "context": {
      "timeline_label": context.get("timeline_label"),
      "summer_window": context.get("summer_window"),
      "current_mood": context.get("current_mood"),
      "energy_level": context.get("energy_level"),
      "max_energy": context.get("max_energy"),
      "training_name": context.get("training_name"),
      "training_score": context.get("training_score"),
      "matching_stat_gain": context.get("matching_stat_gain"),
      "total_stat_gain": context.get("total_stat_gain"),
      "rainbow_count": context.get("rainbow_count"),
      "support_count": context.get("support_count"),
    },
    "candidates": candidates[: max(0, int(limit))],
    "deferred": deferred[: max(0, int(limit))],
  }
