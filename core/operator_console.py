import json
import queue
import tkinter as tk
from tkinter import scrolledtext, ttk
import threading
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageTk

import core.bot as bot
import core.config as config
from core.race_selector import (
  CALENDAR as RACE_SELECTOR_CALENDAR,
  YEAR_ORDER as RACE_SELECTOR_YEARS,
  get_races_for_date,
  get_selector_ui_state,
  serialize_selector_payload,
  summarize_selector_state,
)
from core.trackblazer_item_use import (
  ITEM_USE_BEHAVIOR_MODES,
  get_default_item_use_policy,
  get_default_training_behavior_settings,
  get_effective_item_use_items,
  get_training_behavior_settings,
  get_training_behavior_committed_training_score_threshold,
  get_training_behavior_optional_race_threshold,
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
from core.trackblazer.models import (
  TurnPlan,
  build_quick_bar_payload,
  render_compact_summary,
  render_turn_discussion,
)
import utils.constants as constants
from core.platform.window_focus import focus_target_window, reapply_configured_recognition_geometry
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
    self._region_adjuster_profile_var = None
    self._region_adjuster_profile_menu = None
    self._region_adjuster_profiles = {}
    self._trackblazer_use_items_var = None
    self._trackblazer_use_new_planner_var = None
    self._skill_auto_buy_var = None
    self._skip_scenario_detection_var = None
    self._skip_full_stats_aptitude_check_var = None
    self._trackblazer_scoring_mode_var = None
    self._strong_training_score_threshold_var = None
    self._optional_race_training_threshold_var = None
    self._committed_training_score_threshold_var = None
    self._phase_labels = {}
    self._race_selector_status_var = None
    self._race_selector_window = None
    self._race_selector_summary_var = None
    self._race_selector_entries = {}
    self._race_selector_year_bodies = {}
    self._race_selector_date_window = None
    self._race_selector_date_context = None
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
    self._shop_policy_catalog_timing_var = None
    self._shop_policy_canvas = None
    self._shop_policy_body = None
    self._item_policy_window = None
    self._item_policy_rows = []
    self._item_policy_context_var = None
    self._item_policy_canvas = None
    self._item_policy_body = None
    self._planner_flow_window = None
    self._planner_flow_canvas = None
    self._planner_flow_vars = {}
    self._planner_flow_node_items = {}
    self._stat_weights_window = None
    self._stat_weights_entries = {}
    self._bond_boost_var = None
    self._wit_gate_supports_var = None
    self._wit_gate_rainbows_var = None
    self._wit_gate_energy_var = None
    self._race_lookahead_enabled_var = None
    self._race_lookahead_threshold_var = None
    self._race_lookahead_score_var = None
    self._scheduled_race_vita_enabled_var = None
    self._scheduled_race_vita_threshold_var = None
    self._zero_energy_optional_race_rest_var = None
    self._zero_energy_optional_race_vita_var = None
    self._zero_energy_optional_race_recovery_var = None
    self._save_vita_for_summer_var = None
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
    self._region_adjuster_profiles, active_profile = self._get_region_adjuster_profile_options()
    self._region_adjuster_profile_var = tk.StringVar(value=active_profile)
    tk.Label(primary_controls, text="OCR preset:", fg="#9aa4ad", bg="#101418").pack(side=tk.LEFT, padx=(8, 4))
    profile_names = list(self._region_adjuster_profiles.keys()) or [active_profile]
    self._region_adjuster_profile_menu = tk.OptionMenu(
      primary_controls,
      self._region_adjuster_profile_var,
      *profile_names,
      command=self._set_region_adjuster_profile,
    )
    self._region_adjuster_profile_menu.configure(
      bg="#192028",
      fg="white",
      highlightthickness=0,
      activebackground="#192028",
      activeforeground="white",
      borderwidth=1,
      relief=tk.FLAT,
    )
    self._region_adjuster_profile_menu["menu"].configure(bg="#192028", fg="white")
    self._region_adjuster_profile_menu.pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(primary_controls, text="Asset Creator", command=self._launch_asset_creator).pack(side=tk.LEFT)
    tk.Button(primary_controls, text="Training", command=self._open_stat_weights_window).pack(side=tk.LEFT, padx=(8, 0))
    tk.Button(primary_controls, text="Planner Flow", command=self._open_planner_flow_window).pack(side=tk.LEFT, padx=(4, 0))
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
    tk.Button(secondary_controls, text="Race Selector", command=self._open_race_selector_window).pack(side=tk.LEFT, padx=(12, 0))
    self._race_selector_status_var = tk.StringVar(value=self._race_selector_status_text())
    tk.Label(
      secondary_controls,
      textvariable=self._race_selector_status_var,
      fg="#9aa4ad",
      bg="#101418",
      anchor="w",
    ).pack(side=tk.LEFT, padx=(6, 0))

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
      reason = entry.get("reason", "")
      runtime_path = entry.get("runtime_path", "")
      previous_runtime_path = entry.get("previous_runtime_path", "")
      decision_path = entry.get("decision_path", "")
      source = entry.get("source", "")
      reasons = entry.get("reasons") or []
      trigger = entry.get("trigger", "")
      before_phase = entry.get("before_phase", "")
      before_sub_phase = entry.get("before_sub_phase", "")
      before_status = entry.get("before_status", "")
      previous_reason = entry.get("previous_reason", "")
      changed = entry.get("changed") or []
      changes = entry.get("changes") or {}
      target_sub_phase = entry.get("target_sub_phase", "")
      same_turn_retry = entry.get("same_turn_retry")
      if note:
        details.append(str(note))
      elif reason:
        details.append(f"reason={reason}")
      if reasons:
        details.append(f"reasons={','.join(str(value) for value in reasons)}")
      if runtime_path:
        details.append(f"path={runtime_path}")
      if previous_runtime_path and previous_runtime_path != runtime_path:
        details.append(f"prev={previous_runtime_path}")
      if decision_path:
        details.append(f"decision={decision_path}")
      if source:
        details.append(f"source={source}")
      if trigger:
        details.append(f"trigger={trigger}")
      if before_phase:
        details.append(f"before={before_phase}")
      if before_sub_phase and before_sub_phase != before_phase:
        details.append(f"before_sub={before_sub_phase}")
      if before_status:
        details.append(f"before_status={before_status}")
      if previous_reason:
        details.append(f"previous_reason={previous_reason}")
      if changed:
        details.append(f"changed={','.join(str(value) for value in changed)}")
      if changes:
        details.append(f"changes={','.join(str(key) for key in changes.keys())}")
      if target_sub_phase:
        details.append(f"target_sub={target_sub_phase}")
      if same_turn_retry is not None:
        details.append(f"same_turn_retry={bool(same_turn_retry)}")
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
    quick_bar = snapshot.get("quick_bar")
    if not isinstance(quick_bar, dict):
      snapshot_context = {
        "planned_clicks": snapshot.get("planned_clicks") or [],
      }
      quick_bar = build_quick_bar_payload(snapshot_context, snapshot.get("planned_actions") or {})
    clicks_text = quick_bar.get("planned_clicks_text") or "-"
    if self._planned_clicks_value is not None:
      self._planned_clicks_value.configure(text=clicks_text)
    use_text = quick_bar.get("would_use_text") or "-"
    if self._would_use_value is not None:
      self._would_use_value.configure(text=use_text)
    buy_text = quick_bar.get("would_buy_text") or "-"
    if self._would_buy_value is not None:
      self._would_buy_value.configure(text=buy_text)

  def _poll_queue(self):
    try:
      while True:
        cmd, payload = self._queue.get_nowait()
        if cmd == "refresh":
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
    if self._trackblazer_use_new_planner_var is not None:
      self._trackblazer_use_new_planner_var.set(bool(runtime_state.get("trackblazer_use_new_planner_enabled")))
    if self._trackblazer_scoring_mode_var is not None:
      self._trackblazer_scoring_mode_var.set(runtime_state.get("trackblazer_scoring_mode") or "stat_focused")
    if self._skill_auto_buy_var is not None:
      self._skill_auto_buy_var.set(bool(runtime_state.get("skill_auto_buy_skill_enabled", runtime_state.get("skill_dry_run_enabled"))))
    if self._race_selector_status_var is not None:
      self._race_selector_status_var.set(self._race_selector_status_text())
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
      "turn_metrics": runtime_state.get("turn_metrics") or {},
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
    timing_text = self._format_timing(snapshot, runtime_state=runtime_state)
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

  def _format_timing(self, snapshot, runtime_state=None):
    runtime_state = runtime_state or {}
    sub_phase = snapshot.get("sub_phase") or ""
    if sub_phase in {
      "manual_skill_purchase_check",
      "manual_shop_check",
      "manual_inventory_check",
      "manual_inventory_selection_test",
    }:
      return self._format_flow_timing(snapshot)
    turn_metrics = runtime_state.get("turn_metrics") or {}
    current_metrics = turn_metrics.get("current") or {}
    last_completed_metrics = turn_metrics.get("last_completed") or {}
    sections = []
    if current_metrics:
      sections.append(self._format_turn_metrics_section(current_metrics, title="Current Turn"))
    if last_completed_metrics:
      sections.append(self._format_turn_metrics_section(last_completed_metrics, title="Last Completed Turn"))
    sections = [section for section in sections if section]
    planner_text = self._format_planner_timing(snapshot)
    if planner_text:
      sections.append(planner_text)
    flow_text = self._format_flow_timing(snapshot)
    if flow_text and flow_text != "No timing data":
      sections.append(flow_text)
    if sections:
      return "\n\n".join(section for section in sections if section)
    return "No timing data"

  def _format_flow_timing(self, snapshot):
    state_summary = snapshot.get("state_summary") or {}
    sub_phase = snapshot.get("sub_phase") or ""
    if sub_phase == "manual_skill_purchase_check":
      flow = state_summary.get("skill_purchase_flow") or {}
      title = "Skill Purchase Flow"
      return self._format_flow_timing_section(title, flow) if flow else "No timing data"
    elif sub_phase == "manual_shop_check":
      flow = state_summary.get("trackblazer_shop_flow") or {}
      title = "Shop Flow"
      return self._format_flow_timing_section(title, flow) if flow else "No timing data"
    elif sub_phase in ("manual_inventory_check", "manual_inventory_selection_test"):
      flow = state_summary.get("trackblazer_inventory_flow") or {}
      title = "Inventory Flow"
      return self._format_flow_timing_section(title, flow) if flow else "No timing data"

    sections = []
    planner_state = snapshot.get("trackblazer_planner_state") or state_summary.get("trackblazer_planner_state") or {}
    turn_plan_snapshot = dict((planner_state or {}).get("turn_plan") or {})
    if turn_plan_snapshot:
      turn_plan = TurnPlan.from_snapshot(turn_plan_snapshot)
      planner_timing = dict(turn_plan.timing or {})
      for label, flow in (
        ("Planner Snapshot: Inventory", planner_timing.get("inventory") or {}),
        ("Planner Snapshot: Shop", planner_timing.get("shop") or {}),
        ("Planner Snapshot: Skill", planner_timing.get("skill") or {}),
      ):
        section = self._format_flow_timing_section(label, flow)
        if section:
          sections.append(section)

    for title, flow in (
      ("Inventory Flow (Pre-Shop)", state_summary.get("trackblazer_inventory_pre_shop_flow") or {}),
      ("Inventory Flow", state_summary.get("trackblazer_inventory_flow") or {}),
      ("Shop Flow", state_summary.get("trackblazer_shop_flow") or {}),
      ("Skill Purchase Flow", state_summary.get("skill_purchase_flow") or {}),
    ):
      section = self._format_flow_timing_section(title, flow)
      if section:
        sections.append(section)

    if sections:
      return "\n\n".join(sections)
    return "No timing data"

  def _format_planner_timing(self, snapshot):
    state_summary = snapshot.get("state_summary") or {}
    runtime = (
      snapshot.get("trackblazer_planner_runtime")
      or state_summary.get("trackblazer_planner_runtime")
      or {}
    )
    planner_state = snapshot.get("trackblazer_planner_state") or state_summary.get("trackblazer_planner_state") or {}
    turn_plan_snapshot = dict((planner_state or {}).get("turn_plan") or {})
    turn_plan = TurnPlan.from_snapshot(turn_plan_snapshot) if turn_plan_snapshot else None
    decision_path = getattr(turn_plan, "decision_path", "") or (planner_state.get("decision_path") or "")
    selection_rationale = getattr(turn_plan, "selection_rationale", "") or ""
    debug_summary = dict(getattr(turn_plan, "debug_summary", {}) or {})
    metadata = dict(getattr(turn_plan, "planner_metadata", {}) or {})

    has_runtime = bool(runtime)
    has_plan = bool(turn_plan_snapshot)
    if not has_runtime and not has_plan:
      return ""

    lines = ["=== Planner Runtime ==="]
    runtime_path = runtime.get("runtime_path") or metadata.get("runtime_path") or snapshot.get("trackblazer_runtime_path")
    if runtime_path:
      lines.append(f"  runtime_path  {runtime_path}")
    if decision_path:
      lines.append(f"  decision_path {decision_path}")
    inventory_source = debug_summary.get("inventory_source") or metadata.get("inventory_source")
    if inventory_source:
      lines.append(f"  inventory_src {inventory_source}")
    if runtime.get("latest_observation_id"):
      lines.append(f"  observation   {runtime.get('latest_observation_id')}")
    fallback_count = runtime.get("fallback_count")
    if fallback_count is not None:
      fallback_line = f"  fallbacks     {int(fallback_count)}"
      if runtime.get("last_fallback_reason"):
        fallback_line += f" | {runtime.get('last_fallback_reason')}"
      lines.append(fallback_line)
    if debug_summary:
      summary_parts = []
      for key in (
        "planner_native_candidate_count",
        "ranked_training_count",
        "shop_item_count",
        "shop_deviation_count",
        "execution_item_count",
      ):
        value = debug_summary.get(key)
        if value is not None:
          label = key.replace("_count", "").replace("_", " ")
          summary_parts.append(f"{label}={value}")
      if summary_parts:
        lines.append(f"  plan_counts   {' | '.join(summary_parts)}")
    if selection_rationale:
      lines.append(f"  selection     {selection_rationale}")

    pending_skill = dict(runtime.get("pending_skill_scan") or {})
    pending_skill_line = self._format_pending_state_line("pending_skill", pending_skill)
    if pending_skill_line:
      lines.append(pending_skill_line)
    pending_shop = dict(runtime.get("pending_shop_scan") or {})
    pending_shop_line = self._format_pending_state_line("pending_shop", pending_shop)
    if pending_shop_line:
      lines.append(pending_shop_line)

    transitions = list(runtime.get("transition_breadcrumbs") or [])
    if transitions:
      lines.append("")
      lines.append("Planner Transitions:")
      for index, transition in enumerate(transitions[-8:], start=max(1, len(transitions) - 7)):
        lines.append(self._format_planner_transition(transition, index))

    return "\n".join(lines)

  def _format_pending_state_line(self, label, payload):
    if not isinstance(payload, dict) or not payload:
      return ""
    status = payload.get("status")
    if not status:
      return ""
    parts = [str(status)]
    for key in ("reason", "source", "shop_status", "shop_turn_key", "captured_sp"):
      value = payload.get(key)
      if value in (None, "", []):
        continue
      parts.append(f"{key}={value}")
    return f"  {label:13s} {' | '.join(parts)}"

  def _format_planner_transition(self, transition, index):
    if not isinstance(transition, dict):
      return f"  {index}. {transition}"
    step_label = transition.get("step_id") or transition.get("step_type") or f"transition_{index}"
    status = transition.get("status") or "-"
    timing_text = self._format_planner_transition_timing(transition)
    parts = [f"  {index}. {step_label} [{status}]"]
    if timing_text:
      parts.append(timing_text)
    note = transition.get("note") or ""
    if note:
      parts.append(str(note))
    details = transition.get("details") or {}
    detail_text = self._format_turn_metrics_step_data(details)
    if detail_text:
      parts.append(detail_text)
    return "  ".join(parts)

  def _format_planner_transition_timing(self, transition):
    if not isinstance(transition, dict):
      return ""
    started_at = transition.get("started_at")
    finished_at = transition.get("finished_at")
    duration = transition.get("duration")
    started_text = self._format_transition_timestamp(started_at)
    finished_text = self._format_transition_timestamp(finished_at)
    if started_text and finished_text and duration is not None:
      return f"{started_text} -> {finished_text} ({float(duration):.3f}s)"
    if started_text and duration is not None:
      return f"{started_text} ({float(duration):.3f}s)"
    if started_text:
      return started_text
    if duration is not None:
      return f"{float(duration):.3f}s"
    return ""

  def _format_transition_timestamp(self, value):
    if value is None:
      return ""
    try:
      timestamp = float(value)
    except (TypeError, ValueError):
      return ""
    dt = datetime.fromtimestamp(timestamp)
    return dt.strftime("%H:%M:%S.") + f"{int(dt.microsecond / 1000):03d}"

  def _format_flow_timing_section(self, title, flow):
    if not isinstance(flow, dict) or not flow:
      return ""
    lines = [f"=== {title} ==="]
    for key, value in self._iter_timing_entries(flow):
      lines.append(f"  {key:13s} {self._format_timing_value(value)}")
    status_line = self._format_flow_status_line(flow)
    if status_line:
      lines.append(status_line)

    open_result = flow.get("open_result") or flow.get("entry_result") or {}
    open_timing = open_result.get("timing") or {}
    if open_timing:
      lines.append("")
      lines.append("=== Open Breakdown ===")
      lines.extend(self._format_timing_mapping(open_timing))

    scan_timing = self._extract_scan_timing(flow)
    if scan_timing:
      lines.append("")
      lines.append("=== Scan Breakdown ===")
      lines.extend(self._format_timing_mapping(scan_timing))

    close_result = flow.get("close_result") or {}
    close_timing = close_result.get("timing") or {}
    if close_timing:
      lines.append("")
      lines.append("=== Close Breakdown ===")
      lines.extend(self._format_timing_mapping(close_timing))
    return "\n".join(lines)

  def _format_flow_status_line(self, flow):
    parts = []
    for key in (
      "opened",
      "already_open",
      "entered",
      "scanned",
      "closed",
      "confirmed",
      "skipped",
      "cached",
      "planner_retry_suppressed",
    ):
      if key in flow:
        parts.append(f"{key}={flow.get(key)}")
    for key in ("trigger", "reason", "scan_source"):
      value = flow.get(key)
      if value in (None, "", []):
        continue
      parts.append(f"{key}={value}")
    if not parts:
      return ""
    return f"  status        {' | '.join(parts)}"

  def _extract_scan_timing(self, flow):
    scan_result = flow.get("scan_result") or {}
    if not isinstance(scan_result, dict):
      scan_result = {}
    shared_flow = scan_result.get("flow") or {}
    if not isinstance(shared_flow, dict):
      shared_flow = {}
    return (
      flow.get("scan_timing")
      or scan_result.get("scan_timing")
      or shared_flow.get("timing")
      or shared_flow
      or {}
    )

  def _iter_timing_entries(self, flow):
    if not isinstance(flow, dict):
      return []
    preferred = [
      "timing_open",
      "timing_scan",
      "timing_increments",
      "timing_controls",
      "timing_confirm",
      "timing_close",
      "timing_total",
      "timing_reset_swipes",
      "timing_forward_swipes",
    ]
    seen = set()
    entries = []
    for key in preferred:
      if flow.get(key) is None:
        continue
      seen.add(key)
      entries.append((key.replace("timing_", ""), flow.get(key)))
    for key in sorted(flow.keys()):
      if not str(key).startswith("timing_") or key in seen:
        continue
      value = flow.get(key)
      if value is None:
        continue
      entries.append((str(key).replace("timing_", ""), value))
    return entries

  def _format_timing_value(self, value):
    if isinstance(value, bool):
      return str(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
      return f"{float(value):.3f}s"
    return str(value)

  def _format_turn_metrics_section(self, metrics, title):
    if not isinstance(metrics, dict) or not metrics:
      return ""
    lines = [f"=== {title} ==="]
    turn_label = metrics.get("turn_label") or "Pending main state"
    lines.append(f"  turn          {turn_label}")
    status = metrics.get("status") or "in_progress"
    completion_reason = metrics.get("completion_reason") or ""
    status_text = f"{status} ({completion_reason})" if completion_reason else status
    lines.append(f"  status        {status_text}")
    elapsed = metrics.get("total_duration")
    if elapsed is None:
      started_at = metrics.get("started_at")
      if started_at is not None:
        elapsed = max(0.0, time.time() - float(started_at))
    if elapsed is not None:
      lines.append(f"  elapsed       {float(elapsed):.3f}s")
    selected_action = metrics.get("selected_action") or {}
    action_label = self._format_turn_metrics_action_label(selected_action)
    if action_label:
      lines.append(f"  action        {action_label}")
    summary = metrics.get("state_summary") or {}
    energy = summary.get("energy_level")
    max_energy = summary.get("max_energy")
    if energy is not None or max_energy is not None:
      lines.append(f"  energy        {self._format_number(energy, digits=0)}/{self._format_number(max_energy, digits=0)}")
    mood = summary.get("current_mood")
    if mood:
      lines.append(f"  mood          {mood}")
    category_totals = metrics.get("category_totals") or {}
    if category_totals:
      lines.append("")
      lines.append("Category Totals:")
      for key, value in category_totals.items():
        lines.append(f"  {key:13s} {float(value):.3f}s")
    steps = metrics.get("steps") or []
    if steps:
      lines.append("")
      lines.append("Timeline:")
      for index, step in enumerate(steps, start=1):
        lines.append(self._format_turn_metrics_step(step, index))
    return "\n".join(lines)

  def _format_turn_metrics_action_label(self, selected_action):
    if not isinstance(selected_action, dict):
      return ""
    func_name = selected_action.get("func") or ""
    training_name = selected_action.get("training_name") or ""
    race_name = selected_action.get("race_name") or ""
    if func_name == "do_training" and training_name:
      return f"{func_name}({training_name})"
    if func_name == "do_race" and race_name:
      return f"{func_name}({race_name})"
    return func_name

  def _format_turn_metrics_step(self, step, index):
    if not isinstance(step, dict):
      return f"  {index}. {step}"
    label = step.get("label") or step.get("key") or f"step_{index}"
    duration = step.get("duration")
    duration_text = f"{float(duration):.3f}s" if duration is not None else "-"
    status = step.get("status") or "completed"
    prefix = f"  {index}. {label} [{duration_text}]"
    if status != "completed":
      prefix += f" ({status})"
    detail = step.get("detail") or ""
    if detail:
      prefix += f"  {detail}"
    data_text = self._format_turn_metrics_step_data(step.get("data") or {})
    if data_text:
      prefix += f"  [{data_text}]"
    return prefix

  def _format_turn_metrics_step_data(self, data):
    if not isinstance(data, dict) or not data:
      return ""
    parts = []
    preferred = [
      "timing_open",
      "timing_scan",
      "timing_increments",
      "timing_controls",
      "timing_confirm",
      "timing_close",
      "timing_total",
      "cached",
      "skipped",
      "trigger",
      "reason",
      "source",
      "execution_intent",
    ]
    seen = set()
    for key in preferred:
      value = data.get(key)
      if value is None:
        continue
      seen.add(key)
      parts.append(self._format_step_data_part(key, value))
    for key in sorted(data.keys()):
      if key in seen:
        continue
      value = data.get(key)
      if value in (None, {}, []):
        continue
      if isinstance(value, (dict, list, tuple, set)):
        continue
      parts.append(self._format_step_data_part(key, value))
    return " | ".join(part for part in parts if part)

  def _format_step_data_part(self, key, value):
    label = str(key).replace("timing_", "")
    if isinstance(value, bool):
      return f"{label}={value}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
      if str(key).startswith("timing_"):
        return f"{label}={float(value):.3f}s"
      return f"{label}={value}"
    return f"{label}={value}"

  def _format_planned_actions(self, snapshot):
    turn_discussion_text = snapshot.get("turn_discussion_text")
    if isinstance(turn_discussion_text, str) and turn_discussion_text.strip():
      return turn_discussion_text.strip()

    snapshot_context = {
      "scenario_name": snapshot.get("scenario_name"),
      "turn_label": snapshot.get("turn_label"),
      "execution_intent": snapshot.get("execution_intent"),
      "state_summary": snapshot.get("state_summary") or {},
      "selected_action": snapshot.get("selected_action") or {},
      "ranked_trainings": snapshot.get("ranked_trainings") or [],
      "reasoning_notes": snapshot.get("reasoning_notes") or "",
      "planned_clicks": snapshot.get("planned_clicks") or [],
      "planner_dual_run_comparison": snapshot.get("planner_dual_run_comparison") or {},
    }
    planner_state = (
      snapshot.get("trackblazer_planner_state")
      or (snapshot_context["state_summary"].get("trackblazer_planner_state") or {})
    )
    turn_plan_snapshot = dict((planner_state or {}).get("turn_plan") or {})
    if turn_plan_snapshot:
      return TurnPlan.from_snapshot(turn_plan_snapshot).to_turn_discussion(snapshot_context)
    return render_turn_discussion(snapshot_context, snapshot.get("planned_actions") or {})

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
    state_validation = state_summary.get("state_validation") or {}
    valid_for_history = bool(turn_label) and state_validation.get("valid", True)
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
    # Also skip invalid-retry snapshots; they are recovery placeholders and
    # should not replace the last good turn summary in history.
    if valid_for_history:
      self._history_last_turn = turn_label
      self._history_last_year = year
      self._history_last_planned_text = planned_text
      self._history_last_timing_text = timing_text
      self._history_last_summary_raw = self._summary_raw_value

  def _format_compact_summary(self, snapshot, include_prompt=True):
    compact_summary_text = snapshot.get("compact_summary_text")
    if isinstance(compact_summary_text, str) and compact_summary_text.strip():
      if include_prompt:
        return "\n".join([
          "Compact Turn Summary",
          "Use this for quick back-and-forth turn review.",
          "",
          compact_summary_text.strip(),
        ]).strip()
      return compact_summary_text.strip()

    snapshot_context = {
      "scenario_name": snapshot.get("scenario_name"),
      "turn_label": snapshot.get("turn_label"),
      "execution_intent": snapshot.get("execution_intent"),
      "state_summary": snapshot.get("state_summary") or {},
      "selected_action": snapshot.get("selected_action") or {},
      "ranked_trainings": snapshot.get("ranked_trainings") or [],
      "reasoning_notes": snapshot.get("reasoning_notes") or "",
      "planned_clicks": snapshot.get("planned_clicks") or [],
      "planner_dual_run_comparison": snapshot.get("planner_dual_run_comparison") or {},
    }
    planner_state = (
      snapshot.get("trackblazer_planner_state")
      or (snapshot_context["state_summary"].get("trackblazer_planner_state") or {})
    )
    turn_plan_snapshot = dict((planner_state or {}).get("turn_plan") or {})
    if turn_plan_snapshot:
      return TurnPlan.from_snapshot(turn_plan_snapshot).to_compact_summary(
        snapshot_context,
        include_prompt=include_prompt,
      )
    return render_compact_summary(
      snapshot_context,
      snapshot.get("planned_actions") or {},
      include_prompt=include_prompt,
    )

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

  def _format_operator_race_gate_line(self, state_summary):
    gate = state_summary.get("operator_race_gate") or {}
    if not isinstance(gate, dict):
      return ""
    if not gate.get("enabled") and not gate.get("selected_race"):
      return ""

    parts = []
    if gate.get("enabled"):
      parts.append(f"Race Allowed: {'yes' if gate.get('race_allowed') else 'no'}")
      parts.append("source selector")
    else:
      parts.append("Race Allowed: legacy config")

    selected_race = gate.get("selected_race")
    if selected_race:
      parts.append(f"selected {selected_race}")
    return " | ".join(parts)

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

  def _race_selector_status_text(self):
    summary = summarize_selector_state(
      getattr(config, "OPERATOR_RACE_SELECTOR", None),
      legacy_schedule=getattr(config, "RACE_SCHEDULE_CONF", []),
      use_ui_fallback=True,
    )
    selector_enabled = bool((getattr(config, "OPERATOR_RACE_SELECTOR", {}) or {}).get("enabled"))
    source = "selector" if selector_enabled else "legacy"
    return f"{source}: {summary['selected_count']} sel / {summary['blocked_count']} blocked"

  def _set_execution_intent(self):
    if self._execution_intent_var is None:
      return
    bot.set_execution_intent(self._execution_intent_var.get())
    self.publish()

  def _get_region_adjuster_profile_options(self):
    settings = dict(getattr(config, "REGION_ADJUSTER_CONFIG", {}) or {})
    profiles, active_profile, _ = resolve_region_adjuster_profiles(settings)
    return profiles, active_profile

  def _refresh_region_adjuster_profile_menu(self):
    profiles, active_profile = self._get_region_adjuster_profile_options()
    self._region_adjuster_profiles = profiles
    if self._region_adjuster_profile_var is not None:
      self._region_adjuster_profile_var.set(active_profile)
    if self._region_adjuster_profile_menu is None:
      return
    menu = self._region_adjuster_profile_menu["menu"]
    menu.delete(0, "end")
    for name in profiles.keys():
      menu.add_command(label=name, command=tk._setit(self._region_adjuster_profile_var, name, self._set_region_adjuster_profile))

  def _set_region_adjuster_profile(self, selected_profile):
    profile_name = str(selected_profile or "").strip()
    if not profile_name:
      return
    profiles = self._region_adjuster_profiles or self._get_region_adjuster_profile_options()[0]
    overrides_path = profiles.get(profile_name)
    if not overrides_path:
      self._message_value.set(f"Unknown OCR preset: {profile_name}")
      return
    if not Path(overrides_path).exists():
      self._message_value.set(f"OCR preset file missing: {overrides_path}")
      return
    saved_profile = self._persist_config_value("debug.region_adjuster.active_profile", profile_name)
    saved_path = self._persist_config_value("debug.region_adjuster.overrides_path", overrides_path)
    if not (saved_profile and saved_path):
      self._message_value.set("Failed to save OCR preset.")
      return
    try:
      config.reload_config(print_config=False)
      reapply_configured_recognition_geometry(force_overrides=True)
    except Exception as exc:
      self._message_value.set(f"OCR preset saved, but reload failed: {exc}")
      return
    if self._region_adjuster_profile_var is not None:
      self._region_adjuster_profile_var.set(profile_name)
    self._region_adjuster_profiles = dict(profiles)
    self._message_value.set(f"OCR preset: {profile_name}.")
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

  def _toggle_trackblazer_use_new_planner(self):
    if self._trackblazer_use_new_planner_var is None:
      return
    enabled = bool(self._trackblazer_use_new_planner_var.get())
    bot.set_trackblazer_use_new_planner_enabled(enabled)
    if self._persist_config_value("planner.use_new_planner", enabled):
      try:
        config.reload_config(print_config=False)
      except Exception as exc:
        self._message_value.set(f"Planner toggle saved, but reload failed: {exc}")
        self.publish()
        return
    self._message_value.set(
      "Trackblazer planner path enabled."
      if enabled else
      "Trackblazer planner path disabled."
    )
    self.publish()

  def _toggle_skill_auto_buy(self):
    if self._skill_auto_buy_var is None:
      return
    enabled = bool(self._skill_auto_buy_var.get())
    bot.set_skill_auto_buy_enabled(enabled)
    self._message_value.set(
      "Skill auto-buy enabled."
      if enabled else
      "Skill auto-buy disabled."
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

  def _load_race_selector_entries(self):
    ui_state = get_selector_ui_state(
      getattr(config, "OPERATOR_RACE_SELECTOR", None),
      legacy_schedule=getattr(config, "RACE_SCHEDULE_CONF", []),
    )
    self._race_selector_entries = {
      (entry["year"], entry["date"]): dict(entry)
      for entry in ui_state.get("dates", [])
    }

  def _persist_race_selector_entries(self, message):
    payload = serialize_selector_payload(self._race_selector_entries, enabled=True)
    if not self._persist_config_value("operator_race_selector", payload):
      self._message_value.set("Failed to save race selector state.")
      return False
    try:
      config.reload_config(print_config=False)
    except Exception as exc:
      self._message_value.set(f"Saved race selector state, but reload failed: {exc}")
      return False
    if self._race_selector_status_var is not None:
      self._race_selector_status_var.set(self._race_selector_status_text())
    self._message_value.set(message)
    self.publish()
    return True

  def _clear_race_selector_window_state(self):
    self._race_selector_window = None
    self._race_selector_summary_var = None
    self._race_selector_year_bodies = {}
    if self._race_selector_date_window is not None and self._race_selector_date_window.winfo_exists():
      self._race_selector_date_window.destroy()
    self._race_selector_date_window = None
    self._race_selector_date_context = None

  def _open_race_selector_window(self):
    if self._root is None:
      return
    if self._race_selector_window is not None and self._race_selector_window.winfo_exists():
      self._refresh_race_selector_window()
      self._race_selector_window.deiconify()
      self._race_selector_window.lift()
      self._race_selector_window.focus_force()
      return

    self._load_race_selector_entries()

    window = tk.Toplevel(self._root)
    window.title("Race Selector")
    window.configure(bg="#101418")
    window.geometry("1120x760")
    window.minsize(900, 560)
    window.transient(self._root)

    header = tk.Frame(window, bg="#101418", padx=14, pady=12)
    header.pack(fill=tk.X)
    header.columnconfigure(0, weight=1)
    header.columnconfigure(1, weight=0)

    title_block = tk.Frame(header, bg="#101418")
    title_block.grid(row=0, column=0, sticky="w")
    tk.Label(
      title_block,
      text="Race Selector",
      fg="white",
      bg="#101418",
      font=("Helvetica", 16, "bold"),
      anchor="w",
    ).pack(anchor="w")
    self._race_selector_summary_var = tk.StringVar(value="")
    tk.Label(
      title_block,
      textvariable=self._race_selector_summary_var,
      fg="#8bd5ca",
      bg="#101418",
      anchor="w",
      justify="left",
    ).pack(anchor="w", pady=(2, 0))
    tk.Label(
      title_block,
      text="Click a date card to pick a race. The checkbox on each card is the authoritative race-allowed gate for that date.",
      fg="#9aa4ad",
      bg="#101418",
      anchor="w",
      justify="left",
      wraplength=760,
    ).pack(anchor="w", pady=(6, 0))

    controls = tk.Frame(header, bg="#101418")
    controls.grid(row=0, column=1, sticky="e")
    tk.Button(controls, text="Clear All", command=self._clear_all_race_selector_entries).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(controls, text="Close", command=window.destroy).pack(side=tk.LEFT)

    notebook = ttk.Notebook(window)
    notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
    self._race_selector_year_bodies = {}
    for year in RACE_SELECTOR_YEARS:
      outer = tk.Frame(notebook, bg="#101418")
      outer.rowconfigure(0, weight=1)
      outer.columnconfigure(0, weight=1)

      canvas = tk.Canvas(outer, bg="#101418", highlightthickness=0)
      scrollbar = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
      body = tk.Frame(canvas, bg="#101418")
      window_id = canvas.create_window((0, 0), window=body, anchor="nw")
      canvas.configure(yscrollcommand=scrollbar.set)
      canvas.grid(row=0, column=0, sticky="nsew")
      scrollbar.grid(row=0, column=1, sticky="ns")
      body.bind("<Configure>", lambda _event, c=canvas: c.configure(scrollregion=c.bbox("all")))
      canvas.bind("<Configure>", lambda event, c=canvas, wid=window_id: c.itemconfigure(wid, width=event.width))

      notebook.add(outer, text=year)
      self._race_selector_year_bodies[year] = body

    self._race_selector_window = window
    window.bind(
      "<Destroy>",
      lambda event, target=window: self._clear_race_selector_window_state() if event.widget is target else None,
    )
    self._refresh_race_selector_window()

  def _refresh_race_selector_window(self):
    if self._race_selector_window is None or not self._race_selector_window.winfo_exists():
      return
    selected_count = sum(1 for entry in self._race_selector_entries.values() if str(entry.get("name") or "").strip())
    blocked_count = sum(1 for entry in self._race_selector_entries.values() if not bool(entry.get("race_allowed", True)))
    if self._race_selector_summary_var is not None:
      self._race_selector_summary_var.set(
        f"Console override active. {selected_count} selected date(s), {blocked_count} blocked date(s)."
      )

    for year, body in self._race_selector_year_bodies.items():
      for child in body.winfo_children():
        child.destroy()
      for column in range(4):
        body.grid_columnconfigure(column, weight=1, uniform=f"{year}-cards")

      for index, date in enumerate(RACE_SELECTOR_CALENDAR):
        row = index // 4
        column = index % 4
        entry = dict(self._race_selector_entries.get((year, date)) or {
          "year": year,
          "date": date,
          "name": "",
          "race_allowed": True,
        })
        races = get_races_for_date(year, date)
        selected_name = str(entry.get("name") or "").strip()
        race_allowed = bool(entry.get("race_allowed", True))
        selected_race = next((race for race in races if race.get("name") == selected_name), None)

        if not race_allowed:
          card_bg = "#2a1818"
          border = "#b55"
        elif selected_name:
          card_bg = "#15283b"
          border = "#3b82f6"
        else:
          card_bg = "#161d24"
          border = "#2d333b"

        card = tk.Frame(
          body,
          bg=card_bg,
          highlightbackground=border,
          highlightthickness=1,
          padx=8,
          pady=8,
          cursor="hand2",
        )
        card.grid(row=row, column=column, sticky="nsew", padx=6, pady=6)

        title_row = tk.Frame(card, bg=card_bg)
        title_row.pack(fill=tk.X)
        date_label = tk.Label(
          title_row,
          text=date,
          fg="white",
          bg=card_bg,
          anchor="w",
          font=("Helvetica", 11, "bold"),
          cursor="hand2",
        )
        date_label.pack(side=tk.LEFT, anchor="w")
        badge_text = "Blocked" if not race_allowed else ("Selected" if selected_name else "Open")
        badge_fg = "#ffb4b4" if not race_allowed else ("#8bd5ca" if selected_name else "#9aa4ad")
        badge = tk.Label(
          title_row,
          text=badge_text,
          fg=badge_fg,
          bg=card_bg,
          anchor="e",
          font=("Helvetica", 9, "bold"),
          cursor="hand2",
        )
        badge.pack(side=tk.RIGHT, anchor="e")

        allow_var = tk.BooleanVar(value=race_allowed)
        allow_toggle = tk.Checkbutton(
          card,
          text="Race allowed",
          variable=allow_var,
          command=lambda y=year, d=date, var=allow_var: self._toggle_race_selector_allowed(y, d, var.get()),
          fg="#d6dde5",
          bg=card_bg,
          selectcolor="#192028",
          activebackground=card_bg,
          activeforeground="white",
          anchor="w",
          justify="left",
        )
        allow_toggle.pack(fill=tk.X, pady=(8, 6))
        allow_toggle._codex_var = allow_var

        if selected_race:
          subtitle_text = f"{selected_race.get('name')} ({selected_race.get('grade') or '-'})"
          detail_text = (
            f"{selected_race.get('terrain') or '-'} • "
            f"{(selected_race.get('distance') or {}).get('type') or '-'} "
            f"{(selected_race.get('distance') or {}).get('meters') or '-'}m"
          )
        elif races:
          grades = sorted({str(race.get("grade") or "").strip() for race in races if race.get("grade")})
          grade_text = ", ".join(grade for grade in grades if grade) or "listed races"
          subtitle_text = f"{len(races)} listed race(s)"
          detail_text = grade_text
        else:
          subtitle_text = "No listed races"
          detail_text = "Gate still blocks rival and optional races"

        subtitle = tk.Label(
          card,
          text=subtitle_text,
          fg="#d6dde5",
          bg=card_bg,
          anchor="w",
          justify="left",
          wraplength=220,
          cursor="hand2",
        )
        subtitle.pack(fill=tk.X)
        detail = tk.Label(
          card,
          text=detail_text,
          fg="#9aa4ad",
          bg=card_bg,
          anchor="w",
          justify="left",
          wraplength=220,
          cursor="hand2",
        )
        detail.pack(fill=tk.X, pady=(4, 0))

        for widget in (card, title_row, date_label, badge, subtitle, detail):
          widget.bind("<Button-1>", lambda _event, y=year, d=date: self._open_race_selector_date_popup(y, d))

    if self._race_selector_date_window is not None and self._race_selector_date_window.winfo_exists():
      self._refresh_race_selector_date_popup()

  def _toggle_race_selector_allowed(self, year, date, allowed):
    key = (year, date)
    entry = dict(self._race_selector_entries.get(key) or {
      "year": year,
      "date": date,
      "name": "",
      "race_allowed": True,
    })
    entry["race_allowed"] = bool(allowed)
    if entry["race_allowed"] and not str(entry.get("name") or "").strip():
      self._race_selector_entries.pop(key, None)
    else:
      self._race_selector_entries[key] = entry
    if self._persist_race_selector_entries(
      f"Race gate {'enabled' if allowed else 'blocked'} for {year} {date}."
    ):
      self._refresh_race_selector_window()

  def _set_race_selector_race(self, year, date, race_name):
    key = (year, date)
    entry = dict(self._race_selector_entries.get(key) or {
      "year": year,
      "date": date,
      "name": "",
      "race_allowed": True,
    })
    selected_name = str(race_name or "").strip()
    if selected_name and entry.get("name") == selected_name:
      selected_name = ""
    entry["name"] = selected_name
    if not entry["name"] and bool(entry.get("race_allowed", True)):
      self._race_selector_entries.pop(key, None)
    else:
      self._race_selector_entries[key] = entry
    action_text = f"Selected {selected_name}" if selected_name else "Cleared race selection"
    if self._persist_race_selector_entries(f"{action_text} for {year} {date}."):
      self._refresh_race_selector_window()

  def _clear_all_race_selector_entries(self):
    self._race_selector_entries = {}
    if self._persist_race_selector_entries("Cleared all race selector dates and gates."):
      self._refresh_race_selector_window()

  def _open_race_selector_date_popup(self, year, date):
    parent = self._race_selector_window or self._root
    if parent is None:
      return

    if self._race_selector_date_window is not None and self._race_selector_date_window.winfo_exists():
      if self._race_selector_date_context != (year, date):
        self._race_selector_date_window.destroy()
      else:
        self._refresh_race_selector_date_popup()
        self._race_selector_date_window.deiconify()
        self._race_selector_date_window.lift()
        self._race_selector_date_window.focus_force()
        return

    window = tk.Toplevel(parent)
    window.configure(bg="#101418")
    window.geometry("760x520")
    window.minsize(620, 420)
    window.transient(parent)
    self._race_selector_date_window = window
    self._race_selector_date_context = (year, date)
    window.bind(
      "<Destroy>",
      lambda event, target=window: self._clear_race_selector_date_popup() if event.widget is target else None,
    )
    self._refresh_race_selector_date_popup()

  def _clear_race_selector_date_popup(self):
    self._race_selector_date_window = None
    self._race_selector_date_context = None

  def _refresh_race_selector_date_popup(self):
    window = self._race_selector_date_window
    context = self._race_selector_date_context
    if window is None or context is None or not window.winfo_exists():
      return

    year, date = context
    entry = dict(self._race_selector_entries.get((year, date)) or {
      "year": year,
      "date": date,
      "name": "",
      "race_allowed": True,
    })
    selected_name = str(entry.get("name") or "").strip()
    race_allowed = bool(entry.get("race_allowed", True))
    races = get_races_for_date(year, date)

    window.title(f"Race Selector - {date} - {year}")
    for child in window.winfo_children():
      child.destroy()

    header = tk.Frame(window, bg="#101418", padx=12, pady=12)
    header.pack(fill=tk.X)
    header.columnconfigure(0, weight=1)

    tk.Label(
      header,
      text=f"{date} - {year}",
      fg="white",
      bg="#101418",
      font=("Helvetica", 14, "bold"),
      anchor="w",
    ).grid(row=0, column=0, sticky="w")
    tk.Button(header, text="Close", command=window.destroy).grid(row=0, column=1, sticky="e")

    allow_var = tk.BooleanVar(value=race_allowed)
    allow_toggle = tk.Checkbutton(
      header,
      text="Race allowed",
      variable=allow_var,
      command=lambda y=year, d=date, var=allow_var: self._toggle_race_selector_allowed(y, d, var.get()),
      fg="#d6dde5",
      bg="#101418",
      selectcolor="#192028",
      activebackground="#101418",
      activeforeground="white",
    )
    allow_toggle.grid(row=1, column=0, sticky="w", pady=(8, 0))
    allow_toggle._codex_var = allow_var

    tk.Button(
      header,
      text="Clear Selection",
      state=tk.NORMAL if selected_name else tk.DISABLED,
      command=lambda y=year, d=date: self._set_race_selector_race(y, d, ""),
    ).grid(row=1, column=1, sticky="e", pady=(8, 0))

    tk.Label(
      window,
      text=(
        "Selected race overrides legacy config for this date. "
        "If no race is selected, the date gate still applies to optional and rival races."
      ),
      fg="#9aa4ad",
      bg="#101418",
      anchor="w",
      justify="left",
      wraplength=700,
      padx=12,
    ).pack(fill=tk.X, pady=(0, 8))

    body = tk.Frame(window, bg="#101418", padx=12, pady=0)
    body.pack(fill=tk.BOTH, expand=True)

    if not races:
      tk.Label(
        body,
        text="No listed races on this date. The race-allowed checkbox still blocks rival and other optional race entry.",
        fg="#d6dde5",
        bg="#101418",
        anchor="w",
        justify="left",
        wraplength=680,
      ).pack(fill=tk.X, pady=(12, 0))
      return

    for race in races:
      race_name = str(race.get("name") or "")
      selected = selected_name == race_name
      card_bg = "#15283b" if selected else "#161d24"
      border = "#3b82f6" if selected else "#2d333b"
      card = tk.Frame(
        body,
        bg=card_bg,
        highlightbackground=border,
        highlightthickness=1,
        padx=10,
        pady=10,
        cursor="hand2",
      )
      card.pack(fill=tk.X, pady=(0, 8))

      title_row = tk.Frame(card, bg=card_bg)
      title_row.pack(fill=tk.X)
      tk.Label(
        title_row,
        text=race_name,
        fg="white",
        bg=card_bg,
        anchor="w",
        font=("Helvetica", 11, "bold"),
        cursor="hand2",
      ).pack(side=tk.LEFT, anchor="w")
      tk.Label(
        title_row,
        text=race.get("grade") or "-",
        fg="#8bd5ca" if selected else "#9aa4ad",
        bg=card_bg,
        anchor="e",
        font=("Helvetica", 10, "bold"),
        cursor="hand2",
      ).pack(side=tk.RIGHT, anchor="e")

      terrain = race.get("terrain") or "-"
      distance = race.get("distance") or {}
      distance_label = f"{distance.get('type') or '-'} {distance.get('meters') or '-'}m"
      racetrack = race.get("racetrack") or "-"
      fans = race.get("fans") or {}
      tk.Label(
        card,
        text=f"{racetrack} • {terrain} • {distance_label}",
        fg="#d6dde5",
        bg=card_bg,
        anchor="w",
        justify="left",
        cursor="hand2",
      ).pack(fill=tk.X, pady=(6, 0))
      tk.Label(
        card,
        text=f"Fans +{fans.get('gained') or '-'} • Req {fans.get('required') or '-'}",
        fg="#9aa4ad",
        bg=card_bg,
        anchor="w",
        justify="left",
        cursor="hand2",
      ).pack(fill=tk.X, pady=(2, 8))

      action_button = tk.Button(
        card,
        text="Selected" if selected else "Use This Race",
        command=lambda y=year, d=date, name=race_name: self._set_race_selector_race(y, d, name),
      )
      action_button.pack(anchor="e")

      for widget in card.winfo_children():
        if widget is action_button:
          continue
        widget.bind("<Button-1>", lambda _event, y=year, d=date, name=race_name: self._set_race_selector_race(y, d, name))
      card.bind("<Button-1>", lambda _event, y=year, d=date, name=race_name: self._set_race_selector_race(y, d, name))

  def _launch_asset_creator(self):
    from core.region_adjuster.asset_creator import AssetCreatorWindow
    context = {}
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
      context["capture_space"] = "full_display"
      context["game_window_bbox"] = str(game_window_bbox)
    except Exception:
      pass
    context = {k: v for k, v in context.items() if v}
    AssetCreatorWindow(parent=self._root, context=context)

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
        if self._root is not None:
          self._root.after(0, self._refresh_region_adjuster_profile_menu)
        reapply_configured_recognition_geometry(force_overrides=True)
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
    saved_policy = normalize_shop_policy(getattr(config, "TRACKBLAZER_SHOP_POLICY", None))
    self._shop_policy_catalog_timing_var = tk.BooleanVar(value=saved_policy.get("catalog_timing_overrides", True))
    tk.Checkbutton(
      header,
      text="Catalog timing overrides",
      variable=self._shop_policy_catalog_timing_var,
      fg="#d6dde5",
      bg="#101418",
      selectcolor="#192028",
      activebackground="#101418",
      activeforeground="#d6dde5",
      command=self._refresh_trackblazer_shop_policy_window,
    ).grid(row=0, column=1, padx=(12, 0))
    tk.Button(header, text="Reload", command=self._reload_trackblazer_shop_policy_window).grid(row=0, column=2, padx=(8, 0))
    tk.Button(header, text="Reset Defaults", command=self._reset_trackblazer_shop_policy_defaults).grid(row=0, column=3, padx=(8, 0))
    tk.Button(header, text="Save", command=self._save_trackblazer_shop_policy_from_window).grid(row=0, column=4, padx=(8, 0))

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
    self._shop_policy_catalog_timing_var = None
    self._shop_policy_canvas = None
    self._shop_policy_body = None

  def _reload_trackblazer_shop_policy_window(self):
    if self._shop_policy_catalog_timing_var is not None:
      saved_policy = normalize_shop_policy(getattr(config, "TRACKBLAZER_SHOP_POLICY", None))
      self._shop_policy_catalog_timing_var.set(saved_policy.get("catalog_timing_overrides", True))
    self._refresh_trackblazer_shop_policy_window()

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
    preview_policy = dict(normalized_policy)
    if self._shop_policy_catalog_timing_var is not None:
      preview_policy["catalog_timing_overrides"] = self._shop_policy_catalog_timing_var.get()
    items = get_effective_shop_items(
      policy=preview_policy,
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

    catalog_timing = current_policy.get("catalog_timing_overrides", True)
    if self._shop_policy_catalog_timing_var is not None:
      catalog_timing = self._shop_policy_catalog_timing_var.get()
    policy = {
      "version": int(current_policy.get("version", 1)),
      "catalog_timing_overrides": catalog_timing,
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

  def _get_active_planner_policy(self):
    return config.normalize_trackblazer_planner_policy(getattr(config, "TRACKBLAZER_PLANNER_POLICY", None))

  # ── Planner Flow window ──────────────────────────────────────────────

  _FLOW_BG = "#101418"
  _FLOW_CANVAS_BG = "#0d1117"
  _FLOW_DECISION_FILL = "#1c2333"
  _FLOW_DECISION_OUTLINE = "#7cb3ff"
  _FLOW_ACTION_COLORS = {
    "train": ("#1a3a1a", "#4caf50"),
    "race": ("#1a2a3a", "#42a5f5"),
    "rest": ("#3a2a1a", "#ff9800"),
    "rival_race": ("#1a2a3a", "#7e57c2"),
  }
  _FLOW_LINE_COLOR = "#4a5568"
  _FLOW_TEXT_COLOR = "#d6dde5"
  _FLOW_DIM_TEXT = "#8b949e"
  _FLOW_YES_COLOR = "#4caf50"
  _FLOW_NO_COLOR = "#ef5350"

  def _open_planner_flow_window(self):
    if self._root is None:
      return
    existing = self._planner_flow_window
    if existing is not None:
      try:
        if existing.winfo_exists():
          self._refresh_planner_flow_values()
          existing.lift()
          return
      except Exception:
        pass

    window = tk.Toplevel(self._root)
    window.title("Planner Flow")
    window.configure(bg=self._FLOW_BG)
    window.geometry("920x860")
    window.resizable(True, True)
    window.minsize(800, 700)
    window.bind(
      "<Destroy>",
      lambda event, w=window: self._clear_planner_flow_window() if event.widget is w else None,
    )

    header = tk.Frame(window, bg=self._FLOW_BG, padx=8, pady=6)
    header.pack(fill=tk.X)
    tk.Label(
      header, text="Trackblazer decision flow", fg="#9aa4ad", bg=self._FLOW_BG,
    ).pack(side=tk.LEFT)

    canvas_frame = tk.Frame(window, bg=self._FLOW_BG)
    canvas_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))
    canvas_frame.rowconfigure(0, weight=1)
    canvas_frame.columnconfigure(0, weight=1)

    canvas = tk.Canvas(canvas_frame, bg=self._FLOW_CANVAS_BG, highlightthickness=0)
    v_scroll = tk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=canvas.yview)
    canvas.configure(yscrollcommand=v_scroll.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    v_scroll.grid(row=0, column=1, sticky="ns")
    self._planner_flow_canvas = canvas

    behavior = self._get_active_training_behavior()
    planner = self._get_active_planner_policy()
    self._planner_flow_vars = {}
    self._planner_flow_node_items = {}

    self._draw_planner_flow(canvas, behavior, planner)

    buttons = tk.Frame(window, bg=self._FLOW_BG, padx=8, pady=8)
    buttons.pack(fill=tk.X)
    tk.Button(buttons, text="Save", command=self._save_planner_flow).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(buttons, text="Reset Defaults", command=self._reset_planner_flow_defaults).pack(side=tk.LEFT, padx=(0, 8))
    tk.Label(
      buttons, text="Thresholds are editable — changes apply after Save.",
      fg="#8b949e", bg=self._FLOW_BG,
    ).pack(side=tk.LEFT, padx=(12, 0))

    self._planner_flow_window = window

  def _clear_planner_flow_window(self):
    self._planner_flow_window = None
    self._planner_flow_canvas = None
    self._planner_flow_vars = {}
    self._planner_flow_node_items = {}

  def _refresh_planner_flow_values(self):
    behavior = self._get_active_training_behavior()
    planner = self._get_active_planner_policy()
    var_map = {
      "optional_race_threshold": int(planner.get(
        "training_overrides_race_threshold",
        behavior.get("optional_race_training_threshold", 40),
      )),
      "committed_training_threshold": int(behavior.get("committed_training_score_threshold", 35)),
      "strong_training_threshold": int(behavior.get("strong_training_score_threshold", 40)),
      "race_lookahead_exceptional": int(behavior.get("race_lookahead_exceptional_score", 40)),
      "race_lookahead_conserve_pct": int(behavior.get("race_lookahead_conserve_threshold", 60)),
      "weak_fallback_race_threshold": int(behavior.get("weak_training_fallback_race_score_threshold", 30)),
      "low_energy_rest_pct": int(behavior.get("weak_training_fallback_race_low_energy_rest_pct", 2)),
      "rest_exempt_score": int(behavior.get("weak_training_fallback_race_low_energy_rest_exempt_score", 35)),
    }
    for key, val in var_map.items():
      var = self._planner_flow_vars.get(key)
      if var is not None:
        var.set(str(val))

  def _draw_planner_flow(self, canvas, behavior, planner):
    CW = 880
    y = 30

    def _val(key, default):
      return int(planner.get(key, behavior.get(key, default)))

    optional_race_thr = _val("training_overrides_race_threshold", behavior.get("optional_race_training_threshold", 40))
    committed_thr = int(behavior.get("committed_training_score_threshold", 35))
    strong_thr = int(behavior.get("strong_training_score_threshold", 40))
    lookahead_exceptional = int(behavior.get("race_lookahead_exceptional_score", 40))
    lookahead_conserve = int(behavior.get("race_lookahead_conserve_threshold", 60))
    weak_fallback_thr = int(behavior.get("weak_training_fallback_race_score_threshold", 30))
    low_energy_rest_pct = int(behavior.get("weak_training_fallback_race_low_energy_rest_pct", 2))
    rest_exempt = int(behavior.get("weak_training_fallback_race_low_energy_rest_exempt_score", 35))

    cx = CW // 2

    def draw_action_box(x, y, label, action_type, w=140, h=36):
      fill, outline = self._FLOW_ACTION_COLORS.get(action_type, ("#192028", "#9aa4ad"))
      r = 6
      x0, y0, x1, y1 = x - w // 2, y - h // 2, x + w // 2, y + h // 2
      canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=2)
      canvas.create_text(x, y, text=label, fill="white", font=("Helvetica", 11, "bold"))
      return y1

    def draw_decision(x, y, lines, w=260, h=52):
      hw, hh = w // 2, h // 2
      pts = [x, y - hh, x + hw, y, x, y + hh, x - hw, y]
      canvas.create_polygon(pts, fill=self._FLOW_DECISION_FILL, outline=self._FLOW_DECISION_OUTLINE, width=2)
      text = "\n".join(lines) if isinstance(lines, list) else lines
      canvas.create_text(x, y, text=text, fill=self._FLOW_TEXT_COLOR, font=("Helvetica", 9), justify="center")
      return y + hh

    def draw_arrow(x1, y1, x2, y2, label=None, color=None):
      line_color = color or self._FLOW_LINE_COLOR
      canvas.create_line(x1, y1, x2, y2, fill=line_color, width=2, arrow=tk.LAST, arrowshape=(8, 10, 4))
      if label:
        lbl_color = self._FLOW_YES_COLOR if label.lower() == "yes" else self._FLOW_NO_COLOR if label.lower() == "no" else self._FLOW_DIM_TEXT
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        canvas.create_text(mx - 14, my, text=label, fill=lbl_color, font=("Helvetica", 8, "bold"), anchor="e")

    def draw_arrow_h(x1, y1, x2, y2, label=None, color=None):
      line_color = color or self._FLOW_LINE_COLOR
      mid_x = (x1 + x2) / 2
      canvas.create_line(x1, y1, mid_x, y1, fill=line_color, width=2)
      canvas.create_line(mid_x, y1, mid_x, y2, fill=line_color, width=2)
      canvas.create_line(mid_x, y2, x2, y2, fill=line_color, width=2, arrow=tk.LAST, arrowshape=(8, 10, 4))
      if label:
        lbl_color = self._FLOW_YES_COLOR if label.lower() == "yes" else self._FLOW_NO_COLOR if label.lower() == "no" else self._FLOW_DIM_TEXT
        canvas.create_text(x1 + 10, y1 - 10, text=label, fill=lbl_color, font=("Helvetica", 8, "bold"), anchor="w")

    def place_spinbox(key, x, y, default, from_=0, to_=200, width=5):
      var = tk.StringVar(value=str(default))
      self._planner_flow_vars[key] = var
      spin = tk.Spinbox(
        canvas, from_=from_, to=to_, width=width, textvariable=var,
        bg="#192028", fg="white", buttonbackground="#2d333b", insertbackground="white",
        font=("Helvetica", 10, "bold"), justify="center",
      )
      item_id = canvas.create_window(x, y, window=spin)
      self._planner_flow_node_items[key] = item_id
      return var

    # ── Row 0: Start ──
    draw_action_box(cx, y, "Training Selected", "train", w=180, h=32)
    y += 32

    # ── Row 1: Scheduled Race? ──
    draw_arrow(cx, y, cx, y + 30)
    y += 30 + 26
    bottom = draw_decision(cx, y, "Scheduled Race?")
    race_box_x = cx + 250
    draw_arrow_h(cx + 130, y, race_box_x - 70, y, "Yes")
    draw_action_box(race_box_x, y, "DO RACE", "race")
    y = bottom + 8

    # ── Row 2: Race Lookahead Conserve? ──
    draw_arrow(cx, y, cx, y + 30)
    y += 30 + 26
    canvas.create_text(cx + 145, y - 8, text=f"energy < conserve %", fill=self._FLOW_DIM_TEXT, font=("Helvetica", 8), anchor="w")
    bottom = draw_decision(cx, y, ["Race Lookahead:", "Conserve energy?"], w=280, h=56)
    place_spinbox("race_lookahead_conserve_pct", cx + 260, y + 14, lookahead_conserve, to_=100)
    no_x = cx + 280
    canvas.create_text(no_x - 10, y - 22, text="No", fill=self._FLOW_NO_COLOR, font=("Helvetica", 8, "bold"), anchor="e")
    canvas.create_line(cx + 140, y, no_x, y, fill=self._FLOW_LINE_COLOR, width=2)
    canvas.create_line(no_x, y, no_x, bottom + 120, fill=self._FLOW_LINE_COLOR, width=2)
    canvas.create_line(no_x, bottom + 120, cx, bottom + 120, fill=self._FLOW_LINE_COLOR, width=2, arrow=tk.LAST, arrowshape=(8, 10, 4))
    y = bottom + 8

    # ── Row 3: Exceptional training? ──
    draw_arrow(cx, y, cx, y + 24, label="Yes")
    y += 24 + 26
    bottom = draw_decision(cx, y, ["Training score >=", "exceptional?"], w=260, h=52)
    place_spinbox("race_lookahead_exceptional", cx + 10, y + 26, lookahead_exceptional)
    train_box_x = cx + 250
    draw_arrow_h(cx + 130, y, train_box_x - 80, y, "Yes")
    draw_action_box(train_box_x, y, "TRAIN (+Vita)", "train", w=150)
    y = bottom + 8

    # ── Rest for race ──
    draw_arrow(cx, y, cx, y + 24, label="No")
    y += 24 + 16
    draw_action_box(cx, y, "REST (conserve)", "rest", w=170)
    y += 28

    # ── Row 4: Check optional race (reconnect from No branch) ──
    y += 16
    canvas.create_text(cx, y, text="─── Check Optional Race ───", fill="#4a5568", font=("Helvetica", 9))
    y += 20

    # ── Row 5: Race gate blocked? ──
    draw_arrow(cx, y, cx, y + 28)
    y += 28 + 26
    bottom = draw_decision(cx, y, "Race gate blocked?", w=240, h=48)

    blocked_x = cx - 200
    unblocked_x = cx + 200
    canvas.create_line(cx - 120, y, blocked_x + 60, y, fill=self._FLOW_LINE_COLOR, width=2)
    canvas.create_text(blocked_x + 70, y - 12, text="Yes", fill=self._FLOW_YES_COLOR, font=("Helvetica", 8, "bold"))
    canvas.create_line(cx + 120, y, unblocked_x - 60, y, fill=self._FLOW_LINE_COLOR, width=2)
    canvas.create_text(unblocked_x - 70, y - 12, text="No", fill=self._FLOW_NO_COLOR, font=("Helvetica", 8, "bold"))

    y_branch = y + 40

    # ── Left branch: blocked + score < min → REST ──
    canvas.create_line(blocked_x, y + 24, blocked_x, y_branch + 26, fill=self._FLOW_LINE_COLOR, width=2, arrow=tk.LAST, arrowshape=(8, 10, 4))
    draw_decision(blocked_x, y_branch + 52, ["Score <", "race threshold?"], w=220, h=48)
    place_spinbox("optional_race_threshold", blocked_x + 10, y_branch + 78, optional_race_thr)

    rest_blocked_y = y_branch + 52 + 40
    draw_arrow(blocked_x, rest_blocked_y, blocked_x, rest_blocked_y + 30, label="Yes")
    draw_action_box(blocked_x, rest_blocked_y + 46, "REST", "rest", w=100)

    train_from_blocked_x = blocked_x - 140
    canvas.create_line(blocked_x - 110, y_branch + 52, train_from_blocked_x + 50, y_branch + 52, fill=self._FLOW_LINE_COLOR, width=2)
    canvas.create_text(train_from_blocked_x + 60, y_branch + 40, text="No", fill=self._FLOW_NO_COLOR, font=("Helvetica", 8, "bold"))
    draw_action_box(train_from_blocked_x, y_branch + 52, "TRAIN", "train", w=90)

    # ── Right branch: unblocked + score <= min → rival race or rest ──
    canvas.create_line(unblocked_x, y + 24, unblocked_x, y_branch + 26, fill=self._FLOW_LINE_COLOR, width=2, arrow=tk.LAST, arrowshape=(8, 10, 4))
    draw_decision(unblocked_x, y_branch + 52, ["Score <=", "race threshold?"], w=220, h=48)

    rival_y = y_branch + 52 + 40
    draw_arrow(unblocked_x, rival_y, unblocked_x, rival_y + 28, label="Yes")

    # Low energy sub-check
    low_e_y = rival_y + 28 + 26
    draw_decision(unblocked_x, low_e_y, ["Low energy", f"(< rest %)"], w=200, h=44)
    place_spinbox("low_energy_rest_pct", unblocked_x + 10, low_e_y + 22, low_energy_rest_pct, to_=100)

    rest_low_x = unblocked_x + 160
    draw_arrow_h(unblocked_x + 100, low_e_y, rest_low_x - 50, low_e_y, "Yes")
    draw_action_box(rest_low_x, low_e_y, "REST", "rest", w=90)

    rival_box_y = low_e_y + 40
    draw_arrow(unblocked_x, rival_box_y - 18, unblocked_x, rival_box_y + 8, label="No")
    draw_action_box(unblocked_x, rival_box_y + 24, "RIVAL RACE", "rival_race", w=140)

    train_unblocked_x = unblocked_x + 200
    canvas.create_line(unblocked_x + 110, y_branch + 52, train_unblocked_x - 45, y_branch + 52, fill=self._FLOW_LINE_COLOR, width=2)
    canvas.create_text(train_unblocked_x - 55, y_branch + 40, text="No", fill=self._FLOW_NO_COLOR, font=("Helvetica", 8, "bold"))
    draw_action_box(train_unblocked_x, y_branch + 52, "TRAIN", "train", w=90)

    # ── Weak fallback race section ──
    y_weak = rival_box_y + 60
    canvas.create_text(cx, y_weak, text="─── Weak Training Fallback (post-Classic Sep) ───", fill="#4a5568", font=("Helvetica", 9))
    y_weak += 20

    draw_arrow(cx, y_weak, cx, y_weak + 28)
    y_weak += 28 + 26
    draw_decision(cx, y_weak, ["Score <", "weak fallback?"], w=240, h=48)
    place_spinbox("weak_fallback_race_threshold", cx + 10, y_weak + 26, weak_fallback_thr)
    sched_race_x = cx + 250
    draw_arrow_h(cx + 120, y_weak, sched_race_x - 80, y_weak, "Yes")
    draw_action_box(sched_race_x, y_weak, "SCHEDULE RACE", "race", w=160)

    y_weak_no = y_weak + 38
    draw_arrow(cx, y_weak_no - 12, cx, y_weak_no + 18, label="No")
    draw_action_box(cx, y_weak_no + 34, "TRAIN", "train", w=100)

    # ── Commit threshold legend ──
    y_legend = y_weak_no + 70
    canvas.create_text(cx, y_legend, text="─── Item-Use Commit Thresholds ───", fill="#4a5568", font=("Helvetica", 9))
    y_legend += 24
    canvas.create_text(cx - 160, y_legend, text="Strong training (use burst items):", fill=self._FLOW_DIM_TEXT, font=("Helvetica", 9), anchor="w")
    place_spinbox("strong_training_threshold", cx + 100, y_legend, strong_thr)
    y_legend += 30
    canvas.create_text(cx - 160, y_legend, text="Commit training (rescue w/ items):", fill=self._FLOW_DIM_TEXT, font=("Helvetica", 9), anchor="w")
    place_spinbox("committed_training_threshold", cx + 100, y_legend, committed_thr)
    y_legend += 30
    canvas.create_text(cx - 160, y_legend, text="Rest exempt score (low-energy fallback):", fill=self._FLOW_DIM_TEXT, font=("Helvetica", 9), anchor="w")
    place_spinbox("rest_exempt_score", cx + 130, y_legend, rest_exempt)

    # ── Legend ──
    y_legend += 40
    canvas.create_text(cx - 200, y_legend, text="Legend:", fill="#9aa4ad", font=("Helvetica", 9, "bold"), anchor="w")
    legend_items = [
      ("TRAIN", "train"),
      ("RACE", "race"),
      ("REST", "rest"),
      ("RIVAL", "rival_race"),
    ]
    lx = cx - 120
    for label, atype in legend_items:
      fill, outline = self._FLOW_ACTION_COLORS[atype]
      canvas.create_rectangle(lx, y_legend - 8, lx + 50, y_legend + 8, fill=fill, outline=outline, width=2)
      canvas.create_text(lx + 25, y_legend, text=label, fill="white", font=("Helvetica", 8, "bold"))
      lx += 80

    total_h = y_legend + 40
    canvas.configure(scrollregion=(0, 0, CW, total_h))

  def _save_planner_flow(self):
    current_policy = normalize_item_use_policy(getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None))
    current_planner = config.normalize_trackblazer_planner_policy(getattr(config, "TRACKBLAZER_PLANNER_POLICY", None))
    training_behavior = get_default_training_behavior_settings()
    settings = current_policy.get("settings", {}) if isinstance(current_policy.get("settings"), dict) else {}
    behavior_settings = settings.get("training_behavior", {}) if isinstance(settings.get("training_behavior"), dict) else {}
    training_behavior.update(behavior_settings)

    def _int_var(key, default):
      var = self._planner_flow_vars.get(key)
      if var is None:
        return default
      try:
        return max(0, int(var.get()))
      except (TypeError, ValueError):
        return default

    optional_race_threshold = _int_var("optional_race_threshold", 40)
    training_behavior["optional_race_training_threshold"] = optional_race_threshold
    training_behavior["committed_training_score_threshold"] = _int_var("committed_training_threshold", 35)
    training_behavior["strong_training_score_threshold"] = _int_var("strong_training_threshold", 40)
    training_behavior["race_lookahead_exceptional_score"] = _int_var("race_lookahead_exceptional", 40)
    training_behavior["race_lookahead_conserve_threshold"] = min(100, _int_var("race_lookahead_conserve_pct", 60))
    training_behavior["weak_training_fallback_race_score_threshold"] = _int_var("weak_fallback_race_threshold", 30)
    training_behavior["weak_training_fallback_race_low_energy_rest_pct"] = min(100, _int_var("low_energy_rest_pct", 2))
    training_behavior["weak_training_fallback_race_low_energy_rest_exempt_score"] = _int_var("rest_exempt_score", 35)

    current_planner["training_overrides_race_threshold"] = float(optional_race_threshold)

    policy = {
      "version": int(current_policy.get("version", 1)),
      "settings": {"training_behavior": training_behavior},
      "items": current_policy.get("items", {}),
    }
    if not self._persist_config_value("trackblazer.planner_policy", current_planner):
      self._message_value.set("Failed to save planner flow thresholds.")
      return
    if not self._persist_config_value("trackblazer.item_use_policy", policy):
      self._message_value.set("Failed to save planner flow behavior.")
      return

    try:
      config.reload_config(print_config=False)
    except Exception as exc:
      self._message_value.set(f"Saved planner flow, but reload failed: {exc}")
      return

    self._message_value.set("Saved planner flow thresholds.")
    self.publish()
    if self._stat_weights_window is not None:
      try:
        if self._stat_weights_window.winfo_exists():
          self._refresh_stat_weights_window()
      except Exception:
        pass

  def _reset_planner_flow_defaults(self):
    current_policy = normalize_item_use_policy(getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None))
    default_behavior = get_default_training_behavior_settings()
    default_planner = config.normalize_trackblazer_planner_policy({})

    policy = {
      "version": int(current_policy.get("version", 1)),
      "settings": {"training_behavior": default_behavior},
      "items": current_policy.get("items", {}),
    }
    if not self._persist_config_value("trackblazer.planner_policy", default_planner):
      self._message_value.set("Failed to reset planner flow thresholds.")
      return
    if not self._persist_config_value("trackblazer.item_use_policy", policy):
      self._message_value.set("Failed to reset planner flow behavior.")
      return

    try:
      config.reload_config(print_config=False)
    except Exception as exc:
      self._message_value.set(f"Reset planner flow, but reload failed: {exc}")
      return

    self._refresh_planner_flow_values()
    self._message_value.set("Reset planner flow to defaults.")
    self.publish()
    if self._stat_weights_window is not None:
      try:
        if self._stat_weights_window.winfo_exists():
          self._refresh_stat_weights_window()
      except Exception:
        pass

  # ── Stat Weights / Training Behavior window ────────────────────────

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
    window.geometry("820x760")
    window.minsize(720, 560)
    window.rowconfigure(1, weight=1)
    window.columnconfigure(0, weight=1)
    window.bind(
      "<Destroy>",
      lambda event, root_window=window: self._clear_stat_weights_window() if event.widget is root_window else None,
    )
    window.bind("<Command-s>", lambda event: self._save_stat_weights())
    window.bind("<Control-s>", lambda event: self._save_stat_weights())

    header = tk.Frame(window, bg="#101418", padx=8, pady=8)
    header.grid(row=0, column=0, sticky="ew")
    tk.Label(
      header,
      text="Training behavior settings",
      fg="#9aa4ad",
      bg="#101418",
    ).pack(side=tk.LEFT)

    body_frame = tk.Frame(window, bg="#101418")
    body_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
    body_frame.rowconfigure(0, weight=1)
    body_frame.columnconfigure(0, weight=1)

    canvas = tk.Canvas(body_frame, bg="#101418", highlightthickness=0)
    scrollbar = tk.Scrollbar(body_frame, orient=tk.VERTICAL, command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")

    body = tk.Frame(canvas, bg="#101418", padx=16, pady=4)
    body_window_id = canvas.create_window((0, 0), window=body, anchor="nw")
    body.bind(
      "<Configure>",
      lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas.bind(
      "<Configure>",
      lambda event, window_id=body_window_id: canvas.itemconfigure(window_id, width=event.width),
    )

    def _bind_mousewheel(_event):
      def _on_mousewheel(event):
        if event.delta:
          canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        elif getattr(event, "num", None) == 4:
          canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
          canvas.yview_scroll(1, "units")

      canvas._mousewheel_handler = _on_mousewheel
      canvas.bind_all("<MouseWheel>", _on_mousewheel)
      canvas.bind_all("<Button-4>", _on_mousewheel)
      canvas.bind_all("<Button-5>", _on_mousewheel)

    def _unbind_mousewheel(_event):
      canvas.unbind_all("<MouseWheel>")
      canvas.unbind_all("<Button-4>")
      canvas.unbind_all("<Button-5>")

    canvas.bind("<Enter>", _bind_mousewheel)
    canvas.bind("<Leave>", _unbind_mousewheel)

    active = self._get_active_stat_weights()
    self._stat_weights_entries = {}
    stat_weights_frame = tk.Frame(body, bg="#101418")
    stat_weights_frame.pack(fill=tk.X)
    tk.Label(
      stat_weights_frame,
      text="Stat weights for stat_focused scoring (gain × weight)",
      fg="#d6dde5",
      bg="#101418",
      font=("Helvetica", 10, "bold"),
      anchor="w",
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
    for row_idx, stat in enumerate(self._DEFAULT_STAT_WEIGHTS):
      label = self._STAT_LABELS.get(stat, stat)
      tk.Label(
        stat_weights_frame, text=label, fg="#d6dde5", bg="#101418", width=10, anchor="w",
      ).grid(row=row_idx + 1, column=0, sticky="w", pady=2)
      var = tk.StringVar(value=str(active.get(stat, 1.0)))
      entry = tk.Entry(stat_weights_frame, textvariable=var, width=8, bg="#192028", fg="white", insertbackground="white")
      entry.grid(row=row_idx + 1, column=1, sticky="w", padx=(8, 0), pady=2)
      self._stat_weights_entries[stat] = var

    training_behavior = self._get_active_training_behavior()
    behavior_frame = tk.Frame(body, bg="#101418", padx=16, pady=4)
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

    planner_behavior_frame = tk.Frame(body, bg="#101418", padx=16, pady=4)
    planner_behavior_frame.pack(fill=tk.X)
    tk.Label(
      planner_behavior_frame,
      text="Race / commit thresholds",
      fg="#d6dde5",
      bg="#101418",
      font=("Helvetica", 10, "bold"),
      anchor="w",
    ).grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 4))
    planner_behavior = self._get_active_planner_policy()
    tk.Label(
      planner_behavior_frame,
      text="Optional race below",
      fg="#d6dde5",
      bg="#101418",
      anchor="e",
    ).grid(row=1, column=0, sticky="e", padx=(0, 4), pady=2)
    self._optional_race_training_threshold_var = tk.StringVar(
      value=str(
        int(planner_behavior.get("training_overrides_race_threshold", get_training_behavior_optional_race_threshold()))
      )
    )
    tk.Spinbox(
      planner_behavior_frame,
      from_=0,
      to=200,
      width=5,
      textvariable=self._optional_race_training_threshold_var,
      bg="#192028",
      fg="white",
      buttonbackground="#2d333b",
    ).grid(row=1, column=1, sticky="w", pady=2)
    tk.Label(
      planner_behavior_frame,
      text="Commit training at",
      fg="#d6dde5",
      bg="#101418",
      anchor="e",
    ).grid(row=1, column=2, sticky="e", padx=(12, 4), pady=2)
    self._committed_training_score_threshold_var = tk.StringVar(
      value=str(get_training_behavior_committed_training_score_threshold())
    )
    tk.Spinbox(
      planner_behavior_frame,
      from_=0,
      to=200,
      width=5,
      textvariable=self._committed_training_score_threshold_var,
      bg="#192028",
      fg="white",
      buttonbackground="#2d333b",
    ).grid(row=1, column=3, sticky="w", pady=2)
    tk.Label(
      planner_behavior_frame,
      text=(
        "If the visible board is below the optional race threshold, racing stays valid. "
        "The commit threshold controls when item-use logic treats a training as strong enough to commit/rescue."
      ),
      fg="#8b949e",
      bg="#101418",
      justify="left",
      anchor="w",
      wraplength=700,
    ).grid(row=2, column=0, columnspan=6, sticky="ew", pady=(4, 0))

    optional_race_frame = tk.Frame(body, bg="#101418", padx=16, pady=4)
    optional_race_frame.pack(fill=tk.X)
    tk.Label(
      optional_race_frame,
      text="Zero-energy optional race",
      fg="#d6dde5",
      bg="#101418",
      font=("Helvetica", 10, "bold"),
      anchor="w",
    ).pack(anchor="w", pady=(0, 4))
    self._zero_energy_optional_race_rest_var = tk.BooleanVar(
      value=bool(training_behavior.get("prefer_rest_on_zero_energy_optional_race", True))
    )
    tk.Checkbutton(
      optional_race_frame,
      text="Prefer rest over fallback rival race at 2% energy or lower",
      variable=self._zero_energy_optional_race_rest_var,
      fg="#d6dde5",
      bg="#101418",
      selectcolor="#192028",
      activebackground="#101418",
      activeforeground="white",
      wraplength=640,
      justify="left",
      anchor="w",
    ).pack(anchor="w")
    self._zero_energy_optional_race_vita_var = tk.BooleanVar(
      value=bool(training_behavior.get("allow_zero_energy_optional_race_with_vita", True))
    )
    tk.Checkbutton(
      optional_race_frame,
      text="Allow zero-energy rival race if a Vita / energy item is held",
      variable=self._zero_energy_optional_race_vita_var,
      fg="#d6dde5",
      bg="#101418",
      selectcolor="#192028",
      activebackground="#101418",
      activeforeground="white",
      wraplength=640,
      justify="left",
      anchor="w",
    ).pack(anchor="w")
    self._zero_energy_optional_race_recovery_var = tk.BooleanVar(
      value=bool(training_behavior.get("allow_zero_energy_optional_race_with_recovery_items", True))
    )
    tk.Checkbutton(
      optional_race_frame,
      text="Allow zero-energy rival race if Miracle Cure or Rich Hand Cream is held",
      variable=self._zero_energy_optional_race_recovery_var,
      fg="#d6dde5",
      bg="#101418",
      selectcolor="#192028",
      activebackground="#101418",
      activeforeground="white",
      wraplength=640,
      justify="left",
      anchor="w",
    ).pack(anchor="w")
    tk.Label(
      optional_race_frame,
      text="Scheduled races still ignore this safety gate. Vita cover stages one energy item before the rival race.",
      fg="#8b949e",
      bg="#101418",
      justify="left",
      anchor="w",
      wraplength=700,
    ).pack(anchor="w", pady=(4, 0))

    fallback_race_frame = tk.Frame(body, bg="#101418", padx=16, pady=4)
    fallback_race_frame.pack(fill=tk.X)
    tk.Label(
      fallback_race_frame,
      text="Weak-training fallback race",
      fg="#d6dde5",
      bg="#101418",
      font=("Helvetica", 10, "bold"),
      anchor="w",
    ).grid(row=0, column=0, columnspan=8, sticky="w", pady=(0, 4))
    self._fallback_race_enabled_var = tk.BooleanVar(
      value=bool(training_behavior.get("weak_training_fallback_race_enabled", True))
    )
    tk.Checkbutton(
      fallback_race_frame,
      text="Prefer a schedule race over weak training when no rival indicator",
      variable=self._fallback_race_enabled_var,
      fg="#d6dde5",
      bg="#101418",
      selectcolor="#192028",
      activebackground="#101418",
      activeforeground="white",
      wraplength=640,
      justify="left",
      anchor="w",
    ).grid(row=1, column=0, columnspan=8, sticky="w")
    tk.Label(
      fallback_race_frame,
      text="Earliest turn",
      fg="#d6dde5",
      bg="#101418",
      anchor="e",
    ).grid(row=1, column=2, sticky="e", padx=(12, 4), pady=2)
    earliest_turn_default = str(training_behavior.get("weak_training_fallback_race_earliest_turn", "Classic Year Early Sep"))
    self._fallback_race_earliest_turn_var = tk.StringVar(value=earliest_turn_default)
    earliest_turn_menu = tk.OptionMenu(
      fallback_race_frame,
      self._fallback_race_earliest_turn_var,
      *constants.TIMELINE,
    )
    earliest_turn_menu.config(bg="#192028", fg="white", activebackground="#2d333b", activeforeground="white", highlightthickness=0, width=22)
    earliest_turn_menu["menu"].config(bg="#192028", fg="white", activebackground="#2d333b", activeforeground="white")
    earliest_turn_menu.grid(row=1, column=3, columnspan=3, sticky="w", pady=2)
    tk.Label(
      fallback_race_frame,
      text="Score threshold",
      fg="#d6dde5",
      bg="#101418",
      anchor="e",
    ).grid(row=2, column=0, sticky="e", padx=(0, 4), pady=2)
    self._fallback_race_score_threshold_var = tk.StringVar(
      value=str(training_behavior.get("weak_training_fallback_race_score_threshold", 30))
    )
    tk.Spinbox(
      fallback_race_frame,
      from_=0,
      to=200,
      width=5,
      textvariable=self._fallback_race_score_threshold_var,
      bg="#192028",
      fg="white",
      buttonbackground="#2d333b",
    ).grid(row=2, column=1, sticky="w", pady=2)
    tk.Label(
      fallback_race_frame,
      text="Low-energy rest %",
      fg="#d6dde5",
      bg="#101418",
      anchor="e",
    ).grid(row=2, column=2, sticky="e", padx=(12, 4), pady=2)
    self._fallback_race_low_energy_rest_pct_var = tk.StringVar(
      value=str(training_behavior.get("weak_training_fallback_race_low_energy_rest_pct", 2))
    )
    tk.Spinbox(
      fallback_race_frame,
      from_=0,
      to=100,
      width=5,
      textvariable=self._fallback_race_low_energy_rest_pct_var,
      bg="#192028",
      fg="white",
      buttonbackground="#2d333b",
    ).grid(row=2, column=3, sticky="w", pady=2)
    tk.Label(
      fallback_race_frame,
      text="Rest exempt score",
      fg="#d6dde5",
      bg="#101418",
      anchor="e",
    ).grid(row=2, column=4, sticky="e", padx=(12, 4), pady=2)
    self._fallback_race_rest_exempt_score_var = tk.StringVar(
      value=str(training_behavior.get("weak_training_fallback_race_low_energy_rest_exempt_score", 35))
    )
    tk.Spinbox(
      fallback_race_frame,
      from_=0,
      to=200,
      width=5,
      textvariable=self._fallback_race_rest_exempt_score_var,
      bg="#192028",
      fg="white",
      buttonbackground="#2d333b",
    ).grid(row=2, column=5, sticky="w", pady=2)
    tk.Label(
      fallback_race_frame,
      text="When training score is below the threshold and no rival is on screen, race any available schedule race instead. "
           "Consecutive-race warnings always cancel. At very low energy (below rest %), prefer rest over both racing and "
           "wasting a Good-Luck Charm on weak training — unless training score exceeds the rest exempt score.",
      fg="#8b949e",
      bg="#101418",
      justify="left",
      anchor="w",
      wraplength=700,
    ).grid(row=3, column=0, columnspan=8, sticky="ew", pady=(4, 0))

    race_lookahead_frame = tk.Frame(body, bg="#101418", padx=16, pady=4)
    race_lookahead_frame.pack(fill=tk.X)
    tk.Label(
      race_lookahead_frame,
      text="Race lookahead energy conservation",
      fg="#d6dde5",
      bg="#101418",
      font=("Helvetica", 10, "bold"),
      anchor="w",
    ).grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 4))
    self._race_lookahead_enabled_var = tk.BooleanVar(
      value=bool(training_behavior.get("race_lookahead_enabled", True))
    )
    tk.Checkbutton(
      race_lookahead_frame,
      text="Conserve energy before back-to-back scheduled races",
      variable=self._race_lookahead_enabled_var,
      fg="#d6dde5",
      bg="#101418",
      selectcolor="#192028",
      activebackground="#101418",
      activeforeground="white",
      wraplength=640,
      justify="left",
      anchor="w",
    ).grid(row=1, column=0, columnspan=6, sticky="w")
    tk.Label(
      race_lookahead_frame,
      text="Energy threshold %",
      fg="#d6dde5",
      bg="#101418",
      anchor="e",
    ).grid(row=2, column=0, sticky="e", padx=(0, 4), pady=2)
    self._race_lookahead_threshold_var = tk.StringVar(
      value=str(training_behavior.get("race_lookahead_conserve_threshold", 60))
    )
    tk.Spinbox(
      race_lookahead_frame,
      from_=0,
      to=100,
      width=5,
      textvariable=self._race_lookahead_threshold_var,
      bg="#192028",
      fg="white",
      buttonbackground="#2d333b",
    ).grid(row=2, column=1, sticky="w", pady=2)
    tk.Label(
      race_lookahead_frame,
      text="Min exceptional training score",
      fg="#d6dde5",
      bg="#101418",
      anchor="e",
    ).grid(row=2, column=2, sticky="e", padx=(12, 4), pady=2)
    self._race_lookahead_score_var = tk.StringVar(
      value=str(training_behavior.get("race_lookahead_exceptional_score", 40))
    )
    tk.Spinbox(
      race_lookahead_frame,
      from_=0,
      to=200,
      width=5,
      textvariable=self._race_lookahead_score_var,
      bg="#192028",
      fg="white",
      buttonbackground="#2d333b",
    ).grid(row=2, column=3, sticky="w", pady=2)
    tk.Label(
      race_lookahead_frame,
      text="When enabled, the bot rests before consecutive scheduled races unless training score exceeds the threshold and native energy or one held Vita can still cover the race sequence. Year-end Late Dec races do not trigger this guard.",
      fg="#8b949e",
      bg="#101418",
      justify="left",
      anchor="w",
      wraplength=700,
    ).grid(row=3, column=0, columnspan=6, sticky="ew", pady=(4, 0))

    scheduled_race_vita_frame = tk.Frame(body, bg="#101418", padx=16, pady=4)
    scheduled_race_vita_frame.pack(fill=tk.X)
    tk.Label(
      scheduled_race_vita_frame,
      text="Back-to-back race Vita safeguard",
      fg="#d6dde5",
      bg="#101418",
      font=("Helvetica", 10, "bold"),
      anchor="w",
    ).grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 4))
    self._scheduled_race_vita_enabled_var = tk.BooleanVar(
      value=bool(training_behavior.get("back_to_back_scheduled_race_vita_enabled", True))
    )
    tk.Checkbutton(
      scheduled_race_vita_frame,
      text="Use one Vita before a scheduled race when OCR reads near 0 energy and another scheduled race follows",
      variable=self._scheduled_race_vita_enabled_var,
      fg="#d6dde5",
      bg="#101418",
      selectcolor="#192028",
      activebackground="#101418",
      activeforeground="white",
      wraplength=640,
      justify="left",
      anchor="w",
    ).grid(row=1, column=0, columnspan=6, sticky="w")
    tk.Label(
      scheduled_race_vita_frame,
      text="Near-0 threshold %",
      fg="#d6dde5",
      bg="#101418",
      anchor="e",
    ).grid(row=2, column=0, sticky="e", padx=(0, 4), pady=2)
    self._scheduled_race_vita_threshold_var = tk.StringVar(
      value=str(training_behavior.get("back_to_back_scheduled_race_vita_threshold_pct", 2))
    )
    tk.Spinbox(
      scheduled_race_vita_frame,
      from_=0,
      to=20,
      width=5,
      textvariable=self._scheduled_race_vita_threshold_var,
      bg="#192028",
      fg="white",
      buttonbackground="#2d333b",
    ).grid(row=2, column=1, sticky="w", pady=2)
    tk.Label(
      scheduled_race_vita_frame,
      text="This does not fire on lone scheduled races or on fallback bad-training races. It only covers the first leg of an immediate back-to-back scheduled sequence.",
      fg="#8b949e",
      bg="#101418",
      justify="left",
      anchor="w",
      wraplength=700,
    ).grid(row=3, column=0, columnspan=6, sticky="ew", pady=(4, 0))

    vita_frame = tk.Frame(body, bg="#101418", padx=16, pady=4)
    vita_frame.pack(fill=tk.X)
    tk.Label(
      vita_frame,
      text="Energy item conservation",
      fg="#d6dde5",
      bg="#101418",
      font=("Helvetica", 10, "bold"),
      anchor="w",
    ).pack(anchor="w", pady=(0, 4))
    self._save_vita_for_summer_var = tk.BooleanVar(
      value=bool(training_behavior.get("save_vita_for_summer", True))
    )
    tk.Checkbutton(
      vita_frame,
      text="Save Vita items for summer burst windows",
      variable=self._save_vita_for_summer_var,
      fg="#d6dde5",
      bg="#101418",
      selectcolor="#192028",
      activebackground="#101418",
      activeforeground="white",
      wraplength=640,
      justify="left",
      anchor="w",
    ).pack(anchor="w")
    tk.Label(
      vita_frame,
      text=(
        "When enabled, Vita items are deferred outside summer windows (Early Jul \u2013 Late Aug). "
        "High-fail trainings that need energy to clear failure will be skipped in favor of rest or racing. "
        "Disable to allow Vita use year-round for strong trainings."
      ),
      fg="#8b949e",
      bg="#101418",
      justify="left",
      anchor="w",
      wraplength=700,
    ).pack(anchor="w", pady=(4, 0))

    bond_frame = tk.Frame(body, bg="#101418", padx=16, pady=4)
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
      wraplength=640,
      justify="left",
      anchor="w",
    ).pack(side=tk.LEFT)

    cutoff_frame = tk.Frame(body, bg="#101418", padx=16, pady=0)
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

    buff_override_frame = tk.Frame(body, bg="#101418", padx=16, pady=4)
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
      wraplength=640,
      justify="left",
      anchor="w",
    ).pack(side=tk.LEFT)

    buttons = tk.Frame(window, bg="#101418", padx=8, pady=8)
    buttons.grid(row=2, column=0, sticky="ew")
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
    if self._optional_race_training_threshold_var is not None:
      planner_policy = self._get_active_planner_policy()
      self._optional_race_training_threshold_var.set(
        str(int(planner_policy.get("training_overrides_race_threshold", behavior.get("optional_race_training_threshold", 30))))
      )
    if self._committed_training_score_threshold_var is not None:
      self._committed_training_score_threshold_var.set(
        str(behavior.get("committed_training_score_threshold", 35))
      )
    if self._race_lookahead_enabled_var is not None:
      self._race_lookahead_enabled_var.set(bool(behavior.get("race_lookahead_enabled", True)))
    if self._race_lookahead_threshold_var is not None:
      self._race_lookahead_threshold_var.set(str(behavior.get("race_lookahead_conserve_threshold", 60)))
    if self._race_lookahead_score_var is not None:
      self._race_lookahead_score_var.set(str(behavior.get("race_lookahead_exceptional_score", 40)))
    if self._scheduled_race_vita_enabled_var is not None:
      self._scheduled_race_vita_enabled_var.set(bool(behavior.get("back_to_back_scheduled_race_vita_enabled", True)))
    if self._scheduled_race_vita_threshold_var is not None:
      self._scheduled_race_vita_threshold_var.set(str(behavior.get("back_to_back_scheduled_race_vita_threshold_pct", 2)))
    if self._zero_energy_optional_race_rest_var is not None:
      self._zero_energy_optional_race_rest_var.set(bool(behavior.get("prefer_rest_on_zero_energy_optional_race", True)))
    if self._zero_energy_optional_race_vita_var is not None:
      self._zero_energy_optional_race_vita_var.set(bool(behavior.get("allow_zero_energy_optional_race_with_vita", True)))
    if self._zero_energy_optional_race_recovery_var is not None:
      self._zero_energy_optional_race_recovery_var.set(bool(behavior.get("allow_zero_energy_optional_race_with_recovery_items", True)))
    if self._save_vita_for_summer_var is not None:
      self._save_vita_for_summer_var.set(bool(behavior.get("save_vita_for_summer", True)))

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
    current_planner_policy = config.normalize_trackblazer_planner_policy(getattr(config, "TRACKBLAZER_PLANNER_POLICY", None))
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
    optional_race_training_threshold = None
    if self._optional_race_training_threshold_var is not None:
      threshold_text = str(self._optional_race_training_threshold_var.get() or "").strip()
      try:
        optional_race_training_threshold = max(0, int(threshold_text))
        training_behavior["optional_race_training_threshold"] = optional_race_training_threshold
      except ValueError:
        pass
    if self._committed_training_score_threshold_var is not None:
      threshold_text = str(self._committed_training_score_threshold_var.get() or "").strip()
      try:
        training_behavior["committed_training_score_threshold"] = max(0, int(threshold_text))
      except ValueError:
        pass
    if self._race_lookahead_enabled_var is not None:
      training_behavior["race_lookahead_enabled"] = bool(self._race_lookahead_enabled_var.get())
    if self._race_lookahead_threshold_var is not None:
      threshold_text = str(self._race_lookahead_threshold_var.get() or "").strip()
      try:
        training_behavior["race_lookahead_conserve_threshold"] = min(100, max(0, int(threshold_text)))
      except ValueError:
        pass
    if self._race_lookahead_score_var is not None:
      score_text = str(self._race_lookahead_score_var.get() or "").strip()
      try:
        training_behavior["race_lookahead_exceptional_score"] = max(0, int(score_text))
      except ValueError:
        pass
    if self._scheduled_race_vita_enabled_var is not None:
      training_behavior["back_to_back_scheduled_race_vita_enabled"] = bool(self._scheduled_race_vita_enabled_var.get())
    if self._scheduled_race_vita_threshold_var is not None:
      threshold_text = str(self._scheduled_race_vita_threshold_var.get() or "").strip()
      try:
        training_behavior["back_to_back_scheduled_race_vita_threshold_pct"] = min(100, max(0, int(threshold_text)))
      except ValueError:
        pass
    if self._zero_energy_optional_race_rest_var is not None:
      training_behavior["prefer_rest_on_zero_energy_optional_race"] = bool(self._zero_energy_optional_race_rest_var.get())
    if self._zero_energy_optional_race_vita_var is not None:
      training_behavior["allow_zero_energy_optional_race_with_vita"] = bool(self._zero_energy_optional_race_vita_var.get())
    if self._zero_energy_optional_race_recovery_var is not None:
      training_behavior["allow_zero_energy_optional_race_with_recovery_items"] = bool(self._zero_energy_optional_race_recovery_var.get())
    if self._save_vita_for_summer_var is not None:
      training_behavior["save_vita_for_summer"] = bool(self._save_vita_for_summer_var.get())
    if self._fallback_race_enabled_var is not None:
      training_behavior["weak_training_fallback_race_enabled"] = bool(self._fallback_race_enabled_var.get())
    if self._fallback_race_score_threshold_var is not None:
      threshold_text = str(self._fallback_race_score_threshold_var.get() or "").strip()
      try:
        training_behavior["weak_training_fallback_race_score_threshold"] = max(0, int(threshold_text))
      except ValueError:
        pass
    if self._fallback_race_earliest_turn_var is not None:
      earliest_turn = str(self._fallback_race_earliest_turn_var.get() or "").strip()
      if earliest_turn in constants.TIMELINE:
        training_behavior["weak_training_fallback_race_earliest_turn"] = earliest_turn
    if self._fallback_race_low_energy_rest_pct_var is not None:
      pct_text = str(self._fallback_race_low_energy_rest_pct_var.get() or "").strip()
      try:
        training_behavior["weak_training_fallback_race_low_energy_rest_pct"] = min(100, max(0, int(pct_text)))
      except ValueError:
        pass
    if self._fallback_race_rest_exempt_score_var is not None:
      score_text = str(self._fallback_race_rest_exempt_score_var.get() or "").strip()
      try:
        training_behavior["weak_training_fallback_race_low_energy_rest_exempt_score"] = max(0, int(score_text))
      except ValueError:
        pass

    policy = {
      "version": int(current_policy.get("version", 1)),
      "settings": {
        "training_behavior": training_behavior,
      },
      "items": current_policy.get("items", {}),
    }
    if optional_race_training_threshold is not None:
      current_planner_policy["training_overrides_race_threshold"] = float(optional_race_training_threshold)
    if not self._persist_config_value("trackblazer.planner_policy", current_planner_policy):
      self._message_value.set("Failed to save planner thresholds.")
      return
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
    default_planner_policy = config.normalize_trackblazer_planner_policy({})
    if not self._persist_config_value("trackblazer.planner_policy", default_planner_policy):
      self._message_value.set("Failed to reset planner thresholds.")
      return
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
