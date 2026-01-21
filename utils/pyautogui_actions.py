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

def click(x_y : tuple[int, int], clicks: int = 1, interval: float = 0.1, duration: float = 0.225):
  pyautogui.click(x_y[0], x_y[1], clicks=clicks, interval=interval, duration=duration)
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

def screenshot(region_xywh : tuple[int, int, int, int] = None):
  """
  Take a screenshot of the game window.
  
  On macOS, we use mss to capture the screen region directly since
  pygetwindow doesn't work well with BlueStacks Air.
  
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
      # On macOS, use mss to grab the specified region directly
      # The region coordinates should already be absolute screen coordinates
      x, y, w, h = region_xywh if region_xywh else constants.GAME_WINDOW_REGION
      if args.device_debug:
        debug(f"[DEBUG] macOS screenshot region_xywh: {region_xywh}, x={x}, y={y}, w={w}, h={h}")
      window_region = {
        "left": x,
        "top": y,
        "width": w,
        "height": h
      }
      with mss.mss() as sct:
        if args.device_debug:
          debug(f"Taking new macOS screenshot")
        # take screenshot as BGRA
        screenshot_data = np.array(sct.grab(window_region))
        screenshot_data = cv2.cvtColor(screenshot_data, cv2.COLOR_BGRA2RGB)
      
      # Cache only for this exact region to avoid cross-region mismatches.
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
