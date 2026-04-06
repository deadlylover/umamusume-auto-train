import easyocr
from PIL import Image
import numpy as np
import re
from utils.log import debug, info, warning
import core.config as config


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
  gpu_supported, backend = _easyocr_gpu_supported()
  try:
    reader_obj = easyocr.Reader(["en"], gpu=bool(gpu_supported), verbose=False)
    info(f"[OCR] EasyOCR initialized on {getattr(reader_obj, 'device', 'unknown')} (backend={backend}).")
    return reader_obj
  except Exception as exc:
    if gpu_supported:
      warning(f"[OCR] EasyOCR GPU init failed on {backend} ({exc}); falling back to CPU.")
    reader_obj = easyocr.Reader(["en"], gpu=False, verbose=False)
    info(f"[OCR] EasyOCR initialized on {getattr(reader_obj, 'device', 'unknown')} (fallback=cpu).")
    return reader_obj


reader = _build_easyocr_reader()

def _log_ocr(tag, text, allowlist, threshold):
  if getattr(config, "VERBOSE_OCR", False):
    info(f"[OCR] {tag}: {text} (allowlist={allowlist}, threshold={threshold})")

def extract_text(pil_img: Image.Image, use_recognize=False, allowlist=None, threshold=None) -> str:
  img_np = np.array(pil_img)
  if allowlist is None:
    allowlist = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-!.,'#? "
  if use_recognize:
    if threshold is not None:
      result = reader.recognize(img_np, allowlist=allowlist, text_threshold=threshold)
    else:
      result = reader.recognize(img_np, allowlist=allowlist)
  else:
    if threshold is not None:
      result = reader.readtext(img_np, allowlist=allowlist, text_threshold=threshold)
    else:
      result = reader.readtext(img_np, allowlist=allowlist)
  texts = sort_ocr_result(result)
  _log_ocr("text", texts, allowlist, threshold)
  return texts

def extract_number(pil_img: Image.Image, allowlist="0123456789", threshold=0.8) -> int:
  img_np = np.array(pil_img)
  result = reader.readtext(img_np, allowlist=allowlist, text_threshold=threshold)
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
  result = reader.readtext(img_np, allowlist=allowlist)
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
