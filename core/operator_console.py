import json
import queue
import tkinter as tk
from tkinter import scrolledtext
import threading
from pathlib import Path

from PIL import Image, ImageTk

import core.bot as bot
import core.config as config
import utils.constants as constants
import utils.device_action_wrapper as device_action
from core.platform.window_focus import focus_target_window
from core.region_adjuster import run_region_adjuster_session
from core.region_adjuster.shared import resolve_region_adjuster_profiles
from utils.log import debug, error

PHASES = [
  "idle",
  "focusing_window",
  "scanning_lobby",
  "collecting_main_state",
  "checking_inventory",
  "checking_shop",
  "collecting_training_state",
  "pre_training",
  "evaluating_strategy",
  "pre_race",
  "waiting_for_confirmation",
  "executing_action",
  "recovering",
]

PHASE_CONTROL_CALLBACKS = {
  "checking_inventory": "check_inventory",
  "checking_shop": "check_shop",
}


class OperatorConsole:
  DEFAULT_GEOMETRY = "960x760+40+40"
  MIN_WINDOW_SIZE = (820, 560)
  FLOW_PANE_MIN_WIDTH = 220
  NARROW_PANE_MIN_WIDTH = 154
  NARROW_TEXT_WIDTH = 56

  def __init__(self):
    self._queue = queue.Queue()
    self._root = None
    self._always_on_top_var = None
    self._phase_value = None
    self._status_value = None
    self._scenario_value = None
    self._turn_value = None
    self._energy_value = None
    self._action_value = None
    self._sub_phase_value = None
    self._intent_value = None
    self._backend_value = None
    self._device_value = None
    self._message_value = None
    self._error_value = None
    self._execution_intent_var = None
    self._skip_scenario_detection_var = None
    self._skip_full_stats_aptitude_check_var = None
    self._phase_labels = {}
    self._timing_text = None
    self._summary_text = None
    self._training_text = None
    self._inventory_text = None
    self._ocr_debug_entries = []
    self._ocr_debug_listbox = None
    self._ocr_debug_meta = None
    self._ocr_debug_asset_label = None
    self._ocr_debug_region_label = None
    self._ocr_debug_asset_photo = None
    self._ocr_debug_region_photo = None
    self._preview_windows = {}
    self._bot_button = None
    self._pause_button = None
    self._resume_button = None
    self._continue_button = None
    self._window_geometry = self.DEFAULT_GEOMETRY
    self._last_saved_geometry = None
    self._geometry_persist_ready = False
    self._geometry_debounce_id = None

  def start(self):
    if self._root is not None:
      return
    try:
      self._load_window_geometry()
      self._root = tk.Tk()
      self._root.title("Uma Operator Console")
      self._root.configure(bg="#101418")
      self._root.attributes("-topmost", False)
      self._root.protocol("WM_DELETE_WINDOW", self._hide_window)
      self._build_layout()
      self._root.update_idletasks()
      self._root.geometry(self._window_geometry)
      self._root.bind("<Configure>", self._on_window_configure)
      # Apply position after mainloop starts — macOS WM ignores pre-mainloop positioning
      self._root.after(0, self._deferred_apply_geometry)
      self._root.after(500, self._deferred_apply_geometry)
      self._root.after(1500, self._enable_geometry_persist)
      self._poll_queue()
    except Exception as exc:  # pragma: no cover
      error(f"Operator console failed to start: {exc}")

  def stop(self):
    if self._root is not None:
      self._queue.put(("shutdown", None))

  def publish(self):
    self._queue.put(("refresh", bot.get_runtime_state()))

  def run_mainloop(self):
    if self._root is None:
      self.start()
    if self._root is not None:
      self._root.mainloop()

  def _hide_window(self):
    if self._root is not None:
      self._persist_window_geometry()
      self._root.withdraw()

  def _show_window(self):
    if self._root is None:
      return
    state = str(self._root.state())
    if state == "withdrawn":
      self._geometry_persist_ready = False
      self._load_window_geometry()
      self._root.deiconify()
      self._root.after(0, self._deferred_apply_geometry)
      self._root.after(1000, self._enable_geometry_persist)
    elif state == "iconic":
      self._root.deiconify()
    self._root.lift()

  def _build_layout(self):
    root = self._root
    root.minsize(*self.MIN_WINDOW_SIZE)
    root.columnconfigure(0, weight=0, minsize=self.FLOW_PANE_MIN_WIDTH)
    root.columnconfigure(1, weight=1)
    root.rowconfigure(2, weight=1)

    top = tk.Frame(root, bg="#101418", padx=8, pady=4)
    top.grid(row=0, column=0, columnspan=2, sticky="ew")
    for col in range(5):
      top.columnconfigure(col, weight=1)

    self._phase_value = self._make_stat(top, 0, "Phase")
    self._status_value = self._make_stat(top, 1, "Status")
    self._scenario_value = self._make_stat(top, 2, "Scenario")
    self._turn_value = self._make_stat(top, 3, "Turn")
    self._energy_value = self._make_stat(top, 4, "Energy")
    self._action_value = self._make_stat(top, 5, "Action")
    self._sub_phase_value = self._make_stat(top, 6, "Sub-Phase")
    self._intent_value = self._make_stat(top, 7, "Intent")
    self._backend_value = self._make_stat(top, 8, "Backend")
    self._device_value = self._make_stat(top, 9, "Device")

    actions = tk.Frame(root, bg="#101418", padx=8, pady=0)
    actions.grid(row=1, column=0, columnspan=2, sticky="ew")
    actions.columnconfigure(0, weight=1)
    primary_controls = tk.Frame(actions, bg="#101418")
    primary_controls.grid(row=0, column=0, sticky="w")
    secondary_controls = tk.Frame(actions, bg="#101418")
    secondary_controls.grid(row=1, column=0, sticky="w", pady=(6, 0))

    self._bot_button = tk.Button(primary_controls, text="Start Bot", command=self._toggle_bot)
    self._bot_button.pack(side=tk.LEFT, padx=(0, 8))
    self._pause_button = tk.Button(primary_controls, text="Pause", command=self._request_pause)
    self._pause_button.pack(side=tk.LEFT, padx=(0, 8))
    self._resume_button = tk.Button(primary_controls, text="Resume", command=self._resume_bot)
    self._resume_button.pack(side=tk.LEFT, padx=(0, 8))
    self._continue_button = tk.Button(primary_controls, text="Continue (F2)", command=self._continue_review)
    self._continue_button.pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(primary_controls, text="Open OCR Adjuster", command=self._launch_adjuster).pack(side=tk.LEFT, padx=(0, 4))
    tk.Button(primary_controls, text="Asset Creator", command=self._launch_asset_creator).pack(side=tk.LEFT)
    self._execution_intent_var = tk.StringVar(value=bot.get_execution_intent())
    for intent in ("check_only", "preview_clicks", "execute"):
      tk.Radiobutton(
        secondary_controls,
        text=intent.replace("_", " "),
        value=intent,
        variable=self._execution_intent_var,
        command=self._set_execution_intent,
        fg="white",
        bg="#101418",
        selectcolor="#192028",
        activebackground="#101418",
        activeforeground="white",
      ).pack(side=tk.LEFT, padx=(0 if intent == "check_only" else 4, 0))
    self._always_on_top_var = tk.BooleanVar(value=False)
    self._skip_scenario_detection_var = tk.BooleanVar(value=bool(getattr(config, "SKIP_SCENARIO_DETECTION", True)))
    self._skip_full_stats_aptitude_check_var = tk.BooleanVar(value=bool(getattr(config, "SKIP_FULL_STATS_APTITUDE_CHECK", True)))
    tk.Checkbutton(
      secondary_controls,
      text="Always on top",
      variable=self._always_on_top_var,
      command=self._toggle_always_on_top,
      fg="white",
      bg="#101418",
      selectcolor="#192028",
      activebackground="#101418",
      activeforeground="white",
    ).pack(side=tk.LEFT, padx=(12, 0))
    tk.Checkbutton(
      secondary_controls,
      text="Skip scenario detect",
      variable=self._skip_scenario_detection_var,
      command=self._toggle_skip_scenario_detection,
      fg="white",
      bg="#101418",
      selectcolor="#192028",
      activebackground="#101418",
      activeforeground="white",
    ).pack(side=tk.LEFT, padx=(12, 0))
    tk.Checkbutton(
      secondary_controls,
      text="Skip full stats/aptitude",
      variable=self._skip_full_stats_aptitude_check_var,
      command=self._toggle_skip_full_stats_aptitude_check,
      fg="white",
      bg="#101418",
      selectcolor="#192028",
      activebackground="#101418",
      activeforeground="white",
    ).pack(side=tk.LEFT, padx=(12, 0))
    tk.Button(secondary_controls, text="Save Position", command=self._save_and_test_position).pack(side=tk.LEFT, padx=(12, 0))

    left = tk.LabelFrame(root, text="Flow", fg="white", bg="#101418", padx=6, pady=6)
    left.grid(row=2, column=0, sticky="nsew", padx=(8, 4), pady=6)
    right = tk.Frame(root, bg="#101418")
    right.grid(row=2, column=1, sticky="nsew", padx=(4, 8), pady=6)
    right.columnconfigure(0, weight=1)
    right.rowconfigure(1, weight=1)
    right.rowconfigure(3, weight=1)
    right.rowconfigure(5, weight=3)

    for phase in PHASES:
      label = tk.Label(
        left,
        text=phase.replace("_", " "),
        anchor="w",
        fg="#9aa4ad",
        bg="#192028",
        padx=8,
        pady=3,
        font=("Helvetica", 10),
      )
      label.pack(fill=tk.X, pady=1)
      self._phase_labels[phase] = label
      callback_name = PHASE_CONTROL_CALLBACKS.get(phase)
      if callback_name:
        label.configure(cursor="hand2")
        label.bind("<Button-1>", lambda _event, name=callback_name: self._run_phase_check(name))

    self._message_value = tk.StringVar(value="")
    self._error_value = tk.StringVar(value="")
    summary_header = tk.Frame(right, bg="#101418")
    summary_header.grid(row=0, column=0, sticky="ew")
    summary_header.columnconfigure(0, weight=1)
    summary_header.columnconfigure(1, weight=0)
    summary_header.columnconfigure(2, weight=1)
    summary_header.columnconfigure(3, weight=0)
    tk.Label(summary_header, text="Timing", fg="white", bg="#101418", anchor="w").grid(row=0, column=0, sticky="w")
    tk.Button(summary_header, text="Copy Timing", command=lambda: self._copy_widget(self._timing_text)).grid(row=0, column=1, sticky="e", padx=(0, 12))
    tk.Label(summary_header, text="Summary", fg="white", bg="#101418", anchor="w").grid(row=0, column=2, sticky="w")
    tk.Button(summary_header, text="Copy Summary", command=lambda: self._copy_widget(self._summary_text)).grid(row=0, column=3, sticky="e")
    summary_panel = tk.PanedWindow(right, orient=tk.HORIZONTAL, sashrelief=tk.RAISED, bg="#101418")
    summary_panel.grid(row=1, column=0, sticky="nsew", pady=(2, 6))
    timing_frame = tk.Frame(summary_panel, bg="#101418")
    timing_frame.rowconfigure(0, weight=1)
    timing_frame.columnconfigure(0, weight=1)
    self._timing_text = scrolledtext.ScrolledText(
      timing_frame,
      height=8,
      width=self.NARROW_TEXT_WIDTH,
      bg="#192028",
      fg="#d6dde5",
      insertbackground="white",
    )
    self._timing_text.grid(row=0, column=0, sticky="nsew")
    summary_panel.add(timing_frame, minsize=self.NARROW_PANE_MIN_WIDTH)
    summary_frame = tk.Frame(summary_panel, bg="#101418")
    summary_frame.rowconfigure(0, weight=1)
    summary_frame.columnconfigure(0, weight=1)
    self._summary_text = scrolledtext.ScrolledText(summary_frame, height=8, bg="#192028", fg="#d6dde5", insertbackground="white")
    self._summary_text.grid(row=0, column=0, sticky="nsew")
    summary_panel.add(summary_frame, minsize=220)

    training_header = tk.Frame(right, bg="#101418")
    training_header.grid(row=2, column=0, sticky="ew")
    training_header.columnconfigure(0, weight=1)
    training_header.columnconfigure(1, weight=0)
    training_header.columnconfigure(2, weight=1)
    training_header.columnconfigure(3, weight=0)
    tk.Label(training_header, text="Ranked Trainings", fg="white", bg="#101418", anchor="w").grid(row=0, column=0, sticky="w")
    tk.Button(training_header, text="Copy Trainings", command=lambda: self._copy_widget(self._training_text)).grid(row=0, column=1, sticky="e", padx=(0, 12))
    tk.Label(training_header, text="Inventory", fg="white", bg="#101418", anchor="w").grid(row=0, column=2, sticky="w")
    tk.Button(training_header, text="Copy Inventory", command=lambda: self._copy_widget(self._inventory_text)).grid(row=0, column=3, sticky="e")
    training_panel = tk.PanedWindow(right, orient=tk.HORIZONTAL, sashrelief=tk.RAISED, bg="#101418")
    training_panel.grid(row=3, column=0, sticky="nsew", pady=(2, 6))
    training_frame = tk.Frame(training_panel, bg="#101418")
    training_frame.rowconfigure(0, weight=1)
    training_frame.columnconfigure(0, weight=1)
    self._training_text = scrolledtext.ScrolledText(
      training_frame,
      height=6,
      width=self.NARROW_TEXT_WIDTH,
      bg="#192028",
      fg="#d6dde5",
      insertbackground="white",
    )
    self._training_text.grid(row=0, column=0, sticky="nsew")
    training_panel.add(training_frame, minsize=self.NARROW_PANE_MIN_WIDTH)
    inventory_frame = tk.Frame(training_panel, bg="#101418")
    inventory_frame.rowconfigure(0, weight=1)
    inventory_frame.columnconfigure(0, weight=1)
    self._inventory_text = scrolledtext.ScrolledText(inventory_frame, height=6, bg="#192028", fg="#d6dde5", insertbackground="white")
    self._inventory_text.grid(row=0, column=0, sticky="nsew")
    training_panel.add(inventory_frame, minsize=220)

    ocr_header = tk.Frame(right, bg="#101418")
    ocr_header.grid(row=4, column=0, sticky="ew")
    ocr_header.columnconfigure(0, weight=1)
    tk.Label(ocr_header, text="OCR Debug", fg="white", bg="#101418", anchor="w").grid(row=0, column=0, sticky="w")
    tk.Button(ocr_header, text="Copy OCR Debug", command=self._copy_ocr_debug).grid(row=0, column=1, sticky="e")
    ocr_panel = tk.PanedWindow(right, orient=tk.HORIZONTAL, sashrelief=tk.RAISED, bg="#101418")
    ocr_panel.grid(row=5, column=0, sticky="nsew", pady=(2, 6))

    ocr_list_frame = tk.Frame(ocr_panel, bg="#101418")
    self._ocr_debug_listbox = tk.Listbox(
      ocr_list_frame,
      height=14,
      width=26,
      exportselection=False,
      selectmode=tk.SINGLE,
      bg="#192028",
      fg="#d6dde5",
    )
    self._ocr_debug_listbox.pack(fill=tk.BOTH, expand=True)
    self._ocr_debug_listbox.bind("<<ListboxSelect>>", self._on_ocr_debug_select)
    ocr_panel.add(ocr_list_frame, minsize=190)

    ocr_detail_frame = tk.Frame(ocr_panel, bg="#101418")
    ocr_detail_frame.columnconfigure(0, weight=1)
    ocr_detail_frame.columnconfigure(1, weight=1)
    ocr_detail_frame.rowconfigure(1, weight=1)
    tk.Label(ocr_detail_frame, text="Asset", fg="white", bg="#101418").grid(row=0, column=0, sticky="w")
    tk.Label(ocr_detail_frame, text="Search Region", fg="white", bg="#101418").grid(row=0, column=1, sticky="w")
    self._ocr_debug_asset_label = tk.Label(ocr_detail_frame, bg="#192028", fg="#9aa4ad", anchor="center", justify="center", text="No asset", cursor="hand2")
    self._ocr_debug_asset_label.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
    self._ocr_debug_region_asset_path = None
    self._ocr_debug_asset_label.bind("<Button-1>", lambda _event: self._open_preview_window(self._ocr_debug_region_asset_path, "OCR Asset Preview"))
    self._ocr_debug_region_label = tk.Label(ocr_detail_frame, bg="#192028", fg="#9aa4ad", anchor="center", justify="center", text="No region image", cursor="hand2")
    self._ocr_debug_region_label.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
    self._ocr_debug_region_search_path = None
    self._ocr_debug_region_label.bind("<Button-1>", lambda _event: self._open_preview_window(self._ocr_debug_region_search_path, "OCR Search Region Preview"))
    self._ocr_debug_meta = scrolledtext.ScrolledText(ocr_detail_frame, height=6, bg="#192028", fg="#d6dde5", insertbackground="white")
    self._ocr_debug_meta.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
    ocr_panel.add(ocr_detail_frame, minsize=260)

    tk.Label(right, textvariable=self._message_value, fg="#8bd5ca", bg="#101418", anchor="w", justify="left", wraplength=430).grid(row=6, column=0, sticky="ew")
    tk.Label(right, textvariable=self._error_value, fg="#ff8c8c", bg="#101418", anchor="w", justify="left", wraplength=430).grid(row=7, column=0, sticky="ew")

  def _make_stat(self, parent, column, title):
    row = 0 if column < 5 else 1
    actual_column = column if column < 5 else column - 5
    frame = tk.Frame(parent, bg="#151b22", padx=6, pady=3)
    frame.grid(row=row, column=actual_column, sticky="ew", padx=2, pady=2)
    tk.Label(frame, text=title, fg="#8a949e", bg="#151b22", font=("Helvetica", 9)).pack(anchor="w")
    value = tk.StringVar(value="-")
    tk.Label(frame, textvariable=value, fg="white", bg="#151b22", font=("Helvetica", 10, "bold")).pack(anchor="w")
    return value

  def _poll_queue(self):
    try:
      while True:
        cmd, payload = self._queue.get_nowait()
        if cmd == "refresh":
          self._show_window()
          self._render(payload or {})
        elif cmd == "shutdown":
          if self._root is not None:
            self._root.quit()
            self._root.destroy()
            self._root = None
            return
    except queue.Empty:
      pass

    if self._root is not None:
      self._root.after(100, self._poll_queue)

  def _render(self, runtime_state):
    snapshot = runtime_state.get("snapshot") or {}
    state_summary = snapshot.get("state_summary") or {}
    selected_action = snapshot.get("selected_action") or {}
    backend_state = snapshot.get("backend_state") or runtime_state.get("backend_state") or {}
    adb_state = backend_state.get("adb") or {}

    self._phase_value.set(runtime_state.get("phase") or "-")
    self._status_value.set(runtime_state.get("status") or "-")
    self._scenario_value.set(snapshot.get("scenario_name") or constants.SCENARIO_NAME or "-")
    self._turn_value.set(snapshot.get("turn_label") or "-")
    self._energy_value.set(snapshot.get("energy_label") or "-")
    self._action_value.set(selected_action.get("func") or "-")
    self._sub_phase_value.set(snapshot.get("sub_phase") or "-")
    self._intent_value.set(runtime_state.get("execution_intent") or snapshot.get("execution_intent") or "-")
    self._backend_value.set(backend_state.get("active_backend") or "-")
    self._device_value.set(backend_state.get("device_id") or "-")
    if self._execution_intent_var is not None:
      self._execution_intent_var.set(runtime_state.get("execution_intent") or "execute")
    self._message_value.set(runtime_state.get("message") or "")
    self._error_value.set(runtime_state.get("error") or "")
    self._bot_button.configure(text="Stop Bot" if runtime_state.get("is_bot_running") else "Start Bot")
    self._pause_button.configure(state=tk.NORMAL if runtime_state.get("is_bot_running") else tk.DISABLED)
    self._resume_button.configure(
      state=tk.NORMAL if runtime_state.get("review_waiting") or runtime_state.get("pause_requested") else tk.DISABLED
    )
    self._continue_button.configure(state=tk.NORMAL if runtime_state.get("review_waiting") else tk.DISABLED)

    current_phase = runtime_state.get("phase")
    status = runtime_state.get("status")
    for phase, label in self._phase_labels.items():
      bg = "#192028"
      fg = "#9aa4ad"
      if phase == current_phase:
        bg = "#1f6feb" if status != "error" else "#b42318"
        fg = "white"
      label.configure(bg=bg, fg=fg)

    summary_payload = {
      "scenario": snapshot.get("scenario_name"),
      "turn": snapshot.get("turn_label"),
      "state_summary": state_summary,
      "selected_action": selected_action,
      "available_actions": snapshot.get("available_actions"),
      "reasoning_notes": snapshot.get("reasoning_notes"),
      "sub_phase": snapshot.get("sub_phase"),
      "execution_intent": snapshot.get("execution_intent") or runtime_state.get("execution_intent"),
      "backend_state": backend_state,
      "adb_health": {
        "available": adb_state.get("adb_available"),
        "connected": adb_state.get("adb_connected"),
        "device_ready": adb_state.get("device_ready"),
        "healthy": adb_state.get("healthy"),
        "last_error": adb_state.get("adb_last_error"),
      },
      "planned_clicks": snapshot.get("planned_clicks"),
    }
    self._set_text(self._summary_text, json.dumps(summary_payload, indent=2, ensure_ascii=True))
    self._set_text(self._timing_text, self._format_timing(state_summary))
    self._set_text(self._training_text, json.dumps(snapshot.get("ranked_trainings") or [], indent=2, ensure_ascii=True))
    inventory_payload = {
      "summary": state_summary.get("trackblazer_inventory_summary"),
      "controls": state_summary.get("trackblazer_inventory_controls"),
      "flow": state_summary.get("trackblazer_inventory_flow"),
      "items": snapshot.get("trackblazer_inventory"),
    }
    self._set_text(self._inventory_text, json.dumps(inventory_payload, indent=2, ensure_ascii=True))
    self._ocr_debug_entries = snapshot.get("ocr_debug") or []
    self._render_ocr_debug_entries()

  def _set_text(self, widget, value):
    widget.configure(state=tk.NORMAL)
    widget.delete("1.0", tk.END)
    widget.insert("1.0", value)
    widget.configure(state=tk.DISABLED)

  def _format_timing(self, state_summary):
    flow = state_summary.get("trackblazer_inventory_flow") or {}
    if not flow:
      return "No timing data"
    lines = []
    # Flow-level totals
    lines.append("=== Inventory Flow ===")
    for key in ("timing_open", "timing_scan", "timing_controls", "timing_close", "timing_total"):
      val = flow.get(key)
      if val is not None:
        label = key.replace("timing_", "")
        lines.append(f"  {label:10s} {val:.3f}s")
    # Open breakdown
    open_result = flow.get("open_result") or {}
    open_timing = open_result.get("timing") or {}
    if open_timing:
      lines.append("")
      lines.append("=== Open Breakdown ===")
      lines.extend(self._format_timing_mapping(open_timing))
    # Scan breakdown (from inventory scan info log)
    scan_timing = flow.get("scan_timing") or {}
    if scan_timing:
      lines.append("")
      lines.append("=== Scan Breakdown ===")
      lines.extend(self._format_timing_mapping(scan_timing))
    # Close breakdown
    close_result = flow.get("close_result") or {}
    close_timing = close_result.get("timing") or {}
    if close_timing:
      lines.append("")
      lines.append("=== Close Breakdown ===")
      lines.extend(self._format_timing_mapping(close_timing))
    return "\n".join(lines)

  def _format_timing_mapping(self, mapping, indent="  "):
    lines = []
    for key, val in mapping.items():
      lines.extend(self._format_timing_entry(key, val, indent=indent))
    return lines

  def _format_timing_entry(self, key, val, indent="  "):
    if isinstance(val, dict):
      lines = [f"{indent}{key}:"]
      lines.extend(self._format_timing_mapping(val, indent=indent + "  "))
      return lines
    if isinstance(val, bool):
      return [f"{indent}{key:14s} {val}"]
    if isinstance(val, float):
      return [f"{indent}{key:14s} {val:.4f}s"]
    if isinstance(val, int):
      return [f"{indent}{key:14s} {val}"]
    return [f"{indent}{key:14s} {val}"]

  def _copy_ocr_debug(self):
    if self._root is None:
      return
    value = json.dumps(self._ocr_debug_entries or [], indent=2, ensure_ascii=True)
    self._root.clipboard_clear()
    self._root.clipboard_append(value)
    self._message_value.set("Copied OCR debug to clipboard.")

  def _render_ocr_debug_entries(self):
    if self._ocr_debug_listbox is None:
      return
    current_selection = self._ocr_debug_listbox.curselection()
    selected_index = current_selection[0] if current_selection else 0
    self._ocr_debug_listbox.delete(0, tk.END)
    for idx, entry in enumerate(self._ocr_debug_entries):
      field = entry.get("field", f"entry_{idx}")
      score = entry.get("best_live_score")
      if score is None:
        score = entry.get("best_match_score")
      parsed = entry.get("parsed_value")
      label = field
      if score is not None:
        label = f"{field} [{score:.3f}]"
      elif parsed not in (None, "", []):
        label = f"{field} [{parsed}]"
      self._ocr_debug_listbox.insert(tk.END, label)

    if self._ocr_debug_entries:
      selected_index = min(selected_index, len(self._ocr_debug_entries) - 1)
      self._ocr_debug_listbox.selection_set(selected_index)
      self._ocr_debug_listbox.see(selected_index)
      self._render_ocr_debug_detail(self._ocr_debug_entries[selected_index])
    else:
      self._render_ocr_debug_detail({})

  def _on_ocr_debug_select(self, _event):
    if self._ocr_debug_listbox is None:
      return
    selection = self._ocr_debug_listbox.curselection()
    if not selection:
      return
    index = selection[0]
    if 0 <= index < len(self._ocr_debug_entries):
      self._render_ocr_debug_detail(self._ocr_debug_entries[index])

  def _render_ocr_debug_detail(self, entry):
    self._ocr_debug_region_asset_path = entry.get("template_image_path")
    self._ocr_debug_region_search_path = entry.get("search_image_path")
    self._set_preview_image(self._ocr_debug_asset_label, entry.get("template_image_path"), "No asset")
    self._set_preview_image(self._ocr_debug_region_label, entry.get("search_image_path"), "No region image", is_region=True)
    metadata = json.dumps(entry or {}, indent=2, ensure_ascii=True)
    self._set_text(self._ocr_debug_meta, metadata)

  def _set_preview_image(self, label, image_path, empty_text, is_region=False):
    if label is None:
      return
    photo_attr = "_ocr_debug_region_photo" if is_region else "_ocr_debug_asset_photo"
    if not image_path:
      setattr(self, photo_attr, None)
      label.configure(image="", text=empty_text)
      return

    file_path = Path(image_path)
    if not file_path.is_absolute():
      file_path = Path.cwd() / file_path
    if not file_path.exists():
      setattr(self, photo_attr, None)
      label.configure(image="", text=f"Missing:\n{image_path}")
      return

    try:
      image = Image.open(file_path).convert("RGBA")
      image.thumbnail((320, 260), Image.LANCZOS)
      photo = ImageTk.PhotoImage(image)
    except Exception:
      setattr(self, photo_attr, None)
      label.configure(image="", text=f"Failed to load:\n{image_path}")
      return

    setattr(self, photo_attr, photo)
    label.configure(image=photo, text="")

  def _open_preview_window(self, image_path, title):
    if self._root is None or not image_path:
      return

    file_path = Path(image_path)
    if not file_path.is_absolute():
      file_path = Path.cwd() / file_path
    if not file_path.exists():
      self._message_value.set(f"Missing preview image: {image_path}")
      return

    existing = self._preview_windows.get(title)
    if existing is not None:
      try:
        if existing.winfo_exists():
          existing.lift()
          return
      except Exception:
        pass

    try:
      image = Image.open(file_path).convert("RGBA")
    except Exception:
      self._message_value.set(f"Failed to open preview image: {image_path}")
      return

    window = tk.Toplevel(self._root)
    window.title(title)
    window.configure(bg="#101418")
    window.geometry("960x720")

    frame = tk.Frame(window, bg="#101418")
    frame.pack(fill=tk.BOTH, expand=True)

    canvas = tk.Canvas(frame, bg="#101418", highlightthickness=0)
    h_scroll = tk.Scrollbar(frame, orient=tk.HORIZONTAL, command=canvas.xview)
    v_scroll = tk.Scrollbar(frame, orient=tk.VERTICAL, command=canvas.yview)
    canvas.configure(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    v_scroll.grid(row=0, column=1, sticky="ns")
    h_scroll.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)

    photo = ImageTk.PhotoImage(image)
    canvas.create_image(0, 0, anchor="nw", image=photo)
    canvas.configure(scrollregion=(0, 0, image.width, image.height))
    window._preview_photo = photo

    info_label = tk.Label(
      window,
      text=f"{file_path.name}  {image.width}x{image.height}",
      fg="#d6dde5",
      bg="#101418",
      anchor="w",
      justify="left",
    )
    info_label.pack(fill=tk.X, padx=8, pady=(4, 8))

    self._preview_windows[title] = window
    window.bind("<Destroy>", lambda _event, key=title: self._preview_windows.pop(key, None))

  def _toggle_always_on_top(self):
    if self._root is None or self._always_on_top_var is None:
      return
    self._root.attributes("-topmost", bool(self._always_on_top_var.get()))

  def _copy_widget(self, widget):
    if self._root is None or widget is None:
      return
    value = widget.get("1.0", tk.END).strip()
    self._root.clipboard_clear()
    self._root.clipboard_append(value)
    self._message_value.set("Copied pane contents to clipboard.")

  def _set_execution_intent(self):
    if self._execution_intent_var is None:
      return
    bot.set_execution_intent(self._execution_intent_var.get())
    self.publish()

  def _toggle_skip_scenario_detection(self):
    if self._skip_scenario_detection_var is None:
      return
    enabled = bool(self._skip_scenario_detection_var.get())
    config.SKIP_SCENARIO_DETECTION = enabled
    if self._persist_config_value("skip_scenario_detection", enabled):
      self._message_value.set(f"Skip scenario detection {'enabled' if enabled else 'disabled'}.")
    self.publish()

  def _toggle_skip_full_stats_aptitude_check(self):
    if self._skip_full_stats_aptitude_check_var is None:
      return
    enabled = bool(self._skip_full_stats_aptitude_check_var.get())
    config.SKIP_FULL_STATS_APTITUDE_CHECK = enabled
    if self._persist_config_value("skip_full_stats_aptitude_check", enabled):
      self._message_value.set(f"Skip full stats/aptitude {'enabled' if enabled else 'disabled'}.")
    self.publish()

  def _load_window_geometry(self):
    self._window_geometry = self.DEFAULT_GEOMETRY
    try:
      with open("config.json", "r", encoding="utf-8") as file:
        config_data = json.load(file)
    except Exception:
      return

    debug_config = config_data.get("debug") or {}
    operator_console_config = debug_config.get("operator_console") or {}
    geometry = operator_console_config.get("window_geometry")
    if isinstance(geometry, str) and geometry.strip():
      self._window_geometry = geometry.strip()

  def _deferred_apply_geometry(self):
    if self._root is None:
      return
    geometry = self._window_geometry or self.DEFAULT_GEOMETRY
    self._root.geometry(geometry)

  def _apply_window_geometry(self):
    if self._root is None:
      return
    geometry = self._window_geometry or self.DEFAULT_GEOMETRY
    self._root.geometry(geometry)

  def _enable_geometry_persist(self):
    self._geometry_persist_ready = True

  def _save_and_test_position(self):
    if self._root is None:
      return
    geometry = self._root.geometry()
    saved = self._persist_config_value("debug.operator_console.window_geometry", geometry)
    if saved:
      self._last_saved_geometry = geometry
      self._message_value.set(f"Saved: {geometry}. Testing restore...")
      self._root.geometry("+0+0")
      self._root.after(600, lambda: self._test_restore_position(geometry))
    else:
      self._message_value.set("Failed to save position.")

  def _test_restore_position(self, geometry):
    if self._root is None:
      return
    self._root.geometry(geometry)
    self._message_value.set(f"Restored: {geometry}")

  def _on_window_configure(self, event):
    if self._root is None or event.widget is not self._root:
      return
    if not self._geometry_persist_ready:
      return
    if self._geometry_debounce_id is not None:
      self._root.after_cancel(self._geometry_debounce_id)
    self._geometry_debounce_id = self._root.after(500, self._persist_window_geometry)

  def _persist_window_geometry(self):
    self._geometry_debounce_id = None
    if self._root is None:
      return
    if str(self._root.state()) in {"withdrawn", "iconic"}:
      return

    geometry = self._root.geometry()
    if geometry == self._last_saved_geometry:
      return

    if self._persist_config_value("debug.operator_console.window_geometry", geometry):
      self._last_saved_geometry = geometry

  def _persist_config_value(self, key_path, value):
    try:
      with open("config.json", "r", encoding="utf-8") as file:
        config_data = json.load(file)
    except Exception:
      config_data = {}

    target = config_data
    path_parts = key_path.split(".")
    for key in path_parts[:-1]:
      target = target.setdefault(key, {})
    target[path_parts[-1]] = value

    try:
      with open("config.json", "w", encoding="utf-8") as file:
        json.dump(config_data, file, indent=2)
        file.write("\n")
      return True
    except Exception as exc:
      debug(f"Unable to persist config key '{key_path}': {exc}")
      return False

  def _launch_asset_creator(self):
    from core.region_adjuster.asset_creator import AssetCreatorWindow
    context = {}
    screenshot = None
    capture_bbox = None
    try:
      runtime_state = bot.get_runtime_state()
      snapshot = runtime_state.get("snapshot") or {}
      state_summary = snapshot.get("state_summary") or {}
      context["scenario"] = snapshot.get("scenario_name") or constants.SCENARIO_NAME or ""
      context["turn"] = snapshot.get("turn_label") or ""
      context["energy"] = snapshot.get("energy_label") or ""
      context["phase"] = runtime_state.get("phase") or ""
      action = (snapshot.get("selected_action") or {}).get("func")
      if action:
        context["action"] = action
      for key in ("mood", "speed", "stamina", "power", "guts", "int"):
        val = state_summary.get(key)
        if val:
          context[key] = str(val)
      game_window_bbox = tuple(int(v) for v in constants.GAME_WINDOW_BBOX)
      capture_bbox = game_window_bbox
      full_screenshot = device_action.screenshot()
      if full_screenshot is not None:
        screenshot = Image.fromarray(full_screenshot).convert("RGBA")
        context["capture_space"] = "game_window_bbox"
        context["game_window_bbox"] = str(game_window_bbox)
    except Exception:
      pass
    context = {k: v for k, v in context.items() if v}
    AssetCreatorWindow(parent=self._root, screenshot=screenshot, context=context, capture_bbox=capture_bbox)

  def _launch_adjuster(self):
    threading.Thread(target=self._launch_adjuster_background, daemon=True).start()

  def _toggle_bot(self):
    bot.invoke_control_callback("toggle_bot")
    self.publish()

  def _run_phase_check(self, callback_name):
    triggered = bot.invoke_control_callback(callback_name)
    if not triggered:
      self._message_value.set(f"Manual check callback '{callback_name}' is not registered.")

  def _request_pause(self):
    bot.request_pause()
    self.publish()

  def _resume_bot(self):
    bot.clear_pause_request()
    bot.end_review_wait()
    self.publish()

  def _continue_review(self):
    bot.end_review_wait()
    self.publish()

  def _launch_adjuster_background(self):
    try:
      config.reload_config(print_config=False)
      focus_target_window()
      settings = dict(config.REGION_ADJUSTER_CONFIG)
      settings["enabled"] = True
      success = run_region_adjuster_session(settings)
      if success:
        config.reload_config(print_config=False)
        _, _, overrides_path = resolve_region_adjuster_profiles(settings)
        constants.apply_region_overrides(
          overrides_path=overrides_path,
          force=True,
        )
    except Exception as exc:  # pragma: no cover
      debug(f"Operator console failed to launch region adjuster: {exc}")


def ensure_operator_console():
  if bot.operator_console is None:
    bot.operator_console = OperatorConsole()
    bot.operator_console.start()
  return bot.operator_console


def publish_runtime_state():
  console = bot.operator_console
  if console is not None:
    console.publish()
