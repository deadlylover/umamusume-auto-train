import platform
import subprocess
import textwrap
import time

import pyautogui

try:
  import pygetwindow as gw
except Exception:  # pragma: no cover - optional on macOS
  gw = None

import core.config as config
import utils.constants as constants
from utils.log import info, error, debug
from utils.tools import sleep


def focus_target_window() -> bool:
  """Focus the game window based on the configured platform profile."""

  profile = getattr(config, "PLATFORM_PROFILE", "auto")
  system = platform.system().lower()

  if profile == "mac_bluestacks_air":
    return _focus_mac_bluestacks_air()

  if profile == "auto" and system == "darwin":
    # Assume Bluestacks Air when running on macOS and no explicit profile.
    return _focus_mac_bluestacks_air()

  # Default to the original Windows/Steam behaviour.
  return _focus_default_windows()


def _focus_default_windows() -> bool:
  if gw is None:
    error("pygetwindow is not available; cannot control emulator window.")
    return False

  try:
    windows = gw.getWindowsWithTitle("Umamusume")
    target_window = next((w for w in windows if w.title.strip() == "Umamusume"), None)

    if target_window:
      _bring_window_to_front(target_window)
      return True

    return _focus_alternate_windows_window()
  except Exception as exc:  # pragma: no cover - defensive
    error(f"Error focusing window: {exc}")
    return False


def _focus_alternate_windows_window() -> bool:
  if not config.WINDOW_NAME:
    error("Window name cannot be empty! Please set window name in the config.")
    return False

  if gw is None:
    error("pygetwindow is not available; cannot control emulator window.")
    return False

  info(f"Couldn't get the steam version window, trying {config.WINDOW_NAME}.")
  win = gw.getWindowsWithTitle(config.WINDOW_NAME)
  target_window = next((w for w in win if w.title.strip() == config.WINDOW_NAME), None)
  if not target_window:
    error(f"Couldn't find target window named \"{config.WINDOW_NAME}\". Please double check your window name config.")
    return False

  constants.adjust_constants_offsets(x_offset=405)
  _bring_window_to_front(target_window)
  pyautogui.press("esc")
  pyautogui.press("f11")
  time.sleep(5)
  close_btn = pyautogui.locateCenterOnScreen("assets/buttons/bluestacks/close_btn.png", confidence=0.8, minSearchTime=2)
  if close_btn:
    pyautogui.click(close_btn)
  return True


def _bring_window_to_front(window) -> None:
  if getattr(window, "isMinimized", False):
    window.restore()
  else:
    window.minimize()
    sleep(0.2)
    window.restore()
    sleep(0.5)


def _focus_mac_bluestacks_air() -> bool:
  settings = getattr(config, "MAC_AIR_SETTINGS", {}) or {}

  configured_process_name = settings.get("process_name") or config.WINDOW_NAME or "BlueStacksX"
  configured_window_name = settings.get("window_name")
  set_bounds = settings.get("set_bounds", True)
  bounds = settings.get("bounds", {"x": 0, "y": 0, "width": 659, "height": 1113})
  post_focus_delay = float(settings.get("post_focus_delay", 2.0))
  apply_offset_x = settings.get("apply_offset_x")
  offset_x = settings.get("offset_x")
  apply_offset_y = settings.get("apply_offset_y")
  offset_y = settings.get("offset_y")
  apply_recognition_offset = settings.get("apply_recognition_offset", False)
  recognition_offset_x = settings.get("recognition_offset_x", 0)
  recognition_offset_y = settings.get("recognition_offset_y", 0)

  legacy_apply = settings.get("apply_constant_offset")
  legacy_offset = settings.get("constant_offset")

  if apply_offset_x is None:
    apply_offset_x = legacy_apply if legacy_apply is not None else True
  if offset_x is None:
    offset_x = legacy_offset if legacy_offset is not None else 405
  if apply_offset_y is None:
    apply_offset_y = False
  if offset_y is None:
    offset_y = 0

  if isinstance(configured_process_name, (list, tuple)):
    process_candidates = [name for name in configured_process_name if name]
  else:
    process_candidates = [configured_process_name]

  for fallback_name in ("BlueStacksX", "BlueStacks"):
    if fallback_name not in process_candidates:
      process_candidates.append(fallback_name)

  # Preserve order, but guard against accidental duplicates in config.
  seen = set()
  deduped_candidates = []
  for name in process_candidates:
    if name not in seen:
      deduped_candidates.append(name)
      seen.add(name)
  process_candidates = deduped_candidates

  last_failure_output = ""
  active_process_name = None

  for process_name in process_candidates:
    window_name = configured_window_name or process_name

    script = _build_mac_bluestacks_script(
      process_name=process_name,
      window_name=window_name,
      set_bounds=set_bounds,
      bounds=bounds,
    )

    try:
      result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        check=False,
      )
    except FileNotFoundError:
      error("osascript command is not available on this system.")
      return False

    if result.returncode == 0:
      active_process_name = process_name
      break

    last_failure_output = result.stderr.strip() or result.stdout.strip() or "Unknown error"
    debug(f'macOS: Failed to control process "{process_name}": {last_failure_output}')

  if active_process_name is None:
    error(f"Failed to control BlueStacks Air window: {last_failure_output}")
    return False

  debug(f'macOS: Focused BlueStacks Air window via AppleScript (process "{active_process_name}").')
  time.sleep(post_focus_delay)

  x_shift = offset_x if apply_offset_x else 0
  y_shift = offset_y if apply_offset_y else 0
  if x_shift or y_shift:
    constants.adjust_constants_offsets(x_shift, y_shift)
    debug(f"Applied general offsets x={x_shift}, y={y_shift}.")

  if apply_recognition_offset:
    constants.apply_recognition_offsets(recognition_offset_x, recognition_offset_y)
    debug(
      f"Applied recognition offsets x={recognition_offset_x}, y={recognition_offset_y} to OCR regions."
    )

  overrides_config = getattr(config, "REGION_ADJUSTER_CONFIG", {}) or {}
  overrides_path = overrides_config.get("overrides_path")
  if constants.apply_region_overrides(overrides_path=overrides_path):
    debug("Applied region overrides from adjuster settings.")

  return True


def _build_mac_bluestacks_script(process_name, window_name, set_bounds, bounds) -> str:
  script_lines = [
    'tell application "System Events"',
    f'  if exists (process "{process_name}") then',
    f'    tell process "{process_name}"',
    "      set frontmost to true",
  ]

  escaped_window_name = window_name.replace('"', r'\"')
  script_lines.append(f'      if (count of (windows whose title contains "{escaped_window_name}")) is 0 then')
  script_lines.append(f'        error "Window {escaped_window_name} was not found."')
  script_lines.append("      end if")
  script_lines.append(f'      set targetWindow to (first window whose title contains "{escaped_window_name}")')

  if set_bounds:
    script_lines.extend(
      [
        "      tell targetWindow",
        f'        set size to {{{bounds.get("width", 659)}, {bounds.get("height", 1113)}}}',
        f'        set position to {{{bounds.get("x", 0)}, {bounds.get("y", 0)}}}',
        "      end tell",
      ]
    )

  script_lines.extend(
    [
      "    end tell",
      "  else",
      f'    error "Process {process_name} is not running."',
      "  end if",
      "end tell",
    ]
  )

  script = textwrap.dedent("\n".join(script_lines))
  return script
