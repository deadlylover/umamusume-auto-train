import json
import platform
import subprocess
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pyautogui
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageTk

try:
  import pygetwindow as gw
except Exception:  # pragma: no cover - optional dependency may be missing
  gw = None

GRID_OVERLAYS = {
  "FULL_STATS_APTITUDE_REGION": (4, 3),
  "FULL_STATS_APTITUDE_BBOX": (4, 3),
}


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
    offset_context = context.get("recognition_offset") or {}
    self._recognition_offset_enabled = bool(offset_context.get("enabled"))
    self._recognition_offset_values = (
      int(offset_context.get("x", 0) or 0),
      int(offset_context.get("y", 0) or 0),
    )
    self._recognition_offset_respected = bool(offset_context.get("respected_by_overrides"))
    self.base_dir = Path(context.get("base_dir") or ".")
    self.template_map: Dict[str, List[str]] = {}
    for name, templates in (context.get("templates") or {}).items():
      if not isinstance(name, str):
        continue
      if isinstance(templates, (list, tuple)):
        self.template_map[name] = [str(path) for path in templates if path]
    self.all_templates = [str(path) for path in (context.get("all_templates") or []) if path]
    self.training_positions: Dict[str, Tuple[int, int]] = {}
    for name, pos in (context.get("training_positions") or {}).items():
      if isinstance(name, str) and isinstance(pos, (list, tuple)) and len(pos) >= 2:
        self.training_positions[name] = (int(pos[0]), int(pos[1]))

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
    self.template_photo = None
    self._dirty = False
    self._window_info: Optional[Dict[str, int]] = None
    self._current_templates: List[str] = []
    self._selected_template_path: Optional[str] = None
    self._template_matches: List[Tuple[int, int, int, int]] = []

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

    self.side_canvas = tk.Canvas(self.root, background="#232323", highlightthickness=0, width=320)
    self.side_canvas.grid(row=0, column=2, rowspan=2, sticky="ns")
    self.side_scroll = tk.Scrollbar(self.root, orient="vertical", command=self.side_canvas.yview)
    self.side_scroll.grid(row=0, column=3, rowspan=2, sticky="ns")
    self.side_canvas.configure(yscrollcommand=self.side_scroll.set)

    side_panel = tk.Frame(self.side_canvas, bg="#232323", padx=12, pady=12)
    self.side_window_id = self.side_canvas.create_window((0, 0), window=side_panel, anchor="nw")
    side_panel.bind("<Configure>", self._on_side_panel_configure)
    self.side_canvas.bind("<Configure>", self._on_side_canvas_configure)

    tk.Label(side_panel, text="OCR Regions", fg="white", bg="#232323", font=("Helvetica", 12, "bold")).pack(anchor="w")
    offset_text = self._format_offset_text()
    tk.Label(
      side_panel,
      text=offset_text,
      fg="#f0c674",
      bg="#232323",
      wraplength=220,
      justify="left",
    ).pack(anchor="w", pady=(2, 8))
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

    tk.Label(side_panel, text="Templates", fg="white", bg="#232323", font=("Helvetica", 11, "bold")).pack(anchor="w", pady=(0, 4))
    self.show_all_templates_var = tk.BooleanVar(value=False)
    tk.Checkbutton(
      side_panel,
      text="Show all templates",
      variable=self.show_all_templates_var,
      command=self._refresh_template_list,
      fg="white",
      bg="#232323",
      selectcolor="#1b1b1b",
      activebackground="#232323",
      activeforeground="white",
    ).pack(anchor="w", pady=(0, 4))
    self.template_listbox = tk.Listbox(
      side_panel,
      height=4,
      width=28,
      exportselection=False,
      selectmode=tk.SINGLE,
      bg="#1b1b1b",
      fg="white",
    )
    self.template_listbox.bind("<<ListboxSelect>>", self._on_template_select)
    self.template_listbox.pack(fill=tk.BOTH, expand=False)
    self.template_preview_label = tk.Label(side_panel, bg="#1b1b1b", relief=tk.GROOVE)
    self.template_preview_label.pack(fill=tk.BOTH, expand=False, pady=(6, 4))
    self.template_info_var = tk.StringVar()
    tk.Label(side_panel, textvariable=self.template_info_var, fg="#bbbbbb", bg="#232323", wraplength=220, justify="left").pack(anchor="w", pady=(0, 8))
    tk.Button(side_panel, text="Test Template (Space)", command=self.test_selected_template).pack(fill=tk.X, pady=(0, 10))

    self.coord_var = tk.StringVar()
    tk.Label(side_panel, textvariable=self.coord_var, fg="#9feaf9", bg="#232323", wraplength=220, justify="left").pack(anchor="w", pady=(0, 10))

    step_container = tk.Frame(side_panel, bg="#232323")
    step_container.pack(anchor="w", pady=(0, 10))
    tk.Label(step_container, text="Step (px)", fg="white", bg="#232323").pack(side=tk.LEFT)
    self.step_var = tk.IntVar(value=1)
    for value in (1, 5, 10, 25):
      tk.Radiobutton(
        step_container,
        text=str(value),
        variable=self.step_var,
        value=value,
        indicatoron=False,
        width=3,
        fg="white",
        bg="#3a3a3a",
        selectcolor="#5d5d5d",
      ).pack(side=tk.LEFT, padx=2)

    self.show_training_positions_var = tk.BooleanVar(value=True)
    tk.Checkbutton(
      side_panel,
      text="Show training positions",
      variable=self.show_training_positions_var,
      command=self._render_overlay,
      fg="white",
      bg="#232323",
      selectcolor="#1b1b1b",
      activebackground="#232323",
      activeforeground="white",
    ).pack(anchor="w", pady=(0, 10))

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
    self._refresh_template_list()
    self._update_window_dimensions()

  def _format_offset_text(self) -> str:
    if not self._recognition_offset_enabled:
      return "Recognition offset: disabled"
    x, y = self._recognition_offset_values
    suffix = "applied to overrides" if self._recognition_offset_respected else "not applied to overrides"
    return f"Recognition offset: x={x}, y={y} ({suffix})"

  def _bind_hotkeys(self):
    self.root.bind("<Up>", lambda event: self._handle_arrow(event, 0, -1))
    self.root.bind("<Down>", lambda event: self._handle_arrow(event, 0, 1))
    self.root.bind("<Left>", lambda event: self._handle_arrow(event, -1, 0))
    self.root.bind("<Right>", lambda event: self._handle_arrow(event, 1, 0))
    self.root.bind("<Command-s>", lambda event: self.save_overrides())  # macOS shortcut
    self.root.bind("<Control-s>", lambda event: self.save_overrides())
    self.root.bind("<space>", lambda event: self.test_selected_template())
    self.root.bind("<Shift-space>", lambda event: self.capture_screenshot())
    self.root.bind("<r>", lambda event: self.capture_screenshot())

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
    self._template_matches = []
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
          grid = GRID_OVERLAYS.get(self.selected_name)
          if grid:
            cols, rows = grid
            self._draw_grid_overlay(draw, (x1, y1, x2, y2), cols, rows)

    if self._template_matches:
      draw = ImageDraw.Draw(overlay)
      for x1, y1, x2, y2 in self._template_matches:
        draw.rectangle((x1, y1, x2, y2), outline=(255, 64, 64, 255), width=2)

    if self.show_training_positions_var.get() and self.training_positions:
      draw = ImageDraw.Draw(overlay)
      for name, (x, y) in self.training_positions.items():
        radius = 6
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=(80, 220, 120, 255), width=2)
        draw.text((x + radius + 2, y - radius - 2), name, fill=(80, 220, 120, 255))

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
    self._refresh_template_list()
    self._render_overlay()

  def _refresh_template_list(self):
    if self.show_all_templates_var.get():
      templates = self.all_templates
    else:
      templates = self.template_map.get(self.selected_name, [])
    self._current_templates = list(templates)
    self.template_listbox.configure(state=tk.NORMAL)
    self.template_listbox.delete(0, tk.END)
    if not templates:
      self.template_listbox.insert(0, "(no templates)")
      self.template_listbox.configure(state=tk.DISABLED)
      self._set_template_preview(None, "No template for this region.")
      return

    for idx, path in enumerate(templates):
      self.template_listbox.insert(idx, path)
    self.template_listbox.selection_set(0)
    self._set_template_preview(templates[0])

  def _on_template_select(self, _event):
    selection = self.template_listbox.curselection()
    if not selection:
      return
    templates = self._current_templates
    if not templates:
      return
    index = selection[0]
    if index >= len(templates):
      return
    self._set_template_preview(templates[index])

  def _set_template_preview(self, template_path: Optional[str], message: Optional[str] = None):
    self._selected_template_path = template_path
    if not template_path:
      self.template_photo = None
      self.template_preview_label.configure(image="")
      self.template_info_var.set(message or "No template available.")
      return

    template_file = Path(template_path)
    if not template_file.is_absolute():
      template_file = self.base_dir / template_file

    if not template_file.exists():
      self.template_photo = None
      self.template_preview_label.configure(image="")
      self.template_info_var.set(f"Missing template: {template_path}")
      return

    try:
      img = Image.open(template_file).convert("RGBA")
    except Exception as exc:
      self.template_photo = None
      self.template_preview_label.configure(image="")
      self.template_info_var.set(f"Failed to load template: {exc}")
      return

    max_width = 220
    max_height = 160
    img.thumbnail((max_width, max_height), Image.LANCZOS)
    self.template_photo = ImageTk.PhotoImage(img)
    self.template_preview_label.configure(image=self.template_photo)
    self.template_info_var.set(str(Path(template_path)))

  def _draw_grid_overlay(self, draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], cols: int, rows: int):
    x1, y1, x2, y2 = box
    width = max(1, x2 - x1)
    height = max(1, y2 - y1)
    grid_color = (80, 200, 255, 200)
    for col in range(1, cols):
      x = x1 + int(width * col / cols)
      draw.line((x, y1, x, y2), fill=grid_color, width=1)
    for row in range(1, rows):
      y = y1 + int(height * row / rows)
      draw.line((x1, y, x2, y), fill=grid_color, width=1)

  def _dedupe_boxes(self, boxes: List[Tuple[int, int, int, int]], min_dist: int = 5) -> List[Tuple[int, int, int, int]]:
    filtered: List[Tuple[int, int, int, int]] = []
    for x, y, w, h in boxes:
      cx, cy = x + w // 2, y + h // 2
      if all(
        abs(cx - (fx + fw // 2)) > min_dist or abs(cy - (fy + fh // 2)) > min_dist
        for fx, fy, fw, fh in filtered
      ):
        filtered.append((x, y, w, h))
    return filtered

  def test_selected_template(self):
    if not self.screenshot:
      self.status_var.set("Capture a screenshot before testing templates.")
      return
    if not self._selected_template_path:
      self.status_var.set("Select a template to test.")
      return

    template_file = Path(self._selected_template_path)
    if not template_file.is_absolute():
      template_file = self.base_dir / template_file

    template = cv2.imread(str(template_file), cv2.IMREAD_COLOR)
    if template is None:
      self.status_var.set(f"Template could not be loaded: {self._selected_template_path}")
      return

    screenshot_np = np.array(self.screenshot)
    if screenshot_np.ndim == 3 and screenshot_np.shape[2] == 4:
      screenshot_np = cv2.cvtColor(screenshot_np, cv2.COLOR_RGBA2BGR)
    else:
      screenshot_np = cv2.cvtColor(screenshot_np, cv2.COLOR_RGB2BGR)

    search_box = self._current_box(self.selected_name)
    if search_box:
      x1, y1, x2, y2 = search_box
      x1, y1 = max(0, x1), max(0, y1)
      x2 = min(screenshot_np.shape[1], x2)
      y2 = min(screenshot_np.shape[0], y2)
    else:
      x1, y1, x2, y2 = (0, 0, screenshot_np.shape[1], screenshot_np.shape[0])

    if x2 <= x1 or y2 <= y1:
      self.status_var.set("Selected region is empty; adjust the region first.")
      return

    crop = screenshot_np[y1:y2, x1:x2]
    if crop.shape[0] < template.shape[0] or crop.shape[1] < template.shape[1]:
      self.status_var.set("Template is larger than the selected region.")
      return

    result = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(result)
    threshold = 0.8
    loc = np.where(result >= threshold)
    h, w = template.shape[:2]
    raw_matches = [(x, y, w, h) for (x, y) in zip(*loc[::-1])]
    matches = self._dedupe_boxes(raw_matches, min_dist=max(4, min(w, h) // 3))
    matches = [(x1 + x, y1 + y, x1 + x + w, y1 + y + h) for (x, y, w, h) in matches]
    self._template_matches = matches
    if matches:
      self.status_var.set(f"Template match: {len(matches)} hit(s), best={max_val:.3f}.")
    else:
      self.status_var.set(f"No match (best={max_val:.3f}).")
    self._render_overlay()

  def _on_side_panel_configure(self, _event):
    self.side_canvas.configure(scrollregion=self.side_canvas.bbox("all"))

  def _on_side_canvas_configure(self, event):
    self.side_canvas.itemconfigure(self.side_window_id, width=event.width)

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
