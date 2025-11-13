import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from utils import constants
from utils.log import debug, error, info, warning
from core import state as bot_state

BASE_DIR = Path(__file__).resolve().parents[2]


def _build_context(settings: Dict[str, Any]) -> Dict[str, Any]:
  return {
    "overlay_dim_opacity": int(settings.get("overlay_dim_opacity", 196)),
    "overrides_path": settings.get("overrides_path"),
    "regions": constants.export_adjustable_coordinates(),
    "window_names": _window_name_candidates(),
    "process_names": _process_name_candidates(),
    "mac_bounds": _mac_bounds_settings(),
    "recognition_offset": _active_recognition_offset_snapshot(settings),
  }


def _window_name_candidates() -> List[str]:
  candidates: List[str] = []

  window_name = getattr(bot_state, "WINDOW_NAME", None)
  if isinstance(window_name, str) and window_name.strip():
    candidates.append(window_name.strip())

  mac_settings = getattr(bot_state, "MAC_AIR_SETTINGS", {}) or {}
  for key in ("window_name", "process_name"):
    value = mac_settings.get(key)
    if isinstance(value, str) and value.strip():
      candidates.append(value.strip())
    elif isinstance(value, (list, tuple)):
      for item in value:
        if isinstance(item, str) and item.strip():
          candidates.append(item.strip())

  # Add common fallbacks at the end to increase hit rate.
  candidates.extend(["BlueStacks Air", "BlueStacks"])

  deduped: List[str] = []
  seen = set()
  for name in candidates:
    if name not in seen:
      deduped.append(name)
      seen.add(name)

  return deduped


def _process_name_candidates() -> List[str]:
  candidates: List[str] = []
  mac_settings = getattr(bot_state, "MAC_AIR_SETTINGS", {}) or {}
  value = mac_settings.get("process_name")
  if isinstance(value, str) and value.strip():
    candidates.append(value.strip())
  elif isinstance(value, (list, tuple)):
    for item in value:
      if isinstance(item, str) and item.strip():
        candidates.append(item.strip())

  candidates.extend(["BlueStacks Air", "BlueStacksX", "BlueStacks"])

  deduped: List[str] = []
  seen = set()
  for name in candidates:
    if name not in seen:
      deduped.append(name)
      seen.add(name)

  return deduped


def _mac_bounds_settings() -> Dict[str, Any]:
  mac_settings = getattr(bot_state, "MAC_AIR_SETTINGS", {}) or {}
  bounds = mac_settings.get("bounds") or {}
  return {
    "set_bounds": bool(mac_settings.get("set_bounds", False)),
    "bounds": {
      "x": int(bounds.get("x", 0) or 0),
      "y": int(bounds.get("y", 0) or 0),
      "width": int(bounds.get("width", 640) or 640),
      "height": int(bounds.get("height", 1113) or 1113),
    },
  }


def _active_recognition_offset_snapshot(settings: Dict[str, Any]) -> Dict[str, Any]:
  mac_settings = getattr(bot_state, "MAC_AIR_SETTINGS", {}) or {}
  enabled = bool(mac_settings.get("apply_recognition_offset"))
  x, y = getattr(bot_state, "ACTIVE_RECOGNITION_OFFSET", (0, 0))
  return {
    "x": int(x or 0),
    "y": int(y or 0),
    "enabled": enabled,
    "respected_by_overrides": bool(settings.get("respect_recognition_offset")),
  }


def _write_context_file(context: Dict[str, Any]) -> Path:
  tmp_file = tempfile.NamedTemporaryFile(
    prefix="uma_region_adjuster_",
    suffix=".json",
    delete=False,
    mode="w",
    encoding="utf-8",
  )
  try:
    with tmp_file:
      json.dump(context, tmp_file, indent=2)
  except Exception:
    Path(tmp_file.name).unlink(missing_ok=True)
    raise
  return Path(tmp_file.name)


def run_region_adjuster_session(settings: Dict[str, Any]) -> bool:
  if not settings.get("enabled"):
    warning("Region adjuster is disabled in the config; enable debug.region_adjuster.enabled first.")
    return False

  context = _build_context(settings)
  regions = context.get("regions") or []
  if not regions:
    warning("No OCR regions/bboxes are available to adjust.")
    return False

  try:
    context_path = _write_context_file(context)
  except OSError as exc:
    error(f"Failed to create a context file for the region adjuster: {exc}")
    return False

  cmd = [sys.executable, "-m", "core.region_adjuster", "--context", str(context_path)]
  info("Launching the OCR region adjuster window (this may take a second)...")
  debug(f"Running region adjuster command: {' '.join(cmd)}")

  try:
    process = subprocess.Popen(cmd, cwd=str(BASE_DIR), env=os.environ.copy())
  except Exception as exc:
    context_path.unlink(missing_ok=True)
    error(f"Unable to start the region adjuster UI: {exc}")
    return False

  try:
    return_code = process.wait()
  except KeyboardInterrupt:
    process.terminate()
    raise
  finally:
    context_path.unlink(missing_ok=True)

  if return_code != 0:
    warning("Region adjuster closed with a non-zero exit code; overrides may not have been saved.")
    return False

  info("Region adjuster closed. If you saved overrides, the config will be reloaded momentarily.")
  return True
