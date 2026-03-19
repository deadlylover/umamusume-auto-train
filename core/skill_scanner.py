# core/skill_scanner.py
# Skill list scrollbar + OCR purchase pipeline (Phase 1).
# Scans the skill page using scrollbar-driven buffered capture,
# OCRs skill names from the name band, matches against a configured
# shortlist, pairs to increment buttons, and detects confirm state.

import utils.constants as constants
import utils.device_action_wrapper as device_action
import core.config as config
from core.ocr import extract_text
from utils.log import info, warning, debug
from utils.tools import get_secs, sleep
from utils.screenshot import enhanced_screenshot
import core.bot as bot
from PIL import Image
from time import time as _time
from queue import Queue
import numpy as np
import threading
import re
import Levenshtein

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

# Buffered capture pipeline tuning (kept for future continuous-drag mode).
_SCROLLBAR_DRAG_DURATION_SECONDS = 3.2
_SCROLLBAR_FRAME_INTERVAL_SECONDS = 0.2
_SCROLLBAR_ANALYSIS_WORKERS = 3

# Step-scan tuning — seek to discrete positions, settle, then analyze.
_SCROLLBAR_SCAN_STEPS = 8           # Number of positions from top to bottom
_SCROLLBAR_STEP_SETTLE_SECONDS = 0.3  # Wait after each seek for content to settle

# OCR matching tuning.
_EXACT_MATCH_THRESHOLD = 0.92     # Levenshtein ratio for "exact" match
_FUZZY_MATCH_THRESHOLD = 0.75     # Levenshtein ratio for fuzzy fallback
_FUZZY_MATCH_MIN_LENGTH = 4       # Minimum skill name length for fuzzy

# Increment button pairing.
_INCREMENT_MATCH_THRESHOLD = 0.65
_INCREMENT_Y_TOLERANCE = 50       # Looser than shop — skill cards are taller
_INCREMENT_TEMPLATE = "assets/buttons/skill_increment.png"

# Confirm detection.
_CONFIRM_TEMPLATE = "assets/buttons/confirm_btn.png"
_CONFIRM_THRESHOLD = 0.8

# Skills page open/close.
_SKILLS_BTN_TEMPLATE = "assets/buttons/skills_btn.png"
_BACK_BTN_TEMPLATE = "assets/buttons/back_btn.png"
_EXIT_NO_LEARN_TEMPLATE = "assets/buttons/skill_confirm_exit_no_learn.png"
_EXIT_NO_LEARN_THRESHOLD = 0.8
_OPEN_SETTLE_SECONDS = 1.0
_CLOSE_SETTLE_SECONDS = 0.5
_EXIT_DIALOG_SETTLE_SECONDS = 0.5


# ---------------------------------------------------------------------------
# Screenshot helpers
# ---------------------------------------------------------------------------

def _skill_ui_region():
    """The base screenshot region for skill page captures."""
    return constants.GAME_WINDOW_BBOX


def _capture_live_skill_screenshot():
    """Force a fresh screenshot of the game window."""
    device_action.flush_screenshot_cache()
    return device_action.screenshot(region_ltrb=_skill_ui_region())


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
        gray = np.asarray(Image.fromarray(crop).convert("L"))
    else:
        gray = np.asarray(crop)
    if gray.size == 0 or gray.shape[0] <= 0 or gray.shape[1] <= 0:
        return result

    # Find the darkest vertical lane (the scrollbar track).
    col_mean = gray.mean(axis=0)
    best_col = None
    for center in range(2, gray.shape[1] - 2):
        left = max(0, center - 2)
        right = min(gray.shape[1], center + 3)
        score = float(col_mean[left:right].mean())
        if best_col is None or score < best_col[0]:
            best_col = (score, center)
    track_center_x = int(best_col[1]) if best_col else int(gray.shape[1] // 2)
    left = max(0, track_center_x - _SCROLLBAR_WINDOW_HALF_WIDTH)
    right = min(gray.shape[1], track_center_x + _SCROLLBAR_WINDOW_HALF_WIDTH + 1)
    track = gray[:, left:right]
    row_mean = track.mean(axis=1)
    baseline = float(np.percentile(row_mean, 70))
    threshold = baseline - _SCROLLBAR_DARKNESS_DELTA
    mask = row_mean < threshold

    # Find dark segments (thumb candidates).
    segments = []
    start = None
    for idx, is_dark in enumerate(mask):
        if is_dark and start is None:
            start = idx
        elif not is_dark and start is not None:
            if idx - start >= _SCROLLBAR_MIN_SEGMENT_HEIGHT:
                segments.append((start, idx - 1, float(row_mean[start:idx].mean())))
            start = None
    if start is not None and len(mask) - start >= _SCROLLBAR_MIN_SEGMENT_HEIGHT:
        segments.append((start, len(mask) - 1, float(row_mean[start:].mean())))
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


# ---------------------------------------------------------------------------
# OCR extraction from the name band
# ---------------------------------------------------------------------------

def _extract_ocr_rows_from_name_band(screenshot):
    """OCR the skill name band and return raw EasyOCR results with bounding boxes.

    Returns a list of dicts with text, confidence, and Y-coordinate info
    for each detected text region.
    """
    crop = _crop_absolute_bbox(
        screenshot,
        constants.SKILL_NAME_BAND_BBOX,
        base_region_ltrb=_skill_ui_region(),
    )
    if crop is None or getattr(crop, "size", 0) == 0:
        return []

    # Run EasyOCR directly on the numpy crop to get bounding boxes.
    import easyocr
    from core.ocr import reader
    allowlist = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-!.,'#? "
    raw_results = reader.readtext(crop, allowlist=allowlist)

    # Convert raw EasyOCR results to row entries with absolute Y coordinates.
    bbox_left, bbox_top, _, _ = [int(v) for v in constants.SKILL_NAME_BAND_BBOX]
    rows = []
    for bbox, text, confidence in raw_results:
        # bbox is [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
        y_coords = [pt[1] for pt in bbox]
        x_coords = [pt[0] for pt in bbox]
        row_top = min(y_coords)
        row_bottom = max(y_coords)
        row_center_y = (row_top + row_bottom) / 2
        rows.append({
            "text_raw": text,
            "text_normalized": _normalize_skill_text(text),
            "confidence": round(float(confidence), 4),
            "crop_y_center": round(row_center_y, 1),
            "abs_y_center": round(bbox_top + row_center_y, 1),
            "crop_bbox": [
                int(round(min(x_coords))),
                int(round(row_top)),
                int(round(max(x_coords) - min(x_coords))),
                int(round(row_bottom - row_top)),
            ],
        })
    return rows


def _normalize_skill_text(text):
    """Normalize OCR text for matching: lowercase, strip punctuation, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Skill matching against shortlist
# ---------------------------------------------------------------------------

def _match_skill_rows_to_shortlist(ocr_rows, skill_shortlist):
    """Match OCR rows against the configured skill shortlist.

    Uses exact match first, then conservative fuzzy matching.
    Returns a list of matched rows with match details.
    """
    matched = []
    for row in ocr_rows:
        normalized = row["text_normalized"]
        if not normalized or len(normalized) < 3:
            continue
        best_match = None
        best_score = 0.0
        for skill_name in skill_shortlist:
            skill_normalized = _normalize_skill_text(skill_name)
            # Try exact substring containment first.
            if skill_normalized in normalized or normalized in skill_normalized:
                score = Levenshtein.ratio(normalized, skill_normalized)
                if score > best_score:
                    best_score = score
                    best_match = skill_name
                continue
            # Fuzzy match.
            score = Levenshtein.ratio(normalized, skill_normalized)
            if score > best_score:
                best_score = score
                best_match = skill_name

        if best_match and best_score >= _EXACT_MATCH_THRESHOLD:
            matched.append({
                **row,
                "match_name": best_match,
                "match_score": round(best_score, 4),
                "match_type": "exact" if best_score >= _EXACT_MATCH_THRESHOLD else "fuzzy",
            })
        elif best_match and best_score >= _FUZZY_MATCH_THRESHOLD and len(normalized) >= _FUZZY_MATCH_MIN_LENGTH:
            matched.append({
                **row,
                "match_name": best_match,
                "match_score": round(best_score, 4),
                "match_type": "fuzzy",
            })
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
    matches = device_action.match_template(
        _INCREMENT_TEMPLATE,
        crop,
        threshold=_INCREMENT_MATCH_THRESHOLD,
    )
    return matches


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


# ---------------------------------------------------------------------------
# Per-frame analysis (used by the buffered pipeline)
# ---------------------------------------------------------------------------

def _analyze_skill_frame(frame_payload):
    """Analyze a single captured frame: OCR + scrollbar + increment detection."""
    t0 = _time()
    screenshot = frame_payload.get("screenshot")
    skill_shortlist = frame_payload.get("skill_shortlist", [])

    # OCR the name band.
    ocr_rows = _extract_ocr_rows_from_name_band(screenshot)

    # Match against shortlist.
    matched_targets = _match_skill_rows_to_shortlist(ocr_rows, skill_shortlist)

    # Detect increment buttons.
    increment_matches = _detect_increment_buttons(screenshot)

    # Pair matched targets to increment buttons.
    for target in matched_targets:
        inc = _pair_skill_row_to_increment(target, increment_matches)
        target["increment_match"] = list(inc) if inc else None

    # Scrollbar state.
    scrollbar = inspect_skill_scrollbar(screenshot=screenshot)

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
        "final": bool(frame_payload.get("final")),
        "timing": {
            "capture": round(frame_payload.get("capture_elapsed", 0.0), 4),
            "scan": round(scan_elapsed, 4),
            "wall": round(frame_payload.get("capture_elapsed", 0.0) + scan_elapsed, 4),
        },
    }


# ---------------------------------------------------------------------------
# Buffered capture during scrollbar drag
# ---------------------------------------------------------------------------

def _capture_skill_frames_during_scrollbar_drag(initial_scrollbar, skill_shortlist):
    """Capture frames during a top-to-bottom scrollbar drag, analyzing concurrently.

    Same producer-consumer pattern as the Trackblazer shop scan.
    """

    def _analysis_worker():
        while True:
            frame_payload = analysis_queue.get()
            if frame_payload is None:
                analysis_queue.task_done()
                return
            try:
                analyzed = _analyze_skill_frame(frame_payload)
                analyzed_frames.append(analyzed)
            finally:
                analysis_queue.task_done()

    drag = {
        "start": None,
        "end": None,
        "duration": float(_SCROLLBAR_DRAG_DURATION_SECONDS),
        "swiped": False,
        "frames": [],
        "stop_reason": "",
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
            duration=_SCROLLBAR_DRAG_DURATION_SECONDS,
            text="Skill scrollbar drag top-to-bottom",
        ))

    drag_thread = threading.Thread(target=_run_drag, daemon=True)
    drag_thread.start()
    next_capture_at = _time() + _SCROLLBAR_FRAME_INTERVAL_SECONDS
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
        })
        frame_count += 1
        next_capture_at = max(next_capture_at + _SCROLLBAR_FRAME_INTERVAL_SECONDS, _time() + 0.001)

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
        "final": True,
    })
    frame_count += 1
    capture_window = _time() - t_drag

    # Wait for all analysis to complete.
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
        "frame_interval_target": round(_SCROLLBAR_FRAME_INTERVAL_SECONDS, 4),
        "frames": int(frame_count),
        "capture_total": round(capture_total, 4),
        "scan_total": round(scan_total, 4),
        "analysis_total": round(max(0.0, wall_total - capture_window), 4),
        "wall": round(wall_total, 4),
    }
    return drag


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


# ---------------------------------------------------------------------------
# Main scan + purchase flow (Phase 1 — dry run)
# ---------------------------------------------------------------------------

def scan_and_increment_skill(target_skill=None, skill_shortlist=None, dry_run=True):
    """Phase 1 skill purchase flow.

    1. Detect scrollbar on the open skills page.
    2. Reset to top.
    3. Scan while dragging to bottom, capturing and OCR-ing concurrently.
    4. Find the target skill and click its increment button.
    5. Detect confirm availability (do NOT click confirm in Phase 1).

    Returns a flow result dict with timing and debug output.
    """
    flow = {
        "target_skill": target_skill,
        "skill_shortlist": skill_shortlist or [],
        "scan_timing": {},
        "scrollbar_initial": None,
        "scrollbar_reset": None,
        "drag_result": None,
        "target_found": False,
        "target_frame_index": None,
        "target_row": None,
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

    if not skill_shortlist:
        flow["reason"] = "no_skill_shortlist_configured"
        warning("Skill scanner: no skill shortlist configured.")
        return flow

    t_flow = _time()

    # Step 1: Detect scrollbar.
    info(f"Skill scanner: detecting scrollbar...")
    scrollbar = inspect_skill_scrollbar()
    flow["scrollbar_initial"] = scrollbar
    if not scrollbar.get("detected"):
        flow["reason"] = "scrollbar_not_detected"
        warning("Skill scanner: scrollbar not detected on skills page.")
        return flow
    info(f"Skill scanner: scrollbar detected, ratio={scrollbar.get('position_ratio')}, "
         f"scrollable={scrollbar.get('scrollable')}")

    # Step 2: Reset to top.
    if not scrollbar.get("is_at_top"):
        info("Skill scanner: resetting scrollbar to top...")
        reset_result = _drag_skill_scrollbar(scrollbar, edge="top")
        flow["scrollbar_reset"] = reset_result
        sleep(0.3)
        # Re-detect after reset.
        scrollbar = inspect_skill_scrollbar()
        info(f"Skill scanner: after reset, ratio={scrollbar.get('position_ratio')}")
    else:
        info("Skill scanner: already at top.")

    # Step 2.5: Capture and analyze the initial still frame at top.
    info("Skill scanner: capturing initial still frame at top...")
    initial_screenshot = _capture_live_skill_screenshot()
    initial_frame = _analyze_skill_frame({
        "index": -1,
        "elapsed": 0.0,
        "capture_elapsed": 0.0,
        "screenshot": initial_screenshot,
        "skill_shortlist": skill_shortlist,
    })
    # Check if target is already visible at top.
    target_match = _find_best_target_match(initial_frame, target_skill)
    if target_match:
        info(f"Skill scanner: target '{target_match['match_name']}' found in initial frame! "
             f"score={target_match['match_score']}")
        flow["target_found"] = True
        flow["target_frame_index"] = -1
        flow["target_row"] = target_match
        return _do_increment_and_confirm(flow, target_match, initial_screenshot, dry_run, t_flow)

    # Step 3: Step-scan through the list using scrollbar increments.
    # Each step seeks to a ratio, settles, captures, and analyzes in place.
    # When the target is found, it's already on screen — no seek-back needed.
    if scrollbar.get("scrollable"):
        step_count = _SCROLLBAR_SCAN_STEPS
        frames = []
        info(f"Skill scanner: step-scanning {step_count} positions, shortlist={skill_shortlist}...")
        for step_idx in range(step_count):
            ratio = step_idx / max(1, step_count - 1)
            # Seek scrollbar to this ratio.
            current_sb = inspect_skill_scrollbar()
            if not current_sb.get("detected"):
                info(f"Skill scanner: scrollbar lost at step {step_idx}")
                break
            thumb = current_sb["thumb_center"]
            target_y = int(current_sb["bbox"][1] + ratio * current_sb["travel_pixels"] + current_sb["thumb_height"] // 2)
            device_action.swipe(
                (int(thumb[0]), int(thumb[1])),
                (int(current_sb["track_center_x"]), target_y),
                duration=_SCROLLBAR_SEEK_DURATION_SECONDS,
                text=f"Skill scan step {step_idx}/{step_count} ratio={ratio:.2f}",
            )
            sleep(_SCROLLBAR_STEP_SETTLE_SECONDS)

            # Capture and analyze at this position.
            screenshot = _capture_live_skill_screenshot()
            frame = _analyze_skill_frame({
                "index": step_idx,
                "elapsed": round(_time() - t_flow, 4),
                "capture_elapsed": 0.0,
                "screenshot": screenshot,
                "skill_shortlist": skill_shortlist,
            })
            frames.append(frame)
            target_match = _find_best_target_match(frame, target_skill)
            if target_match and target_match.get("increment_match"):
                info(f"Skill scanner: target '{target_match['match_name']}' found at step "
                     f"{step_idx} (ratio={ratio:.2f})! score={target_match['match_score']}")
                flow["target_found"] = True
                flow["target_frame_index"] = step_idx
                flow["target_row"] = target_match
                flow["drag_result"] = {
                    "stop_reason": "target_found",
                    "frame_count": len(frames),
                    "timing": {"wall": round(_time() - t_flow, 4)},
                }
                return _do_increment_and_confirm(flow, target_match, screenshot, dry_run, t_flow)
            elif target_match:
                info(f"Skill scanner: target '{target_match['match_name']}' OCR'd at step "
                     f"{step_idx} but no increment button paired.")
                flow["target_found"] = True
                flow["target_frame_index"] = step_idx
                flow["target_row"] = target_match

        flow["drag_result"] = {
            "stop_reason": "scan_complete",
            "frame_count": len(frames),
            "timing": {"wall": round(_time() - t_flow, 4)},
        }
    else:
        info("Skill scanner: list is not scrollable, only initial frame was checked.")

    if not flow["target_found"]:
        flow["reason"] = "target_not_found_in_any_frame"
        info(f"Skill scanner: target skill not found. Scanned shortlist: {skill_shortlist}")

    flow["scan_timing"] = {"wall": round(_time() - t_flow, 4)}

    # Log summary of all OCR text seen across frames for debugging.
    _log_scan_debug(flow, None, initial_frame)
    return flow


def _find_best_target_match(frame, target_skill):
    """Find the target skill match in a frame.

    When target_skill is specified, ONLY return a match for that specific skill.
    """
    matched = frame.get("matched_targets", [])
    if not matched:
        return None
    if target_skill:
        target_normalized = _normalize_skill_text(target_skill)
        for m in matched:
            if _normalize_skill_text(m.get("match_name", "")) == target_normalized:
                return m
        return None
    # No specific target — return highest-scoring match.
    return max(matched, key=lambda m: m.get("match_score", 0))


def _do_increment_and_confirm(flow, target_match, screenshot, dry_run, t_flow):
    """Click increment for the matched target and detect confirm."""
    inc = target_match.get("increment_match")
    if not inc:
        flow["reason"] = "target_found_but_no_increment_paired"
        info("Skill scanner: target found but no increment button paired.")
        flow["scan_timing"] = {"wall": round(_time() - t_flow, 4)}
        return flow

    # Convert SCROLLING_SKILL_SCREEN_BBOX-relative increment match to absolute.
    scroll_left, scroll_top, _, _ = [int(v) for v in constants.SCROLLING_SKILL_SCREEN_BBOX]
    click_x = scroll_left + inc[0] + inc[2] // 2
    click_y = scroll_top + inc[1] + inc[3] // 2

    info(f"Skill scanner: clicking increment for '{target_match['match_name']}' "
         f"at ({click_x}, {click_y})...")
    device_action.click(target=(click_x, click_y), duration=0.15)
    flow["increment_click_result"] = {
        "clicked": True,
        "target": [click_x, click_y],
        "increment_match": list(inc),
    }
    sleep(0.5)

    # Detect confirm button.
    info("Skill scanner: checking for confirm button...")
    confirm = _detect_confirm_button()
    flow["confirm_detect_result"] = confirm
    flow["confirm_available"] = confirm.get("detected", False)
    if confirm.get("detected"):
        info("Skill scanner: confirm button detected! (Phase 1 dry run — not clicking)")
        flow["reason"] = "increment_clicked_confirm_detected"
    else:
        info("Skill scanner: confirm button NOT detected after increment.")
        flow["reason"] = "increment_clicked_confirm_not_detected"

    flow["scan_timing"] = {"wall": round(_time() - t_flow, 4)}
    return flow


def _log_scan_debug(flow, drag_result, initial_frame):
    """Log a debug summary of the scan for troubleshooting."""
    all_texts = set()
    if initial_frame:
        for row in initial_frame.get("ocr_rows", []):
            all_texts.add(row.get("text_raw", ""))
    if drag_result:
        for frame in drag_result.get("frames", []):
            for row in frame.get("ocr_rows", []):
                all_texts.add(row.get("text_raw", ""))
    if all_texts:
        debug(f"Skill scanner: all OCR text seen ({len(all_texts)} unique): "
              f"{sorted(all_texts)[:20]}...")
    # Log matched targets summary.
    matched_summary = []
    if drag_result:
        for frame in drag_result.get("frames", []):
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
        min_search_time=get_secs(2),
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

    If skills were incremented but not learned, a confirmation dialog
    ("exit without learning skills?") appears. Detect and click OK on it.

    Returns a close_result dict with timing.
    """
    t0 = _time()
    result = {
        "closed": False,
        "clicked": False,
        "exit_dialog_clicked": False,
        "timing": {},
    }
    t_click = _time()
    clicked = device_action.locate_and_click(
        _BACK_BTN_TEMPLATE,
        min_search_time=get_secs(2),
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
                           allow_open=True, trigger="automatic", dry_run=True):
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

    # Gate: is auto-buy enabled?
    if not getattr(config, "IS_AUTO_BUY_SKILL", False) and trigger != "manual_console":
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

    # Step 2: Scan and optionally increment.
    info("[SKILL] Scanning skill list...")
    t0 = _time()
    scan_result = scan_and_increment_skill(
        target_skill=target_skill,
        skill_shortlist=skill_shortlist,
        dry_run=dry_run,
    )
    flow["timing_scan"] = round(_time() - t0, 3)
    flow["scanned"] = True
    flow["scan_result"] = {
        "target_found": scan_result.get("target_found"),
        "target_skill": scan_result.get("target_skill"),
        "confirm_available": scan_result.get("confirm_available"),
        "reason": scan_result.get("reason"),
        "scan_timing": scan_result.get("scan_timing"),
    }
    if scan_result.get("target_row"):
        flow["scan_result"]["target_row"] = {
            "text_raw": scan_result["target_row"].get("text_raw"),
            "match_score": scan_result["target_row"].get("match_score"),
            "match_type": scan_result["target_row"].get("match_type"),
            "increment_match": scan_result["target_row"].get("increment_match"),
        }
    if scan_result.get("increment_click_result"):
        flow["scan_result"]["increment_click_result"] = scan_result["increment_click_result"]
    if scan_result.get("confirm_detect_result"):
        flow["scan_result"]["confirm_detect_result"] = scan_result["confirm_detect_result"]
    if scan_result.get("drag_result"):
        flow["scan_result"]["drag_result"] = scan_result["drag_result"]
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
