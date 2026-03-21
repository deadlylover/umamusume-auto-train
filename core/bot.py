# core/bot.py - Global bot state
# Shared state variables for the bot, accessible across modules

import threading
import time

# Bot running state
is_bot_running = False
bot_thread = None
bot_lock = threading.Lock()
stop_event = threading.Event()

# Hotkey configuration
hotkey = "f1"

# Device/platform configuration
use_adb = False
device_id = None
CONTROL_BACKEND_HOST_INPUT = "host_input"
CONTROL_BACKEND_ADB = "adb"
requested_control_backend = CONTROL_BACKEND_HOST_INPUT
active_control_backend = CONTROL_BACKEND_HOST_INPUT
screenshot_backend = CONTROL_BACKEND_HOST_INPUT
allow_host_input_fallback = False
backend_resolution_reason = ""
adb_status = {
  "requested_backend": CONTROL_BACKEND_HOST_INPUT,
  "active_backend": CONTROL_BACKEND_HOST_INPUT,
  "device_id": None,
  "adb_available": False,
  "adb_connected": False,
  "device_ready": False,
  "healthcheck_passed": False,
  "healthy": False,
  "adb_last_error": "",
}

# Window reference (platform-specific)
# - On Windows: pygetwindow Window object
# - On macOS: None (uses AppleScript for focus)
windows_window = None

# Training state
PREFERRED_POSITION_SET = False

# Operator console / semi-auto runtime state
runtime_lock = threading.RLock()
runtime_phase = "idle"
runtime_phase_status = "idle"
runtime_phase_message = ""
runtime_error = ""
runtime_updated_at = 0.0
latest_snapshot = {}
review_waiting = False
review_event = threading.Event()
operator_console = None
pause_requested = False
execution_intent = "execute"
control_callbacks = {}
manual_control_active = False
trackblazer_use_items_enabled = False
skill_dry_run_enabled = False


def set_phase(phase, status="active", message="", error=""):
  global runtime_phase, runtime_phase_status, runtime_phase_message, runtime_error, runtime_updated_at
  with runtime_lock:
    runtime_phase = phase
    runtime_phase_status = status
    runtime_phase_message = message
    runtime_error = error
    runtime_updated_at = time.time()


def get_runtime_state():
  with runtime_lock:
    return {
      "phase": runtime_phase,
      "status": runtime_phase_status,
      "message": runtime_phase_message,
      "error": runtime_error,
      "updated_at": runtime_updated_at,
      "review_waiting": review_waiting,
      "pause_requested": pause_requested,
      "manual_control_active": manual_control_active,
      "execution_intent": execution_intent,
      "trackblazer_use_items_enabled": trackblazer_use_items_enabled,
      "skill_dry_run_enabled": skill_dry_run_enabled,
      "is_bot_running": is_bot_running,
      "backend_state": get_backend_state(),
      "snapshot": latest_snapshot.copy() if isinstance(latest_snapshot, dict) else latest_snapshot,
    }


def set_snapshot(snapshot):
  global latest_snapshot, runtime_updated_at
  with runtime_lock:
    latest_snapshot = snapshot
    runtime_updated_at = time.time()


def begin_review_wait():
  global review_waiting
  with runtime_lock:
    review_waiting = True
  review_event.clear()


def end_review_wait():
  global review_waiting
  with runtime_lock:
    review_waiting = False
  review_event.set()


def cancel_review_wait():
  end_review_wait()


def is_review_waiting():
  with runtime_lock:
    return review_waiting


def request_pause():
  global pause_requested
  with runtime_lock:
    pause_requested = True


def clear_pause_request():
  global pause_requested
  with runtime_lock:
    pause_requested = False


def is_pause_requested():
  with runtime_lock:
    return pause_requested


def set_execution_intent(intent):
  global execution_intent, runtime_updated_at
  # Normalize legacy "preview_clicks" to "check_only" (two-mode model).
  if intent == "preview_clicks":
    intent = "check_only"
  normalized = intent if intent in ("check_only", "execute") else "execute"
  with runtime_lock:
    execution_intent = normalized
    runtime_updated_at = time.time()


def get_execution_intent():
  with runtime_lock:
    return execution_intent


def set_manual_control_active(active):
  global manual_control_active, runtime_updated_at
  with runtime_lock:
    manual_control_active = bool(active)
    runtime_updated_at = time.time()


def is_manual_control_active():
  with runtime_lock:
    return manual_control_active


def set_trackblazer_use_items_enabled(enabled):
  global trackblazer_use_items_enabled, runtime_updated_at
  with runtime_lock:
    trackblazer_use_items_enabled = bool(enabled)
    runtime_updated_at = time.time()


def get_trackblazer_use_items_enabled():
  with runtime_lock:
    return trackblazer_use_items_enabled


def set_skill_dry_run_enabled(enabled):
  global skill_dry_run_enabled, runtime_updated_at
  with runtime_lock:
    skill_dry_run_enabled = bool(enabled)
    runtime_updated_at = time.time()


def get_skill_dry_run_enabled():
  with runtime_lock:
    return skill_dry_run_enabled


def set_control_backend_state(
  requested_backend=None,
  active_backend=None,
  screenshot_backend_name=None,
  fallback_allowed=None,
  resolved_device_id=None,
  resolution_reason=None,
):
  global requested_control_backend, active_control_backend, screenshot_backend
  global allow_host_input_fallback, device_id, use_adb, backend_resolution_reason, runtime_updated_at
  with runtime_lock:
    if requested_backend is not None:
      requested_control_backend = requested_backend
    if active_backend is not None:
      active_control_backend = active_backend
      use_adb = active_control_backend == CONTROL_BACKEND_ADB
    if screenshot_backend_name is not None:
      screenshot_backend = screenshot_backend_name
    if fallback_allowed is not None:
      allow_host_input_fallback = bool(fallback_allowed)
    if resolved_device_id is not None:
      device_id = resolved_device_id
    if resolution_reason is not None:
      backend_resolution_reason = resolution_reason
    runtime_updated_at = time.time()


def update_adb_status(status):
  global adb_status, runtime_updated_at
  with runtime_lock:
    adb_status = dict(status or {})
    runtime_updated_at = time.time()


def get_backend_state():
  with runtime_lock:
    backend_snapshot = {
      "requested_backend": requested_control_backend,
      "active_backend": active_control_backend,
      "screenshot_backend": screenshot_backend,
      "device_id": device_id,
      "allow_host_input_fallback": allow_host_input_fallback,
      "resolution_reason": backend_resolution_reason,
      "use_adb_legacy_flag": use_adb,
      "adb": dict(adb_status or {}),
    }
  return backend_snapshot


def get_requested_control_backend():
  with runtime_lock:
    return requested_control_backend


def get_active_control_backend():
  with runtime_lock:
    return active_control_backend


def get_screenshot_backend():
  with runtime_lock:
    return screenshot_backend


def is_adb_requested():
  with runtime_lock:
    return requested_control_backend == CONTROL_BACKEND_ADB


def is_adb_input_active():
  with runtime_lock:
    return active_control_backend == CONTROL_BACKEND_ADB


def uses_adb_for_screenshots():
  with runtime_lock:
    return screenshot_backend == CONTROL_BACKEND_ADB


def register_control_callback(name, callback):
  with runtime_lock:
    control_callbacks[name] = callback


def invoke_control_callback(name):
  with runtime_lock:
    callback = control_callbacks.get(name)
  if callback is None:
    return False
  callback()
  return True
