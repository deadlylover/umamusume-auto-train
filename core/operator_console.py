import json
import queue
import tkinter as tk
from tkinter import scrolledtext, ttk
import threading
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageTk

import core.bot as bot
import core.config as config
from core.trackblazer_item_use import (
  ITEM_USE_BEHAVIOR_MODES,
  get_default_item_use_policy,
  get_default_training_behavior_settings,
  get_effective_item_use_items,
  get_training_behavior_settings,
  normalize_item_use_policy,
)
from core.trackblazer_shop import (
  PRIORITY_LEVELS,
  get_default_shop_policy,
  get_effective_shop_items,
  normalize_priority,
  normalize_shop_policy,
  policy_context,
)
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
  "post_action_resolution",
  "collecting_main_state",
  "checking_inventory",
  "checking_inventory_selection",
  "checking_shop",
  "checking_skill_purchase",
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
  "checking_inventory_selection": "check_inventory_selection",
  "checking_shop": "check_shop",
  "checking_skill_purchase": "check_skill_purchase",
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
    self._trackblazer_use_items_var = None
    self._skip_scenario_detection_var = None
    self._skip_full_stats_aptitude_check_var = None
    self._trackblazer_scoring_mode_var = None
    self._strong_training_score_threshold_var = None
    self._phase_labels = {}
    self._details_notebook = None
    self._planned_actions_text = None
    self._timing_text = None
    self._summary_raw_value = ""
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
    self._training_collapsed = True
    self._ocr_collapsed = True
    self._debug_history_collapsed = True
    self._debug_history_header = None
    self._debug_history_panel = None
    self._debug_history_toggle_label = None
    self._debug_history_text = None
    self._debug_history_rendered_count = 0
    self._training_header = None
    self._training_panel = None
    self._training_toggle_label = None
    self._ocr_header = None
    self._ocr_panel = None
    self._ocr_toggle_label = None
    self._right_pane = None
    self._history_entries = []  # ring buffer of {timestamp, turn_label, year, planned_text, timing_text, summary_raw}
    self._history_max = 5
    self._history_selected = "live"  # "live" or index string "0"-"4"
    self._history_last_turn = None  # track turn changes
    self._history_last_year = None
    self._history_last_planned_text = None
    self._history_last_timing_text = None
    self._history_last_summary_raw = None
    self._history_menu_button = None
    self._history_menu = None
    self._history_label_var = None
    self._planned_clicks_value = None
    self._would_use_value = None
    self._would_buy_value = None
    self._shop_policy_window = None
    self._shop_policy_rows = []
    self._shop_policy_context_var = None
    self._shop_policy_canvas = None
    self._shop_policy_body = None
    self._item_policy_window = None
    self._item_policy_rows = []
    self._item_policy_context_var = None
    self._item_policy_canvas = None
    self._item_policy_body = None
    self._stat_weights_window = None
    self._stat_weights_entries = {}
    self._bond_boost_var = None
    self._wit_gate_supports_var = None
    self._wit_gate_rainbows_var = None
    self._wit_gate_energy_var = None
    self._start_bot_button = None
    self._stop_bot_button = None
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
    tertiary_controls = tk.Frame(actions, bg="#101418")
    tertiary_controls.grid(row=2, column=0, sticky="w", pady=(6, 0))

    self._start_bot_button = tk.Button(primary_controls, text="Start Bot", command=self._start_bot)
    self._start_bot_button.pack(side=tk.LEFT, padx=(0, 8))
    self._stop_bot_button = tk.Button(primary_controls, text="Stop Bot", command=self._stop_bot)
    self._stop_bot_button.pack(side=tk.LEFT, padx=(0, 8))
    self._pause_button = tk.Button(primary_controls, text="Pause", command=self._request_pause)
    self._pause_button.pack(side=tk.LEFT, padx=(0, 8))
    self._resume_button = tk.Button(primary_controls, text="Resume", command=self._resume_bot)
    self._resume_button.pack(side=tk.LEFT, padx=(0, 8))
    self._continue_button = tk.Button(primary_controls, text="Continue (F2)", command=self._continue_review)
    self._continue_button.pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(primary_controls, text="Open OCR Adjuster", command=self._launch_adjuster).pack(side=tk.LEFT, padx=(0, 4))
    tk.Button(primary_controls, text="Asset Creator", command=self._launch_asset_creator).pack(side=tk.LEFT)
    tk.Button(primary_controls, text="Training", command=self._open_stat_weights_window).pack(side=tk.LEFT, padx=(8, 0))
    self._execution_intent_var = tk.StringVar(value=bot.get_execution_intent())
    for intent in ("check_only", "execute"):
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
    self._trackblazer_use_items_var = tk.BooleanVar(value=bot.get_trackblazer_use_items_enabled())
    self._skill_dry_run_var = tk.BooleanVar(value=bot.get_skill_dry_run_enabled())
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

    tk.Button(
      tertiary_controls,
      text="Test Use Items",
      command=lambda: self._run_phase_check("check_inventory_selection"),
    ).pack(side=tk.LEFT, padx=(0, 8))
    tk.Checkbutton(
      tertiary_controls,
      text="Use items",
      variable=self._trackblazer_use_items_var,
      command=self._toggle_trackblazer_use_items,
      fg="white",
      bg="#101418",
      selectcolor="#192028",
      activebackground="#101418",
      activeforeground="white",
    ).pack(side=tk.LEFT, padx=(0, 8))
    tk.Label(
      tertiary_controls,
      text="Off = dry-run and close inventory. On = click first confirm-use scaffold.",
      fg="#9aa4ad",
      bg="#101418",
    ).pack(side=tk.LEFT)
    tk.Checkbutton(
      tertiary_controls,
      text="Dry-run skills",
      variable=self._skill_dry_run_var,
      command=self._toggle_skill_dry_run,
      fg="white",
      bg="#101418",
      selectcolor="#192028",
      activebackground="#101418",
      activeforeground="white",
    ).pack(side=tk.LEFT, padx=(12, 0))
    tk.Button(
      tertiary_controls,
      text="Item Policy",
      command=self._open_trackblazer_item_policy_window,
    ).pack(side=tk.LEFT, padx=(12, 0))
    tk.Button(
      tertiary_controls,
      text="Shop Policy",
      command=self._open_trackblazer_shop_policy_window,
    ).pack(side=tk.LEFT, padx=(12, 0))
    tk.Label(
      tertiary_controls,
      text="Scoring:",
      fg="#9aa4ad",
      bg="#101418",
    ).pack(side=tk.LEFT, padx=(12, 0))
    self._trackblazer_scoring_mode_var = tk.StringVar(value=bot.get_trackblazer_scoring_mode())
    for mode_val, mode_label in (("legacy", "legacy"), ("stat_focused", "stat focused")):
      tk.Radiobutton(
        tertiary_controls,
        text=mode_label,
        value=mode_val,
        variable=self._trackblazer_scoring_mode_var,
        command=self._set_trackblazer_scoring_mode,
        fg="white",
        bg="#101418",
        selectcolor="#192028",
        activebackground="#101418",
        activeforeground="white",
      ).pack(side=tk.LEFT, padx=(4, 0))
    left = tk.LabelFrame(root, text="Flow", fg="white", bg="#101418", padx=6, pady=6)
    left.grid(row=2, column=0, sticky="nsew", padx=(8, 4), pady=6)
    right = tk.Frame(root, bg="#101418")
    right.grid(row=2, column=1, sticky="nsew", padx=(4, 8), pady=6)
    right.columnconfigure(0, weight=1)
    right.rowconfigure(2, weight=1)
    right.rowconfigure(4, weight=1)
    right.rowconfigure(6, weight=3)
    right.rowconfigure(8, weight=0)
    self._right_pane = right

    quick_bar = tk.Frame(right, bg="#192028", padx=6, pady=3)
    quick_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
    quick_bar.columnconfigure(1, weight=1)
    quick_bar.columnconfigure(3, weight=0)
    quick_bar.columnconfigure(5, weight=0)
    tk.Label(quick_bar, text="Clicks:", fg="#8a949e", bg="#192028", font=("Helvetica", 9)).grid(row=0, column=0, sticky="w")
    self._planned_clicks_value = tk.Label(
      quick_bar, text="-", fg="#d6dde5", bg="#192028",
      anchor="w", justify="left", font=("Helvetica", 9),
    )
    self._planned_clicks_value.grid(row=0, column=1, sticky="w", padx=(4, 12))
    tk.Label(quick_bar, text="Use:", fg="#8a949e", bg="#192028", font=("Helvetica", 9)).grid(row=0, column=2, sticky="w")
    self._would_use_value = tk.Label(
      quick_bar, text="-", fg="#d6dde5", bg="#192028",
      anchor="w", justify="left", font=("Helvetica", 9),
    )
    self._would_use_value.grid(row=0, column=3, sticky="w", padx=(4, 12))
    tk.Label(quick_bar, text="Buy:", fg="#8a949e", bg="#192028", font=("Helvetica", 9)).grid(row=0, column=4, sticky="w")
    self._would_buy_value = tk.Label(
      quick_bar, text="-", fg="#d6dde5", bg="#192028",
      anchor="w", justify="left", font=("Helvetica", 9),
    )
    self._would_buy_value.grid(row=0, column=5, sticky="w", padx=(4, 0))

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
    summary_header.grid(row=1, column=0, sticky="ew")
    summary_header.columnconfigure(0, weight=1)
    summary_header.columnconfigure(1, weight=0)
    summary_header.columnconfigure(2, weight=0)
    summary_header.columnconfigure(3, weight=0)
    summary_header.columnconfigure(4, weight=1)
    tk.Label(summary_header, text="Planned Actions / Timing", fg="white", bg="#101418", anchor="w").grid(row=0, column=0, sticky="w")
    self._history_label_var = tk.StringVar(value="\u25be Live")
    self._history_menu_button = tk.Menubutton(
      summary_header, textvariable=self._history_label_var,
      fg="#7cb3ff", bg="#101418", activebackground="#192028", activeforeground="white",
      relief=tk.FLAT, cursor="hand2", anchor="w",
    )
    self._history_menu_button.grid(row=0, column=1, sticky="w", padx=(8, 4))
    self._history_menu = tk.Menu(
      self._history_menu_button, tearoff=0, bg="#192028", fg="#d6dde5",
      activebackground="#1f6feb", activeforeground="white",
    )
    self._history_menu_button["menu"] = self._history_menu
    self._rebuild_history_menu()
    tk.Button(summary_header, text="Copy Planned", command=lambda: self._copy_widget(self._planned_actions_text)).grid(row=0, column=2, sticky="e", padx=(0, 4))
    tk.Button(summary_header, text="Copy Timing", command=lambda: self._copy_widget(self._timing_text)).grid(row=0, column=3, sticky="e", padx=(0, 12))
    tk.Button(summary_header, text="Copy Summary", command=self._copy_active_summary).grid(row=0, column=4, sticky="e")
    details_container = tk.Frame(right, bg="#101418")
    details_container.grid(row=2, column=0, sticky="nsew", pady=(2, 6))
    details_container.rowconfigure(0, weight=1)
    details_container.columnconfigure(0, weight=1)
    self._details_notebook = ttk.Notebook(details_container)
    self._details_notebook.grid(row=0, column=0, sticky="nsew")
    planned_actions_frame = tk.Frame(self._details_notebook, bg="#101418")
    planned_actions_frame.rowconfigure(0, weight=1)
    planned_actions_frame.columnconfigure(0, weight=1)
    self._planned_actions_text = scrolledtext.ScrolledText(
      planned_actions_frame,
      height=8,
      width=self.NARROW_TEXT_WIDTH,
      bg="#192028",
      fg="#d6dde5",
      insertbackground="white",
    )
    self._planned_actions_text.grid(row=0, column=0, sticky="nsew")
    timing_frame = tk.Frame(self._details_notebook, bg="#101418")
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
    self._details_notebook.add(planned_actions_frame, text="Planned Actions")
    self._details_notebook.add(timing_frame, text="Timing")
    self._details_notebook.select(planned_actions_frame)

    training_header = tk.Frame(right, bg="#101418")
    training_header.grid(row=3, column=0, sticky="ew")
    training_header.columnconfigure(0, weight=0)
    training_header.columnconfigure(1, weight=1)
    training_header.columnconfigure(2, weight=0)
    training_header.columnconfigure(3, weight=1)
    training_header.columnconfigure(4, weight=0)
    self._training_toggle_label = tk.Label(training_header, text="\u25bc Ranked Trainings", fg="white", bg="#101418", anchor="w", cursor="hand2")
    self._training_toggle_label.grid(row=0, column=0, sticky="w")
    self._training_toggle_label.bind("<Button-1>", lambda _e: self._toggle_training_section())
    tk.Button(training_header, text="Copy Trainings", command=lambda: self._copy_widget(self._training_text)).grid(row=0, column=1, sticky="e", padx=(0, 12))
    tk.Label(training_header, text="Inventory", fg="white", bg="#101418", anchor="w").grid(row=0, column=2, sticky="w")
    tk.Button(training_header, text="Copy Inventory", command=lambda: self._copy_widget(self._inventory_text)).grid(row=0, column=3, sticky="e")
    self._training_header = training_header
    training_panel = tk.PanedWindow(right, orient=tk.HORIZONTAL, sashrelief=tk.RAISED, bg="#101418")
    training_panel.grid(row=4, column=0, sticky="nsew", pady=(2, 6))
    self._training_panel = training_panel
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
    ocr_header.grid(row=5, column=0, sticky="ew")
    ocr_header.columnconfigure(0, weight=0)
    ocr_header.columnconfigure(1, weight=1)
    self._ocr_toggle_label = tk.Label(ocr_header, text="\u25bc OCR Debug", fg="white", bg="#101418", anchor="w", cursor="hand2")
    self._ocr_toggle_label.grid(row=0, column=0, sticky="w")
    self._ocr_toggle_label.bind("<Button-1>", lambda _e: self._toggle_ocr_section())
    tk.Button(ocr_header, text="Copy OCR Debug", command=self._copy_ocr_debug).grid(row=0, column=1, sticky="e")
    self._ocr_header = ocr_header
    ocr_panel = tk.PanedWindow(right, orient=tk.HORIZONTAL, sashrelief=tk.RAISED, bg="#101418")
    ocr_panel.grid(row=6, column=0, sticky="nsew", pady=(2, 6))
    self._ocr_panel = ocr_panel

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

    # Debug History collapsible section
    debug_history_header = tk.Frame(right, bg="#101418")
    debug_history_header.grid(row=7, column=0, sticky="ew")
    debug_history_header.columnconfigure(0, weight=0)
    debug_history_header.columnconfigure(1, weight=1)
    debug_history_header.columnconfigure(2, weight=0)
    self._debug_history_toggle_label = tk.Label(debug_history_header, text="\u25bc Debug History", fg="white", bg="#101418", anchor="w", cursor="hand2")
    self._debug_history_toggle_label.grid(row=0, column=0, sticky="w")
    self._debug_history_toggle_label.bind("<Button-1>", lambda _e: self._toggle_debug_history_section())
    tk.Button(debug_history_header, text="Copy History", command=self._copy_debug_history).grid(row=0, column=1, sticky="e", padx=(0, 4))
    tk.Button(debug_history_header, text="Clear", command=self._clear_debug_history).grid(row=0, column=2, sticky="e")
    self._debug_history_header = debug_history_header
    debug_history_panel = tk.Frame(right, bg="#101418")
    debug_history_panel.grid(row=8, column=0, sticky="nsew", pady=(2, 6))
    debug_history_panel.rowconfigure(0, weight=1)
    debug_history_panel.columnconfigure(0, weight=1)
    self._debug_history_text = scrolledtext.ScrolledText(
      debug_history_panel,
      height=8,
      bg="#192028",
      fg="#d6dde5",
      insertbackground="white",
    )
    self._debug_history_text.grid(row=0, column=0, sticky="nsew")
    self._debug_history_panel = debug_history_panel

    tk.Label(right, textvariable=self._message_value, fg="#8bd5ca", bg="#101418", anchor="w", justify="left", wraplength=430).grid(row=9, column=0, sticky="ew")
    tk.Label(right, textvariable=self._error_value, fg="#ff8c8c", bg="#101418", anchor="w", justify="left", wraplength=430).grid(row=10, column=0, sticky="ew")
    if self._training_collapsed:
      self._training_toggle_label.configure(text="\u25b6 Ranked Trainings")
      self._training_panel.grid_remove()
    if self._ocr_collapsed:
      self._ocr_toggle_label.configure(text="\u25b6 OCR Debug")
      self._ocr_panel.grid_remove()
    if self._debug_history_collapsed:
      self._debug_history_toggle_label.configure(text="\u25b6 Debug History")
      self._debug_history_panel.grid_remove()
    self._rebalance_right_pane_weights()

  def _make_stat(self, parent, column, title):
    row = 0 if column < 5 else 1
    actual_column = column if column < 5 else column - 5
    frame = tk.Frame(parent, bg="#151b22", padx=6, pady=3)
    frame.grid(row=row, column=actual_column, sticky="ew", padx=2, pady=2)
    tk.Label(frame, text=title, fg="#8a949e", bg="#151b22", font=("Helvetica", 9)).pack(anchor="w")
    value = tk.StringVar(value="-")
    tk.Label(frame, textvariable=value, fg="white", bg="#151b22", font=("Helvetica", 10, "bold")).pack(anchor="w")
    return value

  def _rebalance_right_pane_weights(self):
    right = self._right_pane
    if right is None:
      return
    training_weight = 0 if self._training_collapsed else 1
    ocr_weight = 0 if self._ocr_collapsed else 3
    debug_history_weight = 0 if self._debug_history_collapsed else 2
    collapsed_reclaim = (1 if self._training_collapsed else 0) + (3 if self._ocr_collapsed else 0) + (2 if self._debug_history_collapsed else 0)
    planned_weight = 4 + collapsed_reclaim
    right.rowconfigure(2, weight=planned_weight)
    right.rowconfigure(4, weight=training_weight)
    right.rowconfigure(6, weight=ocr_weight)
    right.rowconfigure(8, weight=debug_history_weight)

  def _toggle_training_section(self):
    self._training_collapsed = not self._training_collapsed
    if self._training_collapsed:
      self._training_toggle_label.configure(text="\u25b6 Ranked Trainings")
      self._training_panel.grid_remove()
    else:
      self._training_toggle_label.configure(text="\u25bc Ranked Trainings")
      self._training_panel.grid()
    self._rebalance_right_pane_weights()

  def _toggle_ocr_section(self):
    self._ocr_collapsed = not self._ocr_collapsed
    if self._ocr_collapsed:
      self._ocr_toggle_label.configure(text="\u25b6 OCR Debug")
      self._ocr_panel.grid_remove()
    else:
      self._ocr_toggle_label.configure(text="\u25bc OCR Debug")
      self._ocr_panel.grid()
    self._rebalance_right_pane_weights()

  def _toggle_debug_history_section(self):
    self._debug_history_collapsed = not self._debug_history_collapsed
    if self._debug_history_collapsed:
      self._debug_history_toggle_label.configure(text="\u25b6 Debug History")
      self._debug_history_panel.grid_remove()
    else:
      self._debug_history_toggle_label.configure(text="\u25bc Debug History")
      self._debug_history_panel.grid()
      self._render_debug_history()
    self._rebalance_right_pane_weights()

  def _copy_debug_history(self):
    if self._debug_history_text is None:
      return
    value = self._debug_history_text.get("1.0", tk.END).strip()
    if not value:
      value = "(empty)"
    self._root.clipboard_clear()
    self._root.clipboard_append(value)
    self._message_value.set("Copied debug history to clipboard.")

  def _clear_debug_history(self):
    import core.bot as _bot
    _bot.clear_debug_history()
    self._debug_history_rendered_count = 0
    if self._debug_history_text:
      self._debug_history_text.configure(state=tk.NORMAL)
      self._debug_history_text.delete("1.0", tk.END)
      self._debug_history_text.configure(state=tk.DISABLED)

  def _render_debug_history(self):
    """Render debug history entries into the text widget (append-only)."""
    import core.bot as _bot
    if self._debug_history_text is None:
      return
    history = _bot.get_debug_history()
    new_count = len(history)
    if new_count <= self._debug_history_rendered_count:
      return
    new_entries = history[self._debug_history_rendered_count:]
    self._debug_history_rendered_count = new_count
    self._debug_history_text.configure(state=tk.NORMAL)
    import datetime
    for entry in new_entries:
      ts = entry.get("_ts", 0)
      ts_str = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "??:??:??"
      event = entry.get("event", "?")
      asset = entry.get("asset", "")
      result = entry.get("result", "")
      context = entry.get("context", "")
      turn_label = entry.get("turn_label", "")
      action = entry.get("action", "")
      phase = entry.get("phase", "")
      sub_phase = entry.get("sub_phase", "")
      line = f"[{ts_str}] {event}: {asset}"
      if result:
        line += f" -> {result}"
      if context:
        line += f"  ({context})"
      details = []
      note = entry.get("note", "")
      backend = entry.get("backend", "")
      target = entry.get("target")
      resolved_click_point = entry.get("resolved_click_point")
      clicks_requested = entry.get("clicks_requested")
      clicks_completed = entry.get("clicks_completed")
      total = entry.get("total")
      if note:
        details.append(str(note))
      if backend:
        details.append(f"backend={backend}")
      if resolved_click_point is not None:
        details.append(f"point={resolved_click_point}")
      elif target is not None:
        details.append(f"target={target}")
      if clicks_completed is not None:
        details.append(f"done={clicks_completed}")
      elif clicks_requested is not None:
        details.append(f"req={clicks_requested}")
      if total is not None:
        details.append(f"total={total}s")
      if details:
        line += " [" + " | ".join(details) + "]"
      metadata = []
      if turn_label:
        metadata.append(f"turn={turn_label}")
      if action:
        metadata.append(f"action={action}")
      if phase:
        metadata.append(f"phase={phase}")
      if sub_phase and sub_phase != phase:
        metadata.append(f"sub={sub_phase}")
      if metadata:
        line += " [" + " | ".join(metadata) + "]"
      line += "\n"
      self._debug_history_text.insert(tk.END, line)
    self._debug_history_text.see(tk.END)
    self._debug_history_text.configure(state=tk.DISABLED)

  def _update_quick_bar(self, snapshot):
    planned = snapshot.get("planned_actions") or {}
    # Planned clicks
    planned_clicks = snapshot.get("planned_clicks") or []
    if planned_clicks:
      labels = []
      for click in planned_clicks:
        if isinstance(click, dict):
          labels.append(click.get("label") or "click")
      clicks_text = " \u2192 ".join(labels) if labels else "-"
    else:
      clicks_text = "-"
    if self._planned_clicks_value is not None:
      self._planned_clicks_value.configure(text=clicks_text)
    # Would use
    would_use = planned.get("would_use") or []
    use_text = self._format_candidate_list(would_use) if would_use else "-"
    if self._would_use_value is not None:
      self._would_use_value.configure(text=use_text)
    # Would buy
    would_buy = planned.get("would_buy") or []
    buy_text = self._format_shop_buy_list(would_buy) if would_buy else "-"
    if self._would_buy_value is not None:
      self._would_buy_value.configure(text=buy_text)

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
    self._sub_phase_value.set(snapshot.get("sub_phase") or runtime_state.get("sub_phase") or "-")
    self._intent_value.set(runtime_state.get("execution_intent") or snapshot.get("execution_intent") or "-")
    self._backend_value.set(backend_state.get("active_backend") or "-")
    self._device_value.set(backend_state.get("device_id") or "-")
    if self._execution_intent_var is not None:
      self._execution_intent_var.set(runtime_state.get("execution_intent") or "execute")
    if self._trackblazer_use_items_var is not None:
      self._trackblazer_use_items_var.set(bool(runtime_state.get("trackblazer_use_items_enabled")))
    if self._trackblazer_scoring_mode_var is not None:
      self._trackblazer_scoring_mode_var.set(runtime_state.get("trackblazer_scoring_mode") or "stat_focused")
    if self._skill_dry_run_var is not None:
      self._skill_dry_run_var.set(bool(runtime_state.get("skill_dry_run_enabled")))
    self._message_value.set(runtime_state.get("message") or "")
    self._error_value.set(runtime_state.get("error") or "")
    is_bot_running = bool(runtime_state.get("is_bot_running"))
    self._start_bot_button.configure(state=tk.DISABLED if is_bot_running else tk.NORMAL)
    self._stop_bot_button.configure(state=tk.NORMAL if is_bot_running else tk.DISABLED)
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
      "trackblazer_use_items_enabled": runtime_state.get("trackblazer_use_items_enabled"),
      "available_actions": snapshot.get("available_actions"),
      "reasoning_notes": snapshot.get("reasoning_notes"),
      "sub_phase": snapshot.get("sub_phase"),
      "execution_intent": snapshot.get("execution_intent") or runtime_state.get("execution_intent"),
      "post_action_resolution": runtime_state.get("post_action_resolution") or {},
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
    self._summary_raw_value = json.dumps(summary_payload, indent=2, ensure_ascii=True, default=str)
    planned_text = self._format_planned_actions(snapshot)
    timing_text = self._format_timing(snapshot)
    self._update_history_on_render(snapshot, planned_text, timing_text)
    if self._history_selected == "live":
      self._set_text(self._planned_actions_text, planned_text)
      self._set_text(self._timing_text, timing_text)
    self._update_quick_bar(snapshot)
    self._set_text(self._training_text, json.dumps(snapshot.get("ranked_trainings") or [], indent=2, ensure_ascii=True, default=str))
    inventory_payload = {
      "summary": state_summary.get("trackblazer_inventory_summary"),
      "controls": state_summary.get("trackblazer_inventory_controls"),
      "flow": state_summary.get("trackblazer_inventory_flow"),
      "items": snapshot.get("trackblazer_inventory"),
      "shop_summary": state_summary.get("trackblazer_shop_summary"),
      "shop_flow": state_summary.get("trackblazer_shop_flow"),
      "shop_items": snapshot.get("trackblazer_shop_items"),
      "skill_purchase_flow": state_summary.get("skill_purchase_flow"),
      "shop_policy_context": policy_context(
        year=state_summary.get("year"),
        turn=state_summary.get("turn"),
      ),
      "shop_priority_preview": [
        {
          "name": item.get("display_name"),
          "priority": item.get("effective_priority"),
          "max_quantity": item.get("max_quantity"),
          "cost": item.get("cost"),
          "asset_collected": item.get("asset_collected"),
        }
        for item in get_effective_shop_items(
          policy=getattr(config, "TRACKBLAZER_SHOP_POLICY", None),
          year=state_summary.get("year"),
          turn=state_summary.get("turn"),
        )[:12]
      ],
      "item_use_priority_preview": [
        {
          "name": item.get("display_name"),
          "priority": item.get("effective_priority"),
          "reserve_quantity": item.get("reserve_quantity"),
          "usage_group": item.get("usage_group"),
          "target_training": item.get("target_training"),
          "asset_collected": item.get("asset_collected"),
        }
        for item in get_effective_item_use_items(
          policy=getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None),
          year=state_summary.get("year"),
          turn=state_summary.get("turn"),
        )[:12]
      ],
    }
    self._set_text(self._inventory_text, json.dumps(inventory_payload, indent=2, ensure_ascii=True, default=str))
    self._ocr_debug_entries = snapshot.get("ocr_debug") or []
    self._render_ocr_debug_entries()
    if not self._debug_history_collapsed:
      self._render_debug_history()

  def _set_text(self, widget, value):
    widget.configure(state=tk.NORMAL)
    widget.delete("1.0", tk.END)
    widget.insert("1.0", value)
    widget.configure(state=tk.DISABLED)

  def _format_timing(self, snapshot):
    state_summary = snapshot.get("state_summary") or {}
    sub_phase = snapshot.get("sub_phase") or ""
    if sub_phase == "manual_skill_purchase_check":
      flow = state_summary.get("skill_purchase_flow") or {}
      title = "Skill Purchase Flow"
    elif sub_phase == "manual_shop_check":
      flow = state_summary.get("trackblazer_shop_flow") or {}
      title = "Shop Flow"
    elif sub_phase in ("manual_inventory_check", "manual_inventory_selection_test"):
      flow = state_summary.get("trackblazer_inventory_flow") or {}
      title = "Inventory Flow"
    else:
      flow = state_summary.get("skill_purchase_flow") or {}
      title = "Skill Purchase Flow"
      if not flow:
        flow = state_summary.get("trackblazer_inventory_flow") or {}
        title = "Inventory Flow"
      if not flow:
        flow = state_summary.get("trackblazer_shop_flow") or {}
        title = "Shop Flow"
    if not flow:
      return "No timing data"
    lines = []
    # Flow-level totals
    lines.append(f"=== {title} ===")
    for key in ("timing_open", "timing_scan", "timing_controls", "timing_close", "timing_total"):
      val = flow.get(key)
      if val is not None:
        label = key.replace("timing_", "")
        lines.append(f"  {label:10s} {val:.3f}s")
    for key in ("timing_reset_swipes", "timing_forward_swipes"):
      val = flow.get(key)
      if val is not None:
        label = key.replace("timing_", "")
        lines.append(f"  {label:10s} {val:.3f}s")
    # Open breakdown
    open_result = flow.get("open_result") or flow.get("entry_result") or {}
    open_timing = open_result.get("timing") or {}
    if open_timing:
      lines.append("")
      lines.append("=== Open Breakdown ===")
      lines.extend(self._format_timing_mapping(open_timing))
    # Scan breakdown (from inventory scan info log)
    scan_timing = flow.get("scan_timing") or ((flow.get("scan_result") or {}).get("flow") if isinstance(flow.get("scan_result"), dict) else {}) or {}
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

  def _format_planned_actions(self, snapshot):
    planned = snapshot.get("planned_actions") or {}
    lines = []
    turn_label = snapshot.get("turn_label") or "?"
    state_summary = snapshot.get("state_summary") or {}
    year_label = state_summary.get("year") or turn_label
    lines.append("Turn Discussion")
    lines.append(f"Paste this back and we can discuss this turn. Year: {year_label}.")
    lines.append("")
    compact_summary = self._format_compact_summary(snapshot, include_prompt=False)
    if compact_summary:
      lines.append(compact_summary)
    if planned:
      lines.append("")
      lines.append("Planned Actions")
      lines.extend(self._format_planned_action_sections(planned))
    else:
      lines.append("")
      lines.append("Planned Actions")
      lines.append("  No planned actions yet")
    planned_clicks = snapshot.get("planned_clicks") or []
    if planned_clicks:
      lines.append("")
      lines.append("Planned Clicks")
      lines.extend(self._format_planned_clicks(planned_clicks))
    return "\n".join(lines).strip()

  def _copy_active_summary(self):
    if self._root is None:
      return
    self._root.clipboard_clear()
    self._root.clipboard_append(self._summary_raw_value or "")
    self._message_value.set("Copied raw summary to clipboard.")

  # --- History ---

  def _push_history(self, turn_label, year, planned_text, timing_text, summary_raw):
    """Save the current turn's planned actions into the history ring buffer."""
    if not planned_text or not planned_text.strip():
      return
    entry = {
      "timestamp": datetime.now().strftime("%H:%M:%S"),
      "turn_label": turn_label or "?",
      "year": year or "",
      "planned_text": planned_text,
      "timing_text": timing_text or "",
      "summary_raw": summary_raw or "",
    }
    self._history_entries.insert(0, entry)
    if len(self._history_entries) > self._history_max:
      self._history_entries = self._history_entries[: self._history_max]
    # If viewing history, shift index to keep showing the same entry
    if self._history_selected != "live":
      try:
        idx = int(self._history_selected)
        self._history_selected = str(idx + 1)
        if idx + 1 >= len(self._history_entries):
          self._select_history("live")
      except ValueError:
        self._select_history("live")
    self._rebuild_history_menu()

  def _rebuild_history_menu(self):
    """Rebuild the history dropdown menu entries."""
    menu = self._history_menu
    if menu is None:
      return
    menu.delete(0, tk.END)
    menu.add_command(
      label="\u25b6 Live",
      command=lambda: self._select_history("live"),
    )
    if self._history_entries:
      menu.add_separator()
    for i, entry in enumerate(self._history_entries):
      year_part = f" {entry['year']}" if entry["year"] else ""
      label = f"{entry['timestamp']}  Turn {entry['turn_label']}{year_part}"
      menu.add_command(
        label=label,
        command=lambda idx=str(i): self._select_history(idx),
      )

  def _select_history(self, key):
    """Select live view or a history entry by index string."""
    self._history_selected = key
    if key == "live":
      self._history_label_var.set("\u25be Live")
      # Restore live content — will be refreshed on next render
      if self._history_last_planned_text is not None:
        self._set_text(self._planned_actions_text, self._history_last_planned_text)
      if self._history_last_timing_text is not None:
        self._set_text(self._timing_text, self._history_last_timing_text)
    else:
      try:
        idx = int(key)
        entry = self._history_entries[idx]
      except (ValueError, IndexError):
        self._select_history("live")
        return
      year_part = f" {entry['year']}" if entry["year"] else ""
      self._history_label_var.set(f"\u25be {entry['timestamp']} T{entry['turn_label']}{year_part}")
      self._set_text(self._planned_actions_text, entry["planned_text"])
      self._set_text(self._timing_text, entry["timing_text"])

  def _update_history_on_render(self, snapshot, planned_text, timing_text):
    """Check if the turn changed and push previous turn to history if so."""
    turn_label = snapshot.get("turn_label") or ""
    state_summary = snapshot.get("state_summary") or {}
    year = state_summary.get("year") or ""
    # Detect turn change — push old content to history
    if self._history_last_turn is not None and turn_label and turn_label != self._history_last_turn:
      self._push_history(
        self._history_last_turn,
        self._history_last_year,
        self._history_last_planned_text,
        self._history_last_timing_text,
        self._history_last_summary_raw,
      )
    # Only update cached content when we have a real turn (non-empty turn_label).
    # Scanning-lobby snapshots have turn_label="" and would overwrite the full
    # planned-actions text, causing history to store the empty scanning state.
    if turn_label:
      self._history_last_turn = turn_label
      self._history_last_year = year
      self._history_last_planned_text = planned_text
      self._history_last_timing_text = timing_text
      self._history_last_summary_raw = self._summary_raw_value

  def _format_compact_summary(self, snapshot, include_prompt=True):
    state_summary = snapshot.get("state_summary") or {}
    selected_action = snapshot.get("selected_action") or {}
    ranked_trainings = snapshot.get("ranked_trainings") or []
    planned = snapshot.get("planned_actions") or {}
    lines = []

    if include_prompt:
      lines.append("Compact Turn Summary")
      lines.append("Use this for quick back-and-forth turn review.")
      lines.append("")

    lines.append(
      "Turn: "
      f"{snapshot.get('turn_label') or '?'}"
      f" | Scenario: {snapshot.get('scenario_name') or '-'}"
      f" | Intent: {snapshot.get('execution_intent') or '-'}"
    )
    lines.append(
      "State: "
      f"mood {state_summary.get('current_mood') or '-'}"
      f" | energy {self._format_ratio(state_summary.get('energy_level'), state_summary.get('max_energy'))}"
      f" | backend {state_summary.get('control_backend') or '-'}"
    )

    stats_line = self._format_current_stats_line(state_summary)
    if stats_line:
      lines.append(stats_line)

    criteria = state_summary.get("criteria")
    if criteria:
      lines.append(f"Criteria: {criteria}")

    lines.append(self._format_selected_action_line(selected_action))

    rival_line = self._format_rival_line(selected_action, state_summary)
    if rival_line:
      lines.append(rival_line)

    race_check_line = self._format_race_check_line(planned)
    if race_check_line:
      lines.append(race_check_line)

    race_gate_lines = self._format_trackblazer_race_lines(selected_action)
    if race_gate_lines:
      lines.extend(race_gate_lines)

    race_entry_lines = self._format_trackblazer_race_entry_lines(planned)
    if race_entry_lines:
      lines.extend(race_entry_lines)

    training_lines = self._format_training_lines(ranked_trainings, selected_action)
    if training_lines:
      lines.append("")
      lines.append("Trainings")
      lines.extend(training_lines)

    inventory_lines = self._format_inventory_lines(planned)
    if inventory_lines:
      lines.append("")
      lines.append("Inventory")
      lines.extend(inventory_lines)

    shop_lines = self._format_shop_lines(planned, state_summary)
    if shop_lines:
      lines.append("")
      lines.append("Shop")
      lines.extend(shop_lines)

    timing_lines = self._format_compact_timing_lines(state_summary)
    if timing_lines:
      lines.append("")
      lines.append("Timing")
      lines.extend(timing_lines)

    notes = snapshot.get("reasoning_notes")
    if notes:
      lines.append("")
      lines.append(f"Notes: {notes}")

    return "\n".join(lines).strip()

  def _format_current_stats_line(self, state_summary):
    current_stats = state_summary.get("current_stats")
    if not current_stats or not isinstance(current_stats, dict):
      return None
    stat_order = ["spd", "sta", "pwr", "guts", "wit", "sp"]
    parts = []
    for key in stat_order:
      val = current_stats.get(key)
      if val is None or val == -1:
        parts.append(f"{key}:?")
      else:
        parts.append(f"{key}:{val}")
    return "Stats: " + " | ".join(parts)

  def _format_selected_action_line(self, selected_action):
    action_name = selected_action.get("func") or "-"
    pre_action_items = selected_action.get("pre_action_item_use") or []
    pre_action_label = ""
    if pre_action_items:
      item_names = ", ".join(entry.get("name") or entry.get("key") or "item" for entry in pre_action_items)
      if selected_action.get("reassess_after_item_use"):
        pre_action_label = f"Action: use {item_names} -> recheck trainings"
      else:
        pre_action_label = f"Action: use {item_names} -> "
    if action_name == "do_training":
      training_name = selected_action.get("training_name") or "unknown"
      parts = [f"Action: train {training_name}"]
      score_tuple = selected_action.get("score_tuple") or ()
      if score_tuple:
        parts.append(f"score {self._format_number(score_tuple[0], digits=3)}")
      gains = selected_action.get("stat_gains") or {}
      total_gain = self._sum_visible_training_gains(gains)
      parts.extend(self._format_training_gain_parts(training_name, gains))
      if total_gain:
        parts.append(f"total+{total_gain}")
      rainbows = selected_action.get("total_rainbow_friends")
      supports = selected_action.get("total_supports")
      if rainbows is not None:
        parts.append(f"rainbows {rainbows}")
      if supports is not None:
        parts.append(f"supports {supports}")
      failure = selected_action.get("failure")
      if failure is not None:
        parts.append(f"fail {self._format_number(failure)}%")
      action_text = " | ".join(parts)
      return pre_action_label + action_text if pre_action_label and not selected_action.get("reassess_after_item_use") else (pre_action_label or action_text)
    if action_name == "do_race":
      race_name = selected_action.get("race_name") or "unspecified"
      action_text = f"Action: race {race_name}"
      return pre_action_label + action_text if pre_action_label and not selected_action.get("reassess_after_item_use") else (pre_action_label or action_text)
    action_text = f"Action: {action_name}"
    return pre_action_label + action_text if pre_action_label and not selected_action.get("reassess_after_item_use") else (pre_action_label or action_text)

  def _format_rival_line(self, selected_action, state_summary):
    rival_scout = selected_action.get("rival_scout") or {}
    if isinstance(rival_scout, dict) and rival_scout:
      rival_found = rival_scout.get("rival_found")
      if rival_found is True:
        return "Race: rival race found"
      if rival_found is False:
        return "Race: rival race not found"
    rival_indicator = state_summary.get("rival_indicator_detected")
    if rival_indicator is True:
      return "Race: rival indicator detected"
    if rival_indicator is False:
      return "Race: rival indicator not detected"
    mission = state_summary.get("race_mission_available")
    if mission is not None:
      return f"Race mission available: {mission}"
    return ""

  def _format_race_check_line(self, planned):
    race_check = planned.get("race_check") or {}
    race_scout = planned.get("race_scout") or {}
    if not race_check and not race_scout:
      return ""

    parts = []
    if race_check:
      indicator = race_check.get("rival_indicator_detected")
      if indicator is True:
        parts.append("indicator yes")
      elif indicator is False:
        parts.append("indicator no")
      method = race_check.get("method")
      if method:
        parts.append(f"check {method}")
    if race_scout:
      if race_scout.get("executed"):
        rival_found = race_scout.get("rival_found")
        if rival_found is True:
          parts.append("scout found")
        elif rival_found is False:
          parts.append("scout none")
        else:
          parts.append("scout ran")
      else:
        parts.append("scout deferred")
    return f"Race Flow: {' | '.join(parts)}" if parts else ""

  def _format_trackblazer_race_lines(self, selected_action):
    decision = selected_action.get("trackblazer_race_decision") or {}
    if not isinstance(decision, dict) or not decision:
      return []

    outcome = "race" if decision.get("should_race") else "train"
    parts = [f"Race Gate: {outcome}"]
    target = decision.get("race_tier_target")
    if target:
      parts.append(f"target {target}")
    race_name = decision.get("race_name")
    if race_name:
      parts.append(f"race {race_name}")
    training_total = decision.get("training_total_stats")
    if training_total is not None:
      parts.append(f"training_total {training_total}")
    training_score = decision.get("training_score")
    if training_score is not None:
      parts.append(f"training_score {training_score}")
    if decision.get("rival_indicator"):
      parts.append("rival yes")
    if decision.get("is_summer"):
      parts.append("summer yes")

    lines = [" | ".join(parts)]
    reason = decision.get("reason")
    if reason:
      lines.append(f"Race Gate Reason: {reason}")
    return lines

  def _format_trackblazer_race_entry_lines(self, planned):
    entry_gate = planned.get("race_entry_gate") or {}
    if not isinstance(entry_gate, dict) or not entry_gate:
      return []

    parts = ["Race Entry Gate: lobby -> race list"]
    expected_branch = entry_gate.get("expected_branch")
    if expected_branch:
      parts.append(f"expected {expected_branch}")
    lines = [" | ".join(parts)]

    meaning = entry_gate.get("warning_meaning")
    if meaning:
      lines.append(f"Race Warning: {meaning}")

    ok_action = entry_gate.get("ok_action")
    cancel_action = entry_gate.get("cancel_action")
    if ok_action or cancel_action:
      lines.append(f"Race Warning Buttons: ok={ok_action or '-'} | cancel={cancel_action or '-'}")
    return lines

  def _format_training_lines(self, ranked_trainings, selected_action):
    if not ranked_trainings:
      return []
    entries = []
    for entry in ranked_trainings:
      if not isinstance(entry, dict):
        continue
      score_tuple = entry.get("score_tuple") or ()
      score_value = score_tuple[0] if score_tuple else None
      gains = entry.get("stat_gains") or {}
      total_gain = self._sum_visible_training_gains(gains)
      entries.append(
        {
          "name": entry.get("name") or "?",
          "score": self._safe_float(score_value),
          "failure": entry.get("failure"),
          "supports": entry.get("total_supports"),
          "rainbows": entry.get("total_rainbow_friends"),
          "total_gain": total_gain,
          "stat_gains": gains,
          "filtered_out": bool(entry.get("filtered_out")),
          "excluded_reason": entry.get("excluded_reason"),
          "max_allowed_failure": entry.get("max_allowed_failure"),
          "failure_bypassed_by_items": bool(entry.get("failure_bypassed_by_items")),
        }
      )
    entries.sort(
      key=lambda item: (
        1 if not item["filtered_out"] else 0,
        item["score"] if item["score"] is not None else float("-inf"),
        item["total_gain"],
      ),
      reverse=True,
    )
    selected_name = selected_action.get("training_name")
    lines = []
    for idx, entry in enumerate(entries, start=1):
      marker = "*" if entry["name"] == selected_name else " "
      parts = [f"  {marker} {idx}. {entry['name']}"]
      if entry["score"] is not None:
        parts.append(f"score {self._format_number(entry['score'], digits=3)}")
      parts.extend(self._format_training_gain_parts(entry["name"], entry.get("stat_gains") or {}))
      if entry["total_gain"]:
        parts.append(f"total+{entry['total_gain']}")
      if entry.get("rainbows") is not None:
        parts.append(f"rainbows {entry['rainbows']}")
      if entry.get("supports") is not None:
        parts.append(f"supports {entry['supports']}")
      if entry.get("failure") is not None:
        parts.append(f"fail {self._format_number(entry['failure'])}%")
      if entry.get("failure_bypassed_by_items"):
        parts.append("items clear")
      if entry.get("filtered_out") and entry.get("max_allowed_failure") is not None:
        parts.append(f"limit {self._format_number(entry['max_allowed_failure'])}%")
      if entry.get("excluded_reason"):
        parts.append(entry["excluded_reason"])
      lines.append(" | ".join(parts))
    return lines

  def _format_inventory_lines(self, planned):
    inventory_scan = planned.get("inventory_scan") or {}
    lines = []
    status = inventory_scan.get("status") or "unknown"
    button_visible = inventory_scan.get("button_visible")
    status_line = f"  Scan: {status}"
    if button_visible is not None:
      status_line += f" | button visible: {button_visible}"
    lines.append(status_line)

    detected = list(inventory_scan.get("items_detected") or [])
    held = self._collect_held_quantities(planned)
    if not held:
      held = dict(inventory_scan.get("held_quantities") or {})
    if not detected and held:
      detected = list(held.keys())
    if detected:
      lines.append(f"  Held: {self._format_inventory_items(detected, held)}")

    would_use = planned.get("would_use") or []
    if would_use:
      lines.append(f"  Use now: {self._format_candidate_list(would_use)}")
    else:
      lines.append("  Use now: none")

    deferred = planned.get("deferred_use") or []
    if deferred:
      lines.append(f"  Deferred: {self._format_candidate_list(deferred, include_reason=True)}")
    return lines

  def _format_shop_lines(self, planned, state_summary):
    shop_scan = planned.get("shop_scan") or {}
    lines = []
    status = shop_scan.get("status") or "unknown"
    shop_coins = shop_scan.get("shop_coins")
    status_line = f"  Scan: {status}"
    if shop_coins not in (None, ""):
      status_line += f" | coins: {shop_coins}"
    lines.append(status_line)

    would_buy = planned.get("would_buy") or []
    if would_buy:
      lines.append(f"  Buy: {self._format_shop_buy_list(would_buy)}")
    else:
      lines.append("  Buy: none")

    priority_preview = state_summary.get("trackblazer_shop_priority_preview") or []
    if priority_preview:
      preview_names = [item.get("name") for item in priority_preview if item.get("name")]
      if preview_names:
        lines.append(f"  Priorities: {', '.join(preview_names)}")
    return lines

  def _format_compact_timing_lines(self, state_summary):
    lines = []
    inventory_flow = (
      state_summary.get("trackblazer_inventory_pre_shop_flow")
      or state_summary.get("trackblazer_inventory_flow")
      or {}
    )
    shop_flow = state_summary.get("trackblazer_shop_flow") or {}
    skill_flow = state_summary.get("skill_purchase_flow") or {}
    for label, flow in (
      ("inventory", inventory_flow),
      ("shop", shop_flow),
      ("skill", skill_flow),
    ):
      if not isinstance(flow, dict):
        continue
      total = flow.get("timing_total")
      if total is None:
        continue
      parts = [f"  {label}: {self._format_number(total, digits=3)}s"]
      for key in ("timing_open", "timing_scan", "timing_close"):
        value = flow.get(key)
        if value is not None:
          parts.append(f"{key.replace('timing_', '')} {self._format_number(value, digits=3)}s")
      lines.append(" | ".join(parts))
    return lines

  def _format_planned_action_sections(self, planned):
    lines = []
    for section_name, payload in (
      ("Race Check", planned.get("race_check") or {}),
      ("Race Decision", planned.get("race_decision") or {}),
      ("Race Entry Gate", planned.get("race_entry_gate") or {}),
      ("Race Scout", planned.get("race_scout") or {}),
      ("Inventory Scan", planned.get("inventory_scan") or {}),
      ("Would Use", planned.get("would_use") or []),
      ("Deferred Use", planned.get("deferred_use") or []),
      ("Shop Scan", planned.get("shop_scan") or {}),
      ("Would Buy", planned.get("would_buy") or []),
    ):
      lines.append(f"  {section_name}:")
      if isinstance(payload, dict):
        summary = self._format_short_mapping(payload)
        if summary:
          lines.extend([f"    {line}" for line in summary])
        else:
          lines.append("    none")
      elif isinstance(payload, list):
        if payload:
          for line in self._format_short_list(payload):
            lines.append(f"    {line}")
        else:
          lines.append("    none")
    return lines

  def _format_planned_clicks(self, planned_clicks):
    lines = []
    for idx, click in enumerate(planned_clicks, start=1):
      if not isinstance(click, dict):
        continue
      line = f"  {idx}. {click.get('label') or 'click'}"
      note_parts = []
      if click.get("template"):
        note_parts.append(click["template"])
      if click.get("target"):
        note_parts.append(f"target={click['target']}")
      if click.get("region_key"):
        note_parts.append(f"region={click['region_key']}")
      if click.get("note"):
        note_parts.append(click["note"])
      if note_parts:
        line += f" | {' | '.join(note_parts)}"
      lines.append(line)
    return lines

  def _format_short_mapping(self, payload):
    lines = []
    for key, value in payload.items():
      if value in (None, "", [], {}):
        continue
      if isinstance(value, list):
        rendered = ", ".join(self._stringify_list_item(item) for item in value)
        lines.append(f"{key}: {rendered}")
      elif isinstance(value, dict):
        parts = []
        for sub_key, sub_value in value.items():
          if sub_value in (None, "", [], {}):
            continue
          parts.append(f"{sub_key}={sub_value}")
        if parts:
          lines.append(f"{key}: {'; '.join(parts)}")
      else:
        lines.append(f"{key}: {value}")
    return lines

  def _format_short_list(self, payload):
    lines = []
    for item in payload:
      if isinstance(item, dict):
        name = item.get("name") or item.get("key") or "item"
        entry = name
        details = []
        priority = item.get("priority")
        if priority:
          details.append(f"policy={priority}")
        target_training = item.get("target_training")
        if target_training:
          details.append(f"target={target_training}")
        usage_group = item.get("usage_group")
        if usage_group:
          details.append(f"group={usage_group}")
        held_quantity = item.get("held_quantity")
        max_quantity = item.get("max_quantity")
        if held_quantity is not None and max_quantity not in (None, "", 0):
          details.append(f"hold {held_quantity}/{max_quantity}")
        reserve_quantity = item.get("reserve_quantity")
        if reserve_quantity not in (None, "", 0):
          details.append(f"reserve {reserve_quantity}")
        cost = item.get("cost")
        if cost not in (None, ""):
          details.append(f"cost {cost}")
        if not details:
          reason = item.get("reason")
          if reason:
            details.append(reason)
        if details:
          entry += f" | {'; '.join(str(detail) for detail in details)}"
        lines.append(entry)
      else:
        lines.append(str(item))
    return lines

  def _collect_held_quantities(self, planned):
    held = {}
    for entry in (planned.get("would_use") or []) + (planned.get("deferred_use") or []):
      if not isinstance(entry, dict):
        continue
      item_key = entry.get("key")
      if item_key and entry.get("held_quantity") is not None:
        held[item_key] = entry.get("held_quantity")
    if held:
      return held
    return dict((planned.get("inventory_scan") or {}).get("held_quantities") or {})

  def _format_inventory_items(self, item_keys, held_quantities):
    rendered = []
    for item_key in item_keys:
      count = held_quantities.get(item_key)
      label = self._humanize_item_name(item_key)
      if count not in (None, ""):
        label += f" x{count}"
      rendered.append(label)
    return ", ".join(rendered)

  def _format_candidate_list(self, items, include_reason=False):
    rendered = []
    for item in items:
      if not isinstance(item, dict):
        continue
      label = item.get("name") or self._humanize_item_name(item.get("key"))
      if include_reason and item.get("reason"):
        label += f" ({item['reason']})"
      rendered.append(label)
    return ", ".join(rendered) if rendered else "none"

  def _format_shop_buy_list(self, items):
    rendered = []
    for item in items:
      if not isinstance(item, dict):
        continue
      label = item.get("name") or self._humanize_item_name(item.get("key"))
      cost = item.get("cost")
      if cost is not None:
        label += f" ({cost})"
      rendered.append(label)
    return ", ".join(rendered) if rendered else "none"

  def _humanize_item_name(self, value):
    text = str(value or "").replace("_", " ").strip()
    return text.title() if text else "Unknown"

  def _stringify_list_item(self, value):
    if isinstance(value, dict):
      return str(value.get("name") or value.get("key") or value)
    return str(value)

  def _format_ratio(self, left, right):
    return f"{self._format_number(left)}/{self._format_number(right)}"

  def _format_number(self, value, digits=1):
    if value is None:
      return "?"
    try:
      number = float(value)
    except (TypeError, ValueError):
      return str(value)
    if digits <= 0:
      return str(int(round(number)))
    text = f"{number:.{digits}f}"
    return text.rstrip("0").rstrip(".")

  def _safe_int(self, value, default_value=0):
    try:
      return int(value)
    except (TypeError, ValueError):
      return default_value

  def _visible_training_gains(self, gains):
    visible = {}
    for stat, value in (gains or {}).items():
      if stat == "sp":
        continue
      amount = self._safe_int(value)
      if amount:
        visible[stat] = amount
    return visible

  def _sum_visible_training_gains(self, gains):
    return sum(self._visible_training_gains(gains).values())

  def _format_training_gain_parts(self, training_name, gains):
    visible = self._visible_training_gains(gains)
    ordered = []
    main_gain = visible.pop(training_name, 0)
    if main_gain:
      ordered.append(f"{training_name}+{main_gain}")
    for stat in sorted(visible):
      ordered.append(f"{stat}+{visible[stat]}")
    return ordered

  def _safe_float(self, value, default_value=None):
    try:
      return float(value)
    except (TypeError, ValueError):
      return default_value

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

  def _set_trackblazer_scoring_mode(self):
    if self._trackblazer_scoring_mode_var is None:
      return
    mode = self._trackblazer_scoring_mode_var.get()
    bot.set_trackblazer_scoring_mode(mode)
    label = "stat focused" if mode == "stat_focused" else "legacy (timeline)"
    self._message_value.set(f"Trackblazer scoring mode: {label}.")
    self.publish()

  def _toggle_trackblazer_use_items(self):
    if self._trackblazer_use_items_var is None:
      return
    enabled = bool(self._trackblazer_use_items_var.get())
    bot.set_trackblazer_use_items_enabled(enabled)
    self._message_value.set(
      "Trackblazer item use scaffold enabled."
      if enabled else
      "Trackblazer item use dry-run enabled."
    )
    self.publish()

  def _toggle_skill_dry_run(self):
    if self._skill_dry_run_var is None:
      return
    enabled = bool(self._skill_dry_run_var.get())
    bot.set_skill_dry_run_enabled(enabled)
    self._message_value.set(
      "Skill purchase dry-run enabled (scan only, no confirm)."
      if enabled else
      "Skill purchase live mode (will confirm + learn)."
    )
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

  def _start_bot(self):
    bot.invoke_control_callback("start_bot")
    self.publish()

  def _stop_bot(self):
    # Signal stop immediately so the bot thread sees it ASAP, then
    # run the full stop (which joins the thread) in the background
    # so the Tkinter main thread stays responsive.
    # Note: only set stop_event here, NOT is_bot_running — the full
    # _stop_bot_locked() needs is_bot_running=True to do cleanup.
    bot.stop_event.set()
    bot.cancel_review_wait()
    self._stop_bot_button.configure(state="disabled")
    self._message_value.set("Stopping bot...")
    def _do_stop():
      bot.invoke_control_callback("stop_bot")
      if self._root is not None:
        self._root.after(0, self.publish)
    threading.Thread(target=_do_stop, daemon=True).start()

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

  def _trackblazer_shop_policy_context(self):
    runtime_state = bot.get_runtime_state()
    snapshot = runtime_state.get("snapshot") or {}
    state_summary = snapshot.get("state_summary") or {}
    return policy_context(
      year=state_summary.get("year"),
      turn=state_summary.get("turn"),
    )

  def _trackblazer_item_policy_context(self):
    return self._trackblazer_shop_policy_context()

  def _open_trackblazer_shop_policy_window(self):
    if self._root is None:
      return

    existing = self._shop_policy_window
    if existing is not None:
      try:
        if existing.winfo_exists():
          self._refresh_trackblazer_shop_policy_window()
          existing.lift()
          return
      except Exception:
        pass

    window = tk.Toplevel(self._root)
    window.title("Trackblazer Shop Policy")
    window.configure(bg="#101418")
    window.geometry("1280x820")
    window.rowconfigure(1, weight=1)
    window.columnconfigure(0, weight=1)
    window.bind(
      "<Destroy>",
      lambda event, root_window=window: self._clear_trackblazer_shop_policy_window() if event.widget is root_window else None,
    )

    header = tk.Frame(window, bg="#101418", padx=8, pady=8)
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(0, weight=1)
    self._shop_policy_context_var = tk.StringVar(value="")
    tk.Label(
      header,
      textvariable=self._shop_policy_context_var,
      fg="#d6dde5",
      bg="#101418",
      anchor="w",
      justify="left",
    ).grid(row=0, column=0, sticky="w")
    tk.Button(header, text="Reload", command=self._refresh_trackblazer_shop_policy_window).grid(row=0, column=1, padx=(8, 0))
    tk.Button(header, text="Reset Defaults", command=self._reset_trackblazer_shop_policy_defaults).grid(row=0, column=2, padx=(8, 0))
    tk.Button(header, text="Save", command=self._save_trackblazer_shop_policy_from_window).grid(row=0, column=3, padx=(8, 0))

    body_frame = tk.Frame(window, bg="#101418")
    body_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
    body_frame.rowconfigure(0, weight=1)
    body_frame.columnconfigure(0, weight=1)

    canvas = tk.Canvas(body_frame, bg="#101418", highlightthickness=0)
    scrollbar = tk.Scrollbar(body_frame, orient=tk.VERTICAL, command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")

    inner = tk.Frame(canvas, bg="#101418")
    canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind(
      "<Configure>",
      lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas.bind(
      "<Configure>",
      lambda event: canvas.itemconfigure("all", width=event.width),
    )

    self._shop_policy_window = window
    self._shop_policy_canvas = canvas
    self._shop_policy_body = inner
    self._refresh_trackblazer_shop_policy_window()

  def _open_trackblazer_item_policy_window(self):
    if self._root is None:
      return

    existing = self._item_policy_window
    if existing is not None:
      try:
        if existing.winfo_exists():
          self._refresh_trackblazer_item_policy_window()
          existing.lift()
          return
      except Exception:
        pass

    window = tk.Toplevel(self._root)
    window.title("Trackblazer Item Use Policy")
    window.configure(bg="#101418")
    window.geometry("1320x820")
    window.rowconfigure(1, weight=1)
    window.columnconfigure(0, weight=1)
    window.bind(
      "<Destroy>",
      lambda event, root_window=window: self._clear_trackblazer_item_policy_window() if event.widget is root_window else None,
    )

    header = tk.Frame(window, bg="#101418", padx=8, pady=8)
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(0, weight=1)
    self._item_policy_context_var = tk.StringVar(value="")
    tk.Label(
      header,
      textvariable=self._item_policy_context_var,
      fg="#d6dde5",
      bg="#101418",
      anchor="w",
      justify="left",
    ).grid(row=0, column=0, sticky="w")
    tk.Button(header, text="Reload", command=self._refresh_trackblazer_item_policy_window).grid(row=0, column=1, padx=(8, 0))
    tk.Button(header, text="Reset Defaults", command=self._reset_trackblazer_item_policy_defaults).grid(row=0, column=2, padx=(8, 0))
    tk.Button(header, text="Save", command=self._save_trackblazer_item_policy_from_window).grid(row=0, column=3, padx=(8, 0))

    body_frame = tk.Frame(window, bg="#101418")
    body_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
    body_frame.rowconfigure(0, weight=1)
    body_frame.columnconfigure(0, weight=1)

    canvas = tk.Canvas(body_frame, bg="#101418", highlightthickness=0)
    scrollbar = tk.Scrollbar(body_frame, orient=tk.VERTICAL, command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")

    inner = tk.Frame(canvas, bg="#101418")
    canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind(
      "<Configure>",
      lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas.bind(
      "<Configure>",
      lambda event: canvas.itemconfigure("all", width=event.width),
    )

    self._item_policy_window = window
    self._item_policy_canvas = canvas
    self._item_policy_body = inner
    self._refresh_trackblazer_item_policy_window()

  def _clear_trackblazer_shop_policy_window(self):
    self._shop_policy_window = None
    self._shop_policy_rows = []
    self._shop_policy_context_var = None
    self._shop_policy_canvas = None
    self._shop_policy_body = None

  def _clear_trackblazer_item_policy_window(self):
    self._item_policy_window = None
    self._item_policy_rows = []
    self._item_policy_context_var = None
    self._item_policy_canvas = None
    self._item_policy_body = None

  def _refresh_trackblazer_shop_policy_window(self):
    if self._shop_policy_body is None:
      return

    context = self._trackblazer_shop_policy_context()
    context_label = context.get("timeline_label") or "Unknown timeline context"
    context_suffix = "" if context.get("known_timeline") else " (live sort is using base order only)"
    if self._shop_policy_context_var is not None:
      self._shop_policy_context_var.set(
        f"Priority list is shown top-to-bottom for: {context_label}{context_suffix}"
      )

    for child in self._shop_policy_body.winfo_children():
      child.destroy()
    self._shop_policy_rows = []

    headers = [
      ("#", 0),
      ("Item", 1),
      ("Cost", 2),
      ("Priority", 3),
      ("Max", 4),
      ("Effective", 5),
      ("Asset", 6),
      ("Notes", 7),
    ]
    for text, column in headers:
      tk.Label(
        self._shop_policy_body,
        text=text,
        fg="white",
        bg="#151b22",
        padx=6,
        pady=4,
        anchor="w",
        font=("Helvetica", 10, "bold"),
      ).grid(row=0, column=column, sticky="ew", padx=1, pady=(0, 4))

    normalized_policy = normalize_shop_policy(getattr(config, "TRACKBLAZER_SHOP_POLICY", None))
    items = get_effective_shop_items(
      policy=normalized_policy,
      year=context.get("year"),
      turn=context.get("turn"),
    )

    for row_index, item in enumerate(items, start=1):
      row_bg = "#192028" if row_index % 2 else "#151b22"
      key = item["key"]
      item_policy = normalized_policy["items"].get(key, {})
      priority_var = tk.StringVar(value=item_policy.get("priority", item.get("priority", "MED")))
      max_var = tk.StringVar(value=str(item_policy.get("max_quantity", item.get("max_quantity", 0))))
      self._shop_policy_rows.append(
        {
          "key": key,
          "priority_var": priority_var,
          "max_var": max_var,
        }
      )

      tk.Label(self._shop_policy_body, text=str(row_index), fg="#d6dde5", bg=row_bg, padx=6, pady=3).grid(row=row_index, column=0, sticky="nsew", padx=1, pady=1)
      tk.Label(
        self._shop_policy_body,
        text=f"{item['display_name']}\n{item['category']}",
        fg="white",
        bg=row_bg,
        justify="left",
        anchor="w",
        padx=6,
        pady=3,
      ).grid(row=row_index, column=1, sticky="nsew", padx=1, pady=1)
      tk.Label(self._shop_policy_body, text=str(item["cost"]), fg="#d6dde5", bg=row_bg, padx=6, pady=3).grid(row=row_index, column=2, sticky="nsew", padx=1, pady=1)
      priority_menu = tk.OptionMenu(self._shop_policy_body, priority_var, *PRIORITY_LEVELS)
      priority_menu.configure(width=7, bg=row_bg, fg="white", highlightthickness=0, activebackground="#1f6feb")
      priority_menu["menu"].configure(bg="#192028", fg="white")
      priority_menu.grid(row=row_index, column=3, sticky="nsew", padx=1, pady=1)
      tk.Spinbox(
        self._shop_policy_body,
        from_=0,
        to=9,
        width=4,
        textvariable=max_var,
        bg=row_bg,
        fg="white",
        buttonbackground="#2d333b",
      ).grid(row=row_index, column=4, sticky="nsew", padx=1, pady=1)
      effective_label = item["effective_priority"]
      if item.get("active_timing_rules"):
        effective_label = f"{effective_label} *"
      tk.Label(
        self._shop_policy_body,
        text=effective_label,
        fg="#8bd5ca" if item.get("active_timing_rules") else "#d6dde5",
        bg=row_bg,
        padx=6,
        pady=3,
      ).grid(row=row_index, column=5, sticky="nsew", padx=1, pady=1)
      tk.Label(
        self._shop_policy_body,
        text="collected" if item.get("asset_collected") else "missing",
        fg="#8bd5ca" if item.get("asset_collected") else "#ffb86c",
        bg=row_bg,
        padx=6,
        pady=3,
      ).grid(row=row_index, column=6, sticky="nsew", padx=1, pady=1)
      notes_parts = [item.get("effect", ""), item.get("policy_notes", "")]
      notes_parts.extend(
        rule.get("note") for rule in (item.get("active_timing_rules") or []) if rule.get("note")
      )
      tk.Label(
        self._shop_policy_body,
        text="\n".join(part for part in notes_parts if part),
        fg="#d6dde5",
        bg=row_bg,
        justify="left",
        anchor="w",
        wraplength=520,
        padx=6,
        pady=3,
      ).grid(row=row_index, column=7, sticky="nsew", padx=1, pady=1)

    for column in range(8):
      weight = 1 if column in (1, 7) else 0
      self._shop_policy_body.columnconfigure(column, weight=weight)

  def _refresh_trackblazer_item_policy_window(self):
    if self._item_policy_body is None:
      return

    context = self._trackblazer_item_policy_context()
    context_label = context.get("timeline_label") or "Unknown timeline context"
    context_suffix = "" if context.get("known_timeline") else " (live sort is using base order only)"
    if self._item_policy_context_var is not None:
      self._item_policy_context_var.set(
        f"Item-use priority list is shown top-to-bottom for: {context_label}{context_suffix}"
      )

    for child in self._item_policy_body.winfo_children():
      child.destroy()
    self._item_policy_rows = []

    normalized_policy = normalize_item_use_policy(getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None))

    settings_frame = tk.Frame(self._item_policy_body, bg="#101418")
    settings_frame.grid(row=0, column=0, columnspan=8, sticky="ew", padx=1, pady=(0, 8))
    for column in range(8):
      settings_frame.columnconfigure(column, weight=1 if column in (1, 3, 5, 7) else 0)

    headers = [
      ("#", 0),
      ("Item", 1),
      ("Rule", 2),
      ("Priority", 3),
      ("Reserve", 4),
      ("Effective", 5),
      ("Asset", 6),
      ("Notes", 7),
    ]
    header_row = 2
    for text, column in headers:
      tk.Label(
        self._item_policy_body,
        text=text,
        fg="white",
        bg="#151b22",
        padx=6,
        pady=4,
        anchor="w",
        font=("Helvetica", 10, "bold"),
      ).grid(row=header_row, column=column, sticky="ew", padx=1, pady=(0, 4))

    items = get_effective_item_use_items(
      policy=normalized_policy,
      year=context.get("year"),
      turn=context.get("turn"),
    )

    for row_index, item in enumerate(items, start=1):
      grid_row = row_index + header_row
      row_bg = "#192028" if row_index % 2 else "#151b22"
      key = item["key"]
      item_policy = normalized_policy["items"].get(key, {})
      priority_var = tk.StringVar(value=item_policy.get("priority", item.get("priority", "MED")))
      reserve_var = tk.StringVar(value=str(item_policy.get("reserve_quantity", item.get("reserve_quantity", 0))))
      self._item_policy_rows.append(
        {
          "key": key,
          "priority_var": priority_var,
          "reserve_var": reserve_var,
        }
      )

      tk.Label(self._item_policy_body, text=str(row_index), fg="#d6dde5", bg=row_bg, padx=6, pady=3).grid(row=grid_row, column=0, sticky="nsew", padx=1, pady=1)
      tk.Label(
        self._item_policy_body,
        text=f"{item['display_name']}\n{item['category']}",
        fg="white",
        bg=row_bg,
        justify="left",
        anchor="w",
        padx=6,
        pady=3,
      ).grid(row=grid_row, column=1, sticky="nsew", padx=1, pady=1)
      rule_parts = [item.get("usage_group") or "utility"]
      if item.get("target_training"):
        rule_parts.append(item["target_training"])
      tk.Label(
        self._item_policy_body,
        text="\n".join(rule_parts),
        fg="#d6dde5",
        bg=row_bg,
        justify="left",
        anchor="w",
        padx=6,
        pady=3,
      ).grid(row=grid_row, column=2, sticky="nsew", padx=1, pady=1)
      priority_menu = tk.OptionMenu(self._item_policy_body, priority_var, *PRIORITY_LEVELS)
      priority_menu.configure(width=7, bg=row_bg, fg="white", highlightthickness=0, activebackground="#1f6feb")
      priority_menu["menu"].configure(bg="#192028", fg="white")
      priority_menu.grid(row=grid_row, column=3, sticky="nsew", padx=1, pady=1)
      tk.Spinbox(
        self._item_policy_body,
        from_=0,
        to=9,
        width=4,
        textvariable=reserve_var,
        bg=row_bg,
        fg="white",
        buttonbackground="#2d333b",
      ).grid(row=grid_row, column=4, sticky="nsew", padx=1, pady=1)
      effective_label = item["effective_priority"]
      if item.get("active_timing_rules"):
        effective_label = f"{effective_label} *"
      tk.Label(
        self._item_policy_body,
        text=effective_label,
        fg="#8bd5ca" if item.get("active_timing_rules") else "#d6dde5",
        bg=row_bg,
        padx=6,
        pady=3,
      ).grid(row=grid_row, column=5, sticky="nsew", padx=1, pady=1)
      tk.Label(
        self._item_policy_body,
        text="collected" if item.get("asset_collected") else "missing",
        fg="#8bd5ca" if item.get("asset_collected") else "#ffb86c",
        bg=row_bg,
        padx=6,
        pady=3,
      ).grid(row=grid_row, column=6, sticky="nsew", padx=1, pady=1)
      notes_parts = [item.get("effect", ""), item.get("policy_notes", "")]
      notes_parts.extend(
        rule.get("note") for rule in (item.get("active_timing_rules") or []) if rule.get("note")
      )
      tk.Label(
        self._item_policy_body,
        text="\n".join(part for part in notes_parts if part),
        fg="#d6dde5",
        bg=row_bg,
        justify="left",
        anchor="w",
        wraplength=520,
        padx=6,
        pady=3,
      ).grid(row=grid_row, column=7, sticky="nsew", padx=1, pady=1)

    for column in range(8):
      weight = 1 if column in (1, 7) else 0
      self._item_policy_body.columnconfigure(column, weight=weight)

  def _save_trackblazer_shop_policy_from_window(self):
    current_policy = normalize_shop_policy(getattr(config, "TRACKBLAZER_SHOP_POLICY", None))
    items = current_policy.get("items", {})
    for row in self._shop_policy_rows:
      key = row["key"]
      max_text = str(row["max_var"].get() or "").strip()
      try:
        max_quantity = max(0, int(max_text))
      except ValueError:
        max_quantity = items.get(key, {}).get("max_quantity", 0)
      item_policy = dict(items.get(key, {}))
      item_policy["priority"] = normalize_priority(row["priority_var"].get())
      item_policy["max_quantity"] = max_quantity
      items[key] = item_policy

    policy = {
      "version": int(current_policy.get("version", 1)),
      "items": items,
    }
    if not self._persist_config_value("trackblazer.shop_policy", policy):
      self._message_value.set("Failed to save Trackblazer shop policy.")
      return

    try:
      config.reload_config(print_config=False)
    except Exception as exc:
      self._message_value.set(f"Saved Trackblazer shop policy, but reload failed: {exc}")
      return

    self._refresh_trackblazer_shop_policy_window()
    self._message_value.set("Saved Trackblazer shop policy.")
    self.publish()

  def _reset_trackblazer_shop_policy_defaults(self):
    if not self._persist_config_value("trackblazer.shop_policy", get_default_shop_policy()):
      self._message_value.set("Failed to reset Trackblazer shop policy.")
      return

    try:
      config.reload_config(print_config=False)
    except Exception as exc:
      self._message_value.set(f"Reset Trackblazer shop policy, but reload failed: {exc}")
      return

    self._refresh_trackblazer_shop_policy_window()
    self._message_value.set("Reset Trackblazer shop policy to defaults.")
    self.publish()

  def _save_trackblazer_item_policy_from_window(self):
    current_policy = normalize_item_use_policy(getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None))
    items = current_policy.get("items", {})
    for row in self._item_policy_rows:
      key = row["key"]
      reserve_text = str(row["reserve_var"].get() or "").strip()
      try:
        reserve_quantity = max(0, int(reserve_text))
      except ValueError:
        reserve_quantity = items.get(key, {}).get("reserve_quantity", 0)
      item_policy = dict(items.get(key, {}))
      item_policy["priority"] = normalize_priority(row["priority_var"].get())
      item_policy["reserve_quantity"] = reserve_quantity
      items[key] = item_policy

    policy = {
      "version": int(current_policy.get("version", 1)),
      "settings": current_policy.get("settings", {}),
      "items": items,
    }
    if not self._persist_config_value("trackblazer.item_use_policy", policy):
      self._message_value.set("Failed to save Trackblazer item-use policy.")
      return

    try:
      config.reload_config(print_config=False)
    except Exception as exc:
      self._message_value.set(f"Saved Trackblazer item-use policy, but reload failed: {exc}")
      return

    self._refresh_trackblazer_item_policy_window()
    self._message_value.set("Saved Trackblazer item-use policy.")
    self.publish()

  def _reset_trackblazer_item_policy_defaults(self):
    current_policy = normalize_item_use_policy(getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None))
    if not self._persist_config_value(
      "trackblazer.item_use_policy",
      {
        "version": int(current_policy.get("version", 1)),
        "settings": current_policy.get("settings", {}),
        "items": get_default_item_use_policy()["items"],
      },
    ):
      self._message_value.set("Failed to reset Trackblazer item-use policy.")
      return

    try:
      config.reload_config(print_config=False)
    except Exception as exc:
      self._message_value.set(f"Reset Trackblazer item-use policy, but reload failed: {exc}")
      return

    self._refresh_trackblazer_item_policy_window()
    self._message_value.set("Reset Trackblazer item-use policy to defaults.")
    self.publish()

  # --- Stat Weights window ---

  _DEFAULT_STAT_WEIGHTS = {"spd": 1.0, "sta": 1.0, "pwr": 1.0, "guts": 1.0, "wit": 1.0}
  _STAT_LABELS = {"spd": "Speed", "sta": "Stamina", "pwr": "Power", "guts": "Guts", "wit": "Wit"}

  def _get_active_stat_weights(self):
    weights = getattr(config, "TRACKBLAZER_STAT_WEIGHTS", None)
    if isinstance(weights, dict) and weights:
      return weights
    return dict(self._DEFAULT_STAT_WEIGHTS)

  def _get_active_training_behavior(self):
    normalized_policy = normalize_item_use_policy(getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None))
    return get_training_behavior_settings(normalized_policy)

  def _open_stat_weights_window(self):
    if self._root is None:
      return

    existing = self._stat_weights_window
    if existing is not None:
      try:
        if existing.winfo_exists():
          self._refresh_stat_weights_window()
          existing.lift()
          return
      except Exception:
        pass

    window = tk.Toplevel(self._root)
    window.title("Training Behavior")
    window.configure(bg="#101418")
    window.geometry("500x500")
    window.resizable(False, False)
    window.bind(
      "<Destroy>",
      lambda event, root_window=window: self._clear_stat_weights_window() if event.widget is root_window else None,
    )

    header = tk.Frame(window, bg="#101418", padx=8, pady=8)
    header.pack(fill=tk.X)
    tk.Label(
      header,
      text="Training behavior settings",
      fg="#9aa4ad",
      bg="#101418",
    ).pack(side=tk.LEFT)

    body = tk.Frame(window, bg="#101418", padx=16, pady=4)
    body.pack(fill=tk.BOTH, expand=True)

    active = self._get_active_stat_weights()
    self._stat_weights_entries = {}
    tk.Label(
      body,
      text="Stat weights for stat_focused scoring (gain × weight)",
      fg="#d6dde5",
      bg="#101418",
      font=("Helvetica", 10, "bold"),
      anchor="w",
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
    for row_idx, stat in enumerate(self._DEFAULT_STAT_WEIGHTS):
      label = self._STAT_LABELS.get(stat, stat)
      tk.Label(
        body, text=label, fg="#d6dde5", bg="#101418", width=10, anchor="w",
      ).grid(row=row_idx + 1, column=0, sticky="w", pady=2)
      var = tk.StringVar(value=str(active.get(stat, 1.0)))
      entry = tk.Entry(body, textvariable=var, width=8, bg="#192028", fg="white", insertbackground="white")
      entry.grid(row=row_idx + 1, column=1, sticky="w", padx=(8, 0), pady=2)
      self._stat_weights_entries[stat] = var

    training_behavior = self._get_active_training_behavior()
    behavior_frame = tk.Frame(window, bg="#101418", padx=16, pady=4)
    behavior_frame.pack(fill=tk.X)
    tk.Label(
      behavior_frame,
      text="Wit failure gate",
      fg="#d6dde5",
      bg="#101418",
      font=("Helvetica", 10, "bold"),
      anchor="w",
    ).grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 4))
    tk.Label(
      behavior_frame,
      text="Wit gate supports",
      fg="#d6dde5",
      bg="#101418",
      anchor="e",
    ).grid(row=1, column=0, sticky="e", padx=(0, 4), pady=2)
    self._wit_gate_supports_var = tk.StringVar(value=str(training_behavior.get("wit_failure_gate_min_supports", 2)))
    tk.Spinbox(
      behavior_frame,
      from_=0,
      to=2,
      width=4,
      textvariable=self._wit_gate_supports_var,
      bg="#192028",
      fg="white",
      buttonbackground="#2d333b",
    ).grid(row=1, column=1, sticky="w", pady=2)
    tk.Label(
      behavior_frame,
      text="Wit gate rainbows",
      fg="#d6dde5",
      bg="#101418",
      anchor="e",
    ).grid(row=1, column=2, sticky="e", padx=(12, 4), pady=2)
    self._wit_gate_rainbows_var = tk.StringVar(value=str(training_behavior.get("wit_failure_gate_min_rainbows", 1)))
    tk.Spinbox(
      behavior_frame,
      from_=0,
      to=2,
      width=4,
      textvariable=self._wit_gate_rainbows_var,
      bg="#192028",
      fg="white",
      buttonbackground="#2d333b",
    ).grid(row=1, column=3, sticky="w", pady=2)
    tk.Label(
      behavior_frame,
      text="Wit energy bypass %",
      fg="#d6dde5",
      bg="#101418",
      anchor="e",
    ).grid(row=1, column=4, sticky="e", padx=(12, 4), pady=2)
    self._wit_gate_energy_var = tk.StringVar(value=str(training_behavior.get("wit_failure_gate_high_energy_pct", 80)))
    tk.Spinbox(
      behavior_frame,
      from_=0,
      to=100,
      width=5,
      textvariable=self._wit_gate_energy_var,
      bg="#192028",
      fg="white",
      buttonbackground="#2d333b",
    ).grid(row=1, column=5, sticky="w", pady=2)
    tk.Label(
      behavior_frame,
      text="Strong score gate",
      fg="#d6dde5",
      bg="#101418",
      anchor="e",
    ).grid(row=1, column=6, sticky="e", padx=(12, 4), pady=2)
    self._strong_training_score_threshold_var = tk.StringVar(
      value=str(training_behavior.get("strong_training_score_threshold", 40))
    )
    tk.Spinbox(
      behavior_frame,
      from_=0,
      to=200,
      width=5,
      textvariable=self._strong_training_score_threshold_var,
      bg="#192028",
      fg="white",
      buttonbackground="#2d333b",
    ).grid(row=1, column=7, sticky="w", pady=2)
    tk.Label(
      behavior_frame,
      text="Below the bypass, wit only stays eligible when it has enough supports or rainbows. Above the bypass, wit is always allowed. Training scores at or above the strong score gate keep the turn on training.",
      fg="#8b949e",
      bg="#101418",
      justify="left",
      anchor="w",
      wraplength=640,
    ).grid(row=2, column=0, columnspan=8, sticky="ew", pady=(4, 0))

    bond_frame = tk.Frame(window, bg="#101418", padx=16, pady=4)
    bond_frame.pack(fill=tk.X)
    self._bond_boost_var = tk.BooleanVar(value=bot.get_trackblazer_bond_boost_enabled())
    tk.Checkbutton(
      bond_frame,
      text="Bond boost (+10/friend, +15 on wit)",
      variable=self._bond_boost_var,
      command=self._toggle_bond_boost,
      fg="#d6dde5",
      bg="#101418",
      selectcolor="#192028",
      activebackground="#101418",
      activeforeground="white",
    ).pack(side=tk.LEFT)

    cutoff_frame = tk.Frame(window, bg="#101418", padx=16, pady=0)
    cutoff_frame.pack(fill=tk.X)
    tk.Label(
      cutoff_frame, text="Active until:", fg="#9aa4ad", bg="#101418",
    ).pack(side=tk.LEFT)
    self._bond_boost_cutoff_var = tk.StringVar(value=bot.get_trackblazer_bond_boost_cutoff())
    cutoff_menu = tk.OptionMenu(
      cutoff_frame,
      self._bond_boost_cutoff_var,
      *constants.TIMELINE[:-1],
      command=self._set_bond_boost_cutoff,
    )
    cutoff_menu.configure(bg="#192028", fg="white", activebackground="#2a3540", activeforeground="white", highlightthickness=0, width=24)
    cutoff_menu["menu"].configure(bg="#192028", fg="white", activebackground="#2a3540", activeforeground="white")
    cutoff_menu.pack(side=tk.LEFT, padx=(4, 0))

    buff_override_frame = tk.Frame(window, bg="#101418", padx=16, pady=4)
    buff_override_frame.pack(fill=tk.X)
    self._buff_override_var = tk.BooleanVar(value=bot.get_trackblazer_allow_buff_override())
    tk.Checkbutton(
      buff_override_frame,
      text="Allow megaphone buff override (60% over 40%)",
      variable=self._buff_override_var,
      command=self._toggle_buff_override,
      fg="#d6dde5",
      bg="#101418",
      selectcolor="#192028",
      activebackground="#101418",
      activeforeground="white",
    ).pack(side=tk.LEFT)

    buttons = tk.Frame(window, bg="#101418", padx=8, pady=8)
    buttons.pack(fill=tk.X)
    tk.Button(buttons, text="Save", command=self._save_stat_weights).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(buttons, text="Reset Defaults", command=self._reset_stat_weights_defaults).pack(side=tk.LEFT, padx=(0, 8))

    self._stat_weights_window = window

  def _toggle_bond_boost(self):
    enabled = self._bond_boost_var.get()
    bot.set_trackblazer_bond_boost_enabled(enabled)
    label = "on" if enabled else "off"
    self._message_value.set(f"Bond boost: {label}.")
    self.publish()

  def _toggle_buff_override(self):
    enabled = self._buff_override_var.get()
    bot.set_trackblazer_allow_buff_override(enabled)
    label = "on" if enabled else "off"
    self._message_value.set(f"Megaphone buff override: {label}.")
    self.publish()

  def _set_bond_boost_cutoff(self, value):
    bot.set_trackblazer_bond_boost_cutoff(value)
    self._message_value.set(f"Bond boost cutoff: {value}.")
    self.publish()

  def _clear_stat_weights_window(self):
    self._stat_weights_window = None
    self._stat_weights_entries = {}

  def _refresh_stat_weights_window(self):
    active = self._get_active_stat_weights()
    for stat, var in self._stat_weights_entries.items():
      var.set(str(active.get(stat, 1.0)))
    behavior = self._get_active_training_behavior()
    if self._wit_gate_supports_var is not None:
      self._wit_gate_supports_var.set(str(behavior.get("wit_failure_gate_min_supports", 2)))
    if self._wit_gate_rainbows_var is not None:
      self._wit_gate_rainbows_var.set(str(behavior.get("wit_failure_gate_min_rainbows", 1)))
    if self._wit_gate_energy_var is not None:
      self._wit_gate_energy_var.set(str(behavior.get("wit_failure_gate_high_energy_pct", 80)))
    if self._strong_training_score_threshold_var is not None:
      self._strong_training_score_threshold_var.set(str(behavior.get("strong_training_score_threshold", 40)))

  def _save_stat_weights(self):
    weights = {}
    for stat, var in self._stat_weights_entries.items():
      try:
        weights[stat] = round(float(var.get()), 2)
      except ValueError:
        self._message_value.set(f"Invalid weight for {self._STAT_LABELS.get(stat, stat)}.")
        return

    if not self._persist_config_value("trackblazer.stat_weights", weights):
      self._message_value.set("Failed to save stat weights.")
      return

    try:
      config.reload_config(print_config=False)
    except Exception as exc:
      self._message_value.set(f"Saved stat weights, but reload failed: {exc}")
      return

    current_policy = normalize_item_use_policy(getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None))
    training_behavior = get_default_training_behavior_settings()
    settings = current_policy.get("settings", {}) if isinstance(current_policy.get("settings"), dict) else {}
    behavior_settings = settings.get("training_behavior", {}) if isinstance(settings.get("training_behavior"), dict) else {}
    training_behavior.update(behavior_settings)
    if self._wit_gate_supports_var is not None:
      supports_text = str(self._wit_gate_supports_var.get() or "").strip()
      try:
        training_behavior["wit_failure_gate_min_supports"] = min(2, max(0, int(supports_text)))
      except ValueError:
        pass
    if self._wit_gate_rainbows_var is not None:
      rainbows_text = str(self._wit_gate_rainbows_var.get() or "").strip()
      try:
        training_behavior["wit_failure_gate_min_rainbows"] = min(2, max(0, int(rainbows_text)))
      except ValueError:
        pass
    if self._wit_gate_energy_var is not None:
      energy_text = str(self._wit_gate_energy_var.get() or "").strip()
      try:
        training_behavior["wit_failure_gate_high_energy_pct"] = min(100, max(0, int(energy_text)))
      except ValueError:
        pass
    if self._strong_training_score_threshold_var is not None:
      threshold_text = str(self._strong_training_score_threshold_var.get() or "").strip()
      try:
        training_behavior["strong_training_score_threshold"] = max(0, int(threshold_text))
      except ValueError:
        pass

    policy = {
      "version": int(current_policy.get("version", 1)),
      "settings": {
        "training_behavior": training_behavior,
      },
      "items": current_policy.get("items", {}),
    }
    if not self._persist_config_value("trackblazer.item_use_policy", policy):
      self._message_value.set("Failed to save training behavior.")
      return

    try:
      config.reload_config(print_config=False)
    except Exception as exc:
      self._message_value.set(f"Saved stat weights, but reload failed: {exc}")
      return

    self._message_value.set(f"Saved stat weights and training behavior: {weights}")
    self.publish()

  def _reset_stat_weights_defaults(self):
    current_policy = normalize_item_use_policy(getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None))
    if not self._persist_config_value("trackblazer.stat_weights", dict(self._DEFAULT_STAT_WEIGHTS)):
      self._message_value.set("Failed to reset stat weights.")
      return

    policy = {
      "version": int(current_policy.get("version", 1)),
      "settings": {
        "training_behavior": get_default_training_behavior_settings(),
      },
      "items": current_policy.get("items", {}),
    }
    if not self._persist_config_value("trackblazer.item_use_policy", policy):
      self._message_value.set("Failed to reset training behavior.")
      return

    try:
      config.reload_config(print_config=False)
    except Exception as exc:
      self._message_value.set(f"Reset stat weights, but reload failed: {exc}")
      return

    self._refresh_stat_weights_window()
    self._message_value.set("Reset stat weights to defaults.")
    self.publish()


def ensure_operator_console():
  if bot.operator_console is None:
    bot.operator_console = OperatorConsole()
    bot.operator_console.start()
  return bot.operator_console


def publish_runtime_state():
  console = bot.operator_console
  if console is not None:
    console.publish()
