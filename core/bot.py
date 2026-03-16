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

# Window reference (platform-specific)
# - On Windows: pygetwindow Window object
# - On macOS: None (uses AppleScript for focus)
windows_window = None

# Training state
PREFERRED_POSITION_SET = False

# Operator console / semi-auto runtime state
runtime_lock = threading.Lock()
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
      "execution_intent": execution_intent,
      "is_bot_running": is_bot_running,
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
  normalized = intent if intent in ("check_only", "preview_clicks", "execute") else "execute"
  with runtime_lock:
    execution_intent = normalized
    runtime_updated_at = time.time()


def get_execution_intent():
  with runtime_lock:
    return execution_intent


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
