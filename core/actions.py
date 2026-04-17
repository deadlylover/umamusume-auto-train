# core/actions.py
# Atomic game actions — lowest-level clicks.
# These don’t decide *when*, only *how*.

import utils.constants as constants
import core.config as config
import re
from time import time
from utils.tools import sleep, get_secs
import utils.device_action_wrapper as device_action
from utils.log import error, info, warning, debug
from utils.screenshot import are_screenshots_same
import pyautogui
import core.bot as bot
from utils.shared import CleanDefaultDict, get_race_type

class Action:
  def __init__(self, **options):
    self.func = None
    self.available_actions = []
    self.options = options

  def run(self):
    if config.VERBOSE_ACTIONS:
      info(f"[ACTION] Running {self.func} with options: {self.options}")
    return globals()[self.func](self.options)

  def get(self, key, default=None):
    """Get an option safely with a default if missing."""
    return self.options.get(key, default)

  # Optional: allow dict-like access
  def __getitem__(self, key):
    return self.options[key]

  def __setitem__(self, key, value):
    self.options[key] = value

  def _format_dict_floats(self, d):
    """Format floats in dictionary string to 2 decimal places using pure regex."""
    s = str(d)
    # Match: digits, dot, 1-2 digits, then any additional digits, comma
    # Replace with: first group (digits.dot.1-2digits) + comma
    return re.sub(r'(\d+\.\d{1,2})\d*,', r'\1,', s)

  def __repr__(self):
    string = f"<Action func={self.func}, available_actions={self.available_actions}, options={self.options!r}>"
    return self._format_dict_floats(string)

  def __str__(self):
    string = f"Action<{self.func}, available_actions={self.available_actions}, options={self.options}>"
    return self._format_dict_floats(string)

def do_training(options):
  training_name = options["training_name"]
  if config.VERBOSE_ACTIONS:
    info(f"[TRAIN] Opening training menu to select '{training_name}'.")
  if training_name not in constants.TRAINING_BUTTON_POSITIONS:
    error(f"Training name \"{training_name}\" not found in training images.")
    return False
  mouse_pos = constants.TRAINING_BUTTON_POSITIONS[training_name]
  bot.push_debug_history({"event": "click", "asset": "training_btn", "result": "opening", "context": "do_training"})
  if not device_action.locate_and_click("assets/buttons/training_btn.png", region_ltrb=constants.SCREEN_BOTTOM_BBOX, min_search_time=get_secs(2)):
    error(f"Couldn't find training button.")
    bot.push_debug_history({"event": "click", "asset": "training_btn", "result": "not_found", "context": "do_training"})
    return False
  bot.push_debug_history({"event": "click", "asset": "training_btn", "result": "opened", "context": "do_training"})
  if config.VERBOSE_ACTIONS:
    info(f"[TRAIN] Training menu opened; clicking '{training_name}'.")
  sleep(0.75)
  bot.push_debug_history({"event": "click", "asset": training_name, "result": "double_clicked", "context": "do_training"})
  device_action.click(target=mouse_pos, clicks=2, interval=0.15)
  return True

def do_infirmary(options=None):
  infirmary_btn = device_action.locate("assets/buttons/infirmary_btn.png", min_search_time=get_secs(2), region_ltrb=constants.SCREEN_BOTTOM_BBOX)
  if not infirmary_btn:
    error(f"Infirmary button not found.")
    return False
  else:
    device_action.click(target=infirmary_btn, duration=0.1)
  return True

event_templates = {
  "aoi_event": "assets/ui/aoi_event.png",
  "tazuna_event": "assets/ui/tazuna_event.png",
  "riko_event": "assets/ui/riko_event.png",
  "trainee_uma": "assets/ui/trainee_uma.png"
}

event_progress_templates = [
  "assets/ui/pal_progress_1.png",
  "assets/ui/pal_progress_2.png",
  "assets/ui/pal_progress_3.png",
  "assets/ui/pal_progress_4.png",
  "assets/ui/pal_progress_5.png"
]




def do_recreation(options=None):
  if options is None:
    options = {}
  if constants.SCENARIO_NAME in ("mant", "trackblazer"):
    date_event_available = options.get("date_event_available")
    if date_event_available is None:
      date_event_available = bool(device_action.locate("assets/ui/recreation_with.png"))
    if not date_event_available:
      info("[RECREATION] Trackblazer recreation blocked because no friend date event is available.")
      return False
  recreation_btn = device_action.locate("assets/buttons/recreation_btn.png", min_search_time=get_secs(2), region_ltrb=constants.SCREEN_BOTTOM_BBOX)

  if recreation_btn:
    device_action.click(target=recreation_btn, duration=0.15)
    sleep(1)
    screenshot = device_action.screenshot()
    matches = CleanDefaultDict()
    for name, path in event_templates.items():
      match = device_action.match_template(path, screenshot)
      if len(match) > 0:
        matches[name] = match[0]
        debug(f"{name} found: {match[0]}")
      else:
        debug(f"{name} not found")

    available_recreation = None
    window_left, window_top = constants.GAME_WINDOW_BBOX[0], constants.GAME_WINDOW_BBOX[1]
    for name, box in matches.items():
      debug(f"{name}, {box}")
      x, y, w, h = box
      abs_x = x + window_left
      abs_y = y + window_top
      region_xywh = (abs_x, abs_y, 550, 85)
      # for later, use event_progress_templates to loop through and find our progress
      pal_screenshot = device_action.screenshot(region_xywh=region_xywh)
      match = device_action.match_template(event_progress_templates[4], pal_screenshot)
      if len(match) > 0:
        debug(f"{name} is NOT available for recreation.")
      else:
        available_recreation = (abs_x + w // 2, abs_y + h // 2)
        debug(f"{name} is available for recreation.")
        break
      
    debug(f"Available recreation: {available_recreation}")  
    if not available_recreation:
      warning("[RECREATION] Recreation menu opened but no valid friend event target was identified.")
      return False
    if not device_action.click(target=available_recreation, duration=0.15):
      warning("[RECREATION] Failed to click the selected recreation target.")
      return False
  else:
    debug(f"No recreation button found, clicking rest summer button")
    recreation_summer_btn = device_action.locate("assets/buttons/rest_summer_btn.png", min_search_time=get_secs(2), region_ltrb=constants.SCREEN_BOTTOM_BBOX)
    if recreation_summer_btn:
      if not device_action.click(target=recreation_summer_btn, duration=0.15):
        warning("[RECREATION] Failed to click the summer recreation fallback button.")
        return False
    else:
      return False
  
  # quit to wait for input
  return True

def do_race(options=None):
  if options is None:
    options = {}
  debug(f"do_race options before enter race: {options}")
  race_name = options.get("race_name")
  custom_race_image_path = options.get("race_image_path") or ""
  if "is_race_day" in options and options["is_race_day"]:
    race_day(options)
  elif ("race_mission_available" in options and options["race_mission_available"]):
    if not enter_race(options=options):
      return False
  elif race_name not in (None, "", "any"):
    race_image_path = custom_race_image_path or f"assets/races/{race_name}.png"
    if not enter_race(race_name, race_image_path, options=options):
      return False
  elif custom_race_image_path:
    if not enter_race(race_name or "any", custom_race_image_path, options=options):
      return False
  else:
    if not enter_race(options=options):
      return False

  debug(f"do_race options after enter race: {options}")
  sleep(2)

  return bool(start_race())


def skip_turn(options=None):
  options["training_name"] = "wit"
  return do_training(options)

def do_rest(options=None):
  if (
    config.NEVER_REST_ENERGY > 0
    and options["energy_level"] > config.NEVER_REST_ENERGY
    and not options.get("disable_skip_turn_fallback")
  ):
    info(f"Wanted to rest when energy was above {config.NEVER_REST_ENERGY}, training wit instead.")
    return skip_turn(options)
  rest_btn = device_action.locate("assets/buttons/rest_btn.png", min_search_time=get_secs(2), region_ltrb=constants.SCREEN_BOTTOM_BBOX)

  if rest_btn:
    device_action.click(target=rest_btn, duration=0.15)
  else:
    rest_summber_btn = device_action.locate("assets/buttons/rest_summer_btn.png", min_search_time=get_secs(2), region_ltrb=constants.SCREEN_BOTTOM_BBOX)
    if rest_summber_btn:
      device_action.click(target=rest_summber_btn, duration=0.15)
    else:
      return False
  return True

def race_day(options=None):
  year = options.get("year") if isinstance(options, dict) else None
  if options.get("trackblazer_climax_race_day"):
    from scenarios.trackblazer import climax_race_button_region

    region_ltrb = climax_race_button_region()
    if not region_ltrb:
      warning("[TB_RACE] Climax race-day button region is invalid; cannot click forced race button.")
      return False
    device_action.locate_and_click(
      constants.TRACKBLAZER_RACE_TEMPLATES["climax_race_button"],
      min_search_time=get_secs(10),
      region_ltrb=region_ltrb,
      template_scaling=1.0 / device_action.GLOBAL_TEMPLATE_SCALING,
    )
  elif year == "Finale Underway":
    device_action.locate_and_click("assets/ura/ura_race_btn.png", min_search_time=get_secs(10), region_ltrb=constants.SCREEN_BOTTOM_BBOX)
  else:
    device_action.locate_and_click("assets/buttons/race_day_btn.png", min_search_time=get_secs(10), region_ltrb=constants.SCREEN_BOTTOM_BBOX)
  sleep(0.5)
  device_action.locate_and_click("assets/buttons/ok_btn.png")
  sleep(0.5)
  for i in range(2):
    if not device_action.locate_and_click("assets/buttons/race_btn.png", min_search_time=get_secs(2)):
      device_action.locate_and_click("assets/buttons/bluestacks/race_btn.png", min_search_time=get_secs(2))
    sleep(0.5)

def go_to_racebox_top():
  for i in range(10):
    screenshot1 = device_action.screenshot(region_ltrb=constants.RACE_LIST_BOX_BBOX)
    device_action.swipe(constants.RACE_SCROLL_TOP_MOUSE_POS, constants.RACE_SCROLL_BOTTOM_MOUSE_POS)
    device_action.click(constants.RACE_SCROLL_BOTTOM_MOUSE_POS)
    sleep(0.25)
    screenshot2 = device_action.screenshot(region_ltrb=constants.RACE_LIST_BOX_BBOX)
    if are_screenshots_same(screenshot1, screenshot2, diff_threshold=15):
      return True
  return False


def _planner_fallback_action(options=None):
  options = options if isinstance(options, dict) else {}
  planner_payload = options.get("trackblazer_planner_race") or {}
  if not isinstance(planner_payload, dict):
    return {}
  fallback_action = planner_payload.get("fallback_action") or {}
  return fallback_action if isinstance(fallback_action, dict) else {}


def _fallback_func(options=None):
  options = options if isinstance(options, dict) else {}
  planner_payload = options.get("trackblazer_planner_race") or {}
  planner_payload = planner_payload if isinstance(planner_payload, dict) else {}
  planner_fallback_func = (_planner_fallback_action(options) or {}).get("func")
  if planner_payload:
    return planner_fallback_func
  if planner_fallback_func:
    return planner_fallback_func
  return options.get("_rival_fallback_func")


def _should_accept_consecutive_race_warning(options=None):
  options = options or {}
  planner_policy = options.get("planner_race_warning_policy") or {}
  if isinstance(planner_policy, dict) and planner_policy:
    if planner_policy.get("accept_warning") is not None:
      return bool(planner_policy.get("accept_warning"))
  # Optional rival races promoted from an original rest decision should back
  # out on consecutive-race warning and let rest proceed.
  if (
    options.get("prefer_rival_race")
    and _fallback_func(options) == "do_rest"
    and not options.get("scheduled_race")
    and not options.get("trackblazer_lobby_scheduled_race")
    and not options.get("is_race_day")
  ):
    return False
  if options.get("fallback_non_rival_race"):
    return False
  return bool(
    options.get("scheduled_race")
    or options.get("trackblazer_lobby_scheduled_race")
  )


def _mark_consecutive_warning_outcome(options=None, *, force_rest=False, reason=None):
  if not isinstance(options, dict):
    return
  planner_policy = options.get("planner_race_warning_policy") or {}
  planner_race_payload = options.get("trackblazer_planner_race") or {}
  planner_race_payload = planner_race_payload if isinstance(planner_race_payload, dict) else {}
  planner_owned = bool(
    (isinstance(planner_policy, dict) and planner_policy.get("planner_owned"))
    or planner_race_payload.get("branch_kind")
  )
  options["planner_warning_outcome"] = {
    "cancelled": True,
    "force_rest": bool(force_rest),
    "reason": str(reason or ""),
  }
  if planner_owned:
    options.pop("_consecutive_warning_cancelled", None)
    options.pop("_consecutive_warning_force_rest", None)
    options.pop("_consecutive_warning_cancel_reason", None)
  else:
    options["_consecutive_warning_cancelled"] = True
    if force_rest:
      options["_consecutive_warning_force_rest"] = True
    if reason:
      options["_consecutive_warning_cancel_reason"] = str(reason)


def _should_force_rest_after_optional_warning(options=None):
  if not isinstance(options, dict):
    return False
  planner_policy = options.get("planner_race_warning_policy") or {}
  if isinstance(planner_policy, dict) and planner_policy:
    if planner_policy.get("force_rest_on_cancel") is not None:
      return bool(planner_policy.get("force_rest_on_cancel"))
  if options.get("scheduled_race") or options.get("trackblazer_lobby_scheduled_race") or options.get("is_race_day"):
    return False
  race_decision = options.get("trackblazer_race_decision") or {}
  if not isinstance(race_decision, dict):
    return False
  if race_decision.get("g1_forced"):
    return False
  if race_decision.get("fallback_non_rival_race"):
    return True
  # Weak-training optional rival path should rest when 3rd-race warning blocks.
  return bool(
    race_decision.get("should_race")
    and race_decision.get("prefer_rival_race")
    and _fallback_func(options) == "do_training"
  )


def _is_trackblazer_aptitude_race_template(race_image_path=""):
  return str(race_image_path or "") == str(
    constants.TRACKBLAZER_RACE_TEMPLATES.get("race_recommend_2_aptitudes") or ""
  )


def _is_post_debut_junior_year(options=None):
  if not isinstance(options, dict):
    return False
  year = str(options.get("year") or "").strip()
  return year.startswith("Junior Year") and year != "Junior Year Pre-Debut"


def _should_allow_trackblazer_maiden_recovery(race_name="any", race_image_path="", options=None):
  if not isinstance(options, dict):
    return False
  if constants.SCENARIO_NAME not in ("mant", "trackblazer"):
    return False
  if not _is_post_debut_junior_year(options):
    return False
  if options.get("trackblazer_maiden_race"):
    return True

  race_decision = options.get("trackblazer_race_decision") or {}
  if isinstance(race_decision, dict) and race_decision.get("mandatory_maiden_race"):
    return True

  planner_payload = options.get("trackblazer_planner_race") or {}
  branch_kind = str(planner_payload.get("branch_kind") or "")
  if branch_kind == "maiden_race":
    return True

  if _is_trackblazer_aptitude_race_template(race_image_path):
    return True

  return bool(
    race_name not in (None, "", "any")
    and (
      options.get("scheduled_race")
      or options.get("trackblazer_lobby_scheduled_race")
      or branch_kind in {"scheduled_race", "lobby_scheduled_race"}
    )
  )


def _click_visible_trackblazer_aptitude_race():
  from scenarios.trackblazer import find_race_aptitude_matches

  matches = find_race_aptitude_matches()
  if not matches:
    return False

  click_target = (matches[0] or {}).get("click_target")
  if not click_target:
    return False

  abs_x = int(click_target[0]) + constants.RACE_LIST_BOX_BBOX[0]
  abs_y = int(click_target[1]) + constants.RACE_LIST_BOX_BBOX[1]
  info(f"[TB_RACE] Clicking aptitude-backed race at ({abs_x}, {abs_y})")
  device_action.click(target=(abs_x, abs_y), duration=0.15)
  return True


def _click_visible_race_entry_on_current_page(race_image_path="", options=None):
  prefer_rival = bool(isinstance(options, dict) and options.get("prefer_rival_race", False))

  if prefer_rival:
    from scenarios.trackblazer import find_rival_races_with_aptitude

    paired = find_rival_races_with_aptitude()
    if not paired:
      return False

    click_target = paired[0]["click_target"]
    abs_x = click_target[0] + constants.RACE_LIST_BOX_BBOX[0]
    abs_y = click_target[1] + constants.RACE_LIST_BOX_BBOX[1]
    info(f"[RIVAL] Clicking rival race aptitude stars at ({abs_x}, {abs_y})")
    device_action.click(target=(abs_x, abs_y), duration=0.15)
    return True

  if _is_trackblazer_aptitude_race_template(race_image_path):
    if _click_visible_trackblazer_aptitude_race():
      if isinstance(options, dict):
        options["trackblazer_maiden_recovery_fallback"] = True
      return True

  if isinstance(options, dict) and options.get("race_mission_available"):
    mission_icon = device_action.locate(
      "assets/icons/race_mission_icon.png",
      min_search_time=get_secs(1),
      region_ltrb=constants.RACE_LIST_BOX_BBOX,
      template_scaling=0.72,
    )
    if mission_icon:
      debug("Found mission icon, looking for aptitude match.")
      screenshot_region = (mission_icon[0], mission_icon[1], mission_icon[0] + 400, mission_icon[1] + 110)
      if device_action.locate_and_click(race_image_path, min_search_time=get_secs(1), region_ltrb=screenshot_region):
        return True
  elif device_action.locate_and_click(
    race_image_path,
    min_search_time=get_secs(1),
    region_ltrb=constants.RACE_LIST_BOX_BBOX,
  ):
    return True

  return False


def _select_and_confirm_race_from_open_list(race_name="any", race_image_path="", options=None):
  if race_image_path == "":
    race_image_path = "assets/ui/match_track.png"
  sleep(1)

  preserve_current_page = bool(isinstance(options, dict) and options.get("planner_race_list_ready"))
  allow_maiden_recovery = _should_allow_trackblazer_maiden_recovery(
    race_name,
    race_image_path,
    options=options,
  )
  maiden_recovery_attempted = _is_trackblazer_aptitude_race_template(race_image_path)
  performed_full_top_rescan = False

  if not _click_visible_race_entry_on_current_page(race_image_path, options=options):
    if preserve_current_page:
      debug("[RACE] Planner left the race list open; scouting commit from the current page before any reset.")
      check_before_scroll = False
    else:
      go_to_racebox_top()
      check_before_scroll = True

    while True:
      screenshot1 = device_action.screenshot(region_ltrb=constants.RACE_LIST_BOX_BBOX)

      if check_before_scroll and _click_visible_race_entry_on_current_page(race_image_path, options=options):
        break

      prefer_rival = bool(isinstance(options, dict) and options.get("prefer_rival_race", False))
      if prefer_rival:
        debug("[RIVAL] No rival on current page, scrolling to find it...")

      sleep(0.5)
      debug("Scrolling races...")
      device_action.swipe(constants.RACE_SCROLL_BOTTOM_MOUSE_POS, constants.RACE_SCROLL_TOP_MOUSE_POS)
      device_action.click(constants.RACE_SCROLL_TOP_MOUSE_POS, duration=0)
      sleep(0.25)
      screenshot2 = device_action.screenshot(region_ltrb=constants.RACE_LIST_BOX_BBOX)
      if are_screenshots_same(screenshot1, screenshot2, diff_threshold=15):
        if prefer_rival:
          info("[RIVAL] No rival race found in list, falling back to generic match.")
          if isinstance(options, dict):
            options["prefer_rival_race"] = False
          go_to_racebox_top()
          preserve_current_page = False
          performed_full_top_rescan = True
          check_before_scroll = True
          continue
        if allow_maiden_recovery and not maiden_recovery_attempted:
          info(
            f"[TB_RACE] {race_name} was not present in the open race list. "
            "Falling back to the visible aptitude-backed maiden entry."
          )
          race_name = "any"
          race_image_path = constants.TRACKBLAZER_RACE_TEMPLATES.get("race_recommend_2_aptitudes") or "assets/ui/match_track.png"
          maiden_recovery_attempted = True
          if isinstance(options, dict):
            options["race_name"] = "any"
            options["race_image_path"] = race_image_path
            options["trackblazer_maiden_recovery_fallback"] = True
          go_to_racebox_top()
          preserve_current_page = False
          performed_full_top_rescan = True
          check_before_scroll = True
          continue
        if preserve_current_page and not performed_full_top_rescan:
          info("[RACE] Current preserved race-list page had no clickable match; resetting to the top once before giving up.")
          go_to_racebox_top()
          preserve_current_page = False
          performed_full_top_rescan = True
          check_before_scroll = True
          continue
        info("Couldn't find race image")
        device_action.locate_and_click("assets/buttons/back_btn.png", min_search_time=get_secs(2), region_ltrb=constants.SCREEN_BOTTOM_BBOX)
        return False

      preserve_current_page = False
      check_before_scroll = True

  for i in range(2):
    if not device_action.locate_and_click("assets/buttons/race_btn.png", min_search_time=get_secs(2)):
      device_action.locate_and_click("assets/buttons/bluestacks/race_btn.png", min_search_time=get_secs(2))
    sleep(0.5)
  return True


def enter_race(race_name="any", race_image_path="", options=None):
  planner_race_list_ready = bool(options and options.get("planner_race_list_ready"))
  if not planner_race_list_ready:
    device_action.locate_and_click("assets/buttons/races_btn.png", min_search_time=get_secs(10), region_ltrb=constants.SCREEN_BOTTOM_BBOX)
    debug(f"race_name: {race_name}, race_image_path: {race_image_path}")
    sleep(1)
    consecutive_cancel_btn = device_action.locate("assets/buttons/cancel_btn.png", min_search_time=get_secs(1))
    accept_warning = _should_accept_consecutive_race_warning(options)
    planner_warning_policy = (options or {}).get("planner_race_warning_policy") or {}
    is_fallback_race = bool(options and options.get("fallback_non_rival_race"))
    is_rest_promoted_optional_race = bool(
      options
      and options.get("prefer_rival_race")
      and _fallback_func(options) == "do_rest"
      and not options.get("scheduled_race")
      and not options.get("trackblazer_lobby_scheduled_race")
      and not options.get("is_race_day")
    )
    if consecutive_cancel_btn and is_fallback_race:
      cancel_reason = ((options or {}).get("planner_race_warning_policy") or {}).get("cancel_reason_key") or "optional_fallback_non_rival_race"
      _mark_consecutive_warning_outcome(
        options,
        force_rest=True,
        reason=cancel_reason,
      )
      device_action.locate_and_click("assets/buttons/cancel_btn.png", min_search_time=get_secs(1), text="[INFO] Consecutive-race warning on fallback non-rival race. Cancelling — not worth a 3rd consecutive race for a weak-training fallback.")
      return False
    if consecutive_cancel_btn and is_rest_promoted_optional_race:
      cancel_reason = ((options or {}).get("planner_race_warning_policy") or {}).get("cancel_reason_key") or "optional_rival_promoted_from_rest"
      _mark_consecutive_warning_outcome(
        options,
        force_rest=True,
        reason=cancel_reason,
      )
      device_action.locate_and_click(
        "assets/buttons/cancel_btn.png",
        min_search_time=get_secs(1),
        text="[INFO] Consecutive-race warning on optional rival race promoted from rest. Cancelling and preserving rest fallback.",
      )
      return False
    cancel_warning = bool(consecutive_cancel_btn and not accept_warning and (config.CANCEL_CONSECUTIVE_RACE or planner_warning_policy))
    if cancel_warning:
      force_rest = _should_force_rest_after_optional_warning(options)
      cancel_reason = ((options or {}).get("planner_race_warning_policy") or {}).get("cancel_reason_key") or "cancel_consecutive_race_setting"
      _mark_consecutive_warning_outcome(
        options,
        force_rest=force_rest,
        reason=cancel_reason,
      )
      device_action.locate_and_click(
        "assets/buttons/cancel_btn.png",
        min_search_time=get_secs(1),
        text="[INFO] Already raced 3+ times consecutively. Cancelling race and using fallback action.",
      )
      return False
    elif consecutive_cancel_btn:
      warning_reason = "scheduled race override" if accept_warning else "config allows consecutive race"
      warning_ok_template = constants.TRACKBLAZER_RACE_TEMPLATES.get("race_warning_consecutive_ok")
      clicked_warning_ok = False
      if warning_ok_template:
        clicked_warning_ok = device_action.locate_and_click(
          warning_ok_template,
          min_search_time=get_secs(1),
          region_ltrb=constants.GAME_WINDOW_BBOX,
          text=f"[INFO] Consecutive-race warning detected. Continuing via warning-specific OK ({warning_reason}).",
        )
      if not clicked_warning_ok:
        device_action.locate_and_click(
          "assets/buttons/ok_btn.png",
          min_search_time=get_secs(1),
          region_ltrb=constants.GAME_WINDOW_BBOX,
          text=f"[INFO] Consecutive-race warning detected. Continuing via fallback OK ({warning_reason}).",
        )

  result = _select_and_confirm_race_from_open_list(race_name, race_image_path, options=options)
  if options is not None and planner_race_list_ready:
    options["planner_race_list_ready"] = False
  return result

# support functions for actions
def start_race():
  if config.POSITION_SELECTION_ENABLED:
    select_position()
    sleep(0.5)
  device_action.locate_and_click("assets/buttons/view_results.png", min_search_time=get_secs(10), region_ltrb=constants.SCREEN_BOTTOM_BBOX)
  sleep(0.5)

  close_btn = device_action.locate("assets/buttons/close_btn.png", min_search_time=get_secs(1))
  if not close_btn:
    device_action.click(target=constants.RACE_SCROLL_BOTTOM_MOUSE_POS, clicks=2, interval=0.1)
    sleep(0.2)
    device_action.click(target=constants.RACE_SCROLL_BOTTOM_MOUSE_POS, clicks=2, interval=0.2)
    info("Race should be over.")
  else:
    info(f"Close button for view results found. Trying to go into the race.")
    device_action.click(target=close_btn)

  for i in range(5):
    device_action.locate_and_click("assets/buttons/next_btn.png", region_ltrb=constants.SCREEN_BOTTOM_BBOX)
    device_action.click(target=constants.SAFE_SPACE_MOUSE_POS)
    if device_action.locate_and_click("assets/buttons/next2_btn.png", region_ltrb=constants.SCREEN_BOTTOM_BBOX):
      break
    sleep(0.25)

  if device_action.locate_and_click("assets/buttons/race_btn.png", min_search_time=get_secs(10), region_ltrb=constants.SCREEN_BOTTOM_BBOX):
    info(f"Went into the race, sleep for {get_secs(10)} seconds to allow loading.")
    sleep(10)
    info("Looking for \"Race!\" button...")
    for i in range(5):
      if device_action.locate_and_click("assets/buttons/race_exclamation_btn.png", min_search_time=get_secs(2), region_ltrb=constants.FULL_SCREEN_LANDSCAPE):
        info("Found \"Race!\" button landscape. After searching for 2 seconds.")
        break
      elif device_action.locate_and_click("assets/buttons/race_exclamation_btn_portrait.png", min_search_time=get_secs(2)):
        info("Found \"Race!\" button portrait. After searching for 2 seconds.")
        break
      elif device_action.locate_and_click("assets/buttons/race_exclamation_btn.png", min_search_time=get_secs(2), template_scaling=0.56):
        info("Found \"Race!\" button landscape. After searching for 2 seconds.")
        break
      elif i == 4:
        warning(f"Could not find \"Race!\" button after {i+1} attempts. Probably can't move onto the race. Please report this.")
    sleep(0.5)

    skip_btn, skip_btn_big = find_skip_buttons(get_secs(2))
    if not skip_btn and not skip_btn_big:
      warning("Couldn't find skip buttons at first search.")
      skip_btn, skip_btn_big = find_skip_buttons(get_secs(10))

    click_any_button(skip_btn, skip_btn_big)
    sleep(0.5)
    click_any_button(skip_btn, skip_btn_big)
    sleep(2)
    click_any_button(skip_btn, skip_btn_big)
    sleep(0.5)
    click_any_button(skip_btn, skip_btn_big)
    skip_btn, _ = find_skip_buttons(get_secs(2))
    device_action.click(target=skip_btn)
    sleep(2)

    while True:
      sleep(1)
      device_action.flush_screenshot_cache()
      screenshot_size = device_action.screenshot().shape # (height 1080, width 800, channels 3)
      if screenshot_size[0] == 800 and screenshot_size[1] == 1080:
        info("Landscape mode detected after race, probably concert. Looking for close button.")
        if device_action.locate_and_click("assets/buttons/close_btn.png", min_search_time=get_secs(5)):
          info("Close button found.")
          break
      else:
        info("Portrait mode detected.")
        break

    device_action.locate_and_click("assets/buttons/close_btn.png", min_search_time=get_secs(5))

  # Race mechanics complete.  Post-race follow-up screens (events, result
  # taps, Trackblazer popups) are handled by the unified post-action
  # resolver in skeleton._resolve_post_action_resolution().
  return True

def find_skip_buttons(min_search_time):
  skip_btn = device_action.locate("assets/buttons/skip_btn.png", min_search_time=min_search_time, region_ltrb=constants.SCREEN_BOTTOM_BBOX)
  if not skip_btn and not bot.is_adb_input_active():
    skip_btn_big = device_action.locate("assets/buttons/skip_btn_big.png", min_search_time=min_search_time, region_ltrb=constants.SKIP_BTN_BIG_BBOX_LANDSCAPE)
  else:
    skip_btn_big = None
  return skip_btn, skip_btn_big

def click_any_button(*buttons):
  for btn in buttons:
    if btn:
      device_action.click(target=btn, clicks=3, interval=0.2)
      return True
  return False

race_types = ["sprint", "mile", "medium", "long"]
def select_position():
  sleep(0.5)
  debug("Selecting position")
  # these two are mutually exclusive, so we only use preferred position if positions by race is not enabled.
  if config.ENABLE_POSITIONS_BY_RACE:
    debug(f"Selecting position based on race type: {config.ENABLE_POSITIONS_BY_RACE}")
    device_action.locate_and_click("assets/buttons/info_btn.png", min_search_time=get_secs(5), region_ltrb=constants.SCREEN_TOP_BBOX)
    sleep(0.5)
    #find race text, get part inside parentheses using regex, strip whitespaces and make it lowercase for our usage
    race_info_text = get_race_type().lower()
    race_type = None
    for distance in race_types:
      if distance in race_info_text:
        race_type = distance
        debug(f"Race type: {race_type}")
        break

    device_action.locate_and_click("assets/buttons/close_btn.png", min_search_time=get_secs(2), region_ltrb=constants.SCREEN_BOTTOM_BBOX)
    if race_type:
      position_for_race = config.POSITIONS_BY_RACE[race_type]
      info(f"Selecting position {position_for_race} based on race type {race_type}")
      device_action.locate_and_click("assets/buttons/change_btn.png", min_search_time=get_secs(4), region_ltrb=constants.SCREEN_MIDDLE_BBOX)
      device_action.locate_and_click(f"assets/buttons/positions/{position_for_race}_position_btn.png", min_search_time=get_secs(2), region_ltrb=constants.SCREEN_MIDDLE_BBOX)
      device_action.locate_and_click("assets/buttons/confirm_btn.png", min_search_time=get_secs(2), region_ltrb=constants.SCREEN_MIDDLE_BBOX)
  elif not bot.PREFERRED_POSITION_SET:
    debug(f"Setting preferred position: {config.PREFERRED_POSITION}")
    device_action.locate_and_click("assets/buttons/change_btn.png", min_search_time=get_secs(6), region_ltrb=constants.SCREEN_MIDDLE_BBOX)
    device_action.locate_and_click(f"assets/buttons/positions/{config.PREFERRED_POSITION}_position_btn.png", min_search_time=get_secs(2), region_ltrb=constants.SCREEN_MIDDLE_BBOX)
    device_action.locate_and_click("assets/buttons/confirm_btn.png", min_search_time=get_secs(2), region_ltrb=constants.SCREEN_MIDDLE_BBOX)
    bot.PREFERRED_POSITION_SET = True
