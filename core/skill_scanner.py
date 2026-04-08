# core/skill_scanner.py
# Skill list scrollbar + OCR purchase pipeline (Phase 1).
# Scans the skill page using scrollbar-driven buffered capture,
# OCRs skill names from the name band, matches against a configured
# shortlist, pairs to increment buttons, and detects confirm state.

import utils.constants as constants
import utils.device_action_wrapper as device_action
import core.config as config
from utils.log import info, warning, debug
from utils.tools import get_secs, sleep
import core.bot as bot
from time import time as _time
from queue import Queue
from functools import lru_cache
import numpy as np
import threading
import re
import cv2
import json
import Levenshtein
from pathlib import Path

_SKILL_RUNTIME_DEBUG_LOCK = threading.Lock()
_SKILL_RUNTIME_DEBUG_CAPTURE_ENABLED = False  # Re-enable this if you need buffered skill-scan frames written under logs/runtime_debug again.

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Scrollbar detection tuning — mirrors the Trackblazer shop scrollbar settings.
_SCROLLBAR_WINDOW_HALF_WIDTH = 3
_SCROLLBAR_DARKNESS_DELTA = 18.0
_SCROLLBAR_MIN_SEGMENT_HEIGHT = 8
_SCROLLBAR_MIN_CONTRAST = 18.0
_SCROLLBAR_NON_SCROLLABLE_HEIGHT_RATIO = 0.85
_SCROLLBAR_EDGE_TOLERANCE = 12
_SCROLLBAR_DRAG_END_PADDING = 10
_SCROLLBAR_RESET_DURATION_SECONDS = 0.5
_SCROLLBAR_SEEK_DURATION_SECONDS = 0.4

# Buffered capture pipeline tuning — continuous drag is the primary scan path.
_SCROLLBAR_DRAG_DURATION_SECONDS = 6.0   # Slow drag for dense captures
_SCROLLBAR_FRAME_INTERVAL_SECONDS = 0.18  # ~5-6 fps effective capture
_SCROLLBAR_ANALYSIS_WORKERS = 3

# Seek-back tuning — used when returning to a target frame's scrollbar ratio.
_SCROLLBAR_SEEKBACK_SETTLE_SECONDS = 0.35  # Settle after seek-back drag
_SCROLLBAR_REACQUIRE_SETTLE_SECONDS = 0.2  # Settle after reacquire nudge
_SCROLLBAR_SEEKBACK_BUFFER_RATIO = 0.05
_SCROLLBAR_BOTTOM_COMPLETION_PASSES = 2
_SCROLLBAR_BOTTOM_COMPLETION_DRAG_DURATION_SECONDS = 0.8

# OCR matching tuning.
_EXACT_MATCH_THRESHOLD = 0.92     # Levenshtein ratio for "exact" match
_FUZZY_MATCH_THRESHOLD = 0.75     # Levenshtein ratio for fuzzy fallback
_FUZZY_MATCH_MIN_LENGTH = 4       # Minimum skill name length for fuzzy
_TOKEN_MERGE_FALLBACK_THRESHOLD = 0.73
_TOKEN_MERGE_MIN_SHARED = 2
_TOKEN_MERGE_MIN_COVERAGE = 0.66
_TOKEN_MERGE_MAX_ROW_TOKENS = 6

# Increment button pairing.
_INCREMENT_MATCH_THRESHOLD = 0.65
_INCREMENT_Y_TOLERANCE = 50       # Looser than shop — skill cards are taller
_INCREMENT_FALLBACK_Y_TOLERANCE = 90
_INCREMENT_TEMPLATE = "assets/buttons/skill_increment.png"
_INVERSE_GLOBAL_SCALE = 1.0 / device_action.GLOBAL_TEMPLATE_SCALING

# "Obtained" badge detection — used to suppress increment pairing for already-learned skills.
_OBTAINED_TEMPLATE = "assets/custom/skill_obtained.png"
_OBTAINED_MATCH_THRESHOLD = 0.85
_OBTAINED_Y_TOLERANCE = 55  # Similar to increment Y tolerance
_OBTAINED_RECHECK_MATCH_THRESHOLD = 0.8
_OBTAINED_RECHECK_Y_TOLERANCE = 65
_OBTAINED_RECHECK_THRESHOLDS = (_OBTAINED_RECHECK_MATCH_THRESHOLD, 0.76)
_OBTAINED_TEXT_Y_TOLERANCE = 72
_OBTAINED_TEXT_MIN_SIMILARITY = 0.72

# Confirm detection.
_CONFIRM_TEMPLATE = "assets/buttons/confirm_btn.png"
_CONFIRM_THRESHOLD = 0.8

# Learn / finalize detection.
_LEARN_TEMPLATE = "assets/buttons/learn_btn.png"
_LEARN_THRESHOLD = 0.8
_CLOSE_TEMPLATE = "assets/buttons/close_btn.png"
_CLOSE_THRESHOLD = 0.8
_TRACKBLAZER_SKILLS_LEARNED_THRESHOLD = 0.8
_TRACKBLAZER_SKILLS_LEARNED_CLOSE_THRESHOLD = 0.8
_TRACKBLAZER_SKILLS_LEARNED_CLOSE_DELAY_SECONDS = 0.5
_LEARN_SETTLE_SECONDS = 0.5
_CLOSE_SETTLE_SECONDS_POST_LEARN = 0.5

# Skills page open/close.
_SKILLS_BTN_TEMPLATE = "assets/buttons/skills_btn.png"
_BACK_BTN_TEMPLATE = "assets/buttons/back_btn.png"
_EXIT_NO_LEARN_TEMPLATE = "assets/buttons/skill_confirm_exit_no_learn.png"
_EXIT_NO_LEARN_THRESHOLD = 0.8
_OPEN_SETTLE_SECONDS = 1.0
_CLOSE_SETTLE_SECONDS = 0.5
_EXIT_DIALOG_SETTLE_SECONDS = 0.5

# OCR tuning.
_SKILL_OCR_CANVAS_SIZE = 1600
_SKILL_OCR_MIN_SIZE = 12
_SKILL_OCR_GPU_BATCH_SIZE = 8
_SKILL_OCR_CPU_BATCH_SIZE = 1
_SKILL_OCR_DIM_ENABLE = True
_SKILL_OCR_DIM_SHARPEN = True
_SKILL_OCR_DIM_CLAHE_CLIP = 2.2
_SKILL_OCR_DIM_CONTRAST_ALPHA = 1.45
_SKILL_OCR_DIM_CONTRAST_BETA = 10
_SKILL_OCR_DIM_ADAPTIVE_BLOCK = 31
_SKILL_OCR_DIM_ADAPTIVE_C = 7
_SKILL_OCR_DIM_ADAPTIVE_C_ALT = 11
_SKILL_OCR_STITCH_Y_TOLERANCE = 24
_SKILL_OCR_STITCH_X_GAP = 190
_SKILL_OCR_STITCH_MAX_TEXT_LENGTH = 40


_SKILL_SHORTLIST_CACHE = {}
_SKILL_MATCH_CACHE = {}
_SKILL_MATCH_CACHE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Screenshot helpers
# ---------------------------------------------------------------------------

def _smoothed_lane_scores(col_mean, window_size=5):
    """Smooth column darkness without introducing zero-padded edge bias."""
    if col_mean.shape[0] < window_size:
        return col_mean
    pad = window_size // 2
    padded = np.pad(col_mean, pad, mode="edge")
    kernel = np.ones(window_size, dtype=np.float32) / float(window_size)
    return np.convolve(padded, kernel, mode="valid")

def _skill_ui_region():
    """The base screenshot region for skill page captures."""
    return constants.GAME_WINDOW_BBOX


def _capture_live_skill_screenshot():
    """Force a fresh screenshot of the game window."""
    device_action.flush_screenshot_cache()
    return device_action.screenshot(region_ltrb=_skill_ui_region())


def _ensure_skill_runtime_debug_dir(session_name):
    if not _SKILL_RUNTIME_DEBUG_CAPTURE_ENABLED:
        return None
    runtime_debug_dir = Path("logs/runtime_debug")
    runtime_debug_dir.mkdir(parents=True, exist_ok=True)
    safe_session = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in str(session_name or "skill_scan"))
    session_dir = runtime_debug_dir / safe_session
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "frames").mkdir(parents=True, exist_ok=True)
    return session_dir


def _append_skill_runtime_debug_manifest(session_dir, entry):
    manifest_path = Path(session_dir) / "manifest.json"
    with _SKILL_RUNTIME_DEBUG_LOCK:
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                manifest = {}
        else:
            manifest = {}
        frames = list(manifest.get("frames") or [])
        frames.append(entry)
        frames.sort(key=lambda item: int(item.get("index", 0)))
        manifest["frames"] = frames
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")


def _save_skill_runtime_debug_frame(session_dir, stem, screenshot, frame_summary=None):
    if screenshot is None or getattr(screenshot, "size", 0) == 0 or not session_dir:
        return ""
    if not _SKILL_RUNTIME_DEBUG_CAPTURE_ENABLED:
        return ""
    safe_stem = "".join(ch if ch.isalnum() or ch in ("_", "-", ".") else "_" for ch in str(stem))
    image_path = Path(session_dir) / "frames" / f"{safe_stem}.png"
    screenshot_bgr = cv2.cvtColor(screenshot, cv2.COLOR_RGB2BGR)
    cv2.imwrite(str(image_path), screenshot_bgr)
    if frame_summary is not None:
        manifest_entry = dict(frame_summary)
        manifest_entry["image_path"] = str(image_path)
        _append_skill_runtime_debug_manifest(session_dir, manifest_entry)
    return str(image_path)


def _crop_absolute_bbox(screenshot, target_bbox, base_region_ltrb=None):
    """Crop an absolute bbox from a screenshot taken over base_region_ltrb."""
    if screenshot is None or getattr(screenshot, "size", 0) == 0:
        return None
    base_left, base_top, _base_right, _base_bottom = base_region_ltrb or _skill_ui_region()
    target_left, target_top, target_right, target_bottom = [int(v) for v in target_bbox]
    rel_left = max(0, int(target_left - base_left))
    rel_top = max(0, int(target_top - base_top))
    rel_right = min(int(screenshot.shape[1]), int(target_right - base_left))
    rel_bottom = min(int(screenshot.shape[0]), int(target_bottom - base_top))
    if rel_right <= rel_left or rel_bottom <= rel_top:
        return None
    return screenshot[rel_top:rel_bottom, rel_left:rel_right].copy()


# ---------------------------------------------------------------------------
# Scrollbar detection
# ---------------------------------------------------------------------------

def inspect_skill_scrollbar(screenshot=None):
    """Detect the skill list scrollbar thumb and current scroll position.

    Uses the same column-darkness algorithm as the Trackblazer shop scrollbar.
    """
    ui_region = _skill_ui_region()
    screenshot = screenshot if screenshot is not None else _capture_live_skill_screenshot()
    crop = _crop_absolute_bbox(
        screenshot,
        constants.SKILL_SCROLLBAR_BBOX,
        base_region_ltrb=ui_region,
    )
    result = {
        "detected": False,
        "scrollable": False,
        "is_at_top": False,
        "is_at_bottom": False,
        "bbox": [int(v) for v in constants.SKILL_SCROLLBAR_BBOX],
        "track_center_x": None,
        "thumb_rect": None,
        "thumb_center": None,
        "thumb_height": 0,
        "travel_pixels": 0,
        "position_ratio": None,
        "contrast": 0.0,
    }
    if crop is None or getattr(crop, "size", 0) == 0:
        return result

    if len(crop.shape) == 3:
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    else:
        gray = np.asarray(crop)
    if gray.size == 0 or gray.shape[0] <= 0 or gray.shape[1] <= 0:
        return result

    # Find the darkest vertical lane (the scrollbar track).
    col_mean = gray.mean(axis=0).astype(np.float32, copy=False)
    lane_scores = _smoothed_lane_scores(col_mean, window_size=5)
    track_center_x = int(np.argmin(lane_scores))
    left = max(0, track_center_x - _SCROLLBAR_WINDOW_HALF_WIDTH)
    right = min(gray.shape[1], track_center_x + _SCROLLBAR_WINDOW_HALF_WIDTH + 1)
    track = gray[:, left:right]
    row_mean = track.mean(axis=1)
    baseline = float(np.percentile(row_mean, 70))
    threshold = baseline - _SCROLLBAR_DARKNESS_DELTA
    mask = row_mean < threshold

    # Find dark segments (thumb candidates).
    padded = np.concatenate(([False], mask, [False]))
    transitions = np.flatnonzero(np.diff(padded.astype(np.int8)))
    segments = []
    for start_idx, end_idx in zip(transitions[0::2], transitions[1::2]):
        if end_idx - start_idx >= _SCROLLBAR_MIN_SEGMENT_HEIGHT:
            segments.append((start_idx, end_idx - 1, float(row_mean[start_idx:end_idx].mean())))
    if not segments:
        return result

    thumb_top, thumb_bottom, thumb_darkness = min(segments, key=lambda entry: entry[2])
    thumb_height = int(thumb_bottom - thumb_top + 1)
    contrast = float(max(0.0, baseline - thumb_darkness))
    if contrast < _SCROLLBAR_MIN_CONTRAST:
        return result

    track_height = int(gray.shape[0])
    travel_pixels = max(0, track_height - thumb_height)
    denominator = float(max(1, travel_pixels))
    position_ratio = min(1.0, max(0.0, float(thumb_top) / denominator))
    bbox_left, bbox_top, _bbox_right, _bbox_bottom = [int(v) for v in constants.SKILL_SCROLLBAR_BBOX]
    thumb_center_y = int(bbox_top + thumb_top + thumb_height // 2)
    track_center_abs_x = int(bbox_left + track_center_x)

    result.update({
        "detected": True,
        "scrollable": bool(thumb_height < int(track_height * _SCROLLBAR_NON_SCROLLABLE_HEIGHT_RATIO)),
        "is_at_top": bool(thumb_top <= _SCROLLBAR_EDGE_TOLERANCE),
        "is_at_bottom": bool((track_height - 1 - thumb_bottom) <= _SCROLLBAR_EDGE_TOLERANCE),
        "track_center_x": track_center_abs_x,
        "thumb_rect": [
            int(bbox_left + left),
            int(bbox_top + thumb_top),
            int(max(1, right - left)),
            int(thumb_height),
        ],
        "thumb_center": [int(track_center_abs_x), int(thumb_center_y)],
        "thumb_height": int(thumb_height),
        "travel_pixels": int(travel_pixels),
        "position_ratio": round(position_ratio, 4),
        "contrast": round(contrast, 2),
    })
    return result


# ---------------------------------------------------------------------------
# Scrollbar drag helpers
# ---------------------------------------------------------------------------

def _drag_skill_scrollbar(scrollbar_state, edge="top", duration=_SCROLLBAR_RESET_DURATION_SECONDS):
    """Drag the skill scrollbar thumb to the requested edge."""
    edge_name = str(edge or "top").strip().lower()
    if edge_name not in ("top", "bottom"):
        raise ValueError(f"Unsupported skill scrollbar edge: {edge}")
    thumb_center = (scrollbar_state or {}).get("thumb_center")
    bbox = (scrollbar_state or {}).get("bbox") or [int(v) for v in constants.SKILL_SCROLLBAR_BBOX]
    track_center_x = int((scrollbar_state or {}).get("track_center_x") or 0)
    if not thumb_center or track_center_x <= 0:
        return {
            "direction": f"scrollbar_{edge_name}",
            "start": None,
            "end": None,
            "duration": float(duration),
            "swiped": False,
        }
    start = (int(thumb_center[0]), int(thumb_center[1]))
    end_y = int(bbox[1] + 10) if edge_name == "top" else int(bbox[3] - _SCROLLBAR_DRAG_END_PADDING)
    end = (track_center_x, end_y)
    swiped = bool(device_action.swipe(
        start,
        end,
        duration=duration,
        text=f"Skill scrollbar drag to {edge_name}",
    ))
    return {
        "direction": f"scrollbar_{edge_name}",
        "start": [int(start[0]), int(start[1])],
        "end": [int(end[0]), int(end[1])],
        "duration": float(duration),
        "swiped": swiped,
    }


def _drag_skill_scrollbar_to_ratio(scrollbar_state, position_ratio, duration=_SCROLLBAR_SEEK_DURATION_SECONDS):
    """Drag the skill scrollbar thumb to an approximate position ratio.

    Used for seek-back when returning to a target found during the full scan.
    """
    thumb_center = (scrollbar_state or {}).get("thumb_center")
    bbox = (scrollbar_state or {}).get("bbox") or [int(v) for v in constants.SKILL_SCROLLBAR_BBOX]
    track_center_x = int((scrollbar_state or {}).get("track_center_x") or 0)
    thumb_height = int((scrollbar_state or {}).get("thumb_height") or 0)
    travel_pixels = int((scrollbar_state or {}).get("travel_pixels") or 0)
    if not thumb_center or track_center_x <= 0 or thumb_height <= 0:
        return {
            "direction": "scrollbar_ratio",
            "start": None,
            "end": None,
            "duration": float(duration),
            "settle_seconds": 0.0,
            "swiped": False,
            "target_ratio": None,
        }
    clamped_ratio = min(1.0, max(0.0, float(position_ratio or 0.0)))
    thumb_top_target = int(round(clamped_ratio * max(0, travel_pixels)))
    end_y = int(bbox[1] + thumb_top_target + max(1, thumb_height // 2))
    start = (int(thumb_center[0]), int(thumb_center[1]))
    end = (track_center_x, end_y)
    swiped = bool(device_action.swipe(
        start,
        end,
        duration=duration,
        text=f"Skill scrollbar seek ratio={clamped_ratio:.3f}",
    ))
    return {
        "direction": "scrollbar_ratio",
        "start": [int(start[0]), int(start[1])],
        "end": [int(end[0]), int(end[1])],
        "duration": float(duration),
        "settle_seconds": 0.0,
        "swiped": swiped,
        "target_ratio": round(clamped_ratio, 4),
    }


# ---------------------------------------------------------------------------
# Row signature for frame deduplication
# ---------------------------------------------------------------------------

def _compute_row_signature(ocr_rows):
    """Compute a lightweight signature from OCR rows to detect duplicate frames.

    Uses the sorted normalized texts so that identical visible rows produce
    the same signature regardless of OCR ordering noise.
    """
    if not ocr_rows:
        return ""
    texts = sorted(row.get("text_normalized", "") for row in ocr_rows if row.get("text_normalized"))
    return "|".join(texts)


# ---------------------------------------------------------------------------
# Live analysis index for waterfall execution
# ---------------------------------------------------------------------------

class _LiveAnalysisIndex:
    """Thread-safe accumulator for analyzed frames with candidate polling.

    Used by the post-drag waterfall execution model: seek-back can begin
    before all background OCR analysis has completed.  Workers append
    frames as they finish; callers poll or wait for specific candidates.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._frames = []
        self._done = threading.Event()
        self._signatures = set()

    def append(self, frame):
        with self._lock:
            self._frames.append(frame)
            sig = frame.get("row_signature", "")
            if sig:
                self._signatures.add(sig)

    def get_frames(self):
        with self._lock:
            return list(self._frames)

    def get_unique_signature_count(self):
        with self._lock:
            return len(self._signatures)

    def get_signatures(self):
        with self._lock:
            return set(self._signatures)

    def mark_done(self):
        self._done.set()

    def is_done(self):
        return self._done.is_set()

    def wait_done(self, timeout=None):
        return self._done.wait(timeout=timeout)

    def find_best_candidate(self, skill_name):
        return _choose_best_candidate(self.get_frames(), skill_name)

    def wait_for_candidate(self, skill_name, timeout=10.0):
        """Poll for a candidate until found, analysis done, or timeout."""
        deadline = _time() + timeout
        while True:
            candidate = self.find_best_candidate(skill_name)
            if candidate:
                return candidate
            if self._done.is_set():
                return None
            remaining = deadline - _time()
            if remaining <= 0:
                return None
            self._done.wait(timeout=min(0.15, remaining))


def _finalize_waterfall_summary(flow, live_index, timeout=10.0):
    """Wait for remaining analysis and populate frame summary in flow."""
    live_index.wait_done(timeout=timeout)
    all_frames = live_index.get_frames()
    flow["all_frames"] = _summarize_frames_for_flow(all_frames)
    flow["frame_signatures_seen"] = len(all_frames)
    flow["frame_signatures_unique"] = live_index.get_unique_signature_count()


# ---------------------------------------------------------------------------
# OCR extraction from the name band
# ---------------------------------------------------------------------------

_OCR_SYMBOL_VARIANTS = (
    ("○", " o "),
    ("◯", " o "),
    ("〇", " o "),
    ("●", " o "),
    ("◎", " o "),
    ("◉", " o "),
    ("•", " "),
    ("・", " "),
    ("’", "'"),
    ("“", "\""),
    ("”", "\""),
)
_SKILL_TOKEN_STOPWORDS = frozenset({"and", "for", "the", "with"})
_GENERIC_SKILL_TOKENS = frozenset({"corner", "corners", "curve", "curves", "turn", "turns", "straight", "straightaway", "straightaways"})
_TOKEN_CANONICAL_MAP = {
    "corners": "corner",
    "curves": "curve",
    "turns": "turn",
    "straightaways": "straightaway",
}
_CORNER_FAMILY_DISTANCE_TOKENS = frozenset({"short", "mile", "middle", "medium", "long"})
_CORNER_FAMILY_SPECIAL_TOKENS = frozenset({"adept", "recovery"})
_NAME_MATCH_METHOD_PRIORITY = {"exact": 3, "token_merge": 2, "fuzzy": 1}
_OCR_VARIANT_PRIORITY = {"normal": 3, "merged": 2, "dim": 1}
_ROW_GROUP_Y_BUCKET = 8.0
_ROW_GROUP_X_BUCKET = 28.0
_MEANINGFUL_NORMAL_CONFIDENCE = 0.45


def _name_match_method_rank(method):
    return int(_NAME_MATCH_METHOD_PRIORITY.get(method, 0))


def _ocr_variant_rank(variant):
    return int(_OCR_VARIANT_PRIORITY.get(variant, 0))


def _ocr_row_quality_key(row):
    confidence = float(row.get("confidence") or 0.0)
    normalized_len = len(str(row.get("text_normalized") or ""))
    return (
        confidence,
        _ocr_variant_rank(row.get("ocr_variant")),
        normalized_len,
    )


def _dedupe_ocr_rows(rows):
    """Dedupe OCR rows by nearby Y/X+text while keeping the strongest candidate."""
    deduped = {}
    for row in (rows or []):
        normalized = row.get("text_normalized") or ""
        if not normalized:
            continue
        crop_bbox = row.get("crop_bbox") or [0, 0, 0, 0]
        key = (
            normalized,
            int(round(float(row.get("abs_y_center") or 0.0) / 6.0)),
            int(round(float(crop_bbox[0]) / 26.0)),
        )
        current = deduped.get(key)
        if current is None or _ocr_row_quality_key(row) > _ocr_row_quality_key(current):
            deduped[key] = dict(row)
    return sorted(
        deduped.values(),
        key=lambda item: (
            float(item.get("abs_y_center") or 0.0),
            int((item.get("crop_bbox") or [0, 0, 0, 0])[0]),
        ),
    )


def _build_dim_text_variants(crop):
    """Create OCR-friendly variants for dim/greyed-out skill rows."""
    if crop is None or getattr(crop, "size", 0) == 0:
        return []
    if len(crop.shape) == 3:
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    else:
        gray = np.asarray(crop)
    if gray is None or getattr(gray, "size", 0) == 0:
        return []
    clahe = cv2.createCLAHE(clipLimit=_SKILL_OCR_DIM_CLAHE_CLIP, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    enhanced = cv2.convertScaleAbs(
        enhanced,
        alpha=_SKILL_OCR_DIM_CONTRAST_ALPHA,
        beta=_SKILL_OCR_DIM_CONTRAST_BETA,
    )
    if _SKILL_OCR_DIM_SHARPEN:
        blurred = cv2.GaussianBlur(enhanced, (0, 0), 1.1)
        enhanced = cv2.addWeighted(enhanced, 1.22, blurred, -0.22, 0)
    adaptive_mean = cv2.adaptiveThreshold(
        enhanced,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY,
        _SKILL_OCR_DIM_ADAPTIVE_BLOCK,
        _SKILL_OCR_DIM_ADAPTIVE_C,
    )
    adaptive_gaussian = cv2.adaptiveThreshold(
        enhanced,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        _SKILL_OCR_DIM_ADAPTIVE_BLOCK,
        _SKILL_OCR_DIM_ADAPTIVE_C_ALT,
    )
    return [enhanced, adaptive_mean, adaptive_gaussian]


@lru_cache(maxsize=4096)
def _extract_key_tokens(normalized_text):
    tokens = [tok for tok in str(normalized_text or "").split() if tok]
    if not tokens:
        return frozenset()
    key_tokens = [
        tok for tok in tokens
        if len(tok) >= 3 and not tok.isdigit() and tok not in _SKILL_TOKEN_STOPWORDS
    ]
    if len(key_tokens) < 2:
        key_tokens = [tok for tok in tokens if len(tok) >= 3 and not tok.isdigit()]
    if len(key_tokens) < 2:
        key_tokens = tokens
    return frozenset(key_tokens)


def _canonicalize_skill_tokens(tokens):
    canonical = []
    for token in (tokens or []):
        token = str(token or "").strip()
        if not token:
            continue
        canonical.append(_TOKEN_CANONICAL_MAP.get(token, token))
    return frozenset(canonical)


def _extract_distinctive_skill_tokens(skill_key_tokens):
    canonical = _canonicalize_skill_tokens(skill_key_tokens)
    distinctive = [token for token in canonical if token not in _GENERIC_SKILL_TOKENS]
    if not distinctive:
        distinctive = list(canonical)
    return frozenset(distinctive)


def _stitch_split_skill_rows(rows):
    """Create merged rows for split OCR fragments (e.g. "Corner" + "Recovery")."""
    stitched = []
    sorted_rows = sorted(
        (dict(row) for row in (rows or []) if row.get("text_normalized")),
        key=lambda item: (
            float(item.get("abs_y_center") or 0.0),
            int((item.get("crop_bbox") or [0, 0, 0, 0])[0]),
        ),
    )
    for idx, row in enumerate(sorted_rows):
        row_bbox = row.get("crop_bbox") or [0, 0, 0, 0]
        row_left = int(row_bbox[0])
        row_right = row_left + int(row_bbox[2])
        row_abs_y = float(row.get("abs_y_center") or 0.0)
        for other in sorted_rows[idx + 1: idx + 6]:
            other_bbox = other.get("crop_bbox") or [0, 0, 0, 0]
            other_abs_y = float(other.get("abs_y_center") or 0.0)
            if abs(other_abs_y - row_abs_y) > _SKILL_OCR_STITCH_Y_TOLERANCE:
                continue
            other_left = int(other_bbox[0])
            other_right = other_left + int(other_bbox[2])
            if other_right <= row_left:
                continue
            x_gap = other_left - row_right
            if x_gap > _SKILL_OCR_STITCH_X_GAP:
                continue
            parts = sorted([row, other], key=lambda item: int((item.get("crop_bbox") or [0, 0, 0, 0])[0]))
            merged_raw = " ".join(str(part.get("text_raw") or "").strip() for part in parts).strip()
            merged_normalized = _normalize_skill_text(merged_raw)
            if not merged_normalized:
                continue
            if merged_normalized in {parts[0].get("text_normalized"), parts[1].get("text_normalized")}:
                continue
            if len(merged_normalized) > _SKILL_OCR_STITCH_MAX_TEXT_LENGTH:
                continue
            left_bbox = parts[0].get("crop_bbox") or [0, 0, 0, 0]
            right_bbox = parts[1].get("crop_bbox") or [0, 0, 0, 0]
            merged_left = int(min(left_bbox[0], right_bbox[0]))
            merged_top = int(min(left_bbox[1], right_bbox[1]))
            merged_right = int(max(left_bbox[0] + left_bbox[2], right_bbox[0] + right_bbox[2]))
            merged_bottom = int(max(left_bbox[1] + left_bbox[3], right_bbox[1] + right_bbox[3]))
            stitched.append({
                "text_raw": merged_raw,
                "text_normalized": merged_normalized,
                "confidence": round(
                    (
                        float(parts[0].get("confidence") or 0.0)
                        + float(parts[1].get("confidence") or 0.0)
                    ) / 2.0,
                    4,
                ),
                "crop_y_center": round((merged_top + merged_bottom) / 2.0, 1),
                "abs_y_center": round((row_abs_y + other_abs_y) / 2.0, 1),
                "crop_bbox": [
                    merged_left,
                    merged_top,
                    max(1, merged_right - merged_left),
                    max(1, merged_bottom - merged_top),
                ],
                "ocr_variant": "merged",
                "ocr_sources": [parts[0].get("ocr_variant"), parts[1].get("ocr_variant")],
            })
    return stitched


def _run_skill_name_ocr_pass(crop, bbox_top, reader, allowlist, batch_size, ocr_variant="normal"):
    raw_results = reader.readtext(
        crop,
        allowlist=allowlist,
        detail=1,
        paragraph=False,
        min_size=_SKILL_OCR_MIN_SIZE,
        canvas_size=_SKILL_OCR_CANVAS_SIZE,
        batch_size=batch_size,
        workers=0,
    )
    rows = []
    for bbox, text, confidence in raw_results:
        normalized_text = _normalize_skill_text(text)
        if not normalized_text:
            continue
        y_coords = [pt[1] for pt in bbox]
        x_coords = [pt[0] for pt in bbox]
        row_top = min(y_coords)
        row_bottom = max(y_coords)
        row_center_y = (row_top + row_bottom) / 2.0
        rows.append({
            "text_raw": text,
            "text_normalized": normalized_text,
            "confidence": round(float(confidence), 4),
            "crop_y_center": round(row_center_y, 1),
            "abs_y_center": round(bbox_top + row_center_y, 1),
            "crop_bbox": [
                int(round(min(x_coords))),
                int(round(row_top)),
                int(round(max(x_coords) - min(x_coords))),
                int(round(row_bottom - row_top)),
            ],
            "ocr_variant": ocr_variant,
        })
    return rows


def _extract_ocr_rows_from_name_band(screenshot, include_dim_pass=True):
    """OCR the skill name band with normal + dim-text passes."""
    crop = _crop_absolute_bbox(
        screenshot,
        constants.SKILL_NAME_BAND_BBOX,
        base_region_ltrb=_skill_ui_region(),
    )
    if crop is None or getattr(crop, "size", 0) == 0:
        return []

    from core.ocr import reader
    allowlist = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-!.,'#?() "
    batch_size = _SKILL_OCR_CPU_BATCH_SIZE
    if str(getattr(reader, "device", "cpu")) != "cpu":
        batch_size = _SKILL_OCR_GPU_BATCH_SIZE
    _bbox_left, bbox_top, _bbox_right, _bbox_bottom = [int(v) for v in constants.SKILL_NAME_BAND_BBOX]

    normal_rows = _run_skill_name_ocr_pass(
        crop,
        bbox_top,
        reader,
        allowlist,
        batch_size,
        ocr_variant="normal",
    )
    if not include_dim_pass or not _SKILL_OCR_DIM_ENABLE:
        return _dedupe_ocr_rows(normal_rows)

    dim_rows = []
    for dim_crop in _build_dim_text_variants(crop):
        dim_rows.extend(
            _run_skill_name_ocr_pass(
                dim_crop,
                bbox_top,
                reader,
                allowlist,
                batch_size,
                ocr_variant="dim",
            )
        )
    merged_base = _dedupe_ocr_rows(normal_rows + dim_rows)
    stitched_rows = _stitch_split_skill_rows(merged_base)
    return _dedupe_ocr_rows(merged_base + stitched_rows)


@lru_cache(maxsize=4096)
def _normalize_skill_text(text):
    """Normalize OCR/shortlist text for matching."""
    text = str(text or "")
    for source, replacement in _OCR_SYMBOL_VARIANTS:
        text = text.replace(source, replacement)
    text = text.replace("&", " and ")
    text = re.sub(r"[/_+]+", " ", text)
    text = re.sub(r"[-]+", " ", text)
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_skill_shortlist(skill_shortlist):
    """Pre-normalize shortlist entries once per distinct shortlist."""
    shortlist_key = tuple(skill_shortlist or [])
    cached = _SKILL_SHORTLIST_CACHE.get(shortlist_key)
    if cached is not None:
        return cached

    normalized = []
    seen = set()
    for skill_name in shortlist_key:
        normalized_name = _normalize_skill_text(skill_name)
        if not normalized_name or normalized_name in seen:
            continue
        seen.add(normalized_name)
        skill_tokens = frozenset(normalized_name.split())
        skill_key_tokens = _extract_key_tokens(normalized_name)
        normalized.append({
            "skill_name": skill_name,
            "skill_normalized": normalized_name,
            "skill_compact": normalized_name.replace(" ", ""),
            "skill_tokens": skill_tokens,
            "skill_key_tokens": skill_key_tokens,
            "skill_family_tokens": _canonicalize_skill_tokens(skill_tokens),
            "skill_distinctive_tokens": _extract_distinctive_skill_tokens(skill_key_tokens),
        })

    cached = tuple(normalized)
    _SKILL_SHORTLIST_CACHE[shortlist_key] = cached
    return cached


def _shared_skill_tokens(row_tokens, skill_tokens):
    if not row_tokens or not skill_tokens:
        return 0
    return len(row_tokens & skill_tokens)


def _row_group_key(row):
    crop_bbox = row.get("crop_bbox") or [0, 0, 0, 0]
    return (
        int(round(float(row.get("abs_y_center") or 0.0) / _ROW_GROUP_Y_BUCKET)),
        int(round(float(crop_bbox[0]) / _ROW_GROUP_X_BUCKET)),
    )


def _group_ocr_rows_by_physical_row(ocr_rows):
    grouped = {}
    for row in (ocr_rows or []):
        normalized = row.get("text_normalized") or ""
        if not normalized:
            continue
        grouped.setdefault(_row_group_key(row), []).append(dict(row))
    groups = list(grouped.values())
    groups.sort(
        key=lambda rows: (
            min(float(item.get("abs_y_center") or 0.0) for item in rows),
            min(int((item.get("crop_bbox") or [0, 0, 0, 0])[0]) for item in rows),
        ),
    )
    return groups


def _build_row_skill_match_metrics(row, skill_entry):
    normalized = row.get("text_normalized") or ""
    normalized_compact = normalized.replace(" ", "")
    row_tokens = frozenset(normalized.split())
    row_key_tokens = _extract_key_tokens(normalized)
    row_family_tokens = _canonicalize_skill_tokens(row_tokens)

    skill_normalized = skill_entry["skill_normalized"]
    skill_compact = skill_entry["skill_compact"]
    skill_tokens = skill_entry["skill_tokens"]
    skill_key_tokens = skill_entry.get("skill_key_tokens") or skill_tokens

    score = max(
        Levenshtein.ratio(normalized, skill_normalized),
        Levenshtein.ratio(normalized_compact, skill_compact),
    )
    method = "fuzzy"
    shorter_len = min(len(normalized), len(skill_normalized))
    longer_len = max(len(normalized), len(skill_normalized))
    if ((skill_normalized in normalized or normalized in skill_normalized)
            and shorter_len >= max(6, int(longer_len * 0.55))):
        score = max(score, 0.96)
        method = "exact"

    shared_key_tokens = _shared_skill_tokens(row_key_tokens, skill_key_tokens)
    key_coverage = shared_key_tokens / max(1, len(skill_key_tokens))
    row_key_coverage = shared_key_tokens / max(1, len(row_key_tokens))
    if (
        len(skill_key_tokens) >= _TOKEN_MERGE_MIN_SHARED
        and len(row_tokens) <= _TOKEN_MERGE_MAX_ROW_TOKENS
        and shared_key_tokens >= _TOKEN_MERGE_MIN_SHARED
        and key_coverage >= _TOKEN_MERGE_MIN_COVERAGE
        and row_key_coverage >= 0.5
    ):
        token_score = 0.74 + (key_coverage * 0.16) + min(0.08, row_key_coverage * 0.08)
        if token_score > score:
            score = token_score
            method = "token_merge"

    shared_tokens = 0
    token_overlap = 0.0
    if skill_tokens:
        shared_tokens = _shared_skill_tokens(row_tokens, skill_tokens)
        token_overlap = shared_tokens / max(1, len(skill_tokens))
        # Do not promote a match on a single shared generic token.
        # That let rows like "Straightaway Spurt" clear the fuzzy
        # threshold for "Straightaway Recovery".
        if shared_tokens >= 2 and token_overlap >= 0.66:
            score = max(score, 0.78 + (token_overlap * 0.18))
        elif shared_tokens >= 2 and token_overlap >= 0.5:
            score = max(score, 0.72 + (token_overlap * 0.12))

    accepted = False
    if score >= _EXACT_MATCH_THRESHOLD:
        accepted = True
    elif method == "token_merge" and score >= _TOKEN_MERGE_FALLBACK_THRESHOLD:
        accepted = True
    elif score >= _FUZZY_MATCH_THRESHOLD and len(normalized) >= _FUZZY_MATCH_MIN_LENGTH:
        accepted = True
    if accepted and score < _EXACT_MATCH_THRESHOLD and method == "fuzzy" and skill_tokens and len(skill_tokens) >= 2 and shared_tokens < 2:
        accepted = False

    return {
        **row,
        "match_name": skill_entry["skill_name"],
        "match_score": round(float(score), 4),
        "raw_match_score": round(float(score), 4),
        "name_match_method": method,
        "shared_key_tokens": int(shared_key_tokens),
        "key_token_coverage": round(float(key_coverage), 4),
        "shared_tokens": int(shared_tokens),
        "token_overlap": round(float(token_overlap), 4),
        "row_tokens": row_tokens,
        "row_key_tokens": row_key_tokens,
        "row_family_tokens": row_family_tokens,
        "accepted": bool(accepted),
    }


def _match_candidate_rank(candidate):
    return (
        float(candidate.get("match_score") or 0.0),
        float(candidate.get("confidence") or 0.0),
        _name_match_method_rank(candidate.get("name_match_method")),
        _ocr_variant_rank(candidate.get("ocr_variant")),
        len(candidate.get("text_normalized") or ""),
    )


def _variants_seen_for_metrics(metrics):
    variants = {str(metric.get("ocr_variant") or "") for metric in (metrics or []) if metric.get("ocr_variant")}
    return sorted(variants, key=_ocr_variant_rank, reverse=True)


def _meaningful_normal_metrics(metrics):
    if not isinstance(metrics, dict):
        return False
    if len(str(metrics.get("text_normalized") or "")) < 5:
        return False
    return float(metrics.get("confidence") or 0.0) >= _MEANINGFUL_NORMAL_CONFIDENCE


def _corner_family_conflict(skill_entry, normal_metrics, corroborated_tokens=None):
    if not isinstance(skill_entry, dict) or not isinstance(normal_metrics, dict):
        return False
    skill_family_tokens = skill_entry.get("skill_family_tokens") or frozenset()
    if "corner" not in skill_family_tokens:
        return False
    target_special = skill_family_tokens & _CORNER_FAMILY_SPECIAL_TOKENS
    if not target_special:
        return False
    normal_family_tokens = normal_metrics.get("row_family_tokens") or frozenset()
    if "corner" not in normal_family_tokens:
        return False
    if not (normal_family_tokens & _CORNER_FAMILY_DISTANCE_TOKENS):
        return False
    corroborated = frozenset(corroborated_tokens or [])
    return not bool(corroborated & target_special)


def _normal_variant_strongly_disagrees(skill_entry, normal_metrics, corroborated_tokens=None):
    if not _meaningful_normal_metrics(normal_metrics):
        return False
    if _corner_family_conflict(skill_entry, normal_metrics, corroborated_tokens=corroborated_tokens):
        return True
    if normal_metrics.get("row_family_tokens") and (normal_metrics["row_family_tokens"] & (skill_entry.get("skill_distinctive_tokens") or frozenset())):
        return False
    if int(normal_metrics.get("shared_key_tokens") or 0) == 0:
        return True
    return (
        float(normal_metrics.get("raw_match_score") or 0.0) < _FUZZY_MATCH_THRESHOLD
        and float(normal_metrics.get("key_token_coverage") or 0.0) < 0.5
    )


def _token_evidence_for_match(skill_entry, best_metric, support_metrics, normal_metrics):
    support_metrics = list(support_metrics or [])
    corroborated_metrics = [metric for metric in support_metrics if metric is not best_metric]
    evidence_tokens = frozenset().union(*(metric.get("row_family_tokens") or frozenset() for metric in support_metrics)) if support_metrics else frozenset()
    corroborated_tokens = frozenset().union(*(metric.get("row_family_tokens") or frozenset() for metric in corroborated_metrics)) if corroborated_metrics else frozenset()
    distinctive_tokens = skill_entry.get("skill_distinctive_tokens") or frozenset()
    skill_tokens = skill_entry.get("skill_tokens") or frozenset()
    best_row_tokens = best_metric.get("row_tokens") or frozenset()
    return {
        "row_tokens": sorted(best_row_tokens),
        "shared_tokens": sorted(best_row_tokens & skill_tokens),
        "distinctive_tokens_required": sorted(distinctive_tokens),
        "distinctive_tokens_present": sorted(evidence_tokens & distinctive_tokens),
        "corroborated_tokens": sorted(corroborated_tokens),
        "normal_tokens": sorted((normal_metrics or {}).get("row_family_tokens") or []),
    }


# ---------------------------------------------------------------------------
# Skill matching against shortlist
# ---------------------------------------------------------------------------

def _match_skill_rows_to_shortlist(ocr_rows, skill_shortlist, normalized_shortlist=None):
    """Match OCR rows against the configured skill shortlist.

    Uses exact match first, then conservative fuzzy matching.
    Returns a list of matched rows with match details.
    """
    normalized_shortlist = tuple(normalized_shortlist or _normalize_skill_shortlist(skill_shortlist))
    shortlist_key = tuple(item["skill_normalized"] for item in normalized_shortlist)
    row_signature = _compute_row_signature(ocr_rows)
    cache_key = (row_signature, shortlist_key)
    cached = _SKILL_MATCH_CACHE.get(cache_key)
    if cached is not None:
        return [dict(row) for row in cached]

    matched_by_skill = {}
    for row_group in _group_ocr_rows_by_physical_row(ocr_rows):
        group_metrics = []
        for skill_entry in normalized_shortlist:
            group_metrics.extend(
                _build_row_skill_match_metrics(row, skill_entry)
                for row in row_group
            )

        for skill_entry in normalized_shortlist:
            skill_name = skill_entry["skill_name"]
            per_skill = [
                metric for metric in group_metrics
                if metric.get("match_name") == skill_name
            ]
            if not per_skill:
                continue
            accepted_metrics = [metric for metric in per_skill if metric.get("accepted")]
            if not accepted_metrics:
                continue

            best_metric = max(accepted_metrics, key=_match_candidate_rank)
            support_metrics = list(accepted_metrics)
            variants_seen = _variants_seen_for_metrics(support_metrics)
            normal_metrics = None
            normal_candidates = [metric for metric in per_skill if metric.get("ocr_variant") == "normal"]
            if normal_candidates:
                normal_metrics = max(normal_candidates, key=_match_candidate_rank)

            token_evidence = _token_evidence_for_match(skill_entry, best_metric, support_metrics, normal_metrics)
            corroborated_tokens = frozenset(token_evidence.get("corroborated_tokens") or [])
            distinctive_present = frozenset(token_evidence.get("distinctive_tokens_present") or [])
            reject_reason = ""
            dim_only_or_merged = bool(variants_seen) and all(variant in ("dim", "merged") for variant in variants_seen)
            if (
                len(skill_entry.get("skill_key_tokens") or []) >= 2
                and skill_entry.get("skill_distinctive_tokens")
                and not distinctive_present
            ):
                reject_reason = "missing_distinctive_token_evidence"
            elif dim_only_or_merged and _corner_family_conflict(
                skill_entry,
                normal_metrics,
                corroborated_tokens=corroborated_tokens,
            ):
                reject_reason = "corner_family_conflict"
            elif (
                dim_only_or_merged
                and float(best_metric.get("raw_match_score") or 0.0) >= _EXACT_MATCH_THRESHOLD
                and _normal_variant_strongly_disagrees(
                    skill_entry,
                    normal_metrics,
                    corroborated_tokens=corroborated_tokens,
                )
            ):
                reject_reason = "dim_only_exact_conflicts_with_normal"
            if reject_reason:
                continue

            consensus_score = float(best_metric.get("raw_match_score") or 0.0)
            if len(variants_seen) >= 2:
                consensus_score += 0.02
            elif dim_only_or_merged and _normal_variant_strongly_disagrees(
                skill_entry,
                normal_metrics,
                corroborated_tokens=corroborated_tokens,
            ):
                consensus_score -= 0.06
            consensus_score = max(0.0, min(1.0, consensus_score))

            final_match_type = None
            if consensus_score >= _EXACT_MATCH_THRESHOLD:
                final_match_type = "exact"
            elif (
                best_metric.get("name_match_method") == "token_merge"
                and consensus_score >= _TOKEN_MERGE_FALLBACK_THRESHOLD
            ):
                final_match_type = "fuzzy"
            elif consensus_score >= _FUZZY_MATCH_THRESHOLD and len(best_metric.get("text_normalized") or "") >= _FUZZY_MATCH_MIN_LENGTH:
                final_match_type = "fuzzy"
            if final_match_type is None:
                continue

            candidate = {
                **{
                    key: value
                    for key, value in best_metric.items()
                    if key not in {"row_tokens", "row_key_tokens", "row_family_tokens", "accepted"}
                },
                "match_score": round(float(consensus_score), 4),
                "match_type": final_match_type,
                "ocr_variants_seen": variants_seen,
                "chosen_variant": best_metric.get("ocr_variant"),
                "consensus_score": round(float(consensus_score), 4),
                "token_evidence": token_evidence,
                "reject_reason": "",
            }
            skill_key = _normalize_skill_text(skill_name)
            existing = matched_by_skill.get(skill_key)
            if existing is None or _match_candidate_rank(candidate) > _match_candidate_rank(existing):
                matched_by_skill[skill_key] = candidate

    matched = sorted(
        matched_by_skill.values(),
        key=lambda item: (
            float(item.get("abs_y_center") or 0.0),
            int((item.get("crop_bbox") or [0, 0, 0, 0])[0]),
        ),
    )
    with _SKILL_MATCH_CACHE_LOCK:
        _SKILL_MATCH_CACHE[cache_key] = tuple(dict(row) for row in matched)
    return matched


# ---------------------------------------------------------------------------
# Increment button detection and pairing
# ---------------------------------------------------------------------------

def _detect_increment_buttons(screenshot):
    """Find all skill increment (+) buttons in the skill list area.

    Searches across the full width of the scrolling skill area so that
    buttons on the right side of skill cards are not clipped. Returns
    matches as (x, y, w, h) relative to SCROLLING_SKILL_SCREEN_BBOX.
    """
    crop = _crop_absolute_bbox(
        screenshot,
        constants.SCROLLING_SKILL_SCREEN_BBOX,
        base_region_ltrb=_skill_ui_region(),
    )
    if crop is None or getattr(crop, "size", 0) == 0:
        return []
    template_attempts = []
    if (constants.SCENARIO_NAME or "") in ("mant", "trackblazer"):
        template_attempts.append(
            (
                constants.SKILL_INCREMENT_TEMPLATES.get("trackblazer"),
                _INVERSE_GLOBAL_SCALE,
            )
        )
    template_attempts.append(
        (
            constants.SKILL_INCREMENT_TEMPLATES.get("default", _INCREMENT_TEMPLATE),
            1.0,
        )
    )

    all_matches = []
    for template_path, template_scaling in template_attempts:
        if not template_path:
            continue
        matches = device_action.match_template(
            template_path,
            crop,
            threshold=_INCREMENT_MATCH_THRESHOLD,
            template_scaling=template_scaling,
        )
        all_matches.extend(matches)
    return device_action.deduplicate_boxes(all_matches)


def _detect_obtained_badges(screenshot, threshold=None):
    """Find all 'Obtained' badges in the skill list area.

    Returns matches as (x, y, w, h) relative to SCROLLING_SKILL_SCREEN_BBOX.
    Used to suppress increment pairing for already-learned skills.
    """
    crop = _crop_absolute_bbox(
        screenshot,
        constants.SCROLLING_SKILL_SCREEN_BBOX,
        base_region_ltrb=_skill_ui_region(),
    )
    if crop is None or getattr(crop, "size", 0) == 0:
        return []
    match_threshold = _OBTAINED_MATCH_THRESHOLD if threshold is None else float(threshold)
    matches = device_action.match_template(
        _OBTAINED_TEMPLATE,
        crop,
        threshold=match_threshold,
        template_scaling=_INVERSE_GLOBAL_SCALE,
    )
    return device_action.deduplicate_boxes(matches)


def _row_has_obtained_badge(row, obtained_matches, y_tolerance=None):
    """Check if an OCR row is near an 'Obtained' badge by vertical proximity."""
    if not obtained_matches:
        return False
    row_abs_y = row["abs_y_center"]
    scroll_bbox_top = int(constants.SCROLLING_SKILL_SCREEN_BBOX[1])
    tolerance = _OBTAINED_Y_TOLERANCE if y_tolerance is None else int(y_tolerance)
    for match in obtained_matches:
        badge_abs_cy = scroll_bbox_top + match[1] + match[3] // 2
        if abs(badge_abs_cy - row_abs_y) <= tolerance:
            return True
    return False


def _detect_obtained_text_tokens(screenshot):
    """Detect OCR text that looks like 'Obtained' in the scrolling skill list."""
    crop = _crop_absolute_bbox(
        screenshot,
        constants.SCROLLING_SKILL_SCREEN_BBOX,
        base_region_ltrb=_skill_ui_region(),
    )
    if crop is None or getattr(crop, "size", 0) == 0:
        return []
    from core.ocr import reader
    allowlist = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
    batch_size = _SKILL_OCR_CPU_BATCH_SIZE
    if str(getattr(reader, "device", "cpu")) != "cpu":
        batch_size = _SKILL_OCR_GPU_BATCH_SIZE
    _scroll_left, scroll_top, _scroll_right, _scroll_bottom = [int(v) for v in constants.SCROLLING_SKILL_SCREEN_BBOX]
    ocr_inputs = [crop]
    ocr_inputs.extend(_build_dim_text_variants(crop))
    matches = []
    seen = {}
    for candidate in ocr_inputs:
        raw_results = reader.readtext(
            candidate,
            allowlist=allowlist,
            detail=1,
            paragraph=False,
            min_size=_SKILL_OCR_MIN_SIZE,
            canvas_size=_SKILL_OCR_CANVAS_SIZE,
            batch_size=batch_size,
            workers=0,
        )
        for bbox, text, confidence in raw_results:
            normalized = _normalize_skill_text(text)
            if not normalized:
                continue
            compact = normalized.replace(" ", "")
            token_score = max(
                Levenshtein.ratio(compact, "obtained"),
                Levenshtein.ratio(compact.replace("0", "o").replace("1", "l"), "obtained"),
            )
            if "obtain" not in compact and token_score < _OBTAINED_TEXT_MIN_SIMILARITY:
                continue
            y_coords = [pt[1] for pt in bbox]
            x_coords = [pt[0] for pt in bbox]
            row_top = min(y_coords)
            row_bottom = max(y_coords)
            row_center_y = (row_top + row_bottom) / 2.0
            row_abs_y = float(scroll_top + row_center_y)
            key = int(round(row_abs_y / 5.0))
            payload = {
                "text_raw": text,
                "text_normalized": normalized,
                "confidence": round(float(confidence), 4),
                "score": round(float(token_score), 4),
                "abs_y_center": round(row_abs_y, 1),
                "crop_bbox": [
                    int(round(min(x_coords))),
                    int(round(row_top)),
                    int(round(max(x_coords) - min(x_coords))),
                    int(round(row_bottom - row_top)),
                ],
            }
            current = seen.get(key)
            rank = (float(payload["score"]), float(payload["confidence"]))
            if current is None or rank > (float(current["score"]), float(current["confidence"])):
                seen[key] = payload
    matches.extend(seen.values())
    matches.sort(key=lambda item: float(item.get("abs_y_center") or 0.0))
    return matches


def _row_has_obtained_text(row, obtained_text_matches, y_tolerance=None):
    if not obtained_text_matches:
        return False
    try:
        row_abs_y = float(row.get("abs_y_center"))
    except (TypeError, ValueError):
        return False
    tolerance = _OBTAINED_TEXT_Y_TOLERANCE if y_tolerance is None else int(y_tolerance)
    for match in obtained_text_matches:
        if abs(float(match.get("abs_y_center") or 0.0) - row_abs_y) <= tolerance:
            return True
    return False


def _collect_obtained_evidence_for_row(row, screenshot):
    """Collect template/text evidence that a no-increment row is already purchased."""
    evidence = {
        "obtained": False,
        "template": False,
        "text": False,
        "source": "none",
    }
    if not isinstance(row, dict):
        return evidence
    if row.get("obtained"):
        existing = str(row.get("obtained_evidence") or "template")
        evidence["obtained"] = True
        evidence["template"] = existing in ("template", "both")
        evidence["text"] = existing in ("text", "both")
        evidence["source"] = existing
        return evidence
    if screenshot is None:
        return evidence

    template_found = False
    for threshold in _OBTAINED_RECHECK_THRESHOLDS:
        obtained_matches = _detect_obtained_badges(screenshot, threshold=threshold)
        if _row_has_obtained_badge(
            row,
            obtained_matches,
            y_tolerance=_OBTAINED_RECHECK_Y_TOLERANCE,
        ):
            template_found = True
            break
    text_matches = _detect_obtained_text_tokens(screenshot)
    text_found = _row_has_obtained_text(
        row,
        text_matches,
        y_tolerance=_OBTAINED_TEXT_Y_TOLERANCE,
    )
    if template_found and text_found:
        source = "both"
    elif template_found:
        source = "template"
    elif text_found:
        source = "text"
    else:
        source = "none"
    evidence.update({
        "obtained": bool(template_found or text_found),
        "template": bool(template_found),
        "text": bool(text_found),
        "source": source,
    })
    return evidence


def _classify_no_increment_row(row, screenshot):
    """Classify no-increment matches as obtained when evidence is visible live."""
    if not isinstance(row, dict):
        return "target_reacquired_but_no_increment_paired"
    evidence = _collect_obtained_evidence_for_row(row, screenshot)
    row["obtained_evidence"] = evidence.get("source", "none")
    row["obtained_recheck"] = True
    if evidence.get("obtained"):
        row["obtained"] = True
        return "target_obtained_no_increment"
    row["obtained"] = False
    return "target_reacquired_but_no_increment_paired"


def _pair_skill_row_to_increment(row, increment_matches):
    """Pair an OCR row to its increment button by vertical proximity.

    OCR rows use absolute Y (from SKILL_NAME_BAND_BBOX), and increment
    matches use coordinates relative to SCROLLING_SKILL_SCREEN_BBOX.
    Convert both to absolute Y for comparison.
    """
    row_abs_y = row["abs_y_center"]
    scroll_bbox_top = int(constants.SCROLLING_SKILL_SCREEN_BBOX[1])
    candidates = []
    for match in increment_matches:
        # Convert increment match Y to absolute
        inc_abs_cy = scroll_bbox_top + match[1] + match[3] // 2
        if abs(inc_abs_cy - row_abs_y) <= _INCREMENT_Y_TOLERANCE:
            candidates.append((match, inc_abs_cy))
    if not candidates:
        return None
    # Rightmost match is the (+) button.
    return max(candidates, key=lambda c: c[0][0])[0]


def _increment_match_abs_center_y(match):
    """Return the absolute center Y for a paired increment match box."""
    scroll_bbox_top = int(constants.SCROLLING_SKILL_SCREEN_BBOX[1])
    return scroll_bbox_top + int(match[1]) + int(match[3]) // 2


def _increment_match_vertical_distance(row, match):
    """Return the absolute vertical distance between a row and increment button."""
    if not row or not match:
        return None
    try:
        row_abs_y = float(row.get("abs_y_center"))
    except (TypeError, ValueError):
        return None
    return abs(float(_increment_match_abs_center_y(match)) - row_abs_y)


def _match_has_safe_increment(target_match):
    """Whether the matched row has a safe increment pairing for live clicks."""
    if not isinstance(target_match, dict):
        return False
    inc = target_match.get("increment_match")
    if not inc:
        return False
    pairing = target_match.get("increment_pairing")
    distance = target_match.get("increment_vertical_distance")
    if pairing == "vertical":
        return True
    if pairing == "fallback_nearest":
        try:
            return float(distance) <= float(_INCREMENT_FALLBACK_Y_TOLERANCE)
        except (TypeError, ValueError):
            return False
    return False


# ---------------------------------------------------------------------------
# Per-frame analysis (used by the buffered pipeline)
# ---------------------------------------------------------------------------

def _analyze_skill_frame(frame_payload):
    """Analyze a single captured frame: OCR + scrollbar + increment detection."""
    t0 = _time()
    screenshot = frame_payload.get("screenshot")
    skill_shortlist = frame_payload.get("skill_shortlist", [])
    normalized_shortlist = frame_payload.get("normalized_shortlist")

    # OCR the name band.
    t_ocr = _time()
    include_dim_pass = (constants.SCENARIO_NAME or "") in ("mant", "trackblazer")
    ocr_rows = _extract_ocr_rows_from_name_band(screenshot, include_dim_pass=include_dim_pass)
    ocr_elapsed = _time() - t_ocr

    # Match against shortlist.
    t_match = _time()
    matched_targets = _match_skill_rows_to_shortlist(
        ocr_rows,
        skill_shortlist,
        normalized_shortlist=normalized_shortlist,
    )
    match_elapsed = _time() - t_match

    # Detect increment buttons.
    t_increment = _time()
    increment_matches = _detect_increment_buttons(screenshot) if matched_targets else []
    increment_elapsed = _time() - t_increment

    # Detect "Obtained" badges to suppress increment pairing for learned skills.
    obtained_matches = _detect_obtained_badges(screenshot) if matched_targets else []

    # Pair matched targets to increment buttons.
    paired_increment_keys = set()
    for target in matched_targets:
        if not target.get("name_match_method"):
            target["name_match_method"] = target.get("match_type") or "fuzzy"
        if _row_has_obtained_badge(target, obtained_matches):
            target["increment_match"] = None
            target["increment_pairing"] = None
            target["increment_vertical_distance"] = None
            target["obtained"] = True
            target["obtained_evidence"] = "template"
            continue
        inc = _pair_skill_row_to_increment(target, increment_matches)
        if inc:
            target["increment_match"] = list(inc)
            target["increment_pairing"] = "vertical"
            target["increment_vertical_distance"] = round(_increment_match_vertical_distance(target, inc) or 0.0, 1)
            target["obtained"] = False
            target["obtained_evidence"] = "none"
            paired_increment_keys.add(tuple(int(v) for v in inc))
        else:
            target["increment_match"] = None
            target["increment_pairing"] = None
            target["increment_vertical_distance"] = None
            target["obtained"] = bool(target.get("obtained"))
            target["obtained_evidence"] = target.get("obtained_evidence") or ("template" if target.get("obtained") else "none")

    # Fallback: if a matched row still has no increment pair, assign the
    # nearest remaining button when it is still plausibly aligned. The skill
    # cards can offset the button far enough from the OCR title that strict
    # vertical proximity misses even when the button is still visibly tied to
    # that row, but we do not want an unbounded best-guess click.
    if increment_matches:
        remaining_targets = [target for target in matched_targets
                             if not target.get("increment_match") and not target.get("obtained")]
        remaining_increments = [
            match for match in sorted(increment_matches, key=_increment_match_abs_center_y)
            if tuple(int(v) for v in match) not in paired_increment_keys
        ]
        while remaining_targets and remaining_increments:
            best_pair = None
            for target in remaining_targets:
                for inc in remaining_increments:
                    distance = _increment_match_vertical_distance(target, inc)
                    if distance is None:
                        continue
                    candidate = (float(distance), target, inc)
                    if best_pair is None or candidate[0] < best_pair[0]:
                        best_pair = candidate
            if best_pair is None or best_pair[0] > _INCREMENT_FALLBACK_Y_TOLERANCE:
                break
            distance, target, inc = best_pair
            target["increment_match"] = list(inc)
            target["increment_pairing"] = "fallback_nearest"
            target["increment_vertical_distance"] = round(distance, 1)
            target["obtained"] = False
            target["obtained_evidence"] = "none"
            paired_increment_keys.add(tuple(int(v) for v in inc))
            remaining_targets = [item for item in remaining_targets if item is not target]
            remaining_increments = [item for item in remaining_increments if item != inc]

    # Scrollbar state.
    t_scrollbar = _time()
    scrollbar = inspect_skill_scrollbar(screenshot=screenshot)
    scrollbar_elapsed = _time() - t_scrollbar

    scan_elapsed = _time() - t0
    return {
        "index": int(frame_payload.get("index", 0)),
        "elapsed": frame_payload.get("elapsed"),
        "ocr_rows_count": len(ocr_rows),
        "ocr_rows": ocr_rows,
        "matched_targets": matched_targets,
        "increment_count": len(increment_matches),
        "increment_matches": [list(m) for m in increment_matches],
        "scrollbar": scrollbar,
        "row_signature": _compute_row_signature(ocr_rows),
        "final": bool(frame_payload.get("final")),
        "timing": {
            "capture": round(frame_payload.get("capture_elapsed", 0.0), 4),
            "ocr": round(ocr_elapsed, 4),
            "match": round(match_elapsed, 4),
            "increment": round(increment_elapsed, 4),
            "scrollbar": round(scrollbar_elapsed, 4),
            "scan": round(scan_elapsed, 4),
            "wall": round(frame_payload.get("capture_elapsed", 0.0) + scan_elapsed, 4),
        },
    }


# ---------------------------------------------------------------------------
# Buffered capture during scrollbar drag
# ---------------------------------------------------------------------------

def _capture_skill_frames_during_scrollbar_drag(
    initial_scrollbar,
    skill_shortlist,
    normalized_shortlist=None,
    drag_duration=None,
    frame_interval=None,
    debug_session_dir=None,
    live_index=None,
):
    """Capture frames during a top-to-bottom scrollbar drag, analyzing concurrently.

    Same producer-consumer pattern as the Trackblazer shop scan.

    When *live_index* is provided, analysis workers append results to it
    and the function returns immediately after the drag completes (without
    waiting for all analysis to finish).  A background thread joins the
    workers and calls ``live_index.mark_done()`` when analysis is complete.
    """

    def _analysis_worker():
        while True:
            frame_payload = analysis_queue.get()
            if frame_payload is None:
                analysis_queue.task_done()
                return
            try:
                analyzed = _analyze_skill_frame(frame_payload)
                if debug_session_dir is not None:
                    frame_index = int(analyzed.get("index", 0))
                    scrollbar = analyzed.get("scrollbar") or {}
                    frame_summary = {
                        "index": frame_index,
                        "elapsed": analyzed.get("elapsed"),
                        "scrollbar_ratio": scrollbar.get("position_ratio"),
                        "ocr_rows_count": analyzed.get("ocr_rows_count", 0),
                        "matched_names": [m.get("match_name") for m in (analyzed.get("matched_targets") or [])],
                        "increment_count": analyzed.get("increment_count", 0),
                        "timing": analyzed.get("timing"),
                    }
                    _save_skill_runtime_debug_frame(
                        debug_session_dir,
                        f"buffer_frame_{frame_index:03d}_ratio_{str(scrollbar.get('position_ratio')).replace('.', '_')}",
                        frame_payload.get("screenshot"),
                        frame_summary=frame_summary,
                    )
                analyzed_frames.append(analyzed)
                if live_index is not None:
                    live_index.append(analyzed)
            finally:
                analysis_queue.task_done()

    resolved_drag_duration = float(drag_duration or _SCROLLBAR_DRAG_DURATION_SECONDS)
    resolved_frame_interval = float(frame_interval or _SCROLLBAR_FRAME_INTERVAL_SECONDS)

    drag = {
        "start": None,
        "end": None,
        "duration": float(resolved_drag_duration),
        "swiped": False,
        "frames": [],
        "stop_reason": "",
        "skipped_due_to_backlog": 0,
        "timing": {},
    }
    thumb_center = (initial_scrollbar or {}).get("thumb_center")
    bbox = (initial_scrollbar or {}).get("bbox") or [int(v) for v in constants.SKILL_SCROLLBAR_BBOX]
    if not thumb_center:
        drag["stop_reason"] = "scrollbar_thumb_not_detected"
        return drag

    drag_start = (int(thumb_center[0]), int(thumb_center[1]))
    drag_end = (
        int(initial_scrollbar.get("track_center_x") or thumb_center[0]),
        int(bbox[3] - _SCROLLBAR_DRAG_END_PADDING),
    )
    drag["start"] = [int(drag_start[0]), int(drag_start[1])]
    drag["end"] = [int(drag_end[0]), int(drag_end[1])]

    t_drag = _time()
    capture_total = 0.0
    frame_count = 0
    analyzed_frames = []
    analysis_queue = Queue()
    analysis_workers = []
    worker_count = max(1, _SCROLLBAR_ANALYSIS_WORKERS)
    for _ in range(worker_count):
        worker = threading.Thread(target=_analysis_worker, daemon=True)
        worker.start()
        analysis_workers.append(worker)

    def _run_drag():
        drag["swiped"] = bool(device_action.swipe(
            drag_start,
            drag_end,
            duration=resolved_drag_duration,
            text="Skill scrollbar drag top-to-bottom",
        ))

    drag_thread = threading.Thread(target=_run_drag, daemon=True)
    drag_thread.start()
    next_capture_at = _time() + resolved_frame_interval
    while drag_thread.is_alive():
        now = _time()
        if now < next_capture_at:
            sleep(max(0.0, next_capture_at - now))
        frame_capture_t0 = _time()
        screenshot = _capture_live_skill_screenshot()
        capture_elapsed = _time() - frame_capture_t0
        capture_total += capture_elapsed
        analysis_queue.put({
            "index": frame_count,
            "elapsed": round(_time() - t_drag, 4),
            "capture_elapsed": capture_elapsed,
            "screenshot": screenshot,
            "skill_shortlist": skill_shortlist,
            "normalized_shortlist": normalized_shortlist,
        })
        frame_count += 1
        next_capture_at = max(next_capture_at + resolved_frame_interval, _time() + 0.001)

    drag_thread.join()
    # Capture one final frame after drag completes.
    final_capture_t0 = _time()
    final_screenshot = _capture_live_skill_screenshot()
    final_capture_elapsed = _time() - final_capture_t0
    capture_total += final_capture_elapsed
    analysis_queue.put({
        "index": frame_count,
        "elapsed": round(_time() - t_drag, 4),
        "capture_elapsed": final_capture_elapsed,
        "screenshot": final_screenshot,
        "skill_shortlist": skill_shortlist,
        "normalized_shortlist": normalized_shortlist,
        "final": True,
    })
    frame_count += 1
    capture_window = _time() - t_drag

    if live_index is not None:
        # Non-blocking path: determine stop_reason from a live scrollbar
        # check and let analysis workers continue in the background.
        post_drag_sb = inspect_skill_scrollbar()
        if post_drag_sb.get("is_at_bottom"):
            drag["stop_reason"] = "scrollbar_bottom_reached"
        elif drag.get("swiped"):
            drag["stop_reason"] = "drag_completed_without_bottom_detection"
        else:
            drag["stop_reason"] = "scrollbar_drag_failed"
        drag["timing"] = {
            "drag_runtime": round(capture_window, 4),
            "frame_interval_target": round(resolved_frame_interval, 4),
            "frames": int(frame_count),
            "skipped_due_to_backlog": 0,
            "capture_total": round(capture_total, 4),
        }

        def _bg_analysis_cleanup():
            analysis_queue.join()
            for _ in analysis_workers:
                analysis_queue.put(None)
            for w in analysis_workers:
                w.join()
            live_index.mark_done()

        threading.Thread(target=_bg_analysis_cleanup, daemon=True).start()
        return drag

    # Blocking path: wait for all analysis to complete.
    scan_total = 0.0
    analysis_queue.join()
    for _ in analysis_workers:
        analysis_queue.put(None)
    for worker in analysis_workers:
        worker.join()
    drag["frames"] = sorted(analyzed_frames, key=lambda f: int(f.get("index", 0)))
    for frame in drag["frames"]:
        scan_total += float(((frame.get("timing") or {}).get("scan") or 0.0))

    final_scrollbar = (
        ((drag.get("frames") or [])[-1] or {}).get("scrollbar")
        if drag.get("frames")
        else None
    ) or {}

    if final_scrollbar.get("is_at_bottom"):
        drag["stop_reason"] = "scrollbar_bottom_reached"
    elif drag.get("swiped"):
        drag["stop_reason"] = "drag_completed_without_bottom_detection"
    else:
        drag["stop_reason"] = "scrollbar_drag_failed"

    wall_total = _time() - t_drag
    drag["timing"] = {
        "drag_runtime": round(capture_window, 4),
        "frame_interval_target": round(resolved_frame_interval, 4),
        "frames": int(frame_count),
        "skipped_due_to_backlog": int(drag.get("skipped_due_to_backlog", 0)),
        "capture_total": round(capture_total, 4),
        "scan_total": round(scan_total, 4),
        "analysis_total": round(max(0.0, wall_total - capture_window), 4),
        "wall": round(wall_total, 4),
    }
    return drag


def _complete_scan_to_bottom(skill_shortlist, start_index, normalized_shortlist=None, debug_session_dir=None):
    """Force one or more bottom-edge captures when the main drag stops early."""
    normalized_shortlist = tuple(normalized_shortlist or _normalize_skill_shortlist(skill_shortlist))
    extra_frames = []
    completion = {
        "attempted": False,
        "passes": [],
        "completed": False,
        "final_scrollbar": None,
    }
    next_index = int(start_index)
    for _ in range(_SCROLLBAR_BOTTOM_COMPLETION_PASSES):
        current_sb = inspect_skill_scrollbar()
        completion["final_scrollbar"] = current_sb
        if not current_sb.get("detected") or current_sb.get("is_at_bottom"):
            completion["completed"] = bool(current_sb.get("detected") and current_sb.get("is_at_bottom"))
            break
        completion["attempted"] = True
        pass_result = _drag_skill_scrollbar(
            current_sb,
            edge="bottom",
            duration=_SCROLLBAR_BOTTOM_COMPLETION_DRAG_DURATION_SECONDS,
        )
        sleep(_SCROLLBAR_REACQUIRE_SETTLE_SECONDS)
        screenshot = _capture_live_skill_screenshot()
        frame = _analyze_skill_frame({
            "index": next_index,
            "elapsed": None,
            "capture_elapsed": 0.0,
            "screenshot": screenshot,
            "skill_shortlist": skill_shortlist,
            "normalized_shortlist": normalized_shortlist,
            "final": True,
        })
        if debug_session_dir is not None:
            _save_skill_runtime_debug_frame(
                debug_session_dir,
                f"bottom_completion_frame_{next_index:03d}",
                screenshot,
                frame_summary={
                    "index": next_index,
                    "elapsed": None,
                    "scrollbar_ratio": ((frame.get("scrollbar") or {}).get("position_ratio")),
                    "ocr_rows_count": frame.get("ocr_rows_count", 0),
                    "matched_names": [m.get("match_name") for m in (frame.get("matched_targets") or [])],
                    "increment_count": frame.get("increment_count", 0),
                    "timing": frame.get("timing"),
                },
            )
        next_index += 1
        live_sb = frame.get("scrollbar") or {}
        pass_result["scrollbar_after"] = {
            "detected": live_sb.get("detected"),
            "position_ratio": live_sb.get("position_ratio"),
            "is_at_bottom": live_sb.get("is_at_bottom"),
        }
        completion["passes"].append(pass_result)
        completion["final_scrollbar"] = live_sb
        extra_frames.append(frame)
        if live_sb.get("is_at_bottom"):
            completion["completed"] = True
            break
    return extra_frames, completion


# ---------------------------------------------------------------------------
# Confirm detection
# ---------------------------------------------------------------------------

def _detect_confirm_button(screenshot=None):
    """Check whether the confirm button is visible after an increment click."""
    if screenshot is None:
        screenshot = _capture_live_skill_screenshot()
    matches = device_action.match_template(
        _CONFIRM_TEMPLATE,
        screenshot,
        threshold=_CONFIRM_THRESHOLD,
    )
    return {
        "detected": len(matches) > 0,
        "matches": [list(m) for m in matches],
    }


def _click_confirm_button(confirm_result):
    """Click the confirm button using the detected match coordinates."""
    if not confirm_result or not confirm_result.get("detected"):
        return {"clicked": False, "reason": "confirm_not_detected"}
    match = confirm_result["matches"][0]
    ui_left, ui_top, _, _ = [int(v) for v in _skill_ui_region()]
    click_x = ui_left + match[0] + match[2] // 2
    click_y = ui_top + match[1] + match[3] // 2
    info(f"[SKILL] Clicking confirm at ({click_x}, {click_y})...")
    device_action.click(target=(click_x, click_y), duration=0.15)
    sleep(0.5)
    return {"clicked": True, "target": [click_x, click_y]}


def _click_learn_and_close():
    """After confirm, click learn → close to finalize the skill purchase.

    Returns a dict describing what was clicked.
    """
    result = {
        "learn_clicked": False,
        "close_clicked": False,
        "timing": {},
    }
    t0 = _time()

    if (constants.SCENARIO_NAME or "") in ("mant", "trackblazer"):
        learned_template = constants.TRACKBLAZER_SKILL_UI_TEMPLATES.get("skills_learned")
        close_template = constants.TRACKBLAZER_SKILL_UI_TEMPLATES.get("skills_learned_close")
        deadline = _time() + get_secs(5)
        learned_detected = False
        close_clicked = False
        popup_visible_since = None
        t_popup = _time()
        while _time() < deadline:
            screenshot = _capture_live_skill_screenshot()
            popup_visible = False
            if learned_template:
                learned_matches = device_action.match_template(
                    learned_template,
                    screenshot,
                    threshold=_TRACKBLAZER_SKILLS_LEARNED_THRESHOLD,
                    template_scaling=_INVERSE_GLOBAL_SCALE,
                )
                if learned_matches:
                    learned_detected = True
                    popup_visible = True
            if close_template:
                close_matches = device_action.match_template(
                    close_template,
                    screenshot,
                    threshold=_TRACKBLAZER_SKILLS_LEARNED_CLOSE_THRESHOLD,
                    template_scaling=_INVERSE_GLOBAL_SCALE,
                )
                if close_matches:
                    popup_visible = True
            else:
                close_matches = []
            if popup_visible:
                if popup_visible_since is None:
                    popup_visible_since = _time()
            else:
                popup_visible_since = None
            popup_ready = (
                popup_visible_since is not None
                and (_time() - popup_visible_since) >= _TRACKBLAZER_SKILLS_LEARNED_CLOSE_DELAY_SECONDS
            )
            if popup_ready and close_matches:
                ui_left, ui_top, _, _ = [int(v) for v in _skill_ui_region()]
                match = close_matches[0]
                click_x = ui_left + match[0] + match[2] // 2
                click_y = ui_top + match[1] + match[3] // 2
                info(f"[SKILL] Trackblazer learned popup close at ({click_x}, {click_y})...")
                device_action.click(target=(click_x, click_y), duration=0.15)
                close_clicked = True
                break
            sleep(0.15)
        result["learn_clicked"] = bool(learned_detected or close_clicked)
        result["close_clicked"] = bool(close_clicked)
        result["timing"]["learned_popup"] = round(_time() - t_popup, 4)
        if close_clicked:
            sleep(_CLOSE_SETTLE_SECONDS_POST_LEARN)
            result["timing"]["close"] = round(_CLOSE_SETTLE_SECONDS_POST_LEARN, 4)
        else:
            warning("[SKILL] Trackblazer learned popup close button not found after confirm; falling back to generic learn/close flow.")
            result["timing"]["close"] = 0.0
        result["timing"]["total"] = round(_time() - t0, 4)
        if close_clicked:
            return result

    # Click learn button.
    learn_clicked = device_action.locate_and_click(
        _LEARN_TEMPLATE,
        min_search_time=get_secs(2),
    )
    result["learn_clicked"] = bool(learn_clicked)
    result["timing"]["learn"] = round(_time() - t0, 4)
    if not learn_clicked:
        warning("[SKILL] Learn button not found after confirm.")
        return result
    sleep(_LEARN_SETTLE_SECONDS)

    # Click close button (the post-learn summary dialog).
    t_close = _time()
    close_clicked = device_action.locate_and_click(
        _CLOSE_TEMPLATE,
        min_search_time=get_secs(2),
    )
    result["close_clicked"] = bool(close_clicked)
    result["timing"]["close"] = round(_time() - t_close, 4)
    if close_clicked:
        sleep(_CLOSE_SETTLE_SECONDS_POST_LEARN)
    else:
        warning("[SKILL] Close button not found after learn.")

    result["timing"]["total"] = round(_time() - t0, 4)
    return result


# ---------------------------------------------------------------------------
# Main scan + purchase flow
# ---------------------------------------------------------------------------

def scan_and_increment_skill(target_skill=None, skill_shortlist=None, dry_run=True,
                             save_debug_frames=False, debug_session_name=None):
    """Continuous-drag skill purchase flow with post-drag waterfall execution.

    1. Detect scrollbar on the open skills page.
    2. Reset to top if needed.
    3. Capture + analyze initial still frame at top.
    4. Run a full continuous drag scan (top→bottom) with background analysis.
    5. After drag finishes, begin seek-back as soon as a candidate is available
       (remaining analysis continues in the background).
    6. Seek back near the saved scrollbar ratio for that frame.
    7. Reacquire the target live, click its increment button.
    8. Detect confirm availability (do NOT click confirm in Phase 1).

    Returns a flow result dict with timing and debug output.
    """
    flow = {
        "target_skill": target_skill,
        "skill_shortlist": skill_shortlist or [],
        "scan_timing": {},
        "scrollbar_initial": None,
        "scrollbar_reset": None,
        "drag_result": None,
        "bottom_completion_result": None,
        "all_frames": [],
        "frame_signatures_seen": 0,
        "frame_signatures_unique": 0,
        "target_found": False,
        "target_frame_index": None,
        "target_scrollbar_ratio": None,
        "target_row": None,
        "seekback_result": None,
        "reacquire_result": None,
        "increment_click_result": None,
        "confirm_detect_result": None,
        "confirm_available": False,
        "reason": "",
    }

    # Build the shortlist — use config if not explicitly provided.
    if not skill_shortlist:
        skill_shortlist = list(getattr(config, "SKILL_LIST", []))
    if target_skill and target_skill not in skill_shortlist:
        skill_shortlist = [target_skill] + skill_shortlist
    flow["skill_shortlist"] = skill_shortlist
    normalized_shortlist = _normalize_skill_shortlist(skill_shortlist)

    if not skill_shortlist:
        flow["reason"] = "no_skill_shortlist_configured"
        warning("Skill scanner: no skill shortlist configured.")
        return flow

    t_flow = _time()
    debug_session_dir = (
        _ensure_skill_runtime_debug_dir(
            debug_session_name or f"skill_purchase_single_{int(_time() * 1000)}"
        )
        if save_debug_frames
        else None
    )
    live_index = _LiveAnalysisIndex()

    try:
        # --- Step 1: Detect scrollbar ---
        info("Skill scanner: detecting scrollbar...")
        scrollbar = inspect_skill_scrollbar()
        flow["scrollbar_initial"] = scrollbar
        if not scrollbar.get("detected"):
            flow["reason"] = "scrollbar_not_detected"
            warning("Skill scanner: scrollbar not detected on skills page.")
            return flow
        info(f"Skill scanner: scrollbar detected, ratio={scrollbar.get('position_ratio')}, "
             f"scrollable={scrollbar.get('scrollable')}")

        # --- Step 2: Reset to top ---
        if not scrollbar.get("is_at_top"):
            info("Skill scanner: resetting scrollbar to top...")
            reset_result = _drag_skill_scrollbar(scrollbar, edge="top")
            flow["scrollbar_reset"] = reset_result
            sleep(0.3)
            scrollbar = inspect_skill_scrollbar()
            info(f"Skill scanner: after reset, ratio={scrollbar.get('position_ratio')}")
        else:
            info("Skill scanner: already at top.")

        # --- Step 3: Capture initial still frame at top ---
        info("Skill scanner: capturing initial still frame at top...")
        initial_screenshot = _capture_live_skill_screenshot()
        initial_frame = _analyze_skill_frame({
            "index": -1,
            "elapsed": 0.0,
            "capture_elapsed": 0.0,
            "screenshot": initial_screenshot,
            "skill_shortlist": skill_shortlist,
            "normalized_shortlist": normalized_shortlist,
        })
        if debug_session_dir is not None:
            _save_skill_runtime_debug_frame(
                debug_session_dir,
                "initial_frame_top",
                initial_screenshot,
                frame_summary={
                    "index": -1,
                    "elapsed": 0.0,
                    "scrollbar_ratio": ((initial_frame.get("scrollbar") or {}).get("position_ratio")),
                    "ocr_rows_count": initial_frame.get("ocr_rows_count", 0),
                    "matched_names": [m.get("match_name") for m in (initial_frame.get("matched_targets") or [])],
                    "increment_count": initial_frame.get("increment_count", 0),
                    "timing": initial_frame.get("timing"),
                },
            )
        live_index.append(initial_frame)

        # --- Step 4: Full continuous drag scan (analysis runs in background) ---
        if scrollbar.get("scrollable"):
            info(f"Skill scanner: starting continuous drag scan, shortlist={skill_shortlist}...")
            drag_result = _capture_skill_frames_during_scrollbar_drag(
                scrollbar,
                skill_shortlist,
                normalized_shortlist=normalized_shortlist,
                debug_session_dir=debug_session_dir,
                live_index=live_index,
            )
            flow["drag_result"] = {
                "stop_reason": drag_result.get("stop_reason"),
                "swiped": drag_result.get("swiped"),
                "timing": drag_result.get("timing"),
            }
            if drag_result.get("stop_reason") != "scrollbar_bottom_reached":
                info("Skill scanner: drag ended before bottom detection, forcing bottom completion...")
                extra_frames, bottom_completion = _complete_scan_to_bottom(
                    skill_shortlist,
                    normalized_shortlist=normalized_shortlist,
                    start_index=max(1, len(live_index.get_frames())),
                    debug_session_dir=debug_session_dir,
                )
                flow["bottom_completion_result"] = bottom_completion
                for frame in extra_frames:
                    live_index.append(frame)
        else:
            info("Skill scanner: list is not scrollable, only initial frame available.")
            live_index.mark_done()

        t_scan_done = _time()

        # --- Step 5: Waterfall — begin seek-back as soon as candidate available ---
        best_candidate = live_index.wait_for_candidate(target_skill, timeout=15.0)
        if not best_candidate:
            # All analysis done but target not found.
            live_index.wait_done(timeout=10.0)
            best_candidate = live_index.find_best_candidate(target_skill)

        info(f"Skill scanner: drag+analysis phase wall={round(_time() - t_flow, 2)}s, "
             f"analyzed={len(live_index.get_frames())} frames")

        if not best_candidate:
            flow["reason"] = "target_not_found_in_any_frame"
            flow["scan_timing"] = _build_scan_timing(t_flow, t_scan_done)
            _log_scan_debug(flow, live_index.get_frames())
            info(f"Skill scanner: target skill not found. Scanned shortlist: {skill_shortlist}")
            return flow

        flow["target_found"] = True
        flow["target_frame_index"] = best_candidate["frame_index"]
        flow["target_scrollbar_ratio"] = best_candidate["scrollbar_ratio"]
        flow["target_row"] = best_candidate["row"]
        info(f"Skill scanner: best candidate '{best_candidate['row']['match_name']}' "
             f"in frame {best_candidate['frame_index']} "
             f"(ratio={best_candidate['scrollbar_ratio']}, "
             f"score={best_candidate['row']['match_score']}, "
             f"has_inc={best_candidate['row'].get('increment_match') is not None})")

        # Skip reacquire if scan determined this skill has an "Obtained" badge.
        if best_candidate["row"].get("obtained") and not _match_has_safe_increment(best_candidate["row"]):
            flow["reason"] = "target_obtained_no_increment"
            flow["scan_timing"] = _build_scan_timing(t_flow, t_scan_done)
            info("Skill scanner: target has Obtained badge, no increment button available.")
            return flow

        # --- Step 6: Seek back near saved scrollbar ratio ---
        t_seekback = _time()
        reacquire_match, reacquire_screenshot, seek_result, reacquire_result = _reacquire_skill_candidate(
            target_skill,
            skill_shortlist,
            best_candidate,
            t_flow,
            normalized_shortlist=normalized_shortlist,
        )
        flow["seekback_result"] = seek_result
        flow["reacquire_result"] = reacquire_result
        if flow["seekback_result"] is None and best_candidate["frame_index"] != -1:
            warning("Skill scanner: scrollbar lost before seek-back.")
            flow["reason"] = "scrollbar_lost_before_seekback"
            flow["scan_timing"] = _build_scan_timing(t_flow, t_scan_done, t_seekback)
            return flow

        if not reacquire_match:
            flow["reason"] = "target_found_in_scan_but_reacquire_failed"
            flow["scan_timing"] = _build_scan_timing(t_flow, t_scan_done, t_seekback)
            info("Skill scanner: could not reacquire target after seek-back.")
            _log_scan_debug(flow, live_index.get_frames())
            return flow

        if not reacquire_match.get("increment_match"):
            flow["reason"] = _classify_no_increment_row(reacquire_match, reacquire_screenshot)
            flow["scan_timing"] = _build_scan_timing(t_flow, t_scan_done, t_seekback)
            if flow["reason"] == "target_obtained_no_increment":
                info("Skill scanner: target reacquired with Obtained badge and no increment.")
            else:
                info("Skill scanner: target reacquired but no increment button paired live.")
            _log_scan_debug(flow, live_index.get_frames())
            return flow
        if not _match_has_safe_increment(reacquire_match):
            flow["reason"] = "target_reacquired_but_increment_pair_unsafe"
            flow["scan_timing"] = _build_scan_timing(t_flow, t_scan_done, t_seekback)
            info("Skill scanner: target reacquired but increment pairing was not safe enough to click.")
            _log_scan_debug(flow, live_index.get_frames())
            return flow

        info(f"Skill scanner: target reacquired! score={reacquire_match['match_score']}, "
             f"increment paired.")

        # --- Step 8: Click increment and detect confirm ---
        return _do_increment_and_confirm(flow, reacquire_match, reacquire_screenshot, dry_run,
                                         t_flow, t_scan_done, t_seekback)
    finally:
        _finalize_waterfall_summary(flow, live_index)


# ---------------------------------------------------------------------------
# Candidate selection from indexed frames
# ---------------------------------------------------------------------------

def _choose_best_candidate(all_frames, target_skill):
    """Choose the best target match across all indexed frames.

    Prefers: has increment > higher match score > earlier frame.
    Returns None if no match found.
    """
    candidates = []
    for frame in all_frames:
        match = _find_best_target_match(frame, target_skill)
        if match:
            sb = frame.get("scrollbar") or {}
            candidates.append({
                "frame_index": frame.get("index", 0),
                "scrollbar_ratio": sb.get("position_ratio"),
                "row": match,
            })

    if not candidates:
        return None

    # Only consider candidates that have an increment button paired.
    # No increment means the skill is already learned / not actionable.
    actionable = [c for c in candidates if _match_has_safe_increment(c["row"])]
    if not actionable:
        actionable = [c for c in candidates if c["row"].get("increment_match")]
    if not actionable:
        return max(
            candidates,
            key=lambda c: (
                float((c.get("row") or {}).get("match_score") or 0.0),
                float((c.get("row") or {}).get("confidence") or 0.0),
                _name_match_method_rank((c.get("row") or {}).get("name_match_method")),
                _ocr_variant_rank((c.get("row") or {}).get("ocr_variant")),
            ),
        )

    # Sort by shortlist similarity first, then OCR confidence.
    actionable.sort(
        key=lambda c: (
            float((c.get("row") or {}).get("match_score") or 0.0),
            float((c.get("row") or {}).get("confidence") or 0.0),
            _name_match_method_rank((c.get("row") or {}).get("name_match_method")),
            _ocr_variant_rank((c.get("row") or {}).get("ocr_variant")),
        ),
        reverse=True,
    )
    return actionable[0]


def _build_reacquire_result(frame, match, seek_ratio=None, nudge_attempts=None):
    scrollbar = (frame or {}).get("scrollbar") or {}
    matched_targets = (frame or {}).get("matched_targets") or []
    return {
        "ocr_rows_count": (frame or {}).get("ocr_rows_count", 0),
        "matched_targets_count": len(matched_targets),
        "target_reacquired": match is not None,
        "target_has_increment": bool(match and match.get("increment_match")),
        "target_increment_safe": bool(_match_has_safe_increment(match)),
        "match_name": (match or {}).get("match_name"),
        "match_score": (match or {}).get("match_score"),
        "ocr_variant_used": (match or {}).get("ocr_variant"),
        "chosen_variant": (match or {}).get("chosen_variant") or (match or {}).get("ocr_variant"),
        "ocr_variants_seen": list((match or {}).get("ocr_variants_seen") or []),
        "name_match_method": (match or {}).get("name_match_method") or (match or {}).get("match_type"),
        "consensus_score": (match or {}).get("consensus_score"),
        "token_evidence": dict((match or {}).get("token_evidence") or {}),
        "reject_reason": (match or {}).get("reject_reason") or "",
        "obtained_evidence": (match or {}).get("obtained_evidence") or ("template" if (match or {}).get("obtained") else "none"),
        "increment_pairing": (match or {}).get("increment_pairing"),
        "increment_vertical_distance": (match or {}).get("increment_vertical_distance"),
        "increment_present": bool((match or {}).get("increment_match")),
        "scrollbar_ratio": scrollbar.get("position_ratio"),
        "seek_ratio": seek_ratio,
        "nudge_attempts": list(nudge_attempts or []),
    }


def _click_increment_for_match(target_match, dry_run):
    """Click or preview-click the increment button for a matched skill row."""
    inc = target_match.get("increment_match")
    if not inc:
        return {
            "clicked": False,
            "dry_run": bool(dry_run),
            "target": None,
            "increment_match": None,
        }
    if not _match_has_safe_increment(target_match):
        return {
            "clicked": False,
            "dry_run": bool(dry_run),
            "target": None,
            "increment_match": list(inc),
            "reason": "unsafe_increment_pairing",
            "increment_pairing": target_match.get("increment_pairing"),
            "increment_vertical_distance": target_match.get("increment_vertical_distance"),
        }
    scroll_left, scroll_top, _, _ = [int(v) for v in constants.SCROLLING_SKILL_SCREEN_BBOX]
    click_x = scroll_left + inc[0] + inc[2] // 2
    click_y = scroll_top + inc[1] + inc[3] // 2
    if dry_run:
        info(f"Skill scanner: [DRY RUN] would click increment for '{target_match['match_name']}' "
             f"at ({click_x}, {click_y})")
        return {
            "clicked": False,
            "dry_run": True,
            "target": [click_x, click_y],
            "increment_match": list(inc),
            "increment_pairing": target_match.get("increment_pairing"),
            "increment_vertical_distance": target_match.get("increment_vertical_distance"),
        }
    info(f"Skill scanner: clicking increment for '{target_match['match_name']}' "
         f"at ({click_x}, {click_y})...")
    device_action.click(target=(click_x, click_y), duration=0.15)
    sleep(0.5)
    return {
        "clicked": True,
        "dry_run": False,
        "target": [click_x, click_y],
        "increment_match": list(inc),
        "increment_pairing": target_match.get("increment_pairing"),
        "increment_vertical_distance": target_match.get("increment_vertical_distance"),
    }


def _reacquire_skill_candidate(target_skill, skill_shortlist, candidate, t_flow, normalized_shortlist=None):
    """Seek back to an indexed candidate and reacquire it live.

    Bias slightly above the indexed ratio, then search downward first. That
    matches the observed behavior better than landing slightly low and needing
    to reverse direction immediately.
    """
    seek_result = None
    candidate_ratio = candidate.get("scrollbar_ratio")
    seek_ratio = None
    if candidate.get("frame_index") == -1:
        current_sb = inspect_skill_scrollbar()
        if current_sb.get("detected") and not current_sb.get("is_at_top"):
            seek_result = _drag_skill_scrollbar(current_sb, edge="top")
            sleep(_SCROLLBAR_SEEKBACK_SETTLE_SECONDS)
        reacquire_screenshot = _capture_live_skill_screenshot()
        reacquire_frame = _analyze_skill_frame({
            "index": -99,
            "elapsed": round(_time() - t_flow, 4),
            "capture_elapsed": 0.0,
            "screenshot": reacquire_screenshot,
            "skill_shortlist": skill_shortlist,
            "normalized_shortlist": normalized_shortlist,
        })
        reacquire_match = _find_best_target_match(reacquire_frame, target_skill)
        reacquire_result = _build_reacquire_result(reacquire_frame, reacquire_match)
        return reacquire_match, reacquire_screenshot, seek_result, reacquire_result

    current_sb = inspect_skill_scrollbar()
    if not current_sb.get("detected"):
        return None, None, None, {
            "target_reacquired": False,
            "target_has_increment": False,
            "scrollbar_ratio": None,
            "seek_ratio": None,
            "nudge_attempts": [],
            "reason": "scrollbar_lost_before_seekback",
        }

    seek_ratio = min(1.0, max(0.0, float(candidate_ratio or 0.0) - _SCROLLBAR_SEEKBACK_BUFFER_RATIO))
    seek_result = _drag_skill_scrollbar_to_ratio(current_sb, seek_ratio)
    sleep(_SCROLLBAR_SEEKBACK_SETTLE_SECONDS)

    reacquire_screenshot = _capture_live_skill_screenshot()
    reacquire_frame = _analyze_skill_frame({
        "index": -99,
        "elapsed": round(_time() - t_flow, 4),
        "capture_elapsed": 0.0,
        "screenshot": reacquire_screenshot,
        "skill_shortlist": skill_shortlist,
        "normalized_shortlist": normalized_shortlist,
    })
    reacquire_match = _find_best_target_match(reacquire_frame, target_skill)
    nudge_attempts = []
    if reacquire_match and reacquire_match.get("increment_match"):
        reacquire_result = _build_reacquire_result(
            reacquire_frame,
            reacquire_match,
            seek_ratio=seek_ratio,
            nudge_attempts=nudge_attempts,
        )
        return reacquire_match, reacquire_screenshot, seek_result, reacquire_result

    for nudge_direction, nudge_delta in [("down", 0.02), ("down2", 0.04), ("down3", 0.06), ("up", -0.02), ("up2", -0.05)]:
        current_sb = inspect_skill_scrollbar()
        if not current_sb.get("detected"):
            break
        current_ratio = current_sb.get("position_ratio", seek_ratio if seek_ratio is not None else 0.5)
        nudge_ratio = min(1.0, max(0.0, current_ratio + nudge_delta))
        debug(f"Skill scanner: nudge {nudge_direction} to ratio={nudge_ratio:.3f}")
        _drag_skill_scrollbar_to_ratio(current_sb, nudge_ratio)
        sleep(_SCROLLBAR_REACQUIRE_SETTLE_SECONDS)
        screenshot = _capture_live_skill_screenshot()
        frame = _analyze_skill_frame({
            "index": -98,
            "elapsed": round(_time() - t_flow, 4),
            "capture_elapsed": 0.0,
            "screenshot": screenshot,
            "skill_shortlist": skill_shortlist,
            "normalized_shortlist": normalized_shortlist,
        })
        match = _find_best_target_match(frame, target_skill)
        nudge_attempts.append({
            "direction": nudge_direction,
            "target_ratio": round(nudge_ratio, 4),
            "matched": bool(match),
            "has_increment": bool(match and match.get("increment_match")),
            "scrollbar_ratio": ((frame.get("scrollbar") or {}).get("position_ratio")),
        })
        if match and match.get("increment_match"):
            reacquire_result = _build_reacquire_result(
                frame,
                match,
                seek_ratio=seek_ratio,
                nudge_attempts=nudge_attempts,
            )
            info(f"Skill scanner: reacquired via nudge {nudge_direction}!")
            return match, screenshot, seek_result, reacquire_result
        if match:
            reacquire_screenshot = screenshot
            reacquire_frame = frame
            reacquire_match = match

    reacquire_result = _build_reacquire_result(
        reacquire_frame,
        reacquire_match,
        seek_ratio=seek_ratio,
        nudge_attempts=nudge_attempts,
    )
    return reacquire_match, reacquire_screenshot, seek_result, reacquire_result


def _find_best_target_match(frame, target_skill):
    """Find the target skill match in a frame.

    When target_skill is specified, ONLY return a match for that specific skill.
    """
    matched = frame.get("matched_targets", [])
    if not matched:
        return None
    def _match_rank(match):
        return (
            1 if _match_has_safe_increment(match) else 0,
            1 if match.get("increment_match") else 0,
            float(match.get("match_score") or 0.0),
            float(match.get("confidence") or 0.0),
            _name_match_method_rank(match.get("name_match_method")),
            _ocr_variant_rank(match.get("ocr_variant")),
        )
    if target_skill:
        target_normalized = _normalize_skill_text(target_skill)
        target_matches = [
            m for m in matched
            if _normalize_skill_text(m.get("match_name", "")) == target_normalized
        ]
        if not target_matches:
            return None
        return max(target_matches, key=_match_rank)
    # No specific target — return highest-scoring match.
    return max(matched, key=_match_rank)


# ---------------------------------------------------------------------------
# Seek-back nudge for reacquisition
# ---------------------------------------------------------------------------

def _nudge_and_reacquire(target_skill, skill_shortlist, flow, t_flow, normalized_shortlist=None):
    """Try small scrollbar nudges up/down to reacquire a target after seek-back."""
    for nudge_direction, nudge_delta in [("up", -0.03), ("down", 0.03), ("up2", -0.06)]:
        current_sb = inspect_skill_scrollbar()
        if not current_sb.get("detected"):
            break
        current_ratio = current_sb.get("position_ratio", 0.5)
        nudge_ratio = min(1.0, max(0.0, current_ratio + nudge_delta))
        debug(f"Skill scanner: nudge {nudge_direction} to ratio={nudge_ratio:.3f}")
        _drag_skill_scrollbar_to_ratio(current_sb, nudge_ratio)
        sleep(_SCROLLBAR_REACQUIRE_SETTLE_SECONDS)
        screenshot = _capture_live_skill_screenshot()
        frame = _analyze_skill_frame({
            "index": -98,
            "elapsed": round(_time() - t_flow, 4),
            "capture_elapsed": 0.0,
            "screenshot": screenshot,
            "skill_shortlist": skill_shortlist,
            "normalized_shortlist": normalized_shortlist,
        })
        match = _find_best_target_match(frame, target_skill)
        if match and match.get("increment_match"):
            info(f"Skill scanner: reacquired via nudge {nudge_direction}!")
            return match
        elif match:
            info(f"Skill scanner: OCR'd target via nudge {nudge_direction} but no increment.")
    return None


# ---------------------------------------------------------------------------
# Increment click + confirm detection
# ---------------------------------------------------------------------------

def _do_increment_and_confirm(flow, target_match, screenshot, dry_run, t_flow,
                               t_scan_done=None, t_seekback=None):
    """Click increment for the matched target and detect confirm."""
    inc = target_match.get("increment_match")
    if not inc:
        flow["reason"] = "target_found_but_no_increment_paired"
        info("Skill scanner: target found but no increment button paired.")
        flow["scan_timing"] = _build_scan_timing(t_flow, t_scan_done, t_seekback)
        return flow

    flow["increment_click_result"] = _click_increment_for_match(target_match, dry_run)
    if not flow["increment_click_result"].get("target"):
        flow["reason"] = flow["increment_click_result"].get("reason") or "increment_click_not_ready"
        flow["scan_timing"] = _build_scan_timing(t_flow, t_scan_done, t_seekback)
        return flow

    # Detect confirm button.
    info("Skill scanner: checking for confirm button...")
    confirm = _detect_confirm_button()
    flow["confirm_detect_result"] = confirm
    flow["confirm_available"] = confirm.get("detected", False)

    if dry_run:
        if confirm.get("detected"):
            info("Skill scanner: [DRY RUN] confirm button detected (not clicking).")
            flow["reason"] = "dry_run_confirm_detected"
        else:
            info("Skill scanner: [DRY RUN] confirm not detected (expected — did not click increment).")
            flow["reason"] = "dry_run_complete"
    elif confirm.get("detected"):
        # Live mode: click confirm → learn → close to finalize purchase.
        info("Skill scanner: confirm button detected, finalizing purchase...")
        flow["confirm_click_result"] = _click_confirm_button(confirm)
        learn_close = _click_learn_and_close()
        flow["learn_close_result"] = learn_close
        if learn_close.get("learn_clicked") and learn_close.get("close_clicked"):
            info(f"Skill scanner: purchase finalized for '{target_match.get('match_name')}'.")
            flow["reason"] = "purchase_finalized"
        elif learn_close.get("learn_clicked"):
            flow["reason"] = "purchase_learned_close_failed"
        else:
            flow["reason"] = "confirm_clicked_learn_failed"
    else:
        info("Skill scanner: confirm button NOT detected after increment.")
        flow["reason"] = "increment_clicked_confirm_not_detected"

    flow["scan_timing"] = _build_scan_timing(t_flow, t_scan_done, t_seekback)
    return flow


def _build_scan_entry(target_skill, source=None):
    return {
        "target_skill": target_skill,
        "source": source,
        "candidate": None,
        "seekback_result": None,
        "reacquire_result": None,
        "increment_click_result": None,
        "confirm_detect_result": None,
        "confirm_available": False,
        "ocr_variant_used": None,
        "chosen_variant": None,
        "ocr_variants_seen": [],
        "name_match_method": None,
        "consensus_score": None,
        "token_evidence": {},
        "reject_reason": "",
        "obtained_evidence": "none",
        "increment_present": False,
        "final_reason": "",
        "reason": "",
    }


def _sync_target_entry_telemetry(entry, match_row=None):
    if not isinstance(entry, dict):
        return entry
    row = match_row or {}
    if isinstance(row, dict):
        ocr_variant = row.get("ocr_variant") or row.get("ocr_variant_used")
        if ocr_variant:
            entry["ocr_variant_used"] = ocr_variant
        chosen_variant = row.get("chosen_variant") or ocr_variant
        if chosen_variant:
            entry["chosen_variant"] = chosen_variant
        if "ocr_variants_seen" in row:
            entry["ocr_variants_seen"] = list(row.get("ocr_variants_seen") or [])
        match_method = row.get("name_match_method") or row.get("match_type")
        if match_method:
            entry["name_match_method"] = match_method
        if "consensus_score" in row:
            entry["consensus_score"] = row.get("consensus_score")
        if "token_evidence" in row:
            entry["token_evidence"] = dict(row.get("token_evidence") or {})
        if "reject_reason" in row:
            entry["reject_reason"] = row.get("reject_reason") or ""
        obtained_evidence = row.get("obtained_evidence")
        if obtained_evidence:
            entry["obtained_evidence"] = obtained_evidence
        elif row.get("obtained"):
            entry["obtained_evidence"] = "template"
        elif not entry.get("obtained_evidence"):
            entry["obtained_evidence"] = "none"
        if "increment_match" in row:
            entry["increment_present"] = bool(row.get("increment_match"))
    click_result = entry.get("increment_click_result") or {}
    if click_result.get("target"):
        entry["increment_present"] = True
    return entry


def _log_target_entry_debug(entry):
    if not isinstance(entry, dict):
        return
    debug(
        "[SKILL][TARGET] "
        f"{entry.get('target_skill')}: "
        f"ocr_variant={entry.get('ocr_variant_used') or 'unknown'}; "
        f"variants_seen={entry.get('ocr_variants_seen') or []}; "
        f"name_match={entry.get('name_match_method') or 'unknown'}; "
        f"consensus={entry.get('consensus_score')}; "
        f"reject_reason={entry.get('reject_reason') or ''}; "
        f"obtained_evidence={entry.get('obtained_evidence') or 'none'}; "
        f"increment_present={bool(entry.get('increment_present'))}; "
        f"final_reason={entry.get('final_reason') or entry.get('reason') or ''}"
    )


def _set_target_entry_reason(entry, reason, match_row=None):
    if not isinstance(entry, dict):
        return
    _sync_target_entry_telemetry(entry, match_row=match_row)
    entry["reason"] = reason
    entry["final_reason"] = reason
    _log_target_entry_debug(entry)


def _build_hint_candidate(target_skill, hint):
    """Build a synthetic candidate payload from a prior preview scan hint."""
    if not isinstance(hint, dict):
        return None
    candidate = hint.get("candidate") or {}
    reacquire_result = hint.get("reacquire_result") or {}
    ratio = reacquire_result.get("scrollbar_ratio")
    if ratio is None:
        ratio = candidate.get("scrollbar_ratio")
    if ratio is None:
        return None
    frame_index = candidate.get("frame_index")
    if frame_index is None:
        frame_index = 0
    match_name = candidate.get("match_name") or reacquire_result.get("match_name") or target_skill
    match_score = candidate.get("match_score")
    if match_score is None:
        match_score = reacquire_result.get("match_score")
    increment_pairing = reacquire_result.get("increment_pairing")
    if increment_pairing is None:
        increment_pairing = candidate.get("increment_pairing")
    increment_vertical_distance = reacquire_result.get("increment_vertical_distance")
    if increment_vertical_distance is None:
        increment_vertical_distance = candidate.get("increment_vertical_distance")
    return {
        "frame_index": frame_index,
        "scrollbar_ratio": ratio,
        "row": {
            "match_name": match_name,
            "match_score": match_score,
            "increment_pairing": increment_pairing,
            "increment_vertical_distance": increment_vertical_distance,
            "ocr_variant": candidate.get("ocr_variant_used") or reacquire_result.get("ocr_variant_used"),
            "chosen_variant": candidate.get("chosen_variant") or reacquire_result.get("chosen_variant"),
            "ocr_variants_seen": list(candidate.get("ocr_variants_seen") or reacquire_result.get("ocr_variants_seen") or []),
            "name_match_method": candidate.get("name_match_method") or reacquire_result.get("name_match_method"),
            "consensus_score": candidate.get("consensus_score") if candidate.get("consensus_score") is not None else reacquire_result.get("consensus_score"),
            "token_evidence": dict(candidate.get("token_evidence") or reacquire_result.get("token_evidence") or {}),
            "reject_reason": candidate.get("reject_reason") or reacquire_result.get("reject_reason") or "",
            "obtained_evidence": candidate.get("obtained_evidence") or reacquire_result.get("obtained_evidence"),
        },
    }


def _normalize_target_hints(target_hints):
    """Index preview target_results by normalized target skill."""
    normalized = {}
    for hint in (target_hints or []):
        if not isinstance(hint, dict):
            continue
        target_skill = hint.get("target_skill")
        target_key = _normalize_skill_text(target_skill or "")
        if not target_key or target_key in normalized:
            continue
        normalized[target_key] = hint
    return normalized


def _queue_hint_targets(target_skills, target_hints, skill_shortlist, normalized_shortlist, dry_run, t_flow):
    """Try to reacquire targets directly from preview hints before full scanning."""
    flow = {
        "attempted": False,
        "reason": "",
        "resolved": 0,
        "unresolved": list(target_skills or []),
        "ordered_targets": [],
    }
    ordered_targets = list(target_skills or [])
    if not ordered_targets:
        flow["reason"] = "no_target_skills_configured"
        return [], [], flow

    hint_map = _normalize_target_hints(target_hints)
    hinted_targets = []
    unresolved = []
    for skill_name in ordered_targets:
        hint = hint_map.get(_normalize_skill_text(skill_name or ""))
        candidate = _build_hint_candidate(skill_name, hint)
        if candidate is None:
            unresolved.append(skill_name)
            continue
        hinted_targets.append((skill_name, hint, candidate))

    if not hinted_targets:
        flow["reason"] = "no_reusable_hints"
        return [], ordered_targets, flow

    flow["attempted"] = True
    current_sb = inspect_skill_scrollbar()
    if not current_sb.get("detected"):
        flow["reason"] = "scrollbar_not_detected"
        return [], ordered_targets, flow
    if not current_sb.get("is_at_top"):
        _drag_skill_scrollbar(current_sb, edge="top")
        sleep(_SCROLLBAR_SEEKBACK_SETTLE_SECONDS)

    hinted_targets.sort(key=lambda item: float(item[2].get("scrollbar_ratio") or 0.0))
    flow["ordered_targets"] = [
        {
            "target_skill": skill_name,
            "scrollbar_ratio": candidate.get("scrollbar_ratio"),
        }
        for skill_name, _hint, candidate in hinted_targets
    ]

    results = []
    for skill_name, hint, candidate in hinted_targets:
        entry = _build_scan_entry(skill_name, source="preview_hint")
        hint_candidate_row = candidate.get("row") or {}
        _sync_target_entry_telemetry(entry, match_row=hint_candidate_row)
        entry["candidate"] = {
            "frame_index": candidate.get("frame_index"),
            "scrollbar_ratio": candidate.get("scrollbar_ratio"),
            "match_name": hint_candidate_row.get("match_name"),
            "match_score": hint_candidate_row.get("match_score"),
            "increment_pairing": hint_candidate_row.get("increment_pairing"),
            "increment_vertical_distance": hint_candidate_row.get("increment_vertical_distance"),
            "ocr_variant_used": hint_candidate_row.get("ocr_variant"),
            "chosen_variant": hint_candidate_row.get("chosen_variant") or hint_candidate_row.get("ocr_variant"),
            "ocr_variants_seen": list(hint_candidate_row.get("ocr_variants_seen") or []),
            "name_match_method": hint_candidate_row.get("name_match_method"),
            "consensus_score": hint_candidate_row.get("consensus_score"),
            "token_evidence": dict(hint_candidate_row.get("token_evidence") or {}),
            "reject_reason": hint_candidate_row.get("reject_reason") or "",
            "obtained_evidence": hint_candidate_row.get("obtained_evidence") or "none",
            "increment_ready": bool(_match_has_safe_increment(hint_candidate_row)),
        }
        entry["hint"] = {
            "candidate": hint.get("candidate"),
            "reacquire_result": hint.get("reacquire_result"),
        }

        reacquire_match, _reacquire_screenshot, seek_result, reacquire_result = _reacquire_skill_candidate(
            skill_name,
            skill_shortlist,
            candidate,
            t_flow,
            normalized_shortlist=normalized_shortlist,
        )
        entry["seekback_result"] = seek_result
        entry["reacquire_result"] = reacquire_result
        _sync_target_entry_telemetry(entry, match_row=reacquire_match)
        if not reacquire_match:
            _set_target_entry_reason(entry, "preview_hint_reacquire_failed")
            unresolved.append(skill_name)
            results.append(entry)
            continue
        if not reacquire_match.get("increment_match"):
            _set_target_entry_reason(
                entry,
                _classify_no_increment_row(reacquire_match, _reacquire_screenshot),
                match_row=reacquire_match,
            )
            if entry["reason"] != "target_obtained_no_increment":
                unresolved.append(skill_name)
            results.append(entry)
            continue
        if not _match_has_safe_increment(reacquire_match):
            _set_target_entry_reason(entry, "preview_hint_increment_pair_unsafe", match_row=reacquire_match)
            unresolved.append(skill_name)
            results.append(entry)
            continue

        entry["increment_click_result"] = _click_increment_for_match(reacquire_match, dry_run)
        _sync_target_entry_telemetry(entry, match_row=reacquire_match)
        if entry["increment_click_result"].get("target"):
            flow["resolved"] += 1
        else:
            _set_target_entry_reason(
                entry,
                entry["increment_click_result"].get("reason") or "preview_hint_increment_click_not_ready",
                match_row=reacquire_match,
            )
            unresolved.append(skill_name)
        results.append(entry)

    flow["unresolved"] = list(unresolved)
    flow["reason"] = (
        "preview_hints_complete"
        if not unresolved
        else ("preview_hints_partial" if flow["resolved"] else "preview_hints_failed")
    )
    return results, unresolved, flow


def _finalize_multi_increment_results(target_results, dry_run):
    """Finalize a queued multi-skill purchase once all increments are selected."""
    summary = {
        "confirm_detect_result": None,
        "confirm_available": False,
        "confirm_click_result": None,
        "learn_close_result": None,
        "queued_targets": [],
        "reason": "",
    }
    queued_entries = [
        entry for entry in (target_results or [])
        if (entry.get("increment_click_result") or {}).get("target")
    ]
    summary["queued_targets"] = [entry.get("target_skill") for entry in queued_entries if entry.get("target_skill")]
    if not queued_entries:
        summary["reason"] = "no_targets_incremented"
        return summary

    confirm = _detect_confirm_button()
    summary["confirm_detect_result"] = confirm
    summary["confirm_available"] = bool(confirm.get("detected"))
    for entry in queued_entries:
        entry["confirm_detect_result"] = confirm
        entry["confirm_available"] = bool(confirm.get("detected"))

    if dry_run:
        reason = "dry_run_confirm_detected" if confirm.get("detected") else "dry_run_complete"
        for entry in queued_entries:
            if not entry.get("reason"):
                _set_target_entry_reason(entry, reason)
        summary["reason"] = reason
        return summary

    if not confirm.get("detected"):
        for entry in queued_entries:
            if not entry.get("reason"):
                _set_target_entry_reason(entry, "increment_clicked_confirm_not_detected")
        summary["reason"] = "increment_clicked_confirm_not_detected"
        return summary

    summary["confirm_click_result"] = _click_confirm_button(confirm)
    learn_close = _click_learn_and_close()
    summary["learn_close_result"] = learn_close
    for entry in queued_entries:
        entry["confirm_click_result"] = summary["confirm_click_result"]
        entry["learn_close_result"] = learn_close
    if learn_close.get("learn_clicked") and learn_close.get("close_clicked"):
        for entry in queued_entries:
            if not entry.get("reason"):
                _set_target_entry_reason(entry, "purchase_finalized")
        summary["reason"] = "purchase_finalized"
    elif learn_close.get("learn_clicked"):
        for entry in queued_entries:
            if not entry.get("reason"):
                _set_target_entry_reason(entry, "purchase_learned_close_failed")
        summary["reason"] = "purchase_learned_close_failed"
    else:
        for entry in queued_entries:
            if not entry.get("reason"):
                _set_target_entry_reason(entry, "confirm_clicked_learn_failed")
        summary["reason"] = "confirm_clicked_learn_failed"
    return summary


def scan_and_increment_skills(target_skills, dry_run=False,
                              save_debug_frames=False, debug_session_name=None,
                              target_hints=None):
    """Scan once, then seek back and increment multiple skills in top-to-bottom order.

    Post-drag waterfall execution: after one uninterrupted drag scan, targets
    are sorted by scrollbar_ratio (ascending) and processed top-to-bottom.
    Background analysis continues while earlier targets are being actioned.
    """
    ordered_targets = []
    seen = set()
    for skill_name in (target_skills or []):
        normalized = _normalize_skill_text(skill_name)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered_targets.append(skill_name)

    flow = {
        "target_skills": ordered_targets,
        "target_hints_used": False,
        "shortcut_result": None,
        "finalize_result": None,
        "scan_timing": {},
        "scrollbar_initial": None,
        "scrollbar_reset": None,
        "drag_result": None,
        "bottom_completion_result": None,
        "all_frames": [],
        "frame_signatures_seen": 0,
        "frame_signatures_unique": 0,
        "target_results": [],
        "reason": "",
    }
    if not ordered_targets:
        flow["reason"] = "no_target_skills_configured"
        return flow

    skill_shortlist = list(ordered_targets)
    normalized_shortlist = _normalize_skill_shortlist(skill_shortlist)
    t_flow = _time()
    debug_session_dir = (
        _ensure_skill_runtime_debug_dir(
            debug_session_name or f"skill_purchase_multi_{int(_time() * 1000)}"
        )
        if save_debug_frames
        else None
    )
    live_index = _LiveAnalysisIndex()

    try:
        if target_hints:
            hint_results, unresolved_targets, shortcut_result = _queue_hint_targets(
                ordered_targets,
                target_hints,
                skill_shortlist,
                normalized_shortlist,
                dry_run,
                t_flow,
            )
            flow["target_hints_used"] = bool((shortcut_result or {}).get("attempted"))
            flow["shortcut_result"] = shortcut_result
            flow["target_results"].extend(hint_results)
        else:
            unresolved_targets = list(ordered_targets)

        if not unresolved_targets:
            t_scan_done = _time()
            flow["finalize_result"] = _finalize_multi_increment_results(flow["target_results"], dry_run)
            flow["scan_timing"] = _build_scan_timing(t_flow, t_scan_done, t_scan_done)
            succeeded = [
                entry for entry in flow["target_results"]
                if (entry.get("increment_click_result") or {}).get("target")
            ]
            flow["reason"] = (
                flow["finalize_result"].get("reason")
                or ("multi_increments_processed" if succeeded else "no_targets_incremented")
            )
            return flow

        info(f"Skill scanner: detecting scrollbar for multi-target run {ordered_targets}...")
        scrollbar = inspect_skill_scrollbar()
        flow["scrollbar_initial"] = scrollbar
        if not scrollbar.get("detected"):
            flow["reason"] = "scrollbar_not_detected"
            return flow
        if not scrollbar.get("is_at_top"):
            flow["scrollbar_reset"] = _drag_skill_scrollbar(scrollbar, edge="top")
            sleep(0.3)
            scrollbar = inspect_skill_scrollbar()

        initial_screenshot = _capture_live_skill_screenshot()
        initial_frame = _analyze_skill_frame({
            "index": -1,
            "elapsed": 0.0,
            "capture_elapsed": 0.0,
            "screenshot": initial_screenshot,
            "skill_shortlist": skill_shortlist,
        })
        if debug_session_dir is not None:
            _save_skill_runtime_debug_frame(
                debug_session_dir,
                "initial_frame_top",
                initial_screenshot,
                frame_summary={
                    "index": -1,
                    "elapsed": 0.0,
                    "scrollbar_ratio": ((initial_frame.get("scrollbar") or {}).get("position_ratio")),
                    "ocr_rows_count": initial_frame.get("ocr_rows_count", 0),
                    "matched_names": [m.get("match_name") for m in (initial_frame.get("matched_targets") or [])],
                    "increment_count": initial_frame.get("increment_count", 0),
                    "timing": initial_frame.get("timing"),
                },
            )
        live_index.append(initial_frame)

        if scrollbar.get("scrollable"):
            drag_result = _capture_skill_frames_during_scrollbar_drag(
                scrollbar,
                skill_shortlist,
                normalized_shortlist=normalized_shortlist,
                drag_duration=6.0,
                frame_interval=0.22,
                debug_session_dir=debug_session_dir,
                live_index=live_index,
            )
            flow["drag_result"] = {
                "stop_reason": drag_result.get("stop_reason"),
                "swiped": drag_result.get("swiped"),
                "timing": drag_result.get("timing"),
            }
            if drag_result.get("stop_reason") != "scrollbar_bottom_reached":
                extra_frames, bottom_completion = _complete_scan_to_bottom(
                    skill_shortlist,
                    normalized_shortlist=normalized_shortlist,
                    start_index=max(1, len(live_index.get_frames())),
                    debug_session_dir=debug_session_dir,
                )
                flow["bottom_completion_result"] = bottom_completion
                for frame in extra_frames:
                    live_index.append(frame)
        else:
            live_index.mark_done()

        t_scan_done = _time()

        # --- Waterfall: collect candidates, sort by scrollbar ratio (top-to-bottom) ---
        # Brief wait for early analysis results before sorting.
        live_index.wait_done(timeout=2.0)

        found_candidates = []
        not_found_skills = []
        for skill_name in unresolved_targets:
            candidate = live_index.find_best_candidate(skill_name)
            if candidate:
                found_candidates.append((skill_name, candidate))
            else:
                not_found_skills.append(skill_name)

        # Sort found targets by scrollbar_ratio ascending (topmost first).
        found_candidates.sort(
            key=lambda tc: float(tc[1].get("scrollbar_ratio") or 0.0)
        )
        # Processing order: found (top-to-bottom by ratio), then not-found.
        processing_order = [(s, c) for s, c in found_candidates] + [(s, None) for s in not_found_skills]

        info(f"Skill scanner: waterfall order: "
             f"{[(s, round(float((c or {}).get('scrollbar_ratio') or -1), 3)) for s, c in processing_order]}")

        for skill_name, candidate in processing_order:
            entry = _build_scan_entry(skill_name, source="full_scan")

            # If not found yet, wait for more background analysis.
            if candidate is None:
                candidate = live_index.wait_for_candidate(skill_name, timeout=10.0)

            if candidate:
                candidate_row = candidate.get("row") or {}
                _sync_target_entry_telemetry(entry, match_row=candidate_row)
                entry["candidate"] = {
                    "frame_index": candidate.get("frame_index"),
                    "scrollbar_ratio": candidate.get("scrollbar_ratio"),
                    "match_name": candidate_row.get("match_name"),
                    "match_score": candidate_row.get("match_score"),
                    "increment_pairing": candidate_row.get("increment_pairing"),
                    "increment_vertical_distance": candidate_row.get("increment_vertical_distance"),
                    "ocr_variant_used": candidate_row.get("ocr_variant"),
                    "chosen_variant": candidate_row.get("chosen_variant") or candidate_row.get("ocr_variant"),
                    "ocr_variants_seen": list(candidate_row.get("ocr_variants_seen") or []),
                    "name_match_method": candidate_row.get("name_match_method"),
                    "consensus_score": candidate_row.get("consensus_score"),
                    "token_evidence": dict(candidate_row.get("token_evidence") or {}),
                    "reject_reason": candidate_row.get("reject_reason") or "",
                    "obtained_evidence": candidate_row.get("obtained_evidence") or "none",
                    "increment_ready": bool(_match_has_safe_increment(candidate_row)),
                }
            else:
                _set_target_entry_reason(entry, "target_not_found_in_any_frame")
                flow["target_results"].append(entry)
                continue

            # Skip reacquire+nudge if the scan already determined this skill
            # has an "Obtained" badge (no increment button exists).
            if candidate_row.get("obtained") and not _match_has_safe_increment(candidate_row):
                _set_target_entry_reason(entry, "target_obtained_no_increment", match_row=candidate_row)
                flow["target_results"].append(entry)
                continue

            reacquire_match, _reacquire_screenshot, seek_result, reacquire_result = _reacquire_skill_candidate(
                skill_name,
                skill_shortlist,
                candidate,
                t_flow,
                normalized_shortlist=normalized_shortlist,
            )
            entry["seekback_result"] = seek_result
            entry["reacquire_result"] = reacquire_result
            _sync_target_entry_telemetry(entry, match_row=reacquire_match)
            if not reacquire_match:
                _set_target_entry_reason(entry, "target_found_in_scan_but_reacquire_failed")
                flow["target_results"].append(entry)
                continue
            if not reacquire_match.get("increment_match"):
                _set_target_entry_reason(
                    entry,
                    _classify_no_increment_row(reacquire_match, _reacquire_screenshot),
                    match_row=reacquire_match,
                )
                flow["target_results"].append(entry)
                continue
            if not _match_has_safe_increment(reacquire_match):
                _set_target_entry_reason(entry, "target_reacquired_but_increment_pair_unsafe", match_row=reacquire_match)
                flow["target_results"].append(entry)
                continue

            entry["increment_click_result"] = _click_increment_for_match(reacquire_match, dry_run)
            _sync_target_entry_telemetry(entry, match_row=reacquire_match)
            if not entry["increment_click_result"].get("target"):
                _set_target_entry_reason(
                    entry,
                    entry["increment_click_result"].get("reason") or "increment_click_not_ready",
                    match_row=reacquire_match,
                )
            flow["target_results"].append(entry)

        flow["finalize_result"] = _finalize_multi_increment_results(flow["target_results"], dry_run)
        flow["scan_timing"] = _build_scan_timing(t_flow, t_scan_done, t_scan_done)
        succeeded = [
            entry for entry in flow["target_results"]
            if (entry.get("increment_click_result") or {}).get("target")
        ]
        flow["reason"] = (
            flow["finalize_result"].get("reason")
            or ("multi_increments_processed" if succeeded else "no_targets_incremented")
        )
        return flow
    finally:
        _finalize_waterfall_summary(flow, live_index)


# ---------------------------------------------------------------------------
# Timing + debug helpers
# ---------------------------------------------------------------------------

def _build_scan_timing(t_flow, t_scan_done=None, t_seekback=None):
    """Build the scan_timing dict with phase breakdowns."""
    now = _time()
    timing = {"wall": round(now - t_flow, 4)}
    if t_scan_done:
        timing["scan_phase"] = round(t_scan_done - t_flow, 4)
    if t_seekback and t_scan_done:
        timing["seekback_phase"] = round(now - t_seekback, 4) if t_seekback else None
    return timing


def _summarize_frames_for_flow(all_frames):
    """Build a compact summary of all analyzed frames for the flow result."""
    summaries = []
    for frame in all_frames:
        sb = frame.get("scrollbar") or {}
        summaries.append({
            "index": frame.get("index"),
            "elapsed": frame.get("elapsed"),
            "ocr_rows_count": frame.get("ocr_rows_count", 0),
            "matched_count": len(frame.get("matched_targets", [])),
            "matched_names": [m.get("match_name") for m in frame.get("matched_targets", [])],
            "increment_count": frame.get("increment_count", 0),
            "scrollbar_ratio": sb.get("position_ratio"),
            "row_signature": frame.get("row_signature", "")[:40],
            "timing": frame.get("timing"),
        })
    return summaries


def _log_scan_debug(flow, all_frames):
    """Log a debug summary of the scan for troubleshooting."""
    all_texts = set()
    for frame in (all_frames or []):
        for row in frame.get("ocr_rows", []):
            all_texts.add(row.get("text_raw", ""))
    if all_texts:
        debug(f"Skill scanner: all OCR text seen ({len(all_texts)} unique): "
              f"{sorted(all_texts)[:20]}...")
    # Log matched targets summary.
    matched_summary = []
    for frame in (all_frames or []):
        for m in frame.get("matched_targets", []):
            matched_summary.append(f"frame={frame.get('index')} "
                                   f"name='{m.get('match_name')}' "
                                   f"score={m.get('match_score')} "
                                   f"type={m.get('match_type')} "
                                   f"inc={m.get('increment_match') is not None}")
    if matched_summary:
        info(f"Skill scanner: matched targets: {matched_summary}")


# ---------------------------------------------------------------------------
# Skills page open / close helpers
# ---------------------------------------------------------------------------

def _open_skills_page():
    """Click the skills button and wait for the page to settle.

    If the scrollbar is already detected (skills page already open),
    skip clicking and report already_open.

    Returns an open_result dict with timing.
    """
    t0 = _time()
    result = {
        "opened": False,
        "already_open": False,
        "clicked": False,
        "timing": {},
    }

    # Check if already on the skills page: the skills button must NOT be
    # visible (it only shows on the lobby) AND the scrollbar must be detected.
    t_pre = _time()
    skills_btn_visible = device_action.match_template(
        _SKILLS_BTN_TEMPLATE,
        device_action.screenshot(region_ltrb=constants.SCREEN_BOTTOM_BBOX),
        threshold=0.8,
    )
    if not skills_btn_visible:
        pre_scrollbar = inspect_skill_scrollbar()
        if pre_scrollbar.get("detected"):
            result["opened"] = True
            result["already_open"] = True
            result["timing"]["precheck"] = round(_time() - t_pre, 4)
            result["timing"]["total"] = round(_time() - t0, 4)
            info("[SKILL] Skills page already open (scrollbar detected, no skills button).")
            return result
    result["timing"]["precheck"] = round(_time() - t_pre, 4)

    t_click = _time()
    clicked = device_action.locate_and_click(
        _SKILLS_BTN_TEMPLATE,
        min_search_time=get_secs(1),
        region_ltrb=constants.SCREEN_BOTTOM_BBOX,
    )
    result["timing"]["click"] = round(_time() - t_click, 4)
    result["clicked"] = bool(clicked)
    if not clicked:
        result["timing"]["total"] = round(_time() - t0, 4)
        return result

    sleep(_OPEN_SETTLE_SECONDS)
    result["timing"]["settle"] = round(_OPEN_SETTLE_SECONDS, 4)

    # Verify the scrollbar is now visible (skills page is open).
    t_verify = _time()
    scrollbar = inspect_skill_scrollbar()
    result["timing"]["verify"] = round(_time() - t_verify, 4)
    result["opened"] = scrollbar.get("detected", False)
    result["timing"]["total"] = round(_time() - t0, 4)
    return result


def _close_skills_page():
    """Click back to close the skills page.

    Handles two possible popups before the page can close:
    1. "Skills learned" popup — if _click_learn_and_close() failed to dismiss it
       during finalize, detect it here and click its close button.
    2. "Exit without learning skills?" dialog — if skills were incremented but
       not learned, detect and click OK.

    Returns a close_result dict with timing.
    """
    t0 = _time()
    result = {
        "closed": False,
        "clicked": False,
        "learned_popup_dismissed": False,
        "exit_dialog_clicked": False,
        "timing": {},
    }

    # Before clicking back, check for a lingering "skills learned" popup that
    # _click_learn_and_close() failed to dismiss.  If present, dismiss it first
    # so that the back button is reachable.
    if (constants.SCENARIO_NAME or "") in ("mant", "trackblazer"):
        t_popup = _time()
        close_template = constants.TRACKBLAZER_SKILL_UI_TEMPLATES.get("skills_learned_close")
        if close_template:
            screenshot = _capture_live_skill_screenshot()
            close_matches = device_action.match_template(
                close_template,
                screenshot,
                threshold=_TRACKBLAZER_SKILLS_LEARNED_CLOSE_THRESHOLD,
                template_scaling=_INVERSE_GLOBAL_SCALE,
            )
            if close_matches:
                match = close_matches[0]
                ui_left, ui_top, _, _ = [int(v) for v in _skill_ui_region()]
                click_x = ui_left + match[0] + match[2] // 2
                click_y = ui_top + match[1] + match[3] // 2
                info(f"[SKILL] Lingering learned popup detected in close step, clicking at ({click_x}, {click_y})")
                device_action.click(target=(click_x, click_y), duration=0.15)
                result["learned_popup_dismissed"] = True
                sleep(_CLOSE_SETTLE_SECONDS_POST_LEARN)
                result["timing"]["learned_popup_dismiss"] = round(_time() - t_popup, 4)
            else:
                result["timing"]["learned_popup_check"] = round(_time() - t_popup, 4)

    t_click = _time()
    clicked = device_action.locate_and_click(
        _BACK_BTN_TEMPLATE,
        min_search_time=get_secs(1),
        region_ltrb=constants.SCREEN_BOTTOM_BBOX,
    )
    result["timing"]["click_back"] = round(_time() - t_click, 4)
    result["clicked"] = bool(clicked)
    if not clicked:
        result["timing"]["total"] = round(_time() - t0, 4)
        return result

    sleep(_CLOSE_SETTLE_SECONDS)
    result["timing"]["settle_back"] = round(_CLOSE_SETTLE_SECONDS, 4)

    # Check for "exit without learning skills" confirmation dialog.
    # Try multiple OK button templates at a lower threshold.
    t_exit = _time()
    exit_clicked = False
    screenshot = _capture_live_skill_screenshot()
    ok_templates = ["assets/buttons/ok_btn.png", "assets/buttons/ok_2_btn.png", _EXIT_NO_LEARN_TEMPLATE]
    for ok_template in ok_templates:
        matches = device_action.match_template(ok_template, screenshot, threshold=0.8)
        if matches:
            match = matches[0]
            ui_left, ui_top, _, _ = [int(v) for v in _skill_ui_region()]
            click_x = ui_left + match[0] + match[2] // 2
            click_y = ui_top + match[1] + match[3] // 2
            info(f"[SKILL] Exit dialog detected via {ok_template}, clicking at ({click_x}, {click_y})")
            device_action.click(target=(click_x, click_y), duration=0.15)
            exit_clicked = True
            break
    result["timing"]["click_exit_dialog"] = round(_time() - t_exit, 4)
    result["exit_dialog_clicked"] = bool(exit_clicked)
    if exit_clicked:
        info("[SKILL] Confirmed 'exit without learning' dialog.")
        sleep(_EXIT_DIALOG_SETTLE_SECONDS)
        result["timing"]["settle_exit_dialog"] = round(_EXIT_DIALOG_SETTLE_SECONDS, 4)

    result["closed"] = True
    result["timing"]["total"] = round(_time() - t0, 4)
    return result


# ---------------------------------------------------------------------------
# Full skill purchase flow (open → scan → close)
# ---------------------------------------------------------------------------

def collect_skill_purchase(target_skill=None, skill_shortlist=None,
                           allow_open=True, trigger="automatic", dry_run=True,
                           save_debug_frames=None, debug_session_name=None,
                           target_hints=None):
    """Full skill purchase flow: open skills page, scan, optionally increment, close.

    Returns a dict with:
      - skill_purchase_flow: timing and state for the operator console
      - skill_purchase_scan: scan result details
    """
    if not skill_shortlist:
        skill_shortlist = list(getattr(config, "SKILL_LIST", []))
    if target_skill and target_skill not in skill_shortlist:
        skill_shortlist = [target_skill] + skill_shortlist

    flow = {
        "trigger": trigger,
        "execution_intent": bot.get_execution_intent(),
        "dry_run": bool(dry_run),
        "target_skill": target_skill,
        "skill_shortlist": skill_shortlist,
        "target_hints": list(target_hints or []),
        "opened": False,
        "scanned": False,
        "closed": False,
        "skipped": False,
        "reason": "",
        "open_result": None,
        "scan_result": None,
        "close_result": None,
    }
    result = {
        "skill_purchase_flow": flow,
        "skill_purchase_scan": None,
    }

    t_total = _time()
    enable_debug_frames = bool(trigger == "manual_console") if save_debug_frames is None else bool(save_debug_frames)

    # Gate: is auto-buy enabled?
    if not bot.get_skill_auto_buy_enabled() and trigger != "manual_console":
        flow["skipped"] = True
        flow["reason"] = "auto_buy_skill_disabled"
        flow["timing_total"] = round(_time() - t_total, 3)
        info("[SKILL] Skill purchase skipped: auto-buy disabled.")
        return result

    # Gate: do we have a shortlist?
    if not skill_shortlist:
        flow["skipped"] = True
        flow["reason"] = "no_skill_shortlist"
        flow["timing_total"] = round(_time() - t_total, 3)
        info("[SKILL] Skill purchase skipped: no shortlist configured.")
        return result

    # Step 1: Open skills page.
    if allow_open:
        info("[SKILL] Opening skills page...")
        t0 = _time()
        open_result = _open_skills_page()
        flow["timing_open"] = round(_time() - t0, 3)
        flow["open_result"] = open_result
        flow["opened"] = bool(open_result.get("opened"))
        if not flow["opened"]:
            flow["reason"] = "failed_to_open_skills_page"
            flow["timing_total"] = round(_time() - t_total, 3)
            warning("[SKILL] Failed to open skills page.")
            return result
    else:
        flow["opened"] = True
        flow["reason"] = "skills_page_assumed_open"

    # Step 2: Scan and increment all targets from the shortlist.
    info(f"[SKILL] Scanning skill list (dry_run={dry_run})...")
    t0 = _time()
    scan_result = scan_and_increment_skills(
        target_skills=skill_shortlist,
        dry_run=dry_run,
        save_debug_frames=enable_debug_frames,
        debug_session_name=debug_session_name,
        target_hints=target_hints,
    )
    flow["timing_scan"] = round(_time() - t0, 3)
    flow["scanned"] = True
    flow["scan_result"] = {
        "target_skills": scan_result.get("target_skills"),
        "target_results": scan_result.get("target_results"),
        "reason": scan_result.get("reason"),
        "scan_timing": scan_result.get("scan_timing"),
        "frame_count": scan_result.get("frame_signatures_seen", 0),
        "unique_frames": scan_result.get("frame_signatures_unique", 0),
    }
    if scan_result.get("drag_result"):
        flow["scan_result"]["drag_result"] = scan_result["drag_result"]
    if scan_result.get("bottom_completion_result"):
        flow["scan_result"]["bottom_completion_result"] = scan_result["bottom_completion_result"]
    if scan_result.get("shortcut_result"):
        flow["scan_result"]["shortcut_result"] = scan_result["shortcut_result"]
    if scan_result.get("finalize_result"):
        flow["scan_result"]["finalize_result"] = scan_result["finalize_result"]
    result["skill_purchase_scan"] = scan_result

    # Step 3: Close skills page (skip if it was already open before we started).
    already_open = (flow.get("open_result") or {}).get("already_open", False)
    if allow_open and not already_open:
        info("[SKILL] Closing skills page...")
        t0 = _time()
        close_result = _close_skills_page()
        flow["timing_close"] = round(_time() - t0, 3)
        flow["close_result"] = close_result
        flow["closed"] = bool(close_result.get("closed"))
        if not flow["closed"]:
            flow["reason"] = flow["reason"] or "failed_to_close_skills_page"
            warning("[SKILL] Failed to close skills page.")
    elif already_open:
        flow["closed"] = False
        flow["reason"] = flow["reason"] or "skills_page_was_already_open"
        info("[SKILL] Skipping close — skills page was already open.")
    else:
        flow["closed"] = False
        flow["reason"] = flow["reason"] or "skills_page_left_open"

    flow["timing_total"] = round(_time() - t_total, 3)
    flow["reason"] = flow["reason"] or scan_result.get("reason", "")

    info(f"[SKILL] Skill purchase flow timing: total={flow.get('timing_total', '?')}s "
         f"(open={flow.get('timing_open', '-')} scan={flow.get('timing_scan', '-')} "
         f"close={flow.get('timing_close', '-')})")
    return result
