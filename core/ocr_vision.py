"""Apple Vision OCR backend.

This module wraps ``VNRecognizeTextRequest`` via PyObjC so the routing facade
in ``core/ocr.py`` can opt specific surfaces into native macOS OCR while the
rest of the bot continues to use EasyOCR.

Design goals:

- Safe imports: if PyObjC or the Vision framework is not present, the module
  still imports and reports ``is_available() == False``. Callers must use
  ``is_available()`` and be prepared to fall back.
- Normalized output: detailed reads return the same
  ``(bbox_quad, text, confidence)`` shape as EasyOCR so the facade does not
  need backend-specific adapters at every call site.
"""

from __future__ import annotations

import io
import re
import threading
from typing import Any, List, Optional, Tuple

import numpy as np
from PIL import Image

from utils.log import debug, warning


BACKEND_NAME = "vision"


# Recognition level enum values as defined by the Vision framework.
# Documented here to avoid importing Vision just to read the constants.
_RECOGNITION_LEVEL_ACCURATE = 0
_RECOGNITION_LEVEL_FAST = 1


_IMPORT_LOCK = threading.Lock()
_IMPORT_STATE = {"attempted": False, "ok": False, "error": None}
_VISION = None
_FOUNDATION = None
_QUARTZ = None


def _try_import():
  """Attempt to import the PyObjC Vision/Foundation/Quartz bindings.

  Results are cached so repeated calls stay cheap and so a one-time failure
  does not keep raising import errors on every OCR call.
  """
  global _VISION, _FOUNDATION, _QUARTZ
  with _IMPORT_LOCK:
    if _IMPORT_STATE["attempted"]:
      return _IMPORT_STATE["ok"]
    _IMPORT_STATE["attempted"] = True
    try:
      import Vision as _vision  # type: ignore
      import Foundation as _foundation  # type: ignore
      import Quartz as _quartz  # type: ignore
      _VISION = _vision
      _FOUNDATION = _foundation
      _QUARTZ = _quartz
      _IMPORT_STATE["ok"] = True
    except Exception as exc:  # pragma: no cover — covered via fallback test
      _IMPORT_STATE["ok"] = False
      _IMPORT_STATE["error"] = str(exc)
      warning(f"[OCR] Apple Vision backend unavailable: {exc}")
    return _IMPORT_STATE["ok"]


def is_available() -> bool:
  """Return True if the Vision framework can be used on this machine."""
  return _try_import()


def last_import_error() -> Optional[str]:
  """Expose the last Vision import error for diagnostics."""
  return _IMPORT_STATE.get("error")


def _pil_to_ci_image(pil_img: Image.Image):
  """Convert a PIL/NumPy image into a Quartz CIImage via PNG bytes."""
  if isinstance(pil_img, np.ndarray):
    pil_img = Image.fromarray(pil_img)
  if pil_img.mode not in ("RGB", "RGBA", "L"):
    pil_img = pil_img.convert("RGB")

  buffer = io.BytesIO()
  pil_img.save(buffer, format="PNG")
  png_bytes = buffer.getvalue()

  ns_data = _FOUNDATION.NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
  # Quartz re-exports CIImage from the CoreImage framework on macOS.
  CIImage = getattr(_QUARTZ, "CIImage", None)
  if CIImage is None:  # pragma: no cover — depends on PyObjC build
    try:
      import CoreImage  # type: ignore
      CIImage = CoreImage.CIImage
    except Exception as exc:
      raise RuntimeError(f"CoreImage.CIImage unavailable: {exc}") from exc
  ci_image = CIImage.imageWithData_(ns_data)
  if ci_image is None:
    raise RuntimeError("CIImage.imageWithData_ returned nil")
  return ci_image, pil_img.size  # (W, H)


def _normalized_bbox_to_quad(bbox, image_size) -> List[Tuple[int, int]]:
  """Convert a Vision normalized bbox into an EasyOCR-compatible quad.

  Vision returns a ``CGRect`` with origin at bottom-left and coordinates in
  [0, 1]. EasyOCR's detailed format is a 4-point quad in pixel space with
  origin at top-left. Return ``[(x_l, y_t), (x_r, y_t), (x_r, y_b), (x_l, y_b)]``.
  """
  width, height = image_size
  origin = bbox.origin
  size = bbox.size
  x_left = float(origin.x) * width
  x_right = (float(origin.x) + float(size.width)) * width
  y_bottom_px = height - float(origin.y) * height
  y_top_px = height - (float(origin.y) + float(size.height)) * height
  x_l = int(round(min(x_left, x_right)))
  x_r = int(round(max(x_left, x_right)))
  y_t = int(round(min(y_top_px, y_bottom_px)))
  y_b = int(round(max(y_top_px, y_bottom_px)))
  return [(x_l, y_t), (x_r, y_t), (x_r, y_b), (x_l, y_b)]


def _filter_to_allowlist(text: str, allowlist: Optional[str]) -> str:
  if not allowlist:
    return text
  allowed = set(allowlist)
  return "".join(ch for ch in text if ch in allowed)


def _apply_settings_to_request(request, settings: Optional[dict]):
  settings = settings or {}
  level_raw = str(settings.get("recognition_level", "accurate") or "accurate").lower()
  level = _RECOGNITION_LEVEL_ACCURATE if level_raw.startswith("acc") else _RECOGNITION_LEVEL_FAST
  try:
    request.setRecognitionLevel_(level)
  except Exception:  # pragma: no cover — defensive only
    pass
  try:
    request.setUsesLanguageCorrection_(bool(settings.get("prefer_language_correction", False)))
  except Exception:
    pass
  min_height = settings.get("minimum_text_height")
  if min_height is not None:
    try:
      request.setMinimumTextHeight_(float(min_height))
    except Exception:
      pass


def _perform_request(ci_image, request) -> bool:
  Handler = _VISION.VNImageRequestHandler
  handler = Handler.alloc().initWithCIImage_options_(ci_image, None)
  try:
    ok, err = handler.performRequests_error_([request], None)
  except Exception as exc:
    raise RuntimeError(f"Vision performRequests_error_ raised: {exc}") from exc
  if not ok:
    raise RuntimeError(f"Vision performRequests_error_ returned error: {err}")
  return True


def _collect_observations(request, image_size, min_confidence: float) -> List[Tuple[list, str, float]]:
  results = request.results() or []
  rows: List[Tuple[list, str, float]] = []
  for observation in results:
    try:
      candidates = observation.topCandidates_(1)
    except Exception:
      candidates = None
    if not candidates:
      continue
    candidate = candidates[0]
    text = str(candidate.string())
    confidence = float(candidate.confidence())
    if confidence < float(min_confidence):
      continue
    try:
      bbox = observation.boundingBox()
    except Exception:
      continue
    quad = _normalized_bbox_to_quad(bbox, image_size)
    rows.append((quad, text, confidence))
  return rows


def readtext_detailed(
  pil_img,
  *,
  allowlist: Optional[str] = None,
  threshold: Optional[float] = None,
  settings: Optional[dict] = None,
  **_unused,
) -> List[Tuple[list, str, float]]:
  """Run Vision OCR and return EasyOCR-shaped rows.

  Raises ``RuntimeError`` on backend unavailability or framework failure so
  the facade can decide to fall back to EasyOCR.
  """
  if not is_available():
    raise RuntimeError("Apple Vision backend is not available on this system")

  settings = settings or {}
  ci_image, image_size = _pil_to_ci_image(pil_img)
  request = _VISION.VNRecognizeTextRequest.alloc().init()
  _apply_settings_to_request(request, settings)

  _perform_request(ci_image, request)

  min_conf_setting = settings.get("minimum_confidence")
  if min_conf_setting is None:
    min_conf_setting = threshold if threshold is not None else 0.0
  rows = _collect_observations(request, image_size, float(min_conf_setting or 0.0))

  if allowlist:
    filtered: List[Tuple[list, str, float]] = []
    for quad, text, conf in rows:
      cleaned = _filter_to_allowlist(text, allowlist)
      cleaned = re.sub(r"\s+", " ", cleaned).strip()
      if cleaned:
        filtered.append((quad, cleaned, conf))
    rows = filtered

  return rows


def _sort_rows_for_text(rows):
  def _sort_key(row):
    quad = row[0]
    y = min(pt[1] for pt in quad)
    x = min(pt[0] for pt in quad)
    return (y, x)
  return sorted(rows, key=_sort_key)


def extract_text(pil_img, use_recognize=False, allowlist=None, threshold=None, settings=None):
  rows = readtext_detailed(
    pil_img,
    allowlist=allowlist,
    threshold=threshold,
    settings=settings,
  )
  rows = _sort_rows_for_text(rows)
  combined = " ".join(text for _, text, _ in rows).strip()
  combined = re.sub(r"\s+", " ", combined).strip()
  return combined


def extract_number(pil_img, allowlist="0123456789", threshold=0.8, settings=None):
  rows = readtext_detailed(
    pil_img,
    allowlist=allowlist,
    threshold=threshold,
    settings=settings,
  )
  rows.sort(key=lambda row: min(pt[0] for pt in row[0]))
  joined = "".join(text for _, text, _ in rows)
  digits = re.sub(r"[^\d]", "", joined)
  if digits:
    return int(digits)
  return -1


def extract_allowed_text(pil_img, allowlist="0123456789", settings=None):
  rows = readtext_detailed(
    pil_img,
    allowlist=allowlist,
    threshold=None,
    settings=settings,
  )
  rows.sort(key=lambda row: min(pt[0] for pt in row[0]))
  return " ".join(text for _, text, _ in rows)
