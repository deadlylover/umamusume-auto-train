# main.py - Umamusume Auto Train Entry Point
# macOS-adapted fork with Unity Cup scenario support

import threading
import traceback
import time
import platform

import pyautogui
import uvicorn

from utils.log import info, warning, error, debug, init_logging

from core.skeleton import career_lobby
from core.hotkeys import HotkeyListener
from core.platform.window_focus import focus_target_window
from core.region_adjuster import run_region_adjuster_session
import core.config as config
import core.bot as bot
import utils.constants as constants
from server.main import app
from update_config import update_config

SYSTEM = platform.system().lower()

hotkey = "f1"
capture_hotkey = "f2"
support_hotkey = "f3"
event_hotkey = "f4"
recreation_hotkey = "f5"
region_adjuster_hotkey = "f6"

def main():
  print("Uma Auto!")
  try:
    config.reload_config()
    bot.stop_event.clear()

    if focus_target_window():
      info(f"Config: {config.CONFIG_NAME}")
      career_lobby()
    else:
      error("Failed to focus Umamusume window")
  except Exception as e:
    error_message = traceback.format_exc()
    error(f"Error in main thread: {error_message}")
  finally:
    debug("[BOT] Stopped.")

def toggle_bot():
  with bot.bot_lock:
    if bot.is_bot_running:
      debug("[BOT] Stopping...")
      bot.stop_event.set()
      bot.is_bot_running = False

      if bot.bot_thread and bot.bot_thread.is_alive():
        debug("[BOT] Waiting for bot to stop...")
        bot.bot_thread.join(timeout=3)

        if bot.bot_thread.is_alive():
          debug("[BOT] Bot still running, please wait...")
        else:
          debug("[BOT] Bot stopped completely")

      bot.bot_thread = None
    else:
      debug("[BOT] Starting...")
      bot.is_bot_running = True
      bot.bot_thread = threading.Thread(target=main, daemon=True)
      bot.bot_thread.start()

def trigger_debug_capture():
  debug(f"Hotkey '{capture_hotkey}' pressed; capturing OCR regions for calibration.")

  def _capture():
    config.reload_config()
    debug("Reloaded config for debug capture.")
    # Import state functions dynamically to avoid circular imports
    from core import state
    active_offset = getattr(state, "ACTIVE_RECOGNITION_OFFSET", (0, 0))
    info(f"[DEBUG] Recognition offset in effect: x={active_offset[0]}, y={active_offset[1]}")
    year_path = state.debug_capture_year_region()
    info(f"[DEBUG] Year-region capture saved to {year_path}")
    stat_paths = state.debug_capture_stat_regions()
    for stat, path in stat_paths.items():
      info(f"[DEBUG] {stat.upper()} stat capture saved to {path}")
    try:
      start_time = time.perf_counter()
      stats = state.stat_state()
      elapsed_ms = (time.perf_counter() - start_time) * 1000
      info(f"[DEBUG] Stat OCR took {elapsed_ms:.1f} ms.")
      info(f"Current stats: {stats}")
    except Exception as exc:
      error(f"[DEBUG] Failed to read stats during capture: {exc}")

  threading.Thread(target=_capture, daemon=True).start()


def trigger_support_capture():
  debug(f"Hotkey '{support_hotkey}' pressed; capturing support OCR region.")

  def _capture_support():
    config.reload_config()
    debug("Reloaded config for support capture.")
    from core import state
    path = state.debug_capture_support_region()
    info(f"[DEBUG] Support-region capture saved to {path}")

  threading.Thread(target=_capture_support, daemon=True).start()


def trigger_event_capture():
  debug(f"Hotkey '{event_hotkey}' pressed; capturing event OCR region.")

  def _capture_event():
    config.reload_config()
    debug("Reloaded config for event capture.")
    from core import state
    path = state.debug_capture_event_region()
    info(f"[DEBUG] Event-region capture saved to {path}")

  threading.Thread(target=_capture_event, daemon=True).start()


def trigger_recreation_capture():
  debug(f"Hotkey '{recreation_hotkey}' pressed; capturing recreation OCR region.")

  def _capture_recreation():
    config.reload_config()
    debug("Reloaded config for recreation capture.")
    from core import state
    path = state.debug_capture_recreation_region()
    info(f"[DEBUG] Recreation-region capture saved to {path}")

  threading.Thread(target=_capture_recreation, daemon=True).start()


def trigger_region_adjuster():
  debug(f"Hotkey '{region_adjuster_hotkey}' pressed; opening OCR region adjuster.")

  def _open_adjuster():
    config.reload_config()
    debug("Reloaded config before launching region adjuster.")
    focus_target_window()
    settings = dict(config.REGION_ADJUSTER_CONFIG)
    settings["enabled"] = True
    success = run_region_adjuster_session(settings)
    if success:
      debug("Region adjuster reported success; reloading config to apply overrides.")
      config.reload_config()
      constants.apply_region_overrides(
        overrides_path=settings.get("overrides_path"),
        force=True,
      )

  threading.Thread(target=_open_adjuster, daemon=True).start()

def start_server():
  res = pyautogui.resolution()
  if res.width != 1920 or res.height != 1080:
    warning_msg = (
      f"Detected desktop resolution {res.width} x {res.height}. "
      "The bot is tuned for a 1920 x 1080 BlueStacks Air viewport; "
      "ensure the streaming window is sized accordingly."
    )
    warning(warning_msg)
  
  host = "127.0.0.1"
  port = 8000
  
  init_logging()
  
  info(
    f"Press '{hotkey}' to start/stop the bot. "
    f"Press '{capture_hotkey}' for year/stat OCR snapshots. "
    f"Press '{support_hotkey}' for support-region capture. "
    f"Press '{event_hotkey}' for event-region capture. "
    f"Press '{recreation_hotkey}' for recreation-region capture. "
    f"Press '{region_adjuster_hotkey}' to open the OCR region adjuster."
  )
  print(f"[SERVER] Open http://{host}:{port} to configure the bot.")
  
  server_config = uvicorn.Config(app, host=host, port=port, workers=1, log_level="warning")
  server = uvicorn.Server(server_config)
  server.run()

if __name__ == "__main__":
  update_config()
  config.reload_config(print_config=False)
  
  listener = HotkeyListener(
    hotkey,
    toggle_bot,
    extra_hotkeys={
      capture_hotkey: trigger_debug_capture,
      support_hotkey: trigger_support_capture,
      event_hotkey: trigger_event_capture,
      recreation_hotkey: trigger_recreation_capture,
      region_adjuster_hotkey: trigger_region_adjuster,
    },
  )
  listener.start()
  start_server()
