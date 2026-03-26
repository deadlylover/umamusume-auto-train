# core/bot.py - Global bot state
# Shared state variables for the bot, accessible across modules

import threading
import time

from core.runtime_flow import SUB_PHASE_IDLE, default_post_action_resolution_state

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
runtime_sub_phase = SUB_PHASE_IDLE
runtime_phase_status = "idle"
runtime_phase_message = ""
runtime_error = ""
runtime_updated_at = 0.0
latest_snapshot = {}
debug_history = []          # Ring buffer of recent template match / action events
DEBUG_HISTORY_MAX = 200
pending_trackblazer_shop_check = False
pending_trackblazer_shop_check_reason = ""
review_waiting = False
review_event = threading.Event()
operator_console = None
pause_requested = False
execution_intent = "execute"
control_callbacks = {}
manual_control_active = False
trackblazer_use_items_enabled = False
trackblazer_scoring_mode = "stat_focused"  # "legacy" = use timeline template, "stat_focused" = stat_weight_training
trackblazer_bond_boost_enabled = True  # +10 score per blue/green friend on training (+15 on wit)
trackblazer_bond_boost_cutoff = "Classic Year Early Jun"  # bond boost inactive after this turn
trackblazer_allow_buff_override = False  # allow 60% megaphone to override active 40% buff
skill_dry_run_enabled = False
post_action_resolution = default_post_action_resolution_state()


def _coerce_debug_turn_label(value):
  if value is None:
    return ""
  text = str(value).strip()
  return text


def _extract_debug_context_from_snapshot(snapshot):
  if not isinstance(snapshot, dict):
    return {}

  summary = snapshot.get("state_summary") or {}
  selected_action = snapshot.get("selected_action") or {}
  context = {}

  year = _coerce_debug_turn_label(summary.get("year"))
  turn = _coerce_debug_turn_label(summary.get("turn"))
  if year:
    context["year"] = year
  if turn:
    context["turn"] = turn
  if year or turn:
    context["turn_label"] = " / ".join(part for part in (year, turn) if part)

  action_name = _coerce_debug_turn_label(selected_action.get("action"))
  if action_name:
    context["action"] = action_name

  return context


def set_phase(phase, status="active", message="", error="", sub_phase=None):
  global runtime_phase, runtime_sub_phase, runtime_phase_status, runtime_phase_message, runtime_error, runtime_updated_at
  with runtime_lock:
    runtime_phase = phase
    if sub_phase is not None:
      runtime_sub_phase = str(sub_phase or SUB_PHASE_IDLE)
    runtime_phase_status = status
    runtime_phase_message = message
    runtime_error = error
    runtime_updated_at = time.time()


def get_runtime_state():
  with runtime_lock:
    return {
      "phase": runtime_phase,
      "sub_phase": runtime_sub_phase,
      "status": runtime_phase_status,
      "message": runtime_phase_message,
      "error": runtime_error,
      "updated_at": runtime_updated_at,
      "review_waiting": review_waiting,
      "pause_requested": pause_requested,
      "manual_control_active": manual_control_active,
      "execution_intent": execution_intent,
      "pending_trackblazer_shop_check": pending_trackblazer_shop_check,
      "pending_trackblazer_shop_check_reason": pending_trackblazer_shop_check_reason,
      "trackblazer_use_items_enabled": trackblazer_use_items_enabled,
      "trackblazer_scoring_mode": trackblazer_scoring_mode,
      "skill_dry_run_enabled": skill_dry_run_enabled,
      "is_bot_running": is_bot_running,
      "post_action_resolution": dict(post_action_resolution or {}),
      "backend_state": get_backend_state(),
      "snapshot": latest_snapshot.copy() if isinstance(latest_snapshot, dict) else latest_snapshot,
    }


def set_snapshot(snapshot):
  global latest_snapshot, runtime_updated_at
  with runtime_lock:
    latest_snapshot = snapshot
    runtime_updated_at = time.time()


def push_debug_history(entry):
  """Append an event to the debug history ring buffer (thread-safe)."""
  with runtime_lock:
    payload = dict(entry or {})
    payload["_ts"] = time.time()

    snapshot_context = _extract_debug_context_from_snapshot(latest_snapshot)
    for key, value in snapshot_context.items():
      payload.setdefault(key, value)

    payload.setdefault("phase", runtime_phase)
    payload.setdefault("sub_phase", runtime_sub_phase)
    debug_history.append(payload)
    if len(debug_history) > DEBUG_HISTORY_MAX:
      del debug_history[:len(debug_history) - DEBUG_HISTORY_MAX]


def get_debug_history():
  """Return a shallow copy of the debug history list."""
  with runtime_lock:
    return list(debug_history)


def clear_debug_history():
  with runtime_lock:
    debug_history.clear()


def request_trackblazer_shop_check(reason=""):
  global pending_trackblazer_shop_check, pending_trackblazer_shop_check_reason, runtime_updated_at
  with runtime_lock:
    pending_trackblazer_shop_check = True
    pending_trackblazer_shop_check_reason = str(reason or "")
    runtime_updated_at = time.time()


def clear_trackblazer_shop_check_request():
  global pending_trackblazer_shop_check, pending_trackblazer_shop_check_reason, runtime_updated_at
  with runtime_lock:
    pending_trackblazer_shop_check = False
    pending_trackblazer_shop_check_reason = ""
    runtime_updated_at = time.time()


def has_pending_trackblazer_shop_check():
  with runtime_lock:
    return bool(pending_trackblazer_shop_check)


def get_pending_trackblazer_shop_check_reason():
  with runtime_lock:
    return str(pending_trackblazer_shop_check_reason or "")


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


def set_trackblazer_scoring_mode(mode):
  global trackblazer_scoring_mode, runtime_updated_at
  if mode == "default":
    normalized = "legacy"
  elif mode in ("legacy", "stat_focused"):
    normalized = mode
  else:
    normalized = "stat_focused"
  with runtime_lock:
    trackblazer_scoring_mode = normalized
    runtime_updated_at = time.time()


def get_trackblazer_scoring_mode():
  with runtime_lock:
    return trackblazer_scoring_mode


def set_trackblazer_bond_boost_enabled(enabled):
  global trackblazer_bond_boost_enabled, runtime_updated_at
  with runtime_lock:
    trackblazer_bond_boost_enabled = bool(enabled)
    runtime_updated_at = time.time()


def get_trackblazer_bond_boost_enabled():
  with runtime_lock:
    return trackblazer_bond_boost_enabled


def set_trackblazer_allow_buff_override(enabled):
  global trackblazer_allow_buff_override, runtime_updated_at
  with runtime_lock:
    trackblazer_allow_buff_override = bool(enabled)
    runtime_updated_at = time.time()


def get_trackblazer_allow_buff_override():
  with runtime_lock:
    return trackblazer_allow_buff_override


def set_trackblazer_bond_boost_cutoff(cutoff):
  global trackblazer_bond_boost_cutoff, runtime_updated_at
  with runtime_lock:
    trackblazer_bond_boost_cutoff = cutoff
    runtime_updated_at = time.time()


def get_trackblazer_bond_boost_cutoff():
  with runtime_lock:
    return trackblazer_bond_boost_cutoff


def set_skill_dry_run_enabled(enabled):
  global skill_dry_run_enabled, runtime_updated_at
  with runtime_lock:
    skill_dry_run_enabled = bool(enabled)
    runtime_updated_at = time.time()


def get_skill_dry_run_enabled():
  with runtime_lock:
    return skill_dry_run_enabled


def begin_post_action_resolution(source_action="", reason="", sub_phase=SUB_PHASE_IDLE):
  global post_action_resolution, runtime_updated_at
  with runtime_lock:
    post_action_resolution = default_post_action_resolution_state()
    post_action_resolution.update({
      "active": True,
      "source_action": str(source_action or ""),
      "reason": str(reason or ""),
      "sub_phase": str(sub_phase or SUB_PHASE_IDLE),
      "status": "active",
      "outcome": "",
    })
    runtime_updated_at = time.time()


def update_post_action_resolution(**changes):
  global post_action_resolution, runtime_updated_at
  with runtime_lock:
    if not isinstance(post_action_resolution, dict):
      post_action_resolution = default_post_action_resolution_state()
    for key, value in (changes or {}).items():
      if key == "deferred_work" and value is not None:
        post_action_resolution[key] = list(value)
      elif value is not None:
        post_action_resolution[key] = value
    runtime_updated_at = time.time()


def end_post_action_resolution(outcome="", status="completed"):
  update_post_action_resolution(active=False, outcome=str(outcome or ""), status=str(status or "completed"))


def clear_post_action_resolution():
  global post_action_resolution, runtime_updated_at
  with runtime_lock:
    post_action_resolution = default_post_action_resolution_state()
    runtime_updated_at = time.time()


def get_post_action_resolution_state():
  with runtime_lock:
    return dict(post_action_resolution or {})


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
