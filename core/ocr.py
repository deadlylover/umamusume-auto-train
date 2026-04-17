"""Routing facade for OCR calls.

Historically this module owned the EasyOCR reader directly. It is now a thin
router that selects a backend per named OCR surface, prefers the configured
default backend (Vision in this repo), and falls back to EasyOCR on error.

Public API (stable):

- ``extract_text(pil_img, ...)``
- ``extract_number(pil_img, ...)``
- ``extract_allowed_text(pil_img, ...)``
- ``get_reader()`` / ``reload_reader()`` / ``flush_gpu_cache()``

New helpers:

- ``readtext_detailed(pil_img, *, ocr_surface=..., ...)``
- ``get_last_ocr_meta()``
- ``resolve_backend(surface, explicit)``

Call sites that need routing should pass ``ocr_surface="<surface_name>"``.
All existing callers keep working without changes.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional, Tuple

from PIL import Image

from utils.log import debug, info, warning
import core.config as config
from core import ocr_easyocr, ocr_vision


# ---------------------------------------------------------------------------
# Routing metadata
# ---------------------------------------------------------------------------

_BACKEND_EASYOCR = "easyocr"
_BACKEND_VISION = "vision"
_KNOWN_BACKENDS = (_BACKEND_EASYOCR, _BACKEND_VISION)

_meta_lock = threading.Lock()
_last_meta: Dict[str, Any] = {}


def _set_last_meta(meta: Dict[str, Any]):
  with _meta_lock:
    _last_meta.clear()
    _last_meta.update(meta)


def get_last_ocr_meta() -> Dict[str, Any]:
  """Return a copy of the routing metadata for the most recent OCR call."""
  with _meta_lock:
    return dict(_last_meta)


def _log_routing(meta: Dict[str, Any]):
  if getattr(config, "VERBOSE_OCR", False):
    info(
      "[OCR] surface={surface} op={op} backend={backend} requested={requested}"
      " fallback={fallback} fallback_error={fallback_error} reason={reason}".format(
        surface=meta.get("surface"),
        op=meta.get("op"),
        backend=meta.get("backend"),
        requested=meta.get("requested_backend"),
        fallback=meta.get("fallback"),
        fallback_error=meta.get("fallback_error"),
        reason=meta.get("reason"),
      )
    )


# ---------------------------------------------------------------------------
# Backend resolution
# ---------------------------------------------------------------------------


def _global_default_backend() -> str:
  raw = str(getattr(config, "OCR_BACKEND", _BACKEND_VISION) or _BACKEND_VISION).strip().lower()
  if raw not in _KNOWN_BACKENDS:
    warning(f"[OCR] Unknown ocr_backend='{raw}'; defaulting to {_BACKEND_VISION}.")
    return _BACKEND_VISION
  return raw


def _route_overrides() -> Dict[str, str]:
  raw = getattr(config, "OCR_ROUTE_OVERRIDES", {}) or {}
  if not isinstance(raw, dict):
    return {}
  normalized: Dict[str, str] = {}
  for surface, backend in raw.items():
    if not surface:
      continue
    backend_normalized = str(backend or "").strip().lower()
    if backend_normalized not in _KNOWN_BACKENDS:
      warning(
        f"[OCR] ocr_route_overrides: surface='{surface}' backend='{backend}' is unknown; ignoring."
      )
      continue
    normalized[str(surface)] = backend_normalized
  return normalized


def _vision_settings() -> Dict[str, Any]:
  raw = getattr(config, "VISION_OCR_SETTINGS", {}) or {}
  return raw if isinstance(raw, dict) else {}


def resolve_backend(surface: Optional[str], explicit_backend: Optional[str]) -> Tuple[str, str]:
  """Resolve the backend name for a call.

  Returns ``(backend, reason)`` where ``backend`` is one of the known backend
  names and ``reason`` describes how the decision was made (for debug logs).
  Priority: explicit > surface override > global default.
  Runtime dispatch may still fall back to EasyOCR if Vision is unavailable or
  raises.
  """
  if explicit_backend:
    normalized = str(explicit_backend).strip().lower()
    if normalized in _KNOWN_BACKENDS:
      return normalized, "explicit"
    warning(
      f"[OCR] Unknown ocr_backend kwarg '{explicit_backend}'; falling back to default."
    )
  if surface:
    overrides = _route_overrides()
    mapped = overrides.get(str(surface))
    if mapped:
      return mapped, "surface_override"
  return _global_default_backend(), "global_default"


# ---------------------------------------------------------------------------
# Dispatchers
# ---------------------------------------------------------------------------


def _backend_is_available(backend: str) -> bool:
  if backend == _BACKEND_VISION:
    return ocr_vision.is_available()
  return ocr_easyocr.is_available()


def _dispatch(op: str, surface, explicit_backend, call_easyocr, call_vision):
  """Run ``call_<backend>`` for the resolved backend with EasyOCR fallback.

  Parameters
  ----------
  op : str
      Operation name used for logging (``text``, ``number``, ``detailed``...).
  surface : Optional[str]
      Named OCR surface for routing.
  explicit_backend : Optional[str]
      Caller-forced backend.
  call_easyocr : callable
      Thunk that invokes the EasyOCR backend and returns its result.
  call_vision : callable
      Thunk that invokes the Vision backend. May raise RuntimeError / other
      exceptions to trigger fallback.
  """
  backend, reason = resolve_backend(surface, explicit_backend)
  requested = backend

  fallback = False
  fallback_error = None
  result = None

  if backend == _BACKEND_VISION:
    if not ocr_vision.is_available():
      fallback = True
      fallback_error = ocr_vision.last_import_error() or "vision_unavailable"
      backend = _BACKEND_EASYOCR
    else:
      try:
        result = call_vision()
      except Exception as exc:
        warning(f"[OCR] Vision backend failed on surface={surface!r}: {exc}; falling back to EasyOCR.")
        fallback = True
        fallback_error = str(exc)
        backend = _BACKEND_EASYOCR

  if backend == _BACKEND_EASYOCR:
    result = call_easyocr()

  meta = {
    "op": op,
    "surface": surface,
    "requested_backend": requested,
    "backend": backend,
    "reason": reason,
    "fallback": fallback,
    "fallback_error": fallback_error,
  }
  _set_last_meta(meta)
  _log_routing(meta)
  return result, meta


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def extract_text(
  pil_img,
  use_recognize=False,
  allowlist=None,
  threshold=None,
  *,
  ocr_surface: Optional[str] = None,
  ocr_backend: Optional[str] = None,
):
  def _easy():
    return ocr_easyocr.extract_text(
      pil_img, use_recognize=use_recognize, allowlist=allowlist, threshold=threshold
    )

  def _vision():
    return ocr_vision.extract_text(
      pil_img,
      use_recognize=use_recognize,
      allowlist=allowlist,
      threshold=threshold,
      settings=_vision_settings(),
    )

  result, _meta = _dispatch("text", ocr_surface, ocr_backend, _easy, _vision)
  if getattr(config, "VERBOSE_OCR", False):
    info(f"[OCR] text: {result} (allowlist={allowlist}, threshold={threshold})")
  return result


def extract_number(
  pil_img,
  allowlist="0123456789",
  threshold=0.8,
  *,
  ocr_surface: Optional[str] = None,
  ocr_backend: Optional[str] = None,
):
  def _easy():
    return ocr_easyocr.extract_number(pil_img, allowlist=allowlist, threshold=threshold)

  def _vision():
    return ocr_vision.extract_number(
      pil_img, allowlist=allowlist, threshold=threshold, settings=_vision_settings()
    )

  result, _meta = _dispatch("number", ocr_surface, ocr_backend, _easy, _vision)
  if getattr(config, "VERBOSE_OCR", False):
    info(f"[OCR] number: {result} (allowlist={allowlist}, threshold={threshold})")
  return result


def extract_allowed_text(
  pil_img,
  allowlist="0123456789",
  *,
  ocr_surface: Optional[str] = None,
  ocr_backend: Optional[str] = None,
):
  def _easy():
    return ocr_easyocr.extract_allowed_text(pil_img, allowlist=allowlist)

  def _vision():
    return ocr_vision.extract_allowed_text(
      pil_img, allowlist=allowlist, settings=_vision_settings()
    )

  result, _meta = _dispatch("allowed_text", ocr_surface, ocr_backend, _easy, _vision)
  if getattr(config, "VERBOSE_OCR", False):
    info(f"[OCR] allowed_text: {result} (allowlist={allowlist})")
  return result


def readtext_detailed(
  pil_img,
  *,
  ocr_surface: Optional[str] = None,
  ocr_backend: Optional[str] = None,
  allowlist: Optional[str] = None,
  threshold: Optional[float] = None,
  min_size: Optional[int] = None,
  canvas_size: Optional[int] = None,
  batch_size: Optional[int] = None,
  paragraph: bool = False,
  workers: int = 0,
  return_meta: bool = False,
):
  """Detailed OCR returning rows shaped like EasyOCR ``detail=1`` results.

  Each row is a ``(bbox_quad, text, confidence)`` tuple where ``bbox_quad`` is
  a 4-point pixel-space quad in top-left origin, matching the shape callers
  already expect from EasyOCR.

  Pass ``ocr_surface="<name>"`` to opt into route-based backend selection.
  Pass ``ocr_backend="vision"`` to force a backend for a single call.
  """

  def _easy():
    return ocr_easyocr.readtext_detailed(
      pil_img,
      allowlist=allowlist,
      threshold=threshold,
      min_size=min_size,
      canvas_size=canvas_size,
      batch_size=batch_size,
      paragraph=paragraph,
      workers=workers,
    )

  def _vision():
    return ocr_vision.readtext_detailed(
      pil_img,
      allowlist=allowlist,
      threshold=threshold,
      settings=_vision_settings(),
    )

  rows, meta = _dispatch("detailed", ocr_surface, ocr_backend, _easy, _vision)
  if return_meta:
    return rows, meta
  return rows


# ---------------------------------------------------------------------------
# Backward-compat shims
# ---------------------------------------------------------------------------


def get_reader():
  """Return the EasyOCR reader, initializing on first call.

  Kept for legacy callers. New code should prefer ``readtext_detailed(...)``
  with ``ocr_surface=...`` so the routing layer can pick the backend.
  """
  return ocr_easyocr.get_reader()


def reload_reader():
  """Force-rebuild the EasyOCR reader (e.g. after config change)."""
  return ocr_easyocr.reload_reader()


def flush_gpu_cache():
  """Release MPS/CUDA memory pools held by PyTorch after OCR batches."""
  ocr_easyocr.flush_gpu_cache()


# Keep legacy module-level ``reader`` attribute writable for tests that
# previously poked ``core.ocr.reader`` directly.
def __getattr__(name):
  if name == "reader":
    return ocr_easyocr._reader
  raise AttributeError(f"module 'core.ocr' has no attribute {name!r}")


def sort_ocr_result(results):  # Preserved helper used by a few callers/tests.
  return ocr_easyocr.sort_ocr_result(results)
