import json
import queue
import tkinter as tk
from tkinter import scrolledtext
import threading

import core.bot as bot
import core.config as config
import utils.constants as constants
from core.platform.window_focus import focus_target_window
from core.region_adjuster import run_region_adjuster_session
from core.region_adjuster.shared import resolve_region_adjuster_profiles
from utils.log import debug, error

PHASES = [
  "idle",
  "focusing_window",
  "scanning_lobby",
  "collecting_main_state",
  "collecting_training_state",
  "evaluating_strategy",
  "waiting_for_confirmation",
  "executing_action",
  "recovering",
]


class OperatorConsole:
  DEFAULT_GEOMETRY = "960x760+40+40"

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
    self._message_value = None
    self._error_value = None
    self._phase_labels = {}
    self._summary_text = None
    self._training_text = None
    self._bot_button = None
    self._pause_button = None
    self._resume_button = None
    self._continue_button = None
    self._window_geometry = self.DEFAULT_GEOMETRY
    self._last_saved_geometry = None

  def start(self):
    if self._root is not None:
      return
    try:
      self._load_window_geometry()
      self._root = tk.Tk()
      self._root.title("Uma Operator Console")
      self._root.configure(bg="#101418")
      self._root.geometry(self._window_geometry)
      self._root.attributes("-topmost", False)
      self._root.protocol("WM_DELETE_WINDOW", self._hide_window)
      self._build_layout()
      self._root.update_idletasks()
      self._apply_window_geometry()
      self._root.bind("<Configure>", self._on_window_configure)
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
    self._load_window_geometry()
    self._apply_window_geometry()
    self._root.deiconify()
    self._root.lift()
    self._persist_window_geometry()

  def _build_layout(self):
    root = self._root
    root.columnconfigure(0, weight=1)
    root.columnconfigure(1, weight=1)
    root.rowconfigure(2, weight=1)

    top = tk.Frame(root, bg="#101418", padx=14, pady=12)
    top.grid(row=0, column=0, columnspan=2, sticky="ew")
    for col in range(4):
      top.columnconfigure(col, weight=1)

    self._phase_value = self._make_stat(top, 0, "Phase")
    self._status_value = self._make_stat(top, 1, "Status")
    self._scenario_value = self._make_stat(top, 2, "Scenario")
    self._turn_value = self._make_stat(top, 3, "Turn")
    self._energy_value = self._make_stat(top, 4, "Energy")
    self._action_value = self._make_stat(top, 5, "Action")

    actions = tk.Frame(root, bg="#101418", padx=14, pady=0)
    actions.grid(row=1, column=0, columnspan=2, sticky="ew")
    self._bot_button = tk.Button(actions, text="Start Bot", command=self._toggle_bot)
    self._bot_button.pack(side=tk.LEFT, padx=(0, 8))
    self._pause_button = tk.Button(actions, text="Pause", command=self._request_pause)
    self._pause_button.pack(side=tk.LEFT, padx=(0, 8))
    self._resume_button = tk.Button(actions, text="Resume", command=self._resume_bot)
    self._resume_button.pack(side=tk.LEFT, padx=(0, 8))
    self._continue_button = tk.Button(actions, text="Continue (F2)", command=self._continue_review)
    self._continue_button.pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(actions, text="Open OCR Adjuster", command=self._launch_adjuster).pack(side=tk.LEFT)
    self._always_on_top_var = tk.BooleanVar(value=False)
    tk.Checkbutton(
      actions,
      text="Always on top",
      variable=self._always_on_top_var,
      command=self._toggle_always_on_top,
      fg="white",
      bg="#101418",
      selectcolor="#192028",
      activebackground="#101418",
      activeforeground="white",
    ).pack(side=tk.LEFT, padx=(12, 0))

    left = tk.LabelFrame(root, text="Flow", fg="white", bg="#101418", padx=12, pady=12)
    left.grid(row=2, column=0, sticky="nsew", padx=(14, 8), pady=12)
    right = tk.Frame(root, bg="#101418")
    right.grid(row=2, column=1, sticky="nsew", padx=(8, 14), pady=12)
    right.columnconfigure(0, weight=1)
    right.rowconfigure(1, weight=1)
    right.rowconfigure(3, weight=1)

    for phase in PHASES:
      label = tk.Label(
        left,
        text=phase.replace("_", " "),
        anchor="w",
        fg="#9aa4ad",
        bg="#192028",
        padx=10,
        pady=8,
      )
      label.pack(fill=tk.X, pady=3)
      self._phase_labels[phase] = label

    self._message_value = tk.StringVar(value="")
    self._error_value = tk.StringVar(value="")
    tk.Label(right, text="Summary", fg="white", bg="#101418", anchor="w").grid(row=0, column=0, sticky="ew")
    self._summary_text = scrolledtext.ScrolledText(right, height=14, bg="#192028", fg="#d6dde5", insertbackground="white")
    self._summary_text.grid(row=1, column=0, sticky="nsew", pady=(4, 10))

    tk.Label(right, text="Ranked Trainings", fg="white", bg="#101418", anchor="w").grid(row=2, column=0, sticky="ew")
    self._training_text = scrolledtext.ScrolledText(right, height=12, bg="#192028", fg="#d6dde5", insertbackground="white")
    self._training_text.grid(row=3, column=0, sticky="nsew", pady=(4, 10))

    tk.Label(right, textvariable=self._message_value, fg="#8bd5ca", bg="#101418", anchor="w", justify="left", wraplength=430).grid(row=4, column=0, sticky="ew")
    tk.Label(right, textvariable=self._error_value, fg="#ff8c8c", bg="#101418", anchor="w", justify="left", wraplength=430).grid(row=5, column=0, sticky="ew")

  def _make_stat(self, parent, column, title):
    row = 0 if column < 4 else 1
    actual_column = column if column < 4 else column - 4
    frame = tk.Frame(parent, bg="#151b22", padx=10, pady=8)
    frame.grid(row=row, column=actual_column, sticky="ew", padx=4, pady=4)
    tk.Label(frame, text=title, fg="#8a949e", bg="#151b22").pack(anchor="w")
    value = tk.StringVar(value="-")
    tk.Label(frame, textvariable=value, fg="white", bg="#151b22", font=("Helvetica", 12, "bold")).pack(anchor="w")
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

    self._phase_value.set(runtime_state.get("phase") or "-")
    self._status_value.set(runtime_state.get("status") or "-")
    self._scenario_value.set(snapshot.get("scenario_name") or constants.SCENARIO_NAME or "-")
    self._turn_value.set(snapshot.get("turn_label") or "-")
    self._energy_value.set(snapshot.get("energy_label") or "-")
    self._action_value.set(selected_action.get("func") or "-")
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
    }
    self._set_text(self._summary_text, json.dumps(summary_payload, indent=2, ensure_ascii=True))
    self._set_text(self._training_text, json.dumps(snapshot.get("ranked_trainings") or [], indent=2, ensure_ascii=True))

  def _set_text(self, widget, value):
    widget.configure(state=tk.NORMAL)
    widget.delete("1.0", tk.END)
    widget.insert("1.0", value)
    widget.configure(state=tk.DISABLED)

  def _toggle_always_on_top(self):
    if self._root is None or self._always_on_top_var is None:
      return
    self._root.attributes("-topmost", bool(self._always_on_top_var.get()))

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

  def _apply_window_geometry(self):
    if self._root is None:
      return
    geometry = self._window_geometry or self.DEFAULT_GEOMETRY
    self._root.geometry(geometry)

  def _on_window_configure(self, event):
    if self._root is None or event.widget is not self._root:
      return
    self._persist_window_geometry()

  def _persist_window_geometry(self):
    if self._root is None:
      return
    if str(self._root.state()) == "withdrawn":
      return

    geometry = self._root.geometry()
    if geometry == self._last_saved_geometry:
      return

    try:
      with open("config.json", "r", encoding="utf-8") as file:
        config_data = json.load(file)
    except Exception:
      config_data = {}

    debug_config = config_data.setdefault("debug", {})
    operator_console_config = debug_config.setdefault("operator_console", {})
    operator_console_config["window_geometry"] = geometry

    try:
      with open("config.json", "w", encoding="utf-8") as file:
        json.dump(config_data, file, indent=2)
    except Exception as exc:
      debug(f"Unable to persist operator console geometry: {exc}")
      return

    self._last_saved_geometry = geometry

  def _launch_adjuster(self):
    threading.Thread(target=self._launch_adjuster_background, daemon=True).start()

  def _toggle_bot(self):
    bot.invoke_control_callback("toggle_bot")
    self.publish()

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
