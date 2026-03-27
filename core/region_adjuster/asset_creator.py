import json
import platform
import tkinter as tk
from tkinter import filedialog, messagebox
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import cv2
import numpy as np
import mss
import pyautogui
from PIL import Image, ImageDraw, ImageTk


def capture_screen_image(capture_bbox=None) -> Image.Image:
  if platform.system() == "Darwin":
    with mss.mss() as sct:
      if capture_bbox is not None:
        left, top, right, bottom = [int(v) for v in capture_bbox]
        monitor = {
          "left": left,
          "top": top,
          "width": max(1, right - left),
          "height": max(1, bottom - top),
        }
      else:
        monitor = dict(sct.monitors[0])
      raw = np.array(sct.grab(monitor))
      return Image.fromarray(cv2.cvtColor(raw, cv2.COLOR_BGRA2RGBA), mode="RGBA")

  screenshot = pyautogui.screenshot()
  if capture_bbox is not None:
    left, top, right, bottom = [int(v) for v in capture_bbox]
    screenshot = screenshot.crop((left, top, right, bottom))
  return screenshot.convert("RGBA")


class AssetCreatorWindow:
  """Standalone window for creating template assets from screenshot selections."""

  def __init__(self, parent=None, screenshot=None, context: Optional[Dict] = None, capture_bbox=None):
    self._context = context or {}
    self._screenshot: Optional[Image.Image] = None
    self._capture_bbox = tuple(capture_bbox) if capture_bbox is not None else None
    self._photo_image = None
    self._selection_photo = None
    self._selecting = False
    self._selection_start = (0, 0)
    self._selection_rect: Optional[Tuple[int, int, int, int]] = None
    self._selection_canvas_id = None
    self._last_saved_path: Optional[str] = None

    if parent:
      self._window = tk.Toplevel(parent)
    else:
      self._window = tk.Tk()

    self._window.title("Uma Asset Creator")
    self._window.configure(bg="#1a1a2e")
    self._window.geometry("1300x850")

    self._build_layout()

    if screenshot:
      self._screenshot = screenshot.convert("RGBA")
      self._render()
      self._status_var.set("Screenshot loaded. Drag to select a region.")
    else:
      self._capture_screenshot()

  def _build_layout(self):
    win = self._window

    # Toolbar
    toolbar = tk.Frame(win, bg="#1a1a2e", padx=8, pady=6)
    toolbar.pack(fill=tk.X)

    tk.Button(toolbar, text="Refresh Screenshot", command=self._capture_screenshot).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(toolbar, text="Close", command=self._window.destroy).pack(side=tk.RIGHT, padx=2)

    self._status_var = tk.StringVar(value="Draw a selection rectangle on the screenshot.")
    tk.Label(
      toolbar,
      textvariable=self._status_var,
      fg="#8bd5ca",
      bg="#1a1a2e",
      anchor="w",
    ).pack(side=tk.LEFT, padx=(16, 0), fill=tk.X, expand=True)

    # Main content
    content = tk.Frame(win, bg="#1a1a2e")
    content.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
    content.columnconfigure(0, weight=1)
    content.columnconfigure(1, weight=0)
    content.rowconfigure(0, weight=1)

    # Canvas with scrollbars
    canvas_frame = tk.Frame(content, bg="#1a1a2e")
    canvas_frame.grid(row=0, column=0, sticky="nsew")
    canvas_frame.rowconfigure(0, weight=1)
    canvas_frame.columnconfigure(0, weight=1)

    self._canvas = tk.Canvas(canvas_frame, bg="black", highlightthickness=0, cursor="crosshair")
    self._canvas.grid(row=0, column=0, sticky="nsew")
    v_scroll = tk.Scrollbar(canvas_frame, orient="vertical", command=self._canvas.yview)
    v_scroll.grid(row=0, column=1, sticky="ns")
    h_scroll = tk.Scrollbar(canvas_frame, orient="horizontal", command=self._canvas.xview)
    h_scroll.grid(row=1, column=0, sticky="ew")
    self._canvas.configure(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)

    self._canvas_image_id = self._canvas.create_image(0, 0, anchor="nw")
    self._canvas.bind("<ButtonPress-1>", self._on_press)
    self._canvas.bind("<B1-Motion>", self._on_drag)
    self._canvas.bind("<ButtonRelease-1>", self._on_release)

    # Right panel
    right = tk.Frame(content, bg="#1a1a2e", padx=12, width=280)
    right.grid(row=0, column=1, sticky="nsew")
    right.grid_propagate(False)

    tk.Label(
      right,
      text="Selection Preview",
      fg="white",
      bg="#1a1a2e",
      font=("Helvetica", 11, "bold"),
    ).pack(anchor="w", pady=(0, 4))
    self._preview_label = tk.Label(
      right,
      bg="#192028",
      relief=tk.GROOVE,
      text="No selection",
      fg="#9aa4ad",
    )
    self._preview_label.pack(fill=tk.X, pady=(0, 8))

    self._selection_info_var = tk.StringVar(value="")
    tk.Label(
      right,
      textvariable=self._selection_info_var,
      fg="#bbbbbb",
      bg="#1a1a2e",
      wraplength=250,
      justify="left",
    ).pack(anchor="w", pady=(0, 12))

    # Save controls
    tk.Label(
      right,
      text="Save Path",
      fg="white",
      bg="#1a1a2e",
      font=("Helvetica", 11, "bold"),
    ).pack(anchor="w", pady=(0, 4))
    self._save_path_var = tk.StringVar(value="assets/custom/")
    path_frame = tk.Frame(right, bg="#1a1a2e")
    path_frame.pack(fill=tk.X, pady=(0, 4))
    tk.Entry(
      path_frame,
      textvariable=self._save_path_var,
      bg="#192028",
      fg="white",
      insertbackground="white",
    ).pack(side=tk.LEFT, fill=tk.X, expand=True)
    tk.Button(path_frame, text="...", command=self._browse_path, width=3).pack(side=tk.RIGHT, padx=(4, 0))

    tk.Label(right, text="Filename", fg="white", bg="#1a1a2e").pack(anchor="w", pady=(0, 2))
    self._filename_var = tk.StringVar(value=f"asset_{datetime.now().strftime('%H%M%S')}.png")
    tk.Entry(
      right,
      textvariable=self._filename_var,
      bg="#192028",
      fg="white",
      insertbackground="white",
    ).pack(fill=tk.X, pady=(0, 8))

    tk.Button(right, text="Save Asset", command=self._save_asset).pack(fill=tk.X, pady=(0, 4))
    tk.Button(right, text="Copy AI Context", command=self._copy_context).pack(fill=tk.X, pady=(0, 4))

    self._saved_info_var = tk.StringVar(value="")
    tk.Label(
      right,
      textvariable=self._saved_info_var,
      fg="#8bd5ca",
      bg="#1a1a2e",
      wraplength=250,
      justify="left",
    ).pack(anchor="w", pady=(8, 0))

    # Context info
    if self._context:
      tk.Label(
        right,
        text="Context",
        fg="white",
        bg="#1a1a2e",
        font=("Helvetica", 11, "bold"),
      ).pack(anchor="w", pady=(16, 4))
      context_lines = []
      for key, val in self._context.items():
        if val not in (None, "", "-"):
          context_lines.append(f"{key}: {val}")
      if context_lines:
        tk.Label(
          right,
          text="\n".join(context_lines),
          fg="#9aa4ad",
          bg="#1a1a2e",
          wraplength=250,
          justify="left",
          anchor="w",
        ).pack(anchor="w")

  def _capture_screenshot(self):
    try:
      screenshot = capture_screen_image(self._capture_bbox)
    except Exception as exc:
      messagebox.showerror("Screenshot Failed", f"Unable to capture: {exc}")
      return

    self._screenshot = screenshot.convert("RGBA")
    self._selection_rect = None
    self._render()
    self._status_var.set("Screenshot captured. Drag to select a region.")

  def _render(self):
    if not self._screenshot:
      return
    self._photo_image = ImageTk.PhotoImage(self._screenshot)
    self._canvas.itemconfigure(self._canvas_image_id, image=self._photo_image)
    self._canvas.config(scrollregion=(0, 0, self._screenshot.width, self._screenshot.height))
    if self._selection_canvas_id:
      self._canvas.delete(self._selection_canvas_id)
      self._selection_canvas_id = None

  def _canvas_coords(self, event):
    return (
      int(round(self._canvas.canvasx(event.x))),
      int(round(self._canvas.canvasy(event.y))),
    )

  def _on_press(self, event):
    x, y = self._canvas_coords(event)
    self._selecting = True
    self._selection_start = (x, y)
    if self._selection_canvas_id:
      self._canvas.delete(self._selection_canvas_id)
    self._selection_canvas_id = self._canvas.create_rectangle(
      x, y, x, y, outline="#00ff88", width=2, dash=(4, 4),
    )

  def _on_drag(self, event):
    if not self._selecting:
      return
    x, y = self._canvas_coords(event)
    sx, sy = self._selection_start
    self._canvas.coords(self._selection_canvas_id, sx, sy, x, y)

  def _on_release(self, event):
    if not self._selecting:
      return
    self._selecting = False
    x, y = self._canvas_coords(event)
    sx, sy = self._selection_start
    x1, y1 = min(sx, x), min(sy, y)
    x2, y2 = max(sx, x), max(sy, y)
    if x2 - x1 < 3 or y2 - y1 < 3:
      self._selection_rect = None
      self._status_var.set("Selection too small. Drag a larger area.")
      return
    self._selection_rect = (x1, y1, x2, y2)
    self._update_selection_preview()
    self._status_var.set(f"Selected {x2 - x1}x{y2 - y1} at ({x1},{y1}). Save or copy context.")

  def _update_selection_preview(self):
    if not self._screenshot or not self._selection_rect:
      self._selection_photo = None
      self._preview_label.configure(image="", text="No selection")
      self._selection_info_var.set("")
      return

    x1, y1, x2, y2 = self._selection_rect
    crop = self._screenshot.crop((x1, y1, x2, y2))
    preview = crop.copy()
    preview.thumbnail((260, 200), Image.LANCZOS)
    self._selection_photo = ImageTk.PhotoImage(preview)
    self._preview_label.configure(image=self._selection_photo, text="")
    info_lines = [f"{x2 - x1}x{y2 - y1}px at ({x1}, {y1}) -> ({x2}, {y2})"]
    game_window_relative = self._selection_game_window_relative_rect()
    if game_window_relative is not None:
      gx1, gy1, gx2, gy2 = game_window_relative
      info_lines.append(f"game window: ({gx1}, {gy1}) -> ({gx2}, {gy2})")
    self._selection_info_var.set("\n".join(info_lines))

  def _selection_absolute_rect(self):
    if not self._selection_rect:
      return None
    x1, y1, x2, y2 = self._selection_rect
    if self._capture_bbox is None:
      return (x1, y1, x2, y2)
    left, top, _, _ = [int(v) for v in self._capture_bbox]
    return (x1 + left, y1 + top, x2 + left, y2 + top)

  def _selection_game_window_relative_rect(self):
    absolute = self._selection_absolute_rect()
    game_window_bbox_text = self._context.get("game_window_bbox")
    if absolute is None or not game_window_bbox_text:
      return None
    try:
      left, top, _, _ = [int(v.strip()) for v in game_window_bbox_text.strip("()[]").split(",")]
    except Exception:
      return None
    x1, y1, x2, y2 = absolute
    return (x1 - left, y1 - top, x2 - left, y2 - top)

  def _browse_path(self):
    directory = filedialog.askdirectory(initialdir=self._save_path_var.get() or "assets/")
    if directory:
      self._save_path_var.set(directory + "/")

  def _save_asset(self):
    if not self._screenshot or not self._selection_rect:
      self._status_var.set("No selection to save. Drag on the screenshot first.")
      return

    x1, y1, x2, y2 = self._selection_rect
    crop = self._screenshot.crop((x1, y1, x2, y2))

    directory = self._save_path_var.get().strip()
    filename = self._filename_var.get().strip()
    if not filename:
      filename = f"asset_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    if not filename.endswith(".png"):
      filename += ".png"

    save_path = Path(directory) / filename
    try:
      save_path.parent.mkdir(parents=True, exist_ok=True)
      crop.convert("RGB").save(str(save_path))
    except Exception as exc:
      messagebox.showerror("Save Failed", f"Unable to save: {exc}")
      return

    self._last_saved_path = str(save_path)
    self._saved_info_var.set(f"Saved: {save_path}")
    self._status_var.set(f"Asset saved to {save_path}")

  def _copy_context(self):
    if not self._selection_rect:
      self._status_var.set("No selection to copy context for.")
      return

    x1, y1, x2, y2 = self._selection_rect
    w, h = x2 - x1, y2 - y1

    lines = ["## New Template Asset"]
    if self._last_saved_path:
      lines.append(f"- **File**: `{self._last_saved_path}`")
    else:
      lines.append("- **File**: (not saved yet)")
    lines.append(f"- **Dimensions**: {w}x{h}px")
    lines.append(f"- **Source coordinates**: ({x1}, {y1}) to ({x2}, {y2})")
    absolute_rect = self._selection_absolute_rect()
    if absolute_rect is not None and absolute_rect != (x1, y1, x2, y2):
      ax1, ay1, ax2, ay2 = absolute_rect
      lines.append(f"- **Absolute screen coordinates**: ({ax1}, {ay1}) to ({ax2}, {ay2})")
    game_window_relative = self._selection_game_window_relative_rect()
    if game_window_relative is not None:
      gx1, gy1, gx2, gy2 = game_window_relative
      lines.append(f"- **Game-window coordinates**: ({gx1}, {gy1}) to ({gx2}, {gy2})")
    lines.append("")

    # Include context from caller
    if self._context:
      lines.append("## Current State")
      for key, val in self._context.items():
        if val not in (None, "", "-"):
          lines.append(f"- {key}: {val}")
      lines.append("")

    lines.append("## Integration Guide")
    lines.append("")
    lines.append("To integrate this asset into the bot for template matching:")
    lines.append("")
    lines.append("1. Register the template path in `utils/constants.py`:")
    if self._last_saved_path:
      lines.append(f'   Add `"{self._last_saved_path}"` to the relevant template dict')
    else:
      lines.append("   Add the saved file path to the relevant template dict")
    lines.append("")
    lines.append("2. Use `device_action.match_template()` for detection:")
    lines.append("```python")
    if self._last_saved_path:
      lines.append(f'result = device_action.match_template("{self._last_saved_path}",')
    else:
      lines.append('result = device_action.match_template("assets/custom/YOUR_ASSET.png",')
    lines.append(f"  region_ltrb=({x1}, {y1}, {x2}, {y2}))")
    lines.append("```")
    lines.append("")
    lines.append("3. Or use `match_cached_templates()` for repeated checks:")
    lines.append("```python")
    lines.append('# Add to a template cache dict, then:')
    lines.append('matches = device_action.match_cached_templates(template_dict, region_ltrb=...)')
    lines.append("```")
    lines.append("")
    lines.append("### Key files")
    lines.append("- `utils/constants.py` - Template path definitions and region coordinates")
    lines.append("- `utils/device_action_wrapper.py` - Template matching API")
    lines.append("- `core/skeleton.py` - Main game loop detection logic")

    context = "\n".join(lines)
    self._window.clipboard_clear()
    self._window.clipboard_append(context)
    self._status_var.set("AI context copied to clipboard.")

  def run(self):
    if isinstance(self._window, tk.Tk):
      self._window.mainloop()


def launch_asset_creator(parent=None, screenshot=None, context=None, capture_bbox=None):
  """Launch the asset creator window. Can be called from any module."""
  return AssetCreatorWindow(parent=parent, screenshot=screenshot, context=context, capture_bbox=capture_bbox)
