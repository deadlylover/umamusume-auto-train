import copy
from collections import Counter
import pyautogui
import os
import cv2
from pathlib import Path

from utils.tools import sleep, get_secs, click
from core.state import APTITUDE_BOX_RATIOS, collect_main_state, collect_training_state, collect_trackblazer_inventory, clear_aptitudes_cache
from utils.shared import CleanDefaultDict
import core.config as config
from PIL import ImageGrab
from core.actions import Action
import utils.constants as constants
from scenarios.unity import unity_cup_function
from core.events import select_event
from core.claw_machine import play_claw_machine
from core.skill import (
  get_skill_purchase_check_state,
  get_skill_purchase_context,
  init_skill_py,
  mark_skill_purchase_checked,
  update_skill_action_count,
)
from core.skill_scanner import collect_skill_purchase
from core.trackblazer_item_use import plan_item_usage
from core.operator_console import ensure_operator_console, publish_runtime_state
from core.region_adjuster.shared import resolve_region_adjuster_profiles
from core.race_selector import get_race_gate_for_turn_label
from core.runtime_flow import (
  PHASE_POST_ACTION_RESOLUTION,
  SUB_PHASE_POST_ACTION_RESOLUTION,
  SUB_PHASE_RESOLVE_CONSECUTIVE_RACE_WARNING,
  SUB_PHASE_RESOLVE_EVENT_CHOICE,
  SUB_PHASE_RESOLVE_POST_ACTION_POPUP,
  SUB_PHASE_RESOLVE_SCHEDULED_RACE_POPUP,
  SUB_PHASE_RESOLVE_SHOP_REFRESH_POPUP,
  SUB_PHASE_RETURN_TO_LOBBY,
)
from core.trackblazer_shop import get_dynamic_shop_limits, get_effective_shop_items, get_priority_preview, policy_context
from core.trackblazer_item_use import get_training_behavior_strong_training_score_threshold
from core.trackblazer_race_logic import (
  evaluate_trackblazer_race,
  get_race_lookahead_energy_advice,
)

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
  "ok_2_btn": "assets/buttons/ok_2_btn.png",
  "shop_refresh": "assets/icons/shop_refresh.png"
}

cached_templates = cache_templates(templates)
RUNTIME_DEBUG_IMAGE_CAPTURE_ENABLED = False  # Re-enable this if you need review/debug crops written under logs/runtime_debug again.

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
TRACKBLAZER_INVENTORY_STATE_KEYS = (
  "trackblazer_inventory",
  "trackblazer_inventory_controls",
  "trackblazer_inventory_summary",
  "trackblazer_inventory_flow",
)
last_trackblazer_shop_refresh_turn = None
_cached_trackblazer_inventory = None
_cached_trackblazer_inventory_turn = None
_TRACKBLAZER_INVENTORY_CACHE_MAX_TURNS = 3


def _canonicalize_scenario_name(name):
  if not name:
    return ""
  return SCENARIO_NAME_ALIASES.get(name, name)


def _normalize_trackblazer_inventory_summary(state_obj, context_label=""):
  """Backfill summary fields from inventory entries when cache data is partial."""
  if not isinstance(state_obj, dict):
    return
  inventory = state_obj.get("trackblazer_inventory")
  if not isinstance(inventory, dict) or not inventory:
    return

  summary = copy.deepcopy(state_obj.get("trackblazer_inventory_summary") or {})
  items_detected = list(summary.get("items_detected") or [])
  held_quantities = dict(summary.get("held_quantities") or {})
  actionable_items = list(summary.get("actionable_items") or [])
  by_category = copy.deepcopy(summary.get("by_category") or {})
  changed = False

  for item_key, item_data in inventory.items():
    if not isinstance(item_data, dict):
      continue
    detected = bool(item_data.get("detected"))
    held_quantity = item_data.get("held_quantity")
    increment_target = item_data.get("increment_target")
    category_name = item_data.get("category", "unknown")
    try:
      held_quantity = int(held_quantity)
    except (TypeError, ValueError):
      held_quantity = None

    if detected and item_key not in items_detected:
      items_detected.append(item_key)
      changed = True
    if held_quantity is not None and held_quantity > 0 and held_quantities.get(item_key) != held_quantity:
      held_quantities[item_key] = held_quantity
      changed = True
    if detected and increment_target and item_key not in actionable_items:
      actionable_items.append(item_key)
      changed = True
    if detected and item_key not in (by_category.get(category_name) or []):
      by_category.setdefault(category_name, []).append(item_key)
      changed = True

  if not changed:
    return

  summary["items_detected"] = items_detected
  summary["held_quantities"] = held_quantities
  summary["actionable_items"] = actionable_items
  summary["by_category"] = by_category
  summary["total_detected"] = len(items_detected)
  state_obj["trackblazer_inventory_summary"] = summary
  if context_label:
    info(f"[TB_INV] Normalized inventory summary from inventory entries ({context_label}).")


def _cache_trackblazer_inventory(state_obj, turn_key=None):
  """Store the current inventory state keys so we can skip re-scanning next turn."""
  global _cached_trackblazer_inventory, _cached_trackblazer_inventory_turn
  if not isinstance(state_obj, dict):
    return
  _normalize_trackblazer_inventory_summary(state_obj, context_label="cache_write")
  _cached_trackblazer_inventory = {
    key: copy.deepcopy(state_obj.get(key)) for key in TRACKBLAZER_INVENTORY_STATE_KEYS
  }
  if turn_key is not None:
    _cached_trackblazer_inventory_turn = turn_key


def _restore_cached_trackblazer_inventory(state_obj, current_turn_number=None):
  """Apply cached inventory data to state_obj. Returns True if cache was available and fresh."""
  if not _cached_trackblazer_inventory or not isinstance(state_obj, dict):
    return False
  # Expire cache if it's older than N turns.
  if (
    current_turn_number is not None
    and _cached_trackblazer_inventory_turn is not None
    and isinstance(_cached_trackblazer_inventory_turn, (int, float))
    and isinstance(current_turn_number, (int, float))
  ):
    age = abs(current_turn_number - _cached_trackblazer_inventory_turn)
    if age >= _TRACKBLAZER_INVENTORY_CACHE_MAX_TURNS:
      info(f"[TB_INV] Inventory cache expired (age {age} turns >= {_TRACKBLAZER_INVENTORY_CACHE_MAX_TURNS}).")
      _invalidate_trackblazer_inventory_cache()
      return False
  for key, value in _cached_trackblazer_inventory.items():
    state_obj[key] = copy.deepcopy(value)
  _normalize_trackblazer_inventory_summary(state_obj, context_label="cache_restore")
  return True


def _trackblazer_inventory_flow_cacheable(flow):
  if not isinstance(flow, dict):
    return False
  if flow.get("skipped"):
    return False
  if flow.get("reason") in {
    "failed_to_open_inventory",
    "failed_to_close_inventory",
    "confirm_use_not_available",
    "confirm_use_not_available_closed_inventory",
    "required_items_not_actionable",
  }:
    return False
  if flow.get("missing_increment_targets"):
    return False
  if flow.get("opened") and not flow.get("closed") and not flow.get("already_open"):
    return False
  return True


def _invalidate_trackblazer_inventory_cache():
  global _cached_trackblazer_inventory, _cached_trackblazer_inventory_turn
  _cached_trackblazer_inventory = None
  _cached_trackblazer_inventory_turn = None


def _apply_trackblazer_used_items_to_state(state_obj, used_item_keys):
  if not isinstance(state_obj, dict):
    return
  item_counts = Counter(str(item_key) for item_key in (used_item_keys or []) if item_key)
  if not item_counts:
    return

  inventory = copy.deepcopy(state_obj.get("trackblazer_inventory") or {})
  summary = copy.deepcopy(state_obj.get("trackblazer_inventory_summary") or {})
  held_quantities = dict(summary.get("held_quantities") or {})
  items_detected = list(summary.get("items_detected") or [])
  actionable_items = list(summary.get("actionable_items") or [])
  by_category = copy.deepcopy(summary.get("by_category") or {})

  for item_key, used_count in item_counts.items():
    entry = dict(inventory.get(item_key) or {})
    prior_quantity = entry.get("held_quantity")
    if prior_quantity is None:
      prior_quantity = held_quantities.get(item_key)
    try:
      prior_quantity = int(prior_quantity)
    except (TypeError, ValueError):
      prior_quantity = None

    remaining_quantity = max(0, prior_quantity - used_count) if prior_quantity is not None else 0
    if entry:
      if remaining_quantity > 0:
        entry["held_quantity"] = remaining_quantity
      else:
        entry["held_quantity"] = 0
        entry["detected"] = False
        entry["increment_target"] = None
        entry["increment_match"] = None
      inventory[item_key] = entry

    if remaining_quantity > 0:
      held_quantities[item_key] = remaining_quantity
    else:
      held_quantities.pop(item_key, None)
      items_detected = [name for name in items_detected if name != item_key]
      actionable_items = [name for name in actionable_items if name != item_key]
      for category_name, category_items in list(by_category.items()):
        filtered_items = [name for name in category_items if name != item_key]
        if filtered_items:
          by_category[category_name] = filtered_items
        else:
          by_category.pop(category_name, None)

  summary["held_quantities"] = held_quantities
  summary["items_detected"] = items_detected
  summary["actionable_items"] = actionable_items
  summary["by_category"] = by_category
  summary["total_detected"] = len(items_detected)
  state_obj["trackblazer_inventory"] = inventory
  state_obj["trackblazer_inventory_summary"] = summary


def _copy_trackblazer_inventory_snapshot(state_obj, prefix="trackblazer_inventory_pre_shop"):
  if not isinstance(state_obj, dict):
    return
  for key in TRACKBLAZER_INVENTORY_STATE_KEYS:
    suffix = key.replace("trackblazer_inventory", "", 1)
    state_obj[f"{prefix}{suffix}"] = copy.deepcopy(state_obj.get(key))


def _project_trackblazer_inventory_for_planned_buys(state_obj, planned_buys):
  """Return a copied state with held quantities incremented by planned buys."""
  projected_state = dict(state_obj or {})
  inventory = copy.deepcopy(projected_state.get("trackblazer_inventory") or {})
  summary = copy.deepcopy(projected_state.get("trackblazer_inventory_summary") or {})
  held_quantities = dict(summary.get("held_quantities") or {})
  items_detected = list(summary.get("items_detected") or [])
  actionable_items = list(summary.get("actionable_items") or [])

  for buy_entry in list(planned_buys or []):
    item_key = buy_entry.get("key") if isinstance(buy_entry, dict) else None
    if not item_key:
      continue
    next_quantity = int(held_quantities.get(item_key) or 0) + 1
    held_quantities[item_key] = next_quantity
    if item_key not in items_detected:
      items_detected.append(item_key)
    if item_key not in actionable_items:
      actionable_items.append(item_key)
    item_entry = dict(inventory.get(item_key) or {})
    item_entry["detected"] = True
    item_entry["held_quantity"] = next_quantity
    inventory[item_key] = item_entry

  summary["held_quantities"] = held_quantities
  summary["items_detected"] = items_detected
  summary["actionable_items"] = actionable_items
  summary["total_detected"] = len(items_detected)
  projected_state["trackblazer_inventory"] = inventory
  projected_state["trackblazer_inventory_summary"] = summary
  return projected_state


def _merge_post_shop_inventory_with_preserved_snapshot(state_obj):
  if not isinstance(state_obj, dict):
    return

  refreshed_inventory = copy.deepcopy(state_obj.get("trackblazer_inventory") or {})
  refreshed_summary = copy.deepcopy(state_obj.get("trackblazer_inventory_summary") or {})
  preserved_inventory = copy.deepcopy(state_obj.get("trackblazer_inventory_pre_shop") or {})
  preserved_summary = copy.deepcopy(state_obj.get("trackblazer_inventory_pre_shop_summary") or {})
  if not refreshed_inventory or not preserved_inventory:
    return

  refreshed_detected = set(refreshed_summary.get("items_detected") or [])
  preserved_detected = set(preserved_summary.get("items_detected") or [])
  if not preserved_detected - refreshed_detected:
    return

  merged_inventory = copy.deepcopy(refreshed_inventory)
  restored_items = []
  for item_key in preserved_detected:
    preserved_entry = preserved_inventory.get(item_key) or {}
    refreshed_entry = merged_inventory.get(item_key) or {}
    if refreshed_entry.get("detected"):
      if refreshed_entry.get("held_quantity") is None and preserved_entry.get("held_quantity") is not None:
        refreshed_entry["held_quantity"] = preserved_entry.get("held_quantity")
        merged_inventory[item_key] = refreshed_entry
      continue
    restored_entry = copy.deepcopy(preserved_entry)
    restored_entry["increment_target"] = None
    restored_entry["increment_match"] = None
    restored_entry["increment_target_stale"] = True
    merged_inventory[item_key] = restored_entry
    restored_items.append(item_key)

  if not restored_items:
    return

  from scenarios.trackblazer import build_inventory_summary

  merged_summary = build_inventory_summary(merged_inventory)
  merged_summary["inventory_button_visible"] = refreshed_summary.get(
    "inventory_button_visible",
    preserved_summary.get("inventory_button_visible"),
  )
  state_obj["trackblazer_inventory"] = merged_inventory
  state_obj["trackblazer_inventory_summary"] = merged_summary
  info(
    "[TB_INV] Post-shop refresh missed previously detected items; "
    f"preserved pre-shop entries for re-plan: {sorted(restored_items)}"
  )


def _merge_trackblazer_shop_result(state_obj, shop_result):
  if not isinstance(state_obj, dict) or not isinstance(shop_result, dict):
    return state_obj
  for key in ("trackblazer_shop_items", "trackblazer_shop_summary", "trackblazer_shop_flow"):
    if key in shop_result:
      state_obj[key] = shop_result[key]
  return state_obj


def _trackblazer_turn_key(state_obj):
  if not isinstance(state_obj, dict):
    return None
  return (state_obj.get("year"), state_obj.get("turn"))


def _trackblazer_scenario_active():
  return constants.SCENARIO_NAME in ("mant", "trackblazer")


def _handle_trackblazer_shop_refresh_popup():
  from scenarios.trackblazer import inspect_shop_entry_state

  shop_state = inspect_shop_entry_state(threshold=0.75)
  refresh_dialog = (shop_state.get("methods") or {}).get("refresh_dialog") or {}
  if not refresh_dialog.get("matched"):
    return {
      "detected": False,
      "handled": False,
      "popup_type": "shop_refresh_popup",
      "reason": "refresh_dialog_not_matched",
      "deferred_work": [],
    }

  dismiss_entry = refresh_dialog.get("dismiss") or {}
  dismiss_target = dismiss_entry.get("click_target")
  if dismiss_target:
    click_metrics = device_action.click_with_metrics(dismiss_target)
    if click_metrics.get("clicked"):
      bot.request_trackblazer_shop_check("refresh_dialog_popup")
      info("[TB_SHOP] Refresh popup detected; dismissed it and queued a shop check.")
      bot.push_debug_history({"event": "click", "asset": "shop_refresh_cancel", "result": "clicked", "context": "trackblazer_refresh_popup"})
      return {
        "detected": True,
        "handled": True,
        "popup_type": "shop_refresh_popup",
        "reason": "dismissed_and_queued_shop_check",
        "deferred_work": ["shop_check_pending"],
      }

  warning("[TB_SHOP] Refresh popup detected but dismiss button was not clickable; shop check NOT queued.")
  return {
    "detected": True,
    "handled": False,
    "popup_type": "shop_refresh_popup",
    "reason": "dismiss_target_not_clickable",
    "deferred_work": [],
  }


def _scenario_banner_templates():
  return {
    os.path.splitext(filename)[0]: f"assets/scenario_banner/{filename}"
    for filename in sorted(os.listdir("assets/scenario_banner"))
    if filename.endswith(".png")
  }


def _scenario_banner_template_scales():
  base_scale = float(device_action._effective_template_scale())
  candidates = [1.0, base_scale * 0.9, base_scale, base_scale * 1.1, base_scale * 1.2]
  unique_scales = []
  seen = set()
  for scale in candidates:
    rounded = round(float(scale), 3)
    if rounded in seen or rounded <= 0:
      continue
    seen.add(rounded)
    unique_scales.append(float(rounded))
  return unique_scales


def _match_scenario_banners(screenshot, threshold=0.8):
  match_counts = {}
  match_debug = {}
  first_match = ""
  first_match_score = -1.0
  template_scales = _scenario_banner_template_scales()
  for raw_name, template_path in _scenario_banner_templates().items():
    best_match = device_action.best_template_match(
      template_path,
      screenshot,
      template_scales=template_scales,
    )
    best_score = round(best_match["score"], 4) if best_match is not None else None
    passed_threshold = bool(best_match is not None and best_match["score"] >= threshold)
    match_counts[raw_name] = 1 if passed_threshold else 0
    match_debug[raw_name] = {
      "threshold": threshold,
      "best_live_score": best_score,
      "passed_threshold": passed_threshold,
      "match_location": best_match["location"] if best_match is not None else None,
      "match_size": best_match["size"] if best_match is not None else None,
      "best_match_scale": round(best_match["scale"], 3) if best_match is not None else None,
      "template_scales_tested": template_scales,
    }
    if passed_threshold and best_match["score"] > first_match_score:
      first_match = raw_name
      first_match_score = best_match["score"]
  return first_match, match_counts, match_debug


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
    banner_name, banner_counts, banner_debug = _match_scenario_banners(screenshot)
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
      "banner_debug": banner_debug,
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
  raw_name, match_counts, match_debug = _match_scenario_banners(screenshot)
  update_startup_scan_snapshot(
    message="Scenario banner scan complete.",
    sub_phase="detect_scenario_match_banner",
    ocr_debug=detail_attempt_entries + [
      _template_debug_entry(
        f"scenario_banner_{name}",
        f"assets/scenario_banner/{name}.png",
        bbox_key="GAME_WINDOW_BBOX",
        parsed_value=count,
        extra=match_debug.get(name),
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
          bbox_key="GAME_WINDOW_BBOX",
          parsed_value=count,
          extra={
            "canonical_name": _canonicalize_scenario_name(name),
            **(match_debug.get(name) or {}),
          },
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


def _is_usable_turn_value(value):
  if value in ("", None, -1):
    return False
  return bool(str(value).strip())


def _restore_turn_from_last_state(state_obj):
  global last_state
  if not isinstance(state_obj, dict) or _is_usable_turn_value(state_obj.get("turn")):
    return False

  previous_year = last_state.get("year") if hasattr(last_state, "get") else None
  previous_turn = last_state.get("turn") if hasattr(last_state, "get") else None
  if not _is_usable_turn_value(previous_turn):
    return False

  current_year = state_obj.get("year")
  if current_year and previous_year and current_year != previous_year:
    return False

  state_obj["turn"] = previous_turn
  state_obj["turn_fallback"] = {
    "source": "last_state",
    "year": current_year or previous_year,
    "turn": previous_turn,
  }
  warning(
    f"[STATE] Turn OCR failed during main-state collection; reusing last remembered turn {previous_turn}"
    f" for year {current_year or previous_year}."
  )
  return True


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
  if image is None or getattr(image, "size", 0) == 0:
    return ""
  if not RUNTIME_DEBUG_IMAGE_CAPTURE_ENABLED:
    return ""
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


def _best_template_score(template_path, crop, grayscale=False, template_scales=None):
  if crop is None or crop.size == 0:
    return None

  best_match = device_action.best_template_match(
    template_path,
    crop,
    grayscale=grayscale,
    template_scales=template_scales,
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
  if entry.get("search_image_path"):
    template_path = _resolve_template_path(entry.get("template"))
    if template_path:
      entry["template_image_path"] = template_path
    return entry

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
    best_score = _best_template_score(
      template_path,
      crop,
      grayscale=bool(entry.get("grayscale", False)),
      template_scales=entry.get("template_scales"),
    )
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


def _apply_runtime_ocr_debug(entry, state_obj):
  if not isinstance(state_obj, dict):
    return entry
  runtime_debug = state_obj.get("ocr_runtime_debug", {}) or {}
  field_debug = runtime_debug.get(entry.get("field"), {}) or {}
  if field_debug:
    entry.update({k: v for k, v in field_debug.items() if v not in (None, "")})
    if field_debug.get("matched_template"):
      entry["template"] = field_debug["matched_template"]
  return entry


def _base_ocr_debug_entries(state_obj):
  scenario_name = constants.SCENARIO_NAME or "default"
  entries = [
    _region_debug_entry("turn", "UNITY_TURN_REGION" if scenario_name == "unity" else "MANT_TURN_REGION" if scenario_name == "trackblazer" else "TURN_REGION", parsed_value=state_obj.get("turn")),
    _region_debug_entry("year", "UNITY_YEAR_REGION" if scenario_name == "unity" else "MANT_YEAR_REGION" if scenario_name == "trackblazer" else "YEAR_REGION", parsed_value=state_obj.get("year")),
    _region_debug_entry("criteria", "UNITY_CRITERIA_REGION" if scenario_name == "unity" else "MANT_CRITERIA_REGION" if scenario_name == "trackblazer" else "CRITERIA_REGION", parsed_value=_truncate(state_obj.get("criteria", ""))),
    _region_debug_entry("energy", "UNITY_ENERGY_REGION" if scenario_name == "unity" else "MANT_ENERGY_REGION" if scenario_name == "trackblazer" else "ENERGY_REGION", parsed_value=f"{state_obj.get('energy_level', '?')}/{state_obj.get('max_energy', '?')}"),
  ]
  if scenario_name == "trackblazer":
    inv_summary = state_obj.get("trackblazer_inventory_summary") or {}
    entries.extend(
      [
        _region_debug_entry("trackblazer_grade_points", "MANT_GRADE_POINT_REGION", parsed_value=state_obj.get("grade_points")),
        _region_debug_entry("trackblazer_shop_coins", "MANT_SHOP_COIN_REGION", parsed_value=state_obj.get("shop_coins")),
        _region_debug_entry("trackblazer_shop_button", "MANT_SHOP_BUTTON_REGION"),
        _region_debug_entry(
          "trackblazer_inventory",
          "MANT_INVENTORY_ITEMS_REGION",
          parsed_value=inv_summary.get("items_detected", []),
          source_type="template_match",
          extra={
            "total_detected": inv_summary.get("total_detected", 0),
            "by_category": inv_summary.get("by_category", {}),
          },
        ),
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
  return [_apply_runtime_ocr_debug(entry, state_obj) for entry in entries]


def _state_validation_ocr_debug_entries(state_obj, validation_result=None):
  entries = _base_ocr_debug_entries(state_obj)
  scenario_name = constants.SCENARIO_NAME or "default"
  stats_region_key = "MANT_CURRENT_STATS_REGION" if scenario_name in ("mant", "trackblazer") else "CURRENT_STATS_REGION"
  current_stats = state_obj.get("current_stats") or {}
  for stat_name in ("spd", "sta", "pwr", "guts", "wit", "sp"):
    if stat_name in current_stats:
      entries.append(
        _region_debug_entry(
          f"current_stat_{stat_name}",
          stats_region_key,
          parsed_value=current_stats.get(stat_name),
          extra={"stat_name": stat_name},
        )
      )

  if validation_result is not None:
    entries.insert(
      0,
      {
        "field": "state_validation",
        "source_type": "ocr_summary",
        "scenario_name": scenario_name,
        "platform_profile": getattr(config, "PLATFORM_PROFILE", "auto"),
        "parsed_value": "valid" if validation_result.get("valid") else "invalid_retry",
        "year": state_obj.get("year"),
        "turn": state_obj.get("turn"),
        "turn_label": f"{state_obj.get('year', '?')} / {state_obj.get('turn', '?')}",
        "reasons": list(validation_result.get("invalid_reasons") or []),
        "same_turn_retry": True,
        "before_phase": validation_result.get("before_phase"),
        "context": validation_result.get("context"),
      },
    )

  return _enrich_ocr_debug_entries(entries)


def _push_turn_retry_debug(state_obj, *, reason, reasons=None, before_phase=None, context=None, event="turn_retry", result="retry", same_turn_retry=True, sub_phase=None, phase=None):
  payload = {
    "event": event,
    "result": result,
    "reason": reason,
    "reasons": list(reasons or []),
    "same_turn_retry": bool(same_turn_retry),
    "before_phase": before_phase or bot.get_runtime_state().get("phase"),
    "context": context,
    "turn_label": f"{state_obj.get('year', '?')} / {state_obj.get('turn', '?')}",
    "year": state_obj.get("year"),
    "turn": state_obj.get("turn"),
  }
  if sub_phase is not None:
    payload["sub_phase"] = sub_phase
  if phase is not None:
    payload["phase"] = phase
  bot.push_debug_history(payload)


def _push_flow_decision_debug(state_obj=None, *, asset, result, note="", context="flow_decision", phase=None, sub_phase=None, **extra):
  payload = {
    "event": "flow_decision",
    "asset": asset,
    "result": result,
    "context": context,
  }
  if note:
    payload["note"] = str(note)
  if isinstance(state_obj, dict):
    payload["year"] = state_obj.get("year")
    payload["turn"] = state_obj.get("turn")
    payload["turn_label"] = f"{state_obj.get('year', '?')} / {state_obj.get('turn', '?')}"
  if phase is not None:
    payload["phase"] = phase
  if sub_phase is not None:
    payload["sub_phase"] = sub_phase
  payload.update(extra)
  bot.push_debug_history(payload)


def _action_value(action, key, default=None):
  if isinstance(action, dict):
    return action.get(key, default)
  if hasattr(action, "get"):
    return action.get(key, default)
  return default


def _action_func(action):
  if isinstance(action, dict):
    return action.get("func")
  return getattr(action, "func", None)


def _action_option_pop(action, key):
  if isinstance(action, dict):
    action.pop(key, None)
  elif hasattr(action, "options"):
    action.options.pop(key, None)


def _clear_optional_race_action_fields(action):
  for key in (
    "race_name",
    "race_image_path",
    "race_mission_available",
    "prioritize_missions_over_g1",
    "scheduled_race",
    "trackblazer_lobby_scheduled_race",
    "hammer_spendable",
    "prefer_rival_race",
    "race_grade_target",
  ):
    _action_option_pop(action, key)


def _operator_race_gate_for_state(state_obj):
  return get_race_gate_for_turn_label(
    state_obj.get("year") if isinstance(state_obj, dict) else "",
    getattr(config, "OPERATOR_RACE_SELECTOR", None),
  )


def _operator_race_gate_blocks_optional_races(state_obj):
  gate = _operator_race_gate_for_state(state_obj)
  blocked = bool(gate.get("enabled") and not gate.get("race_allowed"))
  return blocked, gate


def _operator_race_gate_message(gate, context="racing"):
  turn_label = gate.get("turn_label") or "this date"
  message = f"Operator race gate disabled {context} on {turn_label}."
  selected_race = gate.get("selected_race")
  if selected_race:
    message += f" Selected race remains {selected_race}."
  return message


def _revert_optional_race_to_fallback(action):
  fallback_func = _action_value(action, "_rival_fallback_func")
  if not fallback_func or fallback_func == "do_race":
    return False
  if isinstance(action, dict):
    action["func"] = fallback_func
  else:
    action.func = fallback_func
  if _action_value(action, "_rival_fallback_training_name"):
    action["training_name"] = _action_value(action, "_rival_fallback_training_name")
  if _action_value(action, "_rival_fallback_training_data"):
    action["training_data"] = _action_value(action, "_rival_fallback_training_data")
  _clear_optional_race_action_fields(action)
  return True


def _enforce_operator_race_gate_before_execute(state_obj, action, sub_phase=None, ocr_debug=None, planned_clicks=None):
  if _action_func(action) != "do_race":
    return None
  if _action_value(action, "is_race_day") or _action_value(action, "trackblazer_climax_race_day"):
    return None

  blocked, gate = _operator_race_gate_blocks_optional_races(state_obj)
  if not blocked:
    return None

  reason = _operator_race_gate_message(gate)
  if _revert_optional_race_to_fallback(action):
    update_operator_snapshot(
      state_obj,
      action,
      phase="executing_action",
      message=f"{reason} Reverted to {_action_func(action)} before execute.",
      sub_phase=sub_phase,
      ocr_debug=ocr_debug,
      planned_clicks=planned_clicks,
    )
    return "reverted"

  update_operator_snapshot(
    state_obj,
    action,
    phase="waiting_for_confirmation",
    message=reason,
    sub_phase=sub_phase,
    ocr_debug=ocr_debug,
    planned_clicks=planned_clicks,
  )
  return "blocked"


def _strategy_decision_note(state_obj, action):
  if not hasattr(action, "get"):
    return ""
  func_name = _action_func(action) or "none"
  if func_name == "do_training":
    training_name = action.get("training_name") or "unknown"
    score_tuple = ((action.get("training_data") or {}).get("score_tuple") or [])
    score_value = score_tuple[0] if score_tuple else None
    return f"training={training_name}; score={score_value if score_value is not None else '?'}"
  if func_name == "do_race":
    race_name = action.get("race_name") or "any"
    race_reason = ((action.get("trackblazer_race_decision") or {}).get("reason") or "")
    return f"race={race_name}; reason={race_reason or 'strategy_selected_race'}"
  if func_name == "buy_skill":
    return "skill_review_requested"
  return func_name


def _trackblazer_pre_action_items(action):
  if hasattr(action, "get"):
    return list(action.get("trackblazer_pre_action_items") or [])
  if isinstance(action, dict):
    return list(action.get("trackblazer_pre_action_items") or [])
  return []


def _skill_purchase_plan(action):
  if hasattr(action, "get"):
    return dict(action.get("skill_purchase_plan") or {})
  if isinstance(action, dict):
    return dict(action.get("skill_purchase_plan") or {})
  return {}


def _skill_purchase_has_actionable_targets(scan_result):
  if not isinstance(scan_result, dict):
    return False
  for entry in (scan_result.get("target_results") or []):
    if not isinstance(entry, dict):
      continue
    increment_click_result = entry.get("increment_click_result") or {}
    if increment_click_result.get("target"):
      return True
  return False


_SKILL_DEFAULT_COST = 180
_SKILL_GOLD_PREREQUISITES = {
  "Professor of Curvature": "Corner Adept ○",
  "Swinging Maestro": "Corner Recovery ○",
  "Breath of Fresh Air": "Straightaway Recovery",
}
_SKILL_PREREQ_TO_GOLD = {
  prereq: gold for gold, prereq in _SKILL_GOLD_PREREQUISITES.items()
}


def _normalize_skill_name(value):
  if value is None:
    return ""
  return " ".join(str(value).strip().split())


def _build_skill_entry_index(scan_result):
  indexed = {}
  if not isinstance(scan_result, dict):
    return indexed
  for entry in (scan_result.get("target_results") or []):
    if not isinstance(entry, dict):
      continue
    target_skill = _normalize_skill_name(entry.get("target_skill"))
    if target_skill and target_skill not in indexed:
      indexed[target_skill] = entry
  return indexed


def _is_skill_entry_actionable(entry):
  if not isinstance(entry, dict):
    return False
  increment_click_result = entry.get("increment_click_result") or {}
  return bool(increment_click_result.get("target"))


def _estimate_skill_cost(skill_name, available_entries, selected_targets):
  prerequisite = _SKILL_GOLD_PREREQUISITES.get(skill_name)
  if not prerequisite:
    return _SKILL_DEFAULT_COST, None
  prerequisite_entry = available_entries.get(prerequisite)
  if prerequisite in selected_targets:
    return _SKILL_DEFAULT_COST, prerequisite
  if _is_skill_entry_actionable(prerequisite_entry):
    return _SKILL_DEFAULT_COST * 2, prerequisite
  return _SKILL_DEFAULT_COST, prerequisite


def _plan_budgeted_skill_targets(context, scan_result=None):
  shortlist = [_normalize_skill_name(item) for item in list((context or {}).get("shopping_list") or []) if _normalize_skill_name(item)]
  current_sp = int((context or {}).get("current_sp") or 0)
  available_entries = _build_skill_entry_index(scan_result)
  remaining_sp = current_sp
  selected_targets = []
  selected_set = set()
  covered_prereqs = set()
  plan = {}

  def _entry(skill_name):
    return available_entries.get(skill_name)

  def _set_plan(skill_name, **extra):
    base = {
      "target_skill": skill_name,
      "available": _is_skill_entry_actionable(_entry(skill_name)),
      "selected": False,
      "estimated_cost": None,
      "remaining_sp_before": None,
      "remaining_sp_after": None,
      "reason": "",
      "covered_by": None,
      "paired_gold": None,
    }
    base.update(extra)
    plan[skill_name] = base

  # Primary pass: shortlist order, but gold skills consume their prerequisite.
  for skill_name in shortlist:
    entry = _entry(skill_name)
    available = _is_skill_entry_actionable(entry)
    if not available:
      _set_plan(skill_name, reason=(entry or {}).get("reason") or "not_actionable_from_preview")
      continue
    if skill_name in covered_prereqs:
      _set_plan(skill_name, reason="covered_by_selected_gold", covered_by=_SKILL_PREREQ_TO_GOLD.get(skill_name))
      continue
    if skill_name in _SKILL_PREREQ_TO_GOLD:
      paired_gold = _SKILL_PREREQ_TO_GOLD[skill_name]
      gold_entry = _entry(paired_gold)
      if _is_skill_entry_actionable(gold_entry):
        _set_plan(skill_name, reason="deferred_to_gold_partner", paired_gold=paired_gold)
        continue
    estimated_cost, prerequisite = _estimate_skill_cost(skill_name, available_entries, selected_set)
    remaining_before = remaining_sp
    if remaining_sp >= estimated_cost:
      selected_targets.append(skill_name)
      selected_set.add(skill_name)
      remaining_sp -= estimated_cost
      _set_plan(
        skill_name,
        selected=True,
        estimated_cost=estimated_cost,
        remaining_sp_before=remaining_before,
        remaining_sp_after=remaining_sp,
        reason="selected",
        covered_by=prerequisite if estimated_cost > _SKILL_DEFAULT_COST else None,
      )
      if estimated_cost > _SKILL_DEFAULT_COST and prerequisite:
        covered_prereqs.add(prerequisite)
        if prerequisite not in plan or plan[prerequisite].get("reason") == "deferred_to_gold_partner":
          _set_plan(prerequisite, reason="covered_by_selected_gold", covered_by=skill_name)
    else:
      _set_plan(
        skill_name,
        estimated_cost=estimated_cost,
        remaining_sp_before=remaining_before,
        remaining_sp_after=remaining_before,
        reason="insufficient_skill_points",
        covered_by=prerequisite if estimated_cost > _SKILL_DEFAULT_COST else None,
      )

  # Fallback pass: buy prerequisite whites alone if their paired gold was skipped and budget remains.
  for skill_name in shortlist:
    entry = _entry(skill_name)
    current = plan.get(skill_name) or {}
    if skill_name not in _SKILL_PREREQ_TO_GOLD:
      continue
    if not _is_skill_entry_actionable(entry):
      continue
    if current.get("selected") or current.get("reason") == "covered_by_selected_gold":
      continue
    paired_gold = _SKILL_PREREQ_TO_GOLD[skill_name]
    if paired_gold in selected_set:
      continue
    if current.get("reason") != "deferred_to_gold_partner":
      continue
    remaining_before = remaining_sp
    if remaining_sp >= _SKILL_DEFAULT_COST:
      selected_targets.append(skill_name)
      selected_set.add(skill_name)
      remaining_sp -= _SKILL_DEFAULT_COST
      _set_plan(
        skill_name,
        selected=True,
        estimated_cost=_SKILL_DEFAULT_COST,
        remaining_sp_before=remaining_before,
        remaining_sp_after=remaining_sp,
        reason="selected_prerequisite_fallback",
        paired_gold=paired_gold,
      )
    else:
      _set_plan(
        skill_name,
        estimated_cost=_SKILL_DEFAULT_COST,
        remaining_sp_before=remaining_before,
        remaining_sp_after=remaining_before,
        reason="insufficient_skill_points",
        paired_gold=paired_gold,
      )

  return {
    "current_sp": current_sp,
    "remaining_sp": remaining_sp,
    "selected_targets": selected_targets,
    "plan_by_target": plan,
  }


def _build_skill_purchase_planned_clicks(context, scan_result=None, budget_plan=None):
  clicks = [
    _planned_click("Open skills menu", template="assets/buttons/skills_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
    _planned_click("Scan skill rows", region_key="SCROLLING_SKILL_SCREEN_BBOX", note="OCR + shortlist match over the visible skill cards"),
  ]

  target_results = list((scan_result or {}).get("target_results") or [])
  actionable_count = 0
  budget_details = (budget_plan or {}).get("plan_by_target") or {}
  if target_results:
    for entry in target_results:
      if not isinstance(entry, dict):
        continue
      target_skill = _normalize_skill_name(entry.get("target_skill") or "unknown")
      candidate = entry.get("candidate") or {}
      match_name = candidate.get("match_name") or target_skill
      match_score = candidate.get("match_score")
      pairing = candidate.get("increment_pairing")
      y_delta = candidate.get("increment_vertical_distance")
      budget_entry = budget_details.get(target_skill) or {}
      is_actionable = bool(budget_entry.get("selected"))
      if is_actionable:
        actionable_count += 1
      label = f"Queue skill: {match_name}" if is_actionable else f"Skip skill: {target_skill}"
      note_parts = [f"target={target_skill}"]
      if match_name != target_skill:
        note_parts.append(f"matched={match_name}")
      if match_score is not None:
        note_parts.append(f"score={match_score}")
      if pairing:
        note_parts.append(f"pair={pairing}")
      if y_delta is not None:
        note_parts.append(f"y_delta={y_delta}px")
      estimated_cost = budget_entry.get("estimated_cost")
      if estimated_cost is not None:
        note_parts.append(f"cost={estimated_cost}")
      remaining_after = budget_entry.get("remaining_sp_after")
      if remaining_after is not None and is_actionable:
        note_parts.append(f"sp_after={remaining_after}")
      covered_by = budget_entry.get("covered_by")
      if covered_by:
        note_parts.append(f"covers={covered_by}" if is_actionable else f"covered_by={covered_by}")
      paired_gold = budget_entry.get("paired_gold")
      if paired_gold and not is_actionable:
        note_parts.append(f"paired_gold={paired_gold}")
      reason = budget_entry.get("reason") or entry.get("reason")
      if reason and reason not in {"dry_run_confirm_detected", "dry_run_complete", "selected"}:
        note_parts.append(f"status={reason}")
      clicks.append(_planned_click(label, note="; ".join(note_parts)))
  else:
    shortlist = list(context.get("shopping_list") or [])
    clicks.append(
      _planned_click(
        "Select matching skill rows",
        template="assets/icons/buy_skill.png",
        region_key="SCROLLING_SKILL_SCREEN_BBOX",
        note=", ".join(shortlist) if shortlist else "Use the configured skill shortlist",
      )
    )

  if actionable_count:
    clicks.append(
      _planned_click(
        "Reopen skills using saved preview positions",
        note="Seek directly to previewed scrollbar positions before falling back to a full scan",
      )
    )
    clicks.append(
      _planned_click(
        "Confirm selected skills",
        template="assets/buttons/confirm_btn.png",
        note=f"{actionable_count} queued skill(s) matched the preview scan",
      )
    )
    clicks.append(_planned_click("Learn selected skills", template="assets/buttons/learn_btn.png"))
  else:
    clicks.append(_planned_click("No safe skill increments queued", note="Scanner will exit skills without confirming a purchase"))
  clicks.append(_planned_click("Exit skills screen", template="assets/buttons/back_btn.png", region_key="SCREEN_BOTTOM_BBOX"))
  return clicks


def _build_skill_purchase_scan_hints(scan_result):
  hints = []
  if not isinstance(scan_result, dict):
    return hints
  for entry in (scan_result.get("target_results") or []):
    if not isinstance(entry, dict):
      continue
    increment_click_result = entry.get("increment_click_result") or {}
    candidate = entry.get("candidate") or {}
    if not increment_click_result.get("target"):
      continue
    if candidate.get("scrollbar_ratio") is None:
      continue
    hints.append(
      {
        "target_skill": entry.get("target_skill"),
        "candidate": {
          "frame_index": candidate.get("frame_index"),
          "scrollbar_ratio": candidate.get("scrollbar_ratio"),
          "match_name": candidate.get("match_name"),
          "match_score": candidate.get("match_score"),
          "increment_pairing": candidate.get("increment_pairing"),
          "increment_vertical_distance": candidate.get("increment_vertical_distance"),
        },
        "reacquire_result": dict(entry.get("reacquire_result") or {}),
      }
    )
  return hints


def _trackblazer_items_require_reassess(items):
  for entry in (items or []):
    if not isinstance(entry, dict):
      continue
    if entry.get("key") == "reset_whistle":
      return True
    if entry.get("usage_group") == "energy":
      return True
  return False


def _trackblazer_training_score(action):
  training_data = _action_value(action, "training_data")
  if not isinstance(training_data, dict):
    return None
  score_tuple = training_data.get("score_tuple")
  if not isinstance(score_tuple, (tuple, list)) or not score_tuple:
    return None
  try:
    return float(score_tuple[0])
  except (TypeError, ValueError):
    return None


def _trackblazer_training_score_threshold():
  return get_training_behavior_strong_training_score_threshold()


def _order_trackblazer_pre_action_items(items):
  ordered_items = list(items or [])
  if not ordered_items:
    return []

  kale_indexes = [index for index, entry in enumerate(ordered_items) if entry.get("key") == "royal_kale_juice"]
  mood_indexes = [index for index, entry in enumerate(ordered_items) if entry.get("usage_group") == "mood"]
  if not kale_indexes or not mood_indexes:
    return ordered_items

  kale_index = kale_indexes[0]
  first_mood_index = mood_indexes[0]
  if kale_index < first_mood_index:
    return ordered_items

  kale_entry = ordered_items.pop(kale_index)
  ordered_items.insert(first_mood_index, kale_entry)
  return ordered_items


def _planned_clicks_for_action(action):
  action_func = _action_func(action)
  pre_action_items = _trackblazer_pre_action_items(action)
  shop_buy_plan = list(action.get("trackblazer_shop_buy_plan") or []) if hasattr(action, "get") else []
  skill_purchase_plan = _skill_purchase_plan(action)
  skill_clicks = list(skill_purchase_plan.get("planned_clicks") or [])

  # Shop purchase clicks (run before main action).
  shop_clicks = []
  if shop_buy_plan:
    shop_clicks.append(
      _planned_click(
        "Open shop for purchases",
        note="Trackblazer shop buy step before the main action",
      )
    )
    for entry in shop_buy_plan:
      item_name = entry.get("display_name") or entry.get("name") or str(entry.get("key", "item")).replace("_", " ").title()
      cost = entry.get("cost")
      cost_label = f" ({cost} coins)" if cost else ""
      shop_clicks.append(
        _planned_click(
          f"Buy {item_name}{cost_label}",
          note=f"policy={entry.get('priority', '?')}; hold {entry.get('held_quantity', '?')}/{entry.get('max_quantity', '?')}",
        )
      )
    shop_clicks.append(
      _planned_click(
        "Confirm shop purchase",
        note="Press confirm to finalize all selected shop items",
      )
    )
    shop_clicks.append(
      _planned_click(
        "Close shop",
        note="Return to lobby after purchase",
      )
    )

  pre_action_clicks = []
  if pre_action_items:
    pre_action_clicks.append(
      _planned_click(
        "Open use-items inventory",
        region_key="SCREEN_BOTTOM_BBOX",
        note="Trackblazer pre-action item step before the main action",
      )
    )
    pre_action_clicks.append(
      _planned_click(
        "Scan inventory item rows",
        region_key="MANT_INVENTORY_ITEMS_REGION",
        note="Pair the planned pre-action items to increment controls",
      )
    )
    for item in pre_action_items:
      pre_action_clicks.append(
        _planned_click(
          f"Increment {item.get('name') or item.get('key') or 'item'}",
          note=item.get("reason") or "Select this item once before the main action",
        )
      )
    pre_action_clicks.append(
      _planned_click(
        "Confirm planned item use",
        note=(
          "In execute mode the bot should commit the planned item use before the main action. "
          "In check-only/preview modes this remains a simulation step."
        ),
      )
    )
    if hasattr(action, "get") and action.get("trackblazer_reassess_after_item_use"):
      pre_action_clicks.append(
        _planned_click(
          "Rescan trainings after item use",
          region_key="GAME_WINDOW_BBOX",
          note="Item use changes board state (whistle reroll or energy reducing failure), so the follow-up action must be re-evaluated",
        )
      )
  if not action_func:
    return skill_clicks + shop_clicks + pre_action_clicks
  if action_func == "do_training":
    training_name = _action_value(action, "training_name")
    return skill_clicks + shop_clicks + pre_action_clicks + [
      _planned_click("Open training menu", "assets/buttons/training_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
      _planned_click(
        f"Select training: {training_name or 'unknown'}",
        target=constants.TRAINING_BUTTON_POSITIONS.get(training_name),
        note="Double-click training slot",
      ),
    ]
  if action_func == "do_rest":
    return skill_clicks + shop_clicks + pre_action_clicks + [
      _planned_click("Click rest button", "assets/buttons/rest_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
      _planned_click("Fallback summer rest button", "assets/buttons/rest_summer_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
    ]
  if action_func == "do_recreation":
    return skill_clicks + shop_clicks + pre_action_clicks + [
      _planned_click("Open recreation menu", "assets/buttons/recreation_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
      _planned_click("Fallback summer recreation button", "assets/buttons/rest_summer_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
    ]
  if action_func == "do_infirmary":
    return skill_clicks + shop_clicks + pre_action_clicks + [_planned_click("Click infirmary button", "assets/buttons/infirmary_btn.png", region_key="SCREEN_BOTTOM_BBOX")]
  if action_func == "do_race":
    race_name = _action_value(action, "race_name")
    race_grade_target = _action_value(action, "race_grade_target")
    prefer_rival_race = bool(_action_value(action, "prefer_rival_race"))
    fallback_non_rival_race = bool(_action_value(action, "fallback_non_rival_race"))
    is_race_day = bool(_action_value(action, "is_race_day"))
    is_trackblazer_climax_race_day = bool(_action_value(action, "trackblazer_climax_race_day"))
    scheduled_race = bool(_action_value(action, "scheduled_race") or _action_value(action, "trackblazer_lobby_scheduled_race"))
    race_template = f"assets/races/{race_name}.png" if race_name and race_name not in ("", "any") else _action_value(action, "race_image_path") or "assets/ui/match_track.png"
    if is_race_day and is_trackblazer_climax_race_day:
      clicks = skill_clicks + shop_clicks + pre_action_clicks + [
        _planned_click(
          "Click forced Climax race button",
          constants.TRACKBLAZER_RACE_TEMPLATES.get("climax_race_button"),
          note="Race-day screen replaces the normal training/rest/races buttons.",
        ),
        _planned_click(
          "Confirm race-day prompt",
          "assets/buttons/ok_btn.png",
          region_key="GAME_WINDOW_BBOX",
          note="Advance from the race-day prompt after entering the forced race.",
        ),
        _planned_click("Confirm race", "assets/buttons/race_btn.png"),
        _planned_click("Fallback BlueStacks confirm", "assets/buttons/bluestacks/race_btn.png"),
      ]
      return clicks
    clicks = skill_clicks + shop_clicks + pre_action_clicks + [
      _planned_click("Open race menu", "assets/buttons/races_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
      _planned_click(
        "Check consecutive-race warning",
        constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive"),
        region_key="GAME_WINDOW_BBOX",
        note=(
          "If this warning appears after clicking Races, continue with OK for scheduled races; "
          "otherwise follow the race gate before opening the race list."
          if scheduled_race else
          "Fallback non-rival race: cancel and revert to training if consecutive-race warning appears."
          if fallback_non_rival_race else
          "If this warning appears after clicking Races, decide whether to continue with OK "
          "or back out with Cancel before opening the race list."
        ),
      ),
      _planned_click(
        "Continue through warning (OK)",
        constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive_ok") or "assets/buttons/ok_btn.png",
        region_key="GAME_WINDOW_BBOX",
        note=(
          "Scheduled race override: click the warning dialog OK and continue into the race list."
          if scheduled_race else
          "Use the warning-dialog OK when the race gate accepts a third consecutive race."
        ),
      ),
      _planned_click(
        "Fallback warning OK",
        "assets/buttons/ok_btn.png",
        region_key="GAME_WINDOW_BBOX",
        note="Generic fallback if the warning-specific OK template is not matched.",
      ),
      _planned_click(
        "Back out from warning (Cancel)",
        "assets/buttons/cancel_btn.png",
        region_key="GAME_WINDOW_BBOX",
        note=(
          "Not expected for scheduled races; only use if the dialog must be dismissed back to lobby."
          if scheduled_race else
          "Use this when the race gate rejects a third consecutive race and returns to lobby."
        ),
      ),
      _planned_click(
        "Scan/select race entry",
        race_template,
        region_key="RACE_LIST_BOX_BBOX",
        note=(
          f"target={race_grade_target or 'any'}"
          + ("; prefer rival row when present" if prefer_rival_race else "")
        ),
      ),
      _planned_click("Confirm race", "assets/buttons/race_btn.png"),
      _planned_click("Fallback BlueStacks confirm", "assets/buttons/bluestacks/race_btn.png"),
    ]
    return clicks
  if action_func == "buy_skill":
    return skill_clicks + shop_clicks + pre_action_clicks + [
      _planned_click("Open skills menu", "assets/buttons/skills_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
      _planned_click("Scan skill rows", region_key="SCROLLING_SKILL_SCREEN_BBOX", note="OCR and template scan only"),
      _planned_click("Confirm selected skills", "assets/buttons/confirm_btn.png"),
      _planned_click("Learn selected skills", "assets/buttons/learn_btn.png"),
      _planned_click("Exit skill screen", "assets/buttons/back_btn.png", region_key="SCREEN_BOTTOM_BBOX"),
    ]
  if action_func == "check_inventory":
    return [
      _planned_click("Open use-items inventory", region_key="SCREEN_BOTTOM_BBOX", note="Locate the Trackblazer use-items entry button"),
      _planned_click("Scan inventory item rows", region_key="MANT_INVENTORY_ITEMS_REGION", note="OCR and native-scale template scan only"),
      _planned_click("Verify inventory controls", region_key="GAME_WINDOW_BBOX", note="Check use/close button visibility"),
      _planned_click("Close inventory", note="Dismiss the inventory overlay after scan"),
    ]
  if action_func == "execute_training_items":
    commit_mode = _action_value(action, "commit_mode") or "dry_run"
    clicks = [
      _planned_click("Open use-items inventory", region_key="SCREEN_BOTTOM_BBOX", note="Locate the Trackblazer use-items entry button"),
      _planned_click("Scan inventory item rows", region_key="MANT_INVENTORY_ITEMS_REGION", note="Pair items to increment controls"),
    ]
    if commit_mode == "dry_run":
      clicks.append(_planned_click("Detect controls (no increment clicks)", note="Simulated — no destructive clicks in dry_run"))
      clicks.append(_planned_click("Close inventory", note=f"commit_mode={commit_mode}"))
    else:
      clicks.append(_planned_click("Increment Vita 65", note="Select one Vita 65"))
      clicks.append(_planned_click("Increment Reset Whistle", note="Select one Reset Whistle"))
      clicks.append(_planned_click("Verify confirm-use controls", note="Ensure confirm/cancel controls are available"))
      clicks.append(_planned_click("Press confirm-use", note=f"commit_mode={commit_mode}"))
    return clicks
  if action_func == "check_shop":
    return pre_action_clicks + [
      _planned_click("Open Trackblazer shop", region_key="GAME_WINDOW_BBOX", note="Locate the shop entry button"),
      _planned_click("Scan shop coin display", region_key="MANT_SHOP_COIN_REGION", note="OCR coin count"),
      _planned_click("Scan shop item rows", region_key="GAME_WINDOW_BBOX", note="Template scan for visible shop stock"),
      _planned_click("Close shop", note="Dismiss the shop overlay after scan"),
    ]
  return skill_clicks + pre_action_clicks


def _build_trackblazer_planned_actions(state_obj, action):
  if (constants.SCENARIO_NAME or "default") not in ("mant", "trackblazer"):
    return {}

  inventory = state_obj.get("trackblazer_inventory") or {}
  inventory_summary = state_obj.get("trackblazer_inventory_summary") or {}
  inventory_flow = state_obj.get("trackblazer_inventory_flow") or {}
  pre_shop_inventory_summary = state_obj.get("trackblazer_inventory_pre_shop_summary") or inventory_summary
  pre_shop_inventory_flow = state_obj.get("trackblazer_inventory_pre_shop_flow") or inventory_flow
  shop_items = list(state_obj.get("trackblazer_shop_items") or [])
  shop_summary = state_obj.get("trackblazer_shop_summary") or {}
  shop_flow = state_obj.get("trackblazer_shop_flow") or {}
  held_quantities = pre_shop_inventory_summary.get("held_quantities") or {}
  # Use pre-shop inventory data for item-use planning so it matches the
  # inventory items shown in the planned-actions display.  The post-shop
  # refresh may have overwritten state_obj with empty data if it failed to
  # reopen the inventory screen.
  pre_shop_inventory = state_obj.get("trackblazer_inventory_pre_shop") or inventory
  plan_state = dict(state_obj)
  plan_state["trackblazer_inventory"] = pre_shop_inventory
  plan_state["trackblazer_inventory_summary"] = pre_shop_inventory_summary
  inventory_items_detected = list(pre_shop_inventory_summary.get("items_detected") or [])
  if not inventory_items_detected and isinstance(pre_shop_inventory, dict):
    for item_key, item_entry in pre_shop_inventory.items():
      if not isinstance(item_entry, dict):
        continue
      held_quantity = item_entry.get("held_quantity")
      detected = bool(item_entry.get("detected"))
      try:
        held_quantity = int(held_quantity)
      except (TypeError, ValueError):
        held_quantity = 0
      if detected or held_quantity > 0:
        inventory_items_detected.append(item_key)
  effective_shop_items = get_effective_shop_items(
    policy=config.TRACKBLAZER_SHOP_POLICY,
    year=state_obj.get("year"),
    turn=state_obj.get("turn"),
  )
  action_shop_buy_plan = list(action.get("trackblazer_shop_buy_plan") or []) if hasattr(action, "get") else []
  if action_shop_buy_plan:
    would_buy = action_shop_buy_plan
  else:
    would_buy = _trackblazer_shop_buy_candidates(
      effective_shop_items,
      shop_items=shop_items,
      shop_summary={
        **(shop_summary or {}),
        "year": state_obj.get("year"),
        "turn": state_obj.get("turn"),
      },
      held_quantities=held_quantities,
      limit=8,
    )
  if would_buy:
    plan_state = _project_trackblazer_inventory_for_planned_buys(plan_state, would_buy)

  item_use_plan = plan_item_usage(
    policy=getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None),
    state_obj=plan_state,
    action=action,
    limit=8,
  )

  would_use = list(item_use_plan.get("candidates") or [])
  deferred_use = list(item_use_plan.get("deferred") or [])
  planned_pre_action_items = _trackblazer_pre_action_items(action)
  if planned_pre_action_items:
    planned_item_keys = {entry.get("key") for entry in planned_pre_action_items if entry.get("key")}
    if hasattr(action, "get") and action.get("trackblazer_reassess_after_item_use"):
      deferred_keys = {entry.get("key") for entry in deferred_use if isinstance(entry, dict)}
      for entry in would_use:
        item_key = entry.get("key")
        if not item_key or item_key in planned_item_keys or item_key in deferred_keys:
          continue
        deferred_entry = dict(entry)
        existing_reason = deferred_entry.get("reason") or ""
        deferred_entry["reason"] = (
          f"{existing_reason}; deferred until post-whistle reassess"
          if existing_reason else
          "deferred until post-whistle reassess"
        )
        deferred_use.append(deferred_entry)
    would_use = list(planned_pre_action_items)
  actionable_items = list(pre_shop_inventory_summary.get("actionable_items") or [])

  inventory_status = "unknown"
  if pre_shop_inventory_flow.get("opened") or pre_shop_inventory_flow.get("already_open"):
    inventory_status = "scanned"
  elif pre_shop_inventory_flow.get("skipped"):
    inventory_status = "skipped"

  shop_status = "unknown"
  if shop_flow.get("entered"):
    shop_status = "scanned" if shop_flow.get("closed") else "open_failed_to_close"
  elif shop_flow:
    shop_status = "skipped" if shop_flow.get("reason") else "failed"

  race_decision = action.get("trackblazer_race_decision") if hasattr(action, "get") else {}
  rival_scout = action.get("rival_scout") if hasattr(action, "get") else {}
  race_planned = {}
  race_check = {}
  race_entry_gate = {}
  race_scout_planned = {}
  rival_indicator_detected = state_obj.get("rival_indicator_detected")
  forced_climax_race_day = bool(state_obj.get("trackblazer_climax_race_day"))
  scheduled_race = bool(hasattr(action, "get") and (action.get("scheduled_race") or action.get("trackblazer_lobby_scheduled_race")))
  lobby_scheduled_race = bool(state_obj.get("trackblazer_lobby_scheduled_race") or (hasattr(action, "get") and action.get("trackblazer_lobby_scheduled_race")))
  scheduled_race_source = None
  if lobby_scheduled_race:
    scheduled_race_source = "lobby_button"
  elif scheduled_race:
    scheduled_race_source = "config_schedule"
  if rival_indicator_detected is not None or forced_climax_race_day or scheduled_race:
    race_check = {
      "phase": "collecting_race_state",
      "sub_phase": (
        "check_scheduled_race"
        if scheduled_race else
        ("check_rival_indicator" if not forced_climax_race_day else "check_forced_race_day")
      ),
      "method": (
        "scheduled_race_signal"
        if scheduled_race else
        ("lobby_race_button_indicator" if not forced_climax_race_day else "climax_race_day_banner_or_button")
      ),
      "rival_indicator_detected": bool(rival_indicator_detected),
      "forced_climax_race_day": forced_climax_race_day,
      "forced_climax_race_day_banner": bool(state_obj.get("trackblazer_climax_race_day_banner")),
      "forced_climax_race_day_button": bool(state_obj.get("trackblazer_climax_race_day_button")),
      "scheduled_race": scheduled_race,
      "scheduled_race_source": scheduled_race_source,
      "lobby_scheduled_race_detected": lobby_scheduled_race,
      "scout_required": bool(hasattr(action, "get") and _action_func(action) == "do_race" and action.get("prefer_rival_race")),
    }
  if isinstance(race_decision, dict) and race_decision:
    race_info = race_decision.get("race_tier_info") or {}
    race_planned = {
      "should_race": race_decision.get("should_race"),
      "reason": race_decision.get("reason"),
      "training_total_stats": race_decision.get("training_total_stats"),
      "training_score": race_decision.get("training_score"),
      "is_summer": race_decision.get("is_summer"),
      "g1_forced": race_decision.get("g1_forced"),
      "prefer_rival_race": race_decision.get("prefer_rival_race"),
      "fallback_non_rival_race": race_decision.get("fallback_non_rival_race"),
      "prefer_rest_over_weak_training": race_decision.get("prefer_rest_over_weak_training"),
      "forced_race_day": race_decision.get("forced_race_day"),
      "race_tier_target": race_decision.get("race_tier_target"),
      "race_name": race_decision.get("race_name"),
      "race_available": race_decision.get("race_available"),
      "rival_indicator": race_decision.get("rival_indicator"),
      "available_grades": race_info.get("available_grades"),
      "best_grade": race_info.get("best_grade"),
      "race_count": race_info.get("race_count"),
      "scheduled_race": scheduled_race,
      "scheduled_race_source": scheduled_race_source,
    }
  if scheduled_race:
    race_entry_gate = {
      "opens_from_lobby": True,
      "consecutive_warning_template": constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive"),
      "consecutive_warning_ok_template": constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive_ok"),
      "warning_meaning": "This scheduled race would become the third consecutive race",
      "ok_action": "continue_to_race_list",
      "cancel_action": "return_to_lobby",
      "expected_branch": "continue_to_race_list",
      "scheduled_race": True,
      "scheduled_race_source": scheduled_race_source,
      "force_accept_warning": True,
    }
  elif isinstance(race_decision, dict) and race_decision and (_action_func(action) == "do_race" or race_decision.get("should_race")):
    # Decide expected consecutive-race warning behavior.
    # The race gate already filters weak signals, so should_race=True means
    # the signal was strong enough to justify racing. G1 or should_race both
    # continue; otherwise back out to the lobby.
    if race_decision.get("fallback_non_rival_race"):
      expected_branch = "return_to_lobby"
    elif race_decision.get("g1_forced") or race_decision.get("should_race"):
      expected_branch = "continue_to_race_list"
    else:
      expected_branch = "return_to_lobby"
    race_entry_gate = {
      "opens_from_lobby": True,
      "consecutive_warning_template": constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive"),
      "consecutive_warning_ok_template": constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive_ok"),
      "warning_meaning": "This race would become the third consecutive race",
      "ok_action": "continue_to_race_list",
      "cancel_action": "return_to_lobby",
      "expected_branch": expected_branch,
    }
  if isinstance(rival_scout, dict) and rival_scout:
    race_scout_planned = {
      "phase": "scouting_rival_race",
      "executed": True,
      "rival_found": rival_scout.get("rival_found"),
      "selected_race_name": rival_scout.get("race_name"),
      "selected_match_count": rival_scout.get("match_count"),
      "selected_grade": rival_scout.get("grade"),
      "reverted_to_training": bool(rival_scout.get("rival_found") is False),
    }
  elif hasattr(action, "get") and action.get("prefer_rival_race"):
    race_scout_planned = {
      "phase": "scouting_rival_race",
      "executed": False,
      "status": "pending_execute_commit",
      "reason": "Full rival scout only runs after commit.",
    }

  return {
    "race_check": race_check,
    "race_decision": race_planned,
    "race_entry_gate": race_entry_gate,
    "race_scout": race_scout_planned,
    "inventory_scan": {
      "status": inventory_status,
      "reason": pre_shop_inventory_flow.get("reason") or "",
      "button_visible": pre_shop_inventory_flow.get("use_training_items_button_visible"),
      "items_detected": inventory_items_detected,
      "held_quantities": held_quantities,
      "actionable_items": actionable_items,
    },
    "would_use": would_use,
    "would_use_context": item_use_plan.get("context") or {},
    "deferred_use": deferred_use,
    "shop_scan": {
      "status": shop_status,
      "reason": shop_flow.get("reason") or "",
      "shop_coins": shop_summary.get("shop_coins", state_obj.get("shop_coins")),
      "items_detected": shop_summary.get("items_detected") or shop_items,
      "not_purchasable": sorted(
        set(shop_summary.get("items_detected") or shop_items or [])
        - set(shop_summary.get("purchasable_items") or shop_items or [])
      ),
    },
    "would_buy": would_buy,
  }


def _trackblazer_shop_buy_candidates(effective_shop_items, shop_items=None, shop_summary=None, held_quantities=None, limit=8):
  detected_shop_keys = set(shop_items or (shop_summary or {}).get("items_detected") or [])
  held_quantities = held_quantities or {}
  shop_coins = int((shop_summary or {}).get("shop_coins") or 0)
  if shop_coins == 0:
    return []
  dynamic_limits = get_dynamic_shop_limits(
    held_quantities=held_quantities,
    year=(shop_summary or {}).get("year"),
    turn=(shop_summary or {}).get("turn"),
  )
  budget_known = shop_coins > 0
  remaining_coins = shop_coins if budget_known else 0
  would_buy = []
  planned_counts = {}
  planned_family_counts = {}
  for item in effective_shop_items:
    item_key = item.get("key")
    if item_key not in detected_shop_keys:
      continue
    if item.get("effective_priority") == "NEVER":
      continue
    held_quantity = int(held_quantities.get(item_key) or 0)
    max_quantity = int(item.get("max_quantity") or 0)
    dynamic_limit = dynamic_limits.get(item_key) or {}
    if dynamic_limit.get("block_purchase"):
      continue
    planned_for_item = int(planned_counts.get(item_key) or 0)
    dynamic_max_total = dynamic_limit.get("max_total")
    if dynamic_max_total is not None and held_quantity + planned_for_item >= int(dynamic_max_total):
      continue
    family_key = dynamic_limit.get("family_key")
    family_max_total = dynamic_limit.get("family_max_total")
    if family_key and family_max_total is not None:
      family_planned = int(planned_family_counts.get(family_key) or 0)
      family_total_held = int(dynamic_limit.get("family_total_held") or 0)
      if family_total_held + family_planned >= int(family_max_total):
        continue
    remaining_capacity = max(0, max_quantity - held_quantity)
    if max_quantity > 0 and remaining_capacity <= 0:
      continue
    cost = int(item.get("cost") or 0)
    if budget_known and cost > remaining_coins:
      continue
    reason_parts = [f"policy={item.get('effective_priority')}"]
    timing_rules = item.get("active_timing_rules") or []
    if timing_rules:
      rule = timing_rules[0]
      reason_parts.append(rule.get("label") or "timing override")
      if rule.get("note"):
        reason_parts.append(rule["note"])
    if dynamic_limit.get("reason"):
      reason_parts.append(dynamic_limit["reason"])
    elif item.get("policy_notes"):
      reason_parts.append(item["policy_notes"])
    if max_quantity > 0:
      reason_parts.append(f"hold {held_quantity}/{max_quantity}")
    reason_parts.append(f"cost {cost}")
    would_buy.append(
      {
        "key": item_key,
        "name": item.get("display_name") or str(item_key or "").replace("_", " ").title(),
        "priority": item.get("effective_priority"),
        "cost": cost,
        "held_quantity": held_quantity,
        "max_quantity": max_quantity,
        "reason": "; ".join(part for part in reason_parts if part),
      }
    )
    if budget_known:
      remaining_coins -= cost
    planned_counts[item_key] = planned_for_item + 1
    if family_key:
      planned_family_counts[family_key] = int(planned_family_counts.get(family_key) or 0) + 1
  return would_buy[: max(0, int(limit))]


def _attach_trackblazer_pre_action_item_plan(state_obj, action):
  if (constants.SCENARIO_NAME or "default") not in ("mant", "trackblazer"):
    return action
  if not hasattr(action, "get") or not hasattr(action, "__setitem__"):
    return action

  effective_shop_items = get_effective_shop_items(
    policy=config.TRACKBLAZER_SHOP_POLICY,
    year=state_obj.get("year"),
    turn=state_obj.get("turn"),
  )
  held_quantities = (state_obj.get("trackblazer_inventory_summary") or {}).get("held_quantities") or {}
  shop_buy_plan = _trackblazer_shop_buy_candidates(
    effective_shop_items,
    shop_items=state_obj.get("trackblazer_shop_items"),
    shop_summary={
      **(state_obj.get("trackblazer_shop_summary") or {}),
      "year": state_obj.get("year"),
      "turn": state_obj.get("turn"),
    },
    held_quantities=held_quantities,
    limit=8,
  )
  action["trackblazer_shop_buy_plan"] = shop_buy_plan

  planning_state = (
    _project_trackblazer_inventory_for_planned_buys(state_obj, shop_buy_plan)
    if shop_buy_plan else
    state_obj
  )
  item_use_plan = plan_item_usage(
    policy=getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None),
    state_obj=planning_state,
    action=action,
    limit=8,
  )
  candidates = list(item_use_plan.get("candidates") or [])
  has_whistle = any(entry.get("key") == "reset_whistle" for entry in candidates)
  if has_whistle:
    # Reset Whistle reshuffles the board. Other items (energy, burst, stat)
    # depend on the post-whistle board, so defer them to the reassess pass
    # where they'll be re-planned against the new training state.
    candidates = [entry for entry in candidates if entry.get("key") == "reset_whistle"]
  elif any(entry.get("usage_group") == "energy" for entry in candidates):
    # Energy items change fail rate, so a reassess will happen. Defer burst
    # items (megaphones, stat-specific burst) to the reassess pass — they
    # should only be committed once we've confirmed the training is still
    # viable after the energy change.
    _BURST_GROUPS = ("training_burst", "training_burst_specific")
    candidates = [entry for entry in candidates if entry.get("usage_group") not in _BURST_GROUPS]
  candidates = _order_trackblazer_pre_action_items(candidates)
  item_context = item_use_plan.get("context") or {}
  action["trackblazer_pre_action_items"] = candidates
  action["trackblazer_item_use_context"] = item_context
  # Reassess only when the final planned item list actually changes the board
  # in a way that requires a fresh training scan. Charm-only failure bypass is
  # action-local and should proceed directly into the selected training.
  action["trackblazer_reassess_after_item_use"] = _trackblazer_items_require_reassess(candidates)
  return action


def _skill_purchase_preview_key(current_action_count, context):
  return (
    int(current_action_count),
    bool((context or {}).get("scheduled_g1_race")),
    tuple((context or {}).get("shopping_list") or []),
  )


def _attach_skill_purchase_plan(state_obj, action, current_action_count, race_check=False):
  if not isinstance(state_obj, dict):
    return "skipped"
  if not hasattr(action, "get") or not hasattr(action, "__setitem__"):
    return "skipped"

  existing_key = state_obj.get("skill_purchase_preview_key")
  existing_plan = state_obj.get("skill_purchase_plan") or {}
  if existing_key and existing_key[0] == int(current_action_count) and existing_plan:
    action["skill_purchase_plan"] = existing_plan
    action["skill_purchase_context"] = existing_plan.get("context") or {}
    return "attached"

  context = get_skill_purchase_context(state_obj, current_action_count, race_check=race_check, action=action)
  state_obj["skill_purchase_check"] = {
    **get_skill_purchase_check_state(),
    **context,
  }

  _action_option_pop(action, "skill_purchase_plan")
  _action_option_pop(action, "skill_purchase_context")

  if not context.get("should_check"):
    state_obj.pop("skill_purchase_plan", None)
    state_obj.pop("skill_purchase_preview_key", None)
    return "skipped"

  preview_result = collect_skill_purchase(
    skill_shortlist=context.get("shopping_list"),
    trigger="automatic_preview",
    dry_run=True,
  )
  preview_flow = preview_result.get("skill_purchase_flow") or {}
  preview_scan = preview_result.get("skill_purchase_scan") or {}
  state_obj["skill_purchase_flow"] = preview_flow
  state_obj["skill_purchase_scan"] = preview_scan

  budget_plan = _plan_budgeted_skill_targets(context, preview_scan)
  selected_targets = list(budget_plan.get("selected_targets") or [])
  preview_has_actionable_targets = bool(selected_targets)
  if preview_flow.get("scanned") and preview_has_actionable_targets:
    mark_skill_purchase_checked(
      current_action_count,
      selected_race=bool(context.get("scheduled_g1_race")),
    )
    state_obj["skill_purchase_check"] = {
      **get_skill_purchase_check_state(),
      **context,
      "reason": "Skill scan complete. Purchase is queued before the main action.",
    }
  elif preview_flow.get("scanned"):
    state_obj["skill_purchase_check"] = {
      **get_skill_purchase_check_state(),
      **context,
      "reason": "Skill scan complete. No affordable safe skill increments were queued.",
    }
  else:
    state_obj["skill_purchase_check"] = {
      **get_skill_purchase_check_state(),
      **context,
      "reason": preview_flow.get("reason") or context.get("reason"),
    }

  preview_key = _skill_purchase_preview_key(current_action_count, context)
  if preview_flow.get("opened") and not preview_flow.get("closed") and not (preview_flow.get("open_result") or {}).get("already_open"):
    state_obj.pop("skill_purchase_plan", None)
    state_obj.pop("skill_purchase_preview_key", None)
    return "failed"
  if not preview_flow.get("scanned") or not preview_has_actionable_targets:
    state_obj.pop("skill_purchase_plan", None)
    state_obj.pop("skill_purchase_preview_key", None)
    return "skipped"

  plan = {
    "context": context,
    "target_skills": selected_targets,
    "budget_plan": budget_plan,
    "planned_clicks": _build_skill_purchase_planned_clicks(context, preview_scan, budget_plan=budget_plan),
    "scan_hints": _build_skill_purchase_scan_hints(
      {
        **(preview_scan or {}),
        "target_results": [
          entry for entry in list((preview_scan or {}).get("target_results") or [])
          if _normalize_skill_name((entry or {}).get("target_skill")) in set(selected_targets)
        ],
      }
    ),
    "preview_key": preview_key,
  }
  state_obj["skill_purchase_plan"] = plan
  state_obj["skill_purchase_preview_key"] = preview_key
  action["skill_purchase_plan"] = plan
  action["skill_purchase_context"] = context
  return "attached"


def _run_skill_purchase_plan(state_obj, action, current_action_count):
  plan = _skill_purchase_plan(action)
  context = plan.get("context") or {}
  if not plan or not context.get("should_check"):
    return {"status": "skipped"}

  purchase_result = collect_skill_purchase(
    skill_shortlist=plan.get("target_skills") or context.get("shopping_list"),
    trigger="automatic_execute",
    dry_run=False,
    target_hints=plan.get("scan_hints"),
  )
  purchase_flow = purchase_result.get("skill_purchase_flow") or {}
  purchase_scan = purchase_result.get("skill_purchase_scan") or {}
  state_obj["skill_purchase_flow"] = purchase_flow
  state_obj["skill_purchase_scan"] = purchase_scan
  state_obj["skill_purchase_check"] = {
    **get_skill_purchase_check_state(),
    **context,
    "reason": purchase_flow.get("reason") or context.get("reason"),
  }

  target_results = list(purchase_scan.get("target_results") or [])
  purchased_any = any((entry.get("increment_click_result") or {}).get("target") for entry in target_results)
  if purchased_any:
    update_skill_action_count(current_action_count)
    state_obj["skill_purchase_check"] = {
      **get_skill_purchase_check_state(),
      **context,
      "reason": purchase_flow.get("reason") or "Skill purchase finalized.",
    }

  if purchase_flow.get("opened") and not purchase_flow.get("closed") and not (purchase_flow.get("open_result") or {}).get("already_open"):
    return {
      "status": "failed",
      "result": purchase_result,
      "reason": purchase_flow.get("reason") or "skill_purchase_left_page_open",
    }

  return {
    "status": "executed",
    "result": purchase_result,
    "reason": purchase_flow.get("reason") or purchase_scan.get("reason") or "skill_purchase_complete",
  }


def _run_trackblazer_pre_action_items(state_obj, action, commit_mode="full"):
  """Run Trackblazer pre-action item flow with the given commit_mode.

  commit_mode="full" — production execute: increments + confirm + followup.
  commit_mode="dry_run" — non-destructive simulation: opens, scans, detects
    controls, then closes without increment clicks or confirm.
  """
  planned_items = _trackblazer_pre_action_items(action)
  if not planned_items:
    return {"status": "skipped"}

  # Re-scan inventory immediately before item use so execute/dry-run flows do
  # not rely on a stale per-turn cache when choosing accompanying items.
  state_obj = collect_trackblazer_inventory(
    state_obj,
    allow_open_non_execute=True,
    trigger="pre_action_refresh",
  )
  if _trackblazer_inventory_flow_cacheable(state_obj.get("trackblazer_inventory_flow")):
    _cache_trackblazer_inventory(state_obj, turn_key=action_count)
  else:
    _invalidate_trackblazer_inventory_cache()
  _attach_trackblazer_pre_action_item_plan(state_obj, action)
  planned_items = _trackblazer_pre_action_items(action)
  if not planned_items:
    return {
      "status": "skipped",
      "reason": "trackblazer_pre_action_items_cleared_after_refresh",
    }

  from scenarios.trackblazer import execute_training_items

  item_keys = [entry.get("key") for entry in planned_items if entry.get("key")]
  if not item_keys:
    return {"status": "skipped"}

  result = execute_training_items(item_keys, trigger="automatic", commit_mode=commit_mode)
  flow = result.get("trackblazer_inventory_flow") or {}
  state_obj["trackblazer_inventory"] = result.get("trackblazer_inventory")
  state_obj["trackblazer_inventory_summary"] = result.get("trackblazer_inventory_summary")
  state_obj["trackblazer_inventory_controls"] = result.get("trackblazer_inventory_controls")
  state_obj["trackblazer_inventory_flow"] = flow
  if result.get("success") and _trackblazer_inventory_flow_cacheable(flow):
    _cache_trackblazer_inventory(state_obj, turn_key=action_count)
  else:
    _invalidate_trackblazer_inventory_cache()

  if commit_mode == "dry_run":
    return {
      "status": "simulated",
      "result": result,
      "reason": flow.get("reason") or "trackblazer_pre_action_items_simulated",
    }

  if not result.get("success"):
    return {
      "status": "failed",
      "result": result,
      "reason": flow.get("reason") or "trackblazer_pre_action_items_failed",
    }

  if not flow.get("graceful_noop"):
    _apply_trackblazer_used_items_to_state(state_obj, item_keys)
    _cache_trackblazer_inventory(state_obj, turn_key=action_count)

  if action.get("trackblazer_reassess_after_item_use"):
    return {
      "status": "reassess",
      "result": result,
      "reason": "trackblazer_item_use_requires_reassessment",
    }

  return {
    "status": "executed",
    "result": result,
    "reason": flow.get("reason") or "trackblazer_pre_action_items_applied",
  }


def _trackblazer_action_failure_should_block_retry(state_obj, action):
  if (constants.SCENARIO_NAME or "default") not in ("mant", "trackblazer"):
    return False
  if not hasattr(action, "get"):
    return False

  planned_items = list(action.get("trackblazer_pre_action_items") or [])
  inventory_flow = state_obj.get("trackblazer_inventory_flow") or {}
  inventory_reason = str(inventory_flow.get("reason") or "").strip()
  if planned_items and inventory_reason in {
    "failed_to_open_inventory",
    "failed_to_close_inventory",
    "inventory_did_not_close_after_confirm",
    "required_items_not_actionable",
  }:
    return True

  shop_flow = state_obj.get("trackblazer_shop_flow") or {}
  if shop_flow.get("entered") and not shop_flow.get("closed"):
    return True

  return False

_LOBBY_ANCHOR_TEMPLATES = (
  "assets/buttons/training_btn.png",
  "assets/buttons/rest_btn.png",
  "assets/buttons/rest_summer_btn.png",
  "assets/buttons/recreation_btn.png",
  "assets/buttons/races_btn.png",
)

_POST_ACTION_GENERIC_ADVANCE_TEMPLATES = (
  ("next2", "assets/buttons/next2_btn.png", constants.GAME_WINDOW_BBOX),
  ("next", "assets/buttons/next_btn.png", constants.GAME_WINDOW_BBOX),
  ("ok_2_btn", "assets/buttons/ok_2_btn.png", constants.GAME_WINDOW_BBOX),
  ("retry", "assets/buttons/retry_btn.png", constants.GAME_WINDOW_BBOX),
  ("close", "assets/buttons/close_btn.png", constants.GAME_WINDOW_BBOX),
  ("view_results", "assets/buttons/view_results.png", constants.SCREEN_BOTTOM_BBOX),
  ("back", "assets/buttons/back_btn.png", constants.SCREEN_BOTTOM_BBOX),
  ("cancel", "assets/buttons/cancel_btn.png", constants.GAME_WINDOW_BBOX),
)


def _is_stable_career_lobby_screen(screenshot=None, threshold=0.8):
  screenshot = screenshot if screenshot is not None else device_action.screenshot()
  stable_anchor_counts = _detect_stable_career_screen_anchors(screenshot, threshold=threshold)
  return _has_stable_career_screen(stable_anchor_counts), stable_anchor_counts


def _trackblazer_pre_action_ready_state(screenshot=None):
  result = {
    "ready": False,
    "source": "",
    "forced_climax_race_day": False,
    "climax_detection": None,
  }
  if (constants.SCENARIO_NAME or "") not in ("mant", "trackblazer"):
    return result

  from scenarios.trackblazer import inspect_climax_race_day_detection

  screenshot = screenshot if screenshot is not None else device_action.screenshot(region_ltrb=constants.SCREEN_BOTTOM_BBOX)
  detection = inspect_climax_race_day_detection(screenshot=screenshot, log_result=False)
  result["climax_detection"] = detection
  result["forced_climax_race_day"] = bool(detection.get("detected"))
  if result["forced_climax_race_day"]:
    result["ready"] = True
    result["source"] = "forced_climax_race_day"
  return result


def _update_post_action_resolution_snapshot(
  state_obj,
  action,
  message,
  sub_phase,
  popup_type="",
  deferred_work=None,
  reasoning_notes=None,
  status="active",
):
  bot.update_post_action_resolution(
    active=True,
    source_action=_action_func(action),
    sub_phase=sub_phase,
    popup_type=str(popup_type or ""),
    deferred_work=list(deferred_work or []),
    status=status,
  )
  update_operator_snapshot(
    state_obj,
    action,
    phase=PHASE_POST_ACTION_RESOLUTION,
    status=status,
    message=message,
    reasoning_notes=reasoning_notes,
    sub_phase=sub_phase,
  )


def _handle_trackblazer_scheduled_race_popup(state_obj, action):
  from core.actions import start_race

  _inv_scale = 1.0 / device_action.GLOBAL_TEMPLATE_SCALING
  screenshot = device_action.screenshot()
  sched_race_banner = device_action.match_template(
    "assets/trackblazer/lobby_scheduled_race_available.png",
    screenshot,
    threshold=0.8,
    template_scaling=_inv_scale,
  )
  if not sched_race_banner:
    return {
      "detected": False,
      "handled": False,
      "popup_type": "scheduled_race_popup",
      "reason": "banner_not_found",
      "deferred_work": [],
    }

  _update_post_action_resolution_snapshot(
    state_obj,
    action,
    message="Trackblazer scheduled race popup detected.",
    sub_phase=SUB_PHASE_RESOLVE_SCHEDULED_RACE_POPUP,
    popup_type="scheduled_race_popup",
  )
  info("[TB_POST] Scheduled race popup detected during post-action resolution.")
  bot.push_debug_history({
    "event": "template_match",
    "asset": "lobby_scheduled_race_available.png",
    "result": "found",
    "context": "post_action_resolution",
  })

  race_btn = device_action.match_template(
    "assets/trackblazer/lobby_scheduled_race_race.png",
    screenshot,
    threshold=0.7,
    template_scaling=_inv_scale,
  )
  if not race_btn:
    warning("[TB_POST] Scheduled race popup detected but the popup Race button was not matched.")
    bot.push_debug_history({
      "event": "template_match",
      "asset": "lobby_scheduled_race_race.png",
      "result": "not_found",
      "context": "post_action_resolution",
    })
    return {
      "detected": True,
      "handled": False,
      "popup_type": "scheduled_race_popup",
      "reason": "popup_race_button_not_found",
      "deferred_work": [],
    }

  x, y, w, h = race_btn[0]
  device_action.click(target=(x + w // 2, y + h // 2), text="Clicked scheduled race Race button on popup.")
  bot.push_debug_history({
    "event": "click",
    "asset": "lobby_scheduled_race_race.png",
    "result": "clicked",
    "context": "post_action_resolution",
  })
  sleep(1.2)

  consecutive_cancel_btn = device_action.locate(
    "assets/buttons/cancel_btn.png",
    min_search_time=get_secs(1),
    region_ltrb=constants.GAME_WINDOW_BBOX,
  )
  if consecutive_cancel_btn:
    _update_post_action_resolution_snapshot(
      state_obj,
      action,
      message="Consecutive-race warning detected while resolving scheduled race popup.",
      sub_phase=SUB_PHASE_RESOLVE_CONSECUTIVE_RACE_WARNING,
      popup_type="scheduled_race_popup",
    )
    if config.CANCEL_CONSECUTIVE_RACE:
      info("[TB_POST] Consecutive-race warning overridden for scheduled race popup; continuing with OK.")
    warning_ok_template = constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive_ok")
    warning_ok_clicked = False
    if warning_ok_template:
      warning_ok_clicked = device_action.locate_and_click(
        warning_ok_template,
        min_search_time=get_secs(1),
        region_ltrb=constants.GAME_WINDOW_BBOX,
        text="Accepted consecutive-race warning via warning-specific OK during scheduled race popup flow.",
      )
    if not warning_ok_clicked and not device_action.locate_and_click(
      "assets/buttons/ok_btn.png",
      min_search_time=get_secs(1),
      region_ltrb=constants.GAME_WINDOW_BBOX,
      text="Accepted consecutive-race warning via fallback OK during scheduled race popup flow.",
    ):
      warning("[TB_POST] Consecutive-race warning detected but OK button was not found.")
      return {
        "detected": True,
        "handled": False,
        "popup_type": "scheduled_race_popup",
        "reason": "consecutive_race_warning_ok_not_found",
        "deferred_work": [],
      }
    sleep(1.0)

  confirmed_race = False
  confirm_asset = ""
  for template_path, min_search_time in (
    ("assets/buttons/race_btn.png", get_secs(5)),
    ("assets/buttons/bluestacks/race_btn.png", get_secs(3)),
  ):
    if device_action.locate_and_click(template_path, min_search_time=min_search_time):
      confirmed_race = True
      confirm_asset = template_path
      break
  if not confirmed_race:
    warning("[TB_POST] Could not find race confirm button after scheduled race popup.")
    bot.push_debug_history({
      "event": "template_match",
      "asset": "race_btn.png",
      "result": "not_found",
      "context": "post_action_resolution",
    })
    return {
      "detected": True,
      "handled": False,
      "popup_type": "scheduled_race_popup",
      "reason": "race_confirm_button_not_found",
      "deferred_work": [],
    }

  bot.push_debug_history({
    "event": "click",
    "asset": confirm_asset,
    "result": "clicked",
    "context": "post_action_resolution",
  })
  info("[TB_POST] Scheduled race confirmed from popup flow; starting race sequence.")
  if not start_race():
    warning("[TB_POST] Scheduled race popup branch reached race start but the race flow did not complete cleanly.")
    return {
      "detected": True,
      "handled": False,
      "popup_type": "scheduled_race_popup",
      "reason": "race_sequence_failed",
      "deferred_work": [],
    }

  return {
    "detected": True,
    "handled": True,
    "popup_type": "scheduled_race_popup",
    "reason": "scheduled_race_completed",
    "deferred_work": [],
  }


def _handle_trackblazer_climax_race_result_screen(state_obj, action):
  if not _trackblazer_scenario_active():
    return {
      "detected": False,
      "handled": False,
      "popup_type": "climax_race_result",
      "reason": "scenario_inactive",
      "deferred_work": [],
    }

  template_path = constants.TRACKBLAZER_RACE_TEMPLATES.get("climax_race_result")
  region_ltrb = getattr(constants, "SCREEN_TOP_BBOX", None)
  if not template_path or not region_ltrb:
    return {
      "detected": False,
      "handled": False,
      "popup_type": "climax_race_result",
      "reason": "template_or_region_missing",
      "deferred_work": [],
    }

  _inv_scale = 1.0 / device_action.GLOBAL_TEMPLATE_SCALING
  screenshot = device_action.screenshot(region_ltrb=region_ltrb)
  matches = device_action.match_template(
    template_path,
    screenshot,
    threshold=0.72,
    template_scaling=_inv_scale,
  )
  if not matches:
    return {
      "detected": False,
      "handled": False,
      "popup_type": "climax_race_result",
      "reason": "result_banner_not_found",
      "deferred_work": [],
    }

  info("[TB_POST] Climax race result screen detected during post-action resolution via SCREEN_TOP_BBOX.")
  bot.push_debug_history({
    "event": "template_match",
    "asset": "climax_race_result.png",
    "result": "found",
    "context": "post_action_resolution",
    "region": "SCREEN_TOP_BBOX",
  })

  for label, template_path in (
    ("next", "assets/buttons/next_btn.png"),
    ("next2", "assets/buttons/next2_btn.png"),
  ):
    if device_action.locate_and_click(
      template_path,
      min_search_time=get_secs(0.6),
      region_ltrb=constants.SCREEN_BOTTOM_BBOX,
      text=f"Clicked {label} on Trackblazer climax race result screen.",
    ):
      bot.push_debug_history({
        "event": "click",
        "asset": template_path,
        "result": "clicked",
        "context": "post_action_resolution",
      })
      return {
        "detected": True,
        "handled": True,
        "popup_type": "climax_race_result",
        "reason": f"{label}_clicked",
        "deferred_work": [],
      }

  warning("[TB_POST] Climax race result screen detected but no bottom-region Next button matched.")
  bot.push_debug_history({
    "event": "template_match",
    "asset": "next_btn_or_next2_btn",
    "result": "not_found",
    "context": "post_action_resolution",
  })
  return {
    "detected": True,
    "handled": False,
    "popup_type": "climax_race_result",
    "reason": "next_button_not_found",
    "deferred_work": [],
  }


def _handle_trackblazer_post_race_watch_concert_screen(state_obj, action):
  if not _trackblazer_scenario_active():
    return {
      "detected": False,
      "handled": False,
      "popup_type": "post_race_watch_concert",
      "reason": "scenario_inactive",
      "deferred_work": [],
    }

  watch_template = constants.TRACKBLAZER_RESOLUTION_TEMPLATES.get("post_race_watch_concert")
  next_template = constants.TRACKBLAZER_RESOLUTION_TEMPLATES.get("post_race_watch_concert_next")
  watch_region = getattr(constants, "TRACKBLAZER_POST_RACE_WATCH_CONCERT_BBOX", None)
  next_region = getattr(constants, "TRACKBLAZER_POST_RACE_WATCH_CONCERT_NEXT_BBOX", None)
  if not watch_template or not next_template or not watch_region or not next_region:
    return {
      "detected": False,
      "handled": False,
      "popup_type": "post_race_watch_concert",
      "reason": "template_or_region_missing",
      "deferred_work": [],
    }

  inv_scale = 1.0 / device_action.GLOBAL_TEMPLATE_SCALING
  watch_screenshot = device_action.screenshot(region_ltrb=watch_region)
  watch_matches = device_action.match_template(
    watch_template,
    watch_screenshot,
    threshold=0.72,
    template_scaling=inv_scale,
  )
  if not watch_matches:
    return {
      "detected": False,
      "handled": False,
      "popup_type": "post_race_watch_concert",
      "reason": "watch_concert_not_found",
      "deferred_work": [],
    }

  info("[TB_POST] Post-race watch concert screen detected during post-action resolution.")
  bot.push_debug_history({
    "event": "template_match",
    "asset": "post_race_watch_concert.png",
    "result": "found",
    "context": "post_action_resolution",
  })

  next_screenshot = device_action.screenshot(region_ltrb=next_region)
  next_matches = device_action.match_template(
    next_template,
    next_screenshot,
    threshold=0.72,
    template_scaling=inv_scale,
  )
  if not next_matches:
    warning("[TB_POST] Watch concert screen detected but the paired Next button was not matched.")
    bot.push_debug_history({
      "event": "template_match",
      "asset": "post_race_watch_concert_next.png",
      "result": "not_found",
      "context": "post_action_resolution",
    })
    return {
      "detected": True,
      "handled": False,
      "popup_type": "post_race_watch_concert",
      "reason": "paired_next_not_found",
      "deferred_work": [],
    }

  x, y, w, h = next_matches[0]
  click_target = (
    int(next_region[0] + x + (w // 2)),
    int(next_region[1] + y + (h // 2)),
  )
  device_action.click(
    target=click_target,
    text="Clicked post-race watch concert Next button.",
  )
  bot.push_debug_history({
    "event": "click",
    "asset": "post_race_watch_concert_next.png",
    "result": "clicked",
    "context": "post_action_resolution",
  })
  return {
    "detected": True,
    "handled": True,
    "popup_type": "post_race_watch_concert",
    "reason": "paired_next_clicked",
    "deferred_work": [],
  }


def _click_trackblazer_next_button(context_label, min_search_time=0.6):
  for label, template_path in (
    ("next", "assets/buttons/next_btn.png"),
    ("next2", "assets/buttons/next2_btn.png"),
  ):
    if device_action.locate_and_click(
      template_path,
      min_search_time=get_secs(min_search_time),
      region_ltrb=constants.SCREEN_BOTTOM_BBOX,
      text=f"Clicked {label} on Trackblazer {context_label}.",
    ):
      bot.push_debug_history({
        "event": "click",
        "asset": template_path,
        "result": "clicked",
        "context": "post_action_resolution",
      })
      return label
  return ""


def _handle_trackblazer_goal_complete_screen(state_obj, action):
  if not _trackblazer_scenario_active():
    return {
      "detected": False,
      "handled": False,
      "popup_type": "goal_complete",
      "reason": "scenario_inactive",
      "deferred_work": [],
    }

  template_path = constants.TRACKBLAZER_RACE_TEMPLATES.get("goal_complete")
  if not template_path:
    return {
      "detected": False,
      "handled": False,
      "popup_type": "goal_complete",
      "reason": "template_missing",
      "deferred_work": [],
    }

  screenshot = device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX)
  matches = device_action.match_template(
    template_path,
    screenshot,
    threshold=0.75,
    template_scaling=1.0 / device_action.GLOBAL_TEMPLATE_SCALING,
  )
  if not matches:
    return {
      "detected": False,
      "handled": False,
      "popup_type": "goal_complete",
      "reason": "goal_complete_banner_not_found",
      "deferred_work": [],
    }

  info("[TB_POST] Goal complete screen detected during post-action resolution.")
  bot.push_debug_history({
    "event": "template_match",
    "asset": "goal_complete.png",
    "result": "found",
    "context": "post_action_resolution",
  })

  first_click = _click_trackblazer_next_button("goal complete screen")
  if not first_click:
    warning("[TB_POST] Goal complete screen detected but no bottom-region Next button matched.")
    bot.push_debug_history({
      "event": "template_match",
      "asset": "next_btn_or_next2_btn",
      "result": "not_found",
      "context": "post_action_resolution",
    })
    return {
      "detected": True,
      "handled": False,
      "popup_type": "goal_complete",
      "reason": "next_button_not_found",
      "deferred_work": [],
    }

  sleep(3.0)
  device_action.flush_screenshot_cache()
  second_click = _click_trackblazer_next_button("goal complete second screen", min_search_time=0.8)
  if second_click:
    return {
      "detected": True,
      "handled": True,
      "popup_type": "goal_complete",
      "reason": f"{first_click}_then_{second_click}_clicked",
      "deferred_work": [],
    }

  warning("[TB_POST] Goal complete second screen appeared but no bottom-region Next button matched after the animation wait.")
  bot.push_debug_history({
    "event": "template_match",
    "asset": "next_btn_or_next2_btn",
    "result": "not_found",
    "context": "post_action_resolution",
  })
  return {
    "detected": True,
    "handled": False,
    "popup_type": "goal_complete",
    "reason": "second_next_button_not_found_after_wait",
    "deferred_work": [],
  }


def _detect_trackblazer_complete_career_banner(screenshot=None, threshold=0.75, log_result=False, context=""):
  if not _trackblazer_scenario_active():
    return {
      "detected": False,
      "reason": "scenario_inactive",
      "context": context or "",
    }

  template_path = constants.TRACKBLAZER_RACE_TEMPLATES.get("complete_career")
  region_ltrb = getattr(constants, "TRACKBLAZER_COMPLETE_CAREER_BBOX", None)
  if not template_path or not region_ltrb:
    return {
      "detected": False,
      "reason": "template_or_region_missing",
      "context": context or "",
    }

  region_screenshot = screenshot if screenshot is not None else device_action.screenshot(region_ltrb=region_ltrb)
  matches = device_action.match_template(
    template_path,
    region_screenshot,
    threshold=threshold,
    template_scaling=1.0 / device_action.GLOBAL_TEMPLATE_SCALING,
  )
  detected = bool(matches)
  if detected:
    info("[TB_DONE] Career complete banner detected.")
    bot.push_debug_history({
      "event": "template_match",
      "asset": "complete_career.png",
      "result": "found",
      "context": context or "trackblazer_complete_career",
    })
  elif log_result:
    bot.push_debug_history({
      "event": "template_match",
      "asset": "complete_career.png",
      "result": "not_found",
      "context": context or "trackblazer_complete_career",
    })

  return {
    "detected": detected,
    "reason": "complete_career_banner_found" if detected else "complete_career_banner_not_found",
    "context": context or "",
    "region_ltrb": [int(v) for v in region_ltrb] if region_ltrb else None,
  }


def _stop_for_trackblazer_complete_career(state_obj=None, action=None, context=""):
  reason = "Trackblazer career complete banner detected; stopping bot without clicking."
  info(f"[TB_DONE] {reason}")
  update_operator_snapshot(
    state_obj,
    action,
    phase="idle",
    status="complete",
    message=reason,
    sub_phase=context or "trackblazer_complete_career",
  )
  bot.stop_event.set()
  bot.is_bot_running = False
  raise BotStopException(reason)


def _generic_post_action_return_to_lobby_step():
  for label, template_path, region_ltrb in _POST_ACTION_GENERIC_ADVANCE_TEMPLATES:
    if label == "cancel":
      screenshot = device_action.screenshot()
      if device_action.match_template("assets/icons/clock_icon.png", screenshot=screenshot, threshold=0.9):
        continue
    if device_action.locate_and_click(template_path, min_search_time=get_secs(0.4), region_ltrb=region_ltrb):
      return label
  return ""


_POST_ACTION_MAX_WAIT_RACE = 45.0
_POST_ACTION_MAX_WAIT_DEFAULT = 20.0


# Post-action resolution branch order:
# 1. Stable lobby check
# 2. Trackblazer career-complete banner
# 3. Event choice resolution
# 4. Trackblazer shop refresh popup
# 5. Trackblazer scheduled race popup
# 6. Trackblazer climax race result screen
# 7. Trackblazer post-race watch-concert result screen
# 8. Trackblazer goal-complete screen
# 9. Generic bottom-screen recovery clicks
# 10. Safe-space tap fallback after repeated idle loops
# 11. Timeout fallback to the generic lobby scan
def _resolve_post_action_resolution(state_obj, action, max_wait=None):
  import time as _time_mod

  action_name = _action_func(action) or "unknown_action"
  if max_wait is None:
    # Races chain through result screens, concerts, and events before stable
    # lobby returns — give them the longer budget that the old post-race loop
    # used.
    max_wait = _POST_ACTION_MAX_WAIT_RACE if action_name == "do_race" else _POST_ACTION_MAX_WAIT_DEFAULT
  bot.begin_post_action_resolution(
    source_action=action_name,
    reason="action_committed_waiting_for_stable_lobby",
    sub_phase=SUB_PHASE_POST_ACTION_RESOLUTION,
  )
  deadline = _time_mod.time() + max_wait
  idle_loops = 0

  while _time_mod.time() < deadline:
    if bot.stop_event.is_set():
      bot.end_post_action_resolution(outcome="bot_stopped", status="stopped")
      return False

    device_action.flush_screenshot_cache()
    screenshot = device_action.screenshot()
    stable_lobby, anchor_counts = _is_stable_career_lobby_screen(screenshot=screenshot)
    if stable_lobby:
      _update_post_action_resolution_snapshot(
        state_obj,
        action,
        message=f"Stable lobby confirmed after {action_name}.",
        sub_phase=SUB_PHASE_RETURN_TO_LOBBY,
        reasoning_notes=f"anchor_counts={anchor_counts}",
      )
      bot.end_post_action_resolution(outcome="stable_lobby_confirmed")
      return True

    _update_post_action_resolution_snapshot(
      state_obj,
      action,
      message=f"Resolving post-action popup or follow-up screen after {action_name}.",
      sub_phase=SUB_PHASE_RESOLVE_POST_ACTION_POPUP,
      reasoning_notes=f"anchor_counts={anchor_counts}",
    )

    if _detect_trackblazer_complete_career_banner(context="post_action_resolution").get("detected"):
      bot.end_post_action_resolution(outcome="trackblazer_career_complete", status="completed")
      _stop_for_trackblazer_complete_career(
        state_obj,
        action,
        context="post_action_resolution",
      )

    if select_event():
      _update_post_action_resolution_snapshot(
        state_obj,
        action,
        message="Resolved post-action event choice.",
        sub_phase=SUB_PHASE_RESOLVE_EVENT_CHOICE,
        popup_type="event_choice",
      )
      idle_loops = 0
      sleep(0.6)
      continue

    if _trackblazer_scenario_active():
      shop_refresh_result = _handle_trackblazer_shop_refresh_popup()
      if shop_refresh_result.get("detected"):
        _update_post_action_resolution_snapshot(
          state_obj,
          action,
          message="Resolved Trackblazer shop refresh popup." if shop_refresh_result.get("handled") else "Trackblazer shop refresh popup detected but not fully dismissed.",
          sub_phase=SUB_PHASE_RESOLVE_SHOP_REFRESH_POPUP,
          popup_type=shop_refresh_result.get("popup_type"),
          deferred_work=shop_refresh_result.get("deferred_work"),
          reasoning_notes=shop_refresh_result.get("reason"),
        )
        if shop_refresh_result.get("handled"):
          idle_loops = 0
          sleep(0.8)
          continue

      scheduled_race_result = _handle_trackblazer_scheduled_race_popup(state_obj, action)
      if scheduled_race_result.get("detected"):
        _update_post_action_resolution_snapshot(
          state_obj,
          action,
          message="Resolved Trackblazer scheduled race popup." if scheduled_race_result.get("handled") else "Trackblazer scheduled race popup detected but race branch did not complete cleanly.",
          sub_phase=SUB_PHASE_RESOLVE_SCHEDULED_RACE_POPUP,
          popup_type=scheduled_race_result.get("popup_type"),
          deferred_work=scheduled_race_result.get("deferred_work"),
          reasoning_notes=scheduled_race_result.get("reason"),
        )
        if scheduled_race_result.get("handled"):
          idle_loops = 0
          sleep(0.8)
          continue

      climax_race_result = _handle_trackblazer_climax_race_result_screen(state_obj, action)
      if climax_race_result.get("detected"):
        _update_post_action_resolution_snapshot(
          state_obj,
          action,
          message="Resolved Trackblazer climax race result screen." if climax_race_result.get("handled") else "Trackblazer climax race result screen detected but Next was not matched.",
          sub_phase=SUB_PHASE_RESOLVE_POST_ACTION_POPUP,
          popup_type=climax_race_result.get("popup_type"),
          deferred_work=climax_race_result.get("deferred_work"),
          reasoning_notes=climax_race_result.get("reason"),
        )
        if climax_race_result.get("handled"):
          idle_loops = 0
          sleep(0.8)
          continue

      post_race_watch_concert_result = _handle_trackblazer_post_race_watch_concert_screen(state_obj, action)
      if post_race_watch_concert_result.get("detected"):
        _update_post_action_resolution_snapshot(
          state_obj,
          action,
          message="Resolved Trackblazer post-race result screen." if post_race_watch_concert_result.get("handled") else "Trackblazer post-race result screen detected but the paired Next button was not matched.",
          sub_phase=SUB_PHASE_RESOLVE_POST_ACTION_POPUP,
          popup_type=post_race_watch_concert_result.get("popup_type"),
          deferred_work=post_race_watch_concert_result.get("deferred_work"),
          reasoning_notes=post_race_watch_concert_result.get("reason"),
        )
        if post_race_watch_concert_result.get("handled"):
          idle_loops = 0
          sleep(0.8)
          continue

      goal_complete_result = _handle_trackblazer_goal_complete_screen(state_obj, action)
      if goal_complete_result.get("detected"):
        _update_post_action_resolution_snapshot(
          state_obj,
          action,
          message="Resolved Trackblazer goal complete screen." if goal_complete_result.get("handled") else "Trackblazer goal complete screen detected but Next was not matched.",
          sub_phase=SUB_PHASE_RESOLVE_POST_ACTION_POPUP,
          popup_type=goal_complete_result.get("popup_type"),
          deferred_work=goal_complete_result.get("deferred_work"),
          reasoning_notes=goal_complete_result.get("reason"),
        )
        if goal_complete_result.get("handled"):
          idle_loops = 0
          sleep(0.8)
          continue

    generic_step = _generic_post_action_return_to_lobby_step()
    if generic_step:
      _update_post_action_resolution_snapshot(
        state_obj,
        action,
        message=f"Generic post-action recovery clicked {generic_step}.",
        sub_phase=SUB_PHASE_RETURN_TO_LOBBY,
        popup_type="generic_recovery",
        reasoning_notes=f"clicked={generic_step}",
      )
      idle_loops = 0
      sleep(0.6)
      continue

    idle_loops += 1
    if idle_loops >= 3:
      _update_post_action_resolution_snapshot(
        state_obj,
        action,
        message="No post-action buttons matched; tapping safe space to continue toward the lobby.",
        sub_phase=SUB_PHASE_RETURN_TO_LOBBY,
        popup_type="generic_recovery",
      )
      device_action.click(target=constants.SAFE_SPACE_MOUSE_POS)
      idle_loops = 0
      sleep(0.6)
      continue

    sleep(0.5)

  warning(f"[POST_ACTION] Timed out resolving post-action screens after {action_name}; returning control to generic lobby scan.")
  _update_post_action_resolution_snapshot(
    state_obj,
    action,
    message=f"Timed out resolving post-action screens after {action_name}; falling back to generic lobby scan.",
    sub_phase=SUB_PHASE_RETURN_TO_LOBBY,
    popup_type="generic_timeout_fallback",
    status="warning",
  )
  bot.end_post_action_resolution(outcome="timed_out_fallback", status="warning")
  return True


def _wait_for_lobby_after_item_use(state_obj, action, max_wait=8.0, sub_phase=None, ocr_debug=None, planned_clicks=None):
  """Brief poll for a ready pre-action screen after Trackblazer item use.

  If the inventory close was slow or the game lingered on an overlay, the
  next action target won't be visible yet. Poll up to *max_wait* seconds.

  Each iteration checks forced Climax race-day assets first (instant success
  on forced race day), then lobby anchors, then
  whether the inventory screen is visible (close it and keep polling),
  then falls back to a generic back/close click once.

  Returns True if a ready screen was confirmed, False if timed out.
  """
  import time as _time_mod
  from scenarios.trackblazer import detect_inventory_screen, close_training_items_inventory

  deadline = _time_mod.time() + max_wait
  generic_recovery_attempted = False
  inventory_close_count = 0
  max_inventory_close_attempts = 3
  polls = 0

  while _time_mod.time() < deadline:
    if bot.stop_event.is_set():
      return False
    polls += 1
    device_action.flush_screenshot_cache()

    # 1. Check forced Climax race-day assets and normal lobby anchors.
    screenshot = device_action.screenshot(region_ltrb=constants.SCREEN_BOTTOM_BBOX)
    ready_state = _trackblazer_pre_action_ready_state(screenshot=screenshot)
    if ready_state.get("ready"):
      info(f"[ITEM_USE] Ready screen confirmed after item use via {ready_state.get('source')} (poll {polls}).")
      return True
    lobby_found = False
    for tpl in _LOBBY_ANCHOR_TEMPLATES:
      if device_action.match_template(tpl, screenshot, threshold=0.8):
        lobby_found = True
        break
    if lobby_found:
      info(f"[ITEM_USE] Lobby anchor confirmed after item use (poll {polls}).")
      return True

    # 2. Check if inventory screen is visible.  This can appear after an
    #    animation gap, so it must be checked every iteration, not once.
    if inventory_close_count < max_inventory_close_attempts:
      device_action.flush_screenshot_cache()
      inv_open, _, _ = detect_inventory_screen(threshold=0.75)
      if inv_open:
        inventory_close_count += 1
        info(
          f"[ITEM_USE] Inventory screen visible (poll {polls}, "
          f"close attempt {inventory_close_count}/{max_inventory_close_attempts}); closing."
        )
        close_result = close_training_items_inventory()
        if close_result.get("closed"):
          info("[ITEM_USE] Inventory closed via explicit close in lobby wait.")
        else:
          warning("[ITEM_USE] Explicit inventory close attempt did not succeed.")
        sleep(0.3)
        continue

    # 3. Generic back/close fallback — one attempt only.
    if not generic_recovery_attempted:
      info(f"[ITEM_USE] Lobby not visible (poll {polls}); attempting generic recovery close.")
      for close_tpl in ("assets/buttons/close_btn.png", "assets/buttons/back_btn.png"):
        if device_action.locate_and_click(close_tpl, min_search_time=get_secs(0.4), region_ltrb=constants.SCREEN_BOTTOM_BBOX):
          info(f"[ITEM_USE] Clicked {close_tpl} to dismiss leftover overlay.")
          sleep(0.6)
          break
      generic_recovery_attempted = True

    sleep(0.4)

  warning(f"[ITEM_USE] Timed out waiting for lobby after item use ({polls} polls).")
  return False


def _wait_for_lobby_after_shop_purchase(max_wait=8.0):
  """Brief poll for a ready pre-action screen after Trackblazer shop interaction.

  After the shop close, the game overlay may linger before the next
  pre-action screen settles. Poll up to *max_wait* seconds, accepting either
  the normal lobby anchors or the forced Climax race-day bottom assets, while
  also closing any residual shop/inventory overlay on each iteration
  (capped at 3 close attempts each).

  Returns True if a ready screen was confirmed, False if timed out.
  """
  import time as _time_mod
  from scenarios.trackblazer import (
    detect_inventory_screen, close_training_items_inventory,
    detect_shop_screen, close_trackblazer_shop,
  )

  deadline = _time_mod.time() + max_wait
  generic_recovery_attempted = False
  inventory_close_count = 0
  shop_close_count = 0
  max_close_attempts = 3
  polls = 0
  bot.push_debug_history({
    "event": "trackblazer_shop_wait",
    "asset": "lobby_after_shop_purchase",
    "result": "started",
    "context": "trackblazer_shop_inventory",
    "max_wait": round(float(max_wait), 3),
    "max_close_attempts": max_close_attempts,
  })

  while _time_mod.time() < deadline:
    if bot.stop_event.is_set():
      return False
    polls += 1
    device_action.flush_screenshot_cache()

    # 1. Check forced Climax race-day assets and normal lobby anchors.
    screenshot = device_action.screenshot(region_ltrb=constants.SCREEN_BOTTOM_BBOX)
    ready_state = _trackblazer_pre_action_ready_state(screenshot=screenshot)
    if ready_state.get("ready"):
      info(f"[TB_SHOP] Ready screen confirmed after shop via {ready_state.get('source')} (poll {polls}).")
      bot.push_debug_history({
        "event": "trackblazer_shop_wait",
        "asset": "lobby_after_shop_purchase",
        "result": "ready_detected",
        "context": "trackblazer_shop_inventory",
        "poll": polls,
        "source": ready_state.get("source") or "",
      })
      return True
    lobby_found = False
    for tpl in _LOBBY_ANCHOR_TEMPLATES:
      if device_action.match_template(tpl, screenshot, threshold=0.8):
        lobby_found = True
        break
    if lobby_found:
      info(f"[TB_SHOP] Lobby anchor confirmed after shop purchase (poll {polls}).")
      bot.push_debug_history({
        "event": "trackblazer_shop_wait",
        "asset": "lobby_after_shop_purchase",
        "result": "lobby_anchor_detected",
        "context": "trackblazer_shop_inventory",
        "poll": polls,
      })
      return True

    # 2. Check if shop screen is still visible.
    if shop_close_count < max_close_attempts:
      device_action.flush_screenshot_cache()
      shop_open, _, _ = detect_shop_screen(threshold=0.75)
      if shop_open:
        shop_close_count += 1
        info(
          f"[TB_SHOP] Shop screen visible (poll {polls}, "
          f"close attempt {shop_close_count}/{max_close_attempts}); closing."
        )
        bot.push_debug_history({
          "event": "trackblazer_shop_wait",
          "asset": "lobby_after_shop_purchase",
          "result": "shop_still_open",
          "context": "trackblazer_shop_inventory",
          "poll": polls,
          "shop_close_count": shop_close_count,
          "max_close_attempts": max_close_attempts,
        })
        close_result = close_trackblazer_shop()
        if close_result.get("closed"):
          info("[TB_SHOP] Shop closed via explicit close in lobby wait.")
          bot.push_debug_history({
            "event": "trackblazer_shop_wait",
            "asset": "lobby_after_shop_purchase",
            "result": "shop_closed_in_wait",
            "context": "trackblazer_shop_inventory",
            "poll": polls,
            "shop_close_count": shop_close_count,
          })
        else:
          warning("[TB_SHOP] Explicit shop close attempt did not succeed.")
          bot.push_debug_history({
            "event": "trackblazer_shop_wait",
            "asset": "lobby_after_shop_purchase",
            "result": "shop_close_failed_in_wait",
            "context": "trackblazer_shop_inventory",
            "poll": polls,
            "shop_close_count": shop_close_count,
          })
        sleep(0.3)
        continue

    # 3. Check if inventory screen is visible (from post-shop refresh).
    if inventory_close_count < max_close_attempts:
      device_action.flush_screenshot_cache()
      inv_open, _, _ = detect_inventory_screen(threshold=0.75)
      if inv_open:
        inventory_close_count += 1
        info(
          f"[TB_SHOP] Inventory screen visible (poll {polls}, "
          f"close attempt {inventory_close_count}/{max_close_attempts}); closing."
        )
        bot.push_debug_history({
          "event": "trackblazer_shop_wait",
          "asset": "lobby_after_shop_purchase",
          "result": "inventory_still_open",
          "context": "trackblazer_shop_inventory",
          "poll": polls,
          "inventory_close_count": inventory_close_count,
          "max_close_attempts": max_close_attempts,
        })
        close_result = close_training_items_inventory()
        if close_result.get("closed"):
          info("[TB_SHOP] Inventory closed via explicit close in lobby wait.")
          bot.push_debug_history({
            "event": "trackblazer_shop_wait",
            "asset": "lobby_after_shop_purchase",
            "result": "inventory_closed_in_wait",
            "context": "trackblazer_shop_inventory",
            "poll": polls,
            "inventory_close_count": inventory_close_count,
          })
        else:
          warning("[TB_SHOP] Explicit inventory close attempt did not succeed.")
          bot.push_debug_history({
            "event": "trackblazer_shop_wait",
            "asset": "lobby_after_shop_purchase",
            "result": "inventory_close_failed_in_wait",
            "context": "trackblazer_shop_inventory",
            "poll": polls,
            "inventory_close_count": inventory_close_count,
          })
        sleep(0.3)
        continue

    # 4. Generic back/close fallback — one attempt only.
    if not generic_recovery_attempted:
      info(f"[TB_SHOP] Lobby not visible (poll {polls}); attempting generic recovery close.")
      bot.push_debug_history({
        "event": "trackblazer_shop_wait",
        "asset": "lobby_after_shop_purchase",
        "result": "generic_recovery_attempt",
        "context": "trackblazer_shop_inventory",
        "poll": polls,
      })
      for close_tpl in ("assets/buttons/close_btn.png", "assets/buttons/back_btn.png"):
        if device_action.locate_and_click(close_tpl, min_search_time=get_secs(0.4), region_ltrb=constants.SCREEN_BOTTOM_BBOX):
          info(f"[TB_SHOP] Clicked {close_tpl} to dismiss leftover overlay.")
          bot.push_debug_history({
            "event": "trackblazer_shop_wait",
            "asset": "lobby_after_shop_purchase",
            "result": "generic_recovery_clicked",
            "context": "trackblazer_shop_inventory",
            "poll": polls,
            "template": close_tpl,
          })
          sleep(0.6)
          break
      generic_recovery_attempted = True

    sleep(0.4)

  warning(f"[TB_SHOP] Timed out waiting for lobby after shop purchase ({polls} polls).")
  bot.push_debug_history({
    "event": "trackblazer_shop_wait",
    "asset": "lobby_after_shop_purchase",
    "result": "timeout",
    "context": "trackblazer_shop_inventory",
    "polls": polls,
    "shop_close_count": shop_close_count,
    "inventory_close_count": inventory_close_count,
    "generic_recovery_attempted": generic_recovery_attempted,
  })
  return False


def _run_trackblazer_shop_purchases(state_obj, action):
  # Recompute against the current state so execute mode does not use a stale
  # pre-review shop plan after a refresh, recovery, or overlay mismatch.
  _attach_trackblazer_pre_action_item_plan(state_obj, action)
  planned_buys = list(action.get("trackblazer_shop_buy_plan") or [])
  if not planned_buys:
    return {"status": "skipped"}

  from scenarios.trackblazer import execute_trackblazer_shop_purchases

  item_keys = [entry.get("key") for entry in planned_buys if entry.get("key")]
  if not item_keys:
    return {"status": "skipped"}

  # Invalidate inventory cache before attempting purchases — even a partial
  # purchase changes what we hold, so stale cache must not survive failures.
  _invalidate_trackblazer_inventory_cache()

  result = execute_trackblazer_shop_purchases(item_keys, trigger="automatic")
  state_obj["trackblazer_shop_items"] = result.get("trackblazer_shop_items")
  state_obj["trackblazer_shop_summary"] = result.get("trackblazer_shop_summary")
  state_obj["trackblazer_shop_flow"] = result.get("trackblazer_shop_flow")

  if not result.get("success"):
    return {
      "status": "failed",
      "result": result,
      "reason": (result.get("trackblazer_shop_flow") or {}).get("reason") or "trackblazer_shop_purchase_failed",
    }

  refreshed_state = collect_trackblazer_inventory(
    state_obj,
    allow_open_non_execute=True,
    trigger="post_shop_purchase_refresh",
  )
  state_obj.update(refreshed_state)
  _merge_post_shop_inventory_with_preserved_snapshot(state_obj)
  _cache_trackblazer_inventory(state_obj, turn_key=action_count)
  _attach_trackblazer_pre_action_item_plan(state_obj, action)
  return {
    "status": "executed",
    "result": result,
    "reason": (result.get("trackblazer_shop_flow") or {}).get("reason") or "trackblazer_shop_purchase_applied",
  }


def _ocr_debug_for_action(state_obj, action):
  entries = _base_ocr_debug_entries(state_obj)
  if not hasattr(action, "func"):
    return entries
  scenario_name = constants.SCENARIO_NAME or "default"
  has_training_context = bool(action.get("available_trainings")) if hasattr(action, "get") else False
  if not has_training_context and isinstance(state_obj, dict):
    has_training_context = bool(state_obj.get("training_results"))
  if action.func == "do_training" or has_training_context:
    selected_training_name = action.get("training_name") if hasattr(action, "get") else None
    if not selected_training_name and hasattr(action, "get"):
      available_trainings = action.get("available_trainings", {}) or {}
      if available_trainings:
        selected_training_name = next(iter(available_trainings))
    if not selected_training_name and isinstance(state_obj, dict):
      training_results = state_obj.get("training_results", {}) or {}
      if training_results:
        selected_training_name = next(iter(training_results))
    training_scan_debug = {}
    if isinstance(state_obj, dict):
      training_scan_debug = (state_obj.get("training_scan_debug", {}) or {}).get(selected_training_name, {}) or {}
    entries.extend(
      [
        _region_debug_entry(
          "training_failure",
          "UNITY_FAILURE_REGION" if scenario_name == "unity" else "MANT_FAILURE_REGION" if scenario_name == "trackblazer" else "FAILURE_REGION",
          extra={
            "template": "assets/ui/fail_percent_symbol.png",
            "grayscale": True,
            "template_scales": [0.9, 1.0, 1.1, 1.26, 1.4],
            "search_image_path": training_scan_debug.get("failure"),
            "training_name": selected_training_name,
          },
        ),
        _region_debug_entry(
          "training_support_icons",
          "UNITY_SUPPORT_CARD_ICON_REGION" if scenario_name == "unity" else "MANT_SUPPORT_CARD_ICON_REGION" if scenario_name == "trackblazer" else "SUPPORT_CARD_ICON_REGION",
          extra={"search_image_path": training_scan_debug.get("support_icons"), "training_name": selected_training_name},
        ),
        _region_debug_entry(
          "training_stat_gains",
          "UNITY_STAT_GAINS_REGION" if scenario_name == "unity" else "MANT_STAT_GAINS_REGION" if scenario_name == "trackblazer" else "URA_STAT_GAINS_REGION",
          extra={"search_image_path": training_scan_debug.get("stat_gains"), "training_name": selected_training_name},
        ),
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
  post_action_resolution = bot.get_post_action_resolution_state()
  operator_race_gate = _operator_race_gate_for_state(state_obj)
  debug_entries = ([ _profile_debug_entry() ] + ocr_debug) if ocr_debug is not None else ([ _profile_debug_entry() ] + _ocr_debug_for_action(state_obj, action))
  debug_entries = _enrich_ocr_debug_entries(debug_entries)
  state_summary = {
    "year": state_obj.get("year"),
    "turn": state_obj.get("turn"),
    "criteria": _truncate(state_obj.get("criteria", "")),
    "energy_level": state_obj.get("energy_level"),
    "max_energy": state_obj.get("max_energy"),
    "current_mood": state_obj.get("current_mood"),
    "current_stats": state_obj.get("current_stats"),
    "date_event_available": state_obj.get("date_event_available"),
    "race_mission_available": state_obj.get("race_mission_available"),
    "aptitudes": state_obj.get("aptitudes"),
    "ocr_region_profile": profile_info["active_profile"],
    "ocr_overrides_path": profile_info["overrides_path"],
    "control_backend": backend_state.get("active_backend"),
    "screenshot_backend": backend_state.get("screenshot_backend"),
    "device_id": backend_state.get("device_id"),
    "post_action_resolution": post_action_resolution,
    "pending_trackblazer_shop_check": bot.has_pending_trackblazer_shop_check(),
    "pending_trackblazer_shop_check_reason": bot.get_pending_trackblazer_shop_check_reason(),
    "state_validation": state_obj.get("state_validation"),
    "operator_race_gate": operator_race_gate,
    "skill_auto_buy_skill_enabled": bot.get_skill_auto_buy_enabled(),
    "skill_purchase_flow": state_obj.get("skill_purchase_flow"),
    "skill_purchase_scan": state_obj.get("skill_purchase_scan"),
    "skill_purchase_plan": state_obj.get("skill_purchase_plan"),
  }
  if (constants.SCENARIO_NAME or "default") in ("mant", "trackblazer"):
    state_summary["trackblazer_inventory_summary"] = state_obj.get("trackblazer_inventory_summary")
    state_summary["trackblazer_inventory_controls"] = state_obj.get("trackblazer_inventory_controls")
    state_summary["trackblazer_inventory_flow"] = state_obj.get("trackblazer_inventory_flow")
    state_summary["trackblazer_inventory_pre_shop_summary"] = state_obj.get("trackblazer_inventory_pre_shop_summary")
    state_summary["trackblazer_inventory_pre_shop_flow"] = state_obj.get("trackblazer_inventory_pre_shop_flow")
    state_summary["trackblazer_shop_summary"] = state_obj.get("trackblazer_shop_summary")
    state_summary["trackblazer_shop_flow"] = state_obj.get("trackblazer_shop_flow")
    state_summary["trackblazer_climax"] = state_obj.get("trackblazer_climax")
    state_summary["trackblazer_climax_locked_race"] = state_obj.get("trackblazer_climax_locked_race")
    state_summary["trackblazer_trainings_remaining_upper_bound"] = state_obj.get("trackblazer_trainings_remaining_upper_bound")
    shop_policy_context = policy_context(year=state_obj.get("year"), turn=state_obj.get("turn"))
    state_summary["trackblazer_shop_policy_context"] = shop_policy_context
    state_summary["trackblazer_shop_priority_preview"] = get_priority_preview(
      policy=config.TRACKBLAZER_SHOP_POLICY,
      year=state_obj.get("year"),
      turn=state_obj.get("turn"),
      limit=10,
    )
    state_summary["rival_indicator_detected"] = state_obj.get("rival_indicator_detected")
  state_summary["skill_purchase_check"] = {
    **get_skill_purchase_check_state(),
    **(state_obj.get("skill_purchase_check") or {}),
  }
  selected_action = {
    "func": getattr(action, "func", None),
    "training_name": action.get("training_name") if hasattr(action, "get") else None,
    "training_function": action.get("training_function") if hasattr(action, "get") else None,
    "race_name": action.get("race_name") if hasattr(action, "get") else None,
    "score_tuple": action.get("training_data", {}).get("score_tuple") if hasattr(action, "get") else None,
    "stat_gains": action.get("training_data", {}).get("stat_gains") if hasattr(action, "get") else None,
    "failure": action.get("training_data", {}).get("failure") if hasattr(action, "get") else None,
    "total_supports": action.get("training_data", {}).get("total_supports") if hasattr(action, "get") else None,
    "total_rainbow_friends": action.get("training_data", {}).get("total_rainbow_friends") if hasattr(action, "get") else None,
    "prefer_rival_race": action.get("prefer_rival_race") if hasattr(action, "get") else None,
    "rival_scout": action.get("rival_scout") if hasattr(action, "get") else None,
    "pre_action_item_use": action.get("trackblazer_pre_action_items") if hasattr(action, "get") else None,
    "reassess_after_item_use": action.get("trackblazer_reassess_after_item_use") if hasattr(action, "get") else None,
    "trackblazer_race_decision": action.get("trackblazer_race_decision") if hasattr(action, "get") else None,
    "trackblazer_race_lookahead": action.get("trackblazer_race_lookahead") if hasattr(action, "get") else None,
  }
  ranked_trainings = []
  available_trainings = action.get("available_trainings", {}) if hasattr(action, "get") else {}
  if isinstance(state_obj, dict):
    ranked_trainings = _build_ranked_training_snapshot(
      state_obj=state_obj,
      available_trainings=available_trainings,
      training_function=selected_action.get("training_function"),
    )
  return {
    "scenario_name": constants.SCENARIO_NAME or "default",
    "turn_label": f"{state_obj.get('year', '?')} / {state_obj.get('turn', '?')}",
    "energy_label": f"{state_obj.get('energy_level', '?')}/{state_obj.get('max_energy', '?')}",
    "sub_phase": sub_phase or (post_action_resolution.get("sub_phase") if post_action_resolution.get("active") else "idle") or "idle",
    "execution_intent": bot.get_execution_intent(),
    "state_summary": state_summary,
    "selected_action": selected_action,
    "available_actions": list(getattr(action, "available_actions", [])),
    "ranked_trainings": ranked_trainings,
    "trackblazer_inventory": state_obj.get("trackblazer_inventory") if isinstance(state_obj, dict) else None,
    "trackblazer_inventory_pre_shop": state_obj.get("trackblazer_inventory_pre_shop") if isinstance(state_obj, dict) else None,
    "trackblazer_shop_items": state_obj.get("trackblazer_shop_items") if isinstance(state_obj, dict) else None,
    "planned_actions": _build_trackblazer_planned_actions(state_obj, action) if isinstance(state_obj, dict) else {},
    "reasoning_notes": reasoning_notes or "",
    "min_scores": action.get("min_scores") if hasattr(action, "get") else None,
    "backend_state": backend_state,
    "ocr_debug": debug_entries,
    "planned_clicks": planned_clicks if planned_clicks is not None else _planned_clicks_for_action(action),
  }


def _get_training_filter_settings(training_function):
  settings = {
    "use_risk_taking": False,
    "check_stat_caps": False,
  }
  if training_function in ("rainbow_training", "most_support_cards", "meta_training", "most_stat_gain", "stat_weight_training"):
    settings["use_risk_taking"] = True
  if training_function in ("rainbow_training", "most_support_cards", "meta_training", "stat_weight_training"):
    settings["check_stat_caps"] = True
  return settings


def _resolve_review_risk_taking_set(state_obj, training_function):
  settings = _get_training_filter_settings(training_function)
  if not settings["use_risk_taking"] or not isinstance(training_function, str):
    return {}

  training_template = Strategy().get_training_template(state_obj) or {}
  risk_taking_set = training_template.get("risk_taking_set")
  return risk_taking_set if isinstance(risk_taking_set, dict) else {}


def _summarize_training_exclusion(training_name, training_data, state_obj, training_function, risk_taking_set=None):
  settings = _get_training_filter_settings(training_function)
  current_stats = state_obj.get("current_stats") or {}
  current_stat = current_stats.get(training_name)
  stat_cap = config.STAT_CAPS.get(training_name)
  failure = training_data.get("failure")
  risk_increase = 0
  max_allowed_failure = config.MAX_FAILURE

  if settings["use_risk_taking"]:
    resolved_risk_taking_set = risk_taking_set if isinstance(risk_taking_set, dict) else {}
    if resolved_risk_taking_set:
      from core.trainings import calculate_risk_increase
      risk_increase = calculate_risk_increase(training_data, resolved_risk_taking_set)
      max_allowed_failure += risk_increase

  reason = "filtered"
  if settings["check_stat_caps"] and current_stat is not None and stat_cap is not None and current_stat >= stat_cap:
    reason = f"stat cap ({current_stat}/{stat_cap})"
  elif failure is not None and int(failure) > max_allowed_failure:
    reason = f"fail {int(failure)}% > {int(max_allowed_failure)}%"
  elif training_function:
    reason = f"not selected by {training_function}"

  return max_allowed_failure, risk_increase, reason


def _score_training_for_display(training_name, training_data, state_obj, training_function, training_template):
  """Compute a score for a filtered-out training so it can be compared in the
  operator console.  Uses the same formula as the configured training function."""
  from copy import deepcopy
  from core.trainings import (
    most_stat_score, max_out_friendships_score,
    rainbow_training_score, add_scenario_gimmick_score,
    most_support_score,
  )
  # Work on a copy so scoring side-effects don't leak into the original data.
  td = deepcopy(training_data)
  x = (training_name, td)
  try:
    if training_function == "meta_training":
      stat_gain = most_stat_score(x, state_obj, training_template)
      non_max = max_out_friendships_score(x)
      rainbow = rainbow_training_score(x)
      rainbow = add_scenario_gimmick_score(x, rainbow, state_obj)
      return ((stat_gain[0] / 10) + non_max[0] + rainbow[0], stat_gain[1])
    if training_function == "rainbow_training":
      rainbow = rainbow_training_score(x)
      rainbow = add_scenario_gimmick_score(x, rainbow, state_obj)
      non_max = max_out_friendships_score(x)
      return (rainbow[0] + non_max[0] * config.NON_MAX_SUPPORT_WEIGHT, rainbow[1])
    if training_function == "most_support_cards":
      support = most_support_score(x)
      support = add_scenario_gimmick_score(x, support, state_obj)
      non_max = max_out_friendships_score(x)
      return (non_max[0] * config.NON_MAX_SUPPORT_WEIGHT + support[0], support[1])
    if training_function == "most_stat_gain":
      return most_stat_score(x, state_obj, training_template)
    if training_function == "stat_weight_training":
      stat_weights = getattr(config, "TRACKBLAZER_STAT_WEIGHTS", None)
      if not isinstance(stat_weights, dict) or not stat_weights:
        stat_weights = training_template.get("stat_weight_set", {})
      stat_gains = td.get("stat_gains", {})
      total_value = 0
      current_stats = state_obj.get("current_stats") or {}
      for stat, gain in stat_gains.items():
        if stat == "sp":
          continue
        if current_stats.get(stat, 0) >= config.STAT_CAPS.get(stat, 9999):
          continue
        weight = stat_weights.get(stat, 1)
        total_value += gain * weight
      if bot.get_trackblazer_bond_boost_enabled():
        cutoff = bot.get_trackblazer_bond_boost_cutoff()
        current_year = state_obj.get("year", "")
        try:
          active = constants.TIMELINE.index(current_year) <= constants.TIMELINE.index(cutoff)
        except ValueError:
          active = False
        if active:
          friendship_levels = td.get('total_friendship_levels', {})
          raiseable = friendship_levels.get('blue', 0) + friendship_levels.get('green', 0)
          if raiseable > 0:
            per_friend = 15 if training_name == "wit" else 10
            total_value += raiseable * per_friend
      from core.trainings import get_priority_index
      priority_index = get_priority_index(x)
      return (total_value, -priority_index)
    if training_function == "max_out_friendships":
      max_f = max_out_friendships_score(x)
      max_f = add_scenario_gimmick_score(x, max_f, state_obj)
      rainbow = rainbow_training_score(x)
      return (max_f[0] + rainbow[0] * 0.25 * config.RAINBOW_SUPPORT_WEIGHT_ADDITION, max_f[1])
    # Fallback: stat-gain only
    return most_stat_score(x, state_obj, training_template)
  except Exception:
    return None


def _build_ranked_training_snapshot(state_obj, available_trainings, training_function):
  raw_training_results = state_obj.get("training_results", {}) or {}
  merged_trainings = CleanDefaultDict()
  risk_taking_set = _resolve_review_risk_taking_set(state_obj, training_function)

  # Resolve training template once for scoring filtered trainings.
  training_template = None
  filtered_keys = set(raw_training_results.keys()) - set(available_trainings.keys())
  if filtered_keys:
    try:
      training_template = Strategy().get_training_template(state_obj) or {}
    except Exception:
      training_template = {}

  for training_name, training_data in raw_training_results.items():
    max_allowed_failure, risk_increase, exclusion_reason = _summarize_training_exclusion(
      training_name=training_name,
      training_data=training_data,
      state_obj=state_obj,
      training_function=training_function,
      risk_taking_set=risk_taking_set,
    )

    # Compute a display score even for filtered-out trainings.
    score_tuple = None
    if training_name in filtered_keys and training_template is not None:
      score_tuple = _score_training_for_display(
        training_name, training_data, state_obj, training_function, training_template,
      )

    merged_trainings[training_name] = {
      "name": training_name,
      "score_tuple": score_tuple,
      "failure": training_data.get("failure"),
      "max_allowed_failure": max_allowed_failure,
      "risk_increase": risk_increase,
      "total_supports": training_data.get("total_supports"),
      "total_rainbow_friends": None,
      "total_friendship_increases": None,
      "stat_gains": training_data.get("stat_gains"),
      "unity_gauge_fills": training_data.get("unity_gauge_fills"),
      "unity_spirit_explosions": training_data.get("unity_spirit_explosions"),
      "filtered_out": True,
      "excluded_reason": exclusion_reason,
    }

  for training_name, training_data in available_trainings.items():
    merged_trainings[training_name] = {
      "name": training_name,
      "score_tuple": training_data.get("score_tuple"),
      "failure": training_data.get("failure"),
      "max_allowed_failure": training_data.get("max_allowed_failure"),
      "risk_increase": training_data.get("risk_increase", 0),
      "total_supports": training_data.get("total_supports"),
      "total_rainbow_friends": training_data.get("total_rainbow_friends"),
      "total_friendship_increases": training_data.get("total_friendship_increases"),
      "stat_gains": training_data.get("stat_gains"),
      "unity_gauge_fills": training_data.get("unity_gauge_fills"),
      "unity_spirit_explosions": training_data.get("unity_spirit_explosions"),
      "filtered_out": False,
      "excluded_reason": None,
      "failure_bypassed_by_items": bool(training_data.get("failure_bypassed_by_items")),
    }

  return list(merged_trainings.values())


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
  bot.set_phase("scanning_lobby", status="active", message=message, sub_phase=sub_phase or "idle")
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
    bot.set_phase(phase, status=status, message=message, error=error_text, sub_phase=sub_phase or "idle")
  elif message or error_text:
    current = bot.get_runtime_state()
    bot.set_phase(current["phase"], status=status, message=message, error=error_text, sub_phase=sub_phase or current.get("sub_phase"))
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


def review_action_before_execution(state_obj, action, message="Review action before execution.", sub_phase=None, ocr_debug=None, planned_clicks=None, reasoning_notes=None):
  should_wait = config.EXECUTION_MODE == "semi_auto" or bot.is_pause_requested()
  update_operator_snapshot(
    state_obj,
    action,
    phase="waiting_for_confirmation",
    message=message,
    reasoning_notes=reasoning_notes,
    sub_phase=sub_phase,
    ocr_debug=ocr_debug,
    planned_clicks=planned_clicks,
  )
  if not should_wait:
    update_operator_snapshot(
      state_obj,
      action,
      phase="executing_action",
      message="Executing approved action.",
      reasoning_notes=reasoning_notes,
      sub_phase=sub_phase,
      ocr_debug=ocr_debug,
      planned_clicks=planned_clicks,
    )
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
    reasoning_notes=reasoning_notes,
    sub_phase=sub_phase,
    ocr_debug=ocr_debug,
    planned_clicks=planned_clicks,
  )
  return True


def run_action_with_review(state_obj, action, review_message, pre_run_hook=None, sub_phase=None, ocr_debug=None, planned_clicks=None):
  skill_plan = _skill_purchase_plan(action)
  reasoning_notes = "Use execute mode to commit clicks. Current view shows OCR/debug and planned click targets only."
  if skill_plan:
    reasoning_notes = (
      "Skill scan is complete. Continue will execute the skill purchase sub-routine first, "
      "then continue with the rest of the turn. "
      + reasoning_notes
    )
  if not review_action_before_execution(
    state_obj,
    action,
    review_message,
    sub_phase=sub_phase,
    ocr_debug=ocr_debug,
    planned_clicks=planned_clicks,
    reasoning_notes=reasoning_notes,
  ):
    return "failed"
  execution_intent = _wait_for_execute_intent(
    state_obj,
    action,
    message_prefix="Action review ready",
    reasoning_notes=reasoning_notes,
    sub_phase=sub_phase,
    ocr_debug=ocr_debug,
    planned_clicks=planned_clicks,
  )
  if execution_intent == "failed":
    return "failed"
  if execution_intent != "execute":
    # Run a non-destructive dry_run simulation so check_only exercises
    # the same open→scan→control-detect→close flow as execute mode.
    _run_trackblazer_pre_action_items(state_obj, action, commit_mode="dry_run")
    return "previewed"
  skill_purchase_result = _run_skill_purchase_plan(state_obj, action, action_count)
  if skill_purchase_result.get("status") == "failed":
    skill_result = skill_purchase_result.get("result") or {}
    skill_flow = (skill_result.get("skill_purchase_flow") or {})
    if skill_flow.get("opened") and not skill_flow.get("closed"):
      update_operator_snapshot(
        state_obj,
        action,
        phase="recovering",
        status="error",
        error_text=f"Skill purchase left skills page open: {skill_purchase_result.get('reason')}",
        sub_phase=sub_phase,
        ocr_debug=ocr_debug,
        planned_clicks=planned_clicks,
      )
      return "blocked"
    warning(
      f"[SKILL] Skill purchase failed: {skill_purchase_result.get('reason')}. "
      "Continuing with the rest of the turn."
    )
    update_operator_snapshot(
      state_obj,
      action,
      phase="executing_action",
      message=f"Skill purchase failed ({skill_purchase_result.get('reason')}); proceeding with {action.func}.",
      sub_phase=sub_phase,
      ocr_debug=ocr_debug,
      planned_clicks=planned_clicks,
    )
  # Pre-action items (energy rescue, whistle, etc.) must run before the
  # pre_run_hook (rival scout) because they may trigger a reassess that
  # re-evaluates the action entirely.  Defer the hook until after items.
  shop_purchase_result = _run_trackblazer_shop_purchases(state_obj, action)
  if shop_purchase_result.get("status") == "failed":
    shop_result = shop_purchase_result.get("result") or {}
    shop_flow = (shop_result.get("trackblazer_shop_flow") or {})
    if shop_flow.get("entered") and not shop_flow.get("closed"):
      update_operator_snapshot(
        state_obj,
        action,
        phase="recovering",
        status="error",
        error_text=f"Shop purchase left shop open: {shop_purchase_result.get('reason')}",
        sub_phase=sub_phase,
        ocr_debug=ocr_debug,
        planned_clicks=planned_clicks,
      )
      return "blocked"
    # Shop purchase failure is non-fatal — log it and continue with the
    # main action.  The shop close is handled by the finally block in
    # execute_trackblazer_shop_purchases so we should be back at the lobby.
    warning(
      f"[TB_SHOP] Shop purchase failed: {shop_purchase_result.get('reason')}. "
      "Continuing with main action."
    )
    update_operator_snapshot(
      state_obj,
      action,
      phase="executing_action",
      message=f"Shop purchase failed ({shop_purchase_result.get('reason')}); proceeding with {action.func}.",
      sub_phase=sub_phase,
      ocr_debug=ocr_debug,
      planned_clicks=planned_clicks,
    )
  if shop_purchase_result.get("status") == "executed":
    lobby_after_shop = _wait_for_lobby_after_shop_purchase()
    if not lobby_after_shop:
      update_operator_snapshot(
        state_obj,
        action,
        phase="recovering",
        status="error",
        error_text="Lobby not visible after shop purchase; shop/inventory overlay may still be up.",
        sub_phase=sub_phase,
        ocr_debug=ocr_debug,
        planned_clicks=planned_clicks,
      )
      return "blocked"
  pre_action_item_result = _run_trackblazer_pre_action_items(state_obj, action, commit_mode="full")
  if pre_action_item_result.get("status") == "failed":
    failure_reason = pre_action_item_result.get("reason") or "trackblazer_pre_action_items_failed"
    if failure_reason in {
      "failed_to_open_inventory",
      "failed_to_close_inventory",
      "required_items_not_actionable",
    }:
      update_operator_snapshot(
        state_obj,
        action,
        phase="collecting_main_state",
        message="Pre-action item flow failed; retrying the turn before choosing a fallback action.",
        sub_phase="reassess_after_item_use",
        ocr_debug=ocr_debug,
        planned_clicks=planned_clicks,
      )
      _push_turn_retry_debug(
        state_obj,
        reason="Pre-action item flow failed before the main action; retrying the same turn.",
        reasons=[failure_reason],
        before_phase="executing_action",
        context="post_item_use_failure_retry",
        event="turn_retry",
        result="reassess",
        sub_phase="reassess_after_item_use",
        phase="executing_action",
      )
      return "reassess"
    update_operator_snapshot(
      state_obj,
      action,
      phase="recovering",
      status="error",
      error_text=f"Pre-action item use failed: {failure_reason}",
      sub_phase=sub_phase,
      ocr_debug=ocr_debug,
      planned_clicks=planned_clicks,
    )
    if _trackblazer_action_failure_should_block_retry(state_obj, action):
      return "blocked"
    return "failed"
  if pre_action_item_result.get("status") == "reassess":
    update_operator_snapshot(
      state_obj,
      action,
      phase="collecting_main_state",
      message="Trackblazer items applied. Rechecking turn state before committing an action.",
      sub_phase="reassess_after_item_use",
      ocr_debug=ocr_debug,
      planned_clicks=planned_clicks,
    )
    _push_turn_retry_debug(
      state_obj,
      reason="Trackblazer item use requested reassessment before committing the action.",
      reasons=["trackblazer_item_use_reassess"],
      before_phase="executing_action",
      context="post_item_use_reassess",
      event="turn_retry",
      result="reassess",
      sub_phase="reassess_after_item_use",
      phase="executing_action",
    )
    return "reassess"
  if pre_action_item_result.get("status") == "executed":
    # After item use the inventory should be closed and we should be back
    # on the career lobby.  If the close was slow or the game lingered on
    # an overlay, the next action.run() would fail to find lobby buttons.
    # Poll briefly for a lobby anchor before proceeding.
    lobby_confirmed = _wait_for_lobby_after_item_use(state_obj, action, sub_phase=sub_phase, ocr_debug=ocr_debug, planned_clicks=planned_clicks)
    if not lobby_confirmed:
      update_operator_snapshot(
        state_obj,
        action,
        phase="recovering",
        status="error",
        error_text="Lobby not visible after item use; inventory overlay may still be up.",
        sub_phase=sub_phase,
        ocr_debug=ocr_debug,
        planned_clicks=planned_clicks,
      )
      return "blocked"
  # Run pre_run_hook (e.g. rival scout) now — after items are consumed and
  # any reassess has already returned.  This ensures energy rescue → reassess
  # completes before we open the race list.
  gate_result = _enforce_operator_race_gate_before_execute(
    state_obj,
    action,
    sub_phase=sub_phase,
    ocr_debug=ocr_debug,
    planned_clicks=planned_clicks,
  )
  if gate_result == "blocked":
    return "blocked"
  if gate_result != "reverted" and pre_run_hook is not None:
    hook_result = pre_run_hook()
    if isinstance(hook_result, str) and hook_result in {"failed", "blocked", "reassess", "previewed", "executed"}:
      return hook_result
  update_operator_snapshot(
    state_obj,
    action,
    phase="executing_action",
    message=f"Executing {action.func}.",
    sub_phase="action_run",
    ocr_debug=ocr_debug,
    planned_clicks=planned_clicks,
  )
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
  post_action_result = _resolve_post_action_resolution(state_obj, action)
  if not post_action_result:
    update_operator_snapshot(
      state_obj,
      action,
      phase="recovering",
      status="error",
      error_text=f"Post-action resolution failed after {action.func}",
      sub_phase=SUB_PHASE_POST_ACTION_RESOLUTION,
      ocr_debug=ocr_debug,
      planned_clicks=planned_clicks,
    )
    return "failed"
  return "executed"


def update_pre_action_phase(state_obj, action, message=None, reasoning_notes=None, sub_phase=None):
  is_race_action = getattr(action, "func", None) == "do_race"
  update_operator_snapshot(
    state_obj,
    action,
    phase="pre_race" if is_race_action else "pre_training",
    message=message or ("Preparing race decision." if is_race_action else "Preparing pre-training decision."),
    reasoning_notes=reasoning_notes,
    sub_phase=sub_phase or ("evaluate_race_action" if is_race_action else "evaluate_training_action"),
  )


def maybe_review_skill_purchase(state_obj, current_action_count, race_check=False, action=None):
  if action is None:
    return "skipped"
  return _attach_skill_purchase_plan(
    state_obj,
    action,
    current_action_count,
    race_check=race_check,
  )


def _wait_for_execute_intent(state_obj, action, message_prefix, reasoning_notes=None, sub_phase=None, ocr_debug=None, planned_clicks=None):
  while True:
    execution_intent = bot.get_execution_intent()
    if execution_intent == "execute":
      return execution_intent

    message = "check_only mode active; press Continue to execute this turn."
    notes = (
      f"{reasoning_notes or ''} "
      "Press Continue to execute this action once without switching mode."
    ).strip()

    update_operator_snapshot(
      state_obj,
      action,
      phase="waiting_for_confirmation",
      status="idle",
      message=message,
      reasoning_notes=notes,
      sub_phase=sub_phase,
      ocr_debug=ocr_debug,
      planned_clicks=planned_clicks,
    )

    bot.begin_review_wait()
    publish_runtime_state()
    while bot.is_bot_running and not bot.stop_event.is_set():
      if bot.review_event.wait(timeout=0.1):
        break

    if not bot.is_bot_running or bot.stop_event.is_set():
      bot.cancel_review_wait()
      update_operator_snapshot(
        state_obj,
        action,
        phase="recovering",
        status="error",
        error_text=f"{message_prefix} interrupted by stop request.",
        sub_phase=sub_phase,
        ocr_debug=ocr_debug,
        planned_clicks=planned_clicks,
      )
      return "failed"

    execution_intent = bot.get_execution_intent()
    if execution_intent == "check_only":
      info("[REVIEW] Continue pressed in check_only mode; executing this turn as one-shot execute.")
      return "execute"

def career_lobby(dry_run_turn=False):
  global last_state, action_count, non_match_count, scenario_detection_attempts, last_trackblazer_shop_refresh_turn, _cached_trackblazer_inventory, _cached_trackblazer_inventory_turn
  non_match_count = 0
  action_count=0
  scenario_detection_attempts = 0
  last_trackblazer_shop_refresh_turn = None
  _cached_trackblazer_inventory = None
  _cached_trackblazer_inventory_turn = None
  bot.clear_trackblazer_shop_check_request()
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
      if _detect_trackblazer_complete_career_banner(context="lobby_scan").get("detected"):
        _stop_for_trackblazer_complete_career(context="lobby_scan")
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
        bot.push_debug_history({"event": "template_match", "asset": "event", "result": "found", "context": "lobby_scan"})
        select_event()
        continue
      if click_match(matches.get("inspiration")):
        bot.push_debug_history({"event": "click", "asset": "inspiration", "result": "clicked", "context": "lobby_scan"})
        info("Pressed inspiration.")
        non_match_count = 0
        continue
      if click_match(matches.get("next")):
        bot.push_debug_history({"event": "click", "asset": "next", "result": "clicked", "context": "lobby_scan"})
        info("Pressed next.")
        non_match_count = 0
        continue
      if click_match(matches.get("next2")):
        bot.push_debug_history({"event": "click", "asset": "next2", "result": "clicked", "context": "lobby_scan"})
        info("Pressed next2.")
        non_match_count = 0
        continue
      if matches.get("shop_refresh", False):
        bot.push_debug_history({"event": "template_match", "asset": "shop_refresh", "result": "found", "context": "lobby_scan"})
        info("Shop refresh popup detected — shop has been refreshed.")
        # The primary Trackblazer home for this popup is post_action_resolution.
        # Keep a lobby-scan fallback so an unexpected stale popup does not
        # soft-lock the run after a restart or timeout.
        if _trackblazer_scenario_active():
          warning("[TB_POST] Shop refresh popup reached generic lobby scan; using fallback dismissal path.")
          refresh_result = _handle_trackblazer_shop_refresh_popup()
          if refresh_result.get("handled"):
            non_match_count = 0
          else:
            non_match_count += 1
        else:
          cancel_match = device_action.match_template("assets/buttons/cancel_btn.png", region_ltrb=constants.GAME_WINDOW_BBOX, threshold=0.9)
          if cancel_match:
            x, y, w, h = cancel_match[0]
            device_action.click(target=(x + w // 2, y + h // 2), text="Dismissed shop refresh popup.")
            info("Dismissed shop refresh popup.")
            non_match_count = 0
          else:
            non_match_count += 1
        continue
      if matches.get("cancel", False):
        clock_icon = device_action.match_template("assets/icons/clock_icon.png", screenshot=screenshot, threshold=0.9)
        if clock_icon:
          bot.push_debug_history({"event": "template_match", "asset": "cancel + clock_icon", "result": "lost_race", "context": "lobby_scan"})
          info("Lost race, wait for input.")
          non_match_count += 1
        elif click_match(matches.get("cancel")):
          bot.push_debug_history({"event": "click", "asset": "cancel", "result": "clicked", "context": "lobby_scan"})
          info("Pressed cancel.")
          non_match_count = 0
        continue
      if click_match(matches.get("retry")):
        bot.push_debug_history({"event": "click", "asset": "retry", "result": "clicked", "context": "lobby_scan"})
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
        bot.push_debug_history({"event": "click", "asset": "ok_2_btn", "result": "clicked", "context": "lobby_scan"})
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
            _template_debug_entry("shop_refresh_icon", "assets/icons/shop_refresh.png", bbox_key="GAME_WINDOW_BBOX", parsed_value=len(matches.get("shop_refresh", []))),
          ],
          reasoning_notes=f"Stable screen anchors were not found yet. anchor_counts={stable_anchor_counts}",
        )
        print(".", end="")
        non_match_count += 1
        continue
      else:
        bot.push_debug_history({"event": "state", "asset": "stable_career_screen", "result": "matched", "context": "lobby_scan"})
        info(f"Stable career screen matched, moving to state collection. anchor_counts={stable_anchor_counts}")
        if constants.SCENARIO_NAME == "":
          if config.SKIP_SCENARIO_DETECTION:
            constants.SCENARIO_NAME = config.STARTUP_SCENARIO_OVERRIDE or "default"
            info(f"Skipping scenario detection by config; assuming scenario '{constants.SCENARIO_NAME}'.")
          else:
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
      _restore_turn_from_last_state(state_obj)

      state_validation = strategy.validate_state_details(state_obj)
      if not state_validation.get("valid"):
        validation_reasons = list(state_validation.get("invalid_reasons") or [])
        state_validation = dict(state_validation)
        state_validation.update(
          {
            "reason": "main_state_invalid_before_training_scan",
            "context": "pre_training_scan",
            "before_phase": "collecting_main_state",
            "same_turn_retry": True,
            "turn_label": f"{state_obj.get('year', '?')} / {state_obj.get('turn', '?')}",
          }
        )
        state_obj["state_validation"] = state_validation
        validation_ocr_debug = _state_validation_ocr_debug_entries(state_obj, state_validation)
        invalid_reason_text = ", ".join(validation_reasons) if validation_reasons else "unknown"
        update_operator_snapshot(
          state_obj,
          action,
          phase="recovering",
          status="error",
          message="Main state invalid; retrying before training scan.",
          error_text=f"Invalid main state before training scan: {invalid_reason_text}",
          reasoning_notes=(
            "Pre-training validation rejected the current turn state. "
            "Retrying the same turn without opening training."
          ),
          sub_phase="pre_training_state_validation",
          ocr_debug=validation_ocr_debug,
        )
        _push_turn_retry_debug(
          state_obj,
          reason="Invalid main state before training scan.",
          reasons=validation_reasons,
          before_phase="collecting_main_state",
          context="pre_training_scan",
          event="state_validation",
          result="invalid_retry",
          sub_phase="pre_training_state_validation",
          phase="collecting_main_state",
        )
        continue

      trackblazer_pre_debut = (
        constants.SCENARIO_NAME in ("mant", "trackblazer")
        and state_obj.get("year") == "Junior Year Pre-Debut"
      )
      if constants.SCENARIO_NAME in ("mant", "trackblazer") and not trackblazer_pre_debut:
        # Detect lobby buff icon (megaphone/ankle weight effect active).
        buff_match = device_action.locate(
          constants.TRACKBLAZER_LOBBY_BUFF_ICON,
          region_ltrb=constants.TRACKBLAZER_LOBBY_BUFF_BBOX,
          confidence=0.7,
          template_scaling=1.0 / device_action.GLOBAL_TEMPLATE_SCALING,
        )
        state_obj["trackblazer_buff_active"] = bool(buff_match)
        state_obj["trackblazer_allow_buff_override"] = bot.get_trackblazer_allow_buff_override()
        if buff_match:
          info("[TB_BUFF] Lobby buff icon detected — a megaphone or similar effect is active.")

        execution_intent = bot.get_execution_intent()
        if _restore_cached_trackblazer_inventory(state_obj, current_turn_number=action_count):
          info("[TB_INV] Using cached inventory (no changes since last scan).")
          state_obj["trackblazer_inventory_flow"] = {
            "trigger": "cached",
            "cached": True,
            "skipped": True,
            "reason": "using_cached_inventory",
          }
          _push_flow_decision_debug(
            state_obj,
            asset="trackblazer_inventory",
            result="using_cached",
            note="reason=using_cached_inventory",
            context="pre_training_gate",
            phase="checking_inventory",
            sub_phase="scan_items",
          )
        else:
          _push_flow_decision_debug(
            state_obj,
            asset="trackblazer_inventory",
            result="scan_required",
            note="reason=cache_miss_or_expired",
            context="pre_training_gate",
            phase="checking_inventory",
            sub_phase="scan_items",
          )
          update_operator_snapshot(phase="checking_inventory", message="Scanning Trackblazer inventory.", sub_phase="scan_items")
          state_obj = collect_trackblazer_inventory(
            state_obj,
            allow_open_non_execute=execution_intent != "execute",
          )
          if _trackblazer_inventory_flow_cacheable(state_obj.get("trackblazer_inventory_flow")):
            _cache_trackblazer_inventory(state_obj, turn_key=action_count)
          else:
            _invalidate_trackblazer_inventory_cache()
        _copy_trackblazer_inventory_snapshot(state_obj)
        current_trackblazer_turn = _trackblazer_turn_key(state_obj)
        pending_shop_check = bot.has_pending_trackblazer_shop_check()
        pending_shop_reason = bot.get_pending_trackblazer_shop_check_reason()
        never_scanned = last_trackblazer_shop_refresh_turn is None
        automatic_turn_scan = (
          never_scanned
          or (execution_intent == "check_only" and current_trackblazer_turn != last_trackblazer_shop_refresh_turn)
        )
        shop_flow = {}
        ready_after_shop_scan = True
        if pending_shop_check or automatic_turn_scan:
          _push_flow_decision_debug(
            state_obj,
            asset="trackblazer_shop_gate",
            result="scan_required",
            note=(
              f"pending={bool(pending_shop_check)}; "
              f"pending_reason={pending_shop_reason or '-'}; "
              f"automatic={bool(automatic_turn_scan)}; "
              f"current_turn={current_trackblazer_turn}; "
              f"last_refresh_turn={last_trackblazer_shop_refresh_turn}"
            ),
            context="pre_training_gate",
            phase="checking_shop",
            sub_phase="scan_shop",
          )
          update_operator_snapshot(phase="checking_shop", message="Scanning Trackblazer shop.", sub_phase="scan_shop")
          from scenarios.trackblazer import check_trackblazer_shop_inventory
          trigger = pending_shop_reason or ("first_scan" if never_scanned else "automatic" if automatic_turn_scan else "pending_shop_check")
          shop_result = check_trackblazer_shop_inventory(
            trigger=trigger,
            year=state_obj.get("year"),
          )
          shop_flow = (shop_result or {}).get("trackblazer_shop_flow") or {}
          shop_entry_result = shop_flow.get("entry_result") or {}
          shop_entry_check = (shop_entry_result.get("shop_check") or {})
          shop_best_method = shop_entry_check.get("best_method") or {}
          shop_best_method_name = (
            shop_best_method.get("method")
            or shop_entry_result.get("method")
            or "unknown"
          )
          shop_entry_reason = (
            shop_entry_result.get("reason")
            or shop_flow.get("reason")
            or "failed_to_enter_shop"
          )
          shop_missing_required = list(shop_best_method.get("missing_required") or [])
          if shop_flow.get("entered") or shop_flow.get("scan_result") or shop_flow.get("closed"):
            _merge_trackblazer_shop_result(state_obj, shop_result)
          if shop_entry_result.get("clicked") and not shop_flow.get("entered"):
            warning(
              "[TB_SHOP] Shop entry clicked but was not verified; "
              "retrying the same turn from lobby before opening training."
            )
            _wait_for_lobby_after_shop_purchase(max_wait=4.0)
            update_operator_snapshot(
              state_obj,
              action,
              phase="recovering",
              status="error",
              message="Shop entry was clicked but not verified; retrying turn before training scan.",
              error_text="Trackblazer shop entry verification failed after click.",
              reasoning_notes=(
                "Skipped training scan for safety after a shop-entry misfire. "
                "Retrying the same turn from the lobby."
              ),
              sub_phase="scan_shop",
            )
            _push_turn_retry_debug(
              state_obj,
              reason="Trackblazer shop entry clicked but was not verified.",
              reasons=[shop_flow.get("reason") or "shop_verification_failed"],
              before_phase="checking_shop",
              context="scan_shop",
              event="trackblazer_shop_entry",
              result="invalid_retry",
              sub_phase="scan_shop",
              phase="checking_shop",
            )
            continue
          if pending_shop_check and not shop_flow.get("entered"):
            _push_flow_decision_debug(
              state_obj,
              asset="trackblazer_shop_gate",
              result="retry_before_training",
              note=(
                f"pending shop check failed; reason={shop_entry_reason}; "
                f"best_method={shop_best_method_name}; "
                f"missing_required={','.join(shop_missing_required) or 'none'}"
              ),
              context="pre_training_gate",
              phase="checking_shop",
              sub_phase="scan_shop",
            )
            warning(
              "[TB_SHOP] Pending shop check did not enter the shop; "
              "retrying the same turn before training scan. "
              f"reason={shop_entry_reason}; best_method={shop_best_method_name}; "
              f"missing_required={shop_missing_required or ['unknown']}."
            )
            update_operator_snapshot(
              state_obj,
              action,
              phase="recovering",
              status="error",
              message="Pending Trackblazer shop check failed; retrying before training scan.",
              error_text="Trackblazer pending shop check could not enter the shop.",
              reasoning_notes=(
                "A pending Trackblazer shop refresh was queued, but the shop entry "
                "flow did not verify a shop screen. Retrying the same turn instead "
                "of scanning trainings with stale/empty shop state."
              ),
              sub_phase="scan_shop",
            )
            _push_turn_retry_debug(
              state_obj,
              reason="Pending Trackblazer shop check could not enter the shop.",
              reasons=[
                shop_entry_reason,
                f"best_method={shop_best_method_name}",
                f"missing_required={','.join(shop_missing_required) or 'none'}",
                f"pending_reason={pending_shop_reason or 'pending_shop_check'}",
              ],
              before_phase="checking_shop",
              context="scan_shop",
              event="trackblazer_shop_entry",
              result="invalid_retry",
              sub_phase="scan_shop",
              phase="checking_shop",
            )
            continue
          if shop_flow.get("entered") and shop_flow.get("closed"):
            ready_after_shop_scan = _wait_for_lobby_after_shop_purchase()
            if not ready_after_shop_scan:
              warning("[TB_SHOP] Ready screen not confirmed after shop scan; forced-race detection may be stale.")
          elif pending_shop_check:
            warning(
              "[TB_SHOP] Pending shop check was queued but shop scan did not complete; "
              f"reason={shop_flow.get('reason') or 'unknown'}."
            )
        elif execution_intent == "check_only":
          info(
            "[TB_SHOP] Skipping automatic shop recheck for this turn; "
            f"already refreshed inventory after shop for {current_trackblazer_turn}."
          )
        else:
          _push_flow_decision_debug(
            state_obj,
            asset="trackblazer_shop_gate",
            result="skip_scan",
            note=(
              f"pending={bool(pending_shop_check)}; automatic={bool(automatic_turn_scan)}; "
              f"execution_intent={execution_intent}; current_turn={current_trackblazer_turn}; "
              f"last_refresh_turn={last_trackblazer_shop_refresh_turn}"
            ),
            context="pre_training_gate",
            phase="checking_shop",
            sub_phase="scan_shop",
          )

        from scenarios.trackblazer import inspect_climax_race_day_detection

        climax_detection = inspect_climax_race_day_detection(log_result=True)
        state_obj["trackblazer_climax_race_day"] = bool(climax_detection.get("detected"))
        state_obj["trackblazer_climax_race_day_banner"] = bool((climax_detection.get("banner") or {}).get("passed_threshold"))
        state_obj["trackblazer_climax_race_day_button"] = bool((climax_detection.get("button") or {}).get("passed_threshold"))

        if shop_flow.get("entered") and shop_flow.get("closed"):
          bot.push_debug_history({
            "event": "trackblazer_shop_post_close",
            "asset": "shop_flow",
            "result": "closed",
            "context": "trackblazer_shop_inventory",
            "ready_after_shop_scan": bool(ready_after_shop_scan),
            "climax_race_day": bool(state_obj.get("trackblazer_climax_race_day")),
          })
          if state_obj.get("trackblazer_climax_race_day"):
            info("[TB_RACE] Forced Climax race day visible after shop; skipping post-shop inventory refresh.")
            last_trackblazer_shop_refresh_turn = current_trackblazer_turn
            bot.clear_trackblazer_shop_check_request()
          elif ready_after_shop_scan:
            bot.push_debug_history({
              "event": "trackblazer_shop_post_close",
              "asset": "inventory_refresh",
              "result": "refreshing",
              "context": "trackblazer_shop_inventory",
              "trigger": "post_shop_refresh",
            })
            update_operator_snapshot(
              phase="checking_inventory",
              message="Refreshing Trackblazer inventory after shop.",
              sub_phase="scan_items_post_shop",
            )
            state_obj = collect_trackblazer_inventory(
              state_obj,
              allow_open_non_execute=True,
              trigger="post_shop_refresh",
            )
            _cache_trackblazer_inventory(state_obj, turn_key=action_count)
            last_trackblazer_shop_refresh_turn = current_trackblazer_turn
            bot.clear_trackblazer_shop_check_request()
          elif pending_shop_check:
            bot.push_debug_history({
              "event": "trackblazer_shop_post_close",
              "asset": "inventory_refresh",
              "result": "skipped_no_ready_screen",
              "context": "trackblazer_shop_inventory",
              "trigger": "post_shop_refresh",
            })
            warning("[TB_SHOP] Skipping post-shop inventory refresh because the ready screen never settled.")

        # Detect "Scheduled Race" button on the lobby race button area.
        _inv_scale = 1.0 / device_action.GLOBAL_TEMPLATE_SCALING
        sched_btn = device_action.match_template(
          constants.TRACKBLAZER_LOBBY_SCHEDULED_RACE,
          screenshot,
          threshold=0.8,
          template_scaling=_inv_scale,
        )
        if sched_btn:
          state_obj["trackblazer_lobby_scheduled_race"] = True
          info("[TB_RACE] Scheduled Race button detected on lobby screen.")
          bot.push_debug_history({
            "event": "template_match",
            "asset": "lobby_scheduled_race.png",
            "result": "found",
            "context": "lobby_scan_trackblazer",
          })

      if state_obj.get("trackblazer_climax_race_day"):
        forced_reason = "Forced Climax race-day indicator detected on lobby screen"
        info("[TB_RACE] Taking early forced-race branch before training scan.")
        _push_flow_decision_debug(
          state_obj,
          asset="pre_training_branch",
          result="forced_race",
          note=forced_reason,
          context="pre_training_gate",
          phase="collecting_race_state",
        )
        action.func = "do_race"
        action["is_race_day"] = True
        action["year"] = state_obj["year"]
        action["trackblazer_climax_race_day"] = True
        action["trackblazer_race_decision"] = {
          "should_race": True,
          "reason": forced_reason,
          "forced_race_day": True,
          "race_available": True,
          "rival_indicator": False,
          "prefer_rival_race": False,
          "g1_forced": True,
          "race_name": "any",
        }
        action = _attach_trackblazer_pre_action_item_plan(state_obj, action)
        state_obj["rival_indicator_detected"] = False
        update_pre_action_phase(
          state_obj,
          action,
          message="Forced Climax race day detected. Skipping training scan and preparing race entry.",
        )
        skill_result = maybe_review_skill_purchase(state_obj, action_count, race_check=True, action=action)
        if skill_result in ("failed", "previewed"):
          continue
        forced_race_result = run_action_with_review(
          state_obj,
          action,
          "Forced Climax race day detected. Review before entering race.",
          sub_phase="preview_race_selection",
        )
        if forced_race_result == "executed":
          record_and_finalize_turn(state_obj, action)
          continue
        elif forced_race_result == "previewed":
          continue
        else:
          action.func = None
          action.options.pop("is_race_day", None)
          action.options.pop("year", None)
          action.options.pop("trackblazer_climax_race_day", None)

      optional_race_blocked, race_gate = _operator_race_gate_blocks_optional_races(state_obj)

      if config.PRIORITIZE_MISSIONS_OVER_G1 and config.DO_MISSION_RACES_IF_POSSIBLE and state_obj["race_mission_available"]:
        if optional_race_blocked:
          info(f"[RACE_GATE] {_operator_race_gate_message(race_gate, context='mission racing')}")
        else:
          debug(f"Mission race logic entered with priority.")
          action.func = "do_race"
          action["race_name"] = "any"
          action["race_image_path"] = "assets/ui/match_track.png"
          action["race_mission_available"] = True
          update_pre_action_phase(
            state_obj,
            action,
            message="Mission race candidate detected. Preparing pre-race decision.",
          )
          skill_result = maybe_review_skill_purchase(state_obj, action_count, race_check=True, action=action)
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
            _clear_optional_race_action_fields(action)

      # check and do scheduled races. Dirty version, should be cleaned up.
      action = strategy.check_scheduled_races(state_obj, action)
      if "race_name" in action.options:
        action.func = "do_race"
        action = _attach_trackblazer_pre_action_item_plan(state_obj, action)
        info(f"Taking action: {action.func}")
        update_pre_action_phase(
          state_obj,
          action,
          message="Scheduled race candidate detected. Preparing pre-race decision.",
        )
        skill_result = maybe_review_skill_purchase(state_obj, action_count, race_check=True, action=action)
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
          _clear_optional_race_action_fields(action)

      # Trackblazer lobby "Scheduled Race" button — visual indicator on the race button area.
      if (
        action.func != "do_race"
        and state_obj.get("trackblazer_lobby_scheduled_race")
      ):
        if optional_race_blocked:
          info(f"[RACE_GATE] {_operator_race_gate_message(race_gate, context='scheduled racing')}")
        else:
          from core.trackblazer_item_use import _hammer_usage_state, _safe_int, _HAMMER_TIERS
          held_quantities = {}
          inventory = state_obj.get("trackblazer_inventory") or {}
          for item_key in _HAMMER_TIERS:
            held_quantities[item_key] = _safe_int(
              (inventory.get(item_key) or {}).get("quantity"), 0
            )
          _, hammer_spendable = _hammer_usage_state(held_quantities)
          total_spendable = sum(hammer_spendable.values())
          info(
            f"[TB_RACE] Lobby scheduled race detected. "
            f"Hammer inventory: {held_quantities}, spendable (surplus beyond 3 reserved): {hammer_spendable}"
          )
          action.func = "do_race"
          action["scheduled_race"] = True
          action["race_name"] = "any"
          action["trackblazer_lobby_scheduled_race"] = True
          action["hammer_spendable"] = total_spendable
          action["trackblazer_race_lookahead"] = get_race_lookahead_energy_advice(
            state_obj,
            getattr(config, "OPERATOR_RACE_SELECTOR", None),
          )
          action["trackblazer_race_lookahead_energy_item_key"] = None
          action = _attach_trackblazer_pre_action_item_plan(state_obj, action)
          update_pre_action_phase(
            state_obj,
            action,
            message=f"Trackblazer scheduled race button detected on lobby. Surplus hammers: {total_spendable}.",
          )
          skill_result = maybe_review_skill_purchase(state_obj, action_count, race_check=True, action=action)
          if skill_result in ("failed", "previewed"):
            continue
          tb_sched_result = run_action_with_review(
            state_obj,
            action,
            f"Trackblazer scheduled race detected (surplus hammers: {total_spendable}). Review before race entry.",
            sub_phase="preview_race_selection",
          )
          if tb_sched_result == "executed":
            record_and_finalize_turn(state_obj, action)
            continue
          elif tb_sched_result == "previewed":
            continue
          else:
            action.func = None
            _clear_optional_race_action_fields(action)

      if (not config.PRIORITIZE_MISSIONS_OVER_G1) and config.DO_MISSION_RACES_IF_POSSIBLE and state_obj["race_mission_available"]:
        if optional_race_blocked:
          info(f"[RACE_GATE] {_operator_race_gate_message(race_gate, context='mission racing')}")
        else:
          debug(f"Mission race logic entered.")
          action.func = "do_race"
          action["race_name"] = "any"
          action["race_image_path"] = "assets/ui/match_track.png"
          action["prioritize_missions_over_g1"] = config.PRIORITIZE_MISSIONS_OVER_G1
          action["race_mission_available"] = True
          update_pre_action_phase(
            state_obj,
            action,
            message="Mission race candidate detected. Preparing pre-race decision.",
          )
          skill_result = maybe_review_skill_purchase(state_obj, action_count, race_check=True, action=action)
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
            _clear_optional_race_action_fields(action)

      # check and do goal races. Dirty version, should be cleaned up.
      if not "Achieved" in state_obj["criteria"]:
        action = strategy.decide_race_for_goal(state_obj, action)
        if action.func == "do_race":
          info(f"Taking action: {action.func}")
          update_pre_action_phase(
            state_obj,
            action,
            message="Goal race candidate detected. Preparing pre-race decision.",
          )
          skill_result = maybe_review_skill_purchase(state_obj, action_count, race_check=True, action=action)
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
            _clear_optional_race_action_fields(action)

      training_function_name = strategy.get_training_template(state_obj)['training_function']

      # Apply Trackblazer scoring mode override so operator console shows correct function
      if constants.SCENARIO_NAME in ("mant", "trackblazer"):
        scoring_mode = bot.get_trackblazer_scoring_mode()
        if scoring_mode == "stat_focused":
          training_function_name = "stat_weight_training"

      _push_flow_decision_debug(
        state_obj,
        asset="training_scan",
        result="start",
        note=(
          f"training_function={training_function_name}; "
          f"pending_shop_check={bot.has_pending_trackblazer_shop_check()}; "
          f"shop_items={len(state_obj.get('trackblazer_shop_items') or [])}; "
          f"inventory_items={len(((state_obj.get('trackblazer_inventory_summary') or {}).get('items_detected') or []))}"
        ),
        context="pre_training_gate",
        phase="collecting_training_state",
      )
      update_operator_snapshot(phase="collecting_training_state", message="Scanning all trainings.")
      state_obj = collect_training_state(state_obj, training_function_name)
      update_pre_action_phase(
        state_obj,
        action,
        message="Training scan complete. Preparing pre-training decision.",
      )

      # Collect race state for Trackblazer: checks the rival indicator on
      # the race button (cheap screenshot check, no game interaction).
      # The full race decision is deferred until after strategy.decide()
      # populates training data. The expensive rival scout is deferred
      # to execution time (pre_run_hook).
      if constants.SCENARIO_NAME in ("mant", "trackblazer"):
        update_operator_snapshot(phase="collecting_race_state", message="Checking race indicators.")
        from scenarios.trackblazer import check_rival_race_indicator
        rival_indicator = check_rival_race_indicator(state_obj)
        state_obj["rival_indicator_detected"] = rival_indicator
        update_operator_snapshot(
          state_obj, action,
          phase="collecting_race_state",
          message=f"Rival indicator: {'detected' if rival_indicator else 'not detected'}",
          sub_phase="check_rival_indicator",
        )

      log_encoded(f"{state_obj}", "Encoded state: ")
      info(f"State: {state_obj}")

      update_operator_snapshot(phase="evaluating_strategy", message="Evaluating strategy.")
      action = strategy.decide(state_obj, action)
      update_operator_snapshot(state_obj, action, phase="evaluating_strategy", message="Strategy decision ready.")
      _push_flow_decision_debug(
        state_obj,
        asset="strategy_decision",
        result=_action_func(action) or "none",
        note=_strategy_decision_note(state_obj, action),
        context="strategy",
        phase="evaluating_strategy",
      )

      if (
        constants.SCENARIO_NAME in ("mant", "trackblazer")
      and action.func == "do_rest"
      and bot.get_trackblazer_scoring_mode() == "stat_focused"
      ):
        strong_training_score = _trackblazer_training_score(action)
        strong_training_score_threshold = _trackblazer_training_score_threshold()
        if (
          strong_training_score is not None
          and strong_training_score >= strong_training_score_threshold
          and action.get("training_name")
        ):
          action.func = "do_training"
          info(
            f"[TB_RACE] Promoting rest to training because stat-focused score is strong "
            f"({strong_training_score:.1f} >= {strong_training_score_threshold})."
          )
          update_operator_snapshot(
            state_obj,
            action,
            phase="evaluating_strategy",
            message=(
              "Trackblazer strong training score detected. "
              "Keeping the training turn instead of resting."
            ),
            sub_phase="evaluate_trackblazer_race",
            reasoning_notes=f"training_score={strong_training_score:.1f} | threshold={strong_training_score_threshold}",
          )

      # Trackblazer race-vs-training gate: evaluate race-vs-training using the
      # rival indicator collected earlier (no game interaction here).  The
      # expensive rival scout is deferred to execution time (pre_run_hook).
      # Skip during Junior Pre-Debut — race list is not available yet.
      if constants.SCENARIO_NAME in ("mant", "trackblazer") and not trackblazer_pre_debut:
        race_decision = evaluate_trackblazer_race(state_obj, action)
        action["trackblazer_race_decision"] = race_decision

        if race_decision.get("prefer_rest_over_weak_training") and action.func != "do_rest":
          action["_rival_fallback_func"] = action.func
          action["_rival_fallback_training_name"] = action.get("training_name")
          action["_rival_fallback_training_data"] = action.get("training_data")
          action.func = "do_rest"
          action["energy_level"] = state_obj.get("energy_level", 0)
          action["disable_skip_turn_fallback"] = True
          info(f"[TB_RACE] Overriding to rest: {race_decision['reason']}")
          update_operator_snapshot(
            state_obj, action,
            phase="evaluating_strategy",
            message=f"Trackblazer weak-training rest: {race_decision['reason']}",
            sub_phase="evaluate_trackblazer_race",
            reasoning_notes=(
              f"prefer_rest=True | "
              f"training_score={race_decision.get('training_score')} | "
              f"training_total={race_decision.get('training_total_stats')}"
            ),
          )

        elif race_decision.get("should_race") and action.func != "do_race":
          # Save fallback in case rival scout fails and we need to revert.
          action["_rival_fallback_func"] = action.func
          action["_rival_fallback_training_name"] = action.get("training_name")
          action["_rival_fallback_training_data"] = action.get("training_data")
          action.func = "do_race"
          action["race_name"] = race_decision.get("race_name") or "any"
          action["race_grade_target"] = race_decision.get("race_tier_target")
          if race_decision.get("prefer_rival_race"):
            action["prefer_rival_race"] = True
          if race_decision.get("fallback_non_rival_race"):
            action["fallback_non_rival_race"] = True
          info(f"[TB_RACE] Overriding to race: {race_decision['reason']}")
          update_operator_snapshot(
            state_obj, action,
            phase="evaluating_strategy",
            message=f"Trackblazer race gate: {race_decision['reason']}",
            sub_phase="evaluate_trackblazer_race",
            reasoning_notes=(
              f"should_race={race_decision.get('should_race')} | "
              f"target={race_decision.get('race_tier_target') or '-'} | "
              f"race={race_decision.get('race_name') or '-'} | "
              f"training_total={race_decision.get('training_total_stats')} | "
              f"supports={race_decision.get('training_supports')}"
            ),
          )
        elif race_decision.get("should_race") and action.func == "do_race":
          if race_decision.get("race_name") and action.get("race_name") in (None, "", "any"):
            action["race_name"] = race_decision["race_name"]
          if race_decision.get("race_tier_target") and not action.get("race_grade_target"):
            action["race_grade_target"] = race_decision["race_tier_target"]

        elif not race_decision.get("should_race") and action.func == "do_race" and not action.get("scheduled_race") and not action.get("is_race_day"):
          # Strategy wanted to race (e.g. rival fallback from evaluate_training_alternatives)
          # but the Trackblazer gate says train. Revert if fallback data is available.
          fallback_func = action.get("_rival_fallback_func")
          if fallback_func:
            action.func = fallback_func
            if action.get("_rival_fallback_training_name"):
              action["training_name"] = action["_rival_fallback_training_name"]
            if action.get("_rival_fallback_training_data"):
              action["training_data"] = action["_rival_fallback_training_data"]
            action.options.pop("race_name", None)
            action.options.pop("prefer_rival_race", None)
            action.options.pop("race_grade_target", None)
            info(f"[TB_RACE] Race gate vetoed race, reverted to {fallback_func}: {race_decision['reason']}")
          update_operator_snapshot(
            state_obj, action,
            phase="evaluating_strategy",
            message=f"Trackblazer race gate: prefer training — {race_decision['reason']}",
            sub_phase="evaluate_trackblazer_race",
            reasoning_notes=(
              f"should_race={race_decision.get('should_race')} | "
              f"target={race_decision.get('race_tier_target') or '-'} | "
              f"race={race_decision.get('race_name') or '-'} | "
              f"training_total={race_decision.get('training_total_stats')} | "
              f"supports={race_decision.get('training_supports')}"
            ),
          )

      optional_race_blocked, race_gate = _operator_race_gate_blocks_optional_races(state_obj)
      if (
        optional_race_blocked
        and action.func == "do_race"
        and not action.get("is_race_day")
        and not action.get("trackblazer_climax_race_day")
      ):
        blocked_reason = _operator_race_gate_message(race_gate)
        if _revert_optional_race_to_fallback(action):
          action["trackblazer_race_decision"] = {
            "should_race": False,
            "reason": blocked_reason,
            "race_name": race_gate.get("selected_race"),
            "race_available": False,
            "prefer_rival_race": False,
          }
          info(f"[RACE_GATE] {blocked_reason} Reverted to {_action_func(action)} before review.")
          update_operator_snapshot(
            state_obj,
            action,
            phase="evaluating_strategy",
            message=blocked_reason,
            sub_phase="evaluate_trackblazer_race",
            reasoning_notes="operator_race_gate_veto",
          )

      if state_obj["turn"] == "Race Day" or state_obj.get("trackblazer_climax_race_day"):
        forced_reason = (
          "Forced Climax race-day indicator detected on lobby screen"
          if state_obj.get("trackblazer_climax_race_day") else
          "Turn OCR reports Race Day"
        )
        action.func = "do_race"
        action["is_race_day"] = True
        action["year"] = state_obj["year"]
        action["trackblazer_climax_race_day"] = bool(state_obj.get("trackblazer_climax_race_day"))
        action["trackblazer_race_decision"] = {
          "should_race": True,
          "reason": forced_reason,
          "forced_race_day": True,
          "race_available": True,
          "rival_indicator": bool(state_obj.get("rival_indicator_detected")),
          "prefer_rival_race": False,
          "g1_forced": True,
          "race_name": "any",
        }
        info(f"[TB_RACE] {forced_reason}. Overriding final action to race.")
        update_operator_snapshot(
          state_obj, action,
          phase="evaluating_strategy",
          message=forced_reason,
          sub_phase="evaluate_trackblazer_race",
          reasoning_notes="Forced race day bypasses normal training/rest options at the end of the decision tree.",
        )

      if not trackblazer_pre_debut:
        action = _attach_trackblazer_pre_action_item_plan(state_obj, action)

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
        _push_turn_retry_debug(
          state_obj,
          reason="Strategy returned no_action.",
          reasons=(state_obj.get("state_validation") or {}).get("invalid_reasons") or ["strategy_validation_failed"],
          before_phase="evaluating_strategy",
          context="strategy_decision",
          event="turn_retry",
          result="no_action_retry",
          sub_phase="evaluate_training_action",
          phase="evaluating_strategy",
        )
        update_operator_snapshot(state_obj, action, phase="recovering", status="error", error_text="State invalid, retrying.")
        info("State is invalid, retrying...")
        debug(f"State: {state_obj}")
      elif action.func == "skip_turn":
        _push_turn_retry_debug(
          state_obj,
          reason="Strategy returned skip_turn.",
          reasons=["no_actions_available"],
          before_phase="evaluating_strategy",
          context="strategy_decision",
          event="turn_retry",
          result="skip_turn_retry",
          sub_phase="evaluate_training_action",
          phase="evaluating_strategy",
        )
        update_operator_snapshot(state_obj, action, phase="recovering", message="Skipping turn, retrying.")
        info("Skipping turn, retrying...")
      else:
        skill_result = maybe_review_skill_purchase(
          state_obj,
          action_count,
          race_check=bool(action.func == "do_race"),
          action=action,
        )
        if skill_result == "failed":
          continue
        info(f"Taking action: {action.func}")
        update_pre_action_phase(
          state_obj,
          action,
          message="Reviewing final pre-action state.",
        )

        # go to skill buy function if we come across a do_race function, conditions are handled in buy_skill
        if dry_run_turn:
          update_operator_snapshot(state_obj, action, phase="recovering", message="Dry run turn requested; quitting.")
          info("Dry run turn, quitting.")
          quit()

        # Build a pre_run_hook that scouts the rival race list when the
        # user commits a rival-race action.  The scout opens the race list,
        # checks aptitude, and backs out.  If no suitable rival is found
        # the action reverts to the training fallback.
        pre_run_hook = None
        if action.func == "do_race" and action.get("prefer_rival_race"):
          def _rival_scout_hook():
            from scenarios.trackblazer import scout_rival_race
            update_operator_snapshot(state_obj, action, phase="scouting_rival_race", message="Scouting race list for rival race...")
            scout_result = scout_rival_race()
            action["rival_scout"] = scout_result
            if not scout_result.get("rival_found"):
              fallback_func = action.get("_rival_fallback_func", "do_training")
              info(f"[RIVAL] No rival race found, reverting to {fallback_func}.")
              action.func = fallback_func
              if action.get("_rival_fallback_training_name"):
                action["training_name"] = action["_rival_fallback_training_name"]
              if action.get("_rival_fallback_training_data"):
                action["training_data"] = action["_rival_fallback_training_data"]
              action.options.pop("race_name", None)
              action.options.pop("prefer_rival_race", None)
              update_operator_snapshot(state_obj, action, phase="executing_action", message="No rival race available. Reverted to training.")
            else:
              info(f"[RIVAL] Rival race found! Proceeding with race.")
              update_operator_snapshot(state_obj, action, phase="executing_action", message="Rival race confirmed. Proceeding.")
          pre_run_hook = _rival_scout_hook

        action_result = run_action_with_review(
          state_obj,
          action,
          "Review proposed action before execution.",
          pre_run_hook=pre_run_hook,
          sub_phase="preview_race_selection" if action.func == "do_race" else "preview_action_clicks",
        )
        executed_action = action_result == "executed"
        if action_result == "previewed":
          continue
        elif action_result == "reassess":
          continue
        elif action_result == "blocked":
          continue
        elif action_result != "executed":
          _push_turn_retry_debug(
            state_obj,
            reason="Initial action execution failed; trying fallback actions.",
            reasons=[action.func or "unknown_action"],
            before_phase="evaluating_strategy",
            context="action_selection",
            event="turn_retry",
            result="fallback_retry",
            sub_phase="evaluate_training_action",
            phase="evaluating_strategy",
          )
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
            if function_name == "do_rest":
              action["disable_skip_turn_fallback"] = True
            else:
              action.options.pop("disable_skip_turn_fallback", None)
            if constants.SCENARIO_NAME in ("mant", "trackblazer"):
              action = _attach_trackblazer_pre_action_item_plan(state_obj, action)
            skill_result = maybe_review_skill_purchase(
              state_obj,
              action_count,
              race_check=bool(action.func == "do_race"),
              action=action,
            )
            if skill_result == "failed":
              executed_action = False
              break
            update_pre_action_phase(
              state_obj,
              action,
              message=f"Retry candidate ready: {function_name}.",
            )
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
            if retry_result == "reassess":
              executed_action = False
              break
            if retry_result == "blocked":
              executed_action = False
              break
            if retry_result == "previewed":
              executed_action = False
              break
            info(f"Action {function_name} failed, trying other actions.")

        if not executed_action:
          continue
        record_and_finalize_turn(state_obj, action)
        continue

  except BotStopException as exc:
    message = str(exc) or "Bot stopped."
    info(message)
    update_operator_snapshot(phase="idle", message=message)
    return

def record_and_finalize_turn(state_obj, action):
  global last_state, action_count
  bot.push_debug_history({
    "event": "action_executed",
    "asset": action.func or "unknown",
    "result": "completed",
    "context": f"turn_{action_count + 1}",
  })
  if args.debug is not None:
    record_turn(state_obj, last_state, action)
    last_state = state_obj

  # Races award coins (e.g. 100 for a win), so recheck the shop next turn
  # in case there were items we couldn't afford before.
  if (
    action.func == "do_race"
    and constants.SCENARIO_NAME in ("mant", "trackblazer")
  ):
    bot.request_trackblazer_shop_check("post_race_coins")

  action_count += 1
  if LIMIT_TURNS > 0:
    if action_count >= LIMIT_TURNS:
      info(f"Completed {action_count} actions, stopping bot as requested.")
      quit()
  update_operator_snapshot(state_obj, action, phase="scanning_lobby", message="Turn complete. Returning to lobby scan.")
