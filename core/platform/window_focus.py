import platform
import subprocess
import textwrap
import time
from typing import Dict, Tuple

import pyautogui

try:
  import pygetwindow as gw
except Exception:  # pragma: no cover - optional on macOS
  gw = None

import core.config as config
import utils.constants as constants
from core.region_adjuster.shared import resolve_region_adjuster_profiles
from utils.log import info, error, debug
from utils.tools import sleep


def _coerce_int(value, default: int) -> int:
  try:
    return int(round(float(value)))
  except (TypeError, ValueError):
    return default


def _get_macos_display_resolution() -> Tuple[int, int, str]:
  script = textwrap.dedent(
    """
    tell application "Finder"
      set desktopBounds to bounds of window of desktop
      return (item 3 of desktopBounds as string) & "," & (item 4 of desktopBounds as string)
    end tell
    """
  )

  try:
    result = subprocess.run(
      ["osascript", "-e", script],
      capture_output=True,
      text=True,
      check=False,
    )
    if result.returncode == 0:
      output = (result.stdout or "").strip()
      width_text, height_text = [part.strip() for part in output.split(",", 1)]
      width = _coerce_int(width_text, 0)
      height = _coerce_int(height_text, 0)
      if width > 0 and height > 0:
        return width, height, "osascript"
  except Exception:
    pass

  resolution = pyautogui.resolution()
  return int(resolution.width), int(resolution.height), "pyautogui"


def _compute_display_scale(current_width: int, current_height: int, display_config: Dict) -> float:
  reference = display_config.get("reference_display") or {}
  reference_width = _coerce_int(reference.get("width"), 0)
  reference_height = _coerce_int(reference.get("height"), 0)
  if reference_width <= 0 or reference_height <= 0:
    warning_message = (
      "macOS display-aware bounds are enabled, but "
      "platform.mac_bluestacks_air.display_aware_bounds.reference_display is invalid."
    )
    error(warning_message)
    return 1.0

  scale_x = current_width / reference_width
  scale_y = current_height / reference_height
  scale_mode = str(display_config.get("scale_mode", "contain")).lower()

  if scale_mode == "width":
    scale = scale_x
  elif scale_mode == "height":
    scale = scale_y
  else:
    scale = min(scale_x, scale_y)

  min_scale = display_config.get("min_scale")
  max_scale = display_config.get("max_scale")
  if min_scale is not None:
    scale = max(float(min_scale), scale)
  if max_scale is not None:
    scale = min(float(max_scale), scale)

  return scale


def _resolve_display_aware_mac_settings(
  settings: Dict,
  bounds: Dict,
  offset_x: int,
  offset_y: int,
  recognition_offset_x: int,
  recognition_offset_y: int,
) -> Tuple[Dict, int, int, int, int, float]:
  display_config = settings.get("display_aware_bounds") or {}
  if not display_config.get("enabled"):
    return bounds, offset_x, offset_y, recognition_offset_x, recognition_offset_y, 1.0

  current_width, current_height, source = _get_macos_display_resolution()
  scale = _compute_display_scale(current_width, current_height, display_config)
  scale_regions = bool(display_config.get("scale_regions", False))

  scaled_bounds = dict(bounds)
  if display_config.get("scale_bounds", True):
    scaled_bounds = {
      key: _coerce_int(bounds.get(key), 0 if key in ("x", "y") else 1)
      for key in ("x", "y", "width", "height")
    }
    for key in ("x", "y", "width", "height"):
      scaled_bounds[key] = max(
        1 if key in ("width", "height") else 0,
        int(round(scaled_bounds[key] * scale)),
      )

  if display_config.get("scale_general_offsets", True) and not scale_regions:
    offset_x = int(round(offset_x * scale))
    offset_y = int(round(offset_y * scale))

  if display_config.get("scale_recognition_offsets", True) and not scale_regions:
    recognition_offset_x = int(round(recognition_offset_x * scale))
    recognition_offset_y = int(round(recognition_offset_y * scale))

  reference = display_config.get("reference_display") or {}
  info(
    "macOS display-aware bounds: "
    f"display={current_width}x{current_height} via {source}, "
    f"reference={_coerce_int(reference.get('width'), 0)}x{_coerce_int(reference.get('height'), 0)}, "
    f"scale={scale:.4f}, bounds={scaled_bounds}, offsets=({offset_x}, {offset_y}), "
    f"recognition_offsets=({recognition_offset_x}, {recognition_offset_y})"
  )

  return scaled_bounds, offset_x, offset_y, recognition_offset_x, recognition_offset_y, scale


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

  configured_process_name = settings.get("process_name") or "BlueStacks"
  configured_window_name = settings.get("window_name") or config.WINDOW_NAME
  set_bounds = settings.get("set_bounds", True)
  bounds = settings.get("bounds", {"x": 0, "y": 0, "width": 640, "height": 1113})
  # Give the window a moment to settle after focus before resizing.
  bounds_delay = float(settings.get("bounds_delay", 0.2))
  # Ignore overlay/helper windows when choosing which window to resize.
  window_excludes = settings.get("window_excludes") or ["Keymap Overlay"]
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

  bounds, offset_x, offset_y, recognition_offset_x, recognition_offset_y, display_scale = _resolve_display_aware_mac_settings(
    settings=settings,
    bounds=bounds,
    offset_x=_coerce_int(offset_x, 0),
    offset_y=_coerce_int(offset_y, 0),
    recognition_offset_x=_coerce_int(recognition_offset_x, 0),
    recognition_offset_y=_coerce_int(recognition_offset_y, 0),
  )

  if isinstance(configured_process_name, (list, tuple)):
    process_candidates = [name for name in configured_process_name if name]
  else:
    process_candidates = [configured_process_name]

  if config.WINDOW_NAME and config.WINDOW_NAME not in process_candidates:
    process_candidates.append(config.WINDOW_NAME)

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

  debug(f"macOS: focusing BlueStacks Air (set_bounds={set_bounds}, bounds={bounds}).")

  for process_name in process_candidates:
    script = _build_mac_bluestacks_script(
      process_name=process_name,
      set_bounds=set_bounds,
      bounds=bounds,
      bounds_delay=bounds_delay,
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
  if set_bounds:
    _apply_mac_bluestacks_bounds(
      active_process_name,
      bounds,
      configured_window_name,
      window_excludes,
    )

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
  _, _, overrides_path = resolve_region_adjuster_profiles(overrides_config)
  if constants.apply_region_overrides(overrides_path=overrides_path):
    debug("Applied region overrides from adjuster settings.")

  display_config = settings.get("display_aware_bounds") or {}
  if display_config.get("enabled") and display_config.get("scale_regions", False):
    constants.scale_coordinate_constants(display_scale)
    debug(f"Applied display-aware coordinate scaling factor {display_scale:.4f}.")

  return True


def _apply_mac_bluestacks_bounds(
  process_name: str,
  bounds: dict,
  window_name: str,
  window_excludes: list,
) -> None:
  """Resize the main BlueStacks window while skipping overlay helpers."""
  x = bounds.get("x", 0)
  y = bounds.get("y", 0)
  width = bounds.get("width", 640)
  height = bounds.get("height", 1113)
  right = x + width
  bottom = y + height
  window_hint = (window_name or "").replace('"', r'\"')
  excludes_list = window_excludes if isinstance(window_excludes, (list, tuple)) else []
  escaped_excludes = [str(item).replace('"', r'\"') for item in excludes_list]
  excludes_literal = ", ".join(f'"{item}"' for item in escaped_excludes)

  # Prefer an exact window-name match, then a contains-match, then the largest
  # non-excluded window. This avoids resizing the keymap overlay window.
  script = textwrap.dedent(
    "\n".join(
      [
        'tell application "System Events"',
        f'  if exists (process "{process_name}") then',
        f'    tell process "{process_name}"',
        "      set windowCount to count of windows",
        "      if windowCount > 0 then",
        "        set targetWindow to missing value",
        f'        set targetName to "{window_hint}"',
        f"        set excludes to {{{excludes_literal}}}",
        "        if targetName is not \"\" then",
        "          repeat with i from 1 to windowCount",
        "            set win to window i",
        "            set isExcluded to false",
        "            try",
        "              set winName to name of win as text",
        "            on error",
        '              set winName to ""',
        "            end try",
        "            repeat with ex in excludes",
        "              if winName contains ex then set isExcluded to true",
        "            end repeat",
        "            if isExcluded is false and winName is targetName then",
        "              set targetWindow to win",
        "              exit repeat",
        "            end if",
        "          end repeat",
        "        end if",
        "        if targetWindow is missing value and targetName is not \"\" then",
        "          repeat with i from 1 to windowCount",
        "            set win to window i",
        "            set isExcluded to false",
        "            try",
        "              set winName to name of win as text",
        "            on error",
        '              set winName to ""',
        "            end try",
        "            repeat with ex in excludes",
        "              if winName contains ex then set isExcluded to true",
        "            end repeat",
        "            if isExcluded is false and winName contains targetName then",
        "              set targetWindow to win",
        "              exit repeat",
        "            end if",
        "          end repeat",
        "        end if",
        "        if targetWindow is missing value then",
        "          set bestArea to -1",
        "          repeat with i from 1 to windowCount",
        "            set win to window i",
        "            set isExcluded to false",
        "            try",
        "              set winName to name of win as text",
        "            on error",
        '              set winName to ""',
        "            end try",
        "            repeat with ex in excludes",
        "              if winName contains ex then set isExcluded to true",
        "            end repeat",
        "            if isExcluded is false then",
        "              try",
        "                set winSize to size of win",
        "              on error",
        "                set winSize to {0, 0}",
        "              end try",
        "              set area to (item 1 of winSize) * (item 2 of winSize)",
        "              if area > bestArea then",
        "                set bestArea to area",
        "                set targetWindow to win",
        "              end if",
        "            end if",
        "          end repeat",
        "        end if",
        "        if targetWindow is missing value then",
        "          set targetWindow to window 1",
        "        end if",
        f'        set targetPos to {{{x}, {y}}}',
        f'        set targetSize to {{{width}, {height}}}',
        "        set position of targetWindow to targetPos",
        "        set size of targetWindow to targetSize",
        "        delay 0.1",
        "        set actualSize to size of targetWindow",
        "        if actualSize is not targetSize then",
        f'          set bounds of targetWindow to {{{x}, {y}, {right}, {bottom}}}',
        "          delay 0.1",
        "        end if",
        "        set actualPos to position of targetWindow",
        "        set actualSize to size of targetWindow",
        "        try",
        "          set actualName to name of targetWindow as text",
        "        on error",
        '          set actualName to ""',
        "        end try",
        "        return actualName & \"|\" & (item 1 of actualPos as string) & \",\" & (item 2 of actualPos as string) & \"|\" & (item 1 of actualSize as string) & \",\" & (item 2 of actualSize as string)",
        "      end if",
        "    end tell",
        "  end if",
        "end tell",
        "return \"no window\"",
      ]
    )
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
    return

  if result.returncode != 0:
    failure_output = result.stderr.strip() or result.stdout.strip() or "Unknown error"
    debug(f"macOS: Failed to resize BlueStacks window: {failure_output}")
    return

  output = (result.stdout or "").strip()
  if output:
    debug(f"macOS: Resize result: {output}")


def _build_mac_bluestacks_script(process_name, set_bounds, bounds, bounds_delay) -> str:
  script_lines = [
    'tell application "System Events"',
    f'  if exists (process "{process_name}") then',
    f'    tell process "{process_name}"',
    "      set frontmost to true",
    f"      delay {bounds_delay}",
    "      if (count of windows) > 0 then",
  ]

  if set_bounds:
    script_lines.extend(
      [
        f'        set position of window 1 to {{{bounds.get("x", 0)}, {bounds.get("y", 0)}}}',
        f'        set size of window 1 to {{{bounds.get("width", 640)}, {bounds.get("height", 1113)}}}',
      ]
    )

  script_lines.extend(
    [
      "        return 0",
      "      else",
      "        error \"No windows found.\"",
      "      end if",
      "    end tell",
      "  else",
      f'    error "Process {process_name} is not running."',
      "  end if",
      "end tell",
      "return 1",
    ]
  )

  return textwrap.dedent("\n".join(script_lines))
