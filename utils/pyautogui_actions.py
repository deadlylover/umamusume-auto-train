# pyautogui_actions.py - PyAutoGUI device actions with macOS support
# This module provides the pyautogui implementation of device actions
# Adapted for macOS with mss screenshot support

import pyautogui
import mss
import utils.constants as constants
from utils.log import debug, warning, error, info, debug_window, args
import core.bot as bot
import numpy as np
import cv2
import platform

# Detect if running on macOS
IS_MACOS = platform.system() == "Darwin"
LAST_CLICK_DEBUG = {}

def click(x_y : tuple[int, int], clicks: int = 1, interval: float = 0.1, duration: float = 0.225):
  global LAST_CLICK_DEBUG
  before = pyautogui.position()
  pyautogui.click(x_y[0], x_y[1], clicks=clicks, interval=interval, duration=duration)
  after = pyautogui.position()
  LAST_CLICK_DEBUG = {
    "target": [int(x_y[0]), int(x_y[1])],
    "before": [int(before.x), int(before.y)],
    "after": [int(after.x), int(after.y)],
    "clicks": int(clicks),
    "interval": float(interval),
    "duration": float(duration),
    "moved": (int(before.x), int(before.y)) != (int(after.x), int(after.y)),
    "reached_target": abs(int(after.x) - int(x_y[0])) <= 2 and abs(int(after.y) - int(x_y[1])) <= 2,
  }
  return True

def press_click(x_y: tuple[int, int], hold_duration: float = 0.08, move_duration: float = 0.225):
  global LAST_CLICK_DEBUG
  before = pyautogui.position()
  pyautogui.moveTo(x_y[0], x_y[1], duration=move_duration)
  pyautogui.mouseDown()
  pyautogui.sleep(hold_duration)
  pyautogui.mouseUp()
  after = pyautogui.position()
  LAST_CLICK_DEBUG = {
    "target": [int(x_y[0]), int(x_y[1])],
    "before": [int(before.x), int(before.y)],
    "after": [int(after.x), int(after.y)],
    "move_duration": float(move_duration),
    "hold_duration": float(hold_duration),
    "moved": (int(before.x), int(before.y)) != (int(after.x), int(after.y)),
    "reached_target": abs(int(after.x) - int(x_y[0])) <= 2 and abs(int(after.y) - int(x_y[1])) <= 2,
    "click_mode": "press_click",
  }
  return True

def swipe(start_x_y : tuple[int, int], end_x_y : tuple[int, int], duration=0.3):
  delay_to_first_move = 0.1
  moveTo(start_x_y[0], start_x_y[1], duration=delay_to_first_move)
  hold()
  moveTo(end_x_y[0], end_x_y[1], duration=duration-delay_to_first_move)
  release()
  return True

def moveTo(x, y, duration=0.2):
  pyautogui.moveTo(x, y, duration=duration)
  return True

def hold():
  pyautogui.mouseDown()
  return True

def release():
  pyautogui.mouseUp()
  return True

def crop_screenshot(screenshot, pixel_crop_amount):
  # crop screenshot width-wise by pixel_crop_amount
  return screenshot[:, pixel_crop_amount:-pixel_crop_amount]

def scale_screenshot(screenshot, scaling_factor):
  # scale screenshot by scaling_factor
  return cv2.resize(screenshot, (int(screenshot.shape[1] * scaling_factor), int(screenshot.shape[0] * scaling_factor)), interpolation=cv2.INTER_AREA)


def resize_screenshot_as_1080p(screenshot):
  scaling_factor = 1
  pixel_crop_amount = 0
  if screenshot.shape[1] != expected_window_size[1]:
    pixel_crop_amount = expected_window_size[1] - screenshot.shape[1]
    if pixel_crop_amount > 0:
      screenshot = crop_screenshot(screenshot, pixel_crop_amount)
  if screenshot.shape[0] != expected_window_size[0]:
    scaling_factor = expected_window_size[0] / screenshot.shape[0]
    if scaling_factor != 1:
      screenshot = scale_screenshot(screenshot, scaling_factor)
  return screenshot

expected_window_size = (1080, 1920)
cached_screenshot = []
cached_region = None

# macOS full-screen cache: capture once, crop many sub-regions.
# This matches the OCR adjuster's capture path (sct.grab(monitors[0])),
# guaranteeing identical coordinate-space behavior.
_macos_full_screen_cache = None
_macos_full_screen_meta = None  # diagnostic info from last capture


def _capture_macos_full_screen():
  """Grab the full virtual screen via mss, matching OCR adjuster behavior."""
  global _macos_full_screen_cache, _macos_full_screen_meta
  with mss.mss() as sct:
    monitor = dict(sct.monitors[0])
    raw = np.array(sct.grab(monitor))
    full = cv2.cvtColor(raw, cv2.COLOR_BGRA2RGB)
    _macos_full_screen_cache = full
    _macos_full_screen_meta = {
      "monitor": monitor,
      "image_size": f"{full.shape[1]}x{full.shape[0]}",
    }
    if args.device_debug:
      debug(
        f"[macOS] Full screen capture: monitor={monitor}, "
        f"image_size={full.shape[1]}x{full.shape[0]}"
      )
  return _macos_full_screen_cache


def screenshot(region_xywh : tuple[int, int, int, int] = None):
  """
  Take a screenshot of the game window.

  On macOS, we capture the full screen (same as OCR adjuster) and crop
  the requested region. This avoids coordinate-space mismatches between
  the adjuster's calibration and runtime capture.

  On Windows, we use the windows_window bounds from bot module.
  """
  global cached_screenshot, cached_region
  screenshot_data = None

  if not region_xywh:
    region_xywh = constants.GAME_WINDOW_REGION

  if args.device_debug:
    debug(f"Screenshot region: {region_xywh}")

  if len(cached_screenshot) > 0 and cached_region == region_xywh:
    if args.device_debug:
      debug(f"Using cached screenshot")
    screenshot_data = cached_screenshot
    return screenshot_data
  else:
    if IS_MACOS:
      x, y, w, h = region_xywh if region_xywh else constants.GAME_WINDOW_REGION

      # Grab full screen (cached) and crop — identical to OCR adjuster path.
      full = _macos_full_screen_cache
      if full is None:
        full = _capture_macos_full_screen()

      # Clamp crop bounds to the full image dimensions.
      fh, fw = full.shape[:2]
      x1 = max(0, x)
      y1 = max(0, y)
      x2 = min(fw, x + w)
      y2 = min(fh, y + h)

      if args.device_debug:
        debug(
          f"[macOS] Cropping region ({x}, {y}, {w}, {h}) "
          f"-> clamped ({x1}, {y1}, {x2}, {y2}) from {fw}x{fh} full screen"
        )

      if x2 > x1 and y2 > y1:
        screenshot_data = full[y1:y2, x1:x2].copy()
      else:
        warning(f"Empty crop region after clamping: ({x1}, {y1}, {x2}, {y2})")
        screenshot_data = np.zeros((max(1, h), max(1, w), 3), dtype=np.uint8)

      cached_screenshot = screenshot_data
      cached_region = region_xywh
      return screenshot_data
    else:
      # On Windows, use the window bounds from bot module
      if bot.windows_window:
        window_x, window_y = bot.windows_window.left, bot.windows_window.top
        window_width, window_height = bot.windows_window.width, bot.windows_window.height
      else:
        raise Exception("Couldn't find the windows_window somehow, please report this error.")
      
      window_region = {
        "left": window_x,
        "top": window_y,
        "width": window_width,
        "height": window_height
      }
      with mss.mss() as sct:
        if args.device_debug:
          debug(f"Taking new screenshot")
        # take screenshot as BGRA
        screenshot_data = np.array(sct.grab(window_region))
        screenshot_data = cv2.cvtColor(screenshot_data, cv2.COLOR_BGRA2RGB)

  screenshot_data = resize_screenshot_as_1080p(screenshot_data)
  cached_screenshot = screenshot_data

  # crop screenshot to region_xywh
  if region_xywh:
    x, y, w, h = region_xywh
    screenshot_data = screenshot_data[y:y+h, x:x+w]
  
  return screenshot_data
