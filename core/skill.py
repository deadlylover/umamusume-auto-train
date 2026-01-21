from utils.tools import sleep, drag_scroll
import pyautogui
import Levenshtein

import utils.constants as constants

from utils.log import info, warning, error, debug
from utils.screenshot import enhanced_screenshot
from core.ocr import extract_text
from core.recognizer import match_template, is_btn_active
import core.state as state

def _skill_text_region_from_button(button_bbox):
  """
  Convert a buy-button bbox (x, y, w, h) into the OCR capture region for the skill text.
  Uses two calibratable constants so users can realign via the F6 region tool.
  """
  anchor_region = getattr(constants, "SKILL_BUY_TEMPLATE_REGION", None)
  text_region = getattr(constants, "SKILL_BUY_TEXT_REGION", None)

  if anchor_region and len(anchor_region) >= 2 and text_region and len(text_region) >= 4:
    anchor_x, anchor_y = anchor_region[:2]
    text_x, text_y, text_w, text_h = text_region
    offset_x = text_x - anchor_x
    offset_y = text_y - anchor_y
    x, y, _, _ = button_bbox
    return (x + offset_x, y + offset_y, text_w, text_h)

  # Fallback to historical offsets if constants are missing.
  x, y, w, h = button_bbox
  return (x - 420, y - 40, w + 275, h + 5)

def buy_skill():
  pyautogui.moveTo(constants.SCROLLING_SELECTION_MOUSE_POS)
  found = False

  for i in range(10):
    if state.stop_event.is_set():
      return

    if i > 8:
      sleep(0.5)

    debug(f"[Skill OCR] Pass {i + 1}/10: scanning visible skills.")
    buy_skill_icon = match_template("assets/icons/buy_skill.png", threshold=0.9)

    if not buy_skill_icon:
      debug("[Skill OCR] No buy buttons detected in this pass.")

    if buy_skill_icon:
      for x, y, w, h in buy_skill_icon:
        region = _skill_text_region_from_button((x, y, w, h))
        screenshot = enhanced_screenshot(region)
        text = extract_text(screenshot).strip()

        debug(f"[Skill OCR] Region {region} -> \"{text}\"")
        matched_skill, similarity = find_skill_match(text, state.SKILL_LIST)

        if matched_skill:
          button_region = (x, y, w, h)
          debug(f"[Skill OCR] Best match '{matched_skill}' ({similarity:.3f}). Checking button state.")
          if is_btn_active(button_region):
            info(f"Buy {matched_skill} (OCR='{text}', sim={similarity:.2f})")
            pyautogui.click(x=x + 5, y=y + 5, duration=0.15)
            found = True
          else:
            info(f"{matched_skill} found but not enough skill points.")
        else:
          debug(f"[Skill OCR] No match found for \"{text}\" (best similarity {similarity:.3f}).")

    drag_scroll(constants.SKILL_SCROLL_BOTTOM_MOUSE_POS, -450)
    debug("[Skill OCR] Scrolled skill list by -450 pixels.")

  return found

def find_skill_match(text: str, skill_list: list[str], threshold: float = 0.8):
  """
  Return the best matching skill name and similarity score if it clears the threshold.
  """
  if not text:
    return None, 0.0

  best_skill = None
  best_similarity = 0.0
  lowered_text = text.lower()

  for skill in skill_list:
    similarity = Levenshtein.ratio(lowered_text, skill.lower())
    if similarity > best_similarity:
      best_skill = skill
      best_similarity = similarity

  if best_similarity >= threshold:
    return best_skill, best_similarity

  return None, best_similarity
