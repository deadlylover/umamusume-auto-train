from PIL import Image
import numpy as np
import re
from utils.log import debug, info, warning
import core.config as config


def _easyocr_device_preference():
  preference = str(getattr(config, "EASYOCR_DEVICE", "auto") or "auto").strip().lower()
  if preference not in ("auto", "cpu", "gpu"):
    warning(f"[OCR] Unknown easyocr_device='{preference}', defaulting to auto.")
    return "auto"
  return preference


def _easyocr_gpu_supported():
  try:
    import torch
  except Exception:
    return False, "torch_unavailable"

  if torch.cuda.is_available():
    return True, "cuda"

  mps_backend = getattr(torch.backends, "mps", None)
  if mps_backend is not None and mps_backend.is_available():
    return True, "mps"

  return False, "cpu"


def _build_easyocr_reader():
  import easyocr
  preference = _easyocr_device_preference()
  gpu_supported, detected_backend = _easyocr_gpu_supported()

  if preference == "cpu":
    use_gpu = False
    backend = "cpu_forced"
  elif preference == "gpu":
    use_gpu = True
    backend = detected_backend if gpu_supported else "gpu_forced"
  else:
    use_gpu = bool(gpu_supported)
    backend = detected_backend

  try:
    reader_obj = easyocr.Reader(["en"], gpu=use_gpu, verbose=False)
    info(
      f"[OCR] EasyOCR initialized on {getattr(reader_obj, 'device', 'unknown')} "
      f"(backend={backend}, preference={preference})."
    )
    return reader_obj
  except Exception as exc:
    if use_gpu:
      warning(f"[OCR] EasyOCR GPU init failed on {backend} ({exc}); falling back to CPU.")
    reader_obj = easyocr.Reader(["en"], gpu=False, verbose=False)
    info(
      f"[OCR] EasyOCR initialized on {getattr(reader_obj, 'device', 'unknown')} "
      f"(fallback=cpu, preference={preference})."
    )
    return reader_obj


reader = None


def get_reader():
  """Return the EasyOCR reader, initializing on first call."""
  global reader
  if reader is None:
    reader = _build_easyocr_reader()
  return reader


def reload_reader():
  """Force-rebuild the EasyOCR reader (e.g. after config change)."""
  global reader
  reader = _build_easyocr_reader()
  return reader


def flush_gpu_cache():
  """Release MPS/CUDA memory pools held by PyTorch after OCR batches.

  On Apple Silicon, the MPS backend keeps multi-GB memory pools mapped into
  the process address space even when idle.  Calling this after OCR-heavy
  phases (state collection, training scan, skill scan) lets the OS reclaim
  those pages so the process footprint stays closer to real RSS.
  """
  if reader is None:
    return
  try:
    import torch
  except Exception:
    return
  mps = getattr(torch.backends, "mps", None)
  if mps is not None and mps.is_available() and hasattr(torch.mps, "empty_cache"):
    torch.mps.empty_cache()
  elif torch.cuda.is_available():
    torch.cuda.empty_cache()


def _log_ocr(tag, text, allowlist, threshold):
  if getattr(config, "VERBOSE_OCR", False):
    info(f"[OCR] {tag}: {text} (allowlist={allowlist}, threshold={threshold})")

def extract_text(pil_img: Image.Image, use_recognize=False, allowlist=None, threshold=None) -> str:
  img_np = np.array(pil_img)
  if allowlist is None:
    allowlist = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-!.,'#? "
  r = get_reader()
  if use_recognize:
    if threshold is not None:
      result = r.recognize(img_np, allowlist=allowlist, text_threshold=threshold)
    else:
      result = r.recognize(img_np, allowlist=allowlist)
  else:
    if threshold is not None:
      result = r.readtext(img_np, allowlist=allowlist, text_threshold=threshold)
    else:
      result = r.readtext(img_np, allowlist=allowlist)
  texts = sort_ocr_result(result)
  _log_ocr("text", texts, allowlist, threshold)
  return texts

def extract_number(pil_img: Image.Image, allowlist="0123456789", threshold=0.8) -> int:
  img_np = np.array(pil_img)
  result = get_reader().readtext(img_np, allowlist=allowlist, text_threshold=threshold)
  texts = [item[1] for item in sorted(result, key=lambda x: x[0][0][0])]
  joined_text = "".join(texts)

  digits = re.sub(r"[^\d]", "", joined_text)

  if digits:
    _log_ocr("number", digits, allowlist, threshold)
    return int(digits)
  _log_ocr("number", -1, allowlist, threshold)
  return -1

def extract_allowed_text(pil_img: Image.Image, allowlist="0123456789") -> int:
  img_np = np.array(pil_img)
  result = get_reader().readtext(img_np, allowlist=allowlist)
  texts = [item[1] for item in sorted(result, key=lambda x: x[0][0][0])]
  output = " ".join(texts)
  _log_ocr("allowed_text", output, allowlist, None)
  return output

def sort_ocr_result(results):
  sorted_results = sorted(results, key=lambda x: x[0][0][1])
  if len(sorted_results) == 0:
    return ""
  previous_item = sorted_results[0]

  rows = [[]]
  row_number = 0
  for item in sorted_results:
    if item == previous_item:
      rows[row_number].append(item)
      continue
    tolerance = abs(previous_item[0][0][1] - previous_item[0][2][1]) * 0.6
    if item[0][0][1] < (previous_item[0][0][1] + tolerance) and item[0][0][1] > (previous_item[0][0][1] - tolerance):
      rows[row_number].append(item)
    else:
      row_number += 1
      rows.append([])
      rows[row_number].append(item)
    previous_item = item

  final_text = ""
  for row in rows:
    sorted_row = sorted(row, key=lambda x: x[0][0][0])
    text = " ".join([item[1] for item in sorted_row])
    final_text += text + " "
  final_text = re.sub(r"\s+", " ", final_text).strip()
  return final_text.strip()
