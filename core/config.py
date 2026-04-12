import json
import os

from core.race_selector import normalize_operator_race_selector
from core.trackblazer_item_use import normalize_item_use_policy
from core.trackblazer_shop import normalize_shop_policy

#put a default for sleep time multiplier since it's an important value
SLEEP_TIME_MULTIPLIER = 1
TRACKBLAZER_PLANNER_POLICY = {
  "energy_class_cutoffs": {"critical": 0.05, "low": 0.30, "ok": 0.70},
  "training_value_class_cutoffs": {"weak": 35, "adequate": 40, "strong": 50, "very_strong": 60},
  "training_overrides_race_threshold": 30,
  "rival_race_min_energy_ratio": 0.02,
  "lookahead_horizon_turns": 3,
  "max_fallback_depth": 3,
  "bond_training_cutoff_turn": None,
  "skill_cadence_min_turns": 5,
}


def normalize_trackblazer_planner_policy(raw_policy=None):
  raw_policy = raw_policy if isinstance(raw_policy, dict) else {}
  defaults = TRACKBLAZER_PLANNER_POLICY
  raw_energy = raw_policy.get("energy_class_cutoffs") if isinstance(raw_policy.get("energy_class_cutoffs"), dict) else {}
  raw_training = raw_policy.get("training_value_class_cutoffs") if isinstance(raw_policy.get("training_value_class_cutoffs"), dict) else {}
  return {
    "energy_class_cutoffs": {
      "critical": float(raw_energy.get("critical", defaults["energy_class_cutoffs"]["critical"])),
      "low": float(raw_energy.get("low", defaults["energy_class_cutoffs"]["low"])),
      "ok": float(raw_energy.get("ok", defaults["energy_class_cutoffs"]["ok"])),
    },
    "training_value_class_cutoffs": {
      "weak": float(raw_training.get("weak", defaults["training_value_class_cutoffs"]["weak"])),
      "adequate": float(raw_training.get("adequate", defaults["training_value_class_cutoffs"]["adequate"])),
      "strong": float(raw_training.get("strong", defaults["training_value_class_cutoffs"]["strong"])),
      "very_strong": float(raw_training.get("very_strong", defaults["training_value_class_cutoffs"]["very_strong"])),
    },
    "training_overrides_race_threshold": float(raw_policy.get("training_overrides_race_threshold", defaults["training_overrides_race_threshold"])),
    "rival_race_min_energy_ratio": float(raw_policy.get("rival_race_min_energy_ratio", defaults["rival_race_min_energy_ratio"])),
    "lookahead_horizon_turns": int(raw_policy.get("lookahead_horizon_turns", defaults["lookahead_horizon_turns"])),
    "max_fallback_depth": int(raw_policy.get("max_fallback_depth", defaults["max_fallback_depth"])),
    "bond_training_cutoff_turn": raw_policy.get("bond_training_cutoff_turn", defaults["bond_training_cutoff_turn"]),
    "skill_cadence_min_turns": int(raw_policy.get("skill_cadence_min_turns", defaults["skill_cadence_min_turns"])),
  }


def _migrate_deprecated_display_scaling(config):
  platform_config = config.get("platform")
  if not isinstance(platform_config, dict):
    return config, False

  mac_settings = platform_config.get("mac_bluestacks_air")
  if not isinstance(mac_settings, dict):
    return config, False

  display_config = mac_settings.get("display_aware_bounds")
  if not isinstance(display_config, dict):
    return config, False

  changed = False

  for key in ("scale_regions", "scale_general_offsets", "scale_recognition_offsets"):
    if display_config.get(key):
      display_config[key] = False
      changed = True

  return config, changed

# to see any config variables you must call reload_config()
def load_config():
  with open("config.json", "r", encoding="utf-8") as file:
    config = json.load(file)

  config, migrated = _migrate_deprecated_display_scaling(config)
  if migrated:
    with open("config.json", "w", encoding="utf-8") as file:
      json.dump(config, file, indent=2)
      file.write("\n")
    print("[INFO] Deprecated macOS OCR scaling flags were disabled in config.json. Display-aware bounds now only affect the BlueStacks window size.")

  return config

def load_var(var_name, value):
  globals()[var_name] = value

def reload_config(print_config=True):
  try:
    config = load_config()

    load_var('PRIORITY_STAT', config["priority_stat"])
    load_var('PRIORITY_WEIGHT', config["priority_weight"])
    load_var('MINIMUM_MOOD', config["minimum_mood"])
    load_var('MINIMUM_MOOD_JUNIOR_YEAR', config["minimum_mood_junior_year"])
    load_var('MAX_FAILURE', config["maximum_failure"])
    load_var('MINIMUM_APTITUDES', config["minimum_aptitudes"])
    load_var('USE_RACE_SCHEDULE', config["use_race_schedule"])
    load_var('CANCEL_CONSECUTIVE_RACE', config["cancel_consecutive_race"])
    load_var('STAT_CAPS', config["stat_caps"])
    load_var('IS_AUTO_BUY_SKILL', config["skill"]["is_auto_buy_skill"])
    load_var('SKILL_CHECK_TURNS', config["skill"]["skill_check_turns"])
    load_var('CHECK_SKILL_BEFORE_RACES', config["skill"]["check_skill_before_races"])
    load_var('SKILL_PTS_CHECK', config["skill"]["skill_pts_check"])
    load_var('SKILL_LIST', config["skill"]["skill_list"])
    load_var('PRIORITY_EFFECTS_LIST', {i: v for i, v in enumerate(config["priority_weights"])})
    load_var('SKIP_TRAINING_ENERGY', config["skip_training_energy"])
    load_var('NEVER_REST_ENERGY', config["never_rest_energy"])
    load_var('SKIP_INFIRMARY_UNLESS_MISSING_ENERGY', config["skip_infirmary_unless_missing_energy"])
    load_var('WIT_TRAINING_SCORE_RATIO_THRESHOLD', config["wit_training_score_ratio_threshold"])
    load_var('WIT_TRAINING_MIN_ENERGY', config.get("wit_training_min_energy", 70))
    load_var('MINIMUM_CONDITION_SEVERITY', config["minimum_condition_severity"])
    load_var('PREFERRED_POSITION', config["preferred_position"])
    load_var('ENABLE_POSITIONS_BY_RACE', config["enable_positions_by_race"])
    load_var('POSITIONS_BY_RACE', config["positions_by_race"])
    load_var('POSITION_SELECTION_ENABLED', config["position_selection_enabled"])
    load_var('SLEEP_TIME_MULTIPLIER', config["sleep_time_multiplier"])
    load_var('WINDOW_NAME', config["window_name"])
    load_var('RACE_SCHEDULE', config["race_schedule"])
    load_var('RACE_SCHEDULE_CONF', config["race_schedule"])
    load_var('OPERATOR_RACE_SELECTOR', normalize_operator_race_selector(config.get("operator_race_selector")))
    load_var('CONFIG_NAME', config["config_name"])
    load_var('REST_BEFORE_SUMMER_ENERGY', config["rest_before_summer_energy"])
    load_var('RAINBOW_SUPPORT_WEIGHT_ADDITION', config["rainbow_support_weight_addition"])
    load_var('NON_MAX_SUPPORT_WEIGHT', config["non_max_support_weight"])
    load_var('RACE_TURN_THRESHOLD', config["race_turn_threshold"])
    load_var('USE_ADB', config["use_adb"])
    load_var('DEVICE_ID', config["device_id"])
    load_var('DO_MISSION_RACES_IF_POSSIBLE', config["do_mission_races_if_possible"])
    load_var('PRIORITIZE_MISSIONS_OVER_G1', config["prioritize_missions_over_g1"])
    load_var('USE_OPTIMAL_EVENT_CHOICE', config["event"]["use_optimal_event_choice"])
    load_var('EVENT_CHOICES', config["event"]["event_choices"])
    load_var('HINT_HUNTING_ENABLED', config["hint_hunting_enabled"])
    load_var('HINT_HUNTING_WEIGHTS', config["hint_hunting_weights"])
    load_var('SCENARIO_GIMMICK_WEIGHT', config["scenario_gimmick_weight"])
    load_var('USE_SKIP_CLAW_MACHINE', config["use_skip_claw_machine"])
    load_var('EXECUTION_MODE', config.get("execution_mode", "auto"))
    load_var('SKIP_SCENARIO_DETECTION', bool(config.get("skip_scenario_detection", True)))
    load_var('STARTUP_SCENARIO_OVERRIDE', config.get("startup_scenario_override", "trackblazer") or "")
    load_var('SKIP_FULL_STATS_APTITUDE_CHECK', bool(config.get("skip_full_stats_aptitude_check", True)))
    
    # macOS-specific platform settings (optional, with defaults)
    platform_config = config.get("platform", {})
    load_var('PLATFORM_PROFILE', platform_config.get("profile", "auto"))
    mac_air_settings = platform_config.get("mac_bluestacks_air", {})
    load_var('MAC_AIR_SETTINGS', mac_air_settings)
    load_var('PREFERRED_CONTROL_BACKEND', mac_air_settings.get("preferred_control_backend", "adb"))
    load_var('ALLOW_HOST_INPUT_FALLBACK', bool(mac_air_settings.get("allow_host_input_fallback", False)))
    
    # Debug/region adjuster settings (optional)
    debug_config = config.get("debug", {})
    load_var('REGION_ADJUSTER_CONFIG', debug_config.get("region_adjuster", {"enabled": False}))
    load_var('VERBOSE_LOGGING', debug_config.get("verbose_logging", False))
    load_var('VERBOSE_ACTIONS', debug_config.get("verbose_actions", False))
    load_var('VERBOSE_OCR', debug_config.get("verbose_ocr", False))
    load_var('EASYOCR_DEVICE', str(debug_config.get("easyocr_device", "auto") or "auto").strip().lower())
    load_var('DEVICE_DEBUG_LOGGING', debug_config.get("device_debug", False))
    load_var('SAVE_DEBUG_IMAGES', debug_config.get("save_debug_images", False))
    turn_trace_config = debug_config.get("turn_trace", {})
    load_var('TURN_TRACE_ENABLED', bool(turn_trace_config.get("enabled", False)))
    load_var('TURN_TRACE_FILENAME', str(turn_trace_config.get("filename", "turn_trace.txt") or "turn_trace.txt"))

    trackblazer_config = config.get("trackblazer", {})
    load_var('TRACKBLAZER_CONFIG', trackblazer_config)
    load_var('TRACKBLAZER_SHOP_POLICY', normalize_shop_policy(trackblazer_config.get("shop_policy")))
    load_var('TRACKBLAZER_ITEM_USE_POLICY', normalize_item_use_policy(trackblazer_config.get("item_use_policy")))
    load_var('TRACKBLAZER_STAT_WEIGHTS', trackblazer_config.get("stat_weights"))
    load_var('TRACKBLAZER_PLANNER_POLICY', normalize_trackblazer_planner_policy(trackblazer_config.get("planner_policy")))
    planner_config = config.get("planner", {})
    load_var('PLANNER_CONFIG', planner_config)
    load_var('PLANNER_USE_NEW_PLANNER', bool(planner_config.get("use_new_planner", False)))
      
  except KeyError as e:
    raise RuntimeError(f"Missing config key: {e.args[0]}, please copy it to config.json from config.template.json and try again")

  load_training_strategy(config["training_strategy"])
  if print_config:
    line = f"[DEBUG] Config: {config}\n"
    print(line)

    try:
      with open(os.path.join("logs", "log.txt"), "a", encoding="utf-8") as f:
        f.write(line + "\n")
    except Exception:
      # Never let logging break startup/config
      pass

def load_training_strategy(training_strategy_raw):
  global TRAINING_STRATEGY
  TRAINING_STRATEGY = {"name": training_strategy_raw["name"]}

  # Copy timeline directly — it just references template names
  TRAINING_STRATEGY["timeline"] = training_strategy_raw.get("timeline", {}).copy()

  # Detect all *_sets dynamically so future additions work automatically
  set_types = {
    key: value
    for key, value in training_strategy_raw.items()
    if key.endswith("_sets")
  }

  expanded_templates = {}

  for template_name, template_data in training_strategy_raw.get("templates", {}).items():
    expanded = {}

    for key, val in template_data.items():
      if key.endswith("_set"):
        plural_key = key + "s"  # e.g. stat_weight_set → stat_weight_sets

        # Ensure the plural key actually exists in the input
        if plural_key not in set_types:
          raise ValueError(
            f"❌ Configuration error: '{plural_key}' section not found in training strategy "
            f"while expanding template '{template_name}'."
          )

        # Ensure the requested set exists
        sets_dict = set_types[plural_key]
        if val not in sets_dict:
          raise ValueError(
            f"❌ Configuration error: Set '{val}' not found under '{plural_key}' "
            f"while expanding template '{template_name}'."
          )

        # Expand the reference into its actual dict/list value
        expanded[key] = sets_dict[val]
      else:
        # Keep non-reference values as-is
        expanded[key] = val

    expanded_templates[template_name] = expanded

  TRAINING_STRATEGY["templates"] = expanded_templates
