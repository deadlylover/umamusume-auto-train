"""Offline benchmark: EasyOCR vs Apple Vision on the skill-name-band route.

Replays a saved skill-scan capture session and runs the exact same
``_extract_ocr_rows_from_name_band`` path the live scanner uses, once with
the ``skill_name_band`` OCR surface routed to EasyOCR and once routed to
Apple Vision. Captures per-frame OCR rows, timings, and routing metadata,
then writes machine-readable results under ``logs/runtime_debug/``.

The benchmark is fully offline:

- Loads frames from disk; never screenshots the game window.
- Never clicks, scrolls, focuses, or drives the bot.
- Only mutates config **in-process** — never writes to ``config.json``.

Usage:

    .venv/bin/python checks/skill_ocr_backend_benchmark.py \\
        --capture-dir logs/runtime_debug/skill_benchmark_capture_20260417_105248

``--capture-dir`` can point at any saved session that follows the same
``manifest.json`` + ``frames/`` layout, so the benchmark is easy to retarget.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
  sys.path.insert(0, str(REPO_ROOT))

import core.state  # noqa: F401 — resolves a circular import for window_focus
import core.config as config
import utils.constants as constants
from core import ocr as ocr_facade
from core import ocr_easyocr, ocr_vision
from core import skill_scanner
from core.platform.window_focus import apply_configured_recognition_geometry


DEFAULT_CAPTURE_DIR = REPO_ROOT / "logs" / "runtime_debug" / "skill_benchmark_capture_20260417_105248"
DEFAULT_SHORTLIST: Tuple[str, ...] = (
  "Angling and Scheming",
  "Corner Adept \u25cb",
  "Corner Recovery \u25cb",
  "Swinging Maestro",
  "Professor of Curvature",
)


def _parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description="Offline EasyOCR vs Apple Vision benchmark on a saved skill-scan capture."
  )
  parser.add_argument(
    "--capture-dir",
    type=Path,
    default=DEFAULT_CAPTURE_DIR,
    help="Saved capture session directory (default: %(default)s).",
  )
  parser.add_argument(
    "--output-root",
    type=Path,
    default=REPO_ROOT / "logs" / "runtime_debug",
    help="Where to write the benchmark output folder (default: %(default)s).",
  )
  parser.add_argument(
    "--backends",
    nargs="+",
    default=["easyocr", "vision"],
    help="Backends to compare (default: easyocr vision).",
  )
  parser.add_argument(
    "--include-dim-pass",
    action=argparse.BooleanOptionalAction,
    default=True,
    help="Include the dim-text OCR pass just like the live scanner does.",
  )
  parser.add_argument(
    "--shortlist",
    nargs="*",
    default=list(DEFAULT_SHORTLIST),
    help="Skill shortlist used for matched_names computation.",
  )
  return parser.parse_args()


def _load_manifest(capture_dir: Path) -> List[Dict[str, Any]]:
  manifest_path = capture_dir / "manifest.json"
  if not manifest_path.is_file():
    raise FileNotFoundError(f"manifest.json not found under {capture_dir}")
  with open(manifest_path, "r", encoding="utf-8") as fh:
    manifest = json.load(fh)
  frames = list(manifest.get("frames") or [])
  frames.sort(key=lambda item: int(item.get("index", 0)))
  if not frames:
    raise RuntimeError("manifest has no frames to replay")
  return frames


def _resolve_frame_path(capture_dir: Path, entry: Dict[str, Any]) -> Path:
  raw_path = entry.get("image_path") or ""
  if not raw_path:
    raise RuntimeError(f"Frame entry is missing image_path: {entry}")
  candidate = Path(raw_path)
  if not candidate.is_absolute():
    # image_path is stored relative to repo root in the manifest.
    candidate = REPO_ROOT / candidate
  if candidate.is_file():
    return candidate
  # Fall back to resolving by basename inside the current capture dir.
  fallback = capture_dir / "frames" / Path(raw_path).name
  if fallback.is_file():
    return fallback
  raise FileNotFoundError(f"Cannot find saved frame: {raw_path}")


def _load_rgb(frame_path: Path):
  bgr = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
  if bgr is None:
    raise RuntimeError(f"cv2.imread returned None for {frame_path}")
  return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _set_route(backend: str) -> None:
  """Install per-surface routing for ``skill_name_band`` without mutating disk config."""

  config.OCR_BACKEND = "easyocr"
  config.OCR_ROUTE_OVERRIDES = {"skill_name_band": backend}


def _reset_skill_match_cache() -> None:
  with skill_scanner._SKILL_MATCH_CACHE_LOCK:
    skill_scanner._SKILL_MATCH_CACHE.clear()


def _rss_mb() -> Optional[float]:
  """Best-effort RSS snapshot in MB. Returns ``None`` when unavailable."""
  try:
    import resource  # stdlib on POSIX
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS, kilobytes on Linux; assume macOS here.
    if sys.platform == "darwin":
      return round(usage / (1024 * 1024), 2)
    return round(usage / 1024, 2)
  except Exception:
    return None


def _median(values: List[float]) -> Optional[float]:
  return round(statistics.median(values), 4) if values else None


def _mean(values: List[float]) -> Optional[float]:
  return round(statistics.fmean(values), 4) if values else None


def _normalize_row_texts(rows: List[Dict[str, Any]]) -> List[str]:
  texts = []
  for row in rows:
    text = row.get("text_normalized") or row.get("text_raw") or ""
    text = str(text).strip()
    if text:
      texts.append(text)
  return sorted(set(texts))


def _matched_names_for_frame(rows: List[Dict[str, Any]], shortlist: Tuple[str, ...]) -> List[str]:
  matches = skill_scanner._match_skill_rows_to_shortlist(rows, shortlist)
  names = [m.get("match_name") for m in matches if m.get("match_name")]
  return sorted(set(names))


def _run_backend_pass(
  frames: List[Dict[str, Any]],
  capture_dir: Path,
  backend: str,
  include_dim_pass: bool,
  shortlist: Tuple[str, ...],
  out_dir: Path,
) -> Dict[str, Any]:
  print(f"[benchmark] === backend={backend} include_dim_pass={include_dim_pass} ===")
  available = backend != "vision" or ocr_vision.is_available()
  if not available:
    return {
      "backend": backend,
      "skipped": True,
      "reason": ocr_vision.last_import_error() or "vision_unavailable",
    }

  _set_route(backend)
  _reset_skill_match_cache()

  # Warm attempts are intentionally NOT performed — first-frame cost is
  # reported separately so the benchmark surfaces cold-start behavior.

  rss_before = _rss_mb()

  per_frame: List[Dict[str, Any]] = []
  first_frame_wall: Optional[float] = None
  warmed_walls: List[float] = []
  total_wall: float = 0.0
  unique_texts: set = set()
  unique_matches: set = set()

  for idx, entry in enumerate(frames):
    frame_path = _resolve_frame_path(capture_dir, entry)
    frame_rgb = _load_rgb(frame_path)

    t0 = time.perf_counter()
    rows = skill_scanner._extract_ocr_rows_from_name_band(
      frame_rgb, include_dim_pass=include_dim_pass
    )
    wall = time.perf_counter() - t0
    total_wall += wall
    if idx == 0:
      first_frame_wall = wall
    else:
      warmed_walls.append(wall)

    meta = ocr_facade.get_last_ocr_meta()
    normalized_texts = _normalize_row_texts(rows)
    matched_names = _matched_names_for_frame(rows, shortlist)
    unique_texts.update(normalized_texts)
    unique_matches.update(matched_names)

    per_frame.append({
      "index": entry.get("index"),
      "image": frame_path.name,
      "wall_s": round(wall, 4),
      "rows_detected": len(rows),
      "normalized_texts": normalized_texts,
      "matched_names": matched_names,
      "ocr_meta": meta,
      "scrollbar_ratio": entry.get("scrollbar_ratio"),
      "manifest_ocr_s": (entry.get("timing") or {}).get("ocr"),
    })

  rss_after = _rss_mb()

  aggregate = {
    "backend": backend,
    "skipped": False,
    "frames": len(per_frame),
    "include_dim_pass": include_dim_pass,
    "total_wall_s": round(total_wall, 4),
    "first_frame_wall_s": round(first_frame_wall, 4) if first_frame_wall is not None else None,
    "warmed_mean_wall_s": _mean(warmed_walls),
    "warmed_median_wall_s": _median(warmed_walls),
    "overall_mean_wall_s": _mean([pf["wall_s"] for pf in per_frame]),
    "overall_median_wall_s": _median([pf["wall_s"] for pf in per_frame]),
    "unique_normalized_texts": sorted(unique_texts),
    "unique_matched_names": sorted(unique_matches),
    "rss_mb_before": rss_before,
    "rss_mb_after": rss_after,
    "rss_mb_delta": (
      round(rss_after - rss_before, 2)
      if (rss_before is not None and rss_after is not None)
      else None
    ),
  }

  per_frame_path = out_dir / f"per_frame_{backend}.json"
  per_frame_path.write_text(
    json.dumps(per_frame, indent=2, default=str), encoding="utf-8"
  )
  print(
    f"[benchmark] backend={backend} frames={aggregate['frames']} "
    f"total_wall_s={aggregate['total_wall_s']} "
    f"first_frame_wall_s={aggregate['first_frame_wall_s']} "
    f"warmed_mean_wall_s={aggregate['warmed_mean_wall_s']}"
  )
  return aggregate


def _diff_text_sets(a: List[str], b: List[str]) -> Dict[str, List[str]]:
  set_a = set(a or [])
  set_b = set(b or [])
  return {
    "only_in_a": sorted(set_a - set_b),
    "only_in_b": sorted(set_b - set_a),
    "shared_count": len(set_a & set_b),
  }


def _build_text_diff(
  aggregates: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
  ok = [a for a in aggregates if not a.get("skipped")]
  if len(ok) < 2:
    return None
  a, b = ok[0], ok[1]
  texts_diff = _diff_text_sets(a.get("unique_normalized_texts", []), b.get("unique_normalized_texts", []))
  names_diff = _diff_text_sets(a.get("unique_matched_names", []), b.get("unique_matched_names", []))
  return {
    "a_backend": a["backend"],
    "b_backend": b["backend"],
    "unique_normalized_texts": texts_diff,
    "unique_matched_names": names_diff,
  }


def main() -> int:
  args = _parse_args()

  capture_dir: Path = args.capture_dir.resolve()
  if not capture_dir.is_dir():
    print(f"[benchmark] capture-dir does not exist: {capture_dir}", file=sys.stderr)
    return 2

  # Apply configured recognition geometry so SKILL_NAME_BAND_BBOX aligns
  # with the frame shape on disk (matches the runtime scanner path).
  config.reload_config(print_config=False)
  apply_configured_recognition_geometry()

  frames = _load_manifest(capture_dir)
  shortlist = tuple(args.shortlist or DEFAULT_SHORTLIST)

  stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
  out_dir: Path = args.output_root / f"skill_ocr_backend_benchmark_{stamp}"
  out_dir.mkdir(parents=True, exist_ok=True)

  aggregates: List[Dict[str, Any]] = []
  for backend in args.backends:
    aggregate = _run_backend_pass(
      frames=frames,
      capture_dir=capture_dir,
      backend=backend,
      include_dim_pass=args.include_dim_pass,
      shortlist=shortlist,
      out_dir=out_dir,
    )
    aggregates.append(aggregate)

  summary = {
    "capture_dir": str(capture_dir),
    "frame_count": len(frames),
    "shortlist": list(shortlist),
    "include_dim_pass": args.include_dim_pass,
    "game_window_bbox": list(constants.GAME_WINDOW_BBOX),
    "skill_name_band_bbox": list(constants.SKILL_NAME_BAND_BBOX),
    "vision_available": ocr_vision.is_available(),
    "vision_import_error": ocr_vision.last_import_error(),
    "easyocr_available": ocr_easyocr.is_available(),
    "backends": aggregates,
    "diff": _build_text_diff(aggregates),
    "environment": {
      "python": sys.version.split()[0],
      "platform": sys.platform,
      "pid": os.getpid(),
    },
    "generated_at": datetime.now().isoformat(timespec="seconds"),
  }

  summary_path = out_dir / "summary.json"
  summary_path.write_text(
    json.dumps(summary, indent=2, default=str), encoding="utf-8"
  )
  print(f"[benchmark] Wrote summary -> {summary_path}")
  print(f"[benchmark] Per-frame results written under {out_dir}")

  return 0


if __name__ == "__main__":
  sys.exit(main())
