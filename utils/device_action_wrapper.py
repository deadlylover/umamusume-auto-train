import cv2
import numpy as np
import core.bot as bot
import utils.pyautogui_actions as pyautogui_actions
import utils.adb_actions as adb_actions
import utils.constants as constants
from utils.log import error, info, warning, debug, debug_window, args

from time import sleep, time

# Temporary global template scaling workaround added during Tazuna hint troubleshooting.
# Approximate factor is 1.26, but this was applied broadly as a debugging shortcut and may
# only be correct for some assets. If template matching regresses in isolated places, verify
# whether this global scale is masking an asset-specific sizing problem instead.
GLOBAL_TEMPLATE_SCALING = 1.26

class BotStopException(Exception):
  #Exception raised to immediately stop the bot
  pass

def stop_bot():
  # Stop the bot immediately by raising an exception
  flush_screenshot_cache()
  bot.is_bot_running = False
  raise BotStopException("Bot stopped by user")

Pos = tuple[int, int]                     # (x, y)
Box = tuple[int, int, int, int]           # (x, y, w, h)

def click(target: Pos | Box, clicks: int = 1, interval: float = 0.1, duration: float = 0.225, text: str = ""):
  if text:
    debug(text)
  if not bot.is_bot_running and not bot.is_manual_control_active():
    stop_bot()
  if target is None or len(target) == 0:
    return False
  elif len(target) == 2:
    x, y = target
    if bot.is_adb_input_active():
      sleep(duration)
      for _ in range(clicks):
        if not adb_actions.click(x, y):
          error(f"[INPUT][ADB] Click dispatch failed at ({x}, {y}).")
          return False
        sleep(interval)
    else:
      pyautogui_actions.click(x_y=(x, y), clicks=clicks, interval=interval, duration=duration)
  elif len(target) == 4:
    x, y, w, h = target
    cx = x + w // 2
    cy = y + h // 2
    if bot.is_adb_input_active():
      sleep(duration)
      for _ in range(clicks):
        if not adb_actions.click(cx, cy):
          error(f"[INPUT][ADB] Click dispatch failed at ({cx}, {cy}).")
          return False
        sleep(interval)
    else:
      pyautogui_actions.click(x_y=(cx, cy), clicks=clicks, interval=interval, duration=duration)
  else:
    raise TypeError(f"Expected (x, y) or (x, y, w, h) tuple, got type {type(target)}: {target}")
  if args.device_debug:
    debug(f"We clicked on {target}, screen might change, flushing screenshot cache.")
  flush_screenshot_cache()
  sleep(0.35)
  return True

def swipe(start_x_y : tuple[int, int], end_x_y : tuple[int, int], duration=0.3, text: str = ""):
  if text and args.device_debug:
    debug(text)
  # Swipe from start to end coordinates
  if not bot.is_bot_running and not bot.is_manual_control_active():
    stop_bot()
  if bot.is_adb_input_active():
    if not adb_actions.swipe(start_x_y[0], start_x_y[1], end_x_y[0], end_x_y[1], duration):
      error(f"[INPUT][ADB] Swipe dispatch failed from {start_x_y} to {end_x_y}.")
      return False
  else:
    pyautogui_actions.swipe(start_x_y, end_x_y, duration)
  if args.device_debug:
    debug(f"We swiped from {start_x_y} to {end_x_y}, screen might change, flushing screenshot cache.")
  flush_screenshot_cache()
  return True

def drag(start_x_y : tuple[int, int], end_x_y : tuple[int, int], duration=0.5, text: str = ""):
  if text and args.device_debug:
    debug(text)
  # Swipe from start to end coordinates and click at the end
  if not bot.is_bot_running and not bot.is_manual_control_active():
    stop_bot()
  swipe(start_x_y, end_x_y, duration)
  click(end_x_y)
  if args.device_debug:
    debug(f"We dragged from {start_x_y} to {end_x_y}, screen might change, flushing screenshot cache.")
  flush_screenshot_cache()
  return True

def long_press(mouse_x_y : tuple[int, int], duration=2.0, text: str = ""):
  if text and args.device_debug:
    debug(text)
  # Long press at coordinates
  if not bot.is_bot_running and not bot.is_manual_control_active():
    stop_bot()
  swipe(mouse_x_y, mouse_x_y, duration)
  if args.device_debug:
    debug(f"We long pressed on {mouse_x_y}, screen might change, flushing screenshot cache.")
  flush_screenshot_cache()
  sleep(0.35)
  return True

def _resize_template(template: np.ndarray, scale: float):
  width = max(1, int(round(template.shape[1] * scale)))
  height = max(1, int(round(template.shape[0] * scale)))
  interpolation = cv2.INTER_LINEAR if scale >= 1.0 else cv2.INTER_AREA
  return cv2.resize(template, (width, height), interpolation=interpolation)

def _effective_template_scale(template_scaling: float = 1.0):
  return float(GLOBAL_TEMPLATE_SCALING) * float(template_scaling)

_template_image_cache = {}
_scaled_template_cache = {}

def _load_template_image(template_path: str, grayscale=False):
  cache_key = (template_path, grayscale)
  cached = _template_image_cache.get(cache_key)
  if cached is not None:
    return cached
  if grayscale:
    img = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
  else:
    img = cv2.imread(template_path, cv2.IMREAD_COLOR)
    if img is None:
      return None
    if len(img.shape) == 3 and img.shape[2] == 4:
      img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
  _template_image_cache[cache_key] = img
  return img

def _load_scaled_template(template_path: str, scale: float, grayscale=False):
  cache_key = (template_path, round(scale, 6), grayscale)
  cached = _scaled_template_cache.get(cache_key)
  if cached is not None:
    return cached
  template = _load_template_image(template_path, grayscale=grayscale)
  if template is None:
    return None
  if abs(scale - 1.0) < 1e-6:
    _scaled_template_cache[cache_key] = template
    return template
  scaled = _resize_template(template, scale)
  _scaled_template_cache[cache_key] = scaled
  return scaled

def best_template_match(template_path: str, screenshot: np.ndarray, grayscale=False, template_scales=None):
  if grayscale:
    screenshot = cv2.cvtColor(screenshot, cv2.COLOR_RGB2GRAY)

  scales = template_scales or [_effective_template_scale()]
  best_match = None

  for scale in scales:
    resized_template = _load_scaled_template(template_path, scale, grayscale=grayscale)
    if resized_template is None:
      error(f"Template '{template_path}' could not be loaded.")
      return None

    screenshot_h, screenshot_w = screenshot.shape[:2]
    template_h, template_w = resized_template.shape[:2]
    if template_h > screenshot_h or template_w > screenshot_w:
      continue

    result = cv2.matchTemplate(screenshot, resized_template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    candidate = {
      "score": float(max_val),
      "scale": float(scale),
      "location": max_loc,
      "template": resized_template,
      "size": (template_w, template_h),
    }
    if best_match is None or candidate["score"] > best_match["score"]:
      best_match = candidate

  return best_match

def match_cached_templates(cached_templates, region_ltrb=None, threshold=0.85, text: str = "", template_scaling=1.0, stop_after_first_match=False):
  if region_ltrb == None:
    raise ValueError(f"region_ltrb cannot be None")
  _screenshot = screenshot(region_ltrb=region_ltrb)
  results = {}
  effective_scale = _effective_template_scale(template_scaling)
  if args.save_images:
    debug_window(_screenshot, save_name=f"cached_templates_screenshot")
  for name, template in cached_templates.items():
    scaled_template = template if abs(effective_scale - 1.0) < 1e-6 else _resize_template(template, effective_scale)
    if args.save_images:
      debug_window(scaled_template, save_name=f"{name}_template")
    
    # Validate template and screenshot dimensions before matching
    screenshot_h, screenshot_w = _screenshot.shape[:2]
    template_h, template_w = scaled_template.shape[:2]
    
    if template_h > screenshot_h or template_w > screenshot_w:
      error(f"Cached template '{name}' is larger than screenshot!")
      error(f"  Template size: {template_w}x{template_h}")
      error(f"  Screenshot size: {screenshot_w}x{screenshot_h}")
      results[name] = []  # Return empty list for this template
      continue
    
    result = cv2.matchTemplate(_screenshot, scaled_template, cv2.TM_CCOEFF_NORMED)
    loc = np.where(result >= threshold)
    h, w = scaled_template.shape[:2]
    boxes = [(x+region_ltrb[0], y+region_ltrb[1], w, h) for (x, y) in zip(*loc[::-1])]
    results[name] = deduplicate_boxes(boxes)
    if stop_after_first_match and len(results[name]) > 0:
      debug(f"Stopping after first match: {name}")
      break

  print(f"Results: {results}")
  return results

def multi_match_templates(templates, screenshot: np.ndarray, threshold=0.85, text: str = "", template_scaling=1.0, stop_after_first_match=False):
  results = {}
  for name, path in templates.items():
    if text and args.device_debug:
      text = f"[{name}] {text}"
    results[name] = match_template(path, screenshot, threshold, text, template_scaling=template_scaling)
    if stop_after_first_match and len(results[name]) > 0:
      debug(f"Template found: {name}")
      break
  return results

def match_template(template_path : str, screenshot : np.ndarray, threshold=0.85, text: str = "", grayscale=False, template_scaling=1.0):
  if text and args.device_debug:
    debug(text)
  effective_scale = _effective_template_scale(template_scaling)
  template = _load_scaled_template(template_path, effective_scale, grayscale=grayscale)
  if template is None:
    error(f"Template '{template_path}' could not be loaded.")
    return []
  if grayscale:
    screenshot = cv2.cvtColor(screenshot, cv2.COLOR_RGB2GRAY)
  if args.save_images:
    template_name = template_path.split("/")[-1].split(".")[0]
    debug_window(template, save_name=f"{template_name}_template")
    debug_window(screenshot, save_name=f"{template_name}_screenshot")
  
  # Validate template and screenshot dimensions before matching
  screenshot_h, screenshot_w = screenshot.shape[:2]
  template_h, template_w = template.shape[:2]
  
  if template_h > screenshot_h or template_w > screenshot_w:
    error(f"Template '{template_path}' is larger than screenshot!")
    error(f"  Template size: {template_w}x{template_h}")
    error(f"  Screenshot size: {screenshot_w}x{screenshot_h}")
    error(f"  Template scaling: {effective_scale}")
    return []  # Return empty list instead of crashing
  
  result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
  loc = np.where(result >= threshold)

  h, w = template.shape[:2]
  boxes = [(x, y, w, h) for (x, y) in zip(*loc[::-1])]

  return deduplicate_boxes(boxes)

def match_template_multiscale(template_path: str, screenshot: np.ndarray, threshold=0.85, text: str = "", grayscale=False, template_scales=None):
  if text and args.device_debug:
    debug(text)

  best_match = best_template_match(
    template_path,
    screenshot,
    grayscale=grayscale,
    template_scales=template_scales,
  )
  if best_match is None:
    return []

  if args.device_debug:
    debug(
      f"Best multi-scale match for '{template_path}': "
      f"score={best_match['score']:.4f}, scale={best_match['scale']:.3f}, "
      f"location={best_match['location']}, size={best_match['size']}"
    )

  if best_match["score"] < threshold:
    return []

  x, y = best_match["location"]
  w, h = best_match["size"]
  return [(x, y, w, h)]

def deduplicate_boxes(boxes_xywh : list[tuple[int, int, int, int]], min_dist=5):
  # boxes_xywh = (x, y, width, height)
  filtered = []
  for x, y, w, h in boxes_xywh:
    cx, cy = x + w // 2, y + h // 2
    if all(abs(cx - (fx + fw // 2)) > min_dist or abs(cy - (fy + fh // 2)) > min_dist
        for fx, fy, fw, fh in filtered):
      filtered.append((x, y, w, h))
  return filtered

def screenshot(region_xywh : tuple[int, int, int, int] = None, region_ltrb : tuple[int, int, int, int] = None):
  if not bot.is_bot_running and not bot.is_manual_control_active():
    stop_bot()

  screenshot = None
  if region_xywh:
    if args.device_debug:
      debug(f"Screenshot: {region_xywh}")
  elif region_ltrb:
    left, top, right, bottom = region_ltrb
    region_xywh = (left, top, right - left, bottom - top)
    if args.device_debug:
      debug(f"Screenshot: {region_xywh}")
  else:
    if args.device_debug:
      debug(f"Screenshot: {constants.GAME_WINDOW_REGION}")

  if bot.uses_adb_for_screenshots():
    if args.device_debug:
      debug(f"Using ADB screenshot")
    screenshot = adb_actions.screenshot(region_xywh=region_xywh)
  else:
    if args.device_debug:
      debug(f"Using PyAutoGUI screenshot")
    screenshot = pyautogui_actions.screenshot(region_xywh=region_xywh)
  debug_window(screenshot, save_name="device_screenshot")
  return np.array(screenshot)

def screenshot_match(match, region : tuple[int, int, int, int]):
  screenshot_region=(
    match[0] + region[0],
    match[1] + region[1],
    match[2],
    match[3]
  )
  return screenshot(region_xywh=screenshot_region)

def locate(img_path : str, confidence=0.8, min_search_time=0, region_ltrb : tuple[int, int, int, int] = None, text: str = "", template_scaling=1.0):
  if text and args.device_debug:
    debug(text)
  if region_ltrb is None:
    region_ltrb = constants.GAME_WINDOW_BBOX
  time_start = time()
  _screenshot = screenshot(region_ltrb=region_ltrb)
  boxes = match_template(img_path, _screenshot, confidence, template_scaling=template_scaling)
  tries = 1
  elapsed_time = time() - time_start

  while len(boxes) < 1 and elapsed_time < min_search_time:
    tries += 1
    flush_screenshot_cache()
    _screenshot = screenshot(region_ltrb=region_ltrb)
    boxes = match_template(img_path, _screenshot, confidence, template_scaling=template_scaling)
    sleep(0.5)
    elapsed_time = time() - time_start

  if len(boxes) < 1:
    if min_search_time > 0:
      info(f"{img_path} not found after {elapsed_time:.2f} seconds, tried {tries} times")
    return None
  if args.device_debug:
    debug(f"{img_path} found after {elapsed_time:.2f} seconds, tried {tries} times")
  x, y, w, h = boxes[0]
  offset_x = region_ltrb[0]
  offset_y = region_ltrb[1]

  x_center = x + w // 2 + offset_x
  y_center = y + h // 2 + offset_y

  if args.device_debug:
    debug(f"locate: {x_center}, {y_center}")
  coordinates = (x_center, y_center)
  return coordinates

def locate_and_click(img_path : str, confidence=0.8, min_search_time=0.5, region_ltrb : tuple[int, int, int, int] = None, duration=0.225, text: str = "", template_scaling=1.0):
  if img_path is None or img_path == "":
    error(f"img_path is empty")
    raise ValueError(f"img_path is empty")
  if text and args.device_debug:
    debug(text)
  if region_ltrb is None:
    region_ltrb = constants.GAME_WINDOW_BBOX
  if args.device_debug:
    debug(f"locate_and_click: {img_path}, {region_ltrb}")
  coordinates = locate(img_path, confidence, min_search_time, region_ltrb=region_ltrb, template_scaling=template_scaling)
  if args.device_debug:
    debug(f"locate_and_click: {coordinates}")

  if coordinates:
    click(coordinates, duration=duration)
    return True
  return False

def flush_screenshot_cache():
  if bot.uses_adb_for_screenshots():
    if args.device_debug:
      debug(f"Flushing ADB screenshot cache")
    adb_actions.cached_screenshot = []
  else:
    if args.device_debug:
      debug(f"Flushing PyAutoGUI screenshot cache")
    pyautogui_actions.cached_screenshot = []
    pyautogui_actions.cached_region = None
    pyautogui_actions._macos_full_screen_cache = None
