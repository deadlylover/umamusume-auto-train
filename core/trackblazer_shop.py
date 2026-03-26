from copy import deepcopy

import utils.constants as constants


PRIORITY_LEVELS = ("NEVER", "LOW", "MED", "HIGH")
_PRIORITY_INDEX = {name: index for index, name in enumerate(PRIORITY_LEVELS)}
_PRIORITY_SORT_BASE = {
  "NEVER": 0,
  "LOW": 1000,
  "MED": 2000,
  "HIGH": 3000,
}
_TIMELINE_INDEX = {label: index for index, label in enumerate(constants.TIMELINE)}


TRACKBLAZER_SHOP_CATALOG = [
  {
    "key": "vita_65",
    "display_name": "Vita 65",
    "cost": 75,
    "effect": "Energy +65",
    "category": "energy",
    "sort_rank": 0,
    "default_priority": "HIGH",
    "default_max_quantity": 6,
    "notes": "Top-tier energy stockpile.",
  },
  {
    "key": "royal_kale_juice",
    "display_name": "Royal Kale Juice",
    "cost": 70,
    "effect": "Energy +100, Motivation -1",
    "category": "energy",
    "sort_rank": 1,
    "default_priority": "HIGH",
    "default_max_quantity": 4,
    "notes": "Highest-priority refill when coins allow.",
  },
  {
    "key": "vita_40",
    "display_name": "Vita 40",
    "cost": 55,
    "effect": "Energy +40",
    "category": "energy",
    "sort_rank": 2,
    "default_priority": "HIGH",
    "default_max_quantity": 5,
    "notes": "High-priority refill.",
  },
  {
    "key": "vita_20",
    "display_name": "Vita 20",
    "cost": 35,
    "effect": "Energy +20",
    "category": "energy",
    "sort_rank": 3,
    "default_priority": "HIGH",
    "default_max_quantity": 4,
    "notes": "Cheap energy top-up.",
  },
  {
    "key": "miracle_cure",
    "display_name": "Miracle Cure",
    "cost": 40,
    "effect": "Heal all negative status effects",
    "category": "condition",
    "sort_rank": 4,
    "default_priority": "HIGH",
    "default_max_quantity": 1,
    "notes": "Emergency cleanse; usually only need one.",
  },
  {
    "key": "rich_hand_cream",
    "display_name": "Rich Hand Cream",
    "cost": 15,
    "effect": "Heal Skin Outbreak",
    "category": "condition",
    "sort_rank": 5,
    "default_priority": "HIGH",
    "default_max_quantity": 1,
    "notes": "High-value single cure for Skin Outbreak.",
  },
  {
    "key": "motivating_megaphone",
    "display_name": "Motivating Megaphone",
    "cost": 55,
    "effect": "Training bonus +40% for 3 turns",
    "category": "training_effect",
    "sort_rank": 6,
    "default_priority": "HIGH",
    "default_max_quantity": 3,
    "notes": "Core summer burst item.",
    "timing_overrides": [
      {
        "label": "Summer stockpile",
        "start": "Classic Year Early Apr",
        "end": "Classic Year Late Jun",
        "priority_delta": 1,
        "sort_bonus": 180,
        "note": "Push harder before Classic Summer.",
      },
      {
        "label": "Post Senior Summer taper",
        "start": "Senior Year Late Aug",
        "end": "Senior Year Late Dec",
        "priority_delta": -1,
        "sort_bonus": -60,
        "note": "Drops slightly after Senior Summer.",
      },
    ],
  },
  {
    "key": "empowering_megaphone",
    "display_name": "Empowering Megaphone",
    "cost": 70,
    "effect": "Training bonus +60% for 2 turns",
    "category": "training_effect",
    "sort_rank": 7,
    "default_priority": "HIGH",
    "default_max_quantity": 2,
    "notes": "Best burst megaphone for summer stacks.",
    "timing_overrides": [
      {
        "label": "Summer stockpile",
        "start": "Classic Year Early Apr",
        "end": "Classic Year Late Jun",
        "priority_delta": 1,
        "sort_bonus": 200,
        "note": "Push harder before Classic Summer.",
      },
      {
        "label": "Post Senior Summer taper",
        "start": "Senior Year Late Aug",
        "end": "Senior Year Late Dec",
        "priority_delta": -1,
        "sort_bonus": -70,
        "note": "Drops slightly after Senior Summer.",
      },
    ],
  },
  {
    "key": "speed_scroll",
    "display_name": "Speed Scroll",
    "cost": 30,
    "effect": "Speed +15",
    "category": "stats",
    "sort_rank": 8,
    "default_priority": "HIGH",
    "default_max_quantity": 4,
    "notes": "Best stat book priority.",
  },
  {
    "key": "stamina_scroll",
    "display_name": "Stamina Scroll",
    "cost": 30,
    "effect": "Stamina +15",
    "category": "stats",
    "sort_rank": 9,
    "default_priority": "HIGH",
    "default_max_quantity": 4,
    "notes": "Second stat book priority.",
  },
  {
    "key": "power_scroll",
    "display_name": "Power Scroll",
    "cost": 30,
    "effect": "Power +15",
    "category": "stats",
    "sort_rank": 10,
    "default_priority": "HIGH",
    "default_max_quantity": 4,
    "notes": "Third stat book priority.",
  },
  {
    "key": "wit_scroll",
    "display_name": "Wit Scroll",
    "cost": 30,
    "effect": "Wisdom +15",
    "category": "stats",
    "sort_rank": 11,
    "default_priority": "MED",
    "default_max_quantity": 3,
    "notes": "Useful, but below speed/stamina/power.",
  },
  {
    "key": "guts_scroll",
    "display_name": "Guts Scroll",
    "cost": 30,
    "effect": "Guts +15",
    "category": "stats",
    "sort_rank": 12,
    "default_priority": "MED",
    "default_max_quantity": 2,
    "notes": "Usually the first stat book cut when coins are tight.",
  },
  {
    "key": "speed_manual",
    "display_name": "Speed Manual",
    "cost": 15,
    "effect": "Speed +7",
    "category": "stats",
    "sort_rank": 13,
    "default_priority": "HIGH",
    "default_max_quantity": 4,
    "notes": "Strong medium-cost stat buy.",
  },
  {
    "key": "stamina_manual",
    "display_name": "Stamina Manual",
    "cost": 15,
    "effect": "Stamina +7",
    "category": "stats",
    "sort_rank": 14,
    "default_priority": "HIGH",
    "default_max_quantity": 4,
    "notes": "Strong medium-cost stat buy.",
  },
  {
    "key": "power_manual",
    "display_name": "Power Manual",
    "cost": 15,
    "effect": "Power +7",
    "category": "stats",
    "sort_rank": 15,
    "default_priority": "HIGH",
    "default_max_quantity": 4,
    "notes": "Strong medium-cost stat buy.",
  },
  {
    "key": "wit_manual",
    "display_name": "Wit Manual",
    "cost": 15,
    "effect": "Wisdom +7",
    "category": "stats",
    "sort_rank": 16,
    "default_priority": "MED",
    "default_max_quantity": 3,
    "notes": "Behind speed/stamina/power manuals.",
  },
  {
    "key": "guts_manual",
    "display_name": "Guts Manual",
    "cost": 15,
    "effect": "Guts +7",
    "category": "stats",
    "sort_rank": 17,
    "default_priority": "MED",
    "default_max_quantity": 2,
    "notes": "Usually the first manual cut when coins are tight.",
  },
  {
    "key": "speed_notepad",
    "display_name": "Speed Notepad",
    "cost": 10,
    "effect": "Speed +3",
    "category": "stats",
    "sort_rank": 18,
    "default_priority": "MED",
    "default_max_quantity": 3,
    "notes": "Cheap speed filler.",
  },
  {
    "key": "stamina_notepad",
    "display_name": "Stamina Notepad",
    "cost": 10,
    "effect": "Stamina +3",
    "category": "stats",
    "sort_rank": 19,
    "default_priority": "MED",
    "default_max_quantity": 3,
    "notes": "Cheap stamina filler.",
  },
  {
    "key": "power_notepad",
    "display_name": "Power Notepad",
    "cost": 10,
    "effect": "Power +3",
    "category": "stats",
    "sort_rank": 20,
    "default_priority": "MED",
    "default_max_quantity": 3,
    "notes": "Cheap power filler.",
  },
  {
    "key": "wit_notepad",
    "display_name": "Wit Notepad",
    "cost": 10,
    "effect": "Wisdom +3",
    "category": "stats",
    "sort_rank": 21,
    "default_priority": "LOW",
    "default_max_quantity": 2,
    "notes": "Low-cost wisdom filler.",
  },
  {
    "key": "guts_notepad",
    "display_name": "Guts Notepad",
    "cost": 10,
    "effect": "Guts +3",
    "category": "stats",
    "sort_rank": 22,
    "default_priority": "LOW",
    "default_max_quantity": 2,
    "notes": "Usually the first notepad cut when coins are tight.",
  },
  {
    "key": "speed_ankle_weights",
    "display_name": "Speed Ankle Weights",
    "cost": 50,
    "effect": "Speed training bonus +50%, Energy consumption +20% (One turn)",
    "category": "training_effect",
    "sort_rank": 23,
    "default_priority": "HIGH",
    "default_max_quantity": 2,
    "notes": "Strong one-turn burst item.",
  },
  {
    "key": "reset_whistle",
    "display_name": "Reset Whistle",
    "cost": 20,
    "effect": "Shuffle support card distribution",
    "category": "training_facility",
    "sort_rank": 24,
    "default_priority": "MED",
    "default_max_quantity": 2,
    "notes": "Useful for setting up burst turns.",
    "timing_overrides": [
      {
        "label": "Summer stockpile",
        "start": "Classic Year Early Apr",
        "end": "Classic Year Late Jun",
        "priority_delta": 1,
        "sort_bonus": 120,
        "note": "Worth stockpiling before Classic Summer.",
      },
    ],
  },
  {
    "key": "coaching_megaphone",
    "display_name": "Coaching Megaphone",
    "cost": 40,
    "effect": "Training bonus +20% for 4 turns",
    "category": "training_effect",
    "sort_rank": 25,
    "default_priority": "MED",
    "default_max_quantity": 2,
    "notes": "Solid fallback burst item.",
    "timing_overrides": [
      {
        "label": "Summer stockpile",
        "start": "Classic Year Early Apr",
        "end": "Classic Year Late Jun",
        "priority_delta": 1,
        "sort_bonus": 100,
        "note": "Stock a couple before Classic Summer.",
      },
    ],
  },
  {
    "key": "good_luck_charm",
    "display_name": "Good-Luck Charm",
    "cost": 40,
    "effect": "Training failure rate set to 0% (One turn)",
    "category": "training_effect",
    "sort_rank": 26,
    "default_priority": "MED",
    "default_max_quantity": 2,
    "notes": "Great insurance for risky spikes.",
  },
  {
    "key": "stamina_ankle_weights",
    "display_name": "Stamina Ankle Weights",
    "cost": 50,
    "effect": "Stamina training bonus +50%, Energy consumption +20% (One turn)",
    "category": "training_effect",
    "sort_rank": 27,
    "default_priority": "MED",
    "default_max_quantity": 2,
    "notes": "Good when stamina is a real bottleneck.",
  },
  {
    "key": "power_ankle_weights",
    "display_name": "Power Ankle Weights",
    "cost": 50,
    "effect": "Power training bonus +50%, Energy consumption +20% (One turn)",
    "category": "training_effect",
    "sort_rank": 28,
    "default_priority": "MED",
    "default_max_quantity": 2,
    "notes": "Useful if power training needs a push.",
  },
  {
    "key": "guts_ankle_weights",
    "display_name": "Guts Ankle Weights",
    "cost": 50,
    "effect": "Guts training bonus +50%, Energy consumption +20% (One turn)",
    "category": "training_effect",
    "sort_rank": 29,
    "default_priority": "LOW",
    "default_max_quantity": 1,
    "notes": "Situational unless coins are overflowing.",
  },
  {
    "key": "berry_sweet_cupcake",
    "display_name": "Berry Sweet Cupcake",
    "cost": 55,
    "effect": "Motivation +2",
    "category": "mood",
    "sort_rank": 30,
    "default_priority": "LOW",
    "default_max_quantity": 2,
    "notes": "Stronger mood boost; conserve when a smaller boost can cap mood.",
  },
  {
    "key": "plain_cupcake",
    "display_name": "Plain Cupcake",
    "cost": 30,
    "effect": "Motivation +1",
    "category": "mood",
    "sort_rank": 31,
    "default_priority": "HIGH",
    "default_max_quantity": 2,
    "notes": "Preferred mood top-up for GOOD -> GREAT at low cost.",
  },
  {
    "key": "energy_drink_max",
    "display_name": "Energy Drink MAX",
    "cost": 30,
    "effect": "Maximum energy +4, Energy +5",
    "category": "energy",
    "sort_rank": 32,
    "default_priority": "MED",
    "default_max_quantity": 2,
    "notes": "Fine filler, below raw Vita value.",
  },
  {
    "key": "energy_drink_max_ex",
    "display_name": "Energy Drink MAX EX",
    "cost": 50,
    "effect": "Maximum energy +8",
    "category": "energy",
    "sort_rank": 33,
    "default_priority": "LOW",
    "default_max_quantity": 1,
    "notes": "Low priority unless max-energy planning matters later.",
  },
  {
    "key": "grilled_carrots",
    "display_name": "Grilled Carrots",
    "cost": 40,
    "effect": "All Support card bonds +5",
    "category": "bond",
    "sort_rank": 34,
    "default_priority": "MED",
    "default_max_quantity": 2,
    "notes": "Useful when bond pace is lagging.",
  },
  {
    "key": "yumy_cat_food",
    "display_name": "Yummy Cat Food",
    "cost": 10,
    "effect": "Yayoi Akikawa's bond +5",
    "category": "bond",
    "sort_rank": 35,
    "default_priority": "LOW",
    "default_max_quantity": 2,
    "notes": "Cheap helper for unique-skill bond thresholds.",
  },
  {
    "key": "master_cleat_hammer",
    "display_name": "Master Cleat Hammer",
    "cost": 40,
    "effect": "Race bonus +35% (One turn)",
    "category": "race",
    "sort_rank": 36,
    "default_priority": "MED",
    "default_max_quantity": 3,
    "notes": "Reserve the best three hammers for TSC.",
  },
  {
    "key": "artisan_cleat_hammer",
    "display_name": "Artisan Cleat Hammer",
    "cost": 25,
    "effect": "Race bonus +20% (One turn)",
    "category": "race",
    "sort_rank": 37,
    "default_priority": "MED",
    "default_max_quantity": 3,
    "notes": "Use surplus on G1s after TSC reserve is covered.",
  },
  {
    "key": "speed_training_application",
    "display_name": "Speed Training Application",
    "cost": 150,
    "effect": "Speed Training Level +1",
    "category": "training_facility",
    "sort_rank": 38,
    "default_priority": "LOW",
    "default_max_quantity": 1,
    "notes": "Expensive and situational.",
  },
  {
    "key": "stamina_training_application",
    "display_name": "Stamina Training Application",
    "cost": 150,
    "effect": "Stamina Training Level +1",
    "category": "training_facility",
    "sort_rank": 39,
    "default_priority": "LOW",
    "default_max_quantity": 1,
    "notes": "Expensive and situational.",
  },
  {
    "key": "power_training_application",
    "display_name": "Power Training Application",
    "cost": 150,
    "effect": "Power Training Level +1",
    "category": "training_facility",
    "sort_rank": 40,
    "default_priority": "LOW",
    "default_max_quantity": 1,
    "notes": "Expensive and situational.",
  },
  {
    "key": "guts_training_application",
    "display_name": "Guts Training Application",
    "cost": 150,
    "effect": "Guts Training Level +1",
    "category": "training_facility",
    "sort_rank": 41,
    "default_priority": "LOW",
    "default_max_quantity": 1,
    "notes": "Expensive and situational.",
  },
  {
    "key": "wit_training_application",
    "display_name": "Wit Training Application",
    "cost": 150,
    "effect": "Wisdom Training Level +1",
    "category": "training_facility",
    "sort_rank": 42,
    "default_priority": "LOW",
    "default_max_quantity": 1,
    "notes": "Expensive and situational.",
  },
  {
    "key": "pretty_mirror",
    "display_name": "Pretty Mirror",
    "cost": 150,
    "effect": "Get Charming status effect",
    "category": "good_condition",
    "sort_rank": 43,
    "default_priority": "LOW",
    "default_max_quantity": 1,
    "notes": "Status pickup; tune later if it proves strong enough.",
  },
  {
    "key": "reporters_binoculars",
    "display_name": "Reporter's Binoculars",
    "cost": 150,
    "effect": "Get Hot Topic status effect",
    "category": "good_condition",
    "sort_rank": 44,
    "default_priority": "LOW",
    "default_max_quantity": 1,
    "notes": "Status pickup; tune later if it proves strong enough.",
  },
  {
    "key": "master_practice_guide",
    "display_name": "Master Practice Guide",
    "cost": 150,
    "effect": "Get Practice Perfect status effect",
    "category": "good_condition",
    "sort_rank": 45,
    "default_priority": "LOW",
    "default_max_quantity": 1,
    "notes": "Status pickup; tune later if it proves strong enough.",
  },
  {
    "key": "scholars_hat",
    "display_name": "Scholar's Hat",
    "cost": 280,
    "effect": "Get Fast Learner status effect",
    "category": "good_condition",
    "sort_rank": 46,
    "default_priority": "LOW",
    "default_max_quantity": 1,
    "notes": "Very expensive; keep low until we model payoff.",
  },
  {
    "key": "fluffy_pillow",
    "display_name": "Fluffy Pillow",
    "cost": 15,
    "effect": "Heal Night Owl",
    "category": "condition",
    "sort_rank": 47,
    "default_priority": "LOW",
    "default_max_quantity": 1,
    "notes": "Keep one only if Night Owl becomes common enough.",
  },
  {
    "key": "pocket_planner",
    "display_name": "Pocket Planner",
    "cost": 15,
    "effect": "Heal Slacker",
    "category": "condition",
    "sort_rank": 48,
    "default_priority": "LOW",
    "default_max_quantity": 1,
    "notes": "Cheap spot cure.",
  },
  {
    "key": "smart_scale",
    "display_name": "Smart Scale",
    "cost": 15,
    "effect": "Heal Slow Metabolism",
    "category": "condition",
    "sort_rank": 49,
    "default_priority": "LOW",
    "default_max_quantity": 1,
    "notes": "Cheap spot cure.",
  },
  {
    "key": "aroma_diffuser",
    "display_name": "Aroma Diffuser",
    "cost": 15,
    "effect": "Heal Migraine",
    "category": "condition",
    "sort_rank": 50,
    "default_priority": "LOW",
    "default_max_quantity": 1,
    "notes": "Cheap spot cure.",
  },
  {
    "key": "practice_drills_dvd",
    "display_name": "Practice Drills DVD",
    "cost": 15,
    "effect": "Heal Practice Poor",
    "category": "condition",
    "sort_rank": 51,
    "default_priority": "LOW",
    "default_max_quantity": 1,
    "notes": "Cheap spot cure.",
  },
  {
    "key": "glow_sticks",
    "display_name": "Glow Sticks",
    "cost": 15,
    "effect": "Race fan gain +50% (One turn)",
    "category": "race",
    "sort_rank": 52,
    "default_priority": "LOW",
    "default_max_quantity": 1,
    "notes": "Low-value race helper for now.",
  },
]


def normalize_priority(value):
  text = str(value or "").strip().upper()
  if text in _PRIORITY_INDEX:
    return text
  return "MED"


def _clamp_priority_index(index):
  return max(0, min(index, len(PRIORITY_LEVELS) - 1))


def _normalize_max_quantity(value, default_value):
  try:
    normalized = int(value)
  except (TypeError, ValueError):
    normalized = int(default_value)
  return max(0, normalized)


def _catalog_entry_with_asset(entry):
  enriched = dict(entry)
  template_path = constants.TRACKBLAZER_ITEM_TEMPLATES.get(entry["key"])
  enriched["template_path"] = template_path
  enriched["asset_collected"] = bool(template_path)
  return enriched


def get_shop_catalog():
  return [_catalog_entry_with_asset(entry) for entry in TRACKBLAZER_SHOP_CATALOG]


def get_default_shop_policy():
  items = {}
  for entry in TRACKBLAZER_SHOP_CATALOG:
    items[entry["key"]] = {
      "priority": normalize_priority(entry.get("default_priority")),
      "max_quantity": int(entry.get("default_max_quantity", 0)),
      "notes": str(entry.get("notes") or ""),
      "timing_overrides": deepcopy(entry.get("timing_overrides") or []),
    }
  return {
    "version": 1,
    "items": items,
  }


def normalize_shop_policy(raw_policy=None):
  base_policy = get_default_shop_policy()
  raw_policy = raw_policy if isinstance(raw_policy, dict) else {}
  raw_items = raw_policy.get("items") if isinstance(raw_policy.get("items"), dict) else {}

  normalized_items = {}
  for entry in TRACKBLAZER_SHOP_CATALOG:
    key = entry["key"]
    default_item = base_policy["items"][key]
    override_item = raw_items.get(key) if isinstance(raw_items.get(key), dict) else {}
    normalized_items[key] = {
      "priority": normalize_priority(override_item.get("priority", default_item["priority"])),
      "max_quantity": _normalize_max_quantity(override_item.get("max_quantity"), default_item["max_quantity"]),
      "notes": str(override_item.get("notes", default_item["notes"]) or ""),
      "timing_overrides": deepcopy(override_item.get("timing_overrides", default_item["timing_overrides"]) or []),
    }

  return {
    "version": int(raw_policy.get("version", base_policy["version"])),
    "items": normalized_items,
  }


def policy_context(year=None, turn=None):
  year_text = str(year or "").strip()
  turn_text = str(turn or "").strip()
  label = ""
  if year_text and year_text in _TIMELINE_INDEX:
    label = year_text
  elif year_text and turn_text:
    combined = f"{year_text} {turn_text}"
    if combined in _TIMELINE_INDEX:
      label = combined
    else:
      label = combined
  elif year_text:
    label = year_text
  elif turn_text:
    label = turn_text
  return {
    "year": year_text,
    "turn": turn_text,
    "timeline_label": label,
    "timeline_index": _TIMELINE_INDEX.get(label),
    "known_timeline": label in _TIMELINE_INDEX,
  }


def _rule_matches(rule, timeline_index):
  if timeline_index is None or not isinstance(rule, dict):
    return False
  start_index = _TIMELINE_INDEX.get(rule.get("start")) if rule.get("start") else None
  end_index = _TIMELINE_INDEX.get(rule.get("end")) if rule.get("end") else None
  if start_index is not None and timeline_index < start_index:
    return False
  if end_index is not None and timeline_index > end_index:
    return False
  return True


def get_effective_shop_items(policy=None, year=None, turn=None):
  normalized_policy = normalize_shop_policy(policy)
  context = policy_context(year=year, turn=turn)
  effective_items = []

  for entry in get_shop_catalog():
    item_policy = normalized_policy["items"].get(entry["key"], {})
    base_priority = normalize_priority(item_policy.get("priority", entry.get("default_priority")))
    priority_index = _PRIORITY_INDEX[base_priority]
    sort_score = _PRIORITY_SORT_BASE[base_priority] - int(entry.get("sort_rank", 0))
    active_rules = []

    for rule in item_policy.get("timing_overrides") or []:
      if not _rule_matches(rule, context["timeline_index"]):
        continue
      delta = int(rule.get("priority_delta", 0) or 0)
      priority_index = _clamp_priority_index(priority_index + delta)
      sort_score += int(rule.get("sort_bonus", delta * 100) or 0)
      active_rules.append(
        {
          "label": str(rule.get("label") or "timing_override"),
          "note": str(rule.get("note") or ""),
          "priority_delta": delta,
          "sort_bonus": int(rule.get("sort_bonus", delta * 100) or 0),
        }
      )

    effective_priority = PRIORITY_LEVELS[priority_index]
    effective_items.append(
      {
        **entry,
        "priority": base_priority,
        "effective_priority": effective_priority,
        "max_quantity": _normalize_max_quantity(item_policy.get("max_quantity"), entry.get("default_max_quantity", 0)),
        "policy_notes": str(item_policy.get("notes", entry.get("notes", "")) or ""),
        "active_timing_rules": active_rules,
        "effective_sort_score": sort_score,
        "timeline_label": context["timeline_label"],
        "timeline_known": context["known_timeline"],
      }
    )

  effective_items.sort(
    key=lambda item: (
      item.get("effective_sort_score", 0),
      item.get("asset_collected", False),
      -int(item.get("cost", 0)),
    ),
    reverse=True,
  )
  return effective_items


def get_priority_preview(policy=None, year=None, turn=None, limit=10):
  preview = []
  for item in get_effective_shop_items(policy=policy, year=year, turn=turn)[: max(0, int(limit))]:
    preview.append(
      {
        "key": item["key"],
        "name": item["display_name"],
        "priority": item["effective_priority"],
        "max_quantity": item["max_quantity"],
        "cost": item["cost"],
        "asset_collected": item["asset_collected"],
      }
    )
  return preview
