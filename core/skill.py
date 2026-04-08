from utils.tools import sleep, get_secs
import pyautogui
import Levenshtein

import utils.constants as constants
from core.race_selector import get_race_gate_for_turn_label

from utils.log import info, warning, error, debug
from utils.screenshot import enhanced_screenshot, are_screenshots_same
from core.ocr import extract_text
from core.recognizer import is_btn_active, compare_brightness
import utils.device_action_wrapper as device_action

import core.config as config
import core.bot as bot

_TRACKBLAZER_SKILLS_LEARNED_THRESHOLD = 0.8
_INVERSE_GLOBAL_SCALE = 1.0 / device_action.GLOBAL_TEMPLATE_SCALING

previous_action_count = -1
previous_skill_check_action_count = -1
previous_selected_race_skill_check_action_count = -1
_all_skills_obtained = False
SKILL_RECHECK_TURNS = 6
SKILL_SELECTED_RACE_RECHECK_TURNS = 3

def init_skill_py():
  global previous_action_count, previous_skill_check_action_count, previous_selected_race_skill_check_action_count, _all_skills_obtained
  previous_action_count = -1
  previous_skill_check_action_count = -1
  previous_selected_race_skill_check_action_count = -1
  _all_skills_obtained = False


def update_skill_action_count(action_count):
  global previous_action_count
  previous_action_count = action_count


def mark_skill_purchase_checked(action_count, selected_race=False):
  global previous_skill_check_action_count, previous_selected_race_skill_check_action_count
  previous_skill_check_action_count = action_count
  if selected_race:
    previous_selected_race_skill_check_action_count = action_count


def mark_all_skills_obtained():
  global _all_skills_obtained
  _all_skills_obtained = True
  info("[SKILL] All configured skills are already learned. Skill checks disabled for the rest of this run.")


def get_all_skills_obtained():
  return _all_skills_obtained


def get_skill_purchase_check_state():
  return {
    "last_skill_purchase_action_count": previous_action_count,
    "last_skill_purchase_check_action_count": previous_skill_check_action_count,
    "last_selected_race_skill_purchase_check_action_count": previous_selected_race_skill_check_action_count,
    "skill_recheck_turns": SKILL_RECHECK_TURNS,
    "skill_selected_race_recheck_turns": SKILL_SELECTED_RACE_RECHECK_TURNS,
    "all_skills_obtained": _all_skills_obtained,
  }


def _trackblazer_has_scheduled_g1_race(state, action=None):
  if not isinstance(state, dict):
    return False
  if constants.SCENARIO_NAME not in ("mant", "trackblazer"):
    return False
  gate = get_race_gate_for_turn_label(state.get("year"), getattr(config, "OPERATOR_RACE_SELECTOR", None))
  return bool(gate.get("enabled") and gate.get("race_allowed") and gate.get("selected_race"))


def get_skill_purchase_context(state, action_count, race_check=False, action=None):
  global previous_action_count, previous_skill_check_action_count, previous_selected_race_skill_check_action_count
  current_sp = state.get("current_stats", {}).get("sp", 0)
  scheduled_g1_race = _trackblazer_has_scheduled_g1_race(state, action=action)
  normal_review_due = (
    previous_skill_check_action_count < 0
    or (action_count - previous_skill_check_action_count) >= SKILL_RECHECK_TURNS
  )
  result = {
    "should_check": False,
    "reason": "",
    "auto_buy_skill_enabled": bot.get_skill_auto_buy_enabled(),
    "shopping_list": list(config.SKILL_LIST),
    "current_sp": current_sp,
    "threshold_sp": config.SKILL_PTS_CHECK,
    "race_check": race_check,
    "last_skill_purchase_action_count": previous_action_count,
    "last_skill_purchase_check_action_count": previous_skill_check_action_count,
    "last_selected_race_skill_purchase_check_action_count": previous_selected_race_skill_check_action_count,
    "skill_recheck_turns": SKILL_RECHECK_TURNS,
    "skill_selected_race_recheck_turns": SKILL_SELECTED_RACE_RECHECK_TURNS,
    "scheduled_g1_race": scheduled_g1_race,
    "planned_clicks": [
      {"label": "Open skills menu", "template": "assets/buttons/skills_btn.png", "region_key": "SCREEN_BOTTOM_BBOX"},
      {"label": "Scan skill rows", "region_key": "SCROLLING_SKILL_SCREEN_BBOX", "source_type": "ocr_region"},
      {"label": "Select matching skill rows", "template": "assets/icons/buy_skill.png", "region_key": "SCROLLING_SKILL_SCREEN_BBOX"},
      {"label": "Confirm selected skills", "template": "assets/buttons/confirm_btn.png"},
      {"label": "Learn selected skills", "template": "assets/buttons/learn_btn.png"},
      {"label": "Exit skills screen", "template": "assets/buttons/back_btn.png", "region_key": "SCREEN_BOTTOM_BBOX"},
    ],
    "ocr_debug": [
      {"field": "skill_points", "source_type": "state_value", "parsed_value": current_sp},
      {"field": "skill_list_region", "source_type": "ocr_region", "region_key": "SCROLLING_SKILL_SCREEN_BBOX"},
      {"field": "buy_skill_icon", "source_type": "template_match", "template": "assets/icons/buy_skill.png", "region_key": "SCROLLING_SKILL_SCREEN_BBOX"},
      {"field": "skills_button", "source_type": "template_match", "template": "assets/buttons/skills_btn.png", "region_key": "SCREEN_BOTTOM_BBOX"},
    ],
  }

  if state.get("trackblazer_climax"):
    result["reason"] = "Climax finale races phase — no events can grant new skills; skipping skill check."
    return result
  if _all_skills_obtained:
    result["reason"] = "All configured skills are already learned. Skill checks permanently skipped for this run."
    return result
  if not bot.get_skill_auto_buy_enabled():
    result["reason"] = "Auto-buy skill is disabled in the runtime toggle."
    return result
  if current_sp < config.SKILL_PTS_CHECK:
    result["reason"] = f"Skill points {current_sp} below threshold {config.SKILL_PTS_CHECK}."
    return result
  if race_check and scheduled_g1_race:
    if previous_selected_race_skill_check_action_count >= 0 and (action_count - previous_selected_race_skill_check_action_count) < SKILL_SELECTED_RACE_RECHECK_TURNS:
      turns_remaining = SKILL_SELECTED_RACE_RECHECK_TURNS - (action_count - previous_selected_race_skill_check_action_count)
      result["reason"] = f"Selected-race skill check was already done recently; recheck in {turns_remaining} turn(s)."
      return result
    result["should_check"] = True
    result["reason"] = "Checking skills before scheduled G1 race."
    return result
  if previous_skill_check_action_count >= 0 and (action_count - previous_skill_check_action_count) < SKILL_RECHECK_TURNS:
    turns_remaining = SKILL_RECHECK_TURNS - (action_count - previous_skill_check_action_count)
    result["reason"] = f"Skill purchase was already checked recently; recheck in {turns_remaining} turn(s)."
    return result
  if config.CHECK_SKILL_BEFORE_RACES and race_check and (action_count > previous_action_count):
    result["should_check"] = True
    result["reason"] = "Checking skills before race."
    return result
  if previous_skill_check_action_count < 0:
    result["should_check"] = True
    result["reason"] = "Initial skill purchase check is due."
    return result
  if not normal_review_due:
    turns_remaining = SKILL_RECHECK_TURNS - (action_count - previous_skill_check_action_count)
    result["reason"] = f"Skill purchase was already checked recently; recheck in {turns_remaining} turn(s)."
    return result

  result["should_check"] = True
  result["reason"] = "Skill purchase recheck is due."
  return result

def buy_skill(state, action_count, race_check=False):
  global previous_action_count, previous_skill_check_action_count, previous_selected_race_skill_check_action_count
  debug(
    f"Skill buy: action={action_count}, last_purchase={previous_action_count}, "
    f"last_review={previous_skill_check_action_count}, race_check={race_check}"
  )
  if _all_skills_obtained:
    debug("Skill buy: all configured skills already learned; skipping.")
    return False
  if (bot.get_skill_auto_buy_enabled() and state["current_stats"]["sp"] >= config.SKILL_PTS_CHECK):
    pass
  else:
    return False
  scheduled_g1_race = _trackblazer_has_scheduled_g1_race(state)
  if race_check and scheduled_g1_race:
    if previous_selected_race_skill_check_action_count >= 0 and (action_count - previous_selected_race_skill_check_action_count) < SKILL_SELECTED_RACE_RECHECK_TURNS:
      info("Selected-race skill check is still on cooldown. Not trying.")
      return False
  elif previous_skill_check_action_count >= 0 and (action_count - previous_skill_check_action_count) < SKILL_RECHECK_TURNS:
    info("Skill purchase review is still on cooldown. Not trying.")
    return False
  elif config.CHECK_SKILL_BEFORE_RACES and race_check and (action_count > previous_action_count):
    debug(f"Passed race check condition.")
    pass

  mark_skill_purchase_checked(action_count, selected_race=scheduled_g1_race)
  previous_action_count = action_count
  device_action.locate_and_click("assets/buttons/skills_btn.png", min_search_time=get_secs(2))
  sleep(1)
  shopping_list=[]
  while True:
    screenshot1 = device_action.screenshot(region_ltrb=constants.SCROLLING_SKILL_SCREEN_BBOX)
    buy_skill_icons = device_action.match_template("assets/icons/buy_skill.png", screenshot1, threshold=0.9)
    debug(f"icon locations: {buy_skill_icons}")
    x1, y1, x2, y2 = constants.SCROLLING_SKILL_SCREEN_BBOX

    if buy_skill_icons:
      for x, y, w, h in buy_skill_icons:
        # mutate local coordinates to world coordinates
        x = x + x1
        y = y + y1
        region = (x - 420, y - 40, w + 275, h + 5)
        screenshot = enhanced_screenshot(region)
        text = extract_text(screenshot)
        debug(f"Extracted skill text: {text}")
        if is_skill_match(text, config.SKILL_LIST):
          button_region = (x, y, w, h)
          screenshot = device_action.screenshot(region_xywh=button_region)
          if compare_brightness(template_path="assets/icons/buy_skill.png", other=screenshot, brightness_diff_threshold=0.20):
            info(f"Buying {text}.")
            shopping_list.append(text)
            device_action.click(target=(x + 5, y + 5), duration=0.15)
          else:
            info(f"{text} found but not enough skill points.")
    sleep(0.5)
    debug(f"Scrolling skills...")
    device_action.swipe(constants.SKILL_SCROLL_BOTTOM_MOUSE_POS, constants.SKILL_SCROLL_TOP_MOUSE_POS)
    device_action.click(constants.SKILL_SCROLL_TOP_MOUSE_POS, duration=0)
    sleep(0.25)
    screenshot2 = device_action.screenshot(region_ltrb=constants.SCROLLING_SKILL_SCREEN_BBOX)
    if are_screenshots_same(screenshot1, screenshot2, diff_threshold=5):
      if len(shopping_list) > 0:
        info(f"Found skills, shopping list: {shopping_list}")
        device_action.locate_and_click("assets/buttons/confirm_btn.png")
        sleep(0.5)
        if constants.SCENARIO_NAME in ("mant", "trackblazer"):
          learned_close_template = constants.TRACKBLAZER_SKILL_UI_TEMPLATES.get("skills_learned_close")
          if learned_close_template:
            close_clicked = device_action.locate_and_click(
              learned_close_template,
              min_search_time=get_secs(2),
              template_scaling=_INVERSE_GLOBAL_SCALE,
            )
            if not close_clicked:
              warning("[SKILL] Trackblazer learned popup close not found; falling back to generic learn/close flow.")
              device_action.locate_and_click("assets/buttons/learn_btn.png", min_search_time=get_secs(1))
              device_action.locate_and_click("assets/buttons/close_btn.png", min_search_time=get_secs(2))
          else:
            device_action.locate_and_click("assets/buttons/learn_btn.png", min_search_time=get_secs(1))
            device_action.locate_and_click("assets/buttons/close_btn.png", min_search_time=get_secs(2))
        else:
          device_action.locate_and_click("assets/buttons/learn_btn.png", min_search_time=get_secs(1))
          device_action.locate_and_click("assets/buttons/close_btn.png", min_search_time=get_secs(2))
        device_action.locate_and_click("assets/buttons/back_btn.png", min_search_time=get_secs(2), region_ltrb=constants.SCREEN_BOTTOM_BBOX)
        return
      else:
        info(f"Reached end of skill screen. Returning.")
        device_action.locate_and_click("assets/buttons/back_btn.png", min_search_time=get_secs(2), region_ltrb=constants.SCREEN_BOTTOM_BBOX)
        return

def is_skill_match(text: str, skill_list: list[str], threshold: float = 0.9) -> bool:
  for skill in skill_list:
    similarity = Levenshtein.ratio(text.lower(), skill.lower())
    if similarity >= threshold:
      return True
  return False
