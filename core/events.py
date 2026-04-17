from rapidfuzz import fuzz
import re
from PIL import Image, ImageEnhance
import utils.device_action_wrapper as device_action

import core.config as config
import utils.constants as constants
from core.ocr import extract_text
from utils.log import debug, info, warning, error
from utils.screenshot import enhanced_screenshot
from utils.tools import sleep, get_secs

_EVENT_CHOICE_TEMPLATE = "assets/icons/event_choice_1.png"
_EVENT_CHOICE_VERTICAL_GAP = 112
_EVENT_CHOICE_THRESHOLD = 0.8
_DEFAULT_EVENT_SETTLE_SECONDS = 0.15

def event_choice(event_name):
  threshold = 0.8
  choice = 0

  if not event_name:
    return choice

  default_choice = {
    "character_name": "Unknown",
    "event_name": "Unknown Event",
    "chosen": 1
  }

  best_event_name, similarity = find_best_match(event_name, config.EVENT_CHOICES)
  debug(f"Best event name match: {best_event_name}, similarity: {similarity}")

  if similarity >= threshold:
    event = next(
      (e for e in config.EVENT_CHOICES if e["event_name"] == best_event_name),
      None,  # fallback
    )
    debug(
      f"Event found: {event_name} has {similarity * 100:.2f}% similarity with {event['event_name']}"
    )
    debug(f"event name: {event['event_name']}, chosen: {event['chosen']}")
    return event
  else:
    debug(
      f"No event found, {event_name} has {similarity * 100:.2f}% similarity with {best_event_name}"
    )
    return default_choice

def _crop_absolute_bbox_from_game_window_screenshot(screenshot, bbox):
  if screenshot is None or getattr(screenshot, "size", 0) == 0:
    return None
  game_window_bbox = getattr(constants, "GAME_WINDOW_BBOX", None)
  if not isinstance(game_window_bbox, tuple) or len(game_window_bbox) != 4:
    return None
  if not isinstance(bbox, tuple) or len(bbox) != 4:
    return None
  screenshot_h, screenshot_w = screenshot.shape[:2]
  left = max(0, int(bbox[0] - game_window_bbox[0]))
  top = max(0, int(bbox[1] - game_window_bbox[1]))
  right = min(screenshot_w, int(bbox[2] - game_window_bbox[0]))
  bottom = min(screenshot_h, int(bbox[3] - game_window_bbox[1]))
  if right <= left or bottom <= top:
    return None
  return screenshot[top:bottom, left:right].copy()


def _prepare_event_name_image(event_name_crop):
  if event_name_crop is None or getattr(event_name_crop, "size", 0) == 0:
    return None
  pil_img = Image.fromarray(event_name_crop)
  pil_img = pil_img.resize((pil_img.width * 2, pil_img.height * 2), Image.BICUBIC)
  pil_img = pil_img.convert("L")
  pil_img = ImageEnhance.Contrast(pil_img).enhance(1.5)
  return pil_img


def get_event_name(screenshot=None):
  img = None
  if screenshot is not None:
    event_name_crop = _crop_absolute_bbox_from_game_window_screenshot(
      screenshot,
      getattr(constants, "EVENT_NAME_BBOX", None),
    )
    img = _prepare_event_name_image(event_name_crop)
  if img is None:
    img = enhanced_screenshot(constants.EVENT_NAME_REGION)
  text = extract_text(img)
  debug(f"Event name: {text}")
  return text

def find_best_match(text: str, event_list: list[dict]) -> tuple[str, float]:
  """Find the best matching skill and similarity score"""
  if not text or not event_list:
    return "", 0.0

  best_match = ""
  best_similarity = 0.0

  for event in event_list:
    event_name = event["event_name"]
    clean_text = re.sub(
      r"\s*\((?!Year 2\))[^\)]*\)", "", event_name
    ).strip()  # remove parentheses
    clean_text = re.sub(r"[^\x00-\x7F]", "", clean_text)  # remove non-ASCII
    similarity = fuzz.token_sort_ratio(clean_text.lower(), text.lower()) / 100
    if similarity > best_similarity:
      best_similarity = similarity
      best_match = event_name

  return best_match, best_similarity

def find_event_choice_icon(screenshot=None, threshold=_EVENT_CHOICE_THRESHOLD):
  region_ltrb = getattr(constants, "GAME_WINDOW_BBOX", None)
  if screenshot is None:
    screenshot = device_action.screenshot(region_ltrb=region_ltrb)
  matches = device_action.match_template(
    _EVENT_CHOICE_TEMPLATE,
    screenshot,
    threshold=threshold,
  )
  if not matches:
    return None
  x, y, w, h = matches[0]
  if isinstance(region_ltrb, tuple) and len(region_ltrb) == 4:
    return (int(region_ltrb[0] + x + (w // 2)), int(region_ltrb[1] + y + (h // 2)))
  return (int(x + (w // 2)), int(y + (h // 2)))


# needs a rework can be more optimized
def select_event(event_choices_icon=None, screenshot=None, settle_seconds=_DEFAULT_EVENT_SETTLE_SECONDS):
  event_choices_icon = event_choices_icon or find_event_choice_icon(screenshot=screenshot)

  if not event_choices_icon:
    return False

  if not config.USE_OPTIMAL_EVENT_CHOICE:
    device_action.click(target=event_choices_icon, text=f"Event found, selecting top choice.")
    if settle_seconds > 0:
      sleep(settle_seconds)
    return True

  event_name = get_event_name(screenshot=screenshot)
  if not event_name or event_name == "":
    debug(f"No event name found, returning False")
    return False
  debug(f"Event Name: {event_name}")

  event = event_choice(event_name)
  chosen = event["chosen"]
  debug(f"Event Choice: {chosen}")
  if chosen == 0:
    device_action.click(target=event_choices_icon, text=f"Event found, selecting top choice.")
    if settle_seconds > 0:
      sleep(settle_seconds)
    return True
  
  if event["event_name"] == "A Team at Last":
    debug(f"Team selection event entered")
    current_coords = event_choices_icon
    choice_texts = ["Hoppers", "Runners", "Pudding", "Bloom", "Carrot"]
    test_against = choice_texts[chosen - 1]
    debug(f"test against: {test_against}")
    debug(f"Outside while, coord compare: {current_coords[1]} < {constants.SCREEN_MIDDLE_BBOX[3]}")
    while current_coords[1] < constants.SCREEN_MIDDLE_BBOX[3]:
      debug(f"Coord compare: {current_coords[1]} < {constants.SCREEN_MIDDLE_BBOX[3]}")

      region_xywh = (
        current_coords[0] + 90,
        current_coords[1] - 25,
        500,
        35)
      screenshot = enhanced_screenshot(region_xywh)
      text = extract_text(screenshot)
      debug(f"Text: {text}")
      if test_against == "Carrot":
        debug(f"test against: {test_against} in text: {text}")
        if "Pudding" not in text and "Carrot" in text:
          debug(f"Clicking: {current_coords}")
          device_action.click(target=current_coords, text=f"Selecting optimal choice: {event_name}")
          if settle_seconds > 0:
            sleep(settle_seconds)
          break
      elif test_against in text:
        debug(f"test against: {test_against} in text: {text}")
        debug(f"Clicking: {current_coords}")
        device_action.click(target=current_coords, text=f"Selecting optimal choice: {event_name}")
        if settle_seconds > 0:
          sleep(settle_seconds)
        break
      current_coords = (current_coords[0], current_coords[1] + _EVENT_CHOICE_VERTICAL_GAP)
  else:
    x = event_choices_icon[0]
    y = event_choices_icon[1] + ((chosen - 1) * _EVENT_CHOICE_VERTICAL_GAP)
    # debug(f"Event choices coordinates: {event_choices_icon}")
    debug(f"Event choices coordinates: {event_choices_icon}")
    # debug(f"Clicking: {x}, {y}")
    debug(f"Clicking: {x}, {y}")
    device_action.click(target=(x, y), text=f"Selecting optimal choice: {event_name}")
    if settle_seconds > 0:
      sleep(settle_seconds)
    if "Acupuncturist" in event_name:
      confirm_acupuncturist_y = event_choices_icon[1] + ((4 - 1) * _EVENT_CHOICE_VERTICAL_GAP)
      device_action.click(target=(x, confirm_acupuncturist_y), text=f"Selecting optimal choice: {event_name}")
      # click(boxes=(x, confirm_acupuncturist_y, 1, 1), text="Confirm acupuncturist.")
  return True
