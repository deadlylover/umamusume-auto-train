import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

import core.config as config
from core import ocr


def _dummy_image():
  return Image.fromarray(np.zeros((12, 32, 3), dtype=np.uint8))


class OCRRoutingDefaultChecks(unittest.TestCase):
  def test_global_default_prefers_vision(self):
    with patch.object(config, "OCR_BACKEND", "vision", create=True):
      backend, reason = ocr.resolve_backend(None, None)
    self.assertEqual(backend, "vision")
    self.assertEqual(reason, "global_default")

  def test_explicit_easyocr_override_still_works(self):
    with patch.object(config, "OCR_BACKEND", "vision", create=True), patch(
      "core.ocr.ocr_easyocr.extract_text",
      return_value="easy-path",
    ) as easy_mock, patch(
      "core.ocr.ocr_vision.extract_text",
      return_value="vision-path",
    ) as vision_mock:
      result = ocr.extract_text(_dummy_image(), ocr_backend="easyocr")

    self.assertEqual(result, "easy-path")
    easy_mock.assert_called_once()
    vision_mock.assert_not_called()

  def test_vision_unavailable_falls_back_to_easyocr(self):
    with patch.object(config, "OCR_BACKEND", "vision", create=True), patch(
      "core.ocr.ocr_vision.is_available",
      return_value=False,
    ), patch(
      "core.ocr.ocr_vision.last_import_error",
      return_value="vision_unavailable_for_test",
    ), patch(
      "core.ocr.ocr_easyocr.extract_text",
      return_value="easy-fallback",
    ) as easy_mock:
      result = ocr.extract_text(_dummy_image())
      meta = ocr.get_last_ocr_meta()

    self.assertEqual(result, "easy-fallback")
    easy_mock.assert_called_once()
    self.assertEqual(meta.get("requested_backend"), "vision")
    self.assertEqual(meta.get("backend"), "easyocr")
    self.assertTrue(meta.get("fallback"))
    self.assertEqual(meta.get("fallback_error"), "vision_unavailable_for_test")


if __name__ == "__main__":
  unittest.main()
