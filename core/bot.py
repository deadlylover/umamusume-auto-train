# core/bot.py - Global bot state
# Shared state variables for the bot, accessible across modules

import datetime
import os
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
DEBUG_HISTORY_MAX = 10000
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
trackblazer_use_new_planner_enabled = False
trackblazer_scoring_mode = "stat_focused"  # "legacy" = use timeline template, "stat_focused" = stat_weight_training
trackblazer_bond_boost_enabled = True  # +10 score per blue/green friend on training (+15 on wit)
trackblazer_bond_boost_cutoff = "Classic Year Early Jun"  # bond boost inactive after this turn
trackblazer_allow_buff_override = False  # allow 60% megaphone to override active 40% buff
skill_dry_run_enabled = False
skill_auto_buy_skill_enabled = None
post_action_resolution = default_post_action_resolution_state()
turn_trace_lock = threading.Lock()
turn_trace_path = ""
turn_trace_session_started = False
turn_trace_current_turn_label = ""
turn_trace_snapshot_count = 0
turn_trace_event_count = 0


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

  action_name = _coerce_debug_turn_label(selected_action.get("func") or selected_action.get("action"))
  if action_name:
    context["action"] = action_name

  return context


def _turn_trace_settings():
  try:
    import core.config as config
  except Exception:
    return {
      "enabled": False,
      "filename": "turn_trace.txt",
    }
  return {
    "enabled": bool(getattr(config, "TURN_TRACE_ENABLED", False)),
    "filename": str(getattr(config, "TURN_TRACE_FILENAME", "turn_trace.txt") or "turn_trace.txt"),
  }


def _turn_trace_default_dir():
  if hotkey == "f1":
    return os.path.join(os.getcwd(), "logs")
  return os.path.join(os.getcwd(), "logs", hotkey)


def _resolve_turn_trace_path(filename):
  if os.path.isabs(filename):
    return filename
  return os.path.join(_turn_trace_default_dir(), filename)


def _trace_ts(ts=None):
  target = float(ts if ts is not None else time.time())
  return datetime.datetime.fromtimestamp(target).astimezone().strftime("%Y-%m-%d %H:%M:%S.%f %Z")


def _turn_trace_summary_line(snapshot):
  summary = snapshot.get("state_summary") or {}
  selected_action = snapshot.get("selected_action") or {}
  backend_state = snapshot.get("backend_state") or {}
  mood = summary.get("current_mood") or "?"
  energy = summary.get("energy_level")
  max_energy = summary.get("max_energy")
  backend = (
    summary.get("control_backend")
    or backend_state.get("active_backend")
    or "?"
  )
  action_func = selected_action.get("func") or "?"
  training_name = selected_action.get("training_name") or ""
  if training_name and action_func == "do_training":
    action_label = f"{action_func} ({training_name})"
  else:
    action_label = action_func
  return (
    f"State: mood={mood} | energy={energy}/{max_energy} | backend={backend}\n"
    f"Action: {action_label}"
  )


def _trace_value(value):
  if value in (None, "", [], (), {}):
    return "-"
  if isinstance(value, bool):
    return "yes" if value else "no"
  if isinstance(value, float):
    return f"{value:.1f}".rstrip("0").rstrip(".")
  if isinstance(value, (list, tuple)):
    return ", ".join(str(item) for item in value) if value else "-"
  return str(value)


def _trace_click_line(index, click):
  if not isinstance(click, dict):
    return f"  {index}. {_trace_value(click)}"
  parts = [f"  {index}. {click.get('label') or 'click'}"]
  if click.get("template"):
    parts.append(f"template={click['template']}")
  if click.get("region_key"):
    parts.append(f"region={click['region_key']}")
  if click.get("note"):
    parts.append(f"note={click['note']}")
  return " | ".join(parts)


def _trace_training_line(index, training):
  if not isinstance(training, dict):
    return f"  {index}. {_trace_value(training)}"
  score_tuple = training.get("score_tuple") or []
  score = score_tuple[0] if score_tuple else None
  parts = [
    f"  {index}. {training.get('name') or 'unknown'}",
    f"score={_trace_value(score)}",
    f"fail={_trace_value(training.get('failure'))}%",
    f"supports={_trace_value(training.get('total_supports'))}",
  ]
  rainbows = training.get("total_rainbow_friends")
  if rainbows not in (None, ""):
    parts.append(f"rainbows={_trace_value(rainbows)}")
  gains = training.get("stat_gains") or {}
  if gains:
    gain_text = ", ".join(f"{stat}+{value}" for stat, value in gains.items() if value)
    if gain_text:
      parts.append(f"gains={gain_text}")
  if training.get("filtered_out"):
    parts.append(f"filtered={_trace_value(training.get('excluded_reason'))}")
  return " | ".join(parts)


def _trace_action_line(name, value):
  if isinstance(value, dict):
    scalar_parts = []
    for key, item in value.items():
      if isinstance(item, (dict, list, tuple)):
        continue
      scalar_parts.append(f"{key}={_trace_value(item)}")
    if scalar_parts:
      return f"  - {name}: " + " | ".join(scalar_parts)
    return f"  - {name}:"
  if isinstance(value, (list, tuple)):
    if not value:
      return f"  - {name}: none"
    item_text = "; ".join(_trace_value(item) for item in value)
    return f"  - {name}: {item_text}"
  return f"  - {name}: {_trace_value(value)}"


def _format_trace_snapshot(snapshot, sequence, ts):
  lines = []
  turn_label = snapshot.get("turn_label") or "unknown turn"
  runtime_state = get_runtime_state()
  selected_action = snapshot.get("selected_action") or {}
  lines.append(f"[SNAPSHOT {sequence:03d}] {_trace_ts(ts)}")
  lines.append(
    f"Turn: {turn_label} | phase={runtime_state.get('phase') or '?'} | "
    f"sub={snapshot.get('sub_phase') or runtime_state.get('sub_phase') or '?'} | "
    f"intent={snapshot.get('execution_intent') or runtime_state.get('execution_intent') or '?'}"
  )
  message = runtime_state.get("message") or ""
  error_text = runtime_state.get("error") or ""
  if message:
    lines.append(f"Message: {message}")
  if error_text:
    lines.append(f"Error: {error_text}")
  lines.append(_turn_trace_summary_line(snapshot))
  reasoning = snapshot.get("reasoning_notes") or ""
  if reasoning:
    lines.append(f"Reasoning: {reasoning}")
  available_actions = snapshot.get("available_actions") or []
  lines.append(
    "Available Actions: " + (", ".join(str(value) for value in available_actions) if available_actions else "none")
  )
  ranked_trainings = snapshot.get("ranked_trainings") or []
  if ranked_trainings:
    lines.append("Top Trainings:")
    for index, training in enumerate(ranked_trainings[:5], start=1):
      lines.append(_trace_training_line(index, training))
  planned_actions = snapshot.get("planned_actions") or {}
  if planned_actions:
    lines.append("Planned Actions:")
    for name, value in planned_actions.items():
      lines.append(_trace_action_line(name, value))
  planned_clicks = snapshot.get("planned_clicks") or []
  if planned_clicks:
    lines.append("Planned Clicks:")
    for index, click in enumerate(planned_clicks, start=1):
      lines.append(_trace_click_line(index, click))
  lines.append("")
  lines.append("---")
  lines.append("")
  return "\n".join(lines)


def _format_trace_event(entry, sequence):
  ts = entry.get("_ts", time.time())
  turn_label = entry.get("turn_label") or turn_trace_current_turn_label or "unknown turn"
  line = (
    f"[EVENT {sequence:03d}] {_trace_ts(ts)} | {turn_label} | "
    f"{entry.get('event') or '?'} {entry.get('asset') or '?'} -> {entry.get('result') or '?'}"
  )
  context = entry.get("context")
  if context:
    line += f" | ctx={context}"
  phase = entry.get("phase")
  sub_phase = entry.get("sub_phase")
  if phase:
    line += f" | phase={phase}"
  if sub_phase and sub_phase != phase:
    line += f" | sub={sub_phase}"
  note = entry.get("note") or entry.get("reason") or ""
  if note:
    line += f" | note={note}"
  return line + "\n"


def _write_turn_trace_text_unlocked(text):
  if not turn_trace_path:
    return
  os.makedirs(os.path.dirname(turn_trace_path), exist_ok=True)
  with open(turn_trace_path, "a", encoding="utf-8") as handle:
    handle.write(text)
    handle.flush()
    os.fsync(handle.fileno())


def _ensure_turn_trace_header_unlocked():
  global turn_trace_path, turn_trace_session_started
  settings = _turn_trace_settings()
  if not settings["enabled"]:
    return False

  resolved_path = _resolve_turn_trace_path(settings["filename"])
  if turn_trace_path != resolved_path:
    turn_trace_path = resolved_path
    turn_trace_session_started = False

  if not turn_trace_session_started:
    session_header = [
      "",
      "=" * 100,
      f"TURN TRACE SESSION START | {_trace_ts()}",
      f"File: {turn_trace_path}",
      f"Hotkey: {hotkey}",
      "=" * 100,
      "",
    ]
    _write_turn_trace_text_unlocked("\n".join(session_header))
    turn_trace_session_started = True
  return True


def _start_turn_trace_section_unlocked(turn_label):
  global turn_trace_current_turn_label, turn_trace_snapshot_count, turn_trace_event_count
  normalized = str(turn_label or "").strip()
  if not normalized or normalized == turn_trace_current_turn_label:
    return
  turn_trace_current_turn_label = normalized
  turn_trace_snapshot_count = 0
  turn_trace_event_count = 0
  section_header = [
    "#" * 100,
    f"TURN START | {normalized} | {_trace_ts()}",
    "#" * 100,
    "",
  ]
  _write_turn_trace_text_unlocked("\n".join(section_header))


def _append_turn_trace_snapshot(snapshot):
  global turn_trace_snapshot_count
  if not isinstance(snapshot, dict):
    return
  turn_label = str(snapshot.get("turn_label") or "").strip()
  with turn_trace_lock:
    if not _ensure_turn_trace_header_unlocked():
      return
    _start_turn_trace_section_unlocked(turn_label)
    turn_trace_snapshot_count += 1
    _write_turn_trace_text_unlocked(
      _format_trace_snapshot(snapshot, turn_trace_snapshot_count, time.time())
    )


def _append_turn_trace_event(entry):
  global turn_trace_event_count
  if not isinstance(entry, dict):
    return
  turn_label = str(entry.get("turn_label") or "").strip()
  with turn_trace_lock:
    if not _ensure_turn_trace_header_unlocked():
      return
    _start_turn_trace_section_unlocked(turn_label)
    turn_trace_event_count += 1
    _write_turn_trace_text_unlocked(
      _format_trace_event(entry, turn_trace_event_count)
    )


def set_phase(phase, status="active", message="", error="", sub_phase=None):
  global runtime_phase, runtime_sub_phase, runtime_phase_status, runtime_phase_message, runtime_error, runtime_updated_at
  with runtime_lock:
    before_phase = runtime_phase
    before_sub_phase = runtime_sub_phase
    before_status = runtime_phase_status
    before_message = runtime_phase_message
    before_error = runtime_error

    runtime_phase = phase
    if sub_phase is not None:
      runtime_sub_phase = str(sub_phase or SUB_PHASE_IDLE)
    runtime_phase_status = status
    runtime_phase_message = message
    runtime_error = error
    runtime_updated_at = time.time()

    changed = []
    if runtime_phase != before_phase:
      changed.append("phase")
    if runtime_sub_phase != before_sub_phase:
      changed.append("sub_phase")
    if runtime_phase_status != before_status:
      changed.append("status")
    if runtime_phase_message != before_message:
      changed.append("message")
    if runtime_error != before_error:
      changed.append("error")

    if changed:
      push_debug_history({
        "event": "phase_transition",
        "asset": runtime_phase,
        "result": runtime_phase_status,
        "context": "runtime_flow",
        "note": runtime_phase_message or runtime_error or ",".join(changed),
        "changed": changed,
        "before_phase": before_phase,
        "before_sub_phase": before_sub_phase,
        "before_status": before_status,
        "before_message": before_message,
        "before_error": before_error,
        "error": runtime_error,
      })


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
      "trackblazer_use_new_planner_enabled": trackblazer_use_new_planner_enabled,
      "trackblazer_scoring_mode": trackblazer_scoring_mode,
      "skill_dry_run_enabled": not get_skill_auto_buy_enabled(),
      "skill_auto_buy_skill_enabled": get_skill_auto_buy_enabled(),
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
  _append_turn_trace_snapshot(snapshot)


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
  _append_turn_trace_event(payload)


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
    was_pending = pending_trackblazer_shop_check
    previous_reason = pending_trackblazer_shop_check_reason
    pending_trackblazer_shop_check = True
    pending_trackblazer_shop_check_reason = str(reason or "")
    runtime_updated_at = time.time()
    if not was_pending or pending_trackblazer_shop_check_reason != previous_reason:
      push_debug_history({
        "event": "runtime_flag",
        "asset": "pending_trackblazer_shop_check",
        "result": "set",
        "context": "runtime_flow",
        "reason": pending_trackblazer_shop_check_reason,
        "previous_reason": previous_reason,
      })


def clear_trackblazer_shop_check_request():
  global pending_trackblazer_shop_check, pending_trackblazer_shop_check_reason, runtime_updated_at
  with runtime_lock:
    was_pending = pending_trackblazer_shop_check
    previous_reason = pending_trackblazer_shop_check_reason
    pending_trackblazer_shop_check = False
    pending_trackblazer_shop_check_reason = ""
    runtime_updated_at = time.time()
    if was_pending or previous_reason:
      push_debug_history({
        "event": "runtime_flag",
        "asset": "pending_trackblazer_shop_check",
        "result": "cleared",
        "context": "runtime_flow",
        "reason": previous_reason,
      })


def has_pending_trackblazer_shop_check():
  with runtime_lock:
    return bool(pending_trackblazer_shop_check)


def get_pending_trackblazer_shop_check_reason():
  with runtime_lock:
    return str(pending_trackblazer_shop_check_reason or "")


def begin_review_wait():
  global review_waiting
  with runtime_lock:
    was_waiting = review_waiting
    review_waiting = True
    if not was_waiting:
      push_debug_history({
        "event": "runtime_flag",
        "asset": "review_wait",
        "result": "begin",
        "context": "runtime_flow",
        "note": "Waiting for operator confirmation or continue.",
      })
  review_event.clear()


def end_review_wait():
  global review_waiting
  with runtime_lock:
    was_waiting = review_waiting
    review_waiting = False
    if was_waiting:
      push_debug_history({
        "event": "runtime_flag",
        "asset": "review_wait",
        "result": "end",
        "context": "runtime_flow",
        "note": "Review wait released.",
      })
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
    previous_intent = execution_intent
    execution_intent = normalized
    runtime_updated_at = time.time()
    if execution_intent != previous_intent:
      push_debug_history({
        "event": "runtime_flag",
        "asset": "execution_intent",
        "result": execution_intent,
        "context": "runtime_flow",
        "note": f"previous={previous_intent}",
      })


def get_execution_intent():
  with runtime_lock:
    return execution_intent


def set_manual_control_active(active):
  global manual_control_active, runtime_updated_at
  with runtime_lock:
    previous = manual_control_active
    manual_control_active = bool(active)
    runtime_updated_at = time.time()
    if manual_control_active != previous:
      push_debug_history({
        "event": "runtime_flag",
        "asset": "manual_control_active",
        "result": "enabled" if manual_control_active else "disabled",
        "context": "runtime_flow",
      })


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


def set_trackblazer_use_new_planner_enabled(enabled):
  global trackblazer_use_new_planner_enabled, runtime_updated_at
  with runtime_lock:
    trackblazer_use_new_planner_enabled = bool(enabled)
    runtime_updated_at = time.time()


def get_trackblazer_use_new_planner_enabled():
  with runtime_lock:
    return trackblazer_use_new_planner_enabled


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


def set_skill_auto_buy_enabled(enabled):
  global skill_auto_buy_skill_enabled, skill_dry_run_enabled, runtime_updated_at
  with runtime_lock:
    skill_auto_buy_skill_enabled = bool(enabled)
    skill_dry_run_enabled = not bool(enabled)
    runtime_updated_at = time.time()


def get_skill_auto_buy_enabled():
  global skill_auto_buy_skill_enabled
  with runtime_lock:
    if skill_auto_buy_skill_enabled is not None:
      return bool(skill_auto_buy_skill_enabled)
  try:
    import core.config as config
    return bool(getattr(config, "IS_AUTO_BUY_SKILL", False))
  except Exception:
    return False


def set_skill_dry_run_enabled(enabled):
  set_skill_auto_buy_enabled(enabled)


def get_skill_dry_run_enabled():
  return get_skill_auto_buy_enabled()


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
    push_debug_history({
      "event": "post_action_resolution",
      "asset": source_action or "unknown_action",
      "result": "begin",
      "context": "runtime_flow",
      "reason": str(reason or ""),
      "target_sub_phase": str(sub_phase or SUB_PHASE_IDLE),
    })


def update_post_action_resolution(**changes):
  global post_action_resolution, runtime_updated_at
  with runtime_lock:
    if not isinstance(post_action_resolution, dict):
      post_action_resolution = default_post_action_resolution_state()
    changed = {}
    for key, value in (changes or {}).items():
      if key == "deferred_work" and value is not None:
        new_value = list(value)
      elif value is not None:
        new_value = value
      else:
        continue
      previous = post_action_resolution.get(key)
      if previous != new_value:
        changed[key] = {"before": previous, "after": new_value}
        post_action_resolution[key] = new_value
    runtime_updated_at = time.time()
    if changed:
      push_debug_history({
        "event": "post_action_resolution",
        "asset": post_action_resolution.get("source_action") or "unknown_action",
        "result": str(post_action_resolution.get("status") or "updated"),
        "context": "runtime_flow",
        "changes": changed,
        "note": ",".join(changed.keys()),
      })


def end_post_action_resolution(outcome="", status="completed"):
  update_post_action_resolution(active=False, outcome=str(outcome or ""), status=str(status or "completed"))


def clear_post_action_resolution():
  global post_action_resolution, runtime_updated_at
  with runtime_lock:
    had_state = dict(post_action_resolution or {})
    post_action_resolution = default_post_action_resolution_state()
    runtime_updated_at = time.time()
    if had_state.get("active") or had_state.get("source_action") or had_state.get("reason") or had_state.get("outcome"):
      push_debug_history({
        "event": "post_action_resolution",
        "asset": had_state.get("source_action") or "unknown_action",
        "result": "cleared",
        "context": "runtime_flow",
        "note": had_state.get("outcome") or had_state.get("reason") or "",
      })


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
