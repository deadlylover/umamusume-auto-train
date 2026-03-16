import pyautogui
import os
import cv2
from pathlib import Path

from utils.tools import sleep, get_secs, click
from core.state import APTITUDE_BOX_RATIOS, collect_main_state, collect_training_state, clear_aptitudes_cache
from utils.shared import CleanDefaultDict
import core.config as config
from PIL import ImageGrab
from core.actions import Action
import utils.constants as constants
from scenarios.unity import unity_cup_function
from core.events import select_event
from core.claw_machine import play_claw_machine
from core.skill import buy_skill, init_skill_py, get_skill_purchase_context
from core.operator_console import ensure_operator_console, publish_runtime_state
from core.region_adjuster.shared import resolve_region_adjuster_profiles

pyautogui.useImageNotFoundException(False)

import core.bot as bot
from utils.log import info, warning, error, debug, debug_window, log_encoded, args, record_turn, VERSION
from utils.device_action_wrapper import BotStopException
import utils.device_action_wrapper as device_action
import utils.pyautogui_actions as pyautogui_actions
import utils.adb_actions as adb_actions

from core.strategies import Strategy

def cache_templates(templates):
  cache={}
  image_read_color = cv2.IMREAD_COLOR
  for name, path in templates.items():
    img = cv2.imread(path, image_read_color)
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    if img is None:
      warning(f"Image doesn't exist: {img}")
      continue
    cache[name] = img
  return cache

templates = {
  "next": "assets/buttons/next_btn.png",
  "next2": "assets/buttons/next2_btn.png",
  "event": "assets/icons/event_choice_1.png",
  "inspiration": "assets/buttons/inspiration_btn.png",
  "cancel": "assets/buttons/cancel_btn.png",
  "retry": "assets/buttons/retry_btn.png",
  "tazuna": "assets/ui/tazuna_hint.png",
  "infirmary": "assets/buttons/infirmary_btn.png",
  "claw_btn": "assets/buttons/claw_btn.png",
  "ok_2_btn": "assets/buttons/ok_2_btn.png"
}

cached_templates = cache_templates(templates)

unity_templates = {
  "close_btn": "assets/buttons/close_btn.png",
  "unity_cup_btn": "assets/unity/unity_cup_btn.png",
  "unity_banner_mid_screen": "assets/unity/unity_banner_mid_screen.png"
}

cached_unity_templates = cache_templates(unity_templates)

STABLE_CAREER_SCREEN_ANCHORS = {
  "tazuna_hint": ("assets/ui/tazuna_hint.png", "GAME_WINDOW_BBOX"),
  "training_button": ("assets/buttons/training_btn.png", "SCREEN_BOTTOM_BBOX"),
  "rest_button": ("assets/buttons/rest_btn.png", "SCREEN_BOTTOM_BBOX"),
  "recreation_button": ("assets/buttons/recreation_btn.png", "SCREEN_BOTTOM_BBOX"),
  "races_button": ("assets/buttons/races_btn.png", "SCREEN_BOTTOM_BBOX"),
  "details_button": ("assets/buttons/details_btn.png", "SCREEN_TOP_BBOX"),
  "details_button_alt": ("assets/buttons/details_btn_2.png", "SCREEN_TOP_BBOX"),
}

SCENARIO_NAME_ALIASES = {
  "ura": "default",
  "unity": "unity",
  "trackblazer": "trackblazer",
  "mant": "trackblazer",
}
MAX_SCENARIO_DETECTION_ATTEMPTS = 5
runtime_debug_counter = 0


def _canonicalize_scenario_name(name):
  if not name:
    return ""
  return SCENARIO_NAME_ALIASES.get(name, name)


def _scenario_banner_templates():
  return {
    os.path.splitext(filename)[0]: f"assets/scenario_banner/{filename}"
    for filename in sorted(os.listdir("assets/scenario_banner"))
    if filename.endswith(".png")
  }


def _match_scenario_banners(screenshot, threshold=0.8):
  match_counts = {}
  first_match = ""
  for raw_name, template_path in _scenario_banner_templates().items():
    matches = device_action.match_template(template_path, screenshot, threshold=threshold)
    match_counts[raw_name] = len(matches)
    if not first_match and matches:
      first_match = raw_name
  return first_match, match_counts


def _detect_stable_career_screen_anchors(screenshot, threshold=0.8):
  anchor_counts = {}
  for name, (template_path, _bbox_key) in STABLE_CAREER_SCREEN_ANCHORS.items():
    matches = device_action.match_template(template_path, screenshot, threshold=threshold)
    anchor_counts[name] = len(matches)
  return anchor_counts


def _has_stable_career_screen(anchor_counts):
  return any(count > 0 for count in anchor_counts.values())


def detect_scenario():
  update_startup_scan_snapshot(
    message="Opening details screen to confirm scenario.",
    sub_phase="detect_scenario_open_details",
    ocr_debug=[
      _template_debug_entry("details_button", "assets/buttons/details_btn.png", bbox_key="SCREEN_TOP_BBOX", extra={"threshold": 0.75}),
      _template_debug_entry("details_button_alt", "assets/buttons/details_btn_2.png", bbox_key="SCREEN_TOP_BBOX", extra={"threshold": 0.75}),
    ],
    reasoning_notes="Scenario detection requires opening the Details panel and matching a scenario banner.",
  )
  details_templates = [
    ("details_button", "assets/buttons/details_btn.png"),
    ("details_button_alt", "assets/buttons/details_btn_2.png"),
  ]
  found_details = False
  detail_attempt_entries = []

  def verify_details_opened():
    screenshot = device_action.screenshot()
    close_btn = device_action.best_template_match("assets/buttons/close_btn.png", screenshot)
    banner_name, banner_counts = _match_scenario_banners(screenshot)
    close_btn_score = round(close_btn["score"], 4) if close_btn is not None else None
    close_btn_passed = bool(close_btn is not None and close_btn["score"] >= 0.8)
    verified = close_btn_passed or any(count > 0 for count in banner_counts.values())
    verification_details = {
      "close_btn_score": close_btn_score,
      "close_btn_passed_threshold": close_btn_passed,
      "close_btn_location": close_btn["location"] if close_btn is not None else None,
      "close_btn_size": close_btn["size"] if close_btn is not None else None,
      "banner_first_match": banner_name,
      "banner_counts": banner_counts,
    }
    return verified, verification_details

  for field, template_path in details_templates:
    clicked, detail_entry = _attempt_template_click_with_debug(
      field,
      template_path,
      constants.SCREEN_TOP_BBOX,
      threshold=0.75,
      verify_after_click=verify_details_opened,
      click_mode="press_click",
    )
    detail_attempt_entries.append(detail_entry)
    update_startup_scan_snapshot(
      message=f"Scenario detection details attempt: {field}",
      sub_phase="detect_scenario_open_details",
      ocr_debug=detail_attempt_entries.copy(),
      reasoning_notes=(
        "Trying Details button templates for scenario detection. "
        f"latest_attempt={field} score={detail_entry.get('best_live_score')} "
        f"threshold={detail_entry.get('threshold')} passed={detail_entry.get('passed_threshold')} "
        f"attempted={detail_entry.get('click_attempted')} verified={detail_entry.get('click_verified')}"
      ),
    )
    if clicked:
      found_details = True
      break
  if not found_details:
    update_startup_scan_snapshot(
      message="Scenario detection deferred: details button not found.",
      sub_phase="detect_scenario_waiting_for_details",
      ocr_debug=detail_attempt_entries,
      reasoning_notes=(
        "The bot cannot confirm the scenario until the Details panel is visible. "
        f"detail_attempts={[(entry.get('field'), entry.get('best_live_score'), entry.get('threshold'), entry.get('click_attempted'), entry.get('click_verified')) for entry in detail_attempt_entries]}"
      ),
    )
    warning("Details button not found; scenario detection deferred.")
    return ""
  sleep(0.5)
  screenshot = device_action.screenshot()
  debug_window(screenshot, save_name="scenario_detection_details")
  raw_name, match_counts = _match_scenario_banners(screenshot)
  update_startup_scan_snapshot(
    message="Scenario banner scan complete.",
    sub_phase="detect_scenario_match_banner",
    ocr_debug=detail_attempt_entries + [
      _template_debug_entry(
        f"scenario_banner_{name}",
        f"assets/scenario_banner/{name}.png",
        parsed_value=count,
      )
      for name, count in match_counts.items()
    ],
    reasoning_notes=f"Raw banner match counts: {match_counts}",
  )
  device_action.locate_and_click("assets/buttons/close_btn.png", min_search_time=get_secs(1))
  sleep(0.5)
  if raw_name:
    scenario_name = _canonicalize_scenario_name(raw_name)
    update_startup_scan_snapshot(
      message=f"Scenario confirmed: {scenario_name}",
      sub_phase="detect_scenario_confirmed",
      ocr_debug=detail_attempt_entries + [
        _template_debug_entry(
          f"scenario_banner_{name}",
          f"assets/scenario_banner/{name}.png",
          parsed_value=count,
          extra={"canonical_name": _canonicalize_scenario_name(name)},
        )
        for name, count in match_counts.items()
      ],
      reasoning_notes=f"Scenario confirmed from details banner. raw='{raw_name}' canonical='{scenario_name}'",
    )
    info(
      f"Scenario detected from banner: raw='{raw_name}', canonical='{scenario_name}', "
      f"match_counts={match_counts}"
    )
    return scenario_name
  warning(f"No scenario banner matched; detection deferred. match_counts={match_counts}")
  return ""

LIMIT_TURNS = args.limit_turns
if LIMIT_TURNS is None:
  LIMIT_TURNS = 0

non_match_count = 0
action_count=0
last_state = CleanDefaultDict()
scenario_detection_attempts = 0


def _truncate(value, limit=180):
  text = str(value)
  if len(text) <= limit:
    return text
  return text[: limit - 3] + "..."


def _get_constant(name, default=None):
  return getattr(constants, name, default)


def _active_region_profile_info():
  settings = getattr(config, "REGION_ADJUSTER_CONFIG", {}) or {}
  profiles, active_profile, active_path = resolve_region_adjuster_profiles(settings)
  return {
    "active_profile": active_profile,
    "overrides_path": active_path,
    "available_profiles": sorted(profiles.keys()),
  }


def _region_debug_entry(field, region_key=None, bbox_key=None, parsed_value=None, source_type="ocr_region", extra=None):
  entry = {
    "field": field,
    "source_type": source_type,
    "scenario_name": constants.SCENARIO_NAME or "default",
    "platform_profile": getattr(config, "PLATFORM_PROFILE", "auto"),
  }
  if region_key:
    entry["region_key"] = region_key
    entry["region_xywh"] = _get_constant(region_key)
    if bbox_key is None and region_key.endswith("_REGION"):
      bbox_key = region_key[:-7] + "_BBOX"
  if bbox_key:
    entry["bbox_key"] = bbox_key
    entry["bbox_xyxy"] = _get_constant(bbox_key)
  if parsed_value is not None:
    entry["parsed_value"] = parsed_value
  if extra:
    entry.update(extra)
  return entry


def _planned_click(label, template=None, target=None, region_key=None, note=None):
  entry = {
    "label": label,
    "input_backend": bot.get_active_control_backend(),
    "screenshot_backend": bot.get_screenshot_backend(),
  }
  if template:
    entry["template"] = template
  if target is not None:
    entry["target"] = target
  if region_key:
    entry["region_key"] = region_key
  if note:
    entry["note"] = note
  return entry


def _template_debug_entry(field, template, bbox_key=None, parsed_value=None, extra=None):
  entry = {
    "field": field,
    "source_type": "template_match",
    "scenario_name": constants.SCENARIO_NAME or "unknown",
    "platform_profile": getattr(config, "PLATFORM_PROFILE", "auto"),
    "template": template,
  }
  if bbox_key:
    entry["bbox_key"] = bbox_key
    entry["bbox_xyxy"] = _get_constant(bbox_key)
  if parsed_value is not None:
    entry["parsed_value"] = parsed_value
  if extra:
    entry.update(extra)
  return entry


def _attempt_template_click_with_debug(field, template_path, region_ltrb, threshold=0.8, template_scaling=1.0, duration=0.225, verify_after_click=None, click_mode="default"):
  screenshot = device_action.screenshot(region_ltrb=region_ltrb)
  best_match = device_action.best_template_match(
    template_path,
    screenshot,
    template_scales=[device_action._effective_template_scale(template_scaling)],
  )

  best_score = None
  passed_threshold = False
  click_attempted = False
  click_verified = False
  click_target = None
  match_location = None
  match_size = None
  verification_details = None
  actual_click_mode = "not_attempted"

  if best_match is not None:
    best_score = round(best_match["score"], 4)
    passed_threshold = best_match["score"] >= threshold
    match_location = best_match["location"]
    match_size = best_match["size"]
    if passed_threshold:
      x, y = best_match["location"]
      w, h = best_match["size"]
      click_target = (
        region_ltrb[0] + x + w // 2,
        region_ltrb[1] + y + h // 2,
      )
      if click_mode == "press_click" and not bot.is_adb_input_active():
        actual_click_mode = "host_press_click"
        click_attempted = bool(pyautogui_actions.press_click(click_target, hold_duration=0.08, move_duration=duration))
      else:
        actual_click_mode = "adb_tap" if bot.is_adb_input_active() else "host_click"
        click_attempted = bool(device_action.click(click_target, duration=duration))
      verification_details = {
        "requested_click_mode": click_mode,
        "actual_click_mode": actual_click_mode,
        "input_backend": bot.get_active_control_backend(),
        "input_debug": (
          adb_actions.get_last_input_debug()
          if bot.is_adb_input_active()
          else dict(getattr(pyautogui_actions, "LAST_CLICK_DEBUG", {}) or {})
        ),
        "coordinate_diagnostics": {
          "host_search_region_bbox": list(region_ltrb),
          "host_match_location_in_region": list(match_location) if match_location is not None else None,
          "host_match_size": list(match_size) if match_size is not None else None,
          "host_click_target_absolute": list(click_target) if click_target is not None else None,
          "game_window_region": list(constants.GAME_WINDOW_REGION) if getattr(constants, "GAME_WINDOW_REGION", None) is not None else None,
          "game_window_bbox": list(constants.GAME_WINDOW_BBOX) if getattr(constants, "GAME_WINDOW_BBOX", None) is not None else None,
          "macos_full_screen_meta": dict(getattr(pyautogui_actions, "_macos_full_screen_meta", {}) or {}),
          "adb_display_info": (
            (bot.get_backend_state().get("adb") or {}).get("display_info")
            if bot.is_adb_input_active()
            else None
          ),
        },
      }
      if click_attempted and verify_after_click is not None:
        sleep(0.5)
        click_verified, post_click_details = verify_after_click()
        verification_details.update(post_click_details or {})

  entry = _template_debug_entry(
    field,
    template_path,
    parsed_value=(
      "verified_open"
      if click_verified else
      "click_unverified"
      if click_attempted else
      "matched"
      if passed_threshold else
      "below_threshold"
      if best_score is not None else
      "not_found"
    ),
    extra={
      "threshold": threshold,
      "best_live_score": best_score,
      "passed_threshold": passed_threshold,
      "click_attempted": click_attempted,
      "click_verified": click_verified,
      "match_location": match_location,
      "match_size": match_size,
      "click_target": click_target,
      "verification_details": verification_details,
    },
  )
  entry["bbox_xyxy"] = region_ltrb
  return click_verified, entry


def _profile_debug_entry():
  profile_info = _active_region_profile_info()
  return {
    "field": "ocr_region_profile",
    "source_type": "region_profile",
    "scenario_name": constants.SCENARIO_NAME or "unknown",
    "platform_profile": getattr(config, "PLATFORM_PROFILE", "auto"),
    "parsed_value": profile_info["active_profile"],
    "active_profile": profile_info["active_profile"],
    "overrides_path": profile_info["overrides_path"],
    "available_profiles": profile_info["available_profiles"],
  }


def _save_runtime_debug_image(image, stem):
  global runtime_debug_counter
  runtime_debug_dir = Path("logs/runtime_debug")
  runtime_debug_dir.mkdir(parents=True, exist_ok=True)
  safe_stem = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in stem)
  filename = runtime_debug_dir / f"{runtime_debug_counter:04d}_{safe_stem}.png"
  runtime_debug_counter += 1
  cv2.imwrite(str(filename), image)
  return str(filename)


def _resolve_template_path(template):
  if not template or template == "cached_templates":
    return ""
  candidate = Path(template)
  if candidate.exists():
    return str(candidate)
  rooted = Path.cwd() / template
  if rooted.exists():
    return str(rooted)
  return ""


def _best_template_score(template_path, crop):
  if crop is None or crop.size == 0:
    return None

  best_match = device_action.best_template_match(
    template_path,
    crop,
  )
  if best_match is None:
    return None
  return {
    "score": round(best_match["score"], 4),
    "scale": round(best_match["scale"], 3),
    "location": best_match["location"],
    "size": best_match["size"],
  }


def _capture_debug_crop(entry):
  bbox = entry.get("bbox_xyxy")
  region = entry.get("region_xywh")
  try:
    if bbox and len(bbox) == 4:
      crop = device_action.screenshot(region_ltrb=tuple(int(v) for v in bbox))
    elif region and len(region) == 4:
      crop = device_action.screenshot(region_xywh=tuple(int(v) for v in region))
    else:
      return entry
  except Exception:
    return entry

  if crop is None or getattr(crop, "size", 0) == 0:
    return entry

  crop_bgr = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
  crop_path = _save_runtime_debug_image(crop_bgr, entry.get("field", "ocr_region"))
  entry["search_image_path"] = crop_path

  template_path = _resolve_template_path(entry.get("template"))
  if template_path:
    entry["template_image_path"] = template_path
    best_score = _best_template_score(template_path, crop)
    if best_score is not None:
      if isinstance(best_score, dict):
        entry["best_match_score"] = best_score["score"]
        entry["best_match_scale"] = best_score["scale"]
        entry["best_match_location"] = best_score["location"]
        entry["best_match_size"] = best_score["size"]
      else:
        entry["best_match_score"] = round(best_score, 4)

  return entry


def _enrich_ocr_debug_entries(entries):
  enriched = []
  for raw_entry in entries or []:
    entry = dict(raw_entry)
    if entry.get("source_type") in ("template_match", "ocr_region", "screen_region", "template_region"):
      entry = _capture_debug_crop(entry)
    enriched.append(entry)
  return enriched


def _base_ocr_debug_entries(state_obj):
  scenario_name = constants.SCENARIO_NAME or "default"
  entries = [
    _region_debug_entry("turn", "UNITY_TURN_REGION" if scenario_name == "unity" else "MANT_TURN_REGION" if scenario_name == "trackblazer" else "TURN_REGION", parsed_value=state_obj.get("turn")),
    _region_debug_entry("year", "UNITY_YEAR_REGION" if scenario_name == "unity" else "MANT_YEAR_REGION" if scenario_name == "trackblazer" else "YEAR_REGION", parsed_value=state_obj.get("year")),
    _region_debug_entry("criteria", "UNITY_CRITERIA_REGION" if scenario_name == "unity" else "MANT_CRITERIA_REGION" if scenario_name == "trackblazer" else "CRITERIA_REGION", parsed_value=_truncate(state_obj.get("criteria", ""))),
    _region_debug_entry("energy", "UNITY_ENERGY_REGION" if scenario_name == "unity" else "MANT_ENERGY_REGION" if scenario_name == "trackblazer" else "ENERGY_REGION", parsed_value=f"{state_obj.get('energy_level', '?')}/{state_obj.get('max_energy', '?')}"),
  ]
  if scenario_name == "trackblazer":
    entries.extend(
      [
        _region_debug_entry("trackblazer_grade_points", "MANT_GRADE_POINT_REGION", parsed_value=state_obj.get("grade_points")),
        _region_debug_entry("trackblazer_shop_coins", "MANT_SHOP_COIN_REGION", parsed_value=state_obj.get("shop_coins")),
        _region_debug_entry("trackblazer_shop_button", "MANT_SHOP_BUTTON_REGION"),
      ]
    )
  aptitudes = state_obj.get("aptitudes") or {}
  for key, ratios in APTITUDE_BOX_RATIOS.items():
    entries.append(
      _region_debug_entry(
        f"aptitude_{key}",
        "FULL_STATS_APTITUDE_REGION",
        parsed_value=aptitudes.get(key, "missing"),
        extra={"box_ratios": ratios},
      )
    )
  missing_keys = state_obj.get("aptitudes_missing_keys")
  if missing_keys:
    entries.append(
      {
        "field": "aptitude_missing_keys",
        "source_type": "ocr_summary",
        "scenario_name": scenario_name,
        "platform_profile": getattr(config, "PLATFORM_PROFILE", "auto"),
        "parsed_value": missing_keys,
      }
    )
  return entries


def _planned_clicks_for_action(action):
  if not hasattr(action, "func"):
    return []
  if action.func == "do_training":
    training_name = action.get("training_name")
    return [
      _planned_click("Open training menu", "assets/buttons/training_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
      _planned_click(
        f"Select training: {training_name or 'unknown'}",
        target=constants.TRAINING_BUTTON_POSITIONS.get(training_name),
        note="Double-click training slot",
      ),
    ]
  if action.func == "do_rest":
    return [
      _planned_click("Click rest button", "assets/buttons/rest_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
      _planned_click("Fallback summer rest button", "assets/buttons/rest_summer_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
    ]
  if action.func == "do_recreation":
    return [
      _planned_click("Open recreation menu", "assets/buttons/recreation_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
      _planned_click("Fallback summer recreation button", "assets/buttons/rest_summer_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
    ]
  if action.func == "do_infirmary":
    return [_planned_click("Click infirmary button", "assets/buttons/infirmary_btn.png", region_key="SCREEN_BOTTOM_BBOX")]
  if action.func == "do_race":
    race_name = action.get("race_name")
    race_template = f"assets/races/{race_name}.png" if race_name and race_name not in ("", "any") else action.get("race_image_path") or "assets/ui/match_track.png"
    return [
      _planned_click("Open race menu", "assets/buttons/races_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
      _planned_click("Scan/select race entry", race_template, region_key="RACE_LIST_BOX_BBOX"),
      _planned_click("Confirm race", "assets/buttons/race_btn.png"),
      _planned_click("Fallback BlueStacks confirm", "assets/buttons/bluestacks/race_btn.png"),
    ]
  if action.func == "buy_skill":
    return [
      _planned_click("Open skills menu", "assets/buttons/skills_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
      _planned_click("Scan skill rows", region_key="SCROLLING_SKILL_SCREEN_BBOX", note="OCR and template scan only"),
      _planned_click("Confirm selected skills", "assets/buttons/confirm_btn.png"),
      _planned_click("Learn selected skills", "assets/buttons/learn_btn.png"),
      _planned_click("Exit skill screen", "assets/buttons/back_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
    ]
  return []


def _ocr_debug_for_action(state_obj, action):
  entries = _base_ocr_debug_entries(state_obj)
  if not hasattr(action, "func"):
    return entries
  if action.func == "do_training":
    scenario_name = constants.SCENARIO_NAME or "default"
    entries.extend(
      [
        _region_debug_entry("training_failure", "UNITY_FAILURE_REGION" if scenario_name == "unity" else "MANT_FAILURE_REGION" if scenario_name == "trackblazer" else "FAILURE_REGION"),
        _region_debug_entry("training_support_icons", "UNITY_SUPPORT_CARD_ICON_REGION" if scenario_name == "unity" else "MANT_SUPPORT_CARD_ICON_REGION" if scenario_name == "trackblazer" else "SUPPORT_CARD_ICON_REGION"),
        _region_debug_entry("training_stat_gains", "UNITY_STAT_GAINS_REGION" if scenario_name == "unity" else "MANT_STAT_GAINS_REGION" if scenario_name == "trackblazer" else "URA_STAT_GAINS_REGION"),
      ]
    )
  elif action.func == "do_race":
    entries.append(_region_debug_entry("race_list", bbox_key="RACE_LIST_BOX_BBOX", source_type="screen_region"))
  elif action.func == "buy_skill":
    entries.extend(
      [
        _region_debug_entry("skill_list", bbox_key="SCROLLING_SKILL_SCREEN_BBOX", source_type="screen_region"),
        _region_debug_entry("skills_button", bbox_key="SCREEN_BOTTOM_BBOX", source_type="template_region", extra={"template": "assets/buttons/skills_btn.png"}),
      ]
    )
  return entries


def build_review_snapshot(state_obj, action, reasoning_notes=None, sub_phase=None, ocr_debug=None, planned_clicks=None):
  profile_info = _active_region_profile_info()
  backend_state = bot.get_backend_state()
  debug_entries = ([ _profile_debug_entry() ] + ocr_debug) if ocr_debug is not None else ([ _profile_debug_entry() ] + _ocr_debug_for_action(state_obj, action))
  debug_entries = _enrich_ocr_debug_entries(debug_entries)
  state_summary = {
    "year": state_obj.get("year"),
    "turn": state_obj.get("turn"),
    "criteria": _truncate(state_obj.get("criteria", "")),
    "energy_level": state_obj.get("energy_level"),
    "max_energy": state_obj.get("max_energy"),
    "current_mood": state_obj.get("current_mood"),
    "date_event_available": state_obj.get("date_event_available"),
    "race_mission_available": state_obj.get("race_mission_available"),
    "aptitudes": state_obj.get("aptitudes"),
    "ocr_region_profile": profile_info["active_profile"],
    "ocr_overrides_path": profile_info["overrides_path"],
    "control_backend": backend_state.get("active_backend"),
    "screenshot_backend": backend_state.get("screenshot_backend"),
    "device_id": backend_state.get("device_id"),
  }
  selected_action = {
    "func": getattr(action, "func", None),
    "training_name": action.get("training_name") if hasattr(action, "get") else None,
    "race_name": action.get("race_name") if hasattr(action, "get") else None,
    "score_tuple": action.get("training_data", {}).get("score_tuple") if hasattr(action, "get") else None,
  }
  ranked_trainings = []
  available_trainings = action.get("available_trainings", {}) if hasattr(action, "get") else {}
  for training_name, training_data in available_trainings.items():
    ranked_trainings.append(
      {
        "name": training_name,
        "score_tuple": training_data.get("score_tuple"),
        "failure": training_data.get("failure"),
        "total_supports": training_data.get("total_supports"),
        "total_rainbow_friends": training_data.get("total_rainbow_friends"),
        "total_friendship_increases": training_data.get("total_friendship_increases"),
        "stat_gains": training_data.get("stat_gains"),
        "unity_gauge_fills": training_data.get("unity_gauge_fills"),
        "unity_spirit_explosions": training_data.get("unity_spirit_explosions"),
      }
    )
  return {
    "scenario_name": constants.SCENARIO_NAME or "default",
    "turn_label": f"{state_obj.get('year', '?')} / {state_obj.get('turn', '?')}",
    "energy_label": f"{state_obj.get('energy_level', '?')}/{state_obj.get('max_energy', '?')}",
    "sub_phase": sub_phase or "idle",
    "execution_intent": bot.get_execution_intent(),
    "state_summary": state_summary,
    "selected_action": selected_action,
    "available_actions": list(getattr(action, "available_actions", [])),
    "ranked_trainings": ranked_trainings,
    "reasoning_notes": reasoning_notes or "",
    "min_scores": action.get("min_scores") if hasattr(action, "get") else None,
    "backend_state": backend_state,
    "ocr_debug": debug_entries,
    "planned_clicks": planned_clicks if planned_clicks is not None else _planned_clicks_for_action(action),
  }


def build_startup_scan_snapshot(sub_phase, message, ocr_debug=None, reasoning_notes=None, available_actions=None):
  profile_info = _active_region_profile_info()
  backend_state = bot.get_backend_state()
  debug_entries = _enrich_ocr_debug_entries([_profile_debug_entry()] + (ocr_debug or []))
  return {
    "scenario_name": constants.SCENARIO_NAME or "unknown",
    "turn_label": "",
    "energy_label": "",
    "sub_phase": sub_phase or "scan_lobby_templates",
    "execution_intent": bot.get_execution_intent(),
    "state_summary": {
      "ocr_region_profile": profile_info["active_profile"],
      "ocr_overrides_path": profile_info["overrides_path"],
      "control_backend": backend_state.get("active_backend"),
      "screenshot_backend": backend_state.get("screenshot_backend"),
      "device_id": backend_state.get("device_id"),
    },
    "selected_action": {},
    "available_actions": available_actions or [],
    "ranked_trainings": [],
    "reasoning_notes": reasoning_notes or message,
    "min_scores": None,
    "backend_state": backend_state,
    "ocr_debug": debug_entries,
    "planned_clicks": [],
  }


def update_startup_scan_snapshot(message, sub_phase, ocr_debug=None, reasoning_notes=None, available_actions=None):
  bot.set_phase("scanning_lobby", status="active", message=message)
  bot.set_snapshot(
    build_startup_scan_snapshot(
      sub_phase=sub_phase,
      message=message,
      ocr_debug=ocr_debug,
      reasoning_notes=reasoning_notes,
      available_actions=available_actions,
    )
  )
  publish_runtime_state()


def update_operator_snapshot(
  state_obj=None,
  action=None,
  phase=None,
  status="active",
  message="",
  error_text="",
  reasoning_notes=None,
  sub_phase=None,
  ocr_debug=None,
  planned_clicks=None,
):
  if phase:
    bot.set_phase(phase, status=status, message=message, error=error_text)
  elif message or error_text:
    current = bot.get_runtime_state()
    bot.set_phase(current["phase"], status=status, message=message, error=error_text)
  if state_obj is not None and action is not None:
    bot.set_snapshot(
      build_review_snapshot(
        state_obj,
        action,
        reasoning_notes=reasoning_notes,
        sub_phase=sub_phase,
        ocr_debug=ocr_debug,
        planned_clicks=planned_clicks,
      )
    )
  publish_runtime_state()


def review_action_before_execution(state_obj, action, message="Review action before execution.", sub_phase=None, ocr_debug=None, planned_clicks=None):
  should_wait = config.EXECUTION_MODE == "semi_auto" or bot.is_pause_requested()
  update_operator_snapshot(
    state_obj,
    action,
    phase="waiting_for_confirmation",
    message=message,
    sub_phase=sub_phase,
    ocr_debug=ocr_debug,
    planned_clicks=planned_clicks,
  )
  if not should_wait:
    return True
  ensure_operator_console()
  bot.begin_review_wait()
  publish_runtime_state()
  while bot.is_bot_running and not bot.stop_event.is_set():
    if bot.review_event.wait(timeout=0.1):
      break
  waiting_interrupted = not bot.is_bot_running or bot.stop_event.is_set()
  if waiting_interrupted:
    bot.cancel_review_wait()
    update_operator_snapshot(
      state_obj,
      action,
      phase="recovering",
      status="error",
      error_text="Review wait interrupted by stop request.",
    )
    return False
  bot.clear_pause_request()
  update_operator_snapshot(
    state_obj,
    action,
    phase="executing_action",
    message="Executing approved action.",
    sub_phase=sub_phase,
    ocr_debug=ocr_debug,
    planned_clicks=planned_clicks,
  )
  return True


def run_action_with_review(state_obj, action, review_message, pre_run_hook=None, sub_phase=None, ocr_debug=None, planned_clicks=None):
  if not review_action_before_execution(state_obj, action, review_message, sub_phase=sub_phase, ocr_debug=ocr_debug, planned_clicks=planned_clicks):
    return "failed"
  execution_intent = bot.get_execution_intent()
  if execution_intent != "execute":
    update_operator_snapshot(
      state_obj,
      action,
      phase="waiting_for_confirmation",
      status="idle",
      message=f"{execution_intent} mode active; action not executed.",
      reasoning_notes="Use execute mode to commit clicks. Current view shows OCR/debug and planned click targets only.",
      sub_phase=sub_phase,
      ocr_debug=ocr_debug,
      planned_clicks=planned_clicks,
    )
    return "previewed"
  if pre_run_hook is not None:
    pre_run_hook()
  result = action.run()
  if not result:
    update_operator_snapshot(
      state_obj,
      action,
      phase="recovering",
      status="error",
      error_text=f"Action failed: {action.func}",
      sub_phase=sub_phase,
      ocr_debug=ocr_debug,
      planned_clicks=planned_clicks,
    )
    return "failed"
  return "executed"


def maybe_review_skill_purchase(state_obj, current_action_count, race_check=False):
  context = get_skill_purchase_context(state_obj, current_action_count, race_check=race_check)
  if not context.get("should_check"):
    return "skipped"
  skill_action = Action()
  skill_action.func = "buy_skill"
  skill_action["race_check"] = race_check
  skill_action["shopping_list"] = context.get("shopping_list", [])
  reasoning_notes = context.get("reason", "")
  update_operator_snapshot(
    state_obj,
    skill_action,
    phase="evaluating_strategy",
    message="Skill purchase review ready.",
    reasoning_notes=reasoning_notes,
    sub_phase="evaluate_skill_purchase",
    ocr_debug=context.get("ocr_debug"),
    planned_clicks=context.get("planned_clicks"),
  )
  if not review_action_before_execution(
    state_obj,
    skill_action,
    "Review skill purchase flow before execution.",
    sub_phase="scan_skill_list",
    ocr_debug=context.get("ocr_debug"),
    planned_clicks=context.get("planned_clicks"),
  ):
    return "failed"
  execution_intent = bot.get_execution_intent()
  if execution_intent != "execute":
    update_operator_snapshot(
      state_obj,
      skill_action,
      phase="waiting_for_confirmation",
      status="idle",
      message=f"{execution_intent} mode active; skill purchase not executed.",
      reasoning_notes=reasoning_notes,
      sub_phase="preview_skill_purchase",
      ocr_debug=context.get("ocr_debug"),
      planned_clicks=context.get("planned_clicks"),
    )
    return "previewed"
  buy_skill(state_obj, current_action_count, race_check=race_check)
  update_operator_snapshot(
    state_obj,
    skill_action,
    phase="executing_action",
    message="Skill purchase flow executed.",
    reasoning_notes=reasoning_notes,
    sub_phase="confirm_skill_purchase",
    ocr_debug=context.get("ocr_debug"),
    planned_clicks=context.get("planned_clicks"),
  )
  return "executed"

def career_lobby(dry_run_turn=False):
  global last_state, action_count, non_match_count, scenario_detection_attempts
  non_match_count = 0
  action_count=0
  scenario_detection_attempts = 0
  sleep(1)
  bot.PREFERRED_POSITION_SET = False
  constants.SCENARIO_NAME = ""
  clear_aptitudes_cache()
  strategy = Strategy()
  init_skill_py()
  if config.EXECUTION_MODE == "semi_auto":
    ensure_operator_console()
  update_startup_scan_snapshot(
    message="Career loop started.",
    sub_phase="scan_lobby_init",
    ocr_debug=[
      _template_debug_entry("career_screen_scan", "cached_templates", bbox_key="GAME_WINDOW_BBOX"),
      _template_debug_entry("tazuna_hint", "assets/ui/tazuna_hint.png", bbox_key="GAME_WINDOW_BBOX"),
    ],
    reasoning_notes="Waiting for a stable career screen before collecting state.",
  )
  try:
    while bot.is_bot_running:
      update_startup_scan_snapshot(
        message="Scanning career lobby for next state.",
        sub_phase="scan_lobby_templates",
        ocr_debug=[
          _template_debug_entry("career_screen_scan", "cached_templates", bbox_key="GAME_WINDOW_BBOX"),
          _template_debug_entry("tazuna_hint", "assets/ui/tazuna_hint.png", bbox_key="GAME_WINDOW_BBOX"),
        ],
        reasoning_notes="The bot is scanning generic lobby templates while waiting for a stable training screen.",
      )
      sleep(1)
      device_action.flush_screenshot_cache()
      screenshot = device_action.screenshot()
      stable_anchor_counts = _detect_stable_career_screen_anchors(screenshot, threshold=0.8)

      if non_match_count > 20:
        info("Career lobby stuck, quitting.")
        quit()
      if constants.SCENARIO_NAME == "":
        info("Trying to find what scenario we're on.")
        if device_action.locate_and_click("assets/unity/unity_cup_btn.png", min_search_time=get_secs(1)):
          constants.SCENARIO_NAME = "unity"
          info("Unity race detected, calling unity cup function. If this is not correct, please report this.")
          unity_cup_function()
          continue

      matches = device_action.match_cached_templates(cached_templates, region_ltrb=constants.GAME_WINDOW_BBOX, threshold=0.9, stop_after_first_match=True)
      def click_match(matches):
        if matches and len(matches) > 0:
          x, y, w, h = matches[0]
          cx = x + w // 2
          cy = y + h // 2
          return device_action.click(target=(cx, cy), text=f"Clicked match: {matches[0]}")
        return False

      # modify this portion to get event data out instead. Maybe call collect state or a partial version of it.
      if len(matches.get("event", [])) > 0:
        select_event()
        continue
      if click_match(matches.get("inspiration")):
        info("Pressed inspiration.")
        non_match_count = 0
        continue
      if click_match(matches.get("next")):
        info("Pressed next.")
        non_match_count = 0
        continue
      if click_match(matches.get("next2")):
        info("Pressed next2.")
        non_match_count = 0
        continue
      if matches.get("cancel", False):
        clock_icon = device_action.match_template("assets/icons/clock_icon.png", screenshot=screenshot, threshold=0.9)
        if clock_icon:
          info("Lost race, wait for input.")
          non_match_count += 1
        elif click_match(matches.get("cancel")):
          info("Pressed cancel.")
          non_match_count = 0
        continue
      if click_match(matches.get("retry")):
        info("Pressed retry.")
        non_match_count = 0
        continue

      # adding skip function for claw machine
      if matches.get("claw_btn", False):
        if not config.USE_SKIP_CLAW_MACHINE:
          continue

        info(f"Sleeping {get_secs(10)} seconds to allow for claw machine reset")
        #sleep(10)
        play_claw_machine(matches["claw_btn"][0])
        info("Played claw machine.")
        non_match_count = 0
        continue

      if click_match(matches.get("ok_2_btn")):
        info("Pressed Okay button.")
        non_match_count = 0
        continue

      if constants.SCENARIO_NAME == "unity":
        unity_matches = device_action.match_cached_templates(cached_unity_templates, region_ltrb=constants.GAME_WINDOW_BBOX)
        if click_match(unity_matches.get("unity_cup_btn")):
          info("Pressed unity cup.")
          unity_cup_function()
          non_match_count = 0
          continue
        if click_match(unity_matches.get("close_btn")):
          info("Pressed close.")
          non_match_count = 0
          continue
        if click_match(unity_matches.get("unity_banner_mid_screen")):
          info("Unity banner mid screen found. Starting over.")
          non_match_count = 0
          continue

      if not _has_stable_career_screen(stable_anchor_counts):
        update_startup_scan_snapshot(
          message="Stable career screen not confirmed yet.",
          sub_phase="scan_lobby_waiting_for_tazuna",
          ocr_debug=[
            _template_debug_entry("tazuna_hint", "assets/ui/tazuna_hint.png", bbox_key="GAME_WINDOW_BBOX", parsed_value=stable_anchor_counts.get("tazuna_hint", 0)),
            _template_debug_entry("training_button", "assets/buttons/training_btn.png", bbox_key="SCREEN_BOTTOM_BBOX", parsed_value=stable_anchor_counts.get("training_button", 0)),
            _template_debug_entry("rest_button", "assets/buttons/rest_btn.png", bbox_key="SCREEN_BOTTOM_BBOX", parsed_value=stable_anchor_counts.get("rest_button", 0)),
            _template_debug_entry("recreation_button", "assets/buttons/recreation_btn.png", bbox_key="SCREEN_BOTTOM_BBOX", parsed_value=stable_anchor_counts.get("recreation_button", 0)),
            _template_debug_entry("races_button", "assets/buttons/races_btn.png", bbox_key="SCREEN_BOTTOM_BBOX", parsed_value=stable_anchor_counts.get("races_button", 0)),
            _template_debug_entry("details_button", "assets/buttons/details_btn.png", bbox_key="SCREEN_TOP_BBOX", parsed_value=stable_anchor_counts.get("details_button", 0)),
            _template_debug_entry("details_button_alt", "assets/buttons/details_btn_2.png", bbox_key="SCREEN_TOP_BBOX", parsed_value=stable_anchor_counts.get("details_button_alt", 0)),
            _template_debug_entry("next_button", "assets/buttons/next_btn.png", bbox_key="GAME_WINDOW_BBOX", parsed_value=len(matches.get("next", []))),
            _template_debug_entry("next_button_alt", "assets/buttons/next2_btn.png", bbox_key="GAME_WINDOW_BBOX", parsed_value=len(matches.get("next2", []))),
            _template_debug_entry("event_choice", "assets/icons/event_choice_1.png", bbox_key="GAME_WINDOW_BBOX", parsed_value=len(matches.get("event", []))),
            _template_debug_entry("cancel_button", "assets/buttons/cancel_btn.png", bbox_key="GAME_WINDOW_BBOX", parsed_value=len(matches.get("cancel", []))),
          ],
          reasoning_notes=f"Stable screen anchors were not found yet. anchor_counts={stable_anchor_counts}",
        )
        print(".", end="")
        non_match_count += 1
        continue
      else:
        info(f"Stable career screen matched, moving to state collection. anchor_counts={stable_anchor_counts}")
        if constants.SCENARIO_NAME == "":
          scenario_detection_attempts += 1
          scenario_name = detect_scenario()
          if scenario_name:
            constants.SCENARIO_NAME = scenario_name
            info(f"Scenario confirmed at startup checkpoint: {scenario_name}")
          elif scenario_detection_attempts >= MAX_SCENARIO_DETECTION_ATTEMPTS:
            warning(
              "Scenario detection failed repeatedly; continuing with generic/default logic for now. "
              "Trackblazer-specific logic will stay inactive until a banner match succeeds."
            )
          else:
            warning(
              f"Scenario detection not confirmed yet (attempt {scenario_detection_attempts}/{MAX_SCENARIO_DETECTION_ATTEMPTS}). "
              "Continuing with generic/default logic and will retry on the next stable turn."
            )
        non_match_count = 0

      info(f"Bot version: {VERSION}")

      action = Action()
      update_operator_snapshot(phase="collecting_main_state", message="Collecting main state.")
      state_obj = collect_main_state()

      if state_obj["turn"] == "Race Day":
        action.func = "do_race"
        action["is_race_day"] = True
        action["year"] = state_obj["year"]
        info(f"Race Day")
        race_day_result = run_action_with_review(
          state_obj,
          action,
          "Race day detected. Review before entering race.",
          sub_phase="preview_race_selection",
        )
        if race_day_result == "executed":
          record_and_finalize_turn(state_obj, action)
          continue
        elif race_day_result == "previewed":
          continue
        else:
          action.func = None
          del action.options["is_race_day"]
          del action.options["year"]

      if config.PRIORITIZE_MISSIONS_OVER_G1 and config.DO_MISSION_RACES_IF_POSSIBLE and state_obj["race_mission_available"]:
        debug(f"Mission race logic entered with priority.")
        action.func = "do_race"
        action["race_name"] = "any"
        action["race_image_path"] = "assets/ui/match_track.png"
        action["race_mission_available"] = True
        skill_result = maybe_review_skill_purchase(state_obj, action_count, race_check=True)
        if skill_result in ("failed", "previewed"):
          continue
        mission_race_result = run_action_with_review(
          state_obj,
          action,
          "Mission race selected. Review before race entry.",
          sub_phase="preview_race_selection",
        )
        if mission_race_result == "executed":
          record_and_finalize_turn(state_obj, action)
          continue
        elif mission_race_result == "previewed":
          continue
        else:
          action.func = None
          action.options.pop("race_name", None)
          action.options.pop("race_image_path", None)
          action.options.pop("race_mission_available", None)

      # check and do scheduled races. Dirty version, should be cleaned up.
      action = strategy.check_scheduled_races(state_obj, action)
      if "race_name" in action.options:
        action.func = "do_race"
        info(f"Taking action: {action.func}")
        skill_result = maybe_review_skill_purchase(state_obj, action_count, race_check=True)
        if skill_result in ("failed", "previewed"):
          continue
        scheduled_race_result = run_action_with_review(
          state_obj,
          action,
          "Scheduled race selected. Review before race entry.",
          sub_phase="preview_race_selection",
        )
        if scheduled_race_result == "executed":
          record_and_finalize_turn(state_obj, action)
          continue
        elif scheduled_race_result == "previewed":
          continue
        else:
          action.func = None
          action.options.pop("race_name", None)
          action.options.pop("race_image_path", None)

      if (not config.PRIORITIZE_MISSIONS_OVER_G1) and config.DO_MISSION_RACES_IF_POSSIBLE and state_obj["race_mission_available"]:
        debug(f"Mission race logic entered.")
        action.func = "do_race"
        action["race_name"] = "any"
        action["race_image_path"] = "assets/ui/match_track.png"
        action["prioritize_missions_over_g1"] = config.PRIORITIZE_MISSIONS_OVER_G1
        action["race_mission_available"] = True
        skill_result = maybe_review_skill_purchase(state_obj, action_count, race_check=True)
        if skill_result in ("failed", "previewed"):
          continue
        mission_race_result = run_action_with_review(
          state_obj,
          action,
          "Mission race selected. Review before race entry.",
          sub_phase="preview_race_selection",
        )
        if mission_race_result == "executed":
          record_and_finalize_turn(state_obj, action)
          continue
        elif mission_race_result == "previewed":
          continue
        else:
          action.func = None
          action.options.pop("race_name", None)
          action.options.pop("race_image_path", None)
          action.options.pop("race_mission_available", None)

      # check and do goal races. Dirty version, should be cleaned up.
      if not "Achieved" in state_obj["criteria"]:
        action = strategy.decide_race_for_goal(state_obj, action)
        if action.func == "do_race":
          info(f"Taking action: {action.func}")
          skill_result = maybe_review_skill_purchase(state_obj, action_count, race_check=True)
          if skill_result in ("failed", "previewed"):
            continue
          goal_race_result = run_action_with_review(
            state_obj,
            action,
            "Goal race selected. Review before race entry.",
            sub_phase="preview_race_selection",
          )
          if goal_race_result == "executed":
            record_and_finalize_turn(state_obj, action)
            continue
          elif goal_race_result == "previewed":
            continue
          else:
            action.func = None

      training_function_name = strategy.get_training_template(state_obj)['training_function']

      update_operator_snapshot(phase="collecting_training_state", message="Scanning all trainings.")
      state_obj = collect_training_state(state_obj, training_function_name)

      # Review skill buying separately so OCR and planned clicks can be inspected.
      skill_result = maybe_review_skill_purchase(state_obj, action_count)
      if skill_result in ("failed", "previewed"):
        continue

      log_encoded(f"{state_obj}", "Encoded state: ")
      info(f"State: {state_obj}")

      update_operator_snapshot(phase="evaluating_strategy", message="Evaluating strategy.")
      action = strategy.decide(state_obj, action)
      update_operator_snapshot(state_obj, action, phase="evaluating_strategy", message="Strategy decision ready.")

      if isinstance(action, dict):
        update_operator_snapshot(
          state_obj,
          Action(),
          phase="recovering",
          status="error",
          error_text="Strategy returned invalid action structure.",
        )
        error(f"Strategy returned an invalid action. Please report this line. Returned structure: {action}")
      elif action.func == "no_action":
        update_operator_snapshot(state_obj, action, phase="recovering", status="error", error_text="State invalid, retrying.")
        info("State is invalid, retrying...")
        debug(f"State: {state_obj}")
      elif action.func == "skip_turn":
        update_operator_snapshot(state_obj, action, phase="recovering", message="Skipping turn, retrying.")
        info("Skipping turn, retrying...")
      else:
        info(f"Taking action: {action.func}")

        # go to skill buy function if we come across a do_race function, conditions are handled in buy_skill
        if dry_run_turn:
          update_operator_snapshot(state_obj, action, phase="recovering", message="Dry run turn requested; quitting.")
          info("Dry run turn, quitting.")
          quit()
        action_result = run_action_with_review(
          state_obj,
          action,
          "Review proposed action before execution.",
          sub_phase="preview_race_selection" if action.func == "do_race" else "preview_action_clicks",
        )
        executed_action = action_result == "executed"
        if action_result == "previewed":
          continue
        elif action_result != "executed":
          if action.available_actions:  # Check if the list is not empty
            action.available_actions.pop(0)

          if action.get("race_mission_available") and action.func == "do_race":
            info(f"Couldn't match race mission to aptitudes, trying next action.")
          else:
            info(f"Action {action.func} failed, trying other actions.")
          info(f"Available actions: {action.available_actions}")

          for function_name in action.available_actions:
            sleep(1)
            info(f"Trying action: {function_name}")
            action.func = function_name
            # go to skill buy function if we come across a do_race function, conditions are handled in buy_skill
            retry_result = run_action_with_review(
              state_obj,
              action,
              f"Retry action {function_name}.",
              sub_phase="preview_race_selection" if action.func == "do_race" else "preview_action_clicks",
            )
            if retry_result == "executed":
              executed_action = True
              break
            if retry_result == "previewed":
              executed_action = False
              break
            info(f"Action {function_name} failed, trying other actions.")

        if not executed_action:
          continue
        record_and_finalize_turn(state_obj, action)
        continue

  except BotStopException:
    info("Bot stopped by user.")
    update_operator_snapshot(phase="idle", message="Bot stopped by user.")
    return

def record_and_finalize_turn(state_obj, action):
  global last_state, action_count
  if args.debug is not None:
    record_turn(state_obj, last_state, action)
    last_state = state_obj

  action_count += 1
  if LIMIT_TURNS > 0:
    if action_count >= LIMIT_TURNS:
      info(f"Completed {action_count} actions, stopping bot as requested.")
      quit()
  update_operator_snapshot(state_obj, action, phase="scanning_lobby", message="Turn complete. Returning to lobby scan.")
