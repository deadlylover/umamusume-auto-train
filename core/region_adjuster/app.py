import json
import platform
import subprocess
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pyautogui
from PIL import Image, ImageDraw, ImageTk

try:
  import pygetwindow as gw
except Exception:  # pragma: no cover - optional dependency may be missing
  gw = None


def _load_context(path: Path) -> Dict:
  with path.open("r", encoding="utf-8") as file:
    return json.load(file)


class RegionAdjusterApp:
  def __init__(self, context: Dict):
    self.context = context
    self.overlay_opacity = int(context.get("overlay_dim_opacity", 196))
    overrides_path = context.get("overrides_path") or "data/region_overrides.json"
    self.overrides_path = Path(overrides_path)
    self.window_names: List[str] = context.get("window_names") or []
    self.process_names: List[str] = context.get("process_names") or []
    bounds_context = context.get("mac_bounds") or {}
    self.bounds_default = bounds_context.get("bounds") or {"x": 0, "y": 0, "width": 640, "height": 1113}

    self.regions: Dict[str, Dict] = {}
    self.region_order: List[str] = []
    for entry in context.get("regions", []):
      name = entry.get("name")
      kind = entry.get("kind")
      value = entry.get("value")
      if not name or kind not in {"region", "bbox"}:
        continue
      if not isinstance(value, list) or len(value) < 4:
        continue
      norm_value = [int(round(v)) for v in value[:4]]
      self.regions[name] = {"kind": kind, "value": norm_value}
      self.region_order.append(name)

    if not self.region_order:
      raise RuntimeError("No OCR regions are available for adjustment.")

    self.selected_name = self.region_order[0]
    self.screenshot = None
    self.overlay_image = None
    self.photo_image = None
    self._dirty = False
    self._window_info: Optional[Dict[str, int]] = None

    self.root = tk.Tk()
    self.root.title("Uma OCR Region Adjuster")
    self.root.configure(background="#1f1f1f")
    self.root.geometry("1400x900")
    self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    self.root.columnconfigure(0, weight=1)
    self.root.rowconfigure(0, weight=1)

    self._build_layout()
    self._bind_hotkeys()
    self.capture_screenshot()

  def _build_layout(self):
    self.canvas = tk.Canvas(self.root, background="black", highlightthickness=0)
    self.canvas.grid(row=0, column=0, sticky="nsew")

    self.canvas_image_id = self.canvas.create_image(0, 0, anchor="nw")

    self.v_scroll = tk.Scrollbar(self.root, orient="vertical", command=self.canvas.yview)
    self.v_scroll.grid(row=0, column=1, sticky="ns")
    self.h_scroll = tk.Scrollbar(self.root, orient="horizontal", command=self.canvas.xview)
    self.h_scroll.grid(row=1, column=0, sticky="ew")
    self.canvas.configure(xscrollcommand=self.h_scroll.set, yscrollcommand=self.v_scroll.set)

    side_panel = tk.Frame(self.root, bg="#232323", padx=12, pady=12)
    side_panel.grid(row=0, column=2, rowspan=2, sticky="ns")

    tk.Label(side_panel, text="OCR Regions", fg="white", bg="#232323", font=("Helvetica", 12, "bold")).pack(anchor="w")
    self.region_listbox = tk.Listbox(
      side_panel,
      height=25,
      width=28,
      exportselection=False,
      selectmode=tk.SINGLE,
      bg="#1b1b1b",
      fg="white",
    )
    for idx, name in enumerate(self.region_order):
      self.region_listbox.insert(idx, name)
    self.region_listbox.selection_set(0)
    self.region_listbox.bind("<<ListboxSelect>>", self._on_region_select)
    self.region_listbox.pack(fill=tk.BOTH, expand=False, pady=(6, 10))

    self.coord_var = tk.StringVar()
    tk.Label(side_panel, textvariable=self.coord_var, fg="#9feaf9", bg="#232323", wraplength=220, justify="left").pack(anchor="w", pady=(0, 10))

    step_container = tk.Frame(side_panel, bg="#232323")
    step_container.pack(anchor="w", pady=(0, 10))
    tk.Label(step_container, text="Step (px)", fg="white", bg="#232323").pack(side=tk.LEFT)
    self.step_var = tk.IntVar(value=1)
    step_entry = tk.Spinbox(step_container, from_=1, to=50, width=5, textvariable=self.step_var)
    step_entry.pack(side=tk.LEFT, padx=(6, 0))

    move_frame = tk.Frame(side_panel, bg="#232323")
    move_frame.pack(anchor="center", pady=(5, 10))
    tk.Button(move_frame, text="▲", width=6, command=lambda: self.move_selected(0, -self._step_size())).grid(row=0, column=1, pady=2)
    tk.Button(move_frame, text="◀", width=6, command=lambda: self.move_selected(-self._step_size(), 0)).grid(row=1, column=0, padx=2)
    tk.Button(move_frame, text="▶", width=6, command=lambda: self.move_selected(self._step_size(), 0)).grid(row=1, column=2, padx=2)
    tk.Button(move_frame, text="▼", width=6, command=lambda: self.move_selected(0, self._step_size())).grid(row=2, column=1, pady=2)

    resize_frame = tk.LabelFrame(side_panel, text="Resize", fg="white", bg="#232323", bd=1, relief=tk.GROOVE, labelanchor="n")
    resize_frame.configure(highlightbackground="#484848")
    resize_frame.pack(fill=tk.X, pady=(0, 10))
    tk.Button(resize_frame, text="Wider", command=lambda: self.resize_selected(dw=self._step_size())).grid(row=0, column=0, padx=4, pady=4, sticky="ew")
    tk.Button(resize_frame, text="Narrower", command=lambda: self.resize_selected(dw=-self._step_size())).grid(row=0, column=1, padx=4, pady=4, sticky="ew")
    tk.Button(resize_frame, text="Taller", command=lambda: self.resize_selected(dh=self._step_size())).grid(row=1, column=0, padx=4, pady=4, sticky="ew")
    tk.Button(resize_frame, text="Shorter", command=lambda: self.resize_selected(dh=-self._step_size())).grid(row=1, column=1, padx=4, pady=4, sticky="ew")
    for i in range(2):
      resize_frame.columnconfigure(i, weight=1)

    bounds_frame = tk.LabelFrame(side_panel, text="Window Bounds", fg="white", bg="#232323", bd=1, relief=tk.GROOVE, labelanchor="n")
    bounds_frame.configure(highlightbackground="#484848")
    bounds_frame.pack(fill=tk.X, pady=(0, 10))
    self.bound_vars = {}
    for idx, key in enumerate(["x", "y", "width", "height"]):
      tk.Label(bounds_frame, text=key.capitalize(), fg="white", bg="#232323").grid(row=idx, column=0, sticky="w", padx=4, pady=2)
      value = self.bounds_default.get(key, 0)
      var = tk.StringVar(value=str(value))
      entry = tk.Entry(bounds_frame, textvariable=var, width=8)
      entry.grid(row=idx, column=1, sticky="e", padx=4, pady=2)
      self.bound_vars[key] = var

    tk.Button(bounds_frame, text="Set Bounds", command=self.apply_window_bounds).grid(row=4, column=0, columnspan=2, pady=(6, 2))

    tk.Button(side_panel, text="Refresh Screenshot", command=self.capture_screenshot).pack(fill=tk.X, pady=(5, 5))
    tk.Button(side_panel, text="Save Overrides", command=self.save_overrides).pack(fill=tk.X, pady=(0, 5))
    tk.Button(side_panel, text="Close", command=self._on_close).pack(fill=tk.X)

    self.status_var = tk.StringVar()
    status_label = tk.Label(side_panel, textvariable=self.status_var, fg="#d0d0d0", bg="#232323", wraplength=220, justify="left")
    status_label.pack(fill=tk.X, pady=(10, 0))

    window_names_text = ", ".join(self.window_names) if self.window_names else "(no candidates)"
    self.window_search_var = tk.StringVar(value=f"Looking for window names: {window_names_text}")
    tk.Label(
      side_panel,
      textvariable=self.window_search_var,
      fg="#8fb8ff",
      bg="#232323",
      wraplength=220,
      justify="left",
    ).pack(fill=tk.X, pady=(8, 4))

    self.window_dims_var = tk.StringVar(value="Detecting BlueStacks window size...")
    tk.Label(
      side_panel,
      textvariable=self.window_dims_var,
      fg="#bbbbbb",
      bg="#232323",
      wraplength=220,
      justify="left",
    ).pack(fill=tk.X, pady=(0, 0))
    self._set_coord_text()
    self._update_window_dimensions()

  def _bind_hotkeys(self):
    self.root.bind("<Up>", lambda event: self._handle_arrow(event, 0, -1))
    self.root.bind("<Down>", lambda event: self._handle_arrow(event, 0, 1))
    self.root.bind("<Left>", lambda event: self._handle_arrow(event, -1, 0))
    self.root.bind("<Right>", lambda event: self._handle_arrow(event, 1, 0))
    self.root.bind("<Command-s>", lambda event: self.save_overrides())  # macOS shortcut
    self.root.bind("<Control-s>", lambda event: self.save_overrides())
    self.root.bind("<space>", lambda event: self.capture_screenshot())

  def _handle_arrow(self, event, dx: int, dy: int):
    multiplier = self._step_size()
    if event.state & 0x0001:  # Shift key pressed
      multiplier *= 5
    self.move_selected(dx * multiplier, dy * multiplier)

  def _step_size(self) -> int:
    try:
      value = int(self.step_var.get())
    except (TypeError, ValueError):
      value = 1
    return max(1, min(100, value))

  def capture_screenshot(self):
    try:
      screenshot = pyautogui.screenshot()
    except Exception as exc:
      messagebox.showerror("Screenshot Failed", f"Unable to capture the screen: {exc}")
      return

    self.screenshot = screenshot.convert("RGBA")
    self.status_var.set("Captured a new screenshot. Adjust regions to highlight them on the image.")
    self._render_overlay()
    self._update_window_dimensions()

  def _render_overlay(self):
    if not self.screenshot:
      return

    overlay = self.screenshot.copy()
    dim_layer = Image.new("RGBA", overlay.size, (0, 0, 0, self.overlay_opacity))
    overlay = Image.alpha_composite(overlay, dim_layer)

    if self.selected_name:
      box = self._current_box(self.selected_name)
      if box:
        x1, y1, x2, y2 = box
        x1, y1 = max(0, x1), max(0, y1)
        x2 = min(overlay.width, x2)
        y2 = min(overlay.height, y2)
        if x2 > x1 and y2 > y1:
          crop = self.screenshot.crop((x1, y1, x2, y2))
          overlay.paste(crop, (x1, y1))
          draw = ImageDraw.Draw(overlay)
          draw.rectangle((x1, y1, x2, y2), outline=(255, 215, 0, 255), width=1)

    self.overlay_image = overlay
    self.photo_image = ImageTk.PhotoImage(overlay)
    self.canvas.itemconfigure(self.canvas_image_id, image=self.photo_image)
    self.canvas.config(scrollregion=(0, 0, overlay.width, overlay.height))

  def _current_box(self, name: str) -> Tuple[int, int, int, int]:
    entry = self.regions.get(name)
    if not entry:
      return (0, 0, 0, 0)
    value = entry["value"]
    if entry["kind"] == "bbox":
      x1, y1, x2, y2 = value
      return (x1, y1, x2, y2)
    x, y, w, h = value
    return (x, y, x + w, y + h)

  def _set_coord_text(self):
    entry = self.regions.get(self.selected_name)
    if not entry:
      self.coord_var.set("No region selected")
      return
    value = entry["value"]
    self.coord_var.set(f"{self.selected_name} ({entry['kind']}): {tuple(value)}")

  def _on_region_select(self, _event):
    selection = self.region_listbox.curselection()
    if not selection:
      return
    self.selected_name = self.region_order[selection[0]]
    self._set_coord_text()
    self._render_overlay()

  def move_selected(self, dx: int, dy: int):
    if not self.selected_name:
      return
    entry = self.regions.get(self.selected_name)
    if not entry:
      return

    value = entry["value"]
    if entry["kind"] == "bbox":
      value[0] += dx
      value[2] += dx
      value[1] += dy
      value[3] += dy
    else:
      value[0] += dx
      value[1] += dy

    self._mark_dirty(True)
    self._set_coord_text()
    self._render_overlay()

  def resize_selected(self, dw: int = 0, dh: int = 0):
    if not self.selected_name:
      return
    entry = self.regions.get(self.selected_name)
    if not entry:
      return

    value = entry["value"]
    changed = False
    if dw != 0:
      if entry["kind"] == "bbox":
        current_width = value[2] - value[0]
        new_width = max(1, current_width + dw)
        if new_width != current_width:
          value[2] = value[0] + new_width
          changed = True
      else:
        new_width = max(1, value[2] + dw)
        if new_width != value[2]:
          value[2] = new_width
          changed = True

    if dh != 0:
      if entry["kind"] == "bbox":
        current_height = value[3] - value[1]
        new_height = max(1, current_height + dh)
        if new_height != current_height:
          value[3] = value[1] + new_height
          changed = True
      else:
        new_height = max(1, value[3] + dh)
        if new_height != value[3]:
          value[3] = new_height
          changed = True

    if changed:
      self._mark_dirty(True)
      self._set_coord_text()
      self._render_overlay()

  def save_overrides(self):
    data = {name: [int(v) for v in entry["value"]] for name, entry in self.regions.items()}
    try:
      self.overrides_path.parent.mkdir(parents=True, exist_ok=True)
      with self.overrides_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
    except OSError as exc:
      messagebox.showerror("Save Failed", f"Unable to write overrides: {exc}")
      return

    self._mark_dirty(False)
    self.status_var.set(f"Saved overrides to {self.overrides_path}.")
    self._update_window_dimensions()

  def _mark_dirty(self, dirty: bool):
    self._dirty = dirty
    title = "Uma OCR Region Adjuster*" if dirty else "Uma OCR Region Adjuster"
    self.root.title(title)

  def _on_close(self):
    if self._dirty:
      should_close = messagebox.askyesno(
        "Unsaved Changes",
        "You have unsaved adjustments. Close without saving?",
        icon="warning",
      )
      if not should_close:
        return
    self.root.destroy()

  def run(self):
    self.root.mainloop()

  def apply_window_bounds(self):
    system = platform.system().lower()
    if system != "darwin":
      messagebox.showinfo("Not Supported", "Setting window bounds is currently only supported on macOS.")
      return

    bounds = {}
    try:
      for key in ("x", "y", "width", "height"):
        value = int(float(self.bound_vars[key].get()))
        if key in ("width", "height") and value <= 0:
          raise ValueError
        bounds[key] = value
    except (KeyError, ValueError):
      messagebox.showerror("Invalid Bounds", "Please enter numeric values for x, y, width, and height (width/height > 0).")
      return

    success = self._set_bounds_via_osascript(bounds)
    if success:
      self.status_var.set(f"Set BlueStacks bounds to {bounds['width']}x{bounds['height']} at ({bounds['x']}, {bounds['y']}).")
      self._update_window_dimensions()
    else:
      messagebox.showwarning("Bounds Failed", "Unable to find a BlueStacks window to resize. Confirm it is open and the window name matches your configuration.")

  def _update_window_dimensions(self):
    if not hasattr(self, "window_dims_var"):
      return

    if gw is None:
      self.window_dims_var.set("Window size unavailable (pygetwindow not installed).")
      return

    info = self._find_window_info()
    if info:
      self._window_info = info
      title = info.get("title", "BlueStacks")
      width = info.get("width", 0)
      height = info.get("height", 0)
      left = info.get("left", 0)
      top = info.get("top", 0)
      self.window_dims_var.set(f"{title}: {width}x{height} at ({left}, {top})")
    else:
      self.window_dims_var.set("BlueStacks window not detected.")

  def _find_window_info(self) -> Optional[Dict[str, int]]:
    if gw is None:
      return None

    names = self.window_names or []
    checked_titles = set()
    for name in names:
      if not name:
        continue
      try:
        windows = gw.getWindowsWithTitle(name)
      except Exception:
        continue

      for window in windows:
        title = (window.title or "").strip()
        if not title or title in checked_titles:
          continue
        checked_titles.add(title)
        try:
          width = int(window.width)
          height = int(window.height)
          left = int(window.left)
          top = int(window.top)
        except Exception:
          continue

        if width <= 0 or height <= 0:
          continue

        return {
          "title": title,
          "width": width,
          "height": height,
          "left": left,
          "top": top,
        }

    return None

  def _set_bounds_via_osascript(self, bounds: Dict[str, int]) -> bool:
    process_names = self.process_names or ["BlueStacks"]
    window_names = self.window_names or [""]

    for process_name in process_names:
      if not process_name:
        continue
      for window_name in window_names:
        script = self._build_bounds_script(process_name, window_name or "", bounds)
        try:
          result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
          )
        except FileNotFoundError:
          messagebox.showerror("osascript Missing", "macOS AppleScript support (osascript) is unavailable. Cannot set bounds.")
          return False

        if result.returncode == 0:
          return True

    return False

  def _build_bounds_script(self, process_name: str, window_hint: str, bounds: Dict[str, int]) -> str:
    x = bounds.get("x", 0)
    y = bounds.get("y", 0)
    width = bounds.get("width", 640)
    height = bounds.get("height", 1113)
    window_condition = f'if winName contains "{window_hint.replace("\"", r"\\\"")}" then' if window_hint else "if true then"

    script = f'''
tell application "System Events"
  if exists (process "{process_name}") then
    tell process "{process_name}"
      set frontmost to true
      repeat with win in windows
        try
          set winName to name of win as text
        on error
          set winName to ""
        end try
        {window_condition}
          set position of win to {{{x}, {y}}}
          set size of win to {{{width}, {height}}}
          return 0
        end if
      end repeat
    end tell
  end if
end tell
return 1
'''
    return script


def run_app(context_path: str):
  path = Path(context_path)
  context = _load_context(path)
  app = RegionAdjusterApp(context)
  app.run()
