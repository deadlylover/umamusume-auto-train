import threading
import traceback
import time

import pyautogui
import uvicorn

from utils.log import info, warning, error, debug

from core.execute import career_lobby
from core.hotkeys import HotkeyListener
from core.platform.window_focus import focus_target_window
from core.region_adjuster import run_region_adjuster_session
import core.state as state
from server.main import app
from update_config import update_config

hotkey = "f1"
capture_hotkey = "f2"
support_hotkey = "f3"
event_hotkey = "f4"
recreation_hotkey = "f5"
region_adjuster_hotkey = "f6"

def main():
  print("Uma Auto!")
  try:
    state.reload_config()
    state.stop_event.clear()

    if focus_target_window():
      info(f"Config: {state.CONFIG_NAME}")
      career_lobby()
    else:
      error("Failed to focus Umamusume window")
  except Exception as e:
    error_message = traceback.format_exc()
    error(f"Error in main thread: {error_message}")
  finally:
    debug("[BOT] Stopped.")

def toggle_bot():
  with state.bot_lock:
    if state.is_bot_running:
      debug("[BOT] Stopping...")
      state.stop_event.set()
      state.is_bot_running = False

      if state.bot_thread and state.bot_thread.is_alive():
        debug("[BOT] Waiting for bot to stop...")
        state.bot_thread.join(timeout=3)

        if state.bot_thread.is_alive():
          debug("[BOT] Bot still running, please wait...")
        else:
          debug("[BOT] Bot stopped completely")

      state.bot_thread = None
    else:
      debug("[BOT] Starting...")
      state.is_bot_running = True
      state.bot_thread = threading.Thread(target=main, daemon=True)
      state.bot_thread.start()

def trigger_debug_capture():
  debug(f"Hotkey '{capture_hotkey}' pressed; capturing OCR regions for calibration.")

  def _capture():
    state.reload_config()
    debug("Reloaded config for debug capture.")
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
    state.reload_config()
    debug("Reloaded config for support capture.")
    path = state.debug_capture_support_region()
    info(f"[DEBUG] Support-region capture saved to {path}")

  threading.Thread(target=_capture_support, daemon=True).start()


def trigger_event_capture():
  debug(f"Hotkey '{event_hotkey}' pressed; capturing event OCR region.")

  def _capture_event():
    state.reload_config()
    debug("Reloaded config for event capture.")
    path = state.debug_capture_event_region()
    info(f"[DEBUG] Event-region capture saved to {path}")

  threading.Thread(target=_capture_event, daemon=True).start()


def trigger_recreation_capture():
  debug(f"Hotkey '{recreation_hotkey}' pressed; capturing recreation OCR region.")

  def _capture_recreation():
    state.reload_config()
    debug("Reloaded config for recreation capture.")
    path = state.debug_capture_recreation_region()
    info(f"[DEBUG] Recreation-region capture saved to {path}")

  threading.Thread(target=_capture_recreation, daemon=True).start()


def trigger_region_adjuster():
  debug(f"Hotkey '{region_adjuster_hotkey}' pressed; opening OCR region adjuster.")

  if not state.REGION_ADJUSTER_CONFIG.get("enabled"):
    warning("Region adjuster is disabled in config. Enable debug.region_adjuster.enabled to use it.")
    return

  def _open_adjuster():
    settings = dict(state.REGION_ADJUSTER_CONFIG)
    success = run_region_adjuster_session(settings)
    if success:
      debug("Region adjuster reported success; reloading config to apply overrides.")
      state.reload_config()

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
  info(
    f"Press '{hotkey}' to start/stop the bot. "
    f"Press '{capture_hotkey}' for year/stat OCR snapshots. "
    f"Press '{support_hotkey}' for support-region capture. "
    f"Press '{event_hotkey}' for event-region capture. "
    f"Press '{recreation_hotkey}' for recreation-region capture. "
    f"Press '{region_adjuster_hotkey}' to open the OCR region adjuster."
  )
  print(f"[SERVER] Open http://{host}:{port} to configure the bot.")
  config = uvicorn.Config(app, host=host, port=port, workers=1, log_level="warning")
  server = uvicorn.Server(config)
  server.run()

if __name__ == "__main__":
  update_config()
  state.reload_config()
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
