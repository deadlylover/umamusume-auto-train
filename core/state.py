import difflib
import numpy as np
import operator
import re
import cv2
import time
from pathlib import Path
from PIL import Image

from utils.log import info, warning, error, debug, debug_window, args

from utils.screenshot import enhanced_screenshot, enhance_image_for_ocr, binarize_between_colors, crop_after_plus_component, clean_noise, custom_grabcut
from core.ocr import extract_text, extract_number, extract_allowed_text, reader
from core.recognizer import count_pixels_of_color, find_color_of_pixel, closest_color, compare_brightness
from utils.tools import click, sleep, get_secs, check_race_suitability, get_aptitude_index
import utils.device_action_wrapper as device_action
import utils.pyautogui_actions as pyautogui_actions
import core.bot as bot
from core.platform.window_focus import apply_configured_recognition_geometry
from core.race_selector import get_effective_schedule_entries

from utils.shared import CleanDefaultDict, read_status_effects_from_current_full_stats
import core.config as config
import utils.constants as constants
from collections import defaultdict
from math import floor
from statistics import median

aptitudes_cache = {}
_last_turn = None  # Best-effort fallback when OCR intermittently fails.
_runtime_ocr_debug = {}
_RUNTIME_DEBUG_IMAGE_CAPTURE_ENABLED = False  # Re-enable this if you need OCR/search crops written under logs/runtime_debug again.
_TRACKBLAZER_CLIMAX_YEAR_ALIASES = (
  "Finale Underway",
  "TS Climax Races Underway",
  "Twinkle Star Climax Races Underway",
  "Climax Races Underway",
)
APTITUDE_BOX_RATIOS = {
  "surface_turf": (0.0, 0.00, 0.25, 0.33),
  "surface_dirt": (0.25, 0.00, 0.25, 0.33),
  "distance_sprint": (0.0, 0.33, 0.25, 0.33),
  "distance_mile": (0.25, 0.33, 0.25, 0.33),
  "distance_medium": (0.50, 0.33, 0.25, 0.33),
  "distance_long": (0.75, 0.33, 0.25, 0.33),
  "style_front": (0.0, 0.66, 0.25, 0.33),
  "style_pace": (0.25, 0.66, 0.25, 0.33),
  "style_late": (0.50, 0.66, 0.25, 0.33),
  "style_end": (0.75, 0.66, 0.25, 0.33),
}


def _save_training_scan_debug_image(image, training_name, label):
  if image is None or getattr(image, "size", 0) == 0:
    return ""
  if not _RUNTIME_DEBUG_IMAGE_CAPTURE_ENABLED:
    return ""
  runtime_debug_dir = Path("logs/runtime_debug")
  runtime_debug_dir.mkdir(parents=True, exist_ok=True)
  safe_training = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in str(training_name))
  safe_label = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in str(label))
  filename = runtime_debug_dir / f"scan_{int(time.time() * 1000)}_{safe_training}_{safe_label}.png"
  image_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
  cv2.imwrite(str(filename), image_bgr)
  return str(filename)


def clear_runtime_ocr_debug():
  global _runtime_ocr_debug
  _runtime_ocr_debug = {}


def snapshot_runtime_ocr_debug():
  return {key: dict(value) for key, value in _runtime_ocr_debug.items()}


def record_runtime_ocr_debug(field, image=None, extra=None, image_path=None):
  global _runtime_ocr_debug
  entry = dict(_runtime_ocr_debug.get(field, {}))
  if image_path:
    entry["search_image_path"] = image_path
  elif image is not None:
    saved_path = _save_training_scan_debug_image(image, "state", field)
    if saved_path:
      entry["search_image_path"] = saved_path
  if extra:
    entry.update(extra)
  _runtime_ocr_debug[field] = entry


def _compact_ocr_text(text):
  return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _normalize_trackblazer_climax_year_text(text):
  raw_text = re.sub(r"\s+", " ", str(text or "")).strip()
  if not raw_text or constants.SCENARIO_NAME not in ("mant", "trackblazer"):
    return None

  compact = _compact_ocr_text(raw_text)
  alias_compacts = [_compact_ocr_text(alias) for alias in _TRACKBLAZER_CLIMAX_YEAR_ALIASES]
  if compact in alias_compacts:
    return "Finale Underway"

  fuzzy = difflib.get_close_matches(compact, alias_compacts, n=1, cutoff=0.6)
  if fuzzy:
    return "Finale Underway"

  if "underway" not in compact:
    return None

  climax_markers = ("climax", "clim", "clinar", "finale")
  race_markers = ("race", "races", "pace", "paces", "star", "ts")
  if any(marker in compact for marker in climax_markers) and any(marker in compact for marker in race_markers):
    return "Finale Underway"

  return None

def _inventory_template_debug_entry(field, template_path, result=None, extra=None):
  result = result or {}
  entry = {
    "field": field,
    "source_type": "template_match",
    "template": template_path,
    "template_image_path": template_path,
  }
  if isinstance(result, dict):
    if result.get("score") is not None:
      entry["best_match_score"] = result.get("score")
    if result.get("threshold") is not None:
      entry["threshold"] = result.get("threshold")
    if result.get("passed_threshold") is not None:
      entry["passed_threshold"] = result.get("passed_threshold")
    if result.get("matched") is not None:
      entry["matched"] = result.get("matched")
    if result.get("key") is not None:
      entry["template_key"] = result.get("key")
    if result.get("location") is not None:
      entry["match_location"] = result.get("location")
    if result.get("size") is not None:
      entry["match_size"] = result.get("size")
    if result.get("click_target") is not None:
      entry["click_target"] = result.get("click_target")
    if result.get("search_image_path"):
      entry["search_image_path"] = result.get("search_image_path")
    if result.get("region_ltrb") is not None:
      entry["bbox_xyxy"] = result.get("region_ltrb")
  if extra:
    entry.update(extra)
  return entry

def _build_trackblazer_inventory_debug_entries(flow, controls, inventory):
  entries = []

  for idx, check in enumerate(flow.get("precheck") or []):
    template_path = check.get("template")
    if template_path:
      entries.append(_inventory_template_debug_entry(f"inventory_precheck_{idx}", template_path, check))

  lobby_button = flow.get("lobby_open_button") or {}
  if lobby_button.get("template"):
    entries.append(
      _inventory_template_debug_entry(
        "inventory_open_button",
        lobby_button.get("template"),
        lobby_button,
        extra={
          "match_rect": lobby_button.get("match"),
          "search_image_path": lobby_button.get("search_image_path"),
        },
      )
    )

  open_result = flow.get("open_result") or {}
  open_button = open_result.get("button") or {}
  if open_button.get("template") and not lobby_button.get("template"):
    entries.append(
      _inventory_template_debug_entry(
        "inventory_open_button",
        open_button.get("template"),
        open_button,
        extra={
          "match_rect": open_button.get("match"),
          "search_image_path": open_button.get("search_image_path"),
        },
      )
    )
  for idx, check in enumerate(open_result.get("verification_checks") or []):
    template_path = check.get("template")
    if template_path:
      entries.append(_inventory_template_debug_entry(f"inventory_open_verify_{idx}", template_path, check))

  for key in ("close", "confirm_use"):
    control_entry = controls.get(key) or {}
    template_path = control_entry.get("template")
    if template_path:
      entries.append(_inventory_template_debug_entry(f"inventory_controls_{key}", template_path, control_entry))

  for key, control_entry in (controls.get("confirm_candidates") or {}).items():
    template_path = (control_entry or {}).get("template")
    if template_path:
      entries.append(_inventory_template_debug_entry(f"inventory_confirm_candidate_{key}", template_path, control_entry))

  for item_name, item_data in (inventory or {}).items():
    if item_name == "_timing":
      continue
    template_path = constants.TRACKBLAZER_ITEM_TEMPLATES.get(item_name)
    if not template_path:
      continue
    entries.append(
      _inventory_template_debug_entry(
        f"inventory_item_{item_name}",
        template_path,
        {
          "threshold": item_data.get("threshold"),
          "passed_threshold": bool(item_data.get("detected")),
          "matched": bool(item_data.get("detected")),
        },
        extra={
          "parsed_value": item_data.get("match_count", 0),
          "match_count": item_data.get("match_count", 0),
          "matches": item_data.get("matches"),
          "search_image_path": item_data.get("search_image_path"),
          "held_quantity": item_data.get("held_quantity"),
          "increment_match": item_data.get("increment_match"),
          "increment_target": item_data.get("increment_target"),
        },
      )
    )

  increment_template_path = constants.TRACKBLAZER_SHOP_UI_TEMPLATES.get("shop_aftersale_confirm_use_increment_item")
  for attempt in flow.get("increment_attempts") or []:
    if not increment_template_path:
      break
    item_name = attempt.get("item_name") or "unknown"
    entries.append(
      _inventory_template_debug_entry(
        f"inventory_increment_{item_name}",
        increment_template_path,
        {
          "matched": bool(attempt.get("increment_target")),
          "passed_threshold": bool(attempt.get("increment_target")),
          "click_target": attempt.get("increment_target"),
        },
        extra={
          "increment_match": attempt.get("increment_match"),
          "row_center_y": attempt.get("row_center_y"),
          "held_quantity": attempt.get("held_quantity"),
          "clicked": attempt.get("clicked"),
          "click_metrics": attempt.get("click_metrics"),
        },
      )
    )

  close_result = flow.get("close_result") or {}
  for idx, check in enumerate(close_result.get("attempts") or []):
    template_path = check.get("template")
    if template_path:
      entries.append(_inventory_template_debug_entry(f"inventory_close_attempt_{idx}", template_path, check))
  for idx, check in enumerate(close_result.get("verification_checks") or []):
    template_path = check.get("template")
    if template_path:
      entries.append(_inventory_template_debug_entry(f"inventory_close_verify_{idx}", template_path, check))

  deduped = []
  seen = set()
  for entry in entries:
    dedupe_key = (entry.get("field"), entry.get("template"))
    if dedupe_key in seen:
      continue
    seen.add(dedupe_key)
    deduped.append(entry)
  return deduped


def _build_trackblazer_shop_debug_entries(flow, ocr_runtime_debug=None):
  entries = []

  entry_result = flow.get("entry_result") or {}
  shop_check = entry_result.get("shop_check") or {}
  for method_name, method in (shop_check.get("methods") or {}).items():
    for key, check in ((method or {}).get("checks") or {}).items():
      template_path = (check or {}).get("template")
      if not template_path:
        continue
      entries.append(
        _inventory_template_debug_entry(
          f"shop_entry_{method_name}_{key}",
          template_path,
          check,
        )
      )

  for idx, check in enumerate(entry_result.get("verification_checks") or []):
    template_path = check.get("template")
    if template_path:
      entries.append(_inventory_template_debug_entry(f"shop_entry_verify_{idx}", template_path, check))

  scan_result = flow.get("scan_result") or {}
  # Collect last non-empty search_image_path per item across all pages so
  # rows from drag frames (save_debug_image=False) can inherit a valid path.
  _item_search_image = {}
  for page in scan_result.get("pages") or []:
    for row in page.get("rows") or []:
      path = row.get("search_image_path")
      if path:
        _item_search_image[row.get("item_name")] = path
  for page in scan_result.get("pages") or []:
    page_index = page.get("page_index", 0)
    for row in page.get("rows") or []:
      item_name = row.get("item_name")
      template_path = constants.TRACKBLAZER_ITEM_TEMPLATES.get(item_name)
      if not template_path:
        continue
      entries.append(
        _inventory_template_debug_entry(
          f"shop_item_page_{page_index}_{item_name}",
          template_path,
          {
            "threshold": row.get("threshold"),
            "passed_threshold": bool(row.get("detected")),
            "matched": bool(row.get("detected")),
          },
          extra={
            "category": row.get("category"),
            "match_rect": row.get("match"),
            "row_center_y": row.get("row_center_y"),
            "checkbox_match": row.get("checkbox_match"),
            "checkbox_target": row.get("checkbox_target"),
            "search_image_path": row.get("search_image_path") or _item_search_image.get(item_name, ""),
          },
        )
      )
    confirm_entry = page.get("confirm") or {}
    confirm_template = confirm_entry.get("template")
    if confirm_template:
      entries.append(
        _inventory_template_debug_entry(
          f"shop_confirm_page_{page_index}",
          confirm_template,
          confirm_entry,
        )
      )

  close_result = flow.get("close_result") or {}
  for idx, check in enumerate(close_result.get("attempts") or []):
    template_path = check.get("template")
    if template_path:
      entries.append(_inventory_template_debug_entry(f"shop_close_attempt_{idx}", template_path, check))
  for idx, check in enumerate(close_result.get("verification_checks") or []):
    template_path = check.get("template")
    if template_path:
      entries.append(_inventory_template_debug_entry(f"shop_close_verify_{idx}", template_path, check))

  shop_coins_debug = (ocr_runtime_debug or {}).get("trackblazer_shop_coins") or {}
  if shop_coins_debug:
    entries.append(
      {
        "field": "shop_coins_ocr",
        "source_type": "ocr",
        "search_image_path": shop_coins_debug.get("search_image_path"),
        "parsed_value": shop_coins_debug.get("parsed_value"),
        "raw_text": shop_coins_debug.get("raw_text"),
        "region_key": shop_coins_debug.get("region_key"),
        "region_xywh": shop_coins_debug.get("region_xywh"),
      }
    )

  deduped = []
  seen = set()
  for entry in entries:
    dedupe_key = (
      entry.get("field"),
      entry.get("template"),
      entry.get("search_image_path"),
    )
    if dedupe_key in seen:
      continue
    seen.add(dedupe_key)
    deduped.append(entry)
  return deduped

def clear_aptitudes_cache():
  global aptitudes_cache
  aptitudes_cache = {}

def _close_full_stats():
  attempts = [
    ("close_btn full window", "assets/buttons/close_btn.png", constants.GAME_WINDOW_BBOX),
    ("close_btn bottom", "assets/buttons/close_btn.png", constants.SCREEN_BOTTOM_BBOX),
    ("back_btn bottom", "assets/buttons/back_btn.png", constants.SCREEN_BOTTOM_BBOX),
    ("back_btn full window", "assets/buttons/back_btn.png", constants.GAME_WINDOW_BBOX),
  ]
  for label, path, region in attempts:
    if device_action.locate_and_click(path, min_search_time=get_secs(1), region_ltrb=region):
      if config.VERBOSE_ACTIONS:
        info(f"[STATE] Exited full stats via {label}.")
      return True
  if config.VERBOSE_ACTIONS:
    warning("[STATE] Failed to close full stats via close/back buttons.")
  return False


def _find_infirmary_button_match():
  screenshot = device_action.screenshot(region_ltrb=constants.SCREEN_BOTTOM_BBOX)
  infirmary_matches = device_action.match_template(
    "assets/buttons/infirmary_btn.png",
    screenshot,
    threshold=0.85,
  )
  if not infirmary_matches:
    return None
  return infirmary_matches[0]


def _is_infirmary_button_active():
  infirmary_match = _find_infirmary_button_match()
  if infirmary_match is None:
    return False
  infirmary_screen_image = device_action.screenshot_match(
    match=infirmary_match,
    region=constants.SCREEN_BOTTOM_BBOX,
  )
  return compare_brightness(
    template_path="assets/buttons/infirmary_btn.png",
    other=infirmary_screen_image,
  )


def _collect_lobby_status_effects(state_object):
  state_object["status_effect_names"] = []
  state_object["status_effect_severity"] = 0
  state_object["infirmary_available"] = False

  if not _is_infirmary_button_active():
    return

  state_object["infirmary_available"] = True
  if config.VERBOSE_ACTIONS:
    info("[STATE] Infirmary button is active; opening full stats to read status effects.")
  if not device_action.locate_and_click(
    "assets/buttons/full_stats.png",
    min_search_time=get_secs(1),
    region_ltrb=constants.SCREEN_MIDDLE_BBOX,
  ):
    warning("[STATE] Full stats button not found; skipping status-effect OCR.")
    return

  sleep(0.5)
  _status_text, status_effect_names, total_severity = read_status_effects_from_current_full_stats()
  state_object["status_effect_names"] = status_effect_names
  state_object["status_effect_severity"] = total_severity

  closed = _close_full_stats()
  if config.VERBOSE_ACTIONS:
    info(
      f"[STATE] Status effects detected: {status_effect_names} "
      f"(severity={total_severity}, closed={closed})"
    )

def collect_main_state():
  global aptitudes_cache
  clear_runtime_ocr_debug()
  debug("Start state collection. Collecting stats.")
  #??? minimum_mood_junior_year = constants.MOOD_LIST.index(config.MINIMUM_MOOD_JUNIOR_YEAR)

  state_object = CleanDefaultDict()
  if constants.SCENARIO_NAME in ("mant", "trackblazer"):
    try:
      from scenarios.trackblazer import detect_inventory_screen, close_training_items_inventory

      inventory_open, inventory_entry, inventory_checks = detect_inventory_screen()
      if inventory_open:
        info("[TB_INV] Inventory screen detected during main-state collection; attempting recovery close.")
        close_result = close_training_items_inventory()
        state_object["trackblazer_inventory_recovery"] = {
          "trigger": "collect_main_state",
          "inventory_open": True,
          "detected_by": inventory_entry,
          "detection_checks": inventory_checks,
          "close_result": close_result,
          "closed": bool(close_result.get("closed")),
        }
        if not close_result.get("closed"):
          warning("[TB_INV] Recovery close did not dismiss the inventory before lobby state collection.")
      else:
        state_object["trackblazer_inventory_recovery"] = {
          "trigger": "collect_main_state",
          "inventory_open": False,
          "detected_by": inventory_entry,
          "detection_checks": inventory_checks,
          "closed": False,
        }
    except Exception as exc:
      warning(f"[TB_INV] Inventory recovery check failed during main-state collection: {exc}")

  state_object["current_mood"] = get_mood()
  debug("Mood collection done.")
  mood_index = constants.MOOD_LIST.index(state_object["current_mood"])
  minimum_mood_index = constants.MOOD_LIST.index(config.MINIMUM_MOOD)
  minimum_mood_junior_year_index = constants.MOOD_LIST.index(config.MINIMUM_MOOD_JUNIOR_YEAR)
  state_object["mood_difference"] = mood_index - minimum_mood_index
  state_object["mood_difference_junior_year"] = mood_index - minimum_mood_junior_year_index
  debug("Before turn collection.")
  state_object["turn"] = get_turn()
  debug("Before year collection.")
  state_object["year"] = get_current_year()
  if constants.SCENARIO_NAME in ("mant", "trackblazer") and state_object["year"] == "Finale Underway":
    try:
      from scenarios.trackblazer import check_climax_locked_race_button
      climax_locked = bool(check_climax_locked_race_button())
    except Exception as exc:
      warning(f"[TB_CLIMAX] Locked-race check failed during main-state collection: {exc}")
      climax_locked = False
    state_object["trackblazer_climax"] = True
    state_object["trackblazer_climax_locked_race"] = climax_locked
    state_object["trackblazer_trainings_remaining_upper_bound"] = 3
    if state_object["turn"] == -1:
      state_object["turn"] = "Climax Training" if climax_locked else "Finale Turn"
  else:
    state_object["trackblazer_climax"] = False
    state_object["trackblazer_climax_locked_race"] = False
  debug("Before criteria collection.")
  state_object["criteria"] = get_criteria()
  debug("Before current stats collection.")
  state_object["current_stats"] = get_current_stats(state_object["turn"])
  energy_level, max_energy = get_energy_level()
  state_object["energy_level"] = energy_level
  state_object["max_energy"] = max_energy
  _collect_lobby_status_effects(state_object)

  #find a better way to do this
  if device_action.locate("assets/ui/recreation_with.png"):
    state_object["date_event_available"] = True
  else:
    state_object["date_event_available"] = False

  if config.DO_MISSION_RACES_IF_POSSIBLE:
    if device_action.locate("assets/icons/race_mission_icon.png", region_ltrb=constants.SCREEN_BOTTOM_BBOX):
      state_object["race_mission_available"] = True
  if config.SKIP_FULL_STATS_APTITUDE_CHECK:
    if config.VERBOSE_ACTIONS:
      info("[STATE] Skipping full stats aptitude check by config.")
    state_object["aptitudes"] = dict(aptitudes_cache) if aptitudes_cache else {}
    state_object["aptitudes_missing_keys"] = [
      key for key in APTITUDE_BOX_RATIOS.keys()
      if key not in state_object["aptitudes"]
    ]
    filter_race_list(state_object)
    filter_race_schedule(state_object)
  # first init or inspiration.
  elif aptitudes_cache and "Early Apr" not in state_object["year"]:
    if config.VERBOSE_ACTIONS:
      info("[STATE] Using cached aptitudes; skipping full stats.")
    state_object["aptitudes"] = aptitudes_cache
    filter_race_list(state_object)
    filter_race_schedule(state_object)
  else:
    # Aptitudes are behind full stats button.
    if config.VERBOSE_ACTIONS:
      info("[STATE] Opening full stats to read aptitudes.")
    if device_action.locate_and_click("assets/buttons/full_stats.png", min_search_time=get_secs(1)):
      sleep(1)
      if config.VERBOSE_ACTIONS:
        info("[STATE] Full stats opened; reading aptitudes.")
      state_object["aptitudes"] = get_aptitudes()
      state_object["aptitudes_missing_keys"] = [
        key for key in APTITUDE_BOX_RATIOS.keys()
        if key not in state_object["aptitudes"]
      ]
      aptitudes_cache = state_object["aptitudes"]
      filter_race_list(state_object)
      filter_race_schedule(state_object)
      closed = _close_full_stats()
      if config.VERBOSE_ACTIONS:
        info(f"[STATE] Closing full stats result: {closed}")
        training_btn = device_action.locate("assets/buttons/training_btn.png", min_search_time=get_secs(1), region_ltrb=constants.SCREEN_BOTTOM_BBOX)
        if training_btn:
          info("[STATE] Training button visible after closing full stats.")
        else:
          warning("[STATE] Training button not visible after closing full stats.")
    elif config.VERBOSE_ACTIONS:
      warning("[STATE] Full stats button not found; skipping aptitudes.")
  state_object["ocr_runtime_debug"] = snapshot_runtime_ocr_debug()
  debug(f"Main state collection done.")
  return state_object


def collect_trackblazer_inventory(state_object, allow_open_non_execute=False, trigger="automatic"):
  """Open, scan, and close the Trackblazer training items inventory."""
  from scenarios.trackblazer import (
    scan_training_items_inventory,
    build_inventory_summary,
    open_training_items_inventory,
    close_training_items_inventory,
    detect_inventory_screen,
    detect_inventory_controls,
    detect_training_items_button,
  )

  if constants.SCENARIO_NAME not in ("mant", "trackblazer") and trigger != "manual_console":
    debug("[STATE] Skipping Trackblazer inventory scan — wrong scenario.")
    return state_object

  clear_runtime_ocr_debug()

  inventory = CleanDefaultDict()
  controls = {}
  summary = {
    "items_detected": [],
    "by_category": {},
    "total_detected": 0,
    "actionable_items": [],
  }
  flow = {
    "trigger": trigger,
    "execution_intent": bot.get_execution_intent(),
    "allow_open_non_execute": bool(allow_open_non_execute),
    "opened": False,
    "already_open": False,
    "closed": False,
    "skipped": False,
    "reason": "",
    "open_result": None,
    "close_result": None,
    "action_log": [],
  }

  t_flow_start = time.time()

  inventory_screen_open, _, precheck_entries = detect_inventory_screen()
  flow["precheck"] = precheck_entries
  lobby_open_button = detect_training_items_button()
  flow["lobby_open_button"] = lobby_open_button
  flow["use_training_items_button_visible"] = bool(
    lobby_open_button and lobby_open_button.get("matched") and lobby_open_button.get("click_target")
  )
  flow["use_training_items_button_match"] = (
    list(lobby_open_button.get("match"))
    if lobby_open_button and lobby_open_button.get("match")
    else None
  )

  if inventory_screen_open:
    flow["opened"] = True
    flow["already_open"] = True
  elif not flow["use_training_items_button_visible"]:
    flow["skipped"] = True
    flow["reason"] = "inventory_button_not_visible"
    debug("[STATE] Trackblazer inventory button is not visible on the lobby.")
  elif bot.get_execution_intent() != "execute" and not allow_open_non_execute:
    flow["skipped"] = True
    flow["reason"] = "inventory_open_requires_execute_intent"
    debug("[STATE] Skipping Trackblazer inventory open in non-execute intent.")
  else:
    debug("[STATE] Opening Trackblazer training items inventory.")
    t0 = time.time()
    open_result = open_training_items_inventory(skip_precheck=True)
    flow["timing_open"] = round(time.time() - t0, 3)
    flow["open_result"] = open_result
    flow["action_log"].extend(list(open_result.get("action_log") or []))
    flow["opened"] = bool(open_result.get("opened"))
    flow["already_open"] = bool(open_result.get("already_open"))
    if not flow["opened"]:
      flow["reason"] = "failed_to_open_inventory"
      warning("[STATE] Failed to open Trackblazer inventory.")

  if flow["opened"]:
    debug("[STATE] Scanning Trackblazer training items inventory.")
    t0 = time.time()
    inventory = scan_training_items_inventory()
    flow["timing_scan"] = round(time.time() - t0, 3)
    flow["scan_timing"] = inventory.pop("_timing", None)
    scan_scroll = (flow.get("scan_timing") or {}).get("scroll") or {}
    for key in ("reset_swipe", "forward_swipe", "fallback_swipe", "recovery_swipe"):
      action = scan_scroll.get(key)
      if action and action.get("start") and action.get("end"):
        flow["action_log"].append({
          "step": key,
          "type": action.get("type") or "swipe",
          "start": action.get("start"),
          "end": action.get("end"),
          "performed": bool(action.get("swiped")),
          "why": action.get("why") or "",
          "source": "scan_training_items_inventory",
        })
    t0 = time.time()
    controls = detect_inventory_controls()
    flow["timing_controls"] = round(time.time() - t0, 3)
    summary = build_inventory_summary(inventory)

    if flow["already_open"]:
      flow["closed"] = False
      flow["reason"] = flow["reason"] or "inventory_was_already_open"
    else:
      t0 = time.time()
      close_result = close_training_items_inventory()
      flow["timing_close"] = round(time.time() - t0, 3)
      flow["close_result"] = close_result
      flow["action_log"].extend(list(close_result.get("action_log") or []))
      flow["closed"] = bool(close_result.get("closed"))
      if not flow["closed"]:
        warning("[STATE] Inventory scan completed but close-backout failed.")
        flow["reason"] = flow["reason"] or "failed_to_close_inventory"

  flow["timing_total"] = round(time.time() - t_flow_start, 3)
  info(f"[STATE] Trackblazer inventory flow timing: {flow.get('timing_total', '?')}s "
       f"(open={flow.get('timing_open', '-')} scan={flow.get('timing_scan', '-')} "
       f"controls={flow.get('timing_controls', '-')} close={flow.get('timing_close', '-')})")

  # Always record the flow so callers can inspect skip/open/close state.
  state_object["trackblazer_inventory_flow"] = flow

  # Only overwrite inventory data when the scan was actually performed.
  # If the scan was skipped (e.g. post-shop refresh couldn't find the
  # inventory button), preserve the prior scan's data so downstream
  # consumers like plan_item_usage still see the held items.
  if flow["opened"]:
    state_object["trackblazer_inventory"] = inventory
    state_object["trackblazer_inventory_controls"] = controls
    state_object["trackblazer_inventory_summary"] = summary
  else:
    prior_summary = state_object.get("trackblazer_inventory_summary")
    prior_detected = list((prior_summary or {}).get("items_detected") or [])
    prior_held = dict((prior_summary or {}).get("held_quantities") or {})
    if prior_summary and (prior_detected or prior_held):
      warning(
        f"[STATE] Trackblazer inventory scan skipped (trigger={trigger}, "
        f"reason={flow.get('reason') or 'unknown'}); "
        f"preserving prior scan with {len(prior_detected)} detected items "
        f"and {len(prior_held)} held-quantity entries."
      )
    else:
      # First scan on this turn — write the empty defaults so keys exist.
      state_object["trackblazer_inventory"] = inventory
      state_object["trackblazer_inventory_controls"] = controls
      state_object["trackblazer_inventory_summary"] = summary

  detected = summary.get("items_detected", []) if flow["opened"] else (
    (state_object.get("trackblazer_inventory_summary") or {}).get("items_detected", [])
  )
  summary_to_update = state_object.get("trackblazer_inventory_summary") or summary
  summary_to_update["inventory_button_visible"] = flow.get("use_training_items_button_visible", False)
  if detected:
    info(f"[STATE] Trackblazer items detected: {detected}")
  else:
    debug("[STATE] No Trackblazer items detected in inventory scan.")

  active_summary = state_object.get("trackblazer_inventory_summary") or summary
  record_runtime_ocr_debug(
    "trackblazer_inventory",
    extra={
      "region_key": "MANT_INVENTORY_ITEMS_REGION",
      "region_xywh": list(constants.MANT_INVENTORY_ITEMS_REGION),
      "items_detected": detected,
      "total_detected": active_summary.get("total_detected", 0),
      "by_category": active_summary.get("by_category", {}),
      "controls": state_object.get("trackblazer_inventory_controls") or controls,
      "flow": flow,
    },
  )
  state_object["ocr_runtime_debug"] = snapshot_runtime_ocr_debug()
  state_object["inventory_ocr_debug_entries"] = _build_trackblazer_inventory_debug_entries(flow, controls, inventory)

  return state_object


_ENERGY_TRAINING_NAMES = ("spd", "sta", "pwr", "guts")
_FAILURE_OUTLIER_THRESHOLD = 20  # percentage-point gap from median to flag


def _correct_failure_outliers(training_results):
  """Replace failure-rate outliers among energy-consuming trainings.

  The four energy trainings (spd/sta/pwr/guts) normally have similar failure
  rates.  Wit is excluded because it legitimately reads much lower.  When
  exactly one of the four reads drastically lower than the median of the
  others it is almost certainly an OCR misread, so we replace it with the
  median and log a warning.
  """
  rates = {}
  for name in _ENERGY_TRAINING_NAMES:
    if name in training_results:
      val = training_results[name].get("failure", -1)
      if isinstance(val, (int, float)) and val >= 0:
        rates[name] = int(val)

  if len(rates) < 3:
    return training_results

  med = median(rates.values())
  if med < _FAILURE_OUTLIER_THRESHOLD:
    # All rates are low — nothing to correct (early-game or low-energy usage).
    return training_results

  outliers = {
    name: val for name, val in rates.items()
    if med - val >= _FAILURE_OUTLIER_THRESHOLD
  }
  if len(outliers) == 1:
    name, bad_val = next(iter(outliers.items()))
    corrected = int(med)
    warning(
      f"[STATE] Failure OCR outlier corrected: {name} {bad_val}% → {corrected}% "
      f"(median of energy trainings: {corrected}%, rates: {rates})"
    )
    training_results[name]["failure"] = corrected
    training_results[name]["failure_corrected_from"] = bad_val

  return training_results


def collect_training_state(state_object, training_function_name):
  check_stat_gains = False
  if (
    training_function_name == "meta_training"
    or training_function_name == "most_stat_gain"
    or constants.SCENARIO_NAME in ("mant", "trackblazer")
  ):
    check_stat_gains = True

  if config.VERBOSE_ACTIONS:
    info(f"[STATE] Collecting training state using '{training_function_name}'.")
  bot.push_debug_history({"event": "click", "asset": "training_btn", "result": "opening", "context": "training_scan"})
  if device_action.locate_and_click("assets/buttons/training_btn.png", min_search_time=get_secs(5), region_ltrb=constants.SCREEN_BOTTOM_BBOX):
    bot.push_debug_history({"event": "click", "asset": "training_btn", "result": "opened", "context": "training_scan"})
    training_results = CleanDefaultDict()
    training_scan_debug = CleanDefaultDict()
    sleep(0.6)
    # Hold/drag across training buttons to reveal info without confirming training.
    hold_active = False
    for name, mouse_pos in constants.TRAINING_SCAN_POSITIONS.items():
      bot.push_debug_history({"event": "scan", "asset": name, "result": "scanning", "context": "training_scan"})
      if bot.is_adb_input_active():
        # Keep the original swipe behavior for ADB devices.
        device_action.swipe(mouse_pos, (mouse_pos[0], mouse_pos[1] + 150), duration=0.15)
      else:
        if not hold_active:
          # Begin the drag from neutral space so the initial mouseDown does not
          # land on Speed and accidentally confirm a training.
          pyautogui_actions.moveTo(constants.SAFE_SPACE_MOUSE_POS[0], constants.SAFE_SPACE_MOUSE_POS[1], duration=0.08)
          pyautogui_actions.hold()
          hold_active = True
        pyautogui_actions.moveTo(mouse_pos[0], mouse_pos[1], duration=0.12)

      sleep(0.5)
      device_action.flush_screenshot_cache()

      if args.debug is not None and args.debug > 11:
        from utils.debug_tools import compare_training_samples
        test_results = []
        for i in range(10):
          test_results.append(get_training_data(year=state_object["year"], check_stat_gains=check_stat_gains))
          test_results.append(get_support_card_data())
        equal, sample_info = compare_training_samples(test_results)

        if not equal:
          debug("Training samples diverged")
          debug(sample_info)
      training_results[name].update(get_training_data(year=state_object["year"], check_stat_gains=check_stat_gains))
      training_results[name].update(get_support_card_data())
      if constants.SCENARIO_NAME == "unity":
        failure_region = constants.UNITY_FAILURE_REGION
        support_region = constants.UNITY_SUPPORT_CARD_ICON_REGION
        stat_region = constants.UNITY_STAT_GAINS_REGION
      elif constants.SCENARIO_NAME in ("mant", "trackblazer"):
        failure_region = constants.MANT_FAILURE_REGION
        support_region = constants.MANT_SUPPORT_CARD_ICON_REGION
        stat_region = constants.MANT_STAT_GAINS_REGION
      else:
        failure_region = constants.FAILURE_REGION
        support_region = constants.SUPPORT_CARD_ICON_REGION
        stat_region = constants.URA_STAT_GAINS_REGION

      training_scan_debug[name]["failure"] = _save_training_scan_debug_image(
        device_action.screenshot(region_xywh=failure_region),
        name,
        "failure",
      )
      training_scan_debug[name]["support_icons"] = _save_training_scan_debug_image(
        device_action.screenshot(region_xywh=support_region),
        name,
        "support_icons",
      )
      training_scan_debug[name]["stat_gains"] = _save_training_scan_debug_image(
        device_action.screenshot(region_xywh=stat_region),
        name,
        "stat_gains",
      )

    if hold_active:
      pyautogui_actions.moveTo(constants.SAFE_SPACE_MOUSE_POS[0], constants.SAFE_SPACE_MOUSE_POS[1], duration=0.1)
      pyautogui_actions.release()

    debug(f"Training results: {training_results}")

    training_results = _correct_failure_outliers(training_results)
    training_results = filter_training_lock(training_results)
    if config.VERBOSE_ACTIONS:
      info(f"[STATE] Training scan complete. Options: {list(training_results.keys())}")
    if not training_results:
      warning(
        "[STATE] Training scan returned no usable results. "
        "This may indicate training hover/positions or OCR regions are misaligned."
      )
    bot.push_debug_history({"event": "click", "asset": "back_btn", "result": "closing", "context": "training_scan"})
    device_action.locate_and_click("assets/buttons/back_btn.png", min_search_time=get_secs(1), region_ltrb=constants.SCREEN_BOTTOM_BBOX)
    bot.push_debug_history({"event": "click", "asset": "back_btn", "result": "closed", "context": "training_scan"})
    state_object["training_results"] = training_results
    state_object["training_scan_debug"] = training_scan_debug

  debug(f"State object: {state_object}")
  return state_object

def filter_training_lock(training_results):
  values = list(training_results.values())
  fingerprints = [training_fingerprint(v) for v in values]

  training_locked = all(fp == fingerprints[0] for fp in fingerprints)

  debug(f"Training locked: {training_locked}")

  if training_locked:
    for name, training in list(training_results.items()):
      if not is_valid_training(name, training):
        training_results.pop(name)

    debug(f"Training results after removal: {training_results}")

  return training_results

def training_fingerprint(training):
  fp = []

  # totals (sorted for determinism)
  if "total_supports" in training:
    fp.append(("total_supports", training["total_supports"]))

  if "total_friendship_levels" in training:
    tfl = training["total_friendship_levels"]
    fp.append((
      "total_friendship_levels",
      tuple(sorted(tfl.items()))
    ))

  # per-stat entries
  for stat, data in training.items():
    if stat not in ("spd", "pwr", "sta", "guts", "wit"):
      continue
    if not isinstance(data, dict):
      continue

    entry = []

    if "supports" in data:
      entry.append(("supports", data["supports"]))

    if "friendship_levels" in data:
      entry.append((
        "friendship_levels",
        tuple(sorted(data["friendship_levels"].items()))
      ))

    if entry:
      fp.append((stat, tuple(entry)))

  # final canonical form
  return tuple(sorted(fp))

valid_training_dict={
  'spd': {'stat_gains': {'spd': 1, 'pwr': 1, 'sp': 1}},
  'sta': {'stat_gains': {'sta': 1, 'guts': 1, 'sp': 1}},
  'pwr': {'stat_gains': {'sta': 1, 'pwr': 1, 'sp': 1}},
  'guts': {'stat_gains': {'spd': 1, 'pwr': 1, 'guts': 1, 'sp': 1}},
  'wit': {'stat_gains': {'spd': 1, 'wit': 1, 'sp': 1}}}

def is_valid_training(name, training):
  if name not in valid_training_dict:
    return False

  valid_keys = set(valid_training_dict[name]["stat_gains"].keys())
  training_keys = set(training["stat_gains"].keys())

  return training_keys == valid_keys

def get_support_card_data(threshold=0.8):
  count_result = CleanDefaultDict()
  if constants.SCENARIO_NAME == "unity":
    region_xywh = constants.UNITY_SUPPORT_CARD_ICON_REGION
  elif constants.SCENARIO_NAME in ("mant", "trackblazer"):
    # Accept both names while Trackblazer remains the canonical scenario key.
    region_xywh = constants.MANT_SUPPORT_CARD_ICON_REGION
  else:
    region_xywh = constants.SUPPORT_CARD_ICON_REGION
  screenshot = device_action.screenshot(region_xywh=region_xywh)

  if constants.SCENARIO_NAME == "unity":
    unity_training_matches = device_action.match_template("assets/unity/unity_training.png", screenshot, threshold)
    unity_gauge_matches = device_action.match_template("assets/unity/unity_gauge_unfilled.png", screenshot, threshold)
    unity_spirit_exp_matches = device_action.match_template("assets/unity/unity_spirit_explosion.png", screenshot, threshold)

    for training_match in unity_training_matches:
      count_result["unity_trainings"] += 1
      for gauge_match in unity_gauge_matches:
        dist = gauge_match[1] - training_match[1]
        if dist < 100 and dist > 0:
          count_result["unity_gauge_fills"] += 1
          # each unity training can only be matched to one gauge fill, so break
          break

    for spirit_exp_match in unity_spirit_exp_matches:
      count_result["unity_spirit_explosions"] += 1

  hint_matches = device_action.match_template("assets/icons/support_hint.png", screenshot, threshold)

  for key, icon_path in constants.SUPPORT_ICONS.items():
    matches = device_action.match_template(icon_path, screenshot, threshold)

    for match in matches:
      # auto-created entries if not yet present
      debug(f"{key} match: {match}")
      count_result[key]["supports"] += 1
      count_result["total_supports"] += 1

      # get friend level
      x, y, w, h = match
      icon_to_friend_bar_distance = 77 if constants.SCENARIO_NAME in ("mant", "trackblazer") else 66
      bbox_left = region_xywh[0] + x + w // 2
      bbox_top = region_xywh[1] + y + h // 2 + icon_to_friend_bar_distance
      wanted_pixel = (bbox_left, bbox_top, bbox_left + 1, bbox_top + 1)

      friendship_level_color = find_color_of_pixel(wanted_pixel)
      friend_level = closest_color(constants.SUPPORT_FRIEND_LEVELS, friendship_level_color)

      count_result[key]["friendship_levels"][friend_level] += 1
      count_result["total_friendship_levels"][friend_level] += 1

      for hint_match in hint_matches:
        if abs(hint_match[1] - match[1]) < 45:
          count_result[key]["hints"] += 1
          count_result["total_hints"] += 1
          count_result["hints_per_friend_level"][friend_level] += 1

  return count_result

def get_training_data(year=None, check_stat_gains = False):
  apply_configured_recognition_geometry()
  results = {}

  if constants.SCENARIO_NAME == "unity":
    results["failure"] = get_failure_chance(region_xywh=constants.UNITY_FAILURE_REGION)
    if check_stat_gains:
      stat_gains = get_stat_gains(year=year, region_xywh=constants.UNITY_STAT_GAINS_REGION, scale_factor=1.5)
      stat_gains2 = get_stat_gains(year=year, region_xywh=constants.UNITY_STAT_GAINS_2_REGION, scale_factor=1.5, secondary_stat_gains=True)
      for key, value in stat_gains.items():
        if key in stat_gains2:
          stat_gains[key] += stat_gains2[key]
      results["stat_gains"] = stat_gains
  elif constants.SCENARIO_NAME in ("mant", "trackblazer"):
    results["failure"] = get_failure_chance(region_xywh=constants.MANT_FAILURE_REGION)
    if check_stat_gains:
      results["stat_gains"] = get_stat_gains(year=year, region_xywh=constants.MANT_STAT_GAINS_REGION)
  else:
    results["failure"] = get_failure_chance(region_xywh=constants.FAILURE_REGION)
    if check_stat_gains:
      results["stat_gains"] = get_stat_gains(year=year, region_xywh=constants.URA_STAT_GAINS_REGION)

  return results

def get_stat_gains(year=1, attempts=0, enable_debug=True, show_screenshot=False, region_xywh=None, scale_factor=1, secondary_stat_gains=False):
  if region_xywh is None:
    raise ValueError("region_xywh is required")

  apply_configured_recognition_geometry()
  
  stat_gains={}
  #[220, 100, 60], [255, 245, 170]

  if secondary_stat_gains:
    upper_yellow = [255, 245, 170]
    lower_yellow = [220, 100, 45]
  else:
    upper_yellow = [255, 245, 170]
    lower_yellow = [220, 100, 60]
  stat_screenshots = []
  for i in range(1):
    if i > 0:
      device_action.flush_screenshot_cache()
    stat_screenshot = device_action.screenshot(region_xywh=region_xywh)
    if secondary_stat_gains:
      mask_area=1
    else:
      mask_area=2
    stat_screenshot = custom_grabcut(stat_screenshot, mask_area=mask_area)
    if enable_debug:
      debug_window(stat_screenshot, save_name="grabcut")
    if scale_factor != 1:
      stat_screenshot = cv2.resize(stat_screenshot, (int(stat_screenshot.shape[1] * scale_factor), int(stat_screenshot.shape[0] * scale_factor)))
    stat_screenshot = np.invert(binarize_between_colors(stat_screenshot, lower_yellow, upper_yellow))
    if enable_debug:
      debug_window(stat_screenshot, save_name="binarized")
    # if screenshot is 95% black or white
    mean = np.mean(stat_screenshot)
    if mean > 253 or mean < 2:
      debug(f"Empty screenshot, skipping. Mean: {mean}")
      return {}
    stat_screenshots.append(stat_screenshot)
    if enable_debug:
      debug_window(stat_screenshot, save_name=f"stat_screenshot_{i}_{year}", show_on_screen=show_screenshot)
    sleep(0.15)
  
  # find black pixels that do not change between the three screenshots
  diff = stat_screenshots[0]
  for i in range(1, len(stat_screenshots)):
    diff = diff & stat_screenshots[i]
  if enable_debug:
    debug_window(diff, save_name="stat_gains_diff")

  stat_screenshot = diff
  boxes = {
    "spd":  (0.000, 0.00, 0.166, 1),
    "sta":  (0.167, 0.00, 0.166, 1),
    "pwr":  (0.334, 0.00, 0.166, 1),
    "guts": (0.500, 0.00, 0.166, 1),
    "wit":  (0.667, 0.00, 0.166, 1),
    "sp":   (0.834, 0.00, 0.166, 1),
  }

  h, w = stat_screenshot.shape
  stat_gains={}
  for key, (xr, yr, wr, hr) in boxes.items():
    x, y, ww, hh = int(xr*w), int(yr*h), int(wr*w), int(hr*h)
    cropped_image = np.array(stat_screenshot[y:y+hh, x:x+ww])
    if enable_debug:
      debug_window(cropped_image, save_name=f"stat_{key}", show_on_screen=show_screenshot)
    if secondary_stat_gains:
      cropped_image = crop_after_plus_component(cropped_image, plus_length=12, bar_width=0)
    else:
      cropped_image = crop_after_plus_component(cropped_image)
    if np.all(cropped_image == 0):
      continue
    if enable_debug:
      debug_window(cropped_image, save_name=f"stat_{key}_cropped_{year}", show_on_screen=show_screenshot)
    cropped_image = clean_noise(cropped_image)
    if enable_debug:
      debug_window(cropped_image, save_name=f"stat_{key}_cleaned_{year}", show_on_screen=show_screenshot)
    text = extract_number(cropped_image)

    if text != -1:
      if enable_debug:
        debug_window(cropped_image, save_name=f"{text}_stat_{key}_gain_screenshot_{year}", show_on_screen=show_screenshot)
      stat_gains[key] = text

  if attempts >= 10:
    if enable_debug:
      debug(f"[STAT_GAINS] {year} Extraction failed. Gains: {stat_gains}")
    return stat_gains
  elif any(value > 100 for value in stat_gains.values()):
    if enable_debug:
      debug(f"[STAT_GAINS] {year} Too high, retrying. Gains: {stat_gains}")
    return get_stat_gains(year=year, attempts=attempts + 1, enable_debug=enable_debug, show_screenshot=show_screenshot, region_xywh=region_xywh)
  debug(f"[STAT_GAINS] {year} Gains: {stat_gains}")
  return stat_gains


def _extract_failure_ocr_value(image, allowlist="0123456789", thresholds=None, min_confidence=0.3):
  enhanced = enhance_image_for_ocr(image, resize_factor=4, binarize_threshold=None)
  threshold_values = thresholds or [0.7, 0.6, 0.5, 0.4, 0.3, 0.2]

  if "%" in allowlist:
    for threshold in threshold_values:
      text = extract_text(enhanced, allowlist=allowlist, threshold=threshold)
      matches = re.findall(r"\d{1,3}", text or "")
      for match in matches:
        value = int(match)
        if 0 <= value <= 100:
          return value
    return -1

  img_np = np.array(enhanced)
  for threshold in threshold_values:
    result = reader.readtext(img_np, allowlist=allowlist, text_threshold=threshold)
    # Filter by recognition confidence to discard ghost detections from
    # button edges / background noise that EasyOCR's text detector picks up
    # but the recogniser scores very low.
    confident = [item for item in result if item[2] >= min_confidence]
    if not confident:
      continue
    texts = [item[1] for item in sorted(confident, key=lambda x: x[0][0][0])]
    digits = re.sub(r"[^\d]", "", "".join(texts))
    if digits:
      value = int(digits)
      if 0 <= value <= 100:
        return value
  return -1


def get_trackblazer_shop_coins():
  """Read the current Trackblazer shop currency from the shop screen.

  Canonical source for now is the in-shop coin display at
  ``MANT_SHOP_COIN_REGION``. TODO: add the lobby coin display as a secondary
  source once that region/flow is wired.
  """
  if constants.SCENARIO_NAME not in ("mant", "trackblazer"):
    return -1

  region_xywh = constants.MANT_SHOP_COIN_REGION
  max_attempts = 2
  for attempt in range(max_attempts):
    screenshot = device_action.screenshot(region_xywh=region_xywh)
    record_runtime_ocr_debug(
      "trackblazer_shop_coins",
      image=screenshot,
      extra={
        "region_key": "MANT_SHOP_COIN_REGION",
        "region_xywh": list(region_xywh),
        "attempt": attempt + 1,
      },
    )

    pil = Image.fromarray(screenshot)
    thresholds = [None, 220, 200, 180]
    best_text = ""
    best_digits = ""
    for binarize_threshold in thresholds:
      enhanced = enhance_image_for_ocr(pil, resize_factor=4, binarize_threshold=binarize_threshold)
      text = extract_text(enhanced, allowlist="0123456789,", threshold=0.6)
      digits = re.sub(r"[^\d]", "", text or "")
      if len(digits) > len(best_digits):
        best_text = text or ""
        best_digits = digits
      if digits:
        break

    coins = int(best_digits) if best_digits else -1
    record_runtime_ocr_debug(
      "trackblazer_shop_coins",
      extra={
        "raw_text": best_text,
        "parsed_value": coins,
        "attempt": attempt + 1,
      },
    )
    if coins >= 0:
      return coins
    if attempt < max_attempts - 1:
      warning(f"[STATE] Shop coin OCR failed (attempt {attempt + 1}), retrying...")
  return coins


def collect_trackblazer_shop_state(state_object, trigger="automatic"):
  """Read non-destructive Trackblazer shop state from the current shop screen."""
  if constants.SCENARIO_NAME not in ("mant", "trackblazer") and trigger != "manual_console":
    debug("[STATE] Skipping Trackblazer shop state scan — wrong scenario.")
    return state_object

  shop_coins = get_trackblazer_shop_coins()
  state_object["shop_coins"] = shop_coins
  state_object["ocr_runtime_debug"] = snapshot_runtime_ocr_debug()
  return state_object


def get_failure_chance(region_xywh=None):
  if region_xywh is None:
    raise ValueError("region_xywh is required")
  apply_configured_recognition_geometry()
  screenshot = device_action.screenshot(region_xywh=region_xywh)
  record_runtime_ocr_debug("training_failure", image=screenshot)
  template_scales = [0.9, 1.0, 1.1, 1.26, 1.4]
  best_failure_match = None
  for template_path in constants.FAILURE_PERCENT_TEMPLATES:
    best_match = device_action.best_template_match(
      template_path,
      screenshot,
      grayscale=True,
      template_scales=template_scales,
    )
    if best_match is None or best_match["score"] < 0.7:
      continue
    if best_failure_match is None or best_match["score"] > best_failure_match["score"]:
      best_failure_match = dict(best_match)
      best_failure_match["template_path"] = template_path

  if best_failure_match:
    x, y = best_failure_match["location"]
    w, h = best_failure_match["size"]
    record_runtime_ocr_debug(
      "training_failure",
      extra={
        "matched_template": best_failure_match["template_path"],
        "best_match_score": round(best_failure_match["score"], 4),
        "best_match_scale": round(best_failure_match["scale"], 3),
        "best_match_location": best_failure_match["location"],
        "best_match_size": best_failure_match["size"],
      },
    )
    x = x + region_xywh[0]
    y = y + region_xywh[1]

    digit_crop_width = max(40, int(round(w * 2.8)))
    vertical_padding = max(3, int(round(h * 0.25)))
    failure_cropped = device_action.screenshot(
      region_ltrb=(
        max(0, x - digit_crop_width),
        max(0, y - vertical_padding),
        x,
        y + h + vertical_padding,
      )
    )
    failure_text = _extract_failure_ocr_value(failure_cropped)
    if failure_text != -1:
      return failure_text

    debug("Failure percent digit crop OCR failed, falling back to whole failure region OCR.")
  else:
    debug("Failed to match percent symbol, falling back to whole failure region OCR.")

  fallback_text = _extract_failure_ocr_value(screenshot, allowlist="0123456789%")
  if fallback_text != -1:
    return fallback_text

  error("Failed to read failure percentage from training region.")
  return -1

def get_mood(attempts=0):
  if attempts >= 10:
    debug("Mood determination failed after 10 attempts, returning GREAT for compatibility reasons")
    return "GREAT"

  mood_screenshot = device_action.screenshot(region_xywh=constants.MOOD_REGION) 
  matches = device_action.multi_match_templates(constants.MOOD_IMAGES, mood_screenshot, stop_after_first_match=True)
  for name, match in matches.items():
    if match:
      debug(f"Mood: {name}")
      return name

  debug(f"Mood couldn't be determined, retrying (attempt {attempts + 1}/10)")
  return get_mood(attempts + 1)

# Check turn
def get_turn():
  global _last_turn
  if device_action.locate("assets/buttons/race_day_btn.png", region_ltrb=constants.SCREEN_BOTTOM_BBOX):
    return "Race Day"
  elif device_action.locate("assets/ura/ura_race_btn.png", region_ltrb=constants.SCREEN_BOTTOM_BBOX):
    return "Race Day"
  if constants.SCENARIO_NAME == "unity":
    region_xywh = constants.UNITY_TURN_REGION
  elif constants.SCENARIO_NAME in ("mant", "trackblazer"):
    region_xywh = constants.MANT_TURN_REGION
  else:
    region_xywh = constants.TURN_REGION
  # TODO: add template-matching fallback for digits "1" and "7" when OCR fails.
  turn = device_action.screenshot(region_xywh=region_xywh)
  record_runtime_ocr_debug("turn", image=turn)
  turn = enhance_image_for_ocr(turn, resize_factor=2)
  turn_text = extract_allowed_text(turn, allowlist="0123456789")
  debug(f"Turn text: {turn_text}")

  if constants.SCENARIO_NAME == "unity":
    race_turns = device_action.screenshot(region_xywh=constants.UNITY_RACE_TURNS_REGION)
    race_turns = enhance_image_for_ocr(race_turns, resize_factor=4, binarize_threshold=None)
    race_turns_text = extract_allowed_text(race_turns, allowlist="0123456789")
    digits_only = re.sub(r"[^\d]", "", race_turns_text)
    if digits_only:
      digits_only = int(digits_only)
      debug(f"Unity cup race turns text: {race_turns_text}")
      if digits_only in [5, 10]:
        info(f"Race turns left until unity cup: {digits_only}, waiting for 3 seconds to allow banner to pass.")
        sleep(3)

  digits_only = re.sub(r"[^\d]", "", turn_text)

  if digits_only:
    _last_turn = int(digits_only)
    return _last_turn

  if constants.SCENARIO_NAME in ("mant", "trackblazer"):
    turn_1_match = device_action.locate(
      constants.TURN_1_LEFT_TEMPLATE,
      confidence=0.82,
      region_ltrb=constants.SCREEN_TOP_BBOX,
      text="Checking turn 1 template fallback after OCR miss.",
      template_scaling=1.0 / device_action.GLOBAL_TEMPLATE_SCALING,
    )
    if turn_1_match:
      warning("[STATE] Turn OCR returned no digits; matched turn 1 template fallback.")
      _last_turn = 1
      return _last_turn

  if _last_turn is not None:
    warning(f"[STATE] Turn OCR failed; using last known turn {_last_turn}.")
    return _last_turn

  return -1

# Check year
def get_current_year():
  if constants.SCENARIO_NAME == "unity":
    region_xywh = constants.UNITY_YEAR_REGION
  elif constants.SCENARIO_NAME in ("mant", "trackblazer"):
    region_xywh = constants.MANT_YEAR_REGION
  else:
    region_xywh = constants.YEAR_REGION
  for i in range(10):
    year = enhanced_screenshot(region_xywh)
    record_runtime_ocr_debug("year", image=np.array(year.convert("RGB")))
    text = extract_text(year, allowlist=constants.OCR_DATE_RECOGNITION_SET)
    text = text.replace("Pre Debut", "Pre-Debut")
    text = text.replace("Early- ", "Early ").replace("Late- ", "Late ")
    text = re.sub(r"\s+", " ", text).strip()
    debug(f"Year text: {text}")
    if text in constants.TIMELINE:
      break
    else:
      device_action.flush_screenshot_cache()

  if text not in constants.TIMELINE:
    climax_year = _normalize_trackblazer_climax_year_text(text)
    if climax_year:
      warning(f"[OCR] Year climax-normalized: '{text}' → '{climax_year}'")
      text = climax_year
    else:
      fuzzy = difflib.get_close_matches(text, constants.TIMELINE, n=1, cutoff=0.7)
      if fuzzy:
        warning(f"[OCR] Year fuzzy-matched: '{text}' → '{fuzzy[0]}'")
        text = fuzzy[0]
      else:
        warning(f"[OCR] Year unrecognized after retries: '{text}'")

  return text

# Check criteria
def get_criteria():
  if constants.SCENARIO_NAME == "unity":
    region_xywh = constants.UNITY_CRITERIA_REGION
  elif constants.SCENARIO_NAME in ("mant", "trackblazer"):
    region_xywh = constants.MANT_CRITERIA_REGION
  else:
    region_xywh = constants.CRITERIA_REGION
  img = enhanced_screenshot(region_xywh)
  record_runtime_ocr_debug("criteria", image=np.array(img.convert("RGB")))
  text = extract_text(img)
  debug(f"Criteria text: {text}")
  return text

def is_number(text):
  try:
    int(text)
    return True
  except ValueError:
    return False

def get_current_stats(turn, enable_debug=True):
  if constants.SCENARIO_NAME == "trackblazer":
    stats_region = constants.MANT_CURRENT_STATS_REGION
  else:
    stats_region = constants.CURRENT_STATS_REGION
  if turn == "Race Day":
    stats_region = (stats_region[0], stats_region[1] + 55, stats_region[2], stats_region[3])
  image = device_action.screenshot(region_xywh=stats_region)

  # Arcane numbers that divide the screen into boxes with ratios. Left, top, width, height
  if constants.SCENARIO_NAME == "trackblazer":
    # Trackblazer lobby has aptitude grade icons (D/C/E+) left of each number,
    # shifting the number positions right compared to the base layout.
    # Positions derived from blue-text column projection on the MANT region.
    # SP is read from a separate MANT_LOBBY_SKILL_PTS_REGION.
    boxes = {
      "spd":  (0.036, 0, 0.118, 1.0),
      "sta":  (0.255, 0, 0.117, 1.0),
      "pwr":  (0.468, 0, 0.117, 1.0),
      "guts": (0.683, 0, 0.115, 1.0),
      "wit":  (0.896, 0, 0.100, 1.0),
    }
  else:
    boxes = {
      "spd":  (0.0636, 0, 0.10, 0.56),
      "sta":  (0.238,  0, 0.10, 0.56),
      "pwr":  (0.4036, 0, 0.10, 0.56),
      "guts": (0.5746, 0, 0.10, 0.56),
      "wit":  (0.7436, 0, 0.10, 0.56),
      "sp":   (0.860,  0, 0.14, 0.98),
    }

  h, w = image.shape[:2]
  current_stats={}
  trackblazer_left_expand_px = max(0, int(round(w * 0.009))) if constants.SCENARIO_NAME == "trackblazer" else 0
  trackblazer_right_trim_px = trackblazer_left_expand_px
  for key, (xr, yr, wr, hr) in boxes.items():
    x, y, ww, hh = int(xr*w), int(yr*h), int(wr*w), int(hr*h)
    if trackblazer_left_expand_px:
      x = max(0, x - trackblazer_left_expand_px)
      ww = min(w - x, ww + trackblazer_left_expand_px)
    if trackblazer_right_trim_px:
      ww = max(1, ww - trackblazer_right_trim_px)
    cropped_image = np.array(image[y:y+hh, x:x+ww])
    final_stat_value = -1
    if enable_debug:
      debug_window(cropped_image, save_name=f"stat_{key}_cropped")
    final_stat_value = extract_text(cropped_image, allowlist="0123456789MAX")
    debug(f"Initial stat value: {final_stat_value}")
    if final_stat_value == "":
      cropped_image = enhance_image_for_ocr(cropped_image, binarize_threshold=None)
      final_stat_value = extract_text(cropped_image, allowlist="0123456789MAX")
      for threshold in [0.7, 0.6]:
        if final_stat_value != "":
          break
        debug(f"Couldn't recognize stat {key}, retrying with lower threshold: {threshold}")
        final_stat_value = extract_text(cropped_image, allowlist="0123456789MAX", threshold=threshold)
        debug(f"Threshold: {threshold}, stat value: {final_stat_value}")
    if final_stat_value == "MAX":
      final_stat_value = 1200
    elif is_number(final_stat_value):
      final_stat_value = int(final_stat_value)
    else:
      final_stat_value = -1
    current_stats[key] = final_stat_value
  
  # Read SP: Trackblazer has a separate Skill Pts box on the lobby
  if constants.SCENARIO_NAME == "trackblazer" and "sp" not in current_stats:
    sp_image = device_action.screenshot(region_xywh=constants.MANT_LOBBY_SKILL_PTS_REGION)
    if enable_debug:
      debug_window(sp_image, save_name="stat_sp_cropped")
    sp_value = extract_text(sp_image, allowlist="0123456789")
    if sp_value == "":
      sp_image = enhance_image_for_ocr(sp_image, binarize_threshold=None)
      sp_value = extract_text(sp_image, allowlist="0123456789")
    current_stats["sp"] = int(sp_value) if is_number(sp_value) else -1

  info(f"Current stats: {current_stats}")
  return current_stats

def get_aptitudes():
  aptitudes={}
  image = device_action.screenshot(region_xywh=constants.FULL_STATS_APTITUDE_REGION)
  record_runtime_ocr_debug("full_stats_aptitudes", image=image)
  if not device_action.locate("assets/buttons/close_btn.png", min_search_time=get_secs(2), region_ltrb=constants.GAME_WINDOW_BBOX):
    if config.VERBOSE_ACTIONS:
      warning("[APT] Close button not detected; retrying full stats screenshot.")
    sleep(0.5)
    device_action.flush_screenshot_cache()
    image = device_action.screenshot(region_xywh=constants.FULL_STATS_APTITUDE_REGION)
  h, w = image.shape[:2]
  for key, (xr, yr, wr, hr) in APTITUDE_BOX_RATIOS.items():
    x, y, ww, hh = int(xr*w), int(yr*h), int(wr*w), int(hr*h)
    # Extend crop by 2px to avoid clipping bottom/right edges due to ratio rounding
    cropped_image = np.array(image[y:min(y+hh+2, h), x:min(x+ww+2, w)])
    record_runtime_ocr_debug(f"aptitude_{key}", image=cropped_image)
    best_name, best_score = None, 0.0
    for name, path in constants.APTITUDE_IMAGES.items():
      match = device_action.best_template_match(path, cropped_image, grayscale=True)
      if match and match["score"] > best_score:
        best_score = match["score"]
        best_name = name
    if best_name and best_score >= 0.75:
      aptitudes[key] = best_name
      if config.VERBOSE_ACTIONS:
        info(f"[APT] {key} -> {best_name} (score={best_score:.3f})")
        #debug_window(cropped_image)

  missing_keys = [key for key in APTITUDE_BOX_RATIOS.keys() if key not in aptitudes]
  if missing_keys:
    warning(f"Missing aptitude OCR for: {missing_keys}")
  warning(f"Parsed aptitude values: {aptitudes}. If these values are wrong, please stop and start the bot again with the hotkey.")
  return aptitudes

def _find_bar_start_offset(strip_bgr):
  """Find the x offset where the energy bar interior begins in a 1px-high strip.

  The energy region may include non-bar pixels (e.g. "Energy" text label,
  decorations) to the left of the actual bar. This function skips those by
  scanning left-to-right for the first pixel that looks like bar content:
  either gray (empty bar ~115,115,115) or a high-saturation fill color.

  If accuracy is still off, consider tightening the energy OCR region in the
  region adjuster to exclude as much non-bar content as possible.
  """
  gray_target = 115
  gray_tolerance = 5
  hsv = cv2.cvtColor(strip_bgr, cv2.COLOR_BGR2HSV)

  for x in range(strip_bgr.shape[1]):
    bgr = strip_bgr[0, x].astype(int)
    # Gray (empty bar): all channels ~115
    if all(abs(int(c) - gray_target) <= gray_tolerance for c in bgr):
      return x
    # Saturated fill color with high brightness (bar fill).
    # Anti-aliased text edges can reach S~70, V~196 so thresholds must be
    # high enough to skip those. Actual fill has S>130, V>228.
    s, v = int(hsv[0, x, 1]), int(hsv[0, x, 2])
    if s > 80 and v > 200:
      return x

  return 0  # fallback: assume bar starts at beginning


def _expand_energy_region(region_xywh, min_height=24, vertical_padding=12):
  x, y, w, h = region_xywh
  if h >= min_height:
    return region_xywh

  game_left, game_top, game_right, game_bottom = constants.GAME_WINDOW_BBOX
  extra_height = max(min_height - h, 0)
  top_pad = vertical_padding + extra_height // 2
  bottom_pad = vertical_padding + extra_height - extra_height // 2
  new_top = max(game_top, y - top_pad)
  new_bottom = min(game_bottom, y + h + bottom_pad)
  return (x, new_top, w, max(1, new_bottom - new_top))

def get_energy_level(threshold=0.85):
  # find where the right side of the bar is on screen
  if constants.SCENARIO_NAME == "unity":
    region_xywh = constants.UNITY_ENERGY_REGION
  elif constants.SCENARIO_NAME in ("mant", "trackblazer"):
    region_xywh = constants.MANT_ENERGY_REGION
  else:
    region_xywh = constants.ENERGY_REGION
  screenshot = device_action.screenshot(region_xywh=region_xywh)
  if screenshot.shape[0] < 16:
    expanded_region = _expand_energy_region(region_xywh)
    if expanded_region != region_xywh:
      warning(
        f"Energy region height {screenshot.shape[0]}px is too small for template matching; "
        f"retrying with expanded region {expanded_region}."
      )
      screenshot = device_action.screenshot(region_xywh=expanded_region)
      region_xywh = expanded_region
  record_runtime_ocr_debug("energy", image=screenshot)

  right_bar_match = device_action.match_template("assets/ui/energy_bar_right_end_part.png", screenshot, threshold)
  # longer energy bars get more round at the end
  if not right_bar_match:
    right_bar_match = device_action.match_template("assets/ui/energy_bar_right_end_part_2.png", screenshot, threshold)

  if right_bar_match:
    x, y, w, h = right_bar_match[0]
    energy_bar_length = x
    debug(f"Energy bar right end at x={energy_bar_length}")

    x, y, w, h = region_xywh
    top_bottom_middle_pixel = int(y + h // 2)
    debug(f"Top bottom middle pixel: {top_bottom_middle_pixel}")

    # Find where the actual bar starts by scanning the middle pixel row.
    # The energy region may include text labels or decorations to the left
    # of the bar that would otherwise inflate the energy reading.
    full_strip_region = (x, top_bottom_middle_pixel, x + energy_bar_length, top_bottom_middle_pixel + 1)
    full_strip = device_action.screenshot(region_ltrb=full_strip_region)
    bar_start_offset = _find_bar_start_offset(full_strip)
    if bar_start_offset > 0:
      debug(f"Bar start offset: {bar_start_offset}px (skipping non-bar pixels)")

    bar_left_absolute = x + bar_start_offset
    MAX_ENERGY_REGION = (bar_left_absolute, top_bottom_middle_pixel, x + energy_bar_length, top_bottom_middle_pixel + 1)
    debug_window(device_action.screenshot(region_ltrb=MAX_ENERGY_REGION), save_name="MAX_ENERGY_REGION")
    debug(f"MAX_ENERGY_REGION: {MAX_ENERGY_REGION}")
    #[117,117,117] is gray for missing energy, region templating for this one is a problem, so we do this
    empty_energy_pixel_count = count_pixels_of_color([115,115,115], MAX_ENERGY_REGION, tolerance=5)

    #use the energy_bar_length (a few extra pixels from the outside are remaining so we subtract that)
    total_energy_length = energy_bar_length - bar_start_offset - 1
    hundred_energy_pixel_constant = 236 #counted pixels from one end of the bar to the other, should be fine since we're working in only 1080p

    energy_level = ((total_energy_length - empty_energy_pixel_count) / hundred_energy_pixel_constant) * 100
    info(f"Total energy bar length = {total_energy_length}, Empty energy pixel count = {empty_energy_pixel_count}, Diff = {(total_energy_length - empty_energy_pixel_count)}")
    info(f"Remaining energy guestimate = {energy_level:.2f}")
    max_energy = total_energy_length / hundred_energy_pixel_constant * 100
    return energy_level, max_energy
  else:
    warning(f"Couldn't find energy bar, returning -1")
    return -1, -1

def filter_race_list(state):
  debug(f"Races before filtering: {constants.ALL_RACES}")
  if not state.get("aptitudes"):
    constants.RACES = {date: list(races) for date, races in constants.ALL_RACES.items()}
    debug("Aptitudes unavailable; using unfiltered race list.")
    return
  constants.RACES = {}
  aptitudes = state["aptitudes"]
  min_surface_index = get_aptitude_index(config.MINIMUM_APTITUDES["surface"])
  min_distance_index = get_aptitude_index(config.MINIMUM_APTITUDES["distance"])
  if not aptitudes:
    warning("Aptitudes are empty; race filtering will produce no suitable races instead of crashing.")
  if min_surface_index is None or min_distance_index is None:
    warning(f"Invalid minimum aptitude config: {config.MINIMUM_APTITUDES}")
  for date in constants.ALL_RACES:
    if date not in constants.RACES:
      constants.RACES[date] = []
    for race in constants.ALL_RACES[date]:
      if min_surface_index is None or min_distance_index is None:
        suitable = False
      else:
        suitable = check_race_suitability(race, aptitudes, min_surface_index, min_distance_index)
      if suitable:
        constants.RACES[date].append(race)
  debug(f"Races after filtering: {constants.RACES}")

def filter_race_schedule(state):
  config.RACE_SCHEDULE = get_effective_schedule_entries(
    getattr(config, "OPERATOR_RACE_SELECTOR", None),
    getattr(config, "RACE_SCHEDULE_CONF", []),
  )
  debug(f"Schedule before filtering: {config.RACE_SCHEDULE}")
  schedule = {}
  for race in config.RACE_SCHEDULE:
    date_long = f"{race['year']} {race['date']}"
    if date_long not in schedule:
      schedule[date_long] = []
    schedule[date_long].append(race)
  config.RACE_SCHEDULE = schedule
  if not state.get("aptitudes"):
    for date in schedule:
      for race in schedule[date]:
        for race_data in constants.ALL_RACES.get(date, []):
          if race_data["name"] == race["name"]:
            race["fans_gained"] = race_data["fans"]["gained"]
            break
    debug("Aptitudes unavailable; using unfiltered race schedule.")
    return
  for date in schedule:
    for race in schedule[date]:
      if race["name"] not in [k["name"] for k in constants.RACES[date]]:
        schedule[date].remove(race)
      else:
        # find race name in constants.ALL_RACES[date] and get fans_gained
        for race_data in constants.ALL_RACES[date]:
          if race_data["name"] == race["name"]:
            race["fans_gained"] = race_data["fans"]["gained"]
            break
  debug(f"Schedule after filtering: {config.RACE_SCHEDULE}")


# === Debug capture functions for calibration (macOS hotkeys) ===

# Recognition offsets for OCR calibration
ACTIVE_RECOGNITION_OFFSET = (0, 0)

def debug_capture_year_region():
  """Capture year region for OCR calibration."""
  import os
  from datetime import datetime
  
  if constants.SCENARIO_NAME == "unity":
    region_xywh = constants.UNITY_YEAR_REGION
  elif constants.SCENARIO_NAME in ("mant", "trackblazer"):
    region_xywh = constants.MANT_YEAR_REGION
  else:
    region_xywh = constants.YEAR_REGION
  
  screenshot = device_action.screenshot(region_xywh=region_xywh)
  
  # Save to debug folder
  debug_dir = "debug_output"
  os.makedirs(debug_dir, exist_ok=True)
  timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
  path = os.path.join(debug_dir, f"year_region_{timestamp}.png")
  cv2.imwrite(path, cv2.cvtColor(screenshot, cv2.COLOR_RGB2BGR))
  return path


def debug_capture_stat_regions():
  """Capture stat regions for OCR calibration."""
  import os
  from datetime import datetime
  
  region_xywh = constants.CURRENT_STATS_REGION
  screenshot = device_action.screenshot(region_xywh=region_xywh)
  
  debug_dir = "debug_output"
  os.makedirs(debug_dir, exist_ok=True)
  timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
  
  paths = {}
  path = os.path.join(debug_dir, f"stats_region_{timestamp}.png")
  cv2.imwrite(path, cv2.cvtColor(screenshot, cv2.COLOR_RGB2BGR))
  paths["stats"] = path
  return paths


def debug_capture_support_region():
  """Capture support card region for OCR calibration."""
  import os
  from datetime import datetime
  
  if constants.SCENARIO_NAME == "unity":
    region_xywh = constants.UNITY_SUPPORT_CARD_ICON_REGION
  elif constants.SCENARIO_NAME in ("mant", "trackblazer"):
    region_xywh = constants.MANT_SUPPORT_CARD_ICON_REGION
  else:
    region_xywh = constants.SUPPORT_CARD_ICON_REGION
  
  screenshot = device_action.screenshot(region_xywh=region_xywh)
  
  debug_dir = "debug_output"
  os.makedirs(debug_dir, exist_ok=True)
  timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
  path = os.path.join(debug_dir, f"support_region_{timestamp}.png")
  cv2.imwrite(path, cv2.cvtColor(screenshot, cv2.COLOR_RGB2BGR))
  return path


def debug_capture_event_region():
  """Capture event region for OCR calibration."""
  import os
  from datetime import datetime
  
  region_xywh = constants.EVENT_NAME_REGION
  screenshot = device_action.screenshot(region_xywh=region_xywh)
  
  debug_dir = "debug_output"
  os.makedirs(debug_dir, exist_ok=True)
  timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
  path = os.path.join(debug_dir, f"event_region_{timestamp}.png")
  cv2.imwrite(path, cv2.cvtColor(screenshot, cv2.COLOR_RGB2BGR))
  return path


def debug_capture_recreation_region():
  """Capture recreation region for OCR calibration."""
  import os
  from datetime import datetime
  
  # Recreation region - use screen middle for now
  region_xywh = constants.SCREEN_MIDDLE_REGION
  screenshot = device_action.screenshot(region_xywh=region_xywh)
  
  debug_dir = "debug_output"
  os.makedirs(debug_dir, exist_ok=True)
  timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
  path = os.path.join(debug_dir, f"recreation_region_{timestamp}.png")
  cv2.imwrite(path, cv2.cvtColor(screenshot, cv2.COLOR_RGB2BGR))
  return path


def stat_state():
  """Return current stat values for debug display."""
  return get_current_stats(get_turn())
