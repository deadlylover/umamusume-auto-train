# main.py - Umamusume Auto Train Entry Point
# macOS-adapted fork with Unity Cup scenario support

import threading
import traceback
import time
import platform

import pyautogui
import uvicorn

from utils.log import info, warning, error, debug, init_logging, args

from core.skeleton import career_lobby
from core.hotkeys import HotkeyListener
from core.operator_console import ensure_operator_console, publish_runtime_state
from core.platform.window_focus import focus_target_window
from core.region_adjuster import run_region_adjuster_session
from core.region_adjuster.shared import resolve_region_adjuster_profiles
import core.config as config
import core.bot as bot
import utils.constants as constants
import utils.adb_actions as adb_actions
from server.main import app
from update_config import update_config

SYSTEM = platform.system().lower()

hotkey = "f1"
capture_hotkey = "f2"
support_hotkey = "f3"
event_hotkey = "f4"
recreation_hotkey = "f5"
region_adjuster_hotkey = "f6"


def _is_mac_bluestacks_profile():
  profile = getattr(config, "PLATFORM_PROFILE", "auto")
  return profile == "mac_bluestacks_air" or (profile == "auto" and SYSTEM == "darwin")


def _resolve_requested_backend():
  if args.use_adb:
    return bot.CONTROL_BACKEND_ADB, "CLI --use-adb override."
  if config.USE_ADB:
    return bot.CONTROL_BACKEND_ADB, "config.use_adb=true."
  if _is_mac_bluestacks_profile() and str(getattr(config, "PREFERRED_CONTROL_BACKEND", "adb")).lower() == bot.CONTROL_BACKEND_ADB:
    return bot.CONTROL_BACKEND_ADB, "macOS BlueStacks Air preferred_control_backend=adb."
  return bot.CONTROL_BACKEND_HOST_INPUT, "Host input requested by configuration."


def resolve_control_backend():
  requested_backend, resolution_reason = _resolve_requested_backend()
  resolved_device_id = args.use_adb or config.DEVICE_ID or adb_actions.DEFAULT_DEVICE_ID
  fallback_allowed = bool(getattr(config, "ALLOW_HOST_INPUT_FALLBACK", False))

  bot.set_control_backend_state(
    requested_backend=requested_backend,
    active_backend=bot.CONTROL_BACKEND_HOST_INPUT,
    screenshot_backend_name=bot.CONTROL_BACKEND_HOST_INPUT,
    fallback_allowed=fallback_allowed,
    resolved_device_id=resolved_device_id,
    resolution_reason=resolution_reason,
  )
  bot.update_adb_status(
    {
      "requested_backend": requested_backend,
      "active_backend": bot.CONTROL_BACKEND_HOST_INPUT,
      "device_id": resolved_device_id,
      "adb_available": adb_actions.adb is not None,
      "adb_connected": False,
      "device_ready": False,
      "healthcheck_passed": False,
      "healthy": False,
      "adb_last_error": "",
    }
  )

  info(
    f"[BACKEND] requested={requested_backend} screenshot_backend={bot.CONTROL_BACKEND_HOST_INPUT} "
    f"device_id={resolved_device_id} fallback_allowed={fallback_allowed} reason={resolution_reason}"
  )

  if requested_backend != bot.CONTROL_BACKEND_ADB:
    bot.set_control_backend_state(active_backend=bot.CONTROL_BACKEND_HOST_INPUT)
    return

  adb_status = adb_actions.init_adb()
  adb_ok = bool(adb_status.get("healthy"))
  if adb_ok:
    bot.set_control_backend_state(active_backend=bot.CONTROL_BACKEND_ADB)
    bot.update_adb_status({**adb_status, "active_backend": bot.CONTROL_BACKEND_ADB})
    info(f"[BACKEND] Active input backend: {bot.CONTROL_BACKEND_ADB} ({resolved_device_id})")
    return

  error(f"[ADB] Initialization failed for {resolved_device_id}: {adb_status.get('adb_last_error') or 'unknown error'}")
  if fallback_allowed:
    warning("[BACKEND] Falling back to host_input because allow_host_input_fallback=true.")
    bot.set_control_backend_state(active_backend=bot.CONTROL_BACKEND_HOST_INPUT)
    bot.update_adb_status({**adb_status, "active_backend": bot.CONTROL_BACKEND_HOST_INPUT})
    return

  bot.set_control_backend_state(active_backend=bot.CONTROL_BACKEND_HOST_INPUT)
  bot.update_adb_status({**adb_status, "active_backend": bot.CONTROL_BACKEND_HOST_INPUT})
  raise RuntimeError(
    f"ADB was requested for device '{resolved_device_id}' but initialization failed: "
    f"{adb_status.get('adb_last_error') or 'unknown error'}"
  )

def main():
  print("Uma Auto!")
  try:
    config.reload_config()
    bot.stop_event.clear()
    resolve_control_backend()
    bot.set_phase("focusing_window", message="Focusing emulator window.")
    if config.EXECUTION_MODE == "semi_auto":
      ensure_operator_console()
      publish_runtime_state()

    if focus_target_window():
      info(f"Config: {config.CONFIG_NAME}")
      bot.set_phase("scanning_lobby", message="Waiting in career lobby.")
      publish_runtime_state()
      career_lobby()
    else:
      bot.set_phase("recovering", status="error", error="Failed to focus Umamusume window.")
      publish_runtime_state()
      error("Failed to focus Umamusume window")
  except Exception as e:
    error_message = traceback.format_exc()
    bot.set_phase("recovering", status="error", error=str(e))
    publish_runtime_state()
    error(f"Error in main thread: {error_message}")
  finally:
    bot.cancel_review_wait()
    bot.set_phase("idle", status="idle", message="Bot stopped.")
    publish_runtime_state()
    debug("[BOT] Stopped.")

def toggle_bot():
  with bot.bot_lock:
    if bot.is_bot_running:
      debug("[BOT] Stopping...")
      bot.stop_event.set()
      bot.is_bot_running = False
      bot.clear_pause_request()

      if bot.bot_thread and bot.bot_thread.is_alive():
        debug("[BOT] Waiting for bot to stop...")
        bot.cancel_review_wait()
        bot.bot_thread.join(timeout=3)

        if bot.bot_thread.is_alive():
          debug("[BOT] Bot still running, please wait...")
        else:
          debug("[BOT] Bot stopped completely")

      bot.bot_thread = None
    else:
      debug("[BOT] Starting...")
      bot.clear_pause_request()
      bot.is_bot_running = True
      bot.bot_thread = threading.Thread(target=main, daemon=True)
      bot.bot_thread.start()
  publish_runtime_state()


def _manual_console_snapshot():
  runtime_state = bot.get_runtime_state()
  snapshot = runtime_state.get("snapshot")
  if isinstance(snapshot, dict) and snapshot:
    snapshot = dict(snapshot)
  else:
    snapshot = {
      "scenario_name": constants.SCENARIO_NAME or "trackblazer",
      "turn_label": "",
      "energy_label": "",
      "sub_phase": "manual_console_check",
      "execution_intent": bot.get_execution_intent(),
      "state_summary": {},
      "selected_action": {},
      "available_actions": [],
      "ranked_trainings": [],
      "trackblazer_inventory": None,
      "reasoning_notes": "",
      "min_scores": None,
      "backend_state": bot.get_backend_state(),
      "ocr_debug": [],
      "planned_clicks": [],
    }
  snapshot["state_summary"] = dict(snapshot.get("state_summary") or {})
  snapshot["selected_action"] = dict(snapshot.get("selected_action") or {})
  snapshot["backend_state"] = bot.get_backend_state()
  snapshot["execution_intent"] = bot.get_execution_intent()
  return snapshot


def _start_manual_console_check(name, worker):
  if bot.is_bot_running:
    bot.set_phase(
      "recovering",
      status="error",
      message=f"Skipped {name}.",
      error="Stop the bot before running a manual operator-console check.",
    )
    publish_runtime_state()
    return
  threading.Thread(target=worker, daemon=True).start()


def trigger_manual_inventory_check():
  def _run():
    bot.set_phase("checking_inventory", message="Running manual Trackblazer inventory check.")
    publish_runtime_state()
    snapshot = _manual_console_snapshot()
    snapshot["scenario_name"] = "trackblazer"
    snapshot["sub_phase"] = "manual_inventory_check"
    snapshot["selected_action"] = {"func": "check_inventory"}
    snapshot["reasoning_notes"] = "Manual Trackblazer inventory check triggered from the operator console."
    try:
      config.reload_config(print_config=False)
      resolve_control_backend()
      focus_target_window()
      bot.set_manual_control_active(True)

      from core.state import collect_trackblazer_inventory

      state_obj = collect_trackblazer_inventory({}, allow_open_non_execute=True, trigger="manual_console")
      snapshot["trackblazer_inventory"] = state_obj.get("trackblazer_inventory")
      snapshot["state_summary"]["trackblazer_inventory_summary"] = state_obj.get("trackblazer_inventory_summary")
      snapshot["state_summary"]["trackblazer_inventory_controls"] = state_obj.get("trackblazer_inventory_controls")
      snapshot["state_summary"]["trackblazer_inventory_flow"] = state_obj.get("trackblazer_inventory_flow")
      snapshot["ocr_debug"] = state_obj.get("inventory_ocr_debug_entries") or []
      bot.set_snapshot(snapshot)
      bot.set_phase("checking_inventory", status="complete", message="Manual Trackblazer inventory check complete.")
    except Exception as exc:
      snapshot["state_summary"]["trackblazer_inventory_flow"] = {
        "trigger": "manual_console",
        "error": str(exc),
      }
      bot.set_snapshot(snapshot)
      bot.set_phase("checking_inventory", status="error", message="Manual inventory check failed.", error=str(exc))
    finally:
      bot.set_manual_control_active(False)
    publish_runtime_state()

  _start_manual_console_check("manual inventory check", _run)


def trigger_manual_shop_check():
  def _run():
    bot.set_phase("checking_shop", message="Running manual Trackblazer shop check.")
    publish_runtime_state()
    snapshot = _manual_console_snapshot()
    snapshot["scenario_name"] = "trackblazer"
    snapshot["sub_phase"] = "manual_shop_check"
    snapshot["selected_action"] = {"func": "check_shop"}
    snapshot["reasoning_notes"] = "Manual Trackblazer shop check triggered from the operator console."
    try:
      config.reload_config(print_config=False)
      resolve_control_backend()
      focus_target_window()
      bot.set_manual_control_active(True)

      from scenarios.trackblazer import inspect_shop_entry_state

      shop_check = inspect_shop_entry_state()
      snapshot["state_summary"]["trackblazer_shop_check"] = shop_check
      bot.set_snapshot(snapshot)
      bot.set_phase("checking_shop", status="complete", message="Manual Trackblazer shop check complete.")
    except Exception as exc:
      snapshot["state_summary"]["trackblazer_shop_check"] = {"error": str(exc)}
      bot.set_snapshot(snapshot)
      bot.set_phase("checking_shop", status="error", message="Manual shop check failed.", error=str(exc))
    finally:
      bot.set_manual_control_active(False)
    publish_runtime_state()

  _start_manual_console_check("manual shop check", _run)

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


def trigger_continue_or_capture():
  if bot.is_review_waiting():
    debug(f"Hotkey '{capture_hotkey}' pressed; continuing paused action.")
    bot.end_review_wait()
    publish_runtime_state()
    return
  trigger_debug_capture()


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
      _, _, overrides_path = resolve_region_adjuster_profiles(settings)
      constants.apply_region_overrides(
        overrides_path=overrides_path,
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
    f"Press '{capture_hotkey}' to continue paused semi-auto actions or capture year/stat OCR snapshots. "
    f"Press '{support_hotkey}' for support-region capture. "
    f"Press '{event_hotkey}' for event-region capture. "
    f"Press '{recreation_hotkey}' for recreation-region capture. "
    f"Press '{region_adjuster_hotkey}' to open the OCR region adjuster."
  )
  print(f"[SERVER] Open http://{host}:{port} to configure the bot.")
  
  server_config = uvicorn.Config(app, host=host, port=port, workers=1, log_level="warning")
  server = uvicorn.Server(server_config)
  server.run()


def start_server_in_background():
  threading.Thread(target=start_server, daemon=True).start()

if __name__ == "__main__":
  update_config()
  config.reload_config(print_config=False)
  bot.register_control_callback("toggle_bot", toggle_bot)
  bot.register_control_callback("check_inventory", trigger_manual_inventory_check)
  bot.register_control_callback("check_shop", trigger_manual_shop_check)
  ensure_operator_console()
  publish_runtime_state()
  
  listener = HotkeyListener(
    hotkey,
    toggle_bot,
    extra_hotkeys={
      capture_hotkey: trigger_continue_or_capture,
      support_hotkey: trigger_support_capture,
      event_hotkey: trigger_event_capture,
      recreation_hotkey: trigger_recreation_capture,
      region_adjuster_hotkey: trigger_region_adjuster,
    },
  )
  listener.start()
  start_server_in_background()
  ensure_operator_console().run_mainloop()
