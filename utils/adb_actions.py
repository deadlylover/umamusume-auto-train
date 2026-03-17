# Try to import adbutils, but make it optional for macOS
try:
  from adbutils import adb
except ImportError:
  adb = None

import numpy as np
import core.bot as bot
import utils.constants as constants
from utils.log import info, debug, error, debug_window, args
from utils.constants import name_of_variable

DEFAULT_DEVICE_ID = "127.0.0.1:5555"
device = None
LAST_INPUT_DEBUG = {}
LAST_STATUS = {
  "requested_backend": bot.CONTROL_BACKEND_HOST_INPUT,
  "active_backend": bot.CONTROL_BACKEND_HOST_INPUT,
  "device_id": None,
  "adb_available": adb is not None,
  "adb_connected": False,
  "device_ready": False,
  "healthcheck_passed": False,
  "healthy": False,
  "adb_last_error": "",
}


def _parse_wm_size(output):
  parsed = {"raw": output}
  if not output:
    return parsed
  for line in str(output).splitlines():
    line = line.strip()
    if not line or ":" not in line:
      continue
    key, value = line.split(":", 1)
    key = key.strip().lower().replace(" ", "_")
    value = value.strip()
    if "x" in value:
      width_text, height_text = value.split("x", 1)
      try:
        parsed[key] = {
          "width": int(width_text.strip()),
          "height": int(height_text.strip()),
        }
      except ValueError:
        parsed[key] = value
    else:
      parsed[key] = value
  return parsed


def _parse_density(output):
  parsed = {"raw": output}
  if not output:
    return parsed
  for line in str(output).splitlines():
    line = line.strip()
    if not line or ":" not in line:
      continue
    key, value = line.split(":", 1)
    key = key.strip().lower().replace(" ", "_")
    value = value.strip()
    try:
      parsed[key] = int(value)
    except ValueError:
      parsed[key] = value
  return parsed


def _collect_device_display_info():
  if device is None:
    return {}
  info_map = {}
  try:
    info_map["wm_size"] = _parse_wm_size(str(device.shell("wm size")).strip())
  except Exception as exc:
    info_map["wm_size_error"] = str(exc)
  try:
    info_map["wm_density"] = _parse_density(str(device.shell("wm density")).strip())
  except Exception as exc:
    info_map["wm_density_error"] = str(exc)
  try:
    device_screenshot = np.array(device.screenshot(error_ok=False))
    info_map["screenshot_size"] = {
      "width": int(device_screenshot.shape[1]),
      "height": int(device_screenshot.shape[0]),
    }
  except Exception as exc:
    info_map["screenshot_size_error"] = str(exc)
  return info_map


def _build_status(device_id=None, error_text=""):
  resolved_device_id = device_id or bot.device_id or DEFAULT_DEVICE_ID
  return {
    "requested_backend": bot.get_requested_control_backend(),
    "active_backend": bot.get_active_control_backend(),
    "device_id": resolved_device_id,
    "adb_available": adb is not None,
    "adb_connected": False,
    "device_ready": False,
    "healthcheck_passed": False,
    "healthy": False,
    "adb_last_error": error_text,
  }


def _publish_status(status):
  global LAST_STATUS
  LAST_STATUS = dict(status or {})
  bot.update_adb_status(LAST_STATUS)
  return LAST_STATUS


def _record_runtime_error(message, connected=None):
  status = dict(LAST_STATUS or _build_status())
  status["requested_backend"] = bot.get_requested_control_backend()
  status["active_backend"] = bot.get_active_control_backend()
  status["adb_last_error"] = message
  if connected is not None:
    status["adb_connected"] = bool(connected)
  status["healthy"] = bool(status.get("adb_connected") and status.get("device_ready") and status.get("healthcheck_passed"))
  _publish_status(status)


def get_last_input_debug():
  return dict(LAST_INPUT_DEBUG or {})


def _resolve_adb_input_frame_size():
  display_info = (LAST_STATUS or {}).get("display_info") or {}
  screenshot_size = display_info.get("screenshot_size") or {}
  physical_size = (display_info.get("wm_size") or {}).get("physical_size") or {}

  width = int(screenshot_size.get("width") or physical_size.get("width") or 0)
  height = int(screenshot_size.get("height") or physical_size.get("height") or 0)
  source = "screenshot_size" if screenshot_size else "physical_size"

  if width <= 0 or height <= 0:
    return 0, 0, source, False

  game_left, game_top, game_right, game_bottom = constants.GAME_WINDOW_BBOX
  host_width = max(1, int(game_right - game_left))
  host_height = max(1, int(game_bottom - game_top))
  host_is_portrait = host_height >= host_width
  target_is_portrait = height >= width
  orientation_swapped = False

  if host_is_portrait != target_is_portrait:
    width, height = height, width
    orientation_swapped = True

  return width, height, source, orientation_swapped


def map_host_to_adb_target(x, y):
  game_left, game_top, game_right, game_bottom = constants.GAME_WINDOW_BBOX
  host_width = max(1, int(game_right - game_left))
  host_height = max(1, int(game_bottom - game_top))
  frame_width, frame_height, frame_source, orientation_swapped = _resolve_adb_input_frame_size()

  relative_x = (float(x) - float(game_left)) / float(host_width)
  relative_y = (float(y) - float(game_top)) / float(host_height)
  clamped_x = min(max(relative_x, 0.0), 1.0)
  clamped_y = min(max(relative_y, 0.0), 1.0)

  mapped_x = int(round(clamped_x * frame_width)) if frame_width > 0 else int(round(x))
  mapped_y = int(round(clamped_y * frame_height)) if frame_height > 0 else int(round(y))

  if frame_width > 0:
    mapped_x = min(max(mapped_x, 0), frame_width - 1)
  if frame_height > 0:
    mapped_y = min(max(mapped_y, 0), frame_height - 1)

  return {
    "host_target": [int(x), int(y)],
    "host_game_window_bbox": [int(game_left), int(game_top), int(game_right), int(game_bottom)],
    "host_game_window_size": [int(host_width), int(host_height)],
    "relative_target": [round(relative_x, 6), round(relative_y, 6)],
    "relative_target_clamped": [round(clamped_x, 6), round(clamped_y, 6)],
    "adb_frame_size": [int(frame_width), int(frame_height)],
    "adb_frame_source": frame_source,
    "orientation_swapped": orientation_swapped,
    "mapped_target": [int(mapped_x), int(mapped_y)],
  }


def map_host_swipe_to_adb_target(x1, y1, x2, y2):
  start_mapping = map_host_to_adb_target(x1, y1)
  end_mapping = map_host_to_adb_target(x2, y2)
  return {
    "start": start_mapping,
    "end": end_mapping,
    "mapped_start": list(start_mapping["mapped_target"]),
    "mapped_end": list(end_mapping["mapped_target"]),
    "adb_frame_size": list(start_mapping["adb_frame_size"]),
    "adb_frame_source": start_mapping["adb_frame_source"],
    "orientation_swapped": bool(
      start_mapping["orientation_swapped"] or end_mapping["orientation_swapped"]
    ),
  }


def init_adb():
  global device
  resolved_device_id = bot.device_id or DEFAULT_DEVICE_ID
  status = _build_status(resolved_device_id)
  device = None

  if adb is None:
    status["adb_last_error"] = "adbutils is not installed."
    debug("[ADB] adbutils not available.")
    return _publish_status(status)

  try:
    connect_result = adb.connect(resolved_device_id)
    status["connect_result"] = str(connect_result)
    status["adb_connected"] = True
    debug(f"[ADB] connect({resolved_device_id}) -> {connect_result}")
  except Exception as exc:
    status["adb_last_error"] = f"connect failed: {exc}"
    error(f"[ADB] Failed to connect to {resolved_device_id}: {exc}")
    return _publish_status(status)

  try:
    device = adb.device(resolved_device_id)
    status["device_ready"] = device is not None
  except Exception as exc:
    device = None
    status["adb_last_error"] = f"device acquisition failed: {exc}"
    error(f"[ADB] Failed to acquire device {resolved_device_id}: {exc}")
    return _publish_status(status)

  try:
    healthcheck_response = str(device.shell("echo codex_adb_ok")).strip()
    status["healthcheck_passed"] = "codex_adb_ok" in healthcheck_response
    status["healthcheck_response"] = healthcheck_response
  except Exception as exc:
    status["adb_last_error"] = f"healthcheck failed: {exc}"
    error(f"[ADB] Healthcheck failed for {resolved_device_id}: {exc}")
    return _publish_status(status)

  if not status["healthcheck_passed"]:
    status["adb_last_error"] = "healthcheck returned an unexpected response."
    error(f"[ADB] Healthcheck for {resolved_device_id} returned an unexpected response: {status.get('healthcheck_response')}")
    return _publish_status(status)

  status["display_info"] = _collect_device_display_info()
  status["healthy"] = True
  status["adb_last_error"] = ""
  info(f"[ADB] Ready on {resolved_device_id}. display={status.get('display_info')}")
  return _publish_status(status)

def click(x, y):
  global LAST_INPUT_DEBUG
  if device is None:
    _record_runtime_error("ADB click requested without an initialized device.", connected=False)
    return False
  try:
    mapping = map_host_to_adb_target(x, y)
    mapped_x, mapped_y = mapping["mapped_target"]
    LAST_INPUT_DEBUG = {
      "backend": "adb",
      "action": "tap",
      "target": [int(mapped_x), int(mapped_y)],
      "host_target": [int(x), int(y)],
      "device_id": bot.device_id or DEFAULT_DEVICE_ID,
      "display_info": (LAST_STATUS or {}).get("display_info") or _collect_device_display_info(),
      "mapping": mapping,
    }
    info(f"[INPUT][ADB] Tap host_target=({x}, {y}) mapped_target=({mapped_x}, {mapped_y}) diagnostics={LAST_INPUT_DEBUG}")
    device.click(mapped_x, mapped_y)
    return True
  except Exception as exc:
    _record_runtime_error(f"ADB click failed: {exc}")
    error(f"[INPUT][ADB] Click failed at ({x}, {y}): {exc}")
    return False

def swipe(x1, y1, x2, y2, duration=0.3):
  global LAST_INPUT_DEBUG
  if device is None:
    _record_runtime_error("ADB swipe requested without an initialized device.", connected=False)
    return False
  try:
    mapping = map_host_swipe_to_adb_target(x1, y1, x2, y2)
    mapped_x1, mapped_y1 = mapping["mapped_start"]
    mapped_x2, mapped_y2 = mapping["mapped_end"]
    LAST_INPUT_DEBUG = {
      "backend": "adb",
      "action": "swipe",
      "start": [int(mapped_x1), int(mapped_y1)],
      "end": [int(mapped_x2), int(mapped_y2)],
      "host_start": [int(x1), int(y1)],
      "host_end": [int(x2), int(y2)],
      "duration": float(duration),
      "device_id": bot.device_id or DEFAULT_DEVICE_ID,
      "display_info": (LAST_STATUS or {}).get("display_info") or _collect_device_display_info(),
      "mapping": mapping,
    }
    info(
      f"[INPUT][ADB] Swipe host_start=({x1}, {y1}) host_end=({x2}, {y2}) "
      f"mapped_start=({mapped_x1}, {mapped_y1}) mapped_end=({mapped_x2}, {mapped_y2}) "
      f"duration={duration} diagnostics={LAST_INPUT_DEBUG}"
    )
    device.swipe(mapped_x1, mapped_y1, mapped_x2, mapped_y2, duration)
    return True
  except Exception as exc:
    _record_runtime_error(f"ADB swipe failed: {exc}")
    error(f"[INPUT][ADB] Swipe failed from ({x1}, {y1}) to ({x2}, {y2}): {exc}")
    return False

def text(content):
  if device is None:
    _record_runtime_error("ADB text requested without an initialized device.", connected=False)
    return False
  try:
    device.send_keys(content)
    return True
  except Exception as exc:
    _record_runtime_error(f"ADB text failed: {exc}")
    error(f"[INPUT][ADB] Text entry failed: {exc}")
    return False

def enable_cursor_display():
  if device is None:
    return False
  try:
    device.shell("settings put system pointer_location 1")
    device.shell("settings put system show_touches 1")
    device.shell("settings put system show_screen_updates 1")
    return True
  except Exception:
    return False

def disable_cursor_display():
  if device is None:
    return False
  try:
    device.shell("settings put system pointer_location 0")
    device.shell("settings put system show_touches 0")
    device.shell("settings put system show_screen_updates 0")
    return True
  except Exception:
    return False

cached_screenshot = []
def screenshot(region_xywh: tuple[int, int, int, int] = None):
  global cached_screenshot
  if device is None:
    error(f"ADB device is None, this should not happen, check ADB connection and device ID, if problem persists, please report this error.")
    raise Exception("ADB device is None")
  if args.device_debug:
    debug(f"Screenshot region: {region_xywh}")

  if len(cached_screenshot) > 0:
    if args.device_debug:
      debug(f"Using cached screenshot")
    screenshot = cached_screenshot
  else:
    if args.device_debug:
      debug(f"Taking new screenshot")
    try:
      screenshot = np.array(device.screenshot(error_ok=False))
    except:
      screenshot = np.array(device.screenshot())
    cached_screenshot = screenshot
  if args.device_debug:
    debug(f"Screenshot shape: {screenshot.shape}")
  if screenshot.shape[0] == 800 and screenshot.shape[1] == 1080:
    # change region from portrait to landscape
    region_xywh = (0, 0, 1080, 800)
  if region_xywh:
    x, y, w, h = region_xywh
    screenshot = screenshot[y:y+h, x:x+w]
  if args.device_debug:
    debug(f"Screenshot shape: {screenshot.shape}")
    variable_name = name_of_variable(region_xywh)
    debug_window(screenshot, save_name="adb_screenshot")
  return screenshot
